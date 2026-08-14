import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { BokehPass } from 'three/addons/postprocessing/BokehPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

/**
 * Grade pass — vignette, grain, chromatic aberration, exposure/contrast/saturation in one
 * fragment shader.
 *
 * One pass rather than five: each ShaderPass is a full-screen read plus a write to another
 * target, so five of them cost five round trips through memory for arithmetic that fits in a
 * handful of instructions. Bloom and DOF stay separate because they genuinely need their own
 * multi-tap sampling.
 */
const GradeShader = {
  uniforms: {
    tDiffuse:  { value: null },
    uVignette: { value: 0.25 },
    uGrain:    { value: 0.05 },
    uChroma:   { value: 0.0 },
    uExposure: { value: 1.0 },
    uContrast: { value: 1.0 },
    uSaturation:{ value: 1.0 },
    uTime:     { value: 0 },
  },
  vertexShader: /* glsl */`
    varying vec2 vUv;
    void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }
  `,
  fragmentShader: /* glsl */`
    uniform sampler2D tDiffuse;
    uniform float uVignette, uGrain, uChroma, uExposure, uContrast, uSaturation, uTime;
    varying vec2 vUv;
    float hash(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233))) * 43758.5453); }
    void main(){
      vec2 d = vUv - 0.5;
      vec3 col;
      if (uChroma > 0.0001) {
        // scale the split with radius: a uniform offset looks like a misaligned texture, a
        // radial one looks like a lens
        float k = uChroma * 0.006 * (0.25 + dot(d,d) * 3.0);
        col.r = texture2D(tDiffuse, vUv + d * k).r;
        col.g = texture2D(tDiffuse, vUv).g;
        col.b = texture2D(tDiffuse, vUv - d * k).b;
      } else {
        col = texture2D(tDiffuse, vUv).rgb;
      }
      col *= uExposure;
      float l = dot(col, vec3(0.2126, 0.7152, 0.0722));
      col = mix(vec3(l), col, uSaturation);
      col = (col - 0.5) * uContrast + 0.5;
      col *= mix(1.0, smoothstep(1.25, 0.30, length(d) * 1.15), uVignette);
      // triangular noise (difference of two hashes): flat uniform grain sits as a static film
      // over the frame, this one dissolves banding as well
      float n = hash(gl_FragCoord.xy + fract(uTime) * 37.0) - hash(gl_FragCoord.xy * 1.7 + fract(uTime) * 91.0);
      col += n * uGrain * 0.12;
      gl_FragColor = vec4(max(col, 0.0), 1.0);
    }
  `,
};

export class PostProcessingManager {
  constructor(renderer, scene, camera, size) {
    this.renderer = renderer;
    this.composer = new EffectComposer(renderer);
    this.renderPass = new RenderPass(scene, camera);
    this.bloom = new UnrealBloomPass(new THREE.Vector2(size.w, size.h), 0.3, 0.4, 0.85);
    this.bokeh = new BokehPass(scene, camera, { focus: 4.0, aperture: 0.0002, maxblur: 0.01 });
    this.grade = new ShaderPass(GradeShader);
    this.output = new OutputPass();      // tone map + sRGB, must be last

    this.composer.addPass(this.renderPass);
    this.composer.addPass(this.bloom);
    this.composer.addPass(this.bokeh);
    this.composer.addPass(this.grade);
    this.composer.addPass(this.output);
    this.bokeh.enabled = false;
    this.bloom.enabled = true;
  }

  setCamera(cam) { this.renderPass.camera = cam; this.bokeh.camera = cam; }

  apply(e) {
    this.bloom.strength = e.bloom;
    this.bloom.threshold = e.bloomThreshold;
    this.bloom.radius = e.bloomRadius;
    // Disabling rather than running at zero: a pass at strength 0 still costs its full
    // downsample chain every frame for a result that is guaranteed to be black.
    this.bloom.enabled = e.bloom > 0.001;

    this.bokeh.enabled = e.depthOfField > 0.001;
    if (this.bokeh.enabled) {
      const u = this.bokeh.materialBokeh.uniforms;
      u.focus.value = e.focus;
      u.aperture.value = e.aperture * e.depthOfField * 0.02;
      u.maxblur.value = 0.01 * e.depthOfField;
    }

    const g = this.grade.uniforms;
    g.uVignette.value = e.vignette;
    g.uGrain.value = e.grain;
    g.uChroma.value = e.chromatic;
    g.uExposure.value = e.exposure;
    g.uContrast.value = e.contrast;
    g.uSaturation.value = e.saturation;
  }

  setSize(w, h, pr) {
    this.composer.setSize(w, h);
    this.composer.setPixelRatio(pr);
    this.bloom.setSize(w, h);
  }

  render(dt) {
    this.grade.uniforms.uTime.value += dt;
    this.composer.render(dt);
  }

  dispose() {
    this.composer.dispose?.();
    this.bloom.dispose?.();
    this.bokeh.dispose?.();
    this.grade.dispose?.();
  }
}
