#!/usr/bin/env python3
"""
ATONAL — AI Music Understanding Engine (backend, Layers 1-6 + 10)

Pipeline:  audio -> signal features -> structure -> emotion curves -> genre
           -> Director State -> director.json  (the contract the renderer consumes)

The renderer NEVER sees FFT.  It only reads director.json:
  - sections[]  : structural timeline, each carrying the cinematic decision for it
  - curves{}    : continuous emotional/energetic signals sampled at DIRECTOR_FPS
  - events{}    : beats, downbeats, and predicted upcoming events (anticipation)
  - genre, tempo, meta

Usage:  python analyze.py "track.mp3" -o out/director.json
"""
import sys, os, json, argparse, tempfile, subprocess, math, time, threading
import numpy as np
import tagger   # Layer 4 ML tagging (PANNs), optional

# Bumped whenever the pipeline's OUTPUT changes — new curves, new blocks, changed maths. The
# cache is keyed on the audio's SHA-256, which answers "same bytes?" and not "same analysis?", so
# without this a track analysed before a pipeline change keeps returning the old director for
# ever. The failure is silent and shaped exactly like a bug in the renderer: fields the contract
# promises are simply absent, on some tracks and not others.
ANALYSIS_VERSION = 2

SR = 22050            # analysis sample rate
HOP = 512             # ~23 ms frames at 22.05k
DIRECTOR_FPS = 30     # continuous-curve output rate

# ----------------------------------------------------------------------------- IO
def load_audio(path):
    """Decode anything to mono+stereo float via the bundled ffmpeg, load with soundfile."""
    import soundfile as sf, imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    # mkstemp, not mktemp: mktemp returns a name and leaves the race between that and the write
    # wide open, and server.py already says exactly this about its own upload file. The fd is
    # closed immediately because ffmpeg opens the path itself; what we want from mkstemp is the
    # atomic, exclusive creation, not the handle.
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run([ff, "-y", "-i", path, "-ac", "2", "-ar", str(SR), "-f", "wav", tmp],
                       check=True, capture_output=True)
        data, sr = sf.read(tmp, dtype="float32", always_2d=True)   # (n, ch)
    finally:
        # ran only on success before, so every failed decode left a full-length wav behind —
        # the decoded file is larger than the upload that produced it.
        if os.path.exists(tmp):
            try: os.remove(tmp)
            except OSError: pass
    stereo = data.T                                            # (ch, n)
    mono = stereo.mean(axis=0)
    return mono, stereo, sr

# --------------------------------------------------------------------- utilities
TEMPO_LO, TEMPO_HI = 70.0, 160.0    # the range essentially all dance/electronic music lives in

# How many structural boundaries to ASK the segmenter for, as one per this many seconds.
# Named and module-level so it can be swept against a track of known structure rather than being
# a literal nobody dares touch.
#
# VALUE UNCHANGED, and the sweep is why. On a synthetic 93s track with 4 known interior
# boundaries, every setting from one-per-8s (11 asked) to one-per-30s (4 asked) produced the
# IDENTICAL boundary list. The agglomerative boundaries are not what survives: the energy-novelty
# peaks outrank them in the merge below and the structural ones are absorbed. So this knob does
# not currently control what it appears to control, and tuning it would have been a change with
# no measured effect behind it. Left at the original 8.0 until the merge is rebalanced.
SEG_SECONDS_PER_BOUNDARY = 8.0
SEG_KMAX_LO, SEG_KMAX_HI = 6, 16


def _fold_tempo(bpm):
    """Fold a tempo into TEMPO_LO..TEMPO_HI by octaves.

    Beat trackers routinely report a tempo one octave out (half- or double-time), and folding
    brings a stray reading back into the range dance music lives in.

    ONLY FOR A TEMPO WITH NO GRID BEHIND IT. The docstring used to claim folding was "safe
    because a half-time reading describes the same beat grid at a different metrical level, so
    the visual pacing is what changes, not the alignment". That was true when this number was the
    only clock. It is not true now: the renderer interpolates events.beats for its musical time
    and reads tempo.bpm for everything else, so if the two disagree by an octave the client is
    running two contradictory clocks. Measured on a real upload -- a 176 BPM hardcore track whose
    grid ships at 176.4 and whose reported tempo came back 88.16, exactly half, because the fit
    landed above TEMPO_HI and was folded. See the call site.

    Guarded against a runaway loop on a degenerate input (silence can yield 0 or inf).
    """
    if not np.isfinite(bpm) or bpm <= 1e-3:
        return 120.0
    for _ in range(8):
        if bpm < TEMPO_LO:
            bpm *= 2.0
        elif bpm > TEMPO_HI:
            bpm /= 2.0
        else:
            break
    return float(round(bpm, 2))


def nrm(a, lo=5, hi=95):
    """Robust 0..1 normalise using percentiles (resists outliers)."""
    a = np.asarray(a, dtype=np.float64)
    p1, p2 = np.percentile(a, lo), np.percentile(a, hi)
    if p2 - p1 < 1e-9:
        return np.zeros_like(a)
    return np.clip((a - p1) / (p2 - p1), 0, 1)

def smooth(a, win):
    """Box smooth that always returns the SAME length it was given.

    np.convolve(..., mode="same") returns max(len(a), len(k)) — so a window wider than the signal
    silently returns a LONGER array than it was handed. Every curve here is smoothed with a
    different window (9, 15, 21, 25, 41), so on a short track they came back at different lengths
    and the first arithmetic between two of them raised a broadcast error: a 0.4s upload failed
    with a bare "analysis failed" 500. Clamping the window is the whole fix; the alternative,
    padding, would still misreport the ends as smoothed when there is nothing to smooth with.
    """
    a = np.asarray(a, dtype=np.float64)
    win = int(min(max(1, win), len(a)))
    if win < 2:
        return a
    k = np.ones(win) / win
    return np.convolve(a, k, mode="same")

def resample_curve(x_src, y_src, x_dst):
    return np.interp(x_dst, x_src, y_src)

# ------------------------------------------------------- LAYER 1b: PER-BAND SOUND
# Every distinct sound in a mix lives in a frequency band. Splitting the spectrum lets
# the renderer react to the KICK, the SNARE and the HATS separately instead of lumping
# everything into one "energy" number. Each band gets a sustained level curve AND a
# discrete onset list (the actual hits), so motion can land on individual sounds.
BANDS = [("sub",   20,   80,  0.075),    # name, lo Hz, hi Hz, min gap between onsets (s)
         ("low",   80,   250, 0.070),    # kick body / bassline
         ("mid",   250,  1200, 0.055),   # snare body, chords, vocals
         ("high",  1200, 4500, 0.045),   # snare snap, stabs, leads
         ("air",   4500, 11000, 0.035)]  # hats, cymbals, texture

