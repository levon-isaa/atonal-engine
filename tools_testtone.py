"""Synthesise test tracks with an EXACTLY known beat grid.

Sync cannot be argued, only measured, and measuring it needs material whose true tempo and
downbeat positions are known to the sample. Real music does not come with that. These two
fixtures do:

  click120.wav  120.000 BPM throughout, loud kick on every downbeat, offbeat hats, one bass
                note per bar. Every beat is at a multiple of 0.5s and every downbeat at a
                multiple of 2.0s, so any phase error in the render is directly readable.
  arc120.wav    the same grid, but four contrasting sections (sparse / build / dense drop /
                outro) so the emotional curves actually move. Its first bars are sparse enough
                that the beat tracker's grid starts late and its first beat is NOT a downbeat --
                which is the case that catches a client counting every fourth beat.

Both are .wav and .gitignore'd; run this to regenerate them into assets/ (served, so the viewer
can fetch them).  python tools_testtone.py
"""
import numpy as np, wave, struct, os

SR, BPM = 44100, 120.0
SPB = 60.0 / BPM


def _kick(amp, f0=110, f1=45, length=0.18, dec=0.045):
    n = int(length * SR)
    env = np.exp(-np.arange(n) / (dec * SR))
    f = np.linspace(f0, f1, n)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env * amp


def _hat(amp, seed, length=0.05, dec=0.008):
    n = int(length * SR)
    env = np.exp(-np.arange(n) / (dec * SR))
    return np.random.RandomState(seed).randn(n) * env * amp


def _pad(f, length, amp, bright):
    n = int(length * SR)
    a = np.arange(n) / SR
    w = np.zeros(n)
    for h in range(1, 7):
        w += np.sin(2 * np.pi * f * h * a) * (amp / h) * (bright ** (h - 1))
    return w * np.hanning(n)


def _write(path, x):
    x = np.clip(x / np.max(np.abs(x)) * 0.9, -1, 1)
    with wave.open(path, "w") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(SR)
        f.writeframes(struct.pack("<%dh" % len(x), *(np.int16(x * 32767))))
    print("wrote %s  %.1fs" % (path, len(x) / SR))


def click(dur=75.0):
    n = int(SR * dur); x = np.zeros(n)
    def add(sig, s):
        e = min(len(sig), n - s)
        if e > 0 and s >= 0: x[s:s + e] += sig[:e]
    for i, bt in enumerate(np.arange(0, dur, SPB)):
        add(_kick(0.95) if i % 4 == 0 else _kick(0.55, 180, 80), int(bt * SR))
    for k, ht in enumerate(np.arange(SPB / 2, dur, SPB)):
        add(_hat(0.16, k), int(ht * SR))
    for i, bt in enumerate(np.arange(0, dur, SPB * 4)):
        add(_pad([55, 55, 73.4, 61.7][i % 4], SPB * 4, 0.22, 0.4), int(bt * SR))
    return x


def arc(dur=80.0):
    n = int(SR * dur); x = np.zeros(n)
    def add(sig, s):
        e = min(len(sig), n - s)
        if e > 0 and s >= 0: x[s:s + e] += sig[:e]
    def sec_of(bt): return 0 if bt < 20 else 1 if bt < 40 else 2 if bt < 60 else 3
    for i, bt in enumerate(np.arange(0, dur, SPB)):
        s, sec = int(bt * SR), sec_of(bt)
        if sec == 0:
            if i % 4 == 0: add(_kick(0.45), s)
        elif sec == 1:
            if i % 2 == 0: add(_kick(0.70), s)
            add(_hat(0.10, i), int((bt + SPB / 2) * SR))
        elif sec == 2:
            add(_kick(1.00), s)
            for q in range(4): add(_hat(0.22, i * 4 + q), int((bt + q * SPB / 4) * SR))
            add(_kick(0.5, 240, 120, 0.10, 0.02), int((bt + SPB / 2) * SR))
        else:
            if i % 8 == 0: add(_kick(0.35), s)
    for i, bt in enumerate(np.arange(0, dur, SPB * 4)):
        sec = sec_of(bt)
        add(_pad([55, 55, 73.4, 61.7][i % 4], SPB * 4,
                 [0.16, 0.24, 0.34, 0.12][sec], [0.15, 0.35, 0.75, 0.20][sec]), int(bt * SR))
    return x


if __name__ == "__main__":
    os.makedirs("assets", exist_ok=True)
    _write("assets/click120.wav", click())
    _write("assets/arc120.wav", arc())
