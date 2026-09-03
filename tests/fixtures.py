#!/usr/bin/env python3
"""Generated audio with KNOWN answers, for the accuracy tests next door.

Synthesised rather than committed, for the reason test_director.py gives: a generated track
has a tempo, a bar grid and a section list we know, so the assertions are about whether the
analysis is RIGHT. A committed real track could only ever assert that today's output matches
yesterday's, mistakes included.

Every fixture here was built to catch a specific defect that was measured and then fixed, and
each says which. They are cached under tests/_fixtures/ (gitignored) because generating them
is much cheaper than analysing them, but not free.
"""
import os
import numpy as np
import wave

SR = 22050
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures")


def _write(path, x):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    x = np.clip(x / (np.max(np.abs(x)) + 1e-9) * 0.85, -1.0, 1.0)
    w = wave.open(path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((x * 32767).astype("<i2").tobytes())
    w.close()
    return path


def _kick(x, at, amp=0.9):
    i = int(at * SR)
    e = np.exp(-np.arange(int(0.13 * SR)) / (0.030 * SR))
    n = min(len(e), len(x) - i)
    if n > 0:
        x[i:i + n] += amp * e[:n] * np.sin(2 * np.pi * 55 * np.arange(n) / SR)


def _crash(x, at, seed, amp=0.32):
    i = int(at * SR)
    e = np.exp(-np.arange(int(0.45 * SR)) / (0.12 * SR))
    n = min(len(e), len(x) - i)
    if n > 0:
        x[i:i + n] += amp * e[:n] * np.random.RandomState(seed).randn(n)


def _pad(x, t, t0, t1, chord, harmonics, level=1.0):
    m = (t >= t0) & (t < t1)
    if not m.any():
        return
    tt = t[m] - t0
    v = np.zeros_like(tt)
    for f0 in chord:
        for h in range(1, harmonics + 1):
            v += np.sin(2 * np.pi * f0 * h * tt) / (h * 1.6)
    x[m] += v / (len(chord) * 2.0) * level


CHORDS = [[220, 277, 330], [196, 247, 294], [247, 311, 370], [220, 262, 330],
          [175, 220, 262], [233, 294, 349], [208, 262, 311], [196, 233, 294]]


def tempo_track(bpm, seconds=32.0):
    """A plain click track. Catches the octave error: everything above ~175 BPM used to be
    tracked at half speed, so 180 came back as 90 and the GRID was halved with it."""
    path = os.path.join(CACHE, "tempo_%d.wav" % int(round(bpm)))
    if os.path.exists(path):
        return path, bpm
    t = np.arange(int(SR * seconds)) / SR
    x = np.zeros_like(t)
    beat = 60.0 / bpm
    for k in range(int(seconds / beat)):
        _kick(x, k * beat)
        if k % 2 == 1:
            i = int(k * beat * SR)
            e = np.exp(-np.arange(int(0.05 * SR)) / (0.008 * SR))
            n = min(len(e), len(x) - i)
            x[i:i + n] += 0.28 * e[:n] * np.random.RandomState(k).randn(n)
    for f0 in (220, 277, 330):
        x += 0.06 * np.sin(2 * np.pi * f0 * t)
    return _write(path, x), bpm


def structure_track():
    """Eight equal sections, bar-aligned, where chord AND timbre change at every boundary but
    the LEVEL changes at only two. A boundary the music makes and the loudness does not is what
    an RMS gradient cannot see, and structural boundaries used to lose every merge to it."""
    path = os.path.join(CACHE, "structure.wav")
    bpm, sec, nsec = 120.0, 16.0, 8
    truth = [i * sec for i in range(1, nsec)]
    if os.path.exists(path):
        return path, truth, bpm
    bright = [1, 4, 2, 6, 3, 8, 5, 7]
    loud = [1.0, 1.0, 1.0, 0.45, 0.45, 1.0, 1.0, 1.0]
    t = np.arange(int(SR * sec * nsec)) / SR
    x = np.zeros_like(t)
    for i in range(nsec):
        _pad(x, t, i * sec, (i + 1) * sec, CHORDS[i], bright[i], loud[i])
    for k in range(int(sec * nsec / (60.0 / bpm))):
        _kick(x, k * 60.0 / bpm, 0.7)
    return _write(path, x), truth, bpm


def energy_only_track():
    """Unequal sections at 140bpm, two of whose boundaries move ONLY the level -- same chord,
    same timbre. These are the boundaries the energy-novelty path exists for, and the guard
    against 'improving' structure detection by breaking it."""
    path = os.path.join(CACHE, "energy_only.wav")
    bpm = 140.0
    bar = 4 * 60.0 / bpm
    plan = [(12, 0, 3, 1.00), (8, 1, 6, 1.00), (8, 1, 6, 0.35), (12, 2, 2, 1.00),
            (8, 4, 7, 1.00), (8, 4, 7, 0.30), (12, 5, 4, 1.00)]
    truth, energy_only, acc = [], [], 0.0
    for idx, (bars, ci, hh, lv) in enumerate(plan):
        if idx:
            truth.append(round(acc, 3))
            p = plan[idx - 1]
            if p[1] == ci and p[2] == hh:
                energy_only.append(round(acc, 3))
        acc += bars * bar
    if os.path.exists(path):
        return path, truth, energy_only
    t = np.arange(int(SR * acc)) / SR
    x = np.zeros_like(t)
    a = 0.0
    for bars, ci, hh, lv in plan:
        _pad(x, t, a, a + bars * bar, CHORDS[ci], hh, lv)
        a += bars * bar
    for k in range(int(acc / (60.0 / bpm))):
        _kick(x, k * 60.0 / bpm, 0.7)
    return _write(path, x), truth, energy_only


def downbeat_track(offset_beats, four_to_the_floor=True, bars=32, bpm=120.0):
    """The bar phase. With four_to_the_floor the kick is on EVERY beat and the bar is marked
    only by a crash and a chord change -- both above the low band the phase used to be scored
    on, which made the choice a coin toss. With it False the kick is on beat one alone, which
    is the case the old low-band score was built for and must keep working."""
    kind = "ff" if four_to_the_floor else "kick"
    path = os.path.join(CACHE, "downbeat_%s_%d.wav" % (kind, offset_beats))
    beat = 60.0 / bpm
    truth = [round((offset_beats + 4 * b) * beat, 4) for b in range(bars)]
    if os.path.exists(path):
        return path, truth, bpm
    nb = bars * 4 + offset_beats
    t = np.arange(int(SR * nb * beat)) / SR
    x = np.zeros_like(t)
    for k in range(nb):
        isdb = ((k - offset_beats) % 4 == 0) and k >= offset_beats
        if four_to_the_floor or isdb:
            _kick(x, k * beat)
        if four_to_the_floor and isdb:
            _crash(x, k * beat, k)
    if four_to_the_floor:
        for b in range(bars):
            t0 = (offset_beats + 4 * b) * beat
            _pad(x, t, t0, t0 + 4 * beat, CHORDS[b % 4], 3)
    else:
        for f0 in (220, 277, 330):
            x += 0.05 * np.sin(2 * np.pi * f0 * t)
    return _write(path, x), truth, bpm


def phase_shift_track(shift_at_bar=16, bars=32, bpm=120.0):
    """A uniform beat grid whose BAR marker shifts by one beat halfway through -- what an odd
    bar looks like to a downbeat tracker. A single phase for the whole track scores 16/16
    before the shift and 0/16 after."""
    path = os.path.join(CACHE, "phase_shift.wav")
    beat = 60.0 / bpm
    nb = bars * 4 + 1
    truth, k, bar = [], 0, 0
    while k < nb:
        truth.append(round(k * beat, 4))
        k += 4 + (1 if bar == shift_at_bar - 1 else 0)
        bar += 1
    if os.path.exists(path):
        return path, truth, bpm
    t = np.arange(int(SR * nb * beat)) / SR
    x = np.zeros_like(t)
    for i in range(nb):
        _kick(x, i * beat)
    for bi, d in enumerate(truth):
        _crash(x, d, bi, 0.34)
        _pad(x, t, d, d + 4 * beat, CHORDS[bi % 4], 3)
    return _write(path, x), truth, bpm
