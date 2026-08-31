# ATONAL — AI Music Understanding Engine (backend)

Analyzes a track and emits a **Director State** (`director.json`) — the single contract the
renderer consumes. The renderer never sees FFT; it reads the Director's decisions.

## Layers
1. **Signal** (librosa): RMS, centroid, flatness, rolloff, bandwidth, ZCR, spectral flux,
   contrast, 20 MFCCs, chroma-CQT, HPSS harmonic/percussive, tempo+beats, tempo stability,
   crest factor, dynamic range, stereo width / phase correlation.
1b. **Per-band sounds**: the spectrum is split into `sub / low / mid / high / air`. Each band
   gets a sustained level curve *and* a discrete onset list (the actual hits), so the kick,
   the snare and the hats are separate events instead of one lumped "energy" number. This is
   what lets motion land on individual sounds.
2. **Structure**: beat-synced chroma+MFCC agglomerative segmentation + energy-novelty
   boundaries → labelled sections (intro/build/drop/groove/breakdown/outro).
3. **Emotion**: continuous curves (energy, valence, arousal, tension, darkness, warmth,
   danceability, epicness…) at 30 Hz.
4. **Genre / tagging** (ML): PANNs Cnn14 (AudioSet 527 tags) → genre + mood tags.
   Falls back to a heuristic if the checkpoint is absent (`tagger.available()`).
5. **Semantic**: rule-based section intent ("accumulate tension", "darkening"). LLM-ready.
6. **Director**: each section carries camera / composition / mood / palette / motion / fog /
   bloom / grain.
10. **Prediction**: upcoming events (e.g. drop) with lead time, for visual anticipation.

## Run it on any computer (macOS / Linux / Windows)
**Prerequisites:** Python 3.9+ and ~2 GB free disk (deps + a 300 MB model). Internet for first-time setup.

macOS / Linux:
```bash
cd atonal-engine
./setup.sh      # one time: venv + deps + downloads the model (~300MB)
./run.sh        # then open http://127.0.0.1:8770/
```
Windows:
```bat
cd atonal-engine
setup.bat
run.bat
```
Then open **http://127.0.0.1:8770/**, drop in a track, and it analyses + directs the visuals.

Batch (no server):
```bash
source .venv/bin/activate
python analyze.py "track.mp3" -o out/director.json
```

The server (`server.py`) serves the viewer at `/`, analyses at `POST /analyze`, and reports
`GET /health`. Pass `?job=<id>` on the POST and poll `GET /progress?job=<id>` for
`{stage, p, next, eta}` — the viewer's progress bar is driven by this.

**Progress weights are measured, not guessed.** Timed end to end on a 5.5-minute track:
HPSS is 52% of the whole run and PANNs another 26%, with everything else under 8% each. A bar
built on one tick per layer would spend nearly all its time on two ticks and look frozen. Both of
those stages are opaque library calls that cannot report from inside themselves, so the server
sends the fraction each stage *starts* at, the fraction it *ends* at, and how long it expects to
take, and the client eases between the two. The expected total is extrapolated from the work
already done rather than a hard-coded rate, so it calibrates to the machine it runs on.

**Results are cached on the audio's SHA-256**, not its filename — the same track renamed is the
same analysis, a different track under a reused name is not. Analysis is deterministic, so a
repeat load has no reason to pay for it again: measured 5.4s cold against 1.0s served from
`out/cache/`. A hit returns without taking the analysis semaphore, since it does no CPU work and
queueing it behind a running analysis would stall it for nothing. Entries are written atomically
and treated as best-effort throughout — a corrupt one recomputes rather than failing the request.

**Tempo is corrected, not taken as given.** `beat_track` runs with a 128bpm prior, is
cross-checked against the median inter-beat interval of the grid it actually returned, and is
folded into 70..160 by octaves. Without the prior it takes whichever autocorrelation peak is
tallest, and on material with strong offbeat content that is regularly a *3:2* relative of the
true tempo rather than the usual octave — measured 140 → 92.3 and 126 → 83.4 before, 143.6 and
129.2 after. The renderer locks its spin to this number, so a metrical error shows up directly as
the form turning at the wrong speed.

The client keeps one poll in flight at a time and clamps the displayed value monotonically:
measured round trips reached 473ms against a 300ms interval, so requests overlapped, responses
landed out of order, and the bar jumped backwards 27 times in a single analysis before that.
The PANNs model auto-downloads to `~/panns_data/` on first run.

