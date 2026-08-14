import * as THREE from 'three';

/**
 * SceneManager — owns the scene graph root and the backdrop.
 *
 * The backdrop is deliberately NOT scene.background for the gradient and image cases: a plain
 * scene.background is drawn before everything with no depth and cannot be blurred, tinted per
 * corner, or made partially transparent for export. A camera-locked mesh behind everything can,
 * and it costs one quad.
 */
export class SceneManager {
  constructor() {
    this.scene = new THREE.Scene();
    this.root = new THREE.Group();          // everything animatable hangs off this
    this.scene.add(this.root);

    this._bgGeom = new THREE.PlaneGeometry(2, 2);
    this._bgMat = new THREE.ShaderMaterial({
      depthTest: false, depthWrite: false,
      uniforms: { uA: { value: new THREE.Color('#111111') }, uB: { value: new THREE.Color('#2a2a33') }, uMode: { value: 0 }, uOpacity: { value: 1 } },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: `
        uniform vec3 uA, uB; uniform float uMode, uOpacity; varying vec2 vUv;
        void main(){
          vec3 c = (uMode > 0.5) ? mix(uA, uB, smoothstep(0.0, 1.0, vUv.y)) : uA;
          gl_FragColor = vec4(c, uOpacity);
        }`,
    });
    this.bg = new THREE.Mesh(this._bgGeom, this._bgMat);
    this.bg.frustumCulled = false;
    this.bg.renderOrder = -1000;
    this.scene.add(this.bg);
  }

  /**
   * @returns {boolean} whether the renderer's clear should be transparent
   */
  applyBackground(b) {
    const u = this._bgMat.uniforms;
    if (b.type === 'transparent') { this.bg.visible = false; this.scene.background = null; return true; }
    if (b.type === 'environment') { this.bg.visible = false; this.scene.background = this.scene.environment; return false; }
    this.bg.visible = true;
    this.scene.background = null;
    u.uA.value.set(b.color);
    u.uB.value.set(b.colorB || b.color);
    u.uMode.value = b.type === 'gradient' ? 1 : 0;
    u.uOpacity.value = b.opacity ?? 1;
    return (b.opacity ?? 1) < 1;
  }

  add(o) { this.root.add(o); }

  dispose() {
    this._bgGeom.dispose();
    this._bgMat.dispose();
  }
}
