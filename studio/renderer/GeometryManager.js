import * as THREE from 'three';
import { SVGLoader } from 'three/addons/loaders/SVGLoader.js';

/**
 * GeometryManager — SVG source -> extruded 3D mesh.
 *
 * The parsed SVG is kept as shapes so depth/bevel stay live: re-extruding needs the shape list,
 * not the file, which is what "editable without re-uploading" requires.
 */
export class GeometryManager {
  constructor(scene) {
    this.scene = scene;
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this._shapes = null;      // THREE.Shape[] from the last SVG
    this._geoms = [];         // live geometries, owned here so they can be disposed
    this.mesh = null;
    this.onRebuild = null;    // fired with the bounding sphere so the camera can reframe
  }

  /** True once an SVG has been parsed and extrusion can run. */
  get hasSource() { return !!this._shapes && this._shapes.length > 0; }

  /**
   * Parse SVG text into shapes. Holes are the reason this uses SVGLoader's own
   * `createShapes` rather than walking subpaths by hand: it applies the fill rule, so a
   * counter inside a letter becomes a hole in the shape instead of a second solid island.
   */
  loadSVG(svgText) {
    const parsed = new SVGLoader().parse(svgText);
    const shapes = [];
    for (const path of parsed.paths) {
      // style.fill 'none' means a stroke-only path — nothing to extrude, and including it
      // would emit a degenerate shape that breaks the triangulator.
      const fill = path.userData?.style?.fill;
      if (fill === 'none') continue;
      for (const shape of SVGLoader.createShapes(path)) shapes.push(shape);
    }
    if (!shapes.length) throw new Error('No fillable paths found in this SVG.');
    this._shapes = shapes;
    return shapes.length;
  }

  /**
   * Build (or rebuild) the extrusion. Cheap enough to run on a slider drag for typical logos.
   * Existing geometry is disposed first — this is the main leak vector in an editor where the
   * user scrubs depth for a while.
   */
  build(g, material) {
    if (!this.hasSource) return null;
    this._disposeGeoms();
    while (this.group.children.length) this.group.remove(this.group.children[0]);

    // DEPTH MUST BE PRE-DIVIDED BY THE NORMALISATION FACTOR.
    // The shapes arrive in the SVG's own viewBox units — commonly 0..100, but just as often
    // 0..1024 — and the result is scaled to a fixed on-screen size afterwards. Extruding by the
    // raw depth first and scaling second divides the depth away by that same factor: a 100-unit
    // logo turned 0.5 into 0.011, a flat sheet, and the same slider would mean something
    // completely different for a 1024-unit one. Computing the factor from the 2D bounds FIRST
    // and extruding by depth/s makes the parameter absolute: what the slider says is what the
    // object measures, for any SVG.
    const s = this._normaliseFactor();
    const bevelEnabled = g.bevel > 0.001;
    const geoms = this._shapes.map((shape) => new THREE.ExtrudeGeometry(shape, {
      depth: g.depth / s,
      bevelEnabled,
      bevelThickness: g.bevel / s,
      bevelSize: (g.bevel * 0.75) / s,
      bevelSegments: bevelEnabled ? Math.max(1, g.bevelSegments | 0) : 0,
      curveSegments: Math.max(3, g.curveSegments | 0),
    }));

    // SVG's Y axis points down and its origin is the top-left of the document. Left alone the
    // logo renders upside down and orbits around a pivot off in the corner rather than its own
    // centre. Correct it with a ROTATION about X, not scale.y = -1: a negative scale has
    // determinant -1, so it inverts winding and turns every normal inward. With DoubleSide the
    // faces still draw and the form looks solid, but lighting and the environment reflection are
    // then evaluated against inward normals — which renders a mirror as flat unlit paint. A
    // rotation is determinant +1 and leaves the normals alone. It reverses the extrusion
    // direction too, which is invisible here because the result is recentred anyway.
    const mesh = new THREE.Mesh(geoms[0], material);
    const meshes = geoms.map((geo) => new THREE.Mesh(geo, material));
    const holder = new THREE.Group();
    for (const m of meshes) { m.castShadow = true; m.receiveShadow = true; holder.add(m); }
    holder.rotation.x = Math.PI;

    holder.scale.setScalar(s);          // uniform: any non-uniform scale would skew the normals
    // Recentre on the extruded bounds, measured AFTER the rotation and scale are on the holder,
    // so the pivot is the object's real centre and orbiting does not swing it around a corner.
    const box = new THREE.Box3().setFromObject(holder);
    const centre = new THREE.Vector3(); box.getCenter(centre);
    holder.position.sub(centre);

    this.group.add(holder);
    this._geoms = geoms;
    this.mesh = mesh;
    this.meshes = meshes;

    const sphere = new THREE.Sphere();
    new THREE.Box3().setFromObject(this.group).getBoundingSphere(sphere);
    this.onRebuild?.(sphere);
    return this.group;
  }

  /**
   * Scale that maps the SVG's own units onto a fixed ~2-unit object, derived from the 2D shape
   * bounds so it is known BEFORE extruding. getPoints(4) is a coarse sampling — plenty for a
   * bounding box, and far cheaper than tessellating every curve properly just to measure it.
   */
  _normaliseFactor() {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const shape of this._shapes) {
      for (const p of shape.getPoints(4)) {
        if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
        if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
      }
    }
    const w = maxX - minX, h = maxY - minY;
    const m = Math.max(w, h);
    return (isFinite(m) && m > 1e-6) ? 2.0 / m : 1.0;
  }

  setMaterial(material) {
    for (const m of (this.meshes || [])) m.material = material;
  }

  setFlatShading(/* handled by MaterialManager */) {}

  _disposeGeoms() {
    for (const g of this._geoms) g.dispose();
    this._geoms = [];
  }

  dispose() {
    this._disposeGeoms();
    this.scene.remove(this.group);
    this._shapes = null;
  }
}