## Contract: director.json (schema `atonal.director/1`)
```
meta{duration,director_fps,analysis_sr,hop,analysis_version}
tempo{bpm,stability,crest_db,dynamic_range_db,stereo_width}
tonality{key,mode,confidence,strength}
instruments{drums,synth,piano,guitar,strings,…}          # 0..1, PANNs; {} on the fallback
vocals{presence,is_vocal,types{singing,choir,rapping,speech,whistle}}
genre{primary,confidence,secondary,method,top_tags[],moods{}}
sections[]{t0,t1,label,energy,tension,valence,release,dynamics,atmosphere,density,
           camera,composition,mood,palette,motion,fog,bloom,grain,intent}
events{beats[], onsets{sub|low|mid|high|air: [[t,strength],…]}, predictions[]{t,type,lead}}
curves{t[], energy[],brightness[],arousal[],valence[],tension[],darkness[],
       warmth[],danceability[],epicness[],flux[],percussive[],harmonic[],density[],
       release[],dynamics[],atmosphere[],
       sub[],low[],mid[],high[],air[]}                                              # 30 Hz
```

### Tonality, release, dynamics, atmosphere, density

**Key and mode come free.** chroma-CQT was already the second most expensive feature after HPSS
and was used only to give the segmenter a stable similarity space — the key was sitting in it
unread. Krumhansl-Schmuckler profiles, correlated against all 24 rotations. Validated against
synthesised triads whose key is known: C major, A minor, F major and D minor all detected
correctly at confidence 0.85–1.0. `confidence` is the **margin over the best competing key**, not
the raw correlation — every key correlates decently with a chromatic average, so the raw number
is high, flat and uninformative. `strength` answers a different question, whether the track is
tonal at all: a drum loop has a near-flat chroma that still produces a winning key, just a
meaningless one, so the renderer can ignore the key when there is not really one.

**Release is not the inverse of tension.** Low tension is a calm passage; release is the moment
tension is actively being let go, which is where the visual payoff belongs. It is the rectified
negative slope of tension — positive only while tension falls, zero while it is merely low.

**Dynamics is local, not the whole-track scalar.** `dynamic_range_db` already reports one number
per track; this curve is where the track is *being* dynamic against where it is squashed flat.

**Atmosphere** is sustained harmonic wash against transient attack — high on pads, tails and
reverb, low on hits. It now drives `fog` alongside darkness, because fog on darkness alone gave a
bright airy pad passage no haze and a dark percussive one plenty.

**Density was not a measurement.** It shipped as `density=danceability` — a distinct name in the
contract for a straight alias of a curve already in it. It is now onsets per second across all
five bands over a 2s window. The two are different questions: a half-time breakdown over a dense
hat pattern is high density and low danceability.

**Instrumentation and voice cost nothing.** The Cnn14 forward pass already scores all 527 AudioSet
classes and the pipeline kept two of them; the instrument and voice classes were computed and
discarded on every run. Matched by keyword against the checkpoint's own label list rather than by
exact string, because the AudioSet names carry commas and parentheticals ("Violin, fiddle",
"Keyboard (musical)") and a dict keyed on exact spelling fails by returning an empty section
rather than an error. Speech is scored but excluded from `is_vocal`: AudioSet fires it on an MC
over an instrumental, and that is not a lead vocal. On the synthesised test track — kick, hats,
sub and a pad — it reports `drums 0.68, synth 0.39` and `is_vocal false`.

**`meta.analysis_version` exists so the cache cannot go stale.** Entries are keyed on the audio's
SHA-256, which answers "same bytes?" and not "same analysis?", so without a version a track
analysed before a pipeline change returns the old director for ever — silently, and shaped
exactly like a renderer bug, with contract fields simply absent on some tracks and not others.
The server rejects any entry whose version does not match and recomputes.

### How the new signals drive the visuals

Every mapping below was ablated on a frozen peak-energy frame (playback position pinned via
`startedAt`, clock frozen, noise floor 0) by forcing the curve to 0 and to 1 and diffing the
frame. The percentage is pixels moved by more than 8 levels.

| signal | drives | measured 0 → 1 |
|---|---|---|
| dynamics | the **key:fill ratio** — a dynamic passage gets less fill and is modelled harder; a squashed one gets more and flattens | **9.64%** |
| tonality | hue of the Auto colourway, ordered round the circle of fifths | **67.3%** at full gate, **7.24%** for this track's actual A minor |
| atmosphere | gates `fog` (and the volumetric shafts, currently disabled on every tier) | **4.73%** |
| release | bloom lifts and contrast eases as tension is let go | **2.36%** |
| vocals | rim light strength — a voice is the thing sitting in front | **2.04%** |
| density | surface tooth and grain | **0.2% zoomed, 0% at rest** — see below |

Three of these needed a second attempt, and the reasons are worth keeping.

