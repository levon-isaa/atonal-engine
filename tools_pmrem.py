"""Radiance .hdr -> a prefiltered equirect mip chain, as one float16 blob.

This is a PMREM done offline. Doing it at load time in the browser was the alternative and it
is strictly worse: the convolution is the expensive part, it never changes, and paying for it on
every page load buys nothing. Shipping the finished chain means the runtime cost of image-based
lighting is one textureLod.

WHY NOT HARDWARE MIPS. Tried first, measured, rejected: box-filtering an equirect map is far too
aggressive a convolution -- by the level a satin surface selects, the room is a smear, and the
render came out FLATTER than the analytic room it replaced (91 levels of range down to 26).
A mip level here is a GGX lobe of a specific roughness, importance-sampled, which is a different
filter entirely and the one the BRDF actually asks for.

The last level is a COSINE convolution, not GGX: that level is what the diffuse term reads, and
what a diffuse surface integrates is the cosine-weighted hemisphere, which is band-limited to
about two SH bands. GGX at roughness 1 is close but it is not the same integral.
"""
import numpy as np, struct, sys

# ---------- Radiance RGBE ----------------------------------------------------------------
def read_hdr(path):
    with open(path, 'rb') as fh:
        raw = fh.read()
    # header: text lines until a blank one, then the resolution line
    i = 0
    while True:
        j = raw.index(b'\n', i)
        line = raw[i:j]
        i = j + 1
        if line.strip() == b'':
            break
    j = raw.index(b'\n', i)
    res = raw[i:j].split()
    i = j + 1
    assert res[0] == b'-Y' and res[2] == b'+X', res
    H, W = int(res[1]), int(res[3])

    out = np.zeros((H, W, 4), np.uint8)
    p = i
    for y in range(H):
        if raw[p] == 2 and raw[p+1] == 2 and ((raw[p+2] << 8) | raw[p+3]) == W:
            p += 4                                   # adaptive RLE, one pass per component
            for c in range(4):
                x = 0
                while x < W:
                    n = raw[p]; p += 1
                    if n > 128:                      # run of a single value
                        out[y, x:x+n-128, c] = raw[p]; p += 1; x += n - 128
                    else:                            # literal bytes
                        out[y, x:x+n, c] = np.frombuffer(raw[p:p+n], np.uint8); p += n; x += n
        else:                                        # flat (non-RLE) scanline
            out[y] = np.frombuffer(raw[p:p+W*4], np.uint8).reshape(W, 4); p += W * 4

    e = out[..., 3].astype(np.int32)
    scale = np.where(e == 0, 0.0, np.ldexp(1.0, e - 136)).astype(np.float32)   # 128 + 8
    return out[..., :3].astype(np.float32) * scale[..., None]

# ---------- equirect helpers -------------------------------------------------------------
def dirs_for(W, H):
    """Direction per texel. Must match envDir() in the shader: x = azimuth over [-pi,pi],
    y = sin(elevation) over [-1,1] -- an EQUAL-AREA parameterisation in y, which is why no
    sin(theta) weighting appears anywhere below: every texel already covers the same solid
    angle. Getting this wrong biases the whole convolution toward the poles."""
    u = (np.arange(W) + 0.5) / W
    v = (np.arange(H) + 0.5) / H
    phi = (u * 2 - 1) * np.pi
    ct = v * 2 - 1
    st = np.sqrt(np.maximum(0.0, 1 - ct**2))
    d = np.empty((H, W, 3), np.float32)
    d[..., 0] = st[:, None] * np.cos(phi)[None, :]
    d[..., 1] = ct[:, None]
    d[..., 2] = st[:, None] * np.sin(phi)[None, :]
    return d

