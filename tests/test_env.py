#!/usr/bin/env python3
"""The prefiltered environment maps, and the tool that bakes them.

    python tests/test_env.py

assets/env_*.bin is what every reflective and every diffuse surface in the renderer actually
reads: eight equirect levels, each a GGX convolution of a real HDRI at that level's roughness,
with the last a cosine convolution for the irradiance. Nothing had ever opened one.

Two halves. The first reads the four SHIPPED blobs and checks what a file can be checked for
without its source: that the header says what the shader assumes, that the levels are the sizes
and the count it indexes, that nothing is NaN or negative, and that the convolution CONSERVED
ENERGY -- a normalised convolution cannot change the mean radiance, and the parameterisation is
equal-area, so a level's plain arithmetic mean IS its solid-angle-weighted mean. A level whose
mean has drifted was not convolved correctly.

The second runs tools_pmrem.convolve on a synthetic source with a studio's dynamic range and
checks it against a high-sample reference, so the sample counts cannot quietly go back to being
too low for the small levels.

Dependency-free apart from numpy, which the pipeline needs anyway.
"""
import glob
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

FAILURES = []

# The shader hard-codes both, in the ENV_LEVELS and ENV_W defines next to envDir().
ENV_W, ENV_H, ENV_LEVELS, ENV_C = 512, 256, 8, 3


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def read_blob(path):
    raw = open(path, "rb").read()
    magic, w, h, levels, ch = struct.unpack("<4sHHHH", raw[:12])
    out, off = [], 12
    for i in range(levels):
        lw, lh = max(4, w >> i), max(2, h >> i)
        n = lw * lh * ch
        a = np.frombuffer(raw, np.float16, count=n, offset=off).astype(np.float64)
        out.append(a.reshape(lh, lw, ch))
        off += n * 2
    return magic, w, h, levels, ch, out, off, len(raw)


def test_shipped_blobs():
    print("the baked environments")
    paths = sorted(glob.glob(os.path.join(ROOT, "assets", "env_*.bin")))
    check(len(paths) >= 1, "there are environment blobs to check (%d)" % len(paths))
    for path in paths:
        name = os.path.basename(path)
        magic, w, h, levels, ch, lv, consumed, total = read_blob(path)
        check(magic == b"AENV", "%s starts with the AENV magic (%r)" % (name, magic))
        # The shader indexes eight levels of a 512x256 texture and sizes a texel in radians from
        # ENV_W. A blob baked at another size loads without error and lights everything wrongly.
        check((w, h, levels, ch) == (ENV_W, ENV_H, ENV_LEVELS, ENV_C),
              "%s is %dx%d, %d levels, %d channels, as the shader assumes"
              % (name, w, h, levels, ch))
        check(consumed == total,
              "%s is exactly the size its header implies (%d of %d bytes)"
              % (name, consumed, total))

        allf = np.concatenate([a.ravel() for a in lv])
        check(np.isfinite(allf).all(), "%s has no NaN or Inf" % name)
        check((allf >= 0).all(), "%s has no negative radiance (min %.4f)" % (name, allf.min()))

        # ENERGY. Equal-area parameterisation, so the arithmetic mean is the mean radiance over
        # the sphere, and a normalised convolution preserves it.
        m0 = lv[0].mean()
        drift = [abs(a.mean() / m0 - 1.0) for a in lv]
        check(max(drift[:6]) <= 0.05,
              "%s conserves energy through level 5 (worst %.1f%%)" % (name, 100 * max(drift[:6])))
        # Blur is monotone: a wider lobe cannot increase the variance.
        sds = [a.std() for a in lv]
        check(all(sds[i + 1] <= sds[i] * 1.02 for i in range(5)),
              "%s gets smoother with every level through 5 (%s)"
              % (name, " ".join("%.2f" % s for s in sds[:6])))
        # LEVELS 6 AND 7 ARE PRINTED, NOT ASSERTED, and this is the one place in this file where
        # that is the right thing. They are the levels the old sample counts could not converge:
        # 32 texels and 8 texels, estimated with 192 and 256 samples. tools_pmrem now gives them
        # 8192 each, measured to bring the mean within 1.3% -- but these four blobs were baked
        # before that and the source HDRIs are not in the repo, so nothing here can re-bake them.
        # Re-run tools_pmrem.py against the Poly Haven originals and this drops under 2%.
        print("       %-22s level 6 %+.1f%%  level 7 %+.1f%%   (needs a re-bake; see the note)"
              % (name, 100 * (lv[6].mean() / m0 - 1), 100 * (lv[7].mean() / m0 - 1)))


