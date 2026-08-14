import { section, slider, select, color, toggle, buttons } from './controls.js';
import { PRESETS } from '../renderer/MaterialManager.js';

/**
 * Panels — the editor UI.
 *
 * Each panel takes the store and writes to exactly one section of it. No panel imports the
 * renderer, and no panel is told when the renderer finishes a frame; the only coupling is the
 * store. That is what keeps a slider from being able to stall the loop.
 */

export function buildPanels(host, store, api) {
  host.innerHTML = '';

  /* ---------------- SOURCE ---------------- */
  {
    const { wrap, body } = section('Source');
    buttons(body, { items: [
      { label: 'Upload SVG', onClick: () => api.pickFile() },
      { label: 'Sample', onClick: () => api.loadSample() },
    ]});
    host.append(wrap);
  }

  /* ---------------- GEOMETRY ---------------- */
  {
    const { wrap, body } = section('Geometry');
    const g = store.get('geometry');
    // commit:true — each of these rebuilds an ExtrudeGeometry, so they fire on release, not
    // on every pixel of the drag. The readout still tracks live.
    slider(body, { label: 'Depth', min: 0.2, max: 3, step: 0.01, value: g.depth, commit: true,
      onChange: (v) => store.set('geometry', { depth: v }) });
    slider(body, { label: 'Bevel', min: 0, max: 0.3, step: 0.005, value: g.bevel, commit: true,
      onChange: (v) => store.set('geometry', { bevel: v }) });
    slider(body, { label: 'Bevel seg', min: 1, max: 8, step: 1, value: g.bevelSegments, commit: true,
      onChange: (v) => store.set('geometry', { bevelSegments: v }) });
    slider(body, { label: 'Curve seg', min: 3, max: 24, step: 1, value: g.curveSegments, commit: true,
      onChange: (v) => store.set('geometry', { curveSegments: v }) });
    toggle(body, { label: 'Flat shading', value: g.flatShading,
      onChange: (v) => store.set('geometry', { flatShading: v }) });
    host.append(wrap);
  }

  /* ---------------- MATERIAL ---------------- */
  {
    const { wrap, body } = section('Material');
    const m = store.get('material');
    const refs = {};
    select(body, { label: 'Preset', value: m.type,
      options: Object.keys(PRESETS).map((k) => ({ value: k, label: k[0].toUpperCase() + k.slice(1) })),
      onChange: (v) => {
        const p = PRESETS[v];
        // __preset tells the renderer to seed from the preset before applying, and the sliders
        // are moved to match so the panel never shows values the material does not have.
        store.set('material', { ...p, type: v, __preset: true });
        for (const k in p) refs[k]?.set(p[k]);
      }});
    color(body, { label: 'Color', value: m.color, onChange: (v) => store.set('material', { color: v }) });
    refs.roughness = slider(body, { label: 'Roughness', min: 0, max: 1, step: 0.01, value: m.roughness,
      onChange: (v) => store.set('material', { roughness: v }) });
    refs.metalness = slider(body, { label: 'Metalness', min: 0, max: 1, step: 0.01, value: m.metalness,
      onChange: (v) => store.set('material', { metalness: v }) });
    refs.opacity = slider(body, { label: 'Opacity', min: 0, max: 1, step: 0.01, value: m.opacity,
      onChange: (v) => store.set('material', { opacity: v }) });
    refs.transmission = slider(body, { label: 'Transmission', min: 0, max: 1, step: 0.01, value: m.transmission,
      onChange: (v) => store.set('material', { transmission: v }) });
    refs.clearcoat = slider(body, { label: 'Clearcoat', min: 0, max: 1, step: 0.01, value: m.clearcoat,
      onChange: (v) => store.set('material', { clearcoat: v }) });
    refs.clearcoatRoughness = slider(body, { label: 'CC rough', min: 0, max: 1, step: 0.01, value: m.clearcoatRoughness,
      onChange: (v) => store.set('material', { clearcoatRoughness: v }) });
    refs.reflectivity = slider(body, { label: 'Reflectivity', min: 0, max: 1, step: 0.01, value: m.reflectivity,
      onChange: (v) => store.set('material', { reflectivity: v }) });
    refs.ior = slider(body, { label: 'IOR', min: 1, max: 2.4, step: 0.01, value: m.ior,
      onChange: (v) => store.set('material', { ior: v }) });
    host.append(wrap);
  }

  /* ---------------- LIGHTING ---------------- */
  {
    const { wrap, body } = section('Lighting');
    const l = store.get('lighting');
    select(body, { label: 'Environment', value: l.environment,
      options: [{ value: 'studio', label: 'Studio' }, { value: 'none', label: 'None' }],
      onChange: (v) => { api.setEnvironment(v); store.set('lighting', { environment: v }); }});
    slider(body, { label: 'Env intensity', min: 0, max: 3, step: 0.01, value: l.envIntensity,
      onChange: (v) => store.set('lighting', { envIntensity: v }) });
    slider(body, { label: 'Env rotation', min: 0, max: 1, step: 0.005, value: l.envRotation,
      onChange: (v) => store.set('lighting', { envRotation: v }) });
    slider(body, { label: 'Key', min: 0, max: 10, step: 0.05, value: l.keyIntensity,
      onChange: (v) => store.set('lighting', { keyIntensity: v }) });
    slider(body, { label: 'Key azimuth', min: 0, max: 1, step: 0.005, value: l.keyAzimuth,
      onChange: (v) => store.set('lighting', { keyAzimuth: v }) });
    slider(body, { label: 'Key elevation', min: -1.2, max: 1.5, step: 0.01, value: l.keyElevation,
      onChange: (v) => store.set('lighting', { keyElevation: v }) });
    slider(body, { label: 'Softness', min: 0, max: 1, step: 0.01, value: l.keySoftness,
      onChange: (v) => store.set('lighting', { keySoftness: v }) });
    slider(body, { label: 'Fill', min: 0, max: 5, step: 0.05, value: l.fillIntensity,
      onChange: (v) => store.set('lighting', { fillIntensity: v }) });
    slider(body, { label: 'Rim', min: 0, max: 5, step: 0.05, value: l.rimIntensity,
      onChange: (v) => store.set('lighting', { rimIntensity: v }) });
    slider(body, { label: 'Ambient', min: 0, max: 2, step: 0.01, value: l.ambient,
      onChange: (v) => store.set('lighting', { ambient: v }) });
    toggle(body, { label: 'Shadows', value: l.shadows,
      onChange: (v) => store.set('lighting', { shadows: v }) });
    slider(body, { label: 'Shadow', min: 0, max: 1, step: 0.01, value: l.shadowIntensity,
      onChange: (v) => store.set('lighting', { shadowIntensity: v }) });
    host.append(wrap);
  }

  /* ---------------- CAMERA ---------------- */
  {
    const { wrap, body } = section('Camera');
    const c = store.get('camera');
    select(body, { label: 'Projection', value: c.type,
      options: [{ value: 'perspective', label: 'Perspective' }, { value: 'orthographic', label: 'Orthographic' }],
      onChange: (v) => store.set('camera', { type: v }) });
    slider(body, { label: 'FOV', min: 15, max: 90, step: 1, value: c.fov,
      onChange: (v) => store.set('camera', { fov: v }) });
    slider(body, { label: 'Distance', min: 1, max: 20, step: 0.05, value: c.distance,
      onChange: (v) => store.set('camera', { distance: v, autoFrame: false }) });
    buttons(body, { label: 'Preset', items:
      ['front', 'back', 'left', 'right', 'top', 'isometric', 'perspective'].map((p) => ({
        label: p.slice(0, 3).toUpperCase(), onClick: () => api.cameraPreset(p) })) });
    host.append(wrap);
  }

  /* ---------------- ANIMATION ---------------- */
  {
    const { wrap, body } = section('Animation');
    const a = store.get('animation');
    toggle(body, { label: 'Enabled', value: a.enabled, onChange: (v) => store.set('animation', { enabled: v }) });
    select(body, { label: 'Type', value: a.type, options: [
      { value: 'rotation', label: 'Rotation' }, { value: 'rotationH', label: 'Horizontal' },
      { value: 'rotationV', label: 'Vertical' }, { value: 'float', label: 'Float' },
      { value: 'pulse', label: 'Pulse' }],
      onChange: (v) => store.set('animation', { type: v }) });
    select(body, { label: 'Axis', value: a.axis, options: ['X', 'Y', 'Z'],
      onChange: (v) => store.set('animation', { axis: v }) });
    slider(body, { label: 'Speed', min: 0, max: 2, step: 0.01, value: a.speed,
      onChange: (v) => store.set('animation', { speed: v }) });
    slider(body, { label: 'Amplitude', min: 0, max: 1, step: 0.01, value: a.amplitude,
      onChange: (v) => store.set('animation', { amplitude: v }) });
    select(body, { label: 'Direction', value: String(a.direction),
      options: [{ value: '1', label: 'Forward' }, { value: '-1', label: 'Reverse' }],
      onChange: (v) => store.set('animation', { direction: +v }) });
    host.append(wrap);
  }

  /* ---------------- EFFECTS ---------------- */
  {
    const { wrap, body } = section('Effects');
    const e = store.get('effects');
    const s = (label, key, min, max, step) => slider(body, { label, min, max, step, value: e[key],
      onChange: (v) => store.set('effects', { [key]: v }) });
    s('Bloom', 'bloom', 0, 2, 0.01);
    s('Threshold', 'bloomThreshold', 0, 1, 0.01);
    s('Bloom radius', 'bloomRadius', 0, 1, 0.01);
    s('Depth of field', 'depthOfField', 0, 1, 0.01);
    s('Focus', 'focus', 0.5, 20, 0.05);
    s('Vignette', 'vignette', 0, 1, 0.01);
    s('Grain', 'grain', 0, 1, 0.01);
    s('Chromatic', 'chromatic', 0, 1, 0.01);
    s('Exposure', 'exposure', 0.1, 3, 0.01);
    s('Contrast', 'contrast', 0.5, 2, 0.01);
    s('Saturation', 'saturation', 0, 2, 0.01);
    host.append(wrap);
  }

  /* ---------------- BACKGROUND ---------------- */
  {
    const { wrap, body } = section('Background');
    const b = store.get('background');
    select(body, { label: 'Type', value: b.type, options: [
      { value: 'solid', label: 'Solid' }, { value: 'gradient', label: 'Gradient' },
      { value: 'transparent', label: 'Transparent' }, { value: 'environment', label: 'Environment' }],
      onChange: (v) => store.set('background', { type: v }) });
    color(body, { label: 'Color', value: b.color, onChange: (v) => store.set('background', { color: v }) });
    color(body, { label: 'Color B', value: b.colorB, onChange: (v) => store.set('background', { colorB: v }) });
    slider(body, { label: 'Opacity', min: 0, max: 1, step: 0.01, value: b.opacity,
      onChange: (v) => store.set('background', { opacity: v }) });
    host.append(wrap);
  }

  /* ---------------- EXPORT ---------------- */
  {
    const { wrap, body } = section('Export');
    buttons(body, { label: 'Image', items: [
      { label: 'PNG 1x', onClick: () => api.exportImage({ scale: 1 }) },
      { label: 'PNG 2x', onClick: () => api.exportImage({ scale: 2 }) },
      { label: 'PNG 4x', onClick: () => api.exportImage({ scale: 4 }) },
      { label: 'JPG 2x', onClick: () => api.exportImage({ scale: 2, format: 'jpeg' }) },
      { label: 'PNG alpha', onClick: () => api.exportImage({ scale: 2, transparent: true }) },
    ]});
    buttons(body, { label: 'Scene', items: [
      { label: 'GLB', onClick: () => api.exportGLB() },
      { label: 'Record', onClick: () => api.toggleVideo() },
    ]});
    host.append(wrap);
  }
}
