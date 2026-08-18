#!/usr/bin/env python3
"""
Local Director server.
POST an audio file -> runs the understanding pipeline -> returns director.json.

  POST /analyze     body = raw audio bytes (X-Filename header optional)
                    ?job=<id> registers the run so its progress can be polled
  GET  /progress    ?job=<id> -> {stage, p, next, eta} while that analysis runs
  GET  /health      -> {"ok":true, "panns":bool}

CORS is open so the browser renderer (any origin, incl. file://) can call it.
Run:  python server.py   (default http://127.0.0.1:8770)
"""
import os, json, time, socket, tempfile, traceback, threading, hashlib
from urllib.parse import urlparse, parse_qs, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import analyze, tagger

# One analysis at a time. librosa + a 300MB PANNs model are both CPU and memory heavy; two
# concurrent requests do not run twice as fast, they thrash and can exhaust memory.
_ANALYSIS = threading.Semaphore(1)

# Progress is reported on a SIDE CHANNEL rather than by streaming the response, so POST /analyze
# keeps its existing contract: one request, one director.json, no chunk parsing on the client.
# The analysis thread writes here and the polling GET reads it.
_PROGRESS = {}
_PLOCK = threading.Lock()
_PROGRESS_TTL = 1800        # a client that dies mid-analysis must not leak its entry forever

def _progress_set(job, d):
    if not job:
        return
    with _PLOCK:
        d["at"] = time.time()
        _PROGRESS[job] = d
        if len(_PROGRESS) > 64:                       # bound the dict even if TTL never fires
            for k in [k for k, v in _PROGRESS.items() if time.time() - v.get("at", 0) > _PROGRESS_TTL]:
                _PROGRESS.pop(k, None)