**Tonality first measured 0.00%.** The rotation was applied in `bgPair()`, which only feeds the
duotone inks — and the default `Studio` look sets `ink` to 0, so it was multiplied away. The Auto
background is built *inside* the shader from `u_mood`. Rotating the mood at that single source
reaches the background, the form's tint and the inks together and keeps them agreeing.

**Dynamics drives the lighting, not the grade.** Pushing contrast crushes the whole frame
together; changing the key:fill ratio changes how the *object* is lit, which is what a lighting
cameraman does with a bounce card. It is the strongest of the six for that reason.

**Density is the weakest and stays that way.** It moves the surface tooth and the grain, and both
are near-invisible at the default framing — the detail map is mip-filtered down so far that
toggling Detail off entirely is only 1.26%, so a fraction of that is nothing. Widening it and
adding grain lifted the mean from 0.04 to 0.11 but still crosses 8 levels on no pixel at rest,
and 0.2% zoomed in. It is honestly wired and honestly faint; making it louder would mean giving
it a lever it has no business holding.

**Release is on release, not on low tension.** A calm passage should look calm, not blown — the
bloom lift belongs to the moment of letting go, not to the quiet that follows.

### How each band drives the visuals
| band | onset lands as | level sustains as |
|---|---|---|
| sub / low | scale punch + camera dolly-in, background flash | overall swell |
| mid | shockwave ring through the surface, spin nudge | ink turbulence |
| high | rim flare, spin nudge, surface chatter | surface detail frequency |
| air | iridescent shimmer, bloom lift | sheen |

Transients are decayed then attack-smoothed per band (highs snap, lows hang) so the motion
is articulate without jittering. Surface displacement divides amplitude back down by
frequency — `freq * amp` must stay bounded or the field stops being a valid SDF and the
raymarch creases.

## Viewer controls (top right)
- **Shape** — `Auto · Director` lets the Director pick, or lock one of Shell / Ovoid / Cross /
  Lattice / Column / Disc, plus `Reference mesh` (a BVH over a 4,608-triangle mesh packed into
  textures, `assets/mesh_*.f32`) and `Custom SVG…`.
- **Material** — Chrome, Gold, Plastic, Matte, Pearl, Glass, Clay, Frosted, Holographic.
  Gold carries its own **`f0Tint`** (1.00, 0.77, 0.34), read off the preset rather than the eased
  value: metal reflectance is a discrete identity, and easing it would cross-fade a switch to
  Chrome through white on the way. Gold is not chrome with a yellow light on it.
  Glass **marches its own
  interior**: the field is negative inside the form, so `-mapD` carries the refracted ray to where
  it actually leaves. That gives thickness, which drives Beer-Lambert absorption so thin edges stay
  clear and the body deepens in colour, and a second refraction at the exit surface, where the
  dispersion is applied. Refracting once at the front surface treats the form as an infinitely thin
  shell — it bends the view but has no inside, which is why it read as a soap film.
  Clay has **no surface at all**: `tamp:0` early-outs the texture normal and `trough:0` early-outs
  the roughness variation *and* zeroes the albedo grain, which is scaled by it. Clay is fully matte: `spec:0` gates
  the direct lobe as well as the environment reflection, and it carries its own lower albedo,
  because the shared near-white base blew out across every lit face.


- **Surface detail is gone**, and with it the `Detail` selector and the `Surface` toggle. They
  were one decision wearing two hats: Surface off zeroed `u_txAmp` and `u_txRough`, the amplitudes
  the map was multiplied by, so `Smooth` already made `Detail` inert while still baking and
  sampling it. The look decided it — a triplanar tooth map on the large flat faces of an extruded
  profile reads as orange peel and breaks the specular into mush where the reference holds one
  clean sweep along the bevel. The shading normal is now the geometric normal.
  Removed with it: the GPU bake (4096² RGBA16F, 171 MB resident and ~104 ms at load), the PNG
  fallbacks, the analytic ray differentials that picked its mip level, `fbmT`'s five procedural
  octaves, 12 uniforms and 7 diagnostic modes. The measurement notes below are kept as a record
  of what was learned, not as a description of what runs.

- **Finish** — gloss/iridescence multiplier layered over the material preset.
- **Wear** — edge abrasion, and the opposite of the cavity mask: the cavity mask darkens
  recesses (dirt collects), wear roughens and thins the coating on the **high points** (they rub
  against everything else). A surface wants both. It costs nothing to compute — the edge signal
  is the rate the shading normal turns across a pixel, which the specular-AA term already derives
  and then discards. Being screen-space, a fillet reads as an edge at any zoom. It sits beside the
  material rather than inside it, because wear is a property of the object's history, not of what
  it is made of: chrome and plastic can both be worn.