def sample(img, d):
    """Bilinear lookup of the equirect source along directions d (..,3)."""
    H, W = img.shape[:2]
    u = (np.arctan2(d[..., 2], d[..., 0]) / (2*np.pi) + 0.5) * W - 0.5
    v = (d[..., 1] * 0.5 + 0.5) * H - 0.5
    u0 = np.floor(u); v0 = np.floor(v)
    fu = (u - u0)[..., None]; fv = (v - v0)[..., None]
    u0 = u0.astype(np.int64); v0 = v0.astype(np.int64)
    x0 = u0 % W; x1 = (u0 + 1) % W                       # wrap in azimuth
    y0 = np.clip(v0, 0, H-1); y1 = np.clip(v0 + 1, 0, H-1)   # clamp at the poles
    a = img[y0, x0]*(1-fu) + img[y0, x1]*fu
    b = img[y1, x0]*(1-fu) + img[y1, x1]*fu
    return a*(1-fv) + b*fv

def basis(n):
    up = np.where(np.abs(n[..., 1:2]) < 0.99, np.array([0,1,0], np.float32), np.array([1,0,0], np.float32))
    t = np.cross(up, n); t /= np.maximum(np.linalg.norm(t, axis=-1, keepdims=True), 1e-9)
    b = np.cross(n, t)
    return t, b

def hammersley(N):
    i = np.arange(N, dtype=np.uint32)
    bits = i.copy()
    bits = ((bits << np.uint32(16)) | (bits >> np.uint32(16)))
    bits = ((bits & np.uint32(0x55555555)) << np.uint32(1)) | ((bits & np.uint32(0xAAAAAAAA)) >> np.uint32(1))
    bits = ((bits & np.uint32(0x33333333)) << np.uint32(2)) | ((bits & np.uint32(0xCCCCCCCC)) >> np.uint32(2))
    bits = ((bits & np.uint32(0x0F0F0F0F)) << np.uint32(4)) | ((bits & np.uint32(0xF0F0F0F0)) >> np.uint32(4))
    bits = ((bits & np.uint32(0x00FF00FF)) << np.uint32(8)) | ((bits & np.uint32(0xFF00FF00)) >> np.uint32(8))
    return (i + 0.5) / N, bits.astype(np.float64) * 2.3283064365386963e-10

def convolve(src, W, H, rough, nsamp, cosine=False):
    """GGX (or cosine) convolution of src into a W x H equirect level."""
    n = dirs_for(W, H)
    t, b = basis(n)
    u1, u2 = hammersley(nsamp)
    acc = np.zeros((H, W, 3), np.float64)
    wsum = np.zeros((H, W, 1), np.float64)
    a = max(rough, 1e-3) ** 2
    for k in range(nsamp):
        if cosine:
            r = np.sqrt(u1[k]); phi = 2*np.pi*u2[k]
            lx, ly, lz = r*np.cos(phi), r*np.sin(phi), np.sqrt(max(0.0, 1-u1[k]))
            L = t*lx + b*ly + n*lz
            w = 1.0                                   # pdf cancels the cosine exactly
        else:
            ct = np.sqrt((1 - u1[k]) / (1 + (a*a - 1)*u1[k]))
            st = np.sqrt(max(0.0, 1 - ct*ct)); phi = 2*np.pi*u2[k]
            hx, hy, hz = st*np.cos(phi), st*np.sin(phi), ct
            Hv = t*hx + b*hy + n*hz
            L = 2*np.sum(n*Hv, -1, keepdims=True)*Hv - n     # reflect n about H
            w = np.maximum(np.sum(n*L, -1, keepdims=True), 0.0)   # NoL weighting
        acc += sample(src, L) * w
        wsum += w if not cosine else 1.0
    return (acc / np.maximum(wsum, 1e-9)).astype(np.float32)

SAMPLE_BUDGET = 262144      # sample-lookups per level, for every level small enough to afford it


