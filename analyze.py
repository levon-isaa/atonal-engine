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
import sys, os, json, argparse, tempfile, subprocess, math
import numpy as np
import tagger   # Layer 4 ML tagging (PANNs), optional

SR = 22050            # analysis sample rate
HOP = 512             # ~23 ms frames at 22.05k
DIRECTOR_FPS = 30     # continuous-curve output rate

# ----------------------------------------------------------------------------- IO
def load_audio(path):
    """Decode anything to mono+stereo float via the bundled ffmpeg, load with soundfile."""
    import soundfile as sf, imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = tempfile.mktemp(suffix=".wav")
    subprocess.run([ff, "-y", "-i", path, "-ac", "2", "-ar", str(SR), "-f", "wav", tmp],
                   check=True, capture_output=True)
    data, sr = sf.read(tmp, dtype="float32", always_2d=True)   # (n, ch)
    os.remove(tmp)
    stereo = data.T                                            # (ch, n)
    mono = stereo.mean(axis=0)
    return mono, stereo, sr

# --------------------------------------------------------------------- utilities
def nrm(a, lo=5, hi=95):
    """Robust 0..1 normalise using percentiles (resists outliers)."""
    a = np.asarray(a, dtype=np.float64)
    p1, p2 = np.percentile(a, lo), np.percentile(a, hi)
    if p2 - p1 < 1e-9:
        return np.zeros_like(a)
    return np.clip((a - p1) / (p2 - p1), 0, 1)

def smooth(a, win):
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
def layer1_signal(mono, stereo, sr):
    import librosa
    S = np.abs(librosa.stft(mono, n_fft=2048, hop_length=HOP))
    f = {}
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
    # harmonic / percussive separation -> their energies
    H, P = librosa.effects.hpss(mono)
    f["harm"] = librosa.feature.rms(y=H, hop_length=HOP)[0]
    f["perc"] = librosa.feature.rms(y=P, hop_length=HOP)[0]
    # tempo / beats / downbeats
    onset = f["flux"]
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, hop_length=HOP, trim=False)
    f["tempo"] = float(np.atleast_1d(tempo)[0])
    f["beat_frames"] = beats
    f["beat_times"]  = librosa.frames_to_time(beats, sr=sr, hop_length=HOP).tolist()
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
    # per-band levels + the individual hits in each band (done here, while S is in hand)
    f["band_levels"], f["band_onsets"] = layer1b_bands(S, sr, f["times"])
    return f

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
    kmax = int(np.clip(f["duration"] / 8.0, 6, 16))             # finer than before
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
    bt += librosa.frames_to_time(np.array(nov, dtype=int), sr=sr, hop_length=HOP).tolist()
    bt = sorted(set(round(x, 2) for x in bt if 0 <= x <= f["duration"]))
    # merge boundaries closer than the minimum musical section length
    mn = max(6.0, f["duration"] * 0.03)
    merged = [bt[0]]
    for t in bt[1:]:
        if t - merged[-1] >= mn:
            merged.append(t)
    if merged[-1] < f["duration"] - 1e-3:
        merged[-1] = f["duration"]
    return merged   # boundary times

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
    if moods:   # gentle whole-track bias from the ML mood tags
        g = moods.get
        vb = 0.15*(g("happy",0)+g("tender",0)+0.5*g("exciting",0)) - 0.15*(g("sad",0)+g("scary",0)+g("angry",0))
        tb = 0.15*(g("scary",0)+g("angry",0)+g("exciting",0)) - 0.10*g("tender",0)
        db = 0.15*(g("sad",0)+g("scary",0)) - 0.10*g("happy",0)
        valence = np.clip(valence+vb, 0, 1); tension = np.clip(tension+tb, 0, 1); darkness = np.clip(darkness+db, 0, 1)
    return dict(energy=energy, brightness=bright, arousal=arousal, valence=valence,
                tension=tension, darkness=darkness, warmth=warmth,
                danceability=danceability, epicness=epicness,
                flux=flux, percussive=perc, harmonic=harm, density=danceability)

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
            "motion": round(float(np.clip(0.25 + sar * 0.7, 0, 1)), 3),
            "fog": round(float(np.clip(0.2 + sdark * 0.6, 0, 1)), 3),
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
def build_director(path):
    print(f"[load] {os.path.basename(path)}")
    mono, stereo, sr = load_audio(path)
    print(f"[layer1] signal features…")
    f = layer1_signal(mono, stereo, sr)
    panns = None
    if tagger.available():
        print(f"[layer4] PANNs tagging (ML)…")
        try: panns = tagger.tag(mono, sr)
        except Exception as e: print("  panns failed -> heuristic:", e); panns = None
    print(f"[layer3] emotion curves…")
    cur = layer3_emotion(f, panns.get("moods") if panns else None)
    print(f"[layer2] structure…")
    bounds = layer2_structure(f, sr)
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
              "warmth","danceability","epicness","flux","percussive","harmonic","density"):
        curves[k] = [round(float(x), 4) for x in resample_curve(f["times"], cur[k], td)]
    # per-band sustained levels, normalised the same way as the emotion curves
    for k, lv in f["band_levels"].items():
        curves[k] = [round(float(x), 4) for x in resample_curve(f["times"], nrm(smooth(lv, 5)), td)]
    out = {
        "schema": "atonal.director/1",
        "meta": {"source": os.path.basename(path), "duration": round(dur, 3),
                 "director_fps": DIRECTOR_FPS, "analysis_sr": sr, "hop": HOP},
        "tempo": {"bpm": round(f["tempo"], 2), "stability": round(f["tempo_stability"], 3),
                  "crest_db": round(f["crest"], 2), "dynamic_range_db": round(f["dyn_range_db"], 2),
                  "stereo_width": round(f["stereo_width"], 3)},
        "genre": genre,
        "sections": secs,
        "events": {"beats": [round(x, 3) for x in f["beat_times"]],
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