- **Lighting** — four prefiltered HDRI environments (Studio · daylight, Studio · small, Dawn,
  City; CC0, Poly Haven) plus `Analytic room`, which is the built-in fallback exposed as a choice.
  Each `.bin` is an 8-level equirect chain, one `textureLod` at runtime. See *Image-based
  lighting* below.

Detail used to be **procedural** (`fbmT`), and the notes from that period are kept because the
measurements still hold for anyone reaching for noise here. Detail is amplitude x frequency, and
the lever was the per-material base frequency `tscale`, not octave count: the fbm ran at gain
0.42, so octaves 6 and 7 would together carry about 4% of the amplitude for 40% more noise
evaluations. Both "more octaves" and procedural mipmapping (fading octaves by `fwidth` footprint)
were implemented, measured and reverted — the mipmapping moved pixel-scale energy inside the
silhouette by 1.5-3.1% across four materials, one of them negative, which is noise. It had
nothing to filter because the octaves fine enough to alias were the ones already carrying almost
no amplitude. A sampled map sidesteps that ceiling entirely: its detail is band-limited by the
image and filtered by the hardware.

### Surface detail: why it was baked, and why it is gone

*Kept as a record of what was measured. The detail map itself was removed — see the Surface
note under the controls above.*

The 512² 8-bit PNGs that preceded this were wrong in three separate ways, each measured on a
locked frame with the form filling 1474 canvas px.

**Resolution.** The detail uv spans 2.21 tiles across the form, so one tile covers 667 px — a
512-texel map is being *magnified* 1.30×. Past 1:1 the mip chain has nothing to do and the
hardware is simply stretching texels. At 2048² the same tile sits at 3.07× *minification*, which
is the regime mips and anisotropic filtering exist for.

**Precision.** A normal in 8 bits steps by 2/255, i.e. 0.45° of tilt per code — about a tenth of
the specular lobe width at `rough` 0.35, so it lands as terracing in the highlight rather than as
noise beneath it. RGBA16F has no such floor.

**Content.** Measured on the shipped PNGs: `machined` was 2.27:1 anisotropic with 6.8° mean tilt
(19.3° at p99); `micro` was 13.9° mean and **31.3° at p99**, which is gravel, not microstructure.
Their alpha (the roughness modulation) was mean 0.49 ± 0.28, and `texRough()` maps it through
`0.45 + 1.5v` — so both maps biased roughness up 19% *and* strobed it ±42%. The bake centres
alpha on 0.367, the value that makes that mapping return the material's own roughness unchanged,
and swings it a third as far. Rendered high-frequency energy over the form fell from 2.01
(`machined`) and 3.59 (`micro`) to 0.84 and 0.87, against a 0.77 floor measured with detail off
entirely.

Two things the bake had to get right that are easy to miss:

- **The hash.** `fract(sin(dot(i,k)))` projects the 2D lattice onto one axis before hashing, so
  cells on a line of constant `dot(i,k)` come out correlated — at these periods (9–112 cells)
  that bakes as long axis-aligned ridges. Replaced with a bit-mixing integer hash.
- **Measuring anisotropy at all.** A per-channel `nx`/`ny` variance ratio reported 1.04 for a map
  that was visibly streaked, because variance sees stretching but not *alignment*. An orientation
  histogram of the tilt direction over the whole map is the measure that works; it reported
  1.53:1 with peaks at exactly 0° and 90°, the lattice showing through. A per-octave tileable
  domain warp brings that to 1.44:1. `machined` sits at 3.58:1 by design — it is the brushed
  option — which is why the isotropic map is now the default: any directional map reads as
  streaking on the large flat faces of an extruded profile.

The bake self-calibrates rather than hard-coding a gain: it runs once at unit gradient scale,
reads the result back, recovers the raw gradient from the encoded normal, and solves for the
scale that hits the preset's requested mean tilt. Hard-coding would drift silently the moment the
octave mix changed, which is how the PNGs ended up at 14° without anyone choosing 14°. The
solved scale moves with the resolution — 0.0078 at 2048, 0.0101 at 8192 for the same 4° target —
which is the calibration doing exactly the job it exists for.

**The bake is now 8192², and the honest account of what that buys is: nothing at the default
framing, and a real gain when you push in.** At the framing everything above was measured on, one
detail tile covers 667 px and the map is already minified 3.07× — the mip chain discards the extra
levels, and measured high-frequency energy over the form is 1.671 at 8192 against 1.672 at 2048,
i.e. identical. Pushed in to the zoom clamp (`userZoom` 0.45) the same measurement is **2.398 at
8192 against 2.277 at 2048, +5.2%**, with an A/A repeat of 0.13% — so the gain is well clear of
noise. That is the regime the resolution is for: 4× the linear detail keeps the tile above 1:1
until the form fills roughly four times the frame width it does at rest.

