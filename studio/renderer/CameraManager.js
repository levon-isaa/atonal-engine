import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const PRESETS = {
  front:       [0, 0, 1], back: [0, 0, -1], left: [-1, 0, 0], right: [1, 0, 0],
  top:         [0, 1, 0.0001],                       // exact +Y is degenerate against up=+Y
  isometric:   [1, 0.85, 1], perspective: [0.9, 0.55, 1.35],
};

export class CameraManager {
  constructor(canvas, aspect) {
    this.perspective = new THREE.PerspectiveCamera(45, aspect, 0.1, 200);
    // Ortho frustum is derived from distance/fov in sync(), so switching modes holds framing
    // instead of jumping — the two projections otherwise have no shared notion of "zoom".
    this.ortho = new THREE.OrthographicCamera(-1, 1, 1, -1, -100, 200);
    this.active = this.perspective;
    this.distance = 4;
    this.dir = new THREE.Vector3(...PRESETS.perspective).normalize();

    this.controls = new OrbitControls(this.active, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.enablePan = true;
    this.controls.minDistance = 0.6;
    this.controls.maxDistance = 40;
    this._applyPosition();
  }

  apply(c, aspect) {
    const wantOrtho = c.type === 'orthographic';
    const next = wantOrtho ? this.ortho : this.perspective;
    if (next !== this.active) {
      // Carry the orbit state across rather than resetting: the user's viewpoint is theirs, and
      // a mode toggle that snaps back to default reads as a bug.
      next.position.copy(this.active.position);
      this.active = next;
      this.controls.object = next;
    }
    this.perspective.fov = c.fov;
    this.distance = c.distance;
    this.sync(aspect);
  }

  setPreset(name) {
    const p = PRESETS[name]; if (!p) return;
    this.dir.set(...p).normalize();
    this.controls.target.set(0, 0, 0);
    this._applyPosition();
  }

  _applyPosition() {
    this.active.position.copy(this.dir).multiplyScalar(this.distance).add(this.controls?.target ?? new THREE.Vector3());
    this.active.lookAt(this.controls?.target ?? new THREE.Vector3());
  }

  /** Fit the current object so it fills a consistent share of frame regardless of its aspect. */
  frame(sphere) {
    if (!sphere || !isFinite(sphere.radius) || sphere.radius <= 0) return;
    const fov = THREE.MathUtils.degToRad(this.perspective.fov);
    this.distance = (sphere.radius / Math.sin(fov / 2)) * 1.15;
    this.controls.target.copy(sphere.center);
    this._applyPosition();
    this.controls.update();
    return this.distance;
  }

  sync(aspect) {
    this.perspective.aspect = aspect;
    this.perspective.updateProjectionMatrix();
    // Match the ortho box to what the perspective camera sees at the orbit distance, so the two
    // modes are the same size on screen.
    const d = this.active.position.distanceTo(this.controls.target) || this.distance;
    const h = Math.tan(THREE.MathUtils.degToRad(this.perspective.fov) / 2) * d;
    this.ortho.top = h; this.ortho.bottom = -h;
    this.ortho.left = -h * aspect; this.ortho.right = h * aspect;
    this.ortho.updateProjectionMatrix();
  }

  update() { this.controls.update(); }
  dispose() { this.controls.dispose(); }
}
