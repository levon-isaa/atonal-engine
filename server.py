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
import os, json, time, socket, tempfile, traceback, threading, hashlib, secrets
from urllib.parse import urlparse, parse_qs, unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import analyze, tagger, billing

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
        if len(_PROGRESS) > 64:
            now = time.time()
            for k in [k for k, v in _PROGRESS.items() if now - v.get("at", 0) > _PROGRESS_TTL]:
                _PROGRESS.pop(k, None)
            # TTL alone bounds NOTHING, which is what the old comment here claimed it did: 65
            # entries all younger than the TTL prune to 65, and the same is true on every insert
            # after. Evict oldest-first until the cap actually holds.
            if len(_PROGRESS) > 64:
                oldest = sorted(_PROGRESS, key=lambda k: _PROGRESS[k].get("at", 0))
                for k in oldest[:len(_PROGRESS) - 64]:
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-Render-Key")
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

    def _ip(self):
        """The free tier counts per IP, so behind a proxy the header is the only
        real client address -- but it is client-settable, so it is trusted ONLY
        when ATONAL_TRUST_PROXY says a proxy is in front of us. Trusting it
        unconditionally would make the free-tier limit one header away from
        infinite."""
        if os.environ.get("ATONAL_TRUST_PROXY"):
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()[:64]
        return self.client_address[0]

    def _billing_get(self, path, q):
        """GET side of billing. Returns True if it handled the request."""
        if path.startswith("/credits"):
            key = (q.get("key") or [""])[0].strip()
            out = {"free_left": billing.free_left(self._ip()),
                   "free_per_day": billing.FREE_PER_DAY,
                   "ready": billing.billing_ready(), "provider": "paddle"}
            if key:
                out["known"] = billing.key_exists(key)
                out["balance"] = billing.balance(key) if out["known"] else 0
            self._json(200, out)
            return True
        if path.startswith("/packs"):
            self._json(200, {"currency": billing.CURRENCY,
                             "ready": billing.billing_ready(), "provider": "paddle",
                             "free_per_day": billing.FREE_PER_DAY,
                             "packs": billing.PACKS})
            return True
        if path.startswith("/claim"):
            sid = (q.get("session_id") or [""])[0].strip()
            if not sid:
                self._json(400, {"error": "missing session_id"}); return True
            if not billing.billing_ready():
                self._json(503, {"error": "billing is not configured"}); return True
            try:
                self._json(200, billing.claim(sid))
            except Exception as e:
                traceback.print_exc()
                self._json(400, {"error": "could not confirm that purchase"})
            return True
        return False

    def _body(self, cap=1 << 20):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return None
        if n <= 0 or n > cap:
            return None
        return self.rfile.read(n)

    def _checkout(self):
        """Creates a Paddle transaction and hands back its hosted checkout URL.
        No card details ever reach this server or the site -- the browser goes to
        Paddle's own hosted page, which is the entire point of using it."""
        if not billing.billing_ready():
            return self._json(503, {"error": "billing is not configured yet"})
        raw = self._body()
        if raw is None:
            return self._json(400, {"error": "bad body"})
        try:
            pack = (json.loads(raw or b"{}") or {}).get("pack", "")
        except Exception:
            return self._json(400, {"error": "bad JSON"})
        if pack not in billing.PACKS:
            return self._json(400, {"error": "unknown pack"})
        # The origin the browser is actually on, so the redirect comes back here
        # whether that is localhost or the live domain. Restricted to a host we
        # serve, so this cannot be used to bounce a customer somewhere else.
        origin = os.environ.get("ATONAL_ORIGIN") or ("http://" + (self.headers.get("Host") or f"127.0.0.1:{PORT}"))
        try:
            return self._json(200, {"url": billing.checkout_url(pack, origin)})
        except Exception:
            traceback.print_exc()
            return self._json(502, {"error": "could not start checkout"})

    def _webhook(self):
        """Paddle calls this. The signature check is what makes it safe to expose:
        without it, this is an unauthenticated endpoint that mints credits."""
        raw = self._body()
        if raw is None:
            return self._json(400, {"error": "bad body"})
        sig = self.headers.get("Paddle-Signature", "")
        try:
            out = billing.webhook(raw, sig)
            return self._json(200, out)
        except Exception as e:
            # 400 so Paddle retries a genuine transient failure, and so a forged
            # call gets nothing. The reason stays in our log, not the response.
            traceback.print_exc()
            return self._json(400, {"error": "webhook rejected"})

    def do_GET(self):
        _q = parse_qs(urlparse(self.path).query)
        if self._billing_get(urlparse(self.path).path, _q):
            return
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
        # Static serving, same-origin so the viewer can call /analyze without a preflight.
        path = unquote(self.path.split("?")[0])
        if path in ("/", "/viewer.html"):
            rel = "viewer.html"
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
                                    "pages": ["/"]})
        self._json(404, {"error": "not found", "path": path})

    # Only these roots are reachable. The viewer loads its SDF fields, detail maps and prefiltered
    # environments from assets/, which means real static serving — so the surface is restricted by
    # prefix rather than left open over the whole working directory (which holds out/cache, the
    # venv and .git).
    # SPLIT INTO FILES AND DIRECTORIES, because one startswith() over both cannot tell them
    # apart. "viewer.html" as a prefix also matches viewer.html.bak, viewer.html~, viewer.html.orig
    # and viewer.html.rej -- the editor backups and merge leftovers that collect beside exactly
    # this file -- and each was served in full, verified with a canary. Directory entries keep
    # their trailing slash and stay a prefix test; file entries are now matched exactly.
    # BOTH TUPLES KEEP THEIR TRAILING COMMA. Without it these are plain strings, and then
    # `rel_posix not in _STATIC_FILES` becomes a SUBSTRING test -- "ew.htm" would pass it.
    _STATIC_FILES = ("viewer.html",)
    _STATIC_DIRS = ("assets/", "site/")
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
        # Testing the raw request instead is a directory traversal: "assets/../server.py" starts
        # with an allowed prefix AND still resolves inside the project root, so a prefix test on
        # the request plus a containment test on the result both pass while the path has left the
        # prefix entirely. That served every source file in the repo. realpath also collapses
        # symlinks, so a link planted inside assets/ cannot redirect out either.
        try:
            inside = os.path.relpath(fp, root)
        except ValueError:                       # different drive on Windows
            return False
        if inside == os.pardir or inside.startswith(os.pardir + os.sep) or os.path.isabs(inside):
            return False
        rel_posix = inside.replace(os.sep, "/")
        if rel_posix not in self._STATIC_FILES and not rel_posix.startswith(self._STATIC_DIRS):
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
        # Nothing served here is allowed to sit in the browser cache. The viewer is edited
        # constantly, and assets/ is REGENERATED -- tools_pmrem.py rewrites env_*.bin in place,
        # under the same name, so a cached copy would be silently stale rather than merely old.
        self.send_header("Cache-Control", "no-cache")
        self.end_headers(); self.wfile.write(body)
        return True

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/checkout":
            return self._checkout()
        if path == "/paddle/webhook":
            return self._webhook()
        if not path.startswith("/analyze"):
            return self._json(404, {"error": "not found"})
        # Parsed BEFORE the try below, so a non-numeric header used to raise ValueError straight
        # out of do_POST: no response at all, just a dropped connection and a traceback in the
        # log. A client cannot tell that apart from the server being down, which is the same
        # failure the CORS and Private-Network notes above exist to prevent.
        try:
            n = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            return self._json(400, {"error": "bad Content-Length"})
        if n <= 0:
            return self._json(400, {"error": "empty body"})
        if n > MAX_UPLOAD:
            return self._json(413, {"error": f"file too large (limit {MAX_UPLOAD//(1024*1024)} MB)"})
        job = (parse_qs(urlparse(self.path).query).get("job") or [""])[0][:64]
        # Percent-encoded by the client, because a header cannot carry anything outside
        # ISO-8859-1 and music filenames routinely do (curly apostrophes, en dashes, any
        # non-Latin script). unquote is the exact inverse of the client's encodeURIComponent.
        name = unquote(self.headers.get("X-Filename", "upload.mp3"))
        ext = os.path.splitext(name)[1] or ".mp3"
        tmp = None
        charged = None            # (key, attempt-ref) once a credit has been taken
        freed = None              # (ip, day) once a FREE take has been recorded
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
            # ---- THE GATE ----
            # Below the cache check on purpose: a cache hit does no work, so
            # charging for it would be charging for a dictionary lookup. Above
            # everything expensive, so nothing is spent before payment is.
            # The ref is per ATTEMPT rather than per track, so a refund below
            # cannot leave a track permanently free to re-analyse.
            key = (self.headers.get("X-Render-Key") or "").strip()
            attempt = digest[:16] + ":" + secrets.token_hex(6)
            if key:
                if not billing.key_exists(key):
                    del data
                    return self._json(402, {"error": "That render key is not recognised.",
                                            "code": "bad_key"})
                if not billing.spend(key, "analyze " + name[:60], "an:" + attempt):
                    del data
                    return self._json(402, {"error": "No credits left on this key.",
                                            "code": "no_credits", "balance": 0})
                charged = (key, attempt)
            else:
                if not billing.free_take(self._ip()):
                    del data
                    return self._json(402, {
                        "error": f"That is your free "
                                 f"{'track' if billing.FREE_PER_DAY == 1 else 'tracks'} for today. "
                                 f"A credit unlocks the next one.",
                        "code": "free_used"})
                freed = (self._ip(), billing.utc_day())
            fd, tmp = tempfile.mkstemp(suffix=ext)      # mkstemp, not mktemp: no race, and we own the fd
            with os.fdopen(fd, "wb") as fp: fp.write(data)
            del data                                     # drop the upload copy before analysis allocates
            print(f"[analyze] {name} ({n/1024:.0f} KB)", flush=True)
            _progress_set(job, {"stage": "queued", "p": 0.0, "next": 0.0, "eta": 0.0})
            with _ANALYSIS:
                d = analyze.build_director(tmp, progress=lambda pr: _progress_set(job, pr))
            cache_put(digest, d)
            # Built defensively and BEFORE the response. This used to index d['sections'],
            # d['genre'] and d['tempo'] after _json(200) had already written a complete response,
            # so a single missing key raised into the except below and wrote a SECOND HTTP
            # response onto the same connection -- logging a 500 and "analysis failed" for an
            # analysis that had in fact succeeded and been cached, with the real KeyError
            # swallowed by the generic message.
            secs = len(d.get("sections") or [])
            genre = (d.get("genre") or {}).get("primary", "?")
            bpm = (d.get("tempo") or {}).get("bpm", "?")
            self._json(200, d)
            print(f"[done] {name}: {secs} sections, genre={genre} bpm={bpm}", flush=True)
        except Exception as e:
            # A crash on our side must never cost the customer a credit -- and that has to
            # include the ones who have not paid yet, which for a long time it did not. The free
            # branch below is the same promise as the ledger refund above it; see free_refund.
            if charged:
                billing.refund(charged[0], "refund: analysis failed", "rf:" + charged[1])
            elif freed:
                billing.free_refund(*freed)
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

    # Off by default: the per-request lines drown the [analyze]/[done]/[cache] ones that are
    # actually being read. ATONAL_LOG=1 turns it on — the first question when a browser says
    # "unreachable" is whether the request arrives at all, and silence cannot answer it.
    _LOG = bool(os.environ.get("ATONAL_LOG"))

    def log_message(self, fmt, *a):
        if self._LOG:
            print(f"  {self.command} {self.path} -> {a[1] if len(a) > 1 else ''}", flush=True)