**Runtime cost is nil; the cost is memory and bake time.** Interleaved A/B/A at the close framing:
20.1 fps at 2048, 20.1 at 8192, 20.1 back at 2048 — mip selection means the shader fetches the
same number of texels either way. The bake itself goes 68 ms → 203 ms, paid once at load and
again on a Detail change. Memory goes **43 MB → 683 MB** with the mip chain, which is the number
to watch: that is why the size is negotiated rather than assumed. `texImage2D` does not throw on
an over-large allocation — it raises a GL error and leaves the texture incomplete, which would
surface later as a black surface rather than as a failed bake — so the error queue is drained,
checked, and the FBO tested for completeness before anything is drawn; anything the driver
refuses is halved and retried down to 512, and the PNG fallback still sits under all of it.
`window.DETAILN = 2048` before a Detail change pins the size by hand.

Anisotropic filtering was raised 8× → 16× at the same time. At 12× minification the anisotropy
cap carries more of the result than the texel count does, and the hardware here reports 16 while
the code was asking for half of it.
- **Scene** — `Colour field` (default) floats the form in the fluid backdrop with no ground.
  `Studio` puts it in a lit cyclorama with a real floor and a cast shadow.
- **Look** — the *press grade*, i.e. the label aesthetic. `Studio`, `Label`, `Riso`,
  `Xerox`, `Halftone`, `Chrome`, `Clean`. Drives duotone ink separation, posterisation,
  ordered dither, dot screen, misregistration/chromatic split and grain.
- **Palette / Colors** — background colourway. `Auto · Director` takes the hue from the
  current section's mood and slams the saturation; or pick Blood / Acid / Ultra / Cyber /
  Solar / Mono, or set the two colours by hand. The palette also feeds the duotone inks,
  so the whole frame stays on one colourway.

## Image-based lighting

The environments are **prefiltered offline** by `tools_pmrem.py`, which turns a Radiance `.hdr`
into an 8-level equirect mip chain stored as one float16 blob (`AENV` magic, 1.05 MB each):

```bash
python tools_pmrem.py studio.hdr assets/env_studio.bin
```

Doing the convolution in the browser was the alternative and it is strictly worse: it is the
expensive part, it never changes, and paying for it on every page load buys nothing. Shipping the
finished chain makes the runtime cost of IBL **one `textureLod`** — measured *cheaper* than the
analytic room it replaced (54.7 ms → 51.5 ms).

**Not hardware mips.** Tried first, measured, rejected: box-filtering an equirect map is far too
aggressive a convolution. By the level a satin surface selects, the room is a smear, and the render
came out *flatter* than the analytic room — the form's luminance range collapsed from 91 levels to
26, and it was slightly slower besides. A mip level here is a GGX lobe of a specific roughness,
importance-sampled with a Hammersley sequence, which is a different filter entirely and the one
the BRDF actually asks for. The **last** level is a *cosine* convolution rather than GGX, because
that level is what the diffuse term reads, and what a diffuse surface integrates is the
cosine-weighted hemisphere.

The parameterisation is **equal-area** (`y = sin(elevation)`), which is why no `sin(theta)`
weighting appears in the convolution: every texel already covers the same solid angle. Getting
that wrong biases the whole result toward the poles. Verified by energy conservation — every mip
mean lands within 1% of the source's 0.720.

## Clearcoat and subsurface

**Clearcoat** is a second GGX lobe over everything else, at its own roughness, with F0 fixed at
0.04 — a clear coat *is* a dielectric at IOR ~1.5 and its reflectance is not the artist's to pick.
It is what separates a glazed surface from a painted one: a tight highlight sitting on top of a
broad, rougher body highlight, instead of the single lobe both were previously sharing. The base
is attenuated by the coat's Fresnel, because light the coat reflects never reaches the material
underneath — skipping that is how a clearcoat ends up returning more light than it was given. The
coat reflects the environment as well as the key.

**Subsurface** replaces a flat back-lambert. What was there was `dot(n,-L)*0.18`, with no notion
of how much material the light had crossed, so a thin lip and the thickest part of the body glowed
identically — it read as a rim someone had drawn on rather than as light coming through something.
An SDF makes the honest version cheap: the field is negative inside the form, so marching from
just under the surface along -L and counting how much of that path stays interior **is** the
thickness. Beer-Lambert on that, exactly as glass already does with its absorption, plus a
forward-scatter lobe, because light leaving a translucent body is strongest looking back down the
beam. Six taps, bounded on purpose.

