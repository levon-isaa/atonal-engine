#!/usr/bin/env python3
"""Accuracy tests for the analysis: is it RIGHT, not merely unchanged.

    python tests/test_analysis.py            everything
    python tests/test_analysis.py tempo      one group (tempo, structure, downbeat, phase)

Dependency-free and self-checking in the same way as test_director.py next door, which covers
the CONTRACT -- the shape of director.json and that it survives degenerate input. This file
covers the CONTENT: the tempo, the section boundaries and the bar grid, each against a fixture
whose answer is known by construction.

Every threshold below is a measurement, not a preference, and each records what the code scored
before the defect it guards was fixed. They are set with margin: the point is to catch a real
regression, not to fail on the third decimal place.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyze                      # noqa: E402
import fixtures                     # noqa: E402  (same directory)

FAILURES = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def _score(truth, got, tol):
    """recall, precision against a tolerance in seconds."""
    used = set()
    for tr in truth:
        cand = sorted((abs(g - tr), g) for g in got if g not in used and abs(g - tr) <= tol)
        if cand:
            used.add(cand[0][1])
    rec = len(used) / max(1, len(truth))
    prec = len(used) / max(1, len(got))
    return rec, prec


# --------------------------------------------------------------------------- tempo
def test_tempo():
    """Everything from 178 BPM up used to come back at exactly half, GRID INCLUDED, because
    librosa's log-normal prior around start_bpm=128 puts 180 and 90 almost equidistant."""
    print("\ntempo, 70 to 200 BPM   (was: 178/180/190/200 all halved)")
    for bpm in (70, 100, 120, 128, 140, 160, 174, 178, 180, 190, 200):
        path, want = fixtures.tempo_track(float(bpm))
        d = analyze.build_director(path)
        got = d["tempo"]["bpm"]
        beats = d["events"]["beats"]
        grid = 60.0 / float(np.median(np.diff(beats))) if len(beats) > 3 else float("nan")
        check(abs(got / want - 1.0) < 0.04,
              "%3d BPM -> %7.2f (grid %7.2f)" % (bpm, got, grid))


# ----------------------------------------------------------------------- structure
def test_structure():
    """Structural boundaries carried a flat strength of 1.0 while a novelty peak carried
    1 + de/thr, and de > thr is the test for being a peak -- so loudness won every contest.
    Measured before the fix on this fixture: recall 57%, precision 40%, F1 0.47."""
    print("\nsection boundaries, structure vs loudness   (was: recall 57%, F1 0.47)")
    path, truth, _ = fixtures.structure_track()
    d = analyze.build_director(path)
    got = [round(s["t0"], 2) for s in d["sections"]][1:]
    got = [g for g in got if g < d["meta"]["duration"] - 0.5]
    rec, prec = _score(truth, got, 2.0)
    f1 = 0.0 if rec + prec == 0 else 2 * rec * prec / (rec + prec)
    # THESE THRESHOLDS ARE SET TO SEPARATE TWO MEASURED STATES, not picked for comfort.
    # 57%/0.47 was with BOTH the flat structural strength AND the earliest-first novelty
    # picker. With the picker fixed, the flat strength alone still reaches recall 100%,
    # precision 70%, F1 0.82 -- verified by running this test with ATONAL_SEG_STRUCT_GAIN=0.
    # So a threshold at 0.80 would have passed the very regression it exists to catch. The
    # analysis is deterministic on a generated fixture, so the gap between 0.82 and 0.88 is
    # real and repeatable, and the bar sits inside it.
    check(rec >= 0.95, "recall %.0f%% of %d boundaries" % (rec * 100, len(truth)))
    check(prec >= 0.74, "precision %.0f%% (%d found; flat-strength scores 70%%)" % (prec * 100, len(got)))
    check(f1 >= 0.85, "F1 %.2f (flat-strength scores 0.82)" % f1)


def test_energy_only_boundaries():
    """The guard on the above: two boundaries here move only the LEVEL, same chord and timbre.
    They are what the energy-novelty path exists for and what a structure-first change could
    quietly break."""
    print("\nenergy-only boundaries   (the case novelty exists for)")
    path, truth, energy_only = fixtures.energy_only_track()
    d = analyze.build_director(path)
    got = [round(s["t0"], 2) for s in d["sections"]][1:]
    got = [g for g in got if g < d["meta"]["duration"] - 0.5]
    rec, prec = _score(truth, got, 2.0)
    found = sum(1 for tr in energy_only if any(abs(g - tr) <= 2.0 for g in got))
    check(rec >= 0.95, "recall %.0f%% of %d boundaries" % (rec * 100, len(truth)))
    check(found == len(energy_only),
          "%d of %d level-only boundaries found" % (found, len(energy_only)))


