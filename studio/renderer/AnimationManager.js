/**
 * AnimationManager — drives the object's transform from a clock, nothing else.
 *
 * Every motion is a pure function of accumulated time rather than an increment on the current
 * value. That matters for two reasons: pausing and resuming does not jump, and export can
 * evaluate any moment without stepping the loop to get there.
 */
export class AnimationManager {
  constructor(group) {
    this.group = group;
    this.t = 0;
    this.cfg = { enabled: true, type: 'rotation', speed: 0.25, axis: 'Y', direction: 1, amplitude: 0.15 };
    this._base = { x: 0, y: 0, z: 0 };
  }

  apply(a) { Object.assign(this.cfg, a); if (!a.enabled) this._settle(); }

  /** dt in seconds. Called once per frame by Renderer, never by the UI. */
  update(dt) {
    const c = this.cfg;
    if (!c.enabled) return;
    this.t += dt * c.speed * c.direction;
    const g = this.group;
    const tau = Math.PI * 2;

    switch (c.type) {
      case 'rotation': {
        const a = this.t * tau;
        if (c.axis === 'X') g.rotation.x = a;
        else if (c.axis === 'Z') g.rotation.z = a;
        else g.rotation.y = a;
        break;
      }
      case 'rotationH': g.rotation.y = this.t * tau; break;
      case 'rotationV': g.rotation.x = this.t * tau; break;
      case 'float':
        g.position.y = Math.sin(this.t * tau) * c.amplitude;
        g.rotation.y = Math.sin(this.t * tau * 0.5) * 0.25;
        break;
      case 'pulse': {
        const s = 1 + Math.sin(this.t * tau) * c.amplitude * 0.5;
        g.scale.setScalar(s);
        break;
      }
      default: break;
    }
  }

  /** Return to rest when animation is switched off, so the object does not freeze mid-tilt. */
  _settle() {
    this.group.position.y = 0;
    this.group.scale.setScalar(1);
  }

  reset() { this.t = 0; this._settle(); this.group.rotation.set(0, 0, 0); }
}
