import { SceneStore, defaultState } from './state/sceneStore.js';
import { Renderer } from './renderer/Renderer.js';
import { ExportManager } from './renderer/ExportManager.js';
import { buildPanels } from './components/panels.js';

const $ = (id) => document.getElementById(id);
const canvas = $('gl');
const store = new SceneStore(defaultState());

let renderer, exporter;
try {
  renderer = new Renderer(canvas, store);
  exporter = new ExportManager(renderer);
} catch (err) {
  // A hard failure here is almost always "no WebGL2". Say so plainly instead of leaving a
  // black rectangle and a console trace nobody will look at.
  $('boot').innerHTML = `<h2>Cannot start the renderer</h2><p>${String(err.message || err)}</p>`;
  $('boot').classList.add('err');
  throw err;
}

function status(msg, isErr = false, ms = 1800) {
  const s = $('stat');
  s.textContent = msg;
  s.classList.toggle('err', isErr);
  s.style.display = 'flex';
  clearTimeout(status._t);
  if (ms) status._t = setTimeout(() => { s.style.display = 'none'; }, ms);
}

/** A minimal SVG so the tool is never staring at an empty canvas on first open. */
const SAMPLE = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <path d="M50 6 L94 88 L6 88 Z" fill="#fff"/>
  <circle cx="50" cy="64" r="13" fill="#000"/>
</svg>`;

function ingest(text, label) {
  try {
    const n = renderer.loadSVG(text);
    $('boot').style.display = 'none';
    status(`${n} path${n === 1 ? '' : 's'} extruded — ${label}`);
  } catch (err) {
    status(err.message || 'Could not parse that SVG', true, 3600);
  }
}

const api = {
  pickFile: () => $('file').click(),
  loadSample: () => ingest(SAMPLE, 'sample'),
  cameraPreset: (p) => { renderer.cameraMgr.setPreset(p); store.set('camera', { autoFrame: false }); },
  setEnvironment: (v) => renderer.lightingMgr.setEnvironment(v, store.get('lighting').envIntensity),
  exportImage: async (opts) => {
    try { const r = await exporter.image(opts); status(`Saved ${r.w}x${r.h} (${(r.bytes / 1048576).toFixed(1)} MB)`); }
    catch (err) { status(err.message, true, 3600); }
  },
  exportGLB: async () => {
    try { const r = await exporter.glb(); status(`Saved GLB (${(r.bytes / 1024).toFixed(0)} KB)`); }
    catch (err) { status(err.message, true, 3600); }
  },
  exportUSDZ: async () => {
    try { const r = await exporter.usdz(); status(`Saved USDZ (${(r.bytes / 1024).toFixed(0)} KB)`); }
    catch (err) { status(err.message, true, 3600); }
  },
  pickHDR: () => $('hdr').click(),
  pickBackgroundImage: () => $('bgimg').click(),
  pickAudio: () => $('audio').click(),
  togglePlay: () => {
    const a = renderer.audio;
    if (!a.hasTrack) { status('Load a track first', true, 2200); return; }
    if (a.playing) { a.pause(); status('Paused'); } else { a.play(); status('Playing'); }
  },
  toggleVideo: () => {
    try {
      if (exporter.recording) { exporter.stopVideo(); return; }
      const mime = exporter.startVideo((r) => status(`Saved ${r.ext.toUpperCase()} (${(r.bytes / 1048576).toFixed(1)} MB)`));
      status(`Recording ${mime.split(';')[0]} — press Record again to stop`, false, 0);
    } catch (err) { status(err.message, true, 3600); }
  },
};

buildPanels($('panels'), store, api);

$('file').addEventListener('change', (e) => {
  const f = e.target.files?.[0];
  if (!f) return;
  f.text().then((t) => ingest(t, f.name));
  e.target.value = '';        // so re-picking the same file fires again
});

$('hdr').addEventListener('change', async (e) => {
  const f = e.target.files?.[0]; e.target.value = '';
  if (!f) return;
  status('Prefiltering HDRI…', false, 0);
  try {
    await renderer.lightingMgr.setEnvironmentFromHDR(await f.arrayBuffer(), store.get('lighting').envIntensity);
    store.set('lighting', { environment: 'hdri' });
    renderer.lightingMgr.apply(store.get('lighting'));   // re-apply: setEnvironment resets intensity
    status(`Environment — ${f.name}`);
  } catch (err) {
    status(`Could not read that .hdr — ${err.message}`, true, 4000);
  }
});

$('bgimg').addEventListener('change', (e) => {
  const f = e.target.files?.[0]; e.target.value = '';
  if (!f) return;
  const img = new Image();
  const url = URL.createObjectURL(f);
  img.onload = () => {
    renderer.sceneMgr.setBackgroundImage(img);
    store.set('background', { type: 'image' });
    renderer.sceneMgr.setViewportAspect(canvas.clientWidth / Math.max(1, canvas.clientHeight));
    URL.revokeObjectURL(url);
    status(`Background — ${f.name}`);
  };
  img.onerror = () => { URL.revokeObjectURL(url); status('Could not decode that image', true, 3000); };
  img.src = url;
});

$('audio').addEventListener('change', async (e) => {
  const f = e.target.files?.[0]; e.target.value = '';
  if (!f) return;
  await ingestAudio(f);
});

async function ingestAudio(f) {
  status('Decoding…', false, 0);
  try {
    const d = await renderer.audio.load(f, (p) => {
      // stage + percentage, the same side-channel the viewer polls
      const pct = Math.round((p.p ?? 0) * 100);
      status(`${p.stage || 'analysing'} ${pct}%`, false, 0);
    });
    const info = $('audioInfo');
    if (info) info.textContent = `${f.name} · ${d.tempo.bpm} BPM · ${d.genre.primary}`;
    renderer.audio.play();
    status(`${d.sections.length} sections · ${d.tempo.bpm} BPM · ${d.genre.primary}`);
  } catch (err) {
    status(`Analysis failed — ${err.message}`, true, 4200);
  }
}

// Drag and drop anywhere on the viewport.
addEventListener('dragover', (e) => e.preventDefault());
addEventListener('drop', (e) => {
  e.preventDefault();
  const files = [...e.dataTransfer.files];
  const svg = files.find((x) => /svg/i.test(x.type) || /\.svg$/i.test(x.name));
  const aud = files.find((x) => /^audio\//i.test(x.type) || /\.(mp3|wav|m4a|flac|ogg|aiff?)$/i.test(x.name));
  if (svg) svg.text().then((t) => ingest(t, svg.name));
  if (aud) ingestAudio(aud);
  if (!svg && !aud) status('Drop an .svg or an audio file', true, 2400);
});

$('toggle').onclick = () => {
  const hidden = document.body.classList.toggle('collapsed');
  $('toggle').textContent = hidden ? 'Panels' : 'Hide';
};

renderer.start();
ingest(SAMPLE, 'sample');

// exposed for debugging from the console; nothing in the app reads these
window.__studio = { store, renderer, exporter };
