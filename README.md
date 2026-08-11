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
- **Shape** — `Auto · Director` lets the Director pick, or lock one of Organic / Helix / Gem / Petals / Twist.
- **Material** — Pearl, Iridescent, Chrome, Frosted, Holographic, Neon.
- **Look** — the *press grade*, i.e. the label aesthetic. `Label` (default), `Riso`,
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
Raymarched SDF → RGBA16F → post. Quality is adaptive: `rscale` tracks frame time
(0.50–1.0) so it holds framerate on weaker GPUs. Bloom comes off the half-float mip chain
(4 taps, wide and soft) with a 25-tap fallback when a GPU can't mipmap RGBA16F. AO and a
short soft shadow give the form volume — both use `mapD()`, which undoes the deliberate
0.5 scaling `map()` applies, otherwise free space reads as fully occluded.

## Next
Camera + world evolution driven further by the Director State (per-section framing,
predictive pre-roll into drops).