class _V6(ThreadingHTTPServer):
    address_family = socket.AF_INET6


if __name__ == "__main__":
    # Listen on BOTH loopback families. "localhost" resolves to 127.0.0.1 and ::1, and browsers
    # commonly try ::1 first — against an IPv4-only bind that is a refused connection, which the
    # viewer can only report as "server unreachable". curl hides the problem by walking the
    # address list; the page has no such luxury. Two explicit loopback listeners rather than
    # binding "::" with V6ONLY off, because that would also expose the server on every external
    # interface — this stays loopback-only, as it was.
    # ATONAL_HOST OPENS THAT UP, DELIBERATELY AND NEVER BY DEFAULT.
    #
    # Loopback-only is right for the normal case and stays the default: with no ATONAL_HOST set,
    # the binds below are exactly the two above and nothing is reachable off this machine. The one
    # case it cannot serve is looking at the viewer from a phone or a tablet, where the browser is
    # on a different device and 127.0.0.1 means that device, not this one.
    #
    # KNOW WHAT SETTING IT COSTS. /analyze accepts an arbitrary upload and spends real CPU on it,
    # and the static handler serves out of the project directory. Exposed on a LAN, both are
    # reachable by anything sharing the network -- a guest phone, a smart TV, whatever else is on
    # the Wi-Fi. There is no authentication here and this flag does not add any. So it is opt-in,
    # per run, and it announces itself loudly at startup rather than being a quiet default:
    #     ATONAL_HOST=0.0.0.0 python server.py     then http://<this machine's LAN IP>:8770
    # Turn it off by restarting without the variable. Do not set it on a network you do not
    # control.
    host_env = os.environ.get("ATONAL_HOST", "").strip()
    if host_env:
        binds = [(ThreadingHTTPServer, host_env)]
    else:
        binds = [(ThreadingHTTPServer, "127.0.0.1"), (_V6, "::1")]

    servers = []
    for cls, host in binds:
        try:
            servers.append(cls((host, PORT), H))
        except OSError as e:
            print(f"  (no listener on {host}: {e})", flush=True)
    if not servers:
        where = host_env if host_env else "either loopback address"
        raise SystemExit(f"could not bind port {PORT} on {where}")

    if host_env:
        print(f"!! ATONAL_HOST={host_env} -- this server is reachable from OTHER DEVICES on your "
              f"network.\n!! /analyze takes uploads and there is no authentication. "
              f"Restart without ATONAL_HOST to go back to loopback-only.", flush=True)
    print(f"ATONAL Director server on http://127.0.0.1:{PORT}   (PANNs: {tagger.available()})",
          flush=True)
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
