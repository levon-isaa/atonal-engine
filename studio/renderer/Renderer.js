import * as THREE from 'three';
import { SceneManager } from './SceneManager.js';
import { CameraManager } from './CameraManager.js';
import { GeometryManager } from './GeometryManager.js';
import { MaterialManager } from './MaterialManager.js';
import { LightingManager } from './LightingManager.js';
import { AnimationManager } from './AnimationManager.js';
import { PostProcessingManager } from './PostProcessingManager.js';
import { AudioManager } from './AudioManager.js';

/**
 * Renderer — owns the WebGL context, the frame loop, and the wiring from store to managers.
 *
 * This is the only file that runs per frame. The UI never calls into the loop; it writes to the
 * store, the store notifies one section, and the matching manager mutates its own objects. That
 * is why a slider drag never rebuilds the scene: nothing here is constructed after start().
 */
export class Renderer {
  constructor(canvas, store) {
    this.canvas = canvas;
    this.store = store;

    this.gl = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: true,
      preserveDrawingBuffer: false,   // export re-renders at size instead; see ExportManager
      powerPreference: 'high-performance',
    });
    this.gl.setClearColor(0x000000, 0);
    this.gl.shadowMap.enabled = true;
    this.gl.shadowMap.type = THREE.PCFSoftShadowMap;
    this.gl.toneMapping = THREE.ACESFilmicToneMapping;
    this.gl.toneMappingExposure = 1.0;

    const rect = canvas.getBoundingClientRect();
    const aspect = Math.max(1e-3, rect.width / Math.max(1, rect.height));

    this.sceneMgr = new SceneManager();
    this.cameraMgr = new CameraManager(canvas, aspect);
    this.materialMgr = new MaterialManager();
    this.geometryMgr = new GeometryManager(this.sceneMgr.root);
    this.lightingMgr = new LightingManager(this.sceneMgr.scene, this.gl);
    this.animationMgr = new AnimationManager(this.geometryMgr.group);
    this.post = new PostProcessingManager(this.gl, this.sceneMgr.scene, this.cameraMgr.active,
                                          { w: rect.width, h: rect.height });

    this.audio = new AudioManager();

    this.geometryMgr.onRebuild = (sphere) => {
      if (this.store.get('camera').autoFrame) {
        const d = this.cameraMgr.frame(sphere);
        this.store.set('camera', { distance: +d.toFixed(2) });
      }
    };

    this._clock = new THREE.Clock();
    this._raf = 0;
    this._frameMs = 16;
    this._pr = 1;
    this._running = false;

    this._bindStore();
    this._applyAll();
    this._onResize = () => this.resize();
    addEventListener('resize', this._onResize);
    this.resize();
  }

  /** One subscription per section — the whole point of the store's shape. */
  _bindStore() {
    const s = this.store;
    s.on('geometry',   (g) => { this.geometryMgr.build(g, this.materialMgr.material);
                                this.materialMgr.setFlatShading(g.flatShading); });
    s.on('material',   (m) => { if (m.__preset) { this.materialMgr.applyPreset(m.type); m.__preset = false; }
                                this.materialMgr.apply(m); });
    s.on('lighting',   (l) => { this.lightingMgr.apply(l);
                                this.materialMgr.setEnvIntensity(l.envIntensity); });
    s.on('camera',     (c) => { this.cameraMgr.apply(c, this._aspect());
                                this.post.setCamera(this.cameraMgr.active); });
    s.on('animation',  (a) => this.animationMgr.apply(a));
    s.on('effects',    (e) => { this.post.apply(e); this.gl.toneMappingExposure = e.exposure; });
    s.on('background', (b) => { const transparent = this.sceneMgr.applyBackground(b);
                                this.gl.setClearAlpha(transparent ? 0 : 1); });
  }

  _applyAll() {
    const st = this.store.state;
    this.lightingMgr.setEnvironment(st.lighting.environment, st.lighting.envIntensity);
    this.lightingMgr.apply(st.lighting);
    this.materialMgr.applyPreset(st.material.type);
    this.materialMgr.apply(st.material);
    this.materialMgr.setEnvIntensity(st.lighting.envIntensity);
    this.cameraMgr.apply(st.camera, this._aspect());
    this.animationMgr.apply(st.animation);
    this.post.apply(st.effects);
    const transparent = this.sceneMgr.applyBackground(st.background);
    this.gl.setClearAlpha(transparent ? 0 : 1);
  }

  loadSVG(text) {
    const n = this.geometryMgr.loadSVG(text);
    this.geometryMgr.build(this.store.get('geometry'), this.materialMgr.material);
    return n;
  }

  _aspect() {
    const r = this.canvas.getBoundingClientRect();
    return Math.max(1e-3, r.width / Math.max(1, r.height));
  }

  resize() {
    const r = this.canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
    const q = this.store.get('quality');
    this._pr = Math.min(devicePixelRatio || 1, q.maxPixelRatio);
    this.gl.setPixelRatio(this._pr);
    this.gl.setSize(w, h, false);
    this.post.setSize(w, h, this._pr);
    this.cameraMgr.sync(w / h);
    this.sceneMgr.setViewportAspect(w / h);
  }

  start() {
    if (this._running) return;
    this._running = true;
    const tick = () => {
      this._raf = requestAnimationFrame(tick);
      const dt = Math.min(0.05, this._clock.getDelta());
      // Adaptive pixel ratio: quality is traded before framerate, and only between bounds so it
      // can never collapse to a blur. Measured against frame time, not a fixed device guess.
      const q = this.store.get('quality');
      if (q.adaptive) {
        this._frameMs += ((dt * 1000) - this._frameMs) * 0.05;
        const cap = Math.min(devicePixelRatio || 1, q.maxPixelRatio);
        if (this._frameMs > 26 && this._pr > cap * 0.55) this._setPR(this._pr - 0.08);
        else if (this._frameMs < 15 && this._pr < cap) this._setPR(Math.min(cap, this._pr + 0.06));
      }
      this.audio.update(dt);
      this._react(dt);
      this.animationMgr.update(dt);
      this.cameraMgr.update();
      this.post.render(dt);
    };
    tick();
  }

  /**
   * Music reactivity, applied to the LIVE objects each frame rather than written back into the
   * store. Writing to the store 60 times a second would fire every listener and rebuild geometry
   * on any parameter that touches it — the store holds the user's intent, and this is a
   * modulation on top of it, so the two must not share a channel.
   */
  _react(dt) {
    const a = this.audio, r = this.store.get('reactive');
    if (!r.enabled || !a.director || !a.playing) {
      // ease back to the unmodulated values so switching off does not leave the scene bent
      if (this._reacted) {
        this.animationMgr.cfg.speed = this.store.get('animation').speed;
        this.geometryMgr.group.scale.setScalar(1);
        this.post.apply(this.store.get('effects'));
        this._reacted = false;
      }
      return;
    }
    this._reacted = true;
    const st = this.store.state;

    // TEMPO -> spin. One revolution per 32 beats (8 bars), the same musical ratio the viewer
    // locks to, so a faster track visibly turns faster instead of just pulsing harder.
    if (r.tempoSpin) {
      this.animationMgr.cfg.speed = (a.bpm / 60) / 32 * r.spinScale;
    }

    // LOW-BAND transient -> scale punch. Radial, so it does not fight the rotation.
    const punch = 1 + (a.ENV.low * 0.09 + a.ENV.sub * 0.07) * r.punch;
    this.geometryMgr.group.scale.setScalar(punch);

    // ENERGY / AIR -> bloom lift; the shimmer belongs to the highs
    const e = st.effects;
    const energy = a.curve('energy'), air = a.ENV.air;
    // Coefficients kept low deliberately. At 0.35/0.5 a loud section drove bloom from 0.22 to
    // 0.81 and the form washed out to the same flat white the static defaults were calibrated
    // away from — the reaction has to be legible without undoing the grade it sits on.
    this.post.bloom.strength = e.bloom + (energy * 0.14 + air * 0.22) * r.bloom;
    this.post.bloom.enabled = this.post.bloom.strength > 0.001;

    // SECTION MOOD -> material colour, eased so a section change is a fade not a cut
    if (r.moodColor) {
      const sec = a.section();
      if (sec?.palette) {
        const c = this.materialMgr.material.color;
        c.r += (sec.palette[0] - c.r) * Math.min(1, dt * 1.5);
        c.g += (sec.palette[1] - c.g) * Math.min(1, dt * 1.5);
        c.b += (sec.palette[2] - c.b) * Math.min(1, dt * 1.5);
      }
    }
  }

  _setPR(pr) {
    this._pr = pr;
    const r = this.canvas.getBoundingClientRect();
    this.gl.setPixelRatio(pr);
    this.post.setSize(Math.round(r.width), Math.round(r.height), pr);
  }

  stop() { cancelAnimationFrame(this._raf); this._running = false; }

  dispose() {
    this.stop();
    removeEventListener('resize', this._onResize);
    this.geometryMgr.dispose();
    this.materialMgr.dispose();
    this.lightingMgr.dispose();
    this.post.dispose();
    this.cameraMgr.dispose();
    this.sceneMgr.dispose();
    this.gl.dispose();
  }
}