| material | clearcoat | coat roughness | subsurface extinction |
|---|---|---|---|
| pearl | 0.35 | 0.15 | 2.4 |
| clay (the ceramic entry) | 0.10 | 0.34 | 3.6 |
| frosted | 0.18 | 0.26 | 1.3 |
| holo | 0.45 | 0.08 | — (metal, no subsurface) |
| glass | — | — | — (transmission already carries it) |

Ablated on a pinned frame: clearcoat moves 0.33% of pixels and subsurface 0.73%, against a drift
floor of 0.04%. Both are real and both are meant to be quiet — a coat that announces itself is a
coat you have overdone.

**Measuring either requires setting the target as well as the value.** `MAT` is lerped toward
`MATT` every frame, so `MAT.cc = 0` had already decayed back to 0.244 within 500ms and the first
ablation measured pure drift for both terms. Set both, or measure nothing.

## Motion blur

**Reprojection, not accumulation.** Temporal accumulation was implemented here once, measured and
removed (`0130974`) — it trails on every cut and needs a history buffer that the adaptive render
scale invalidates the moment it resizes. This reconstructs where each pixel *was* one frame ago
and blurs along that vector, which is a pure function of the current frame: nothing to invalidate,
nothing to trail, correct through a resize.

The reconstruction is exact rather than a screen-space heuristic, because everything it needs is
already known. Alpha carries the ray's `t`, so the world point is `ro + rd*t`; the object's motion
is a rotation about the tilted Y axis by the spin delta; the previous camera basis is last frame's.
Undo the object rotation, project with the previous camera, and the difference is the velocity —
camera orbit and object spin fall out of the same expression with no separate cases. Pixels past
the `17.0` miss sentinel skip the object rotation and reproject on camera motion alone, which is
what makes the backdrop shear with an orbit while the form smears with its own spin.

Validated by holding the geometry perfectly still and telling the shader the form had rotated by a
known delta since the last frame — the image is then reproducible and the blur is the only
variable:

| spin delta (rad/frame) | pixels moved | softening |
|---|---|---|
| 0.02 | 0.66% | 0.1% |
| 0.06 | 1.56% | 10.5% |
| 0.15 | 2.61% | 17.4% |
| 0.35 | 3.24% | 19.7% |

Monotonic in velocity, which is the property that matters. **And it is a no-op when nothing
moves**: with spin and camera both pinned, blur on against blur off measured 0.80 mean / 0.10% of
pixels against a same-setting drift baseline of 0.76 / 0.06% — indistinguishable, so a held frame
is not being quietly softened.

Shutter is per tier (Preview 0, High 0.5, Ultra 0.8) and **dt-normalised to 60fps**: velocity is
measured per *frame*, so on a 30fps machine each frame covers twice the ground, and an
un-normalised shutter would double the smear exactly where the frame rate can least afford it.
Taps are jittered per pixel — eight evenly spaced samples of a moving edge print eight copies of
it, and the offset turns that ghosting into noise the grain already covers. The smear is capped at
5% of the frame.

Cost is not quoted here because it could not be measured cleanly: over six-frame medians the
blur-off A/A spread was 49.5 against 37.5 fps, wider than the difference to blur-on at 38.6, so
the governor and thermal drift dominate at this sample size. What can be said is that both early
-outs fire — zero shutter and sub-pixel velocity both return before any tap — so a still frame
pays nothing.

## Quality tiers

`Preview · High · Ultra`, replacing the old `Auto / Rays / Surface` row. Those three asked the
viewer to understand a render-scale governor, a volumetric toggle and a normal-map switch in order
to answer "make it look better", and two of the three were named after their implementation. Each
tier is now a complete point on the cost/quality curve, so the axes move together and cannot be
left in a nonsensical combination — supersampled, with the shadow budget of a preview.

| | dpr | render scale | shadow samples | march step near/far | supersample | measured |
|---|---|---|---|---|---|---|
| Preview | 0.72 | governor, floor 0.60 | 96 | 1.45 / 1.75 | 0.80 | 1.7 Mpx march, **66 fps** (58–74) |
| High | 1.00 | governor, floor 0.82 | 128 | 1.45 / 1.75 | 1.45 | 3.1 Mpx march, **42 fps** (32–52) |
| Ultra | 1.00 | 1.35, adapts 1.00–1.60 | 200 | 1.25 / 1.55 | 1.45 | 4.7 Mpx march, **26 fps** (20–34) |

Measured on an Apple M1 Pro at a 2330×1996 canvas (dpr 2), one frozen pose, Wave / pearl / studio
HDRI. Medians of 16 samples per tier.

**Taken INTERLEAVED, and that is not a formality.** A straight sequential pass — Preview, then
High, then Ultra, then High again — returned 50.1 fps for High on the first visit and 37.9 on the
last, at an identical 3.1 Mpx render scale. A 24% spread on the same configuration, from the
machine warming up over the 75 seconds the sweep took. Cycling the three tiers four times instead
spreads that drift evenly across all of them. The ranges in brackets are the honest spread; the
median is what to compare.