def pick_onsets(env, times, min_gap_s, sr):
    """Peak-pick a band's onset envelope -> [[t, strength], ...] (sorted, strength 0..1).
    Hand-rolled: librosa.util.peak_pick trips a numba type error on this build."""
    e = smooth(np.asarray(env, dtype=np.float64), 3)
    mx = float(e.max())
    if mx <= 1e-9:
        return []
    en = e / mx
    thr = max(0.10, float(np.percentile(en, 72)))
    wait = max(1, int(min_gap_s * sr / HOP))
    out, last = [], -10**9
    for i in range(1, len(en) - 1):
        if en[i] > thr and en[i] >= en[i-1] and en[i] > en[i+1] and (i - last) > wait:
            out.append([round(float(times[i]), 3), round(float(en[i]), 3)])
            last = i
    return out

def layer1b_bands(S, sr, times):
    """Per-band level curves + discrete onsets, straight off the magnitude spectrogram."""
    import librosa
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    levels, onsets = {}, {}
    for name, lo, hi, gap in BANDS:
        idx = np.where((freqs >= lo) & (freqs < hi))[0]
        if len(idx) == 0:
            idx = np.array([min(len(freqs) - 1, 1)])
        B = S[idx, :]
        levels[name] = np.sqrt((B ** 2).mean(axis=0))               # band RMS
        L = np.log1p(B)                                             # log-domain flux
        d = np.maximum(np.diff(L, axis=1, prepend=L[:, :1]), 0)     # half-wave rectified
        onsets[name] = pick_onsets(d.mean(axis=0), times, gap, sr)
    return levels, onsets

