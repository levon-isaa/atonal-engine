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

if __name__ == '__main__':
    SRC = '/private/tmp/claude-501/-Users-levonkostandyan-Desktop-Atonal/5dacd0d3-2aec-4f18-82c4-9bf98ac03536/scratchpad/studio.hdr'
    img = read_hdr(SRC)
    print('source', img.shape, 'lum mean %.3f max %.1f' % (img.mean(), img.max()))
    # Work from a half-size copy: the 2K source is far more detail than any level needs, and
    # sampling it 128 times per output texel is the whole cost.
    src = img[::2, ::2]
    W0, H0, LEVELS = 512, 256, 8
    blobs, meta = [], []
    for L in range(LEVELS):
        w, h = max(4, W0 >> L), max(2, H0 >> L)
        rough = L / (LEVELS - 1)
        cos = (L == LEVELS - 1)
        ns = 32 if L == 0 else (192 if not cos else 256)
        lv = convolve(src, w, h, rough, ns, cosine=cos)
        blobs.append(lv.astype(np.float16))
        meta.append((w, h, rough, float(lv.mean())))
        print('  level %d  %4dx%-4d rough %.2f  %-6s mean %.3f' %
              (L, w, h, rough, 'cosine' if cos else 'ggx', lv.mean()))
    out = b''.join(b.tobytes() for b in blobs)
    hdr = struct.pack('<4sHHHH', b'AENV', W0, H0, LEVELS, 3)
    open('/Users/levonkostandyan/Desktop/Atonal/assets/env_studio.bin', 'wb').write(hdr + out)
    print('wrote assets/env_studio.bin  %.2f MB' % ((len(hdr)+len(out))/1e6))
