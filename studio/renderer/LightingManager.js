import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

/**
 * LightingManager — image-based environment plus a three-point rig.
 *
 * The environment does most of the work: a metal with no env map has nothing to reflect and
 * reads as flat paint however many point lights are aimed at it. RoomEnvironment is generated
 * on the GPU rather than loaded, so there is no HDR file to ship and no download to wait on.
 */
export class LightingManager {
  constructor(scene, renderer) {
    this.scene = scene;
    this.renderer = renderer;
    this._pmrem = new THREE.PMREMGenerator(renderer);
    this._envRT = null;

    this.key = new THREE.DirectionalLight(0xffffff, 3.0);
    this.key.castShadow = true;
    // Shadow camera is fitted to the normalised object (~2 units), not left at the default 5:
    // an oversized frustum spreads the same map over more area and the contact edge goes soft
    // and blocky.
    const s = 2.2;
    this.key.shadow.camera.left = -s; this.key.shadow.camera.right = s;
    this.key.shadow.camera.top = s;   this.key.shadow.camera.bottom = -s;
    this.key.shadow.camera.near = 0.1; this.key.shadow.camera.far = 20;
    this.key.shadow.mapSize.set(1024, 1024);
    this.key.shadow.bias = -0.0016;      // kills acne on the bevel's shallow-angle faces
    this.key.shadow.radius = 4;

    this.fill = new THREE.DirectionalLight(0x9fb8ff, 0.6);
    this.rim  = new THREE.DirectionalLight(0xffd9b0, 1.2);
    this.ambient = new THREE.AmbientLight(0xffffff, 0.25);

    scene.add(this.key, this.key.target, this.fill, this.rim, this.ambient);

    // A ground plane exists only to catch the shadow. ShadowMaterial draws nothing but the
    // shadow itself, so the backdrop stays whatever the background system decides.
    this.shadowPlane = new THREE.Mesh(
      new THREE.PlaneGeometry(40, 40),
      new THREE.ShadowMaterial({ opacity: 0.35 })
    );
    this.shadowPlane.rotation.x = -Math.PI / 2;
    this.shadowPlane.position.y = -1.35;
    this.shadowPlane.receiveShadow = true;
    scene.add(this.shadowPlane);
  }

  setEnvironment(name, intensity = 1.0) {
    this._disposeEnv();
    if (name === 'none') { this.scene.environment = null; return; }
    const env = new RoomEnvironment();
    this._envRT = this._pmrem.fromScene(env, 0.04);
    this.scene.environment = this._envRT.texture;
    this.scene.environmentIntensity = intensity;
    env.dispose?.();
  }

  apply(l) {
    this.key.intensity = l.keyIntensity;
    this.fill.intensity = l.fillIntensity;
    this.rim.intensity = l.rimIntensity;
    this.ambient.intensity = l.ambient;

    // Spherical placement so azimuth/elevation are the controls, not raw xyz — moving a studio
    // light is an angle, not a coordinate.
    const r = 6;
    const az = l.keyAzimuth * Math.PI * 2, el = l.keyElevation;
    this.key.position.set(r * Math.cos(az) * Math.cos(el), r * Math.sin(el), r * Math.sin(az) * Math.cos(el));
    this.fill.position.set(-r * 0.7, r * 0.3, r * 0.6);
    this.rim.position.set(0, r * 0.4, -r);

    this.key.castShadow = !!l.shadows;
    this.shadowPlane.material.opacity = l.shadowIntensity * 0.45;
    this.shadowPlane.visible = !!l.shadows && l.shadowIntensity > 0.001;
    this.key.shadow.radius = 1 + l.keySoftness * 8;

    if (this.scene.environment) this.scene.environmentIntensity = l.envIntensity;
    this.scene.environmentRotation?.set(0, l.envRotation * Math.PI * 2, 0);
  }

  _disposeEnv() {
    if (this._envRT) { this._envRT.dispose(); this._envRT = null; }
  }

  dispose() {
    this._disposeEnv();
    this._pmrem.dispose();
    this.shadowPlane.geometry.dispose();
    this.shadowPlane.material.dispose();
  }
}
