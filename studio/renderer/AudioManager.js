/**
 * AudioManager — plays a track and exposes the Director's analysis of it.
 *
 * This is the same backend the viewer uses: POST /analyze returns director.json, and the
 * per-band onsets and 30Hz emotion curves in it are what the studio reacts to. Nothing here
 * does its own FFT — the whole point of the Director contract is that the renderer consumes
 * decisions, not spectra.
 *
 * The clock is WebAudio's, not a JS timer. AudioContext.currentTime is driven by the audio
 * hardware, so it cannot drift away from what is being heard the way a rAF-accumulated
 * timestamp does after a few dropped frames — and a visual that drifts off the beat is worse
 * than one that never reacted.
 */
export class AudioManager {
  constructor() {
    this.ctx = null;
    this.buffer = null;
    this.director = null;
    this.source = null;
    this.playing = false;
    this._startedAt = 0;
    this._pausedAt = 0;
    this.onEnded = null;

    // per-band transient envelopes, decayed then attack-smoothed — the same shape the viewer
    // uses, because raw onsets are instantaneous and a visual needs an envelope to ride
    this.HIT = { sub: 0, low: 0, mid: 0, high: 0, air: 0 };
    this.ENV = { sub: 0, low: 0, mid: 0, high: 0, air: 0 };
    this._cursor = { sub: 0, low: 0, mid: 0, high: 0, air: 0 };
    this._prevT = -1;
    this.beat = 0;
  }

  static DECAY = { sub: 3.6, low: 4.0, mid: 5.4, high: 8.0, air: 11.0 };
  static ATTACK = { sub: 9, low: 10, mid: 13, high: 19, air: 25 };
  static BANDS = ['sub', 'low', 'mid', 'high', 'air'];

  get duration() { return this.buffer?.duration ?? 0; }
  get hasTrack() { return !!this.buffer; }

  /** Decode locally and analyse on the server, in that order so a bad file fails fast. */
  async load(file, onProgress) {
    this.ctx ||= new (window.AudioContext || window.webkitAudioContext)();
    const bytes = await file.arrayBuffer();
    this.buffer = await this.ctx.decodeAudioData(bytes.slice(0));

    const job = 'studio-' + Math.random().toString(36).slice(2, 10);
    let poll = null;
    if (onProgress) {
      // One poll in flight at a time. Overlapping requests come back out of order and the bar
      // jumps backwards, so the displayed value is also clamped monotonically.
      let inFlight = false, shown = 0;
      poll = setInterval(async () => {
        if (inFlight) return;
        inFlight = true;
        try {
          const r = await fetch(`/progress?job=${job}`);
          const p = await r.json();
          shown = Math.max(shown, p.p ?? 0);
          onProgress({ ...p, p: shown });
        } catch { /* a dropped poll must never abort the analysis */ }
        inFlight = false;
      }, 300);
    }
    try {
      const res = await fetch(`/analyze?job=${job}`, {
        method: 'POST', headers: { 'X-Filename': file.name }, body: bytes,
      });
      if (!res.ok) throw new Error(`server ${res.status}`);
      this.director = await res.json();
    } finally {
      if (poll) clearInterval(poll);
    }
    this._resetCursors(0);
    return this.director;
  }

  play() {
    if (!this.buffer || this.playing) return;
    if (this.ctx.state === 'suspended') this.ctx.resume();
    this.source = this.ctx.createBufferSource();
    this.source.buffer = this.buffer;
    this.source.connect(this.ctx.destination);
    this.source.onended = () => { if (this.playing) { this.playing = false; this.onEnded?.(); } };
    this.source.start(0, this._pausedAt);
    this._startedAt = this.ctx.currentTime - this._pausedAt;
    this.playing = true;
    this._prevT = -1;
  }

  pause() {
    if (!this.playing) return;
    this._pausedAt = this.time;
    try { this.source.stop(); } catch { /* already stopped */ }
    this.playing = false;
  }

  seek(t) {
    const was = this.playing;
    if (was) this.pause();
    this._pausedAt = Math.max(0, Math.min(this.duration - 0.05, t));
    this._resetCursors(this._pausedAt);
    if (was) this.play();
  }

  get time() {
    return this.playing ? (this.ctx.currentTime - this._startedAt) : this._pausedAt;
  }

  /** Binary-search each band's onset list to the new position after a seek. */
  _resetCursors(t) {
    const on = this.director?.events?.onsets;
    this._prevT = -1;
    this.beat = 0;
    for (const b of AudioManager.BANDS) {
      this.HIT[b] = 0; this.ENV[b] = 0;
      const a = on?.[b];
      if (!a) { this._cursor[b] = 0; continue; }
      let lo = 0, hi = a.length;
      while (lo < hi) { const m = (lo + hi) >> 1; if (a[m][0] <= t) lo = m + 1; else hi = m; }
      this._cursor[b] = lo;
    }
  }

  /** Sample a named 30Hz curve at the current position. */
  curve(name, t = this.time) {
    const c = this.director?.curves;
    if (!c || !c[name]) return 0;
    const fps = this.director.meta.director_fps || 30;
    const i = Math.max(0, Math.min(c[name].length - 1, Math.round(t * fps)));
    return c[name][i];
  }

  get bpm() { return this.director?.tempo?.bpm || 120; }

  section(t = this.time) {
    const s = this.director?.sections;
    if (!s?.length) return null;
    for (const sec of s) if (t >= sec.t0 && t < sec.t1) return sec;
    return s[s.length - 1];
  }

  /** Advance the envelopes. Called once per frame by Renderer; dt in seconds. */
  update(dt) {
    if (!this.director) return;
    const t = this.time;
    const on = this.director.events.onsets;

    if (this._prevT >= 0 && t > this._prevT) {
      const beats = this.director.events.beats;
      for (const bt of beats) { if (bt > this._prevT && bt <= t) { this.beat = 1; break; } }
      for (const b of AudioManager.BANDS) {
        const a = on?.[b]; if (!a) continue;
        while (this._cursor[b] < a.length && a[this._cursor[b]][0] <= t) {
          this.HIT[b] = Math.max(this.HIT[b], a[this._cursor[b]][1]);
          this._cursor[b]++;
        }
      }
    } else if (this._prevT >= 0 && t < this._prevT) {
      this._resetCursors(t);   // looped or seeked backwards
    }
    this._prevT = t;

    this.beat *= Math.exp(-6 * dt);
    for (const b of AudioManager.BANDS) {
      this.HIT[b] *= Math.exp(-AudioManager.DECAY[b] * dt);
      this.ENV[b] += (this.HIT[b] - this.ENV[b]) * (1 - Math.exp(-AudioManager.ATTACK[b] * dt));
    }
  }

  dispose() {
    try { this.source?.stop(); } catch { /* not started */ }
    this.ctx?.close();
    this.buffer = null; this.director = null;
  }
}
