import * as THREE from 'three';

/**
 * MaterialManager — one MeshPhysicalMaterial, mutated in place.
 *
 * Presets seed the parameters; they are not a separate material each. Swapping the material
 * object on every preset change would drop the compiled program and re-upload uniforms, which
 * is a visible hitch on a slider. Mutating and setting needsUpdate only when a define actually
 * changes keeps preset switching free.
 */

export const PRESETS = {
  chrome:    { color: '#ffffff', metalness: 1.00, roughness: 0.05, transmission: 0, clearcoat: 0.0, reflectivity: 1.0, opacity: 1 },
  glass:     { color: '#ffffff', metalness: 0.00, roughness: 0.03, transmission: 1.0, clearcoat: 1.0, reflectivity: 0.5, opacity: 1, ior: 1.5 },
  plastic:   { color: '#e8e8ee', metalness: 0.00, roughness: 0.35, transmission: 0, clearcoat: 0.6, reflectivity: 0.5, opacity: 1 },
  matte:     { color: '#d8d8dc', metalness: 0.00, roughness: 0.95, transmission: 0, clearcoat: 0.0, reflectivity: 0.2, opacity: 1 },
  metallic:  { color: '#b9bcc4', metalness: 1.00, roughness: 0.32, transmission: 0, clearcoat: 0.0, reflectivity: 0.8, opacity: 1 },
  glossy:    { color: '#ffffff', metalness: 0.10, roughness: 0.08, transmission: 0, clearcoat: 1.0, reflectivity: 0.9, opacity: 1 },
  gold:      { color: '#ffc75a', metalness: 1.00, roughness: 0.18, transmission: 0, clearcoat: 0.0, reflectivity: 1.0, opacity: 1 },
  silver:    { color: '#e6e9ef', metalness: 1.00, roughness: 0.12, transmission: 0, clearcoat: 0.0, reflectivity: 1.0, opacity: 1 },
  black:     { color: '#0d0d10', metalness: 0.30, roughness: 0.22, transmission: 0, clearcoat: 0.8, reflectivity: 0.6, opacity: 1 },
  white:     { color: '#f6f6f8', metalness: 0.00, roughness: 0.42, transmission: 0, clearcoat: 0.2, reflectivity: 0.4, opacity: 1 },
};

export class MaterialManager {
  constructor() {
    this.material = new THREE.MeshPhysicalMaterial({
      color: 0xffffff, metalness: 1, roughness: 0.15,
      clearcoat: 0, clearcoatRoughness: 0.1, transmission: 0, ior: 1.5,
      side: THREE.DoubleSide,       // extruded caps can face away on self-intersecting paths
      envMapIntensity: 1.0,
    });
    this._flat = false;
  }

  /** Apply a preset by name, returning the parameter patch so the UI can follow. */
  applyPreset(name) {
    const p = PRESETS[name];
    if (!p) return null;
    this.apply({ ...p, type: name });
    return p;
  }

  apply(m) {
    const mat = this.material;
    if (m.color !== undefined) mat.color.set(m.color);
    if (m.roughness !== undefined) mat.roughness = m.roughness;
    if (m.metalness !== undefined) mat.metalness = m.metalness;
    if (m.clearcoat !== undefined) mat.clearcoat = m.clearcoat;
    if (m.clearcoatRoughness !== undefined) mat.clearcoatRoughness = m.clearcoatRoughness;
    if (m.reflectivity !== undefined) mat.reflectivity = m.reflectivity;
    if (m.ior !== undefined) mat.ior = m.ior;

    // transmission and opacity both need `transparent`, and toggling that flips a shader define
    // — so only touch it when the boolean actually changes, or every frame of a slider drag
    // recompiles the program.
    if (m.transmission !== undefined) mat.transmission = m.transmission;
    if (m.opacity !== undefined) mat.opacity = m.opacity;
    const wantTransparent = (mat.opacity < 1) || (mat.transmission > 0);
    if (mat.transparent !== wantTransparent) {
      mat.transparent = wantTransparent;
      mat.needsUpdate = true;
    }
    return mat;
  }

  setFlatShading(flat) {
    if (this._flat === flat) return;
    this._flat = flat;
    this.material.flatShading = flat;
    this.material.needsUpdate = true;   // flatShading is a define, so this one must recompile
  }

  setEnvIntensity(v) { this.material.envMapIntensity = v; }

  dispose() { this.material.dispose(); }
}
