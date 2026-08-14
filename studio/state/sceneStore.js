/**
 * sceneStore — the single description of what should be on screen.
 *
 * The renderer is the source of truth for the OUTPUT; this is the source of truth for the
 * INTENT. UI writes here, managers read here. Nothing in this file touches WebGL, and nothing
 * here runs per frame.
 *
 * Subscription is per top-level SECTION rather than one global "something changed" callback.
 * That is what lets a roughness tweak rebind a single uniform while a depth tweak rebuilds
 * geometry — with one callback every manager would have to diff the whole tree on every
 * keystroke, and rebuilding an ExtrudeGeometry because a light moved is exactly the
 * "destroy and recreate the world" behaviour this design has to avoid.
 */

export const defaultState = () => ({
  geometry: { depth: 0.5, bevel: 0.08, bevelSegments: 3, curveSegments: 12, flatShading: false },
  material: {
    type: 'chrome', color: '#ffffff', roughness: 0.15, metalness: 1.0, opacity: 1.0,
    transmission: 0.0, clearcoat: 0.0, clearcoatRoughness: 0.1, reflectivity: 0.5,
    normalIntensity: 0.0, ior: 1.5,
  },
  // Calibrated against the chrome preset, which is the harshest case: a near-mirror reflects the
  // environment AND takes the full key, so it clips first. At key 3.0 with env 1.0 the default
  // view was a flat white silhouette — every surface above 1.0 and tone-mapped to the same
  // value, which reads as a broken renderer rather than as polished metal. The environment now
  // carries the look and the key is a highlight on top of it, not the primary source.
  lighting: {
    environment: 'studio', envIntensity: 0.9, envRotation: 0.0,
    keyIntensity: 1.5, keyAzimuth: 0.6, keyElevation: 0.9, keySoftness: 0.5,
    fillIntensity: 0.35, rimIntensity: 0.7, ambient: 0.12,
    shadowIntensity: 0.8, shadows: true,
  },
  camera: { type: 'perspective', fov: 45, distance: 4, autoFrame: true },
  animation: { enabled: true, type: 'rotation', speed: 0.25, axis: 'Y', direction: 1, amplitude: 0.15 },
  effects: {
    // Threshold 0.85 caught most of a bright metal's body, not just its highlights, and bloomed
    // the whole form into a glowing blob. 0.95 restricts it to what is genuinely near-clipping.
    bloom: 0.22, bloomThreshold: 0.95, bloomRadius: 0.4,
    depthOfField: 0.0, focus: 4.0, aperture: 0.02,
    vignette: 0.25, grain: 0.05, chromatic: 0.0, motionBlur: 0.0,
    exposure: 1.0, contrast: 1.0, saturation: 1.0,
  },
  background: { type: 'solid', color: '#111111', colorB: '#2a2a33', opacity: 1.0, image: null },
  quality: { maxPixelRatio: 2, adaptive: true },
});

export class SceneStore {
  constructor(initial = defaultState()) {
    this.state = initial;
    this._subs = new Map();   // section -> Set<fn>
  }

  /** Subscribe to one section. Returns an unsubscribe. */
  on(section, fn) {
    if (!this._subs.has(section)) this._subs.set(section, new Set());
    this._subs.get(section).add(fn);
    return () => this._subs.get(section)?.delete(fn);
  }

  get(section) { return this.state[section]; }

  /**
   * Merge a patch into one section and notify only that section's listeners.
   * Returns the section's new value.
   */
  set(section, patch) {
    const cur = this.state[section];
    if (!cur) throw new Error(`sceneStore: unknown section "${section}"`);
    let changed = false;
    for (const k in patch) {
      if (cur[k] !== patch[k]) { cur[k] = patch[k]; changed = true; }
    }
    // Bail on a no-op write. Sliders fire continuously and often repeat a value; without this
    // a drag that does not actually move the number still triggers a geometry rebuild.
    if (changed) this._emit(section, cur, patch);
    return cur;
  }

  _emit(section, value, patch) {
    const subs = this._subs.get(section);
    if (!subs) return;
    for (const fn of subs) {
      // One throwing listener must not stop the others: a broken effects panel should never
      // prevent the geometry from updating.
      try { fn(value, patch); } catch (err) { console.error(`[sceneStore] ${section} listener:`, err); }
    }
  }

  /** Replace everything (preset load / reset). Emits every section once. */
  replace(next) {
    this.state = next;
    for (const section of Object.keys(next)) this._emit(section, next[section], next[section]);
  }

  toJSON() { return JSON.parse(JSON.stringify(this.state)); }
}