def samples_for(level, texels, cosine):
    """How many importance samples a level gets.

    IT WAS A CONSTANT, 192 (256 for the cosine level), AND THE SMALL LEVELS NEED FAR MORE. A
    Monte Carlo estimate converges with the sample count and nothing else, and the error that
    survives into the finished map is the error left in the MEAN of each level -- but a level's
    mean is itself an average over its texels, so the big levels hide their noise by having tens
    of thousands of texels to average over and the small ones have nowhere to hide it. Level 7 is
    eight texels.

    MEASURED against a 16384-sample reference, on a synthetic source with a studio's dynamic
    range (background 0.25, sources up to 900):

        level  texels   192/256 samples        with the rule below
          4       512   mean  -1.43%            512   mean +1.26%
          5       128   mean  +3.46%           2048   mean +0.25%
          6        32   mean  -6.33%           8192   mean +0.05%
          7         8   mean -12.32%           8192   mean -1.32%

    Level 7 is the cosine convolution -- the irradiance every diffuse surface reads -- so that
    12% was a systematic error in the ambient level of every material in the renderer.

    The budget is per LEVEL rather than per texel, which is what makes this nearly free: the
    levels that need more samples are exactly the ones with almost no texels to spend them on.
    Whole-bake cost is 12.58M sample-lookups against 13.30M, or +6%.

    Levels 1-3 keep 192 and are NOT fixed by this. Their means are already within 1% for the same
    reason -- thousands of texels -- but individual texels are still noisy (worst-texel error
    1060%, 673% and 339% in the same measurement) because a narrow GGX lobe either catches a
    bright source or misses it. Bringing those to the same standard costs 8500% of level 0 and is
    not affordable here; the honest fix is a firefly clamp on the source, which changes what the
    room looks like and is not a change to make without the original HDRIs to check it against.
    """
    if level == 0:
        return 32               # roughness 0: the lobe is a delta, every sample lands together
    lo = 256 if cosine else 192
    return int(min(8192, max(lo, SAMPLE_BUDGET // max(1, texels))))


def level_plan(W0=512, H0=256, levels=8):
    """The chain to bake: one (level, w, h, roughness, cosine, samples) per level.

    Lifted out of __main__ so it can be tested. It was four lines inside the loop, and four lines
    nothing could reach without running the tool against a real HDRI -- which meant the two
    decisions that define the whole chain (that level L is roughness L/(levels-1), and that the
    LAST level is a cosine convolution rather than GGX) were the only part of this file a test
    could not see. Both are silent if wrong: a chain baked entirely at one roughness, or with no
    cosine level at all, loads and lights the scene without complaint.

    The sizes floor at 4x2 rather than running to 1x1: the shader declares exactly `levels` mip
    levels with texStorage2D, and an equirect narrower than 4 texels has no azimuth left to
    interpolate across.
    """
    out = []
    for L in range(levels):
        w, h = max(4, W0 >> L), max(2, H0 >> L)
        rough = L / (levels - 1)
        cos = (L == levels - 1)
        out.append((L, w, h, rough, cos, samples_for(L, w * h, cos)))
    return out


if __name__ == '__main__':
    # Both paths come from the command line. They were hardcoded when this was committed —
    # an absolute path into a temp directory on one machine, which meant the tool in the repo
    # could not be run by anyone including its author after that directory was cleaned up.
    #     python tools_pmrem.py studio.hdr assets/env_studio.bin
    if len(sys.argv) != 3:
        raise SystemExit('usage: python tools_pmrem.py <source.hdr> <dest.bin>')
    SRC, DST = sys.argv[1], sys.argv[2]
    img = read_hdr(SRC)
    print('source', img.shape, 'lum mean %.3f max %.1f' % (img.mean(), img.max()))
    # Work from a half-size copy: the 2K source is far more detail than any level needs, and
    # sampling it 128 times per output texel is the whole cost.
    src = img[::2, ::2]
    W0, H0, LEVELS = 512, 256, 8
    blobs, meta = [], []
    for L, w, h, rough, cos, ns in level_plan(W0, H0, LEVELS):
        lv = convolve(src, w, h, rough, ns, cosine=cos)
        blobs.append(lv.astype(np.float16))
        meta.append((w, h, rough, float(lv.mean())))
        print('  level %d  %4dx%-4d rough %.2f  %-6s mean %.3f' %
              (L, w, h, rough, 'cosine' if cos else 'ggx', lv.mean()))
    out = b''.join(b.tobytes() for b in blobs)
    hdr = struct.pack('<4sHHHH', b'AENV', W0, H0, LEVELS, 3)
    open(DST, 'wb').write(hdr + out)
    print('wrote %s  %.2f MB' % (DST, (len(hdr)+len(out))/1e6))
