#!/usr/bin/env python3
"""
Local Director server.
POST an audio file -> runs the understanding pipeline -> returns director.json.

  POST /analyze     body = raw audio bytes (X-Filename header optional)
  GET  /health      -> {"ok":true, "panns":bool}

CORS is open so the browser renderer (any origin, incl. file://) can call it.
Run:  python server.py   (default http://127.0.0.1:8770)
"""
import os, json, tempfile, traceback, hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import analyze, tagger

PORT = int(os.environ.get("ATONAL_PORT", "8770"))
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(HERE, "out", "cache")
# A whole track is read into memory before analysis, so this is the real ceiling on a request.
# 300MB covers a long lossless file with room to spare; without a cap a bad or hostile request
# can drive the process into swap.
MAX_UPLOAD = 300 * 1024 * 1024


def cache_path(digest):
    return os.path.join(CACHE_DIR, digest + ".json")


def cache_get(digest):
    """Return a previously computed director for these exact bytes, or None."""
    fp = cache_path(digest)
    if not os.path.exists(fp):
        return None
    try:
        with open(fp, "r") as fh:
            return json.load(fh)
    except Exception:
        # a truncated or corrupt entry must never be fatal — just recompute over it
        return None


def cache_put(digest, director):
    """Write atomically: a reader must never observe a half-written entry."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = cache_path(digest) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(director, fh)
        os.replace(tmp, cache_path(digest))
    except Exception:
        traceback.print_exc()   # caching is best-effort; never fail the request over it

class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "panns": tagger.available()})
            return
        # serve the viewer so everything is same-origin (open http://127.0.0.1:PORT/)
        path = self.path.split("?")[0]
        fname = "viewer.html" if path in ("/", "/viewer.html") else None
        if fname:
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
            if os.path.exists(fp):
                body = open(fp, "rb").read()
                self.send_response(200); self._cors()
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body); return
        self._json(200, {"service": "atonal-director", "post": "/analyze"})

    def do_POST(self):
        if not self.path.startswith("/analyze"):
            return self._json(404, {"error": "not found"})
        tmp = None
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0:
                return self._json(400, {"error": "empty body"})
            if n > MAX_UPLOAD:
                return self._json(413, {"error": f"file too large ({n/1048576:.0f}MB, max "
                                                 f"{MAX_UPLOAD//1048576}MB)"})
            data = self.rfile.read(n)
            name = self.headers.get("X-Filename", "upload.mp3")

            # Cache on the CONTENT, not the filename: the same track renamed is the same
            # analysis, and a different track under a reused name is not. Analysis is ~8s warm
            # and 60-90s cold, all of it deterministic, so a repeat load has no reason to pay it.
            digest = hashlib.sha256(data).hexdigest()
            hit = cache_get(digest)
            if hit is not None:
                print(f"[cache] {name} -> {digest[:12]}")
                return self._json(200, hit)

            ext = os.path.splitext(name)[1] or ".mp3"
            # mkstemp, not mktemp: mktemp only invents a name, leaving a window in which anything
            # else can create that path first. mkstemp creates the file atomically and hands back
            # an open descriptor.
            fd, tmp = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as fp:
                fp.write(data)
            print(f"[analyze] {name} ({n/1024:.0f} KB)")
            d = analyze.build_director(tmp)
            cache_put(digest, d)
            self._json(200, d)
            print(f"[done] {name}: {len(d['sections'])} sections, "
                  f"genre={d['genre']['primary']} bpm={d['tempo']['bpm']}")
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": str(e)})
        finally:
            # The old code removed the temp file only on the success path, so every failed
            # analysis leaked a full copy of the track into /tmp.
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def log_message(self, *a):  # quieter
        pass

if __name__ == "__main__":
    print(f"ATONAL Director server on http://127.0.0.1:{PORT}   (PANNs: {tagger.available()})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