# ============================================================ LAYER 1: SIGNAL
def layer1_signal(mono, stereo, sr, prog=None):
    import librosa
    def step(k):
        if prog: prog(k)
    # HPSS STARTS FIRST AND RUNS ALONGSIDE EVERYTHING ELSE IN THIS FUNCTION. It needs only `mono`,
    # nothing here needs its result until the end, and it is the single longest call in the
    # pipeline -- 6.6s of a 14.9s run, against ~3s for all the other features together. Started
    # here it finishes under cover of the spectral features and the beat tracker.
    #
    # It has to be effects.hpss on the time-domain signal, not the cheaper decompose.hpss on the
    # spectrogram already in hand. That substitution was measured across five kinds of material
    # and it is NOT equivalent: the envelopes agree on the bench signal (perc r=0.988) and come
    # apart everywhere else -- percussive r=0.84, harmonic r=0.57, mixed harm r=0.87. Truncating
    # the spectrogram is worse again (sparse perc 0.95 -> 0.28 at 128 bins) and a mel-scaled
    # version worst of all, going ANTI-correlated on harmonic material at r=-0.32. The one-signal
    # result that said "equivalent" was luck.
    _hp = {}
    def _hpss_worker():
        try:
            H, P = librosa.effects.hpss(mono)
            _hp["h"] = librosa.feature.rms(y=H, hop_length=HOP)[0]
            _hp["p"] = librosa.feature.rms(y=P, hop_length=HOP)[0]
        except Exception as e:
            _hp["e"] = e
    _hp_th = threading.Thread(target=_hpss_worker, name="hpss", daemon=True)
    _hp_th.start()
    step("spectrum")
    S = np.abs(librosa.stft(mono, n_fft=2048, hop_length=HOP))
    f = {}
    step("features")
    f["rms"]       = librosa.feature.rms(S=S, hop_length=HOP)[0]
    f["centroid"]  = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
    f["flatness"]  = librosa.feature.spectral_flatness(S=S)[0]
    f["rolloff"]   = librosa.feature.spectral_rolloff(S=S, sr=sr)[0]
    f["bandwidth"] = librosa.feature.spectral_bandwidth(S=S, sr=sr)[0]
    f["zcr"]       = librosa.feature.zero_crossing_rate(mono, hop_length=HOP)[0]
    f["flux"]      = librosa.onset.onset_strength(y=mono, sr=sr, hop_length=HOP)
    f["contrast"]  = librosa.feature.spectral_contrast(S=S, sr=sr).mean(axis=0)
    f["mfcc"]      = librosa.feature.mfcc(y=mono, sr=sr, n_mfcc=20, hop_length=HOP)
    f["chroma"]    = librosa.feature.chroma_cqt(y=mono, sr=sr, hop_length=HOP)
    # tempo / beats / downbeats
    step("beats")
    # THE BEAT GRID IS TRACKED ON THE LOW BAND, NOT ON FULL-BAND FLUX.
    #
    # Full-band spectral flux is dominated by whatever has the sharpest spectral change, and that
    # is the hi-hat, not the kick: a hat is broadband noise with a near-instant attack, while a
    # kick is a low sine with a slow one. So on any material with hats between the beats, the
    # tallest flux peaks sit on the OFFBEATS, and the tracker locks its grid to them.
    #
    # MEASURED on click120.wav, whose beats are at exact multiples of 0.5s by construction:
    #     full-band flux    0.0% of beats within 60ms of a true beat, 100% on the offbeat hat
    #     low band (160Hz)  100% within 60ms, median error 24ms (one hop -- the floor)
    # and on arc120.wav, 37.8% -> 99.4%.
    #
    # A half-beat error is 250ms at 120bpm. Every beat-keyed event in the renderer -- the accent,
    # the shape change, the motion-period change -- fired a half beat after the sound that was
    # supposed to trigger it, which is the whole of the reported "the movement is behind the
    # track". It is a PHASE error, so it survived every tempo fix, and nothing downstream could
    # detect it: the grid is perfectly regular, just wrong by half a beat.
    #
    # The same low-band envelope already existed below for downbeat inference, for exactly the
    # reason it belongs here -- that is where the kick lives. It is computed once now and used
    # for both. start_bpm still biases the autocorrelation prior: without it librosa takes
    # whichever periodicity peak is tallest, and on strong offbeat content that is regularly a
    # 3:2 relative of the true tempo rather than the usual octave (measured 140 -> 92.3 and
    # 126 -> 83.4, both almost exactly two thirds).
    lo_onset = librosa.onset.onset_strength(
        S=librosa.power_to_db(
            librosa.feature.melspectrogram(y=mono, sr=sr, hop_length=HOP, fmax=160.0),
            ref=np.max),
        sr=sr, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=lo_onset, sr=sr, hop_length=HOP,
                                           start_bpm=128.0, trim=False)
    bpm = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beats, sr=sr, hop_length=HOP)
    # ---- HALF-BEAT PHASE CHECK ----
    # Tracking on the low band fixes most of the offbeat locking but not all of it, because the
    # dynamic program is scored on PERIODICITY and a grid shifted by exactly half a beat is just
    # as periodic as the right one. The half-beat phase is genuinely ambiguous to the tracker,
    # and which of the two it lands on comes down to whatever the envelope happened to favour.
    #
    # For kick-driven music it is not ambiguous to a listener: the beat is where the kick is.
    # That is a fact about the genre, not a property of any estimator, so it can be used to
    # settle the phase directly. Scored on sub-bass LEVEL (30-100Hz RMS, already in S), which is
    # a different signal from the mel FLUX the grid was tracked on -- deliberately, so this is
    # not just re-running the same measurement and agreeing with itself.
    #
    # MEASURED on 30 tracks drawn at random from a working techno/house library, scoring each
    # grid by sub-bass energy on the beat over energy at the half-beat (below 1.0 means the grid
    # sits where the bass is quietest, i.e. between the kicks):
    #     full-band flux, as shipped before   22 of 30 offbeat (73%)   median 0.70x
    #     low band, no phase check             7 of 30 offbeat (23%)   median 1.41x
    #     low band + this check                3 of 30 offbeat (10%)   median 1.65x
    # and this check moves the grid on 4 of 30, correcting all four, breaking none.
    #
    # The 1.15 margin is what keeps it honest. On material where the two phases genuinely score
    # the same -- no kick at all, or a bassline that runs through the beat -- there is no evidence
    # either way and the grid is left exactly as the tracker returned it. Only a clear win moves
    # it. The fixtures, where the truth is known to the sample, must not move at all: they score
    # about 6x in favour of the phase they already have.
    if len(beat_times) >= 8:
        _f = librosa.fft_frequencies(sr=sr, n_fft=2048)
        _sub = np.sqrt((S[(_f >= 30) & (_f <= 100)] ** 2).sum(0))
        _ibi = float(np.median(np.diff(beat_times)))

        # SCORED OVER THE KICK'S ATTACK, not at the instant of the beat. A point sample asks "is
        # the sub-bass loud exactly on the beat", and a kick does not peak at its own onset -- it
        # takes a few ms to develop, so a grid sitting correctly on the transient scores LOW while
        # a sustained bassline running between the kicks scores high. Measured over the same 30
        # tracks, comparing a point sample against a max over the 70ms after each beat:
        #     point sample     5 of 30 offbeat   4 corrected, 2 BROKEN
        #     max over +70ms   3 of 30 offbeat   4 corrected, 0 broken
        # The two it broke were tracks already sitting correctly on the beat -- one at 1.73x
        # flipped to 0.58x -- which is the worst thing this check can do, and the window removes
        # it outright. 70ms is three hops: long enough to contain the attack of any kick, short
        # enough that it cannot reach the following offbeat at any tempo this analyser accepts.
        def _phase_score(times):
            idx = np.round(times * sr / HOP).astype(int)
            vals = [_sub[max(0, i):min(len(_sub), i + 4)].max()
                    for i in idx if min(len(_sub), i + 4) > max(0, i)]
            return float(np.mean(vals)) if vals else 0.0

        if _ibi > 1e-3:
            _here = _phase_score(beat_times)
            _shift = _phase_score(beat_times + _ibi * 0.5)
            if _shift > _here * 1.15:
                beat_times = beat_times + _ibi * 0.5
                beat_times = beat_times[beat_times < len(mono) / sr]
                beats = np.round(beat_times * sr / HOP).astype(int)
    # TEMPO FROM THE WHOLE SPAN, NOT FROM THE MEDIAN GAP.
    #
    # Beats are reported as frame indices, so every beat time is quantised to a 23.2ms hop. A true
    # 0.5s beat is 21.5 hops, so consecutive gaps alternate 21 and 22 frames -- 0.488s and 0.511s
    # -- and the MEDIAN of that pair is whichever side won, not 0.5. On the 120.000 BPM fixture the
    # median gap gave 117.45 BPM, a 2.1% error that is pure quantisation and has nothing to do with
    # the music. Fitting a line through beat index -> beat time averages the quantisation out over
    # the whole track instead of trusting one gap.
    #     truth 120.00 | librosa 117.45 | median gap 117.45 | least squares 120.00  (both fixtures)
    # The renderer advances rotation off the beat GRID rather than this number, so a wrong tempo no
    # longer desynchronises the turn -- but it is still the reported BPM, it still drives modUpdate
    # and the HUD, and it is the fallback whenever the grid is too short to interpolate.
    # PREFERRED OUTRIGHT, not only on a large disagreement. There used to be a 4% threshold here,
    # on the reasoning that only a real metrical disagreement should override librosa's estimate
    # and anything smaller was estimator noise. Quantisation is exactly the sub-4% case -- 117.45
    # against 120.00 is 2.17% -- so the guard rejected precisely the error it was sitting next to,
    # and the fixture came back at 117.45 with the fit already computed and discarded. The fit is
    # not a second opinion about the tempo: it is the spacing of the grid that actually ships, and
    # the client interpolates that grid, so the two must describe the same thing. Guarded only
    # against a degenerate fit (too few beats, or a slope outside any plausible tempo).
    # AND THE FIT IS NOT FOLDED. _fold_tempo brings a stray octave back into 70..160, which is
    # right for a bare estimate and wrong for this one: the fit measures the spacing of the grid
    # that ships in events.beats, and the client interpolates that grid. Folding it makes the
    # reported tempo describe a different metrical level from the beats beside it, and the
    # renderer uses both -- the grid for musical time, the scalar for the kaleidoscope's sweep,
    # the HUD, the genre heuristics and every grid-less fallback.
    # MEASURED on a real upload: a hardcore track whose grid spans 734 beats over 249.3s, i.e.
    # 176.4 BPM, reported 88.16 -- exactly half, because 176.4 is above TEMPO_HI and got folded
    # once. The grid was right and the number beside it was an octave out.
    # The fold stays for the path with no grid to be consistent with.
    fitted = None
    if len(beat_times) >= 4:
        k = np.arange(len(beat_times), dtype=np.float64)
        slope = float(np.polyfit(k, beat_times, 1)[0])
        if slope > 1e-3 and 20.0 <= 60.0 / slope <= 400.0:
            fitted = 60.0 / slope
    f["tempo"] = round(fitted, 2) if fitted is not None else _fold_tempo(bpm)
    f["beat_frames"] = beats
    # ---- DOWNBEATS ----
    # The module docstring has always advertised events.downbeats and the payload has never
    # carried it, so the client had no way to know where a bar starts. It was counting every
    # fourth beat from whichever beat the tracker happened to return first, which lands the bar
    # on an arbitrary phase -- and the two things keyed to it, the downbeat accent and the shape
    # change, are the most visible events in the render.
    # librosa has no downbeat tracker, so infer the phase: of the four ways to group the beats
    # into bars, the true one is the grouping whose first beats carry the most low-frequency
    # onset energy, because that is where the kick sits in almost all metered popular music.
    # Falls back to phase 0 when there is no evidence either way.
    db_times = []
    if len(beat_times) >= 8:
        # the same low-band envelope the grid was tracked on, computed once above
        lo = lo_onset
        bf = np.clip(f["beat_frames"], 0, len(lo) - 1)
        strength = lo[bf]
        best, best_score = 0, -np.inf
        for ph in range(4):
            sc = float(np.mean(strength[ph::4])) if len(strength[ph::4]) else -np.inf
            if sc > best_score:
                best, best_score = ph, sc
        db_times = [float(x) for x in beat_times[best::4]]
    f["downbeat_times"] = db_times
    f["beat_times"]  = beat_times.tolist()
    # tempo stability: dynamic tempo std (API name moved across librosa versions)
    try:
        _tempo_fn = getattr(librosa.feature, "tempo", None) or librosa.beat.tempo
        dtempo = _tempo_fn(onset_envelope=onset, sr=sr, hop_length=HOP, aggregate=None)
        f["tempo_stability"] = float(np.clip(1 - np.std(dtempo) / (np.mean(dtempo) + 1e-6), 0, 1))
    except Exception:
        f["tempo_stability"] = 0.8
    # crest factor & dynamic range (dB) over the loudness curve
    peak = float(np.max(np.abs(mono)) + 1e-9)
    f["crest"] = float(20 * np.log10(peak / (np.sqrt(np.mean(mono**2)) + 1e-9)))
    ld = 20 * np.log10(f["rms"] + 1e-6)
    f["dyn_range_db"] = float(np.percentile(ld, 95) - np.percentile(ld, 10))
    # stereo width / phase correlation
    if stereo.shape[0] >= 2:
        L, R = stereo[0], stereo[1]
        c = np.corrcoef(L, R)[0, 1] if len(L) > 1 else 1.0
        f["phase_corr"] = float(0.0 if np.isnan(c) else c)
        f["stereo_width"] = float(np.clip(1 - (f["phase_corr"] * 0.5 + 0.5), 0, 1))
    else:
        f["phase_corr"], f["stereo_width"] = 1.0, 0.0
    f["n"] = len(f["rms"])
    f["times"] = librosa.frames_to_time(np.arange(f["n"]), sr=sr, hop_length=HOP)
    f["duration"] = float(len(mono) / sr)
    # Collect the separation started at the top. By here it has usually finished already; on a
    # short track or a busy machine this is where the remaining wait lands.
    step("separate")
    _hp_th.join()
    if _hp.get("e") is not None:
        raise _hp["e"]
    f["harm"], f["perc"] = _hp["h"], _hp["p"]
    # per-band levels + the individual hits in each band (done here, while S is in hand)
    step("bands")
    f["band_levels"], f["band_onsets"] = layer1b_bands(S, sr, f["times"])
    return f