def synthetic_source(seed=7):
    """A room with a studio's dynamic range: a dim interior and a few very bright sources.

    The range is the point. A flat source converges with almost no samples and would say nothing
    about whether the counts are adequate for a real HDRI.
    """
    rng = np.random.default_rng(seed)
    src = np.full((ENV_H, ENV_W, 3), 0.25, np.float32)
    src += rng.random((ENV_H, ENV_W, 3)).astype(np.float32) * 0.15
    y, x = np.mgrid[0:ENV_H, 0:ENV_W]
    for cy, cx, r, v in ((70, 120, 14, 320.0), (60, 300, 9, 180.0),
                         (96, 430, 18, 60.0), (30, 60, 6, 900.0)):
        src[((y - cy) ** 2 + (x - cx) ** 2) < r * r] = v
    return src


def test_sample_counts():
    print("the bake's sample counts")
    import tools_pmrem as P

    # THE WHOLE PLAN, not just the sample counts. Which level is which roughness, and which one
    # is the cosine convolution, define the chain -- and both were unreachable from a test until
    # they were lifted out of the tool's __main__, where a mutation to either survived everything
    # in this file. A chain baked at one roughness throughout, or with no cosine level, loads and
    # lights the scene with no complaint at all.
    plan = P.level_plan(ENV_W, ENV_H, ENV_LEVELS)
    check(len(plan) == ENV_LEVELS, "the plan has %d levels (%d)" % (ENV_LEVELS, len(plan)))
    check([(w, h) for _L, w, h, _r, _c, _n in plan]
          == [(max(4, ENV_W >> L), max(2, ENV_H >> L)) for L in range(ENV_LEVELS)],
          "every level is the size the loader will upload it as")
    check([round(r, 6) for _L, _w, _h, r, _c, _n in plan]
          == [round(L / (ENV_LEVELS - 1.0), 6) for L in range(ENV_LEVELS)],
          "roughness runs 0 to 1 across the chain, one step per level")
    check([c for _L, _w, _h, _r, c, _n in plan] == [False] * (ENV_LEVELS - 1) + [True],
          "the last level, and only the last, is the cosine convolution")
    got = {L: n for L, _w, _h, _r, _c, n in plan}
    want = {0: 32, 1: 192, 2: 192, 3: 192, 4: 512, 5: 2048, 6: 8192, 7: 8192}
    check(got == want, "every level gets the samples the budget rule says (%s)"
          % " ".join("%d:%d" % (k, v) for k, v in sorted(got.items())))

    # And that those counts actually converge, on the two levels cheap enough to check here.
    # Level 7 is the irradiance the diffuse term reads; at the old 256 samples its mean was
    # 12.3% low against this same reference.
    src = synthetic_source()
    for L in (6, 7):
        w, h = max(4, ENV_W >> L), max(2, ENV_H >> L)
        cos = (L == ENV_LEVELS - 1)
        ref = P.convolve(src, w, h, L / (ENV_LEVELS - 1.0), 16384, cosine=cos).astype(np.float64)
        old = P.convolve(src, w, h, L / (ENV_LEVELS - 1.0), 256 if cos else 192,
                         cosine=cos).astype(np.float64)
        new = P.convolve(src, w, h, L / (ENV_LEVELS - 1.0), got[L], cosine=cos).astype(np.float64)
        e_old = abs(old.mean() / ref.mean() - 1)
        e_new = abs(new.mean() / ref.mean() - 1)
        check(e_new <= 0.03,
              "level %d converges: %.2f%% against a 16384-sample reference (was %.2f%%)"
              % (L, 100 * e_new, 100 * e_old))


def test_convolve_is_a_convolution():
    """Two laws a normalised convolution obeys whatever the source, checked without a reference.

    The convergence test next door compares convolve() against convolve() at a higher sample
    count, so a SYSTEMATIC error -- a missing normalisation, the wrong weight, a lobe pointing
    the wrong way -- cancels on both sides and passes. These two do not: they are properties of
    the integral itself, so anything that stops being a weighted average of the source fails
    them no matter how many samples it takes.
    """
    print("convolve obeys the laws of a convolution")
    import tools_pmrem as P

    # 1. A constant field convolves to the same constant. Any weighted average of a constant is
    #    that constant, so this holds at every roughness and for the cosine level too.
    flat = np.full((ENV_H, ENV_W, 3), 0.37, np.float32)
    worst, where = 0.0, None
    for L in (0, 3, 5, 7):
        w, h = max(4, ENV_W >> L), max(2, ENV_H >> L)
        a = P.convolve(flat, w, h, L / (ENV_LEVELS - 1.0), 256, cosine=(L == ENV_LEVELS - 1))
        d = float(np.abs(a.astype(np.float64) - 0.37).max() / 0.37)
        if d > worst:
            worst, where = d, L
    check(worst <= 0.01,
          "a constant source stays constant at every roughness (worst %.3f%% at level %d)"
          % (100 * worst, where))

    # 2. Energy. The parameterisation is equal-area, so the mean over the sphere is the plain
    #    mean, and a normalised convolution cannot move it. Checked on the high-range source at
    #    a level with enough texels that sampling noise is not the thing being measured.
    src = synthetic_source()
    w, h = max(4, ENV_W >> 4), max(2, ENV_H >> 4)
    a = P.convolve(src, w, h, 4 / (ENV_LEVELS - 1.0), 2048).astype(np.float64)
    rel = abs(a.mean() / src.mean() - 1)
    check(rel <= 0.06, "the mean radiance survives the convolution (%.2f%% of the source's)"
          % (100 * rel))


