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

The server (`server.py`) serves the viewer at `/`, analyses at `POST /analyze`, and reports `GET /health`.
The PANNs model auto-downloads to `~/panns_data/` on first run.

## Contract: director.json (schema `atonal.director/1`)
```
meta{duration,director_fps,analysis_sr,hop}
tempo{bpm,stability,crest_db,dynamic_range_db,stereo_width}
genre{primary,confidence,secondary,method,top_tags[],moods{}}
sections[]{t0,t1,label,energy,tension,valence,camera,composition,mood,palette,
           motion,fog,bloom,grain,intent}
events{beats[], onsets{sub|low|mid|high|air: [[t,strength],…]}, predictions[]{t,type,lead}}
curves{t[], energy[],brightness[],arousal[],valence[],tension[],darkness[],
       warmth[],danceability[],epicness[],flux[],percussive[],harmonic[],density[],
       sub[],low[],mid[],high[],air[]}                                              # 30 Hz
```

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
- **Shape** — `Auto · Director` lets the Director pick, or lock one of Pods / Ribbon /
  Tower / Gyroscope / Space Frame / Shell.
- **Material** — Pearl, Glass, Clay, Fur, Frosted, Holographic. Glass **marches its own
  interior**: the field is negative inside the form, so `-mapD` carries the refracted ray to where
  it actually leaves. That gives thickness, which drives Beer-Lambert absorption so thin edges stay
  clear and the body deepens in colour, and a second refraction at the exit surface, where the
  dispersion is applied. Refracting once at the front surface treats the form as an infinitely thin
  shell — it bends the view but has no inside, which is why it read as a soap film.
  Clay has **no surface at all**: `tamp:0` early-outs the texture normal and `trough:0` early-outs
  the roughness variation *and* zeroes the albedo grain, which is scaled by it. Fur has **real strand geometry** — the SDF
  is displaced by squashed, squared noise inside a thin shell around the surface — plus a grazing
  sheen and a wrapped diffuse so it has no hard terminator. Clay is fully matte: `spec:0` gates
  the direct lobe as well as the environment reflection, and it carries its own lower albedo,
  because the shared near-white base blew out across every lit face.

Displacing the field raises its Lipschitz constant to about `1 + 2*A*F`, and the march may only
step by `distance/L` or it cuts the corner off every strand and the surface creases. `u_hairStep`
carries that bound, derived in JS from the same two numbers the shader displaces with. It is
applied **only inside the strand shell** — scaling the whole field by it slows the entire approach
through empty space and cost fur 42 fps down to 13.9.

Surface detail is **procedural** — there are no image textures, so there is no map resolution to
raise. Detail is amplitude x frequency, and the lever is the per-material base frequency `tscale`,
not octave count: the fbm runs at gain 0.42, so octaves 6 and 7 would together carry about 4% of
the amplitude for 40% more noise evaluations. Both "more octaves" and procedural mipmapping
(fading octaves by `fwidth` footprint) were implemented, measured and reverted — the mipmapping
moved pixel-scale energy inside the silhouette by 1.5-3.1% across four materials, one of them
negative, which is noise. It has nothing to filter because the octaves fine enough to alias are
the ones already carrying almost no amplitude.
- **Scene** — `Colour field` (default) floats the form in the fluid backdrop with no ground.
  `Studio` puts it in a lit cyclorama with a real floor and a cast shadow.
- **Look** — the *press grade*, i.e. the label aesthetic. `Studio`, `Label`, `Riso`,
  `Xerox`, `Halftone`, `Chrome`, `Clean`. Drives duotone ink separation, posterisation,
  ordered dither, dot screen, misregistration/chromatic split and grain.
- **Palette / Colors** — background colourway. `Auto · Director` takes the hue from the
  current section's mood and slams the saturation; or pick Blood / Acid / Ultra / Cyber /
  Solar / Mono, or set the two colours by hand. The palette also feeds the duotone inks,
  so the whole frame stays on one colourway.

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

The deeper cause of the same rings: `map()`'s bounding-sphere early-out returns the distance
to a sphere of radius 1.45 while the branch below returns the distance to the real form, and
the two disagree at the switch. A raymarch tolerates that (the value is still a conservative
lower bound); anything reading the field's *value* sees a cliff. The switch is pushed out to
0.85 in the studio scene, which puts the disagreement beyond where it can darken anything
visible, and left tight in the colour field, which has no floor to show it and spends the
budget on resolution instead.

**Debug hooks.** `QLOCK = true` freezes the adaptive quality governor; `TFREEZE = <seconds>`
pins the clock *and* every dt-integrated value, which is the only way to A/B two builds on an
identical frame. Both exist because reasoning about render defects instead of measuring them
has cost several wrong fixes here.

## Next
Camera + world evolution driven further by the Director State (per-section framing,
predictive pre-roll into drops).
