import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

/**
 * ExportManager — writes out what the scene actually is, not what the canvas happens to hold.
 *
 * Images are produced by re-rendering the whole composer chain at the target size, so a 4x PNG
 * has 4x the real detail: geometry is re-rasterised, bloom is resolved at the larger size, and
 * the grade runs over the larger buffer. Upscaling the canvas would just be a blurry screenshot
 * with the post-processing baked in at the wrong scale.
 */
export class ExportManager {
  constructor(renderer) { this.r = renderer; }

  _save(blob, name) {
    const url = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  _stamp() {
    const d = new Date(), p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  /**
   * @param {object} o
   * @param {number} o.scale        multiplier over the editor canvas
   * @param {boolean} o.transparent force an alpha background regardless of the scene setting
   * @param {'png'|'jpeg'} o.format
   */
  async image({ scale = 2, transparent = false, format = 'png', quality = 0.95 } = {}) {
    const r = this.r;
    const rect = r.canvas.getBoundingClientRect();
    const w = Math.round(rect.width * scale), h = Math.round(rect.height * scale);

    // Save every piece of state this touches. An export that leaves the editor at a different
    // size or alpha is worse than no export at all.
    const prevPR = r.gl.getPixelRatio();
    const prevSize = new THREE.Vector2(); r.gl.getSize(prevSize);
    const prevAlpha = r.gl.getClearAlpha();
    const prevBgVisible = r.sceneMgr.bg.visible;

    try {
      if (transparent) { r.sceneMgr.bg.visible = false; r.gl.setClearAlpha(0); }
      r.gl.setPixelRatio(1);            // scale is already in w/h; PR on top would square it
      r.gl.setSize(w, h, false);
      r.post.setSize(w, h, 1);
      r.cameraMgr.sync(w / h);
      r.post.render(0);                 // dt 0 so the frame matches what is on screen

      const blob = await new Promise((res) => r.canvas.toBlob(res, `image/${format}`, quality));
      if (!blob) throw new Error('canvas.toBlob returned null');
      this._save(blob, `render-${this._stamp()}-${w}x${h}.${format === 'jpeg' ? 'jpg' : 'png'}`);
      return { w, h, bytes: blob.size };
    } finally {
      r.sceneMgr.bg.visible = prevBgVisible;
      r.gl.setClearAlpha(prevAlpha);
      r.gl.setPixelRatio(prevPR);
      r.gl.setSize(prevSize.x, prevSize.y, false);
      r.post.setSize(prevSize.x, prevSize.y, prevPR);
      r.cameraMgr.sync(prevSize.x / prevSize.y);
    }
  }

  /** GLB — geometry + materials, openable in any DCC tool or AR viewer. */
  async glb() {
    const exporter = new GLTFExporter();
    const src = this.r.geometryMgr.group;
    if (!src.children.length) throw new Error('Nothing to export — load an SVG first.');
    const buf = await exporter.parseAsync(src, { binary: true });
    this._save(new Blob([buf], { type: 'model/gltf-binary' }), `model-${this._stamp()}.glb`);
    return { bytes: buf.byteLength };
  }

  /**
   * Video via MediaRecorder on the live canvas. Codec support is probed rather than assumed —
   * Safari only recently accepted WebM and answers mp4 instead.
   */
  startVideo(onStop) {
    if (!window.MediaRecorder || !this.r.canvas.captureStream) throw new Error('Recording unsupported in this browser.');
    const want = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4'];
    const mime = want.find((m) => MediaRecorder.isTypeSupported(m));
    if (!mime) throw new Error('No supported video codec.');
    const stream = this.r.canvas.captureStream(60);
    const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 16_000_000 });
    const chunks = [];
    rec.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
    rec.onstop = () => {
      const ext = mime.includes('mp4') ? 'mp4' : 'webm';
      const blob = new Blob(chunks, { type: mime });
      this._save(blob, `render-${this._stamp()}.${ext}`);
      stream.getTracks().forEach((t) => t.stop());
      onStop?.({ bytes: blob.size, ext });
    };
    rec.start(1000);        // timeslices, so a crash still leaves usable footage
    this._rec = rec;
    return mime;
  }

  stopVideo() { this._rec?.stop(); this._rec = null; }
  get recording() { return this._rec?.state === 'recording'; }
}
