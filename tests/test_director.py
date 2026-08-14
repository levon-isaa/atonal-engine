#!/usr/bin/env python3
"""Contract tests for director.json — the single interface the renderer consumes.

Deliberately dependency-free (no pytest): the venv installs only what the pipeline needs, and
a test that cannot run because of a missing dev dependency protects nothing.

    python tests/test_director.py

Audio is synthesised here rather than committed as a fixture. A generated click track has a
tempo we KNOW, which is what makes the tempo assertions meaningful — with a real track we would
only be asserting that today's output matches yesterday's, including its mistakes.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyze  # noqa: E402

SR = 44100
FAILURES = []


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILURES.append(msg)


def make_track(bpm, seconds=16.0, offbeat_hats=True):
    """A kick on every beat, optional hats on the 8ths, sub bass, and a pad entering halfway.

    The hats matter: offbeat energy is exactly what pushed the beat tracker onto a 3:2 relative
    of the true tempo, so leaving them in keeps the regression this guards against reachable.
    """
    n = int(SR * seconds)
    x = np.zeros(n, dtype=np.float64)
    spb = 60.0 / bpm

    for beat in np.arange(0.0, seconds, spb):
        i = int(beat * SR)
        length = min(int(SR * 0.25), n - i)
        if length <= 0:
            continue
        k = np.arange(length)
        env = np.exp(-k / (SR * 0.055))
        freq = np.linspace(125.0, 45.0, length)
        x[i:i + length] += 0.95 * env * np.sin(2 * np.pi * np.cumsum(freq) / SR)

    if offbeat_hats:
        for beat in np.arange(spb / 2, seconds, spb / 2):
            i = int(beat * SR)
            length = min(int(SR / 22), n - i)
            if length <= 0:
                continue
            k = np.arange(length)
            x[i:i + length] += 0.30 * np.exp(-k / (SR * 0.009)) * np.random.randn(length)

    t = np.arange(n) / SR
    x += 0.22 * np.sin(2 * np.pi * 55 * t)
    x += 0.16 * np.sin(2 * np.pi * 220 * t) * (t > seconds * 0.5)
    x = np.clip(x, -1.0, 1.0)

    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, np.stack([x, x * 0.95], axis=1), SR)
    return path


def test_fold_tempo():
    print("\n_fold_tempo")
    check(abs(analyze._fold_tempo(140.0) - 140.0) < 1e-6, "in-range tempo passes through")
    check(abs(analyze._fold_tempo(280.0) - 140.0) < 1e-6, "double-time folds down")
    # 35 -> 70 stops at the first octave inside the range; folding must not keep going past it
    check(abs(analyze._fold_tempo(35.0) - 70.0) < 1e-6, "folds up only until it is in range")
    for raw in (35.0, 65.0, 300.0, 41.0):
        folded = analyze._fold_tempo(raw)
        ratio = folded / raw
        octave = abs(np.log2(ratio) - round(np.log2(ratio))) < 1e-6
        check(analyze.TEMPO_LO <= folded <= analyze.TEMPO_HI and octave,
              f"{raw} -> {folded} is in range and an exact octave of the input")
    # degenerate inputs must not hang or propagate a NaN into the renderer's spin rate
    check(analyze._fold_tempo(0.0) == 120.0, "zero falls back to 120")
    check(analyze._fold_tempo(float("nan")) == 120.0, "NaN falls back to 120")
    check(analyze._fold_tempo(float("inf")) == 120.0, "inf falls back to 120")


def test_schema_and_tempo():
    print("\ndirector contract")
    path = make_track(128.0)
    try:
        d = analyze.build_director(path)
    finally:
        os.remove(path)

    for key in ("schema", "meta", "tempo", "genre", "sections", "events", "curves"):
        check(key in d, f"top-level key '{key}'")
    check(d["schema"] == "atonal.director/1", "schema id is atonal.director/1")

    meta = d["meta"]
    for key in ("duration", "director_fps", "analysis_sr", "hop"):
        check(key in meta, f"meta.{key}")
    check(abs(meta["duration"] - 16.0) < 0.5, f"duration ~16s (got {meta['duration']})")

    bpm = d["tempo"]["bpm"]
    check(analyze.TEMPO_LO <= bpm <= analyze.TEMPO_HI, f"bpm inside fold range (got {bpm})")
    # The renderer locks its spin rate to this, so a metrical error shows up directly as the
    # form turning at the wrong speed. 6% covers estimator noise but not a 3:2 or 2:1 error.
    check(abs(bpm - 128.0) / 128.0 < 0.06, f"bpm within 6% of the true 128 (got {bpm})")

    check(len(d["sections"]) >= 1, "at least one section")
    for s in d["sections"]:
        for key in ("t0", "t1", "label", "camera", "composition", "mood", "palette", "motion"):
            if key not in s:
                check(False, f"section missing '{key}'")
                break
        else:
            continue
        break
    else:
        check(True, "every section carries its director fields")

    check(sorted(s["t0"] for s in d["sections"]) == [s["t0"] for s in d["sections"]],
          "sections are ordered by start time")

    ev = d["events"]
    check("beats" in ev and len(ev["beats"]) > 0, "beats present")
    check("onsets" in ev, "onsets present")
    for band in ("sub", "low", "mid", "high", "air"):
        check(band in ev["onsets"], f"onsets.{band}")

    cur = d["curves"]
    n = len(cur["t"])
    check(n > 0, "curves have samples")
    ragged = [k for k, v in cur.items() if isinstance(v, list) and len(v) != n]
    check(not ragged, f"all curves share one length ({n}){' — ragged: ' + str(ragged) if ragged else ''}")
    for name in ("energy", "valence", "tension", "arousal", "darkness", "sub", "air"):
        check(name in cur, f"curves.{name}")

    # the renderer indexes curves by time; a NaN would silently poison a uniform
    bad = [k for k, v in cur.items()
           if isinstance(v, list) and v and not np.all(np.isfinite(np.asarray(v, dtype=float)))]
    check(not bad, f"no NaN/inf in curves{' — ' + str(bad) if bad else ''}")


def test_tempo_across_range():
    print("\ntempo across the usable range")
    for bpm in (100.0, 140.0):
        path = make_track(bpm, seconds=14.0)
        try:
            got = analyze.build_director(path)["tempo"]["bpm"]
        finally:
            os.remove(path)
        check(abs(got - bpm) / bpm < 0.06, f"{bpm:.0f}bpm detected as {got} (within 6%)")


if __name__ == "__main__":
    test_fold_tempo()
    test_schema_and_tempo()
    test_tempo_across_range()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("all passed")