def _progress_clear(job):
    if job:
        with _PLOCK:
            _PROGRESS.pop(job, None)

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
            hit = json.load(fh)
    except Exception:
        # a truncated or corrupt entry must never be fatal — just recompute over it
        return None
    # The digest answers "same bytes?", not "same analysis?". An entry written by an older
    # pipeline is stale in exactly the way that is hardest to spot — it returns 200 with a
    # director that is missing whatever the contract has gained since. Recompute instead.
    if (hit.get("meta") or {}).get("analysis_version") != analyze.ANALYSIS_VERSION:
        return None
    return hit


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
        # Private Network Access. Chrome treats a request from a public or opaque origin (which
        # includes file://) to a loopback address as a private-network request, preflights it with
        # Access-Control-Request-Private-Network, and BLOCKS it unless the response opts in.
        # Without this, opening viewer.html by double-clicking it gives a page that loads fine and
        # an upload that fails as a bare network error — indistinguishable from the server being
        # down. Scoped to a loopback-only dev server, so it grants nothing that was not already
        # reachable from this machine.
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204); self._cors(); self.end_headers()

    def do_GET(self):
        if self.path.startswith("/progress"):
            job = (parse_qs(urlparse(self.path).query).get("job") or [""])[0]
            with _PLOCK:
                d = dict(_PROGRESS.get(job) or {})
            # A missing entry means the run finished (the POST clears it) or never started. The
            # client already has its result by then, so "done" is the honest answer either way.
            return self._json(200, d or {"stage": "", "p": 1.0, "next": 1.0, "eta": 0.0, "done": True})
        if self.path.startswith("/health"):
            self._json(200, {"ok": True, "panns": tagger.available()})
            return
        # Static serving, same-origin so the viewer and the studio can both call /analyze.
        # "/" stays the ATONAL viewer; the studio is an ADDITIONAL page, not a replacement.
        path = unquote(self.path.split("?")[0])
        if path in ("/", "/viewer.html"):
            rel = "viewer.html"
        elif path == "/studio":
            rel = "studio.html"
        else:
            rel = path.lstrip("/")
        served = self._serve_static(rel)
        if served:
            return
        # The service banner belongs on the ROOT only. Returning it with a 200 for every unknown
        # path told any client that a missing file had been fetched successfully: a request for
        # /assets/track.wav came back as 200 with a JSON body, which the viewer then handed to
        # decodeAudioData to fail on for reasons that pointed nowhere near the real cause.
        if path in ("/", ""):
            return self._json(200, {"service": "atonal-director", "post": "/analyze",
                                    "pages": ["/", "/studio"]})
        self._json(404, {"error": "not found", "path": path})

    # Only these roots are reachable. The studio needs to load ES modules and the vendored
    # three.js, which means real static serving — so the surface is restricted by prefix rather
    # than left open over the whole working directory (which holds out/cache, the venv and .git).
    _STATIC_OK = ("viewer.html", "studio.html", "studio/", "vendor/", "assets/")
    _MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
             ".json": "application/json", ".svg": "image/svg+xml", ".wasm": "application/wasm",
             ".png": "image/png", ".jpg": "image/jpeg", ".hdr": "application/octet-stream", ".sdf": "application/octet-stream", ".f32": "application/octet-stream"}

    def _serve_static(self, rel):
        if not rel:
            return False
        root = os.path.realpath(os.path.dirname(os.path.abspath(__file__)))
        fp = os.path.realpath(os.path.join(root, rel))
        # NORMALISE FIRST, THEN CHECK THE ALLOWLIST — in that order, and on the resolved path.
        # Testing the raw request instead is a directory traversal: "vendor/../server.py" starts
        # with an allowed prefix AND still resolves inside the project root, so a prefix test on
        # the request plus a containment test on the result both pass while the path has left the
        # prefix entirely. That served every source file in the repo. realpath also collapses
        # symlinks, so a link planted inside studio/ cannot redirect out either.
        try:
            inside = os.path.relpath(fp, root)
        except ValueError:                       # different drive on Windows
            return False
        if inside == os.pardir or inside.startswith(os.pardir + os.sep) or os.path.isabs(inside):
            return False
        if not inside.replace(os.sep, "/").startswith(self._STATIC_OK):
            return False
        if not os.path.isfile(fp):
            return False
        try:
            with open(fp, "rb") as fh:
                body = fh.read()
        except OSError:
            return False
        ctype = self._MIME.get(os.path.splitext(fp)[1].lower(), "application/octet-stream")
        self.send_response(200); self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The vendored library is immutable for a given file; the app code is edited constantly,
        # so only the former is allowed to sit in the browser cache.
        self.send_header("Cache-Control", "public, max-age=86400" if inside.replace(os.sep, "/").startswith("vendor/") else "no-cache")
        self.end_headers(); self.wfile.write(body)
        return True

    def do_POST(self):
        if not self.path.startswith("/analyze"):
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        if n <= 0:
            return self._json(400, {"error": "empty body"})
        if n > MAX_UPLOAD:
            return self._json(413, {"error": f"file too large (limit {MAX_UPLOAD//(1024*1024)} MB)"})
        job = (parse_qs(urlparse(self.path).query).get("job") or [""])[0][:64]
        name = self.headers.get("X-Filename", "upload.mp3")
        ext = os.path.splitext(name)[1] or ".mp3"
        tmp = None
        try:
            data = self.rfile.read(n)
            # Cache on the CONTENT, not the filename: the same track renamed is the same
            # analysis, and a different track under a reused name is not. The digest has to be
            # taken here, before `del data` below drops the upload copy.
            digest = hashlib.sha256(data).hexdigest()
            hit = cache_get(digest)
            if hit is not None:
                # Returned WITHOUT taking _ANALYSIS: a cache hit does no CPU work, so queueing it
                # behind a running analysis would stall it for no reason.
                print(f"[cache] {name} -> {digest[:12]}", flush=True)
                del data
                return self._json(200, hit)
            fd, tmp = tempfile.mkstemp(suffix=ext)      # mkstemp, not mktemp: no race, and we own the fd
            with os.fdopen(fd, "wb") as fp: fp.write(data)
            del data                                     # drop the upload copy before analysis allocates
            print(f"[analyze] {name} ({n/1024:.0f} KB)", flush=True)
            _progress_set(job, {"stage": "queued", "p": 0.0, "next": 0.0, "eta": 0.0})
            with _ANALYSIS:
                d = analyze.build_director(tmp, progress=lambda pr: _progress_set(job, pr))
            cache_put(digest, d)
            self._json(200, d)
            print(f"[done] {name}: {len(d['sections'])} sections, "
                  f"genre={d['genre']['primary']} bpm={d['tempo']['bpm']}", flush=True)
        except Exception as e:
            traceback.print_exc()                        # full detail to the server log...
            # ...but never to the client: the raw exception carried absolute filesystem paths
            # and the full ffmpeg command line.
            msg = "could not decode audio" if "ffmpeg" in str(e).lower() else "analysis failed"
            self._json(500, {"error": msg})
        finally:
            # ran only on success before, so every failed upload left its temp file behind
            _progress_clear(job)
            if tmp and os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass

    # Off by default (the studio pulls ~40 vendored module files per load, which drowns the
    # interesting lines). ATONAL_LOG=1 turns it on — the first question when a browser says
    # "unreachable" is whether the request arrives at all, and silence cannot answer it.
    _LOG = bool(os.environ.get("ATONAL_LOG"))

    def log_message(self, fmt, *a):
        if self._LOG:
            print(f"  {self.command} {self.path} -> {a[1] if len(a) > 1 else ''}", flush=True)

    def _unused_log_message(self, *a):  # quieter
        pass

class _V6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


if __name__ == "__main__":
    # Listen on BOTH loopback families. "localhost" resolves to 127.0.0.1 and ::1, and browsers
    # commonly try ::1 first — against an IPv4-only bind that is a refused connection, which the
    # viewer can only report as "server unreachable". curl hides the problem by walking the
    # address list; the page has no such luxury. Two explicit loopback listeners rather than
    # binding "::" with V6ONLY off, because that would also expose the server on every external
    # interface — this stays loopback-only, as it was.
    servers = []
    for cls, host in ((ThreadingHTTPServer, "127.0.0.1"), (_V6, "::1")):
        try:
            servers.append(cls((host, PORT), H))
        except OSError as e:
            print(f"  (no listener on {host}: {e})", flush=True)
    if not servers:
        raise SystemExit(f"could not bind port {PORT} on either loopback address")

    print(f"ATONAL Director server on http://127.0.0.1:{PORT}   (PANNs: {tagger.available()})",
          flush=True)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
