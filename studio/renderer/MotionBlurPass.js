import * as THREE from 'three';
import { Pass, FullScreenQuad } from 'three/addons/postprocessing/Pass.js';

/**
 * Accumulation motion blur.
 *
 * Each frame is blended into a persistent history buffer, and the history is what gets
 * displayed. Because the blend operates on successive real renders, the smear is produced by
 * whatever actually moved between them — it is not a directional blur painted on afterwards,
 * and it cannot invent motion that did not happen. A perfectly still scene produces identical
 * frames, so the blend is a no-op and the image stays sharp with no extra cost in fidelity.
 *
 * This is the accumulation approach rather than a velocity-buffer one. Velocity buffers give
 * shutter-accurate symmetric blur; accumulation gives a trail behind the motion, which is what a
 * long exposure actually looks like. It needs no per-object previous-matrix bookkeeping and
 * cannot desynchronise from the scene, which matters here because geometry is rebuilt whenever
 * depth changes.
 *
 * The history must be RGBA/HalfFloat: at 8 bits, repeatedly blending 0.85*old + 0.15*new
 * quantises the tail and the trail bands into visible steps.
 */
export class MotionBlurPass extends Pass {
  constructor(width, height) {
    super();
    this.amount = 0.0;
    this.needsSwap = true;

    const opts = { type: THREE.HalfFloatType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter };
    this.history = new THREE.WebGLRenderTarget(width, height, opts);
    this._primed = false;

    this.blendMat = new THREE.ShaderMaterial({
      uniforms: { tNew: { value: null }, tOld: { value: null }, uMix: { value: 0.0 } },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: `
        uniform sampler2D tNew, tOld; uniform float uMix; varying vec2 vUv;
        void main(){
          vec4 n = texture2D(tNew, vUv);
          vec4 o = texture2D(tOld, vUv);
          gl_FragColor = mix(n, o, uMix);
        }`,
      depthTest: false, depthWrite: false,
    });
    this.copyMat = new THREE.ShaderMaterial({
      uniforms: { tDiffuse: { value: null } },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: `uniform sampler2D tDiffuse; varying vec2 vUv;
        void main(){ gl_FragColor = texture2D(tDiffuse, vUv); }`,
      depthTest: false, depthWrite: false,
    });
    this._quad = new FullScreenQuad(this.blendMat);
  }

  setSize(w, h) {
    this.history.setSize(w, h);
    this._primed = false;    // the old contents are the wrong size; do not blend against them
  }

  render(renderer, writeBuffer, readBuffer) {
    // Cap well below 1.0: at 0.97+ the tail effectively never decays and the frame smears into
    // permanent ghosting that reads as a broken renderer rather than as exposure.
    const mix = Math.min(0.92, this.amount);

    if (!this._primed) {
      // Seed the history with the current frame instead of black, or the first blurred frames
      // fade up from nothing.
      this._quad.material = this.copyMat;
      this.copyMat.uniforms.tDiffuse.value = readBuffer.texture;
      renderer.setRenderTarget(this.history);
      this._quad.render(renderer);
      this._primed = true;
    }

    this._quad.material = this.blendMat;
    this.blendMat.uniforms.tNew.value = readBuffer.texture;
    this.blendMat.uniforms.tOld.value = this.history.texture;
    this.blendMat.uniforms.uMix.value = mix;

    // Blend into the write buffer, then copy that result back into history. Writing straight
    // into history while sampling it is a feedback loop on the same texture — undefined in
    // WebGL and it produces garbage on some drivers rather than failing loudly.
    renderer.setRenderTarget(writeBuffer);
    this._quad.render(renderer);

    this._quad.material = this.copyMat;
    this.copyMat.uniforms.tDiffuse.value = writeBuffer.texture;
    renderer.setRenderTarget(this.history);
    this._quad.render(renderer);

    renderer.setRenderTarget(null);
  }

  dispose() {
    this.history.dispose();
    this.blendMat.dispose();
    this.copyMat.dispose();
    this._quad.dispose();
  }
}