# ------------------------------------------------------------------------ downbeats
def test_downbeat_phase():
    """The phase was scored on the low band alone, so in four-to-the-floor -- where every beat
    carries the same kick -- all four phases scored alike and the pick was noise. Measured
    before the fix: 2 of 4 offsets correct, with the four scores within 25% of each other."""
    print("\nbar phase   (was: 2/4 on four-to-the-floor, 4/4 on kick-only)")
    for ff, label in ((True, "four-to-the-floor, bar marked by crash + chord"),
                      (False, "kick on bar one only")):
        ok = 0
        for off in (0, 1, 2, 3):
            path, truth, bpm = fixtures.downbeat_track(off, four_to_the_floor=ff)
            db = analyze.build_director(path)["events"]["downbeats"]
            beat = 60.0 / bpm
            ok += bool(db) and round((db[0] - truth[0]) / beat) % 4 == 0
        check(ok == 4, "%d/4 offsets correct -- %s" % (ok, label))


def test_phase_shift():
    """One odd bar shifts every downbeat after it. A single phase for the track scored 16/16
    before the shift and 0/16 after; the per-window Viterbi has to get both halves."""
    print("\nbar phase across a mid-track shift   (was: 50% overall, 0/16 after the shift)")
    path, truth, bpm = fixtures.phase_shift_track()
    d = analyze.build_director(path)
    got = d["events"]["downbeats"]
    tol = 0.5 * 60.0 / bpm                      # half a beat
    rec, prec = _score(truth, got, tol)
    split = truth[16]
    before = [t for t in truth if t < split]
    after = [t for t in truth if t >= split]
    hb = sum(1 for t in before if any(abs(g - t) <= tol for g in got))
    ha = sum(1 for t in after if any(abs(g - t) <= tol for g in got))
    check(rec >= 0.95, "recall %.0f%% of %d downbeats" % (rec * 100, len(truth)))
    check(prec >= 0.90, "precision %.0f%%" % (prec * 100))
    check(hb == len(before), "%d/%d before the shift" % (hb, len(before)))
    check(ha == len(after), "%d/%d after the shift" % (ha, len(after)))


def test_phase_does_not_flap():
    """The other side of the same knob. A phase free to change every window chases noise, and a
    bar grid that moves is worse than one steadily offset. Counted as downbeat gaps that are
    not 4 beats, on material with no shift in it at all."""
    print("\nbar phase stability on unshifted material   (was: 5 spurious changes at switch=0)")
    for ff in (True, False):
        path, _, bpm = fixtures.downbeat_track(0, four_to_the_floor=ff)
        d = analyze.build_director(path)
        db = d["events"]["downbeats"]
        gaps = np.diff(db) * d["tempo"]["bpm"] / 60.0 if len(db) > 2 else np.array([])
        odd = int(np.sum(np.abs(gaps - 4.0) > 0.6))
        check(odd == 0, "%s: %d phase changes in %d bars"
              % ("four-to-the-floor" if ff else "kick-only", odd, len(db)))


# ------------------------------------------------------------------------- onsets
def test_band_presence():
    """A band with nothing in it must say nothing. Measured before the gate: a band holding
    0.01% of the track's energy reported 55 onsets in 30 seconds, and the renderer routes
    visual events per band, so those fired from nothing."""
    print("\nper-band onsets, empty band   (was: 55 onsets from 0.01% of the energy)")
    path, truth = fixtures.band_isolated_track()
    ons = analyze.build_director(path)["events"]["onsets"]
    check(len(ons.get("high", [])) == 0,
          "high band (empty by construction) reports %d onsets" % len(ons.get("high", [])))
    for band in ("sub", "mid", "air"):
        got = [o[0] for o in ons.get(band, [])]
        want = truth[band]
        found = sum(1 for tr in want if any(abs(g - tr) <= 0.12 for g in got))
        check(found >= len(want) - 1,
              "%s band found %d of its %d events" % (band, found, len(want)))


def test_band_presence_does_not_silence_real_bands():
    """The other side of the gate. Every band of the shipped fixtures carries real content and
    must keep reporting after it."""
    print("\nper-band onsets, real material   (the gate must not silence these)")
    for name in ("arc120", "click120", "fast176"):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", name + ".wav")
        if not os.path.exists(path):
            print("  --   %s not present, skipped" % name)
            continue
        ons = analyze.build_director(path)["events"]["onsets"]
        empty = [b for b in ("sub", "low", "mid", "high", "air") if not ons.get(b)]
        check(not empty, "%s: every band reports onsets (silent: %s)" % (name, empty or "none"))


GROUPS = {
    "tempo": [test_tempo],
    "structure": [test_structure, test_energy_only_boundaries],
    "downbeat": [test_downbeat_phase, test_phase_does_not_flap],
    "phase": [test_phase_shift],
    "onsets": [test_band_presence, test_band_presence_does_not_silence_real_bands],
}

if __name__ == "__main__":
    want = sys.argv[1:] or list(GROUPS)
    unknown = [w for w in want if w not in GROUPS]
    if unknown:
        print("unknown group(s): %s\nknown: %s" % (", ".join(unknown), ", ".join(GROUPS)))
        sys.exit(2)
    for g in want:
        for fn in GROUPS[g]:
            fn()
    print()
    if FAILURES:
        print("%d FAILED" % len(FAILURES))
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("all passed")
