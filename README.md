# ATONAL — AI Music Understanding Engine (backend)

Analyzes a track and emits a **Director State** (`director.json`) — the single contract the
renderer consumes. The renderer never sees FFT; it reads the Director's decisions.

## Layers
1. **Signal** (librosa): RMS, centroid, flatness, rolloff, bandwidth, ZCR, spectral flux,
   contrast, 20 MFCCs, chroma-CQT, HPSS harmonic/percussive, tempo+beats, tempo stability,
   crest factor, dynamic range, stereo width / phase correlation.
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
events{beats[], predictions[]{t,type,lead}}
curves{t[], energy[],brightness[],arousal[],valence[],tension[],darkness[],
       warmth[],danceability[],epicness[],flux[],percussive[],harmonic[],density[]}   # 30 Hz
```

## Next
Frontend renderer consumes this over the server, synced to audio playback:
pearlescent instanced generative geometry (cluster → radial → spiral), camera + world
evolution driven by the Director State.
