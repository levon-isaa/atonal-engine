#!/usr/bin/env python3
"""
Local Director server.
POST an audio file -> runs the understanding pipeline -> returns director.json.

  POST /analyze     body = raw audio bytes (X-Filename header optional)
  GET  /health      -> {"ok":true, "panns":bool}

CORS is open so the browser renderer (any origin, incl. file://) can call it.
Run:  python server.py   (default http://127.0.0.1:8770)
"""
import os, json, tempfile, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import analyze, tagger

PORT = int(os.environ.get("ATONAL_PORT", "8770"))

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
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0:
                return self._json(400, {"error": "empty body"})
            data = self.rfile.read(n)
            name = self.headers.get("X-Filename", "upload.mp3")
            ext = os.path.splitext(name)[1] or ".mp3"
            tmp = tempfile.mktemp(suffix=ext)
            with open(tmp, "wb") as fp: fp.write(data)
            print(f"[analyze] {name} ({n/1024:.0f} KB)")
            d = analyze.build_director(tmp)
            os.remove(tmp)
            self._json(200, d)
            print(f"[done] {name}: {len(d['sections'])} sections, "
                  f"genre={d['genre']['primary']} bpm={d['tempo']['bpm']}")
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"error": str(e)})

    def log_message(self, *a):  # quieter
        pass

if __name__ == "__main__":
    print(f"ATONAL Director server on http://127.0.0.1:{PORT}   (PANNs: {tagger.available()})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