# ============================================== PROGRESS REPORTING
# Stage weights are MEASURED, not guessed. Re-measured after the two long stages were moved onto
# their own threads, on the same 3-minute track:
#
#   sequential (before)                        overlapped (now)
#     load        0.00                           load        0.00
#     stft        1.10                           stft        0.09
#     features    0.93                           features    2.34   <- now sharing cores
#     HPSS        6.61                           beats       1.08
#     beats       0.87                           HPSS wait   4.13   <- residual only
#     bands       0.05                           bands       0.05
#     PANNs       5.14                           PANNs wait  0.00   <- finished under cover
#     finalise    0.07                           finalise    0.05
#     TOTAL      14.89                           TOTAL       9.01
#
# What is charged to "separate" and "tagging" is now only the part that did NOT fit under the
# other work, so both shrink and the stages they hid behind grow. HPSS is the critical path and
# everything else runs inside it. The weights are ratios, and every stage scales with track
# length, so they hold for any duration even though the absolute times do not.
#
# tagging keeps a small weight rather than zero: on a short track, or a machine where the model
# load dominates, there is still a real wait there.
STAGE_W = {"load":0.01, "spectrum":0.01, "features":0.26, "beats":0.12,
           "separate":0.46, "bands":0.01, "tagging":0.02, "finalise":0.01}
STAGE_LABEL = {"load":"decoding audio", "spectrum":"spectrum",
               "features":"spectral features", "separate":"harmonic / percussive",
               "beats":"tempo & beats", "bands":"per-band onsets",
               "tagging":"genre & mood (ML)", "finalise":"structure & direction"}

class Progress:
    """Weighted progress with a self-calibrating estimate of the remaining time.

    The single long stage (HPSS) is an opaque library call with no way to report from inside it,
    so the client is given the fraction it starts at, the fraction it ends at, and how long it is
    expected to take; the client eases between the two. That keeps the bar moving through the
    twelve seconds where nothing can be reported, without ever letting it run past the truth.

    The expected total is extrapolated from the work already done rather than from a hard-coded
    rate, so it calibrates itself to whatever machine it is on instead of being right only here.
    """
    def __init__(self, cb, with_panns=True, audio_dur=None):
        self.cb, self.t0, self.done, self.dur = cb, time.time(), 0.0, audio_dur
        w = dict(STAGE_W)
        if not with_panns:
            w["tagging"] = 0.0                       # renormalise; skipped work is not "instant"
        tot = sum(w.values()) or 1.0
        self.w = {k: v / tot for k, v in w.items()}

    def __call__(self, key):
        if not self.cb:
            return
        wk = self.w.get(key, 0.0)
        el = time.time() - self.t0
        if self.done > 0.02:
            total = el / self.done
        elif self.dur:
            total = self.dur * 0.075                 # first stage only: rough seed from duration
        else:
            total = 20.0
        try:
            self.cb({"stage": STAGE_LABEL.get(key, key), "p": round(self.done, 4),
                     "next": round(min(1.0, self.done + wk), 4),
                     "eta": round(max(0.2, wk * total), 2)})
        except Exception:
            pass                                     # progress must never break the analysis
        self.done = min(1.0, self.done + wk)