The shafts column is gone because every tier now carries `rays:0` — the screen-space
implementation is disabled everywhere, since the key light is never on screen for it to radiate
from (see the note above `TIERS`). Ultra no longer pins its scale either: it adapts between 1.00
and 1.60, and on this machine the governor settles it at the 1.00 floor.

Every knob is one with measured cost behind it: 96 → 128 shadow samples buys a 3.1× error cut, so
Preview takes it back and Ultra spends 200 where the 1/N curve is still paying; the march step was
measured at 1.45 → 20 fps against 1.70 → 24 fps at native, so Preview buys real time there.

**A tier binds the governor rather than suggesting to it.** Preview's render scale was handed
straight back on the first build: the governor saw the headroom Preview had just bought, spent it
on resolution, and the declared 0.72 became decorative — Preview and High measured an identical
8.0 Mpx grid. The tier now sets the ceiling the governor may climb to. Ultra is the one tier that
starts from a declared scale (1.35) rather than from the governor, but it is no longer excluded
from adaptation: it moves between a 1.00 floor and a 1.60 ceiling, because a scale it cannot hold
is not a quality setting, it is a stutter. On the machine in the table above the governor settles
it at the floor.

The individual switches are still reachable from the console for measuring — `raysOn`,
`STEP.shaN`, `STEP.near` — they simply no longer have buttons. (`surfTarget` was one of them and
is gone entirely; surface detail no longer exists to switch.)

## Library (Controls > Library)

Picks a track from your music library and renders it. The library is used as an
**index** — the audio always comes from a file on your own machine.

That split is forced, not a shortcut. Neither Spotify nor Apple will hand a web
page decoded audio: playback runs through a protected pipeline specifically so
nothing can tap the samples, which is exactly what `analyze.py` needs. What the
APIs do allow is reading your library, so that is what they are used for.

Two ways in, and the second needs no developer account:

**Paste a list.** One `Artist - Title` per line, or an exported playlist. In
Music.app that is `File > Library > Export Playlist` — tab-separated with Name,
Artist and Time columns, so an Apple library arrives with its durations and the
length check below runs in full.

**Connect Spotify.** Needs a client id from developer.spotify.com. Register the
redirect URI as exactly:

    http://127.0.0.1:8770/callback

A loopback IP literal is the only `http` redirect Spotify still accepts —
`localhost` is rejected, and the failure happens at the consent screen without
saying why. The flow is authorization-code + PKCE, so there is no client secret
to keep; the id and token live in your browser's localStorage and nowhere else.

Then **Music folder** to point at your files. Nothing is uploaded until you pick
a track; the folder is read in the browser.

### How a track is matched to a file

Text shortlists, length decides.

The text score is asymmetric on purpose. Jaccard was the obvious choice and it
is wrong here, because it penalises both sides equally for tokens the other
lacks — and the file side is *supposed* to have more. A library on disk is
normally `Artist/Album/NN Title`, so "Radiohead Karma Police" gets compared
against "radiohead ok computer 03 karma police": every query token is present
and Jaccard still scored it 0.500, below any threshold that also rejects a wrong
track. Scoring `matched / (query + 0.35 x extra)` puts that at 0.741 while the
nearest wrong answer sits at 0.597, and the bar is 0.65.

Text alone cannot finish the job, and it is worth being precise about why. A
query that is a strict *subset* of a wrong filename scores at the top under any
metric: "The Beatles Yesterday" against "The Beatles - Yesterday and Today"
covers every query token. No threshold separates those, because in the text
there is nothing to separate.

Length does separate them. Spotify returns `duration_ms`, exported playlists
carry a Time column, and the local file's duration is read from its container
header — so every match is confirmed against it, in the background for the whole
list and again on the click that spends a credit. Tolerance is 2.5s: wide enough
for a trimmed fade or a gapless boundary, far narrower than two different songs.
A file that disagrees is dropped with the reason shown, rather than rendered.

Anything unmatched stays listed and greyed; the Load button still takes a file
directly.


## Export (bottom bar)
**Frame** saves a PNG at full canvas resolution; **Record** captures the canvas to WebM via
`MediaRecorder` (VP9 where available, falling back to VP8 or mp4 for Safari). The frame grab is
serviced at the *end of the draw*, not from the click handler: the context has no
`preserveDrawingBuffer` — it costs a full-size copy every frame — so the drawing buffer is
cleared at composite and a handler-time `toBlob()` returns a blank image. Recording uses 1s
timeslices so a crash still leaves usable footage.