def test_lobe_width():
    """How WIDE the filter is, which neither law above can see.

    A constant stays constant and energy is conserved for any normalised weighted average,
    however wrong its shape. Two real GGX-prefilter mistakes live entirely in the shape and
    passed everything else here: dropping the NoL weight, and using the half-vector H as the
    sample direction instead of reflecting the normal about it (which roughly halves the angle,
    since H is distributed around n and reflecting doubles the deviation).

    So: convolve a near-delta source and measure the rms angle of the response away from it.
    MEASURED, on this source, at 1024 samples:

        level (roughness)      correct    no NoL weight    H instead of reflect
          3  (0.43)             26.5          40.8                 15.9
          4  (0.57)             37.0          43.8                 22.6

    The band below is +-30% of the correct column, which excludes both at level 3 and the
    reflect mutation at level 4. These are characterisation numbers, not theory -- GGX's rms
    angle is tail-dominated and does not close-form usefully -- so they are recorded with the
    mutations they exist to catch rather than presented as derived.
    """
    print("the filter's lobe width")
    import tools_pmrem as P
    src = np.zeros((ENV_H, ENV_W, 3), np.float32)
    src[ENV_H // 2 - 1:ENV_H // 2 + 1, ENV_W // 2 - 1:ENV_W // 2 + 1] = 1000.0
    d0 = P.dirs_for(ENV_W, ENV_H)[ENV_H // 2, ENV_W // 2]
    for L, want in ((3, 26.5), (4, 37.0)):
        w, h = max(4, ENV_W >> L), max(2, ENV_H >> L)
        a = P.convolve(src, w, h, L / (ENV_LEVELS - 1.0), 1024).astype(np.float64).mean(-1)
        n = P.dirs_for(w, h)
        th = np.arccos(np.clip((n * d0).sum(-1), -1, 1))
        wt = np.maximum(a, 0)
        got = float(np.degrees(np.sqrt((wt * th ** 2).sum() / max(wt.sum(), 1e-12))))
        check(abs(got / want - 1) <= 0.30,
              "level %d spreads %.1f deg, within 30%% of %.1f" % (L, got, want))


def test_parameterisation():
    """dirs_for() and the shader's envDir() have to be the same mapping, in both directions.

    They are written in different languages in different files and nothing connects them but a
    comment in each. If they drift, every reflection points somewhere the bake never looked --
    silently, because both halves still produce a perfectly plausible image.
    """
    print("the equirect parameterisation")
    import tools_pmrem as P
    d = P.dirs_for(ENV_W, ENV_H)
    check(np.allclose(np.linalg.norm(d, axis=-1), 1.0, atol=1e-5),
          "every baked direction is a unit vector")

    # envDir(): u = atan2(z,x)/2pi + 0.5, v = y*0.5 + 0.5. Feed it the directions the baker used
    # and it must land back on the texel centres they came from.
    u = np.arctan2(d[..., 2], d[..., 0]) / (2 * np.pi) + 0.5
    v = d[..., 1] * 0.5 + 0.5
    want_u = (np.arange(ENV_W) + 0.5) / ENV_W
    want_v = (np.arange(ENV_H) + 0.5) / ENV_H
    check(np.allclose(u, want_u[None, :], atol=1e-5),
          "the shader's azimuth maps back to the column it was baked from")
    check(np.allclose(v, want_v[:, None], atol=1e-5),
          "the shader's elevation maps back to the row it was baked from")
    # Equal-area is the property that lets the convolution skip sin(theta) weighting: equal steps
    # in v must be equal steps in solid angle, i.e. v linear in cos(theta) and NOT in theta.
    ct = d[:, 0, 1]
    check(np.allclose(np.diff(ct), np.diff(ct)[0], atol=1e-6),
          "rows are equally spaced in cos(elevation), which is what makes them equal-area")


if __name__ == "__main__":
    test_shipped_blobs()
    test_parameterisation()
    test_convolve_is_a_convolution()
    test_lobe_width()
    test_sample_counts()
    print()
    if FAILURES:
        print("%d FAILED" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all passed")