# ========================================================= LAYER 2: STRUCTURE
def layer2_structure(f, sr):
    import librosa
    n = f["n"]
    # beat-synchronous feature stack (chroma + mfcc) for a stable similarity space
    beats = f["beat_frames"]
    if len(beats) < 4:
        beats = np.linspace(0, n - 1, max(8, n // 40)).astype(int)
    stack = np.vstack([librosa.util.normalize(f["chroma"], axis=1),
                       librosa.util.normalize(f["mfcc"], axis=1)])
    sync = librosa.util.sync(stack, beats, aggregate=np.mean)
    kmax = int(np.clip(f["duration"] / SEG_SECONDS_PER_BOUNDARY, SEG_KMAX_LO, SEG_KMAX_HI))
    bt = [0.0, f["duration"]]
    try:
        bounds = librosa.segment.agglomerative(sync, kmax)      # structural boundaries
        bframes = beats[np.clip(bounds, 0, len(beats) - 1)]
        bt += librosa.frames_to_time(bframes, sr=sr, hop_length=HOP).tolist()
    except Exception:
        bt += np.linspace(0, f["duration"], kmax + 1).tolist()
    # add ENERGY-NOVELTY boundaries so hard build->drop / drop->breakdown changes land
    e = smooth(f["rms"], 9)
    de = np.abs(np.gradient(smooth(e, 21)))
    thr = float(np.percentile(de, 92))
    wait = int(4 * sr / HOP)                       # >=4 s between novelty boundaries
    nov, last = [], -10**9
    for i in range(1, len(de) - 1):
        if de[i] > thr and de[i] >= de[i-1] and de[i] >= de[i+1] and (i - last) > wait:
            nov.append(i); last = i
    nov_t = librosa.frames_to_time(np.array(nov, dtype=int), sr=sr, hop_length=HOP).tolist()
    bt += nov_t

    # Boundaries carry a STRENGTH so the merge below can choose between them. Novelty peaks are
    # scored by how far they clear the threshold; structural boundaries get a flat 1.0; the two
    # track endpoints are pinned so they can never be merged away.
    strength = {}
    for t in bt:
        strength.setdefault(round(t, 2), 1.0)
    for i, t in zip(nov, nov_t):
        k = round(t, 2)
        strength[k] = max(strength.get(k, 0.0), 1.0 + float(de[i] / max(thr, 1e-9)))
    strength[round(0.0, 2)] = 1e9
    strength[round(f["duration"], 2)] = 1e9

    bt = sorted(set(round(x, 2) for x in bt if 0 <= x <= f["duration"]))
    # Merge boundaries closer than the minimum musical section length, KEEPING THE STRONGEST.
    # Taking the earliest instead — which is what a running "is it far enough from the last one"
    # test does — throws away a hard drop whenever a weak structural boundary happens to sit a
    # few seconds before it, and the drop is the one edit in the track that has to land.
    mn = max(6.0, f["duration"] * 0.03)
    merged = []
    group = [bt[0]]
    for t in bt[1:]:
        if t - group[0] < mn:
            group.append(t)
        else:
            merged.append(max(group, key=lambda x: strength.get(x, 1.0)))
            group = [t]
    merged.append(max(group, key=lambda x: strength.get(x, 1.0)))
    # ---- SNAP TO THE BEAT GRID ----
    # The Director cuts its sections on these times, so a boundary that lands mid-bar produces a
    # visual edit that is audibly late or early against the music. Snapping costs nothing and is
    # the difference between a cut that reads as deliberate and one that reads as loose.
    # A downbeat is preferred and given a wide catch (two beats), because sections in almost all
    # metered music begin on one; failing that, any beat, with a tight half-beat catch. Outside
    # both, the boundary is left exactly where the analysis put it rather than dragged onto a
    # grid it does not belong to — a genuine mid-bar event (a filter sweep, a stab) is real.
    bts = librosa.frames_to_time(np.asarray(beats), sr=sr, hop_length=HOP) if len(beats) else np.array([])
    if bts.size >= 2:
        ibi = float(np.median(np.diff(bts)))
        if np.isfinite(ibi) and ibi > 1e-3:
            downs = bts[::4]                                    # assume 4/4
            snapped = []
            for t in merged:
                if t <= 1e-9 or t >= f["duration"] - 1e-9:
                    snapped.append(t); continue                 # endpoints stay pinned
                best = t
                for grid, tol in ((downs, ibi * 2.0), (bts, ibi * 0.5)):
                    if grid.size:
                        j = int(np.argmin(np.abs(grid - t)))
                        if abs(float(grid[j]) - t) <= tol:
                            best = float(grid[j]); break
                snapped.append(round(best, 3))
            # Snapping can collide two boundaries onto one beat; dedupe and re-check spacing.
            out = []
            for t in sorted(set(snapped)):
                if not out or t - out[-1] >= mn * 0.5:
                    out.append(t)
            if len(out) >= 2:
                merged = out
    if merged[-1] < f["duration"] - 1e-3:
        merged[-1] = f["duration"]
    # A track shorter than the minimum section length collapses to a SINGLE boundary, which
    # yields zero sections downstream — and a director with an empty sections[] crashes the
    # renderer's frame loop on the first lookup. Always emit at least one whole-track section.
    if len(merged) < 2:
        merged = [0.0, max(f["duration"], 0.05)]
    return merged   # boundary times

# ================================================== LAYER 2c: TONALITY
# Krumhansl-Schmuckler. The chroma-CQT was already being computed — the most expensive single
# feature after HPSS — and used only to give the segmenter a stable similarity space. The key
# was sitting in it unread.
KS_MAJOR = np.array([6.35,2.23,3.48,2.33,4.38,4.09,2.52,5.19,2.39,3.66,2.29,2.88])
KS_MINOR = np.array([6.33,2.68,3.52,5.38,2.60,3.53,2.54,4.75,3.98,2.69,3.34,3.17])
PITCHES  = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]


def layer2c_tonality(f):
    """Key, mode, and how tonal the track is at all.

    `confidence` is the MARGIN over the best competing key, not the raw correlation. Every key
    correlates decently with a chromatic average, so the raw number is high and flat and says
    nothing; what carries information is how far the winner sits above the runner-up.

    `strength` is separate and answers a different question — whether the track is tonal in the
    first place. A drum loop or a noise wash has a near-flat chroma, which still produces a
    winning key, just a meaningless one. Measured as distance from a uniform distribution, so
    the renderer can ignore the key when there is not really one.
    """
    ch = np.asarray(f["chroma"], dtype=np.float64)
    prof = ch.mean(axis=1)
    tot = float(prof.sum())
    if not np.isfinite(tot) or tot <= 1e-9:
        return {"key": None, "mode": None, "confidence": 0.0, "strength": 0.0}
    prof = prof / tot
    scored = []
    for mode, tmpl in (("major", KS_MAJOR), ("minor", KS_MINOR)):
        t = tmpl / tmpl.sum()
        for r in range(12):
            c = np.corrcoef(prof, np.roll(t, r))[0, 1]
            if np.isfinite(c):
                scored.append((float(c), PITCHES[r], mode))
    if not scored:
        return {"key": None, "mode": None, "confidence": 0.0, "strength": 0.0}
    scored.sort(key=lambda x: -x[0])
    top = scored[0]
    rival = next((x for x in scored[1:] if x[1] != top[1]), (0.0, "", ""))
    margin = float(np.clip((top[0] - rival[0]) / 0.35, 0, 1))
    # entropy against uniform: 0 = flat chroma (no key worth reporting), 1 = one pitch class
    ent = -np.sum(prof * np.log(prof + 1e-12)) / np.log(12.0)
    strength = float(np.clip(1.0 - ent, 0, 1) * 3.0)
    return {"key": top[1], "mode": top[2],
            "confidence": round(margin, 3),
            "strength": round(min(1.0, strength), 3)}


def onset_rate(f, win_s=2.0):
    """Onsets per second across every band, on the feature time grid.

    This is what DENSITY was supposed to be. It shipped as `density=danceability` — a straight
    alias, so the contract advertised a distinct curve and delivered a duplicate of one already
    in it. How busy a passage is and how danceable it is are not the same question: a half-time
    breakdown over a dense hat pattern is high density and low danceability.
    """
    t = np.asarray(f["times"], dtype=np.float64)
    allon = []
    for lst in f["band_onsets"].values():
        allon += [o[0] for o in lst]
    if not allon:
        return np.zeros(len(t))
    a = np.sort(np.asarray(allon, dtype=np.float64))
    left = np.searchsorted(a, t - win_s * 0.5)
    right = np.searchsorted(a, t + win_s * 0.5)
    return (right - left) / win_s


# =================================================== LAYER 3: EMOTION CURVES
def layer3_emotion(f, moods=None):
    energy   = nrm(smooth(f["rms"], 9))
    bright   = nrm(smooth(f["centroid"], 9))
    flux     = nrm(smooth(f["flux"], 9))
    perc     = nrm(smooth(f["perc"], 9))
    harm     = nrm(smooth(f["harm"], 9))
    flat     = nrm(smooth(f["flatness"], 9))
    low      = 1 - bright
    # continuous emotional dimensions (heuristic, musically motivated, smoothed)
    arousal    = nrm(smooth(0.5 * energy + 0.3 * flux + 0.2 * perc, 15))
    tension    = nrm(smooth(0.45 * flux + 0.3 * flat + 0.25 * np.abs(np.gradient(smooth(energy, 15))) * 30, 20))
    darkness   = nrm(smooth(0.6 * low + 0.4 * (1 - energy), 15))
    warmth     = nrm(smooth(0.5 * harm + 0.5 * low, 15))
    danceability = nrm(smooth(0.6 * perc + 0.4 * energy, 21))
    # valence: brighter + harmonic + steady groove reads more positive
    valence    = nrm(smooth(0.4 * bright + 0.3 * harm + 0.3 * danceability - 0.2 * tension + 0.2, 21))
    epicness   = nrm(smooth(0.5 * energy + 0.3 * f_std(f["rms"]) + 0.2 * bright, 25))
    # RELEASE — the other half of tension, and not simply its inverse. Low tension is a calm
    # passage; RELEASE is the moment tension is actively being let go, which is where the visual
    # payoff belongs. Rectified negative slope of tension: positive only while it is falling,
    # zero while it is merely low.
    release = nrm(smooth(np.maximum(-np.gradient(smooth(tension, 21)), 0) * 60, 25))
    # DYNAMICS — local loudness variation, not the whole-track scalar. dynamic_range_db already
    # reports one number for the track; this is where the track is BEING dynamic versus where it
    # is squashed flat, which is what separates a live-sounding passage from a limited one.
    ld = 20 * np.log10(np.asarray(f["rms"]) + 1e-6)
    dynamics = nrm(smooth(np.abs(ld - smooth(ld, 101)), 31))
    # ATMOSPHERE — sustained harmonic wash against transient attack. High where the track is pads,
    # tails and reverb; low where it is hits. Drives fog/haze rather than motion.
    atmosphere = nrm(smooth(harm / (harm + perc + 1e-6) * (1 - flux), 31))
    # DENSITY — a real measurement now, see onset_rate(). Was an alias of danceability.
    density = nrm(smooth(onset_rate(f), 15))
    if moods:   # gentle whole-track bias from the ML mood tags
        g = moods.get
        vb = 0.15*(g("happy",0)+g("tender",0)+0.5*g("exciting",0)) - 0.15*(g("sad",0)+g("scary",0)+g("angry",0))
        tb = 0.15*(g("scary",0)+g("angry",0)+g("exciting",0)) - 0.10*g("tender",0)
        db = 0.15*(g("sad",0)+g("scary",0)) - 0.10*g("happy",0)
        valence = np.clip(valence+vb, 0, 1); tension = np.clip(tension+tb, 0, 1); darkness = np.clip(darkness+db, 0, 1)
    return dict(energy=energy, brightness=bright, arousal=arousal, valence=valence,
                tension=tension, darkness=darkness, warmth=warmth,
                danceability=danceability, epicness=epicness,
                flux=flux, percussive=perc, harmonic=harm, density=density,
                release=release, dynamics=dynamics, atmosphere=atmosphere)

def f_std(a):
    a = np.asarray(a); m = smooth(a, 41)
    return nrm(np.abs(a - m))

# ============================================================ LAYER 4: GENRE
def layer4_genre(f, cur):
    """Heuristic estimator with a clean interface. Swap in a tagging model (PANNs/musicnn/CLAP)
    behind this function later; the Director never depends on how genre was produced."""
    bpm = f["tempo"]; perc = float(np.mean(cur["percussive"])); bright = float(np.mean(cur["brightness"]))
    harm = float(np.mean(cur["harmonic"])); flat = float(np.mean(f["flatness"]))
    def near(x, c, w): return max(0.0, 1 - abs(x - c) / w)
    scores = {
        "techno":        0.6 * near(bpm, 130, 18) + 0.4 * perc,
        "melodic techno":0.5 * near(bpm, 124, 12) + 0.3 * harm + 0.2 * bright,
        "house":         0.6 * near(bpm, 124, 12) + 0.4 * perc,
        "drum & bass":   0.7 * near(bpm, 174, 16) + 0.3 * perc,
        "dubstep":       0.6 * near(bpm, 140, 12) + 0.4 * (1 - harm),
        "ambient":       0.6 * (1 - perc) + 0.4 * (1 - near(bpm, 120, 40)),
        "hip hop":       0.6 * near(bpm, 90, 16) + 0.4 * perc,
        "cinematic":     0.5 * harm + 0.3 * (1 - perc) + 0.2 * near(bpm, 90, 40),
    }
    order = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    tot = sum(max(0, v) for _, v in order) + 1e-6
    return {"primary": order[0][0], "confidence": round(order[0][1] / tot, 3),
            "secondary": order[1][0], "method": "heuristic",
            "scores": {k: round(v, 3) for k, v in order[:4]}}

# ======================================== LAYERS 2b + 6: LABELS & DIRECTOR
CAMERAS   = {"intro":"wide_drift","build":"slow_push","drop":"orbit","chorus":"orbit",
            "groove":"tracking","breakdown":"pull_back","outro":"retreat"}
COMPS_BY_E = ["column","cluster","radial","spiral","grid"]   # low->high energy layouts

def label_and_direct(bounds, f, cur, genre):
    t = f["times"]
    e = cur["energy"]; ten = cur["tension"]; val = cur["valence"]
    dark = cur["darkness"]; warm = cur["warmth"]; ar = cur["arousal"]
    peak = float(np.percentile(e, 95))
    secs = []
    prev_e = 0.0; prev_role = ""
    import random
    rng = random.Random(0xA70)             # deterministic-per-run randomness for coherent variety
    for i in range(len(bounds) - 1):
        t0, t1 = bounds[i], bounds[i + 1]
        m = (t >= t0) & (t < t1)
        if not np.any(m):
            continue
        se, sten, sval, sdark, swarm, sar = [float(np.mean(x[m])) for x in (e, ten, val, dark, warm, ar)]
        srel, sdyn, satm, sden = [float(np.mean(cur[k][m])) for k in
                                  ("release", "dynamics", "atmosphere", "density")]
        rise = float(np.mean(np.gradient(smooth(e, 15))[m])) * 100
        last = i == len(bounds) - 2
        # ---- role / section label (Layer 2 semantic labelling) ----
        if i == 0 and se < peak * 0.82:                       role = "intro"
        elif last and se < prev_e - 0.05:                     role = "outro"
        elif rise > 0.35 and se < peak * 0.9:                 role = "build"
        elif se > peak * 0.78 and (se > prev_e + 0.08 or prev_role in ("build", "breakdown", "intro")):
            role = "drop"
        elif se < peak * 0.5:                                 role = "breakdown"
        else:                                                 role = "groove"
        prev_e = se; prev_role = role
        # ---- Layer 6: cinematic decision for the section ----
        comp = COMPS_BY_E[min(4, int(se * 4 + (1 if role == "drop" else 0)))]
        if rng.random() < 0.35:                   # unpredictable-but-on-style variety
            comp = rng.choice(["cluster", "radial", "spiral", "grid", "column"])
        mood = ("violet" if sdark > 0.6 else "amber" if swarm > 0.6 else
                "ice" if sval > 0.6 else "rose" if sval > 0.4 else "pearl")
        # label colourways: every mood carries a CLEAR hue at medium saturation. The renderer
        # tints the form with this directly and slams the saturation for the background ink,
        # so a near-grey palette here would give a washed-out backdrop. "mono" stays neutral
        # on purpose — it becomes the stark black & white press look.
        palette = {"violet":[0.58,0.42,0.92],"amber":[0.95,0.62,0.30],"ice":[0.45,0.80,0.98],
                   "rose":[0.95,0.48,0.62],"pearl":[0.62,0.68,0.92],"mono":[0.80,0.80,0.85]}[mood]
        intent = semantic_intent(role, sten, sval, sdark, rise)
        secs.append({
            "t0": round(t0, 3), "t1": round(t1, 3), "label": role,
            "energy": round(se, 3), "tension": round(sten, 3), "valence": round(sval, 3),
            "camera": CAMERAS.get(role, "orbit"), "composition": comp, "mood": mood,
            "palette": [round(c, 3) for c in palette],
            "release": round(srel, 3), "dynamics": round(sdyn, 3),
            "atmosphere": round(satm, 3), "density": round(sden, 3),
            "motion": round(float(np.clip(0.25 + sar * 0.7, 0, 1)), 3),
            # Atmosphere earns its place here: fog was driven by darkness alone, so a bright,
            # airy pad passage got no haze while a dark percussive one got plenty. Sustained
            # harmonic content is what actually reads as air in a room.
            "fog": round(float(np.clip(0.2 + sdark * 0.45 + satm * 0.30, 0, 1)), 3),
            "bloom": round(float(np.clip(0.3 + se * 0.5, 0, 1)), 3),
            "grain": round(float(np.clip(0.2 + sdark * 0.4, 0, 1)), 3),
            "intent": intent,
        })
    return secs

def semantic_intent(role, tension, val, dark, rise):
    """Layer 5 (rule-based): a short human-readable read of the section."""
    bits = []
    bits.append({"intro":"Establish the world","build":"Accumulate tension",
                 "drop":"Impact and release","groove":"Hold the world in motion",
                 "breakdown":"Strip back, let it breathe","outro":"Dissolve"}[role])
    if rise > 0.8: bits.append("anticipation rising")
    if dark > 0.65: bits.append("darkening")
    elif val > 0.65: bits.append("uplifting")
    if tension > 0.7: bits.append("high tension")
    return ", ".join(bits)

# ==================================================== LAYER 10: PREDICTION
def layer10_predict(secs):
    ev = []
    for i, s in enumerate(secs):
        nxt = secs[i + 1] if i + 1 < len(secs) else None
        if nxt and nxt["label"] in ("drop", "chorus"):
            ev.append({"t": nxt["t0"], "type": "drop", "from": s["t0"],
                       "lead": round(min(6.0, nxt["t0"] - s["t0"]), 2)})
        elif nxt:
            ev.append({"t": nxt["t0"], "type": "transition", "to": nxt["label"],
                       "lead": round(min(4.0, nxt["t0"] - s["t0"]), 2)})
    return ev

# ================================================================== ASSEMBLE
def build_director(path, progress=None):
    """progress, if given, is called with {stage, p, next, eta} as each stage begins."""
    prog = Progress(progress, with_panns=tagger.available()) if progress else None
    if prog: prog("load")
    print(f"[load] {os.path.basename(path)}")
    mono, stereo, sr = load_audio(path)
    if prog: prog.dur = len(mono) / float(sr)
    # PANNS RUNS ALONGSIDE LAYER 1, not after it. The two are genuinely independent -- tag() takes
    # only (mono, sr) and layer1_signal never sees the tags -- and between them they were 79% of
    # the wall clock (HPSS 6.6s, PANNs 5.1s of a 14.9s run on a 3-minute track). Running them in
    # sequence was costing the whole of the shorter one for nothing.
    #
    # A thread, not a process: both sides spend nearly all their time inside numpy/scipy and
    # torch, which release the GIL, so they overlap properly. A process would have to ship the
    # audio across a pipe and load a second copy of the model.
    #
    # The result is identical to the sequential version, not approximately identical: the same
    # calls run on the same inputs, only overlapped.
    panns, _panns_err, _panns_th = None, None, None
    if tagger.available():
        _box = {}
        def _tag_worker():
            try: _box["r"] = tagger.tag(mono, sr)
            except Exception as e: _box["e"] = e
        _panns_th = threading.Thread(target=_tag_worker, name="panns", daemon=True)
        print(f"[layer4] PANNs tagging (ML), in parallel with layer 1…")
        _panns_th.start()
    print(f"[layer1] signal features…")
    f = layer1_signal(mono, stereo, sr, prog)
    if _panns_th is not None:
        if prog: prog("tagging")          # only the RESIDUAL wait is charged to this stage now
        _panns_th.join()
        panns = _box.get("r")
        if _box.get("e"): print("  panns failed -> heuristic:", _box["e"])
    if prog: prog("finalise")
    print(f"[layer3] emotion curves…")
    cur = layer3_emotion(f, panns.get("moods") if panns else None)
    print(f"[layer2] structure…")
    bounds = layer2_structure(f, sr)
    print(f"[layer2c] tonality…")
    tonality = layer2c_tonality(f)
    print(f"[layer4] genre…")
    genre = panns or layer4_genre(f, cur)
    print(f"[layer1b] per-band sounds: " +
          ", ".join(f"{k}={len(v)}" for k, v in f["band_onsets"].items()))
    print(f"[layer6] director / sections…")
    secs = label_and_direct(bounds, f, cur, genre)
    events = layer10_predict(secs)
    # resample continuous curves to DIRECTOR_FPS
    dur = f["duration"]
    td = np.arange(0, dur, 1.0 / DIRECTOR_FPS)
    curves = {"t": [round(float(x), 3) for x in td]}
    for k in ("energy","brightness","arousal","valence","tension","darkness",
              "warmth","danceability","epicness","flux","percussive","harmonic","density",
              "release","dynamics","atmosphere"):
        curves[k] = [round(float(x), 4) for x in resample_curve(f["times"], cur[k], td)]
    # per-band sustained levels, normalised the same way as the emotion curves
    for k, lv in f["band_levels"].items():
        curves[k] = [round(float(x), 4) for x in resample_curve(f["times"], nrm(smooth(lv, 5)), td)]
    out = {
        "schema": "atonal.director/1",
        "meta": {"source": os.path.basename(path), "duration": round(dur, 3),
                 "director_fps": DIRECTOR_FPS, "analysis_sr": sr, "hop": HOP,
                 "analysis_version": ANALYSIS_VERSION},
        "tempo": {"bpm": round(f["tempo"], 2), "stability": round(f["tempo_stability"], 3),
                  "crest_db": round(f["crest"], 2), "dynamic_range_db": round(f["dyn_range_db"], 2),
                  "stereo_width": round(f["stereo_width"], 3)},
        "tonality": tonality,
        # Instrumentation and voice come from the tags the PANNs pass already produced. Absent on
        # the heuristic fallback, which has no way to know what is playing — the key stays present
        # either way, since it is derived from the signal rather than from the model.
        "instruments": (panns or {}).get("instruments", {}),
        "vocals": (panns or {}).get("vocals", {"presence": 0.0, "is_vocal": False, "types": {}}),
        "genre": genre,
        "sections": secs,
        "events": {"beats": [round(x, 3) for x in f["beat_times"]],
                   "downbeats": [round(x, 3) for x in f.get("downbeat_times", [])],
                   "onsets": f["band_onsets"],          # the individual sounds, per band
                   "predictions": events},
        "curves": curves,
    }
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default="director.json")
    a = ap.parse_args()
    d = build_director(a.input)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fp:
        json.dump(d, fp)
    kb = os.path.getsize(a.out) / 1024
    print(f"[done] {a.out}  ({kb:.0f} KB)")
    print(f"       bpm={d['tempo']['bpm']}  genre={d['genre']['primary']} "
          f"({d['genre']['confidence']})  sections={len(d['sections'])}")
    for s in d["sections"]:
        print(f"        {s['t0']:6.1f}-{s['t1']:6.1f}  {s['label']:<10} "
              f"{s['composition']:<8} {s['mood']:<7} e={s['energy']:.2f} — {s['intent']}")

if __name__ == "__main__":
    main()