## Tests
```bash
python tests/test_director.py
```
Covers the `director.json` contract the whole renderer depends on: schema keys, curve alignment
(all curves one length, no NaN/inf), section ordering, per-band onsets, and tempo accuracy within
6% at 100/128/140bpm. Dependency-free by design — the venv installs only what the pipeline needs,
and a test that cannot run because of a missing dev dependency protects nothing. Audio is
synthesised rather than committed as a fixture so the true tempo is *known*; a recorded fixture
would only assert that today's output matches yesterday's, mistakes included.

Section palettes in `analyze.py` must carry a **clear hue** — a near-grey palette gives a
washed-out backdrop, since the renderer slams saturation to derive the ink. `mono` is
deliberately neutral: it produces the stark black & white press look.

## Rendering notes
Raymarched SDF → RGBA16F → post. Quality is adaptive: `rscale` tracks frame time so it
holds framerate on weaker GPUs. Bloom comes off a dedicated half-res bright pass and the
half-float mip chain, with a 25-tap fallback when a GPU can't mipmap RGBA16F. AO and a
soft shadow give the form volume — both use `mapD()`, which undoes the deliberate 0.5
scaling `map()` applies, otherwise free space reads as fully occluded.

**Shadows.** The `Colour field` scene has none: the forms float clear of any ground, so a
projected shadow reads as a second object rather than as contact, and it was removed for
the same reason the ground plane before it was. `Studio` has a real floor and a real cast
shadow.

`calcSha()` does **not** sphere trace. Its penumbra estimate is `min(k*h/t)` along the ray,
and a min over sparse samples is discontinuous in the shaded point, so a sphere trace's
metre-long steps across open floor printed terraced contour rings. It instead intersects the
bounding sphere analytically — no hit means lit, at zero field evaluations — then spends a
fixed *count* of samples across the chord, so no sample can pop in or out.

That fixed count was **40, and it was too few** — not on the floor, which was already clean, but
on the form itself, where it left nested contours running parallel to the silhouette across every
flat face. Against a converged N=800 reference over the whole object the error is 4.31 levels RMS
at N=40 and falls as a clean 1/N (2.83 at 64, 1.90 at 96, 1.40 at 128, 0.86 at 200) — plain
undersampling, which converges that slowly because `k*h/t` is V-shaped near a grazing occluder.

Three reformulations were measured against that reference and **all three were worse**:

| attempt | RMS | why it fails |
|---|---|---|
| even spread, N=40 (baseline) | 4.31 | — |
| 60% of the budget front-loaded into the first 0.7 units | 5.11 | the minimum is not decided near the surface; on a form 2.3 across, the ray passes closest to *another lobe* at t = 1..3 |
| constant 2R step instead of chord-relative | 6.74 | positions stop sliding, but most of the budget lands past `t1` where nothing can occlude |
| closest-approach correction between samples | 4.35 | moves the penumbra (2.42 RMS from the plain answer) without removing the sampling error |

So the fix is simply `STEP.shaN = 128`. Measured cost, interleaved A/B/A over 45 paired frames at
a native 4.6 Mpx target: 12.6 ms → 15.1 ms, about +20%, or 0.028 ms per sample — cheap because
only pixels that hit the form run the shadow ray at all. It is one constant if you want the
frame time back.

The deeper cause of the same rings: `map()`'s bounding-sphere early-out returns the distance
to a sphere of radius 1.45 while the branch below returns the distance to the real form, and
the two disagree at the switch. A raymarch tolerates that (the value is still a conservative
lower bound); anything reading the field's *value* sees a cliff. The switch is pushed out to
0.85 in the studio scene, which puts the disagreement beyond where it can darken anything
visible, and left tight in the colour field, which has no floor to show it and spends the
budget on resolution instead.

**Debug hooks.** `QLOCK = true` freezes the adaptive quality governor; `TFREEZE = <seconds>`
pins the clock *and* every dt-integrated value, which is the only way to A/B two builds on an
identical frame. `DBGMASK = 1` outputs the depth-derived silhouette instead of the image.
`DX = {bloom, chroma, sharpen, thr}` ablates individual post-pass terms and
`DXS = {ao, shadow, disp, twoTone}` does the same for the scene pass — set `DXS.shadow = 0` and
the contour banding above vanishes in one step, which is how it was pinned on `calcSha()` rather
than on the detail map it was sitting under. `KEYG` / `FILLG` / `AMBG` override the three IBL rig
gains for sweeping the key-to-ambient ratio. All of it exists because reasoning about render
defects instead of measuring them has cost several wrong fixes here.

## Next
Camera + world evolution driven further by the Director State (per-section framing,
predictive pre-roll into drops).
