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
# concurrent requests do not run twice as fast, they thrash and can exhaust memory. The
# serialisation itself now lives in _Slot below, which owns the order as well as the count.

# ------------------------------------------------------------------ WAITING IS NOT ANALYSING
# A second customer arriving during someone else's analysis blocks on the semaphore above, and
# for as long as that lasts the only thing their page was told was {"stage": "queued", "p": 0,
# "next": 0, "eta": 0}. The client strips "queued" from its label, so it read "Analysing..."
# with the bar parked at the upload share and no way to move -- the exact frozen bar the ease
# in viewer.html exists to prevent, except here nothing was happening at all.
#
# MEASURED, two 25s uploads 0.9s apart against this server: the second sat in that state for
# 2.5s of a 3.7s request. On real material the wait is the whole of the other track's analysis,
# and with a 20-minute cap that is up to 46 seconds of a page that looks hung.
#
# So say what is true: waiting, how many are in front, and for roughly how long. The estimate
# uses the duration the length probe already measured, at a rate that starts from the numbers in
# analyze.py's comment and then calibrates itself to whatever machine this is -- the same thing
# analyze.Progress does with its own totals, and for the same reason.
_QCOND = threading.Condition()          # guards _WAITING, _RUNNING, _BUSY and _EST_RATE
_WAITING = []                # _Slot objects waiting for the slot, in SERVICE order
_RUNNING = None              # (started_at, audio_seconds) of the one holding it
_BUSY = False                # is the slot taken
_EST_FIXED = 2.8             # imports and the PANNs checkpoint: constant per analysis
_EST_RATE = 0.036            # wall seconds per second of audio (measured: 3.51s at 20s, 25.81s at 640s)


def _est(secs):
    return _EST_FIXED + _EST_RATE * max(0.0, secs or 0.0)


def _est_observe(audio_secs, wall):
    """Pull the rate towards what this machine actually just did. Guarded on both sides
    because one absurd sample -- a swapping box, a clock jump -- must not poison every
    estimate after it."""
    global _EST_RATE
    if not audio_secs or audio_secs < 20 or wall <= _EST_FIXED:
        return
    r = (wall - _EST_FIXED) / audio_secs
    if 0.002 < r < 1.0:
        with _QCOND:
            _EST_RATE += (r - _EST_RATE) * 0.3


class _Slot:
    """The analysis slot, taken as a context manager so the wait for it can report itself
    and so the ORDER of the wait is ours to decide.

    Nothing here changes what is serialised or for how long -- one analysis at a time is the
    right answer and stays. What changes is that the queue is visible from outside it, and
    that a key which bought a pack carrying `priority` is served before one that did not.
    That line was on the pricing page for a while with nothing behind it.

    TURN-TAKING RATHER THAN A RACE. The first version of this looped on
    Semaphore.acquire(timeout=0.4), which cannot express an order at all: every waiter
    re-enters the semaphore's own wait queue on each pass, so whoever happens to be woken
    wins. It was not even reliably first-come. A condition variable plus an explicitly
    ordered list makes the next server a decision instead of an accident.
    """

    def __init__(self, job, secs, prio=False):
        self.job, self.secs, self.prio = job, secs, bool(prio)

    def _say_waiting(self):
        """Caller holds _QCOND. Only _progress_set's own lock is taken below it, and nothing
        anywhere takes _QCOND while holding that, so the nesting is one-way."""
        try:
            pos = _WAITING.index(self)
        except ValueError:
            pos = 0
        wait = sum(_est(w.secs) for w in _WAITING[:pos])
        if _RUNNING:
            wait += max(0.0, _est(_RUNNING[1]) - (time.time() - _RUNNING[0]))
        # `ahead` CAN GO UP while someone is waiting, when a priority upload arrives behind
        # them and is placed in front. That is what it looks like to be overtaken, and the
        # honest thing is to show it rather than to freeze a number that is no longer true.
        _progress_set(self.job, {"stage": "waiting", "p": 0.0, "next": 0.0,
                                 "eta": round(wait, 1),
                                 "ahead": pos + (1 if _RUNNING else 0)})

    def _seat(self):
        """Insert into _WAITING at the position we should be served from. Priority goes ahead
        of everything unprioritised and behind everything prioritised, so each class stays
        first-come within itself and no one is reordered twice."""
        i = len(_WAITING)
        if self.prio:
            while i > 0 and not _WAITING[i - 1].prio:
                i -= 1
        _WAITING.insert(i, self)

    def __enter__(self):
        global _BUSY, _RUNNING
        with _QCOND:
            # The common case -- nobody else here -- never enters the queue and so never
            # flashes a "waiting" state it is not in.
            if _BUSY or _WAITING:
                self._seat()
                self._say_waiting()
                try:
                    while _BUSY or _WAITING[0] is not self:
                        _QCOND.wait(0.5)         # timed, so the countdown keeps ticking
                        self._say_waiting()
                except BaseException:
                    if self in _WAITING:
                        _WAITING.remove(self)
                    _QCOND.notify_all()          # our seat is free; someone else may be next
                    raise
                _WAITING.remove(self)
            _BUSY = True
            _RUNNING = (time.time(), self.secs)
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        global _BUSY, _RUNNING
        with _QCOND:
            _BUSY = False
            _RUNNING = None
            _QCOND.notify_all()
        if exc[0] is None:
            _est_observe(self.secs, time.time() - self.t0)
        return False

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
# The ceiling on the TRANSFER. It is not the ceiling on the work, which this comment used to
# claim it was: analysis cost is linear in seconds of audio and has no term in file size, so
# 300 MB of 128 kbps mp3 is five hours and about 94 GB. That is analyze.MAX_SECONDS' job now;
# see the measurements above it. This number still earns its place -- the body is read into
# memory in one call below, on a thread per request -- it just bounds a different thing.
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

    @staticmethod
    def _bucket(addr):
        """The identity the free tier counts against.

        IPv4 IS THE ADDRESS. IPv6 IS THE /64, AND THAT DIFFERENCE IS THE WHOLE POINT.
        A v6 host does not have an address, it has a range: RFC 4941 privacy extensions
        rotate the low 64 bits on a schedule (daily on most desktops, and some stacks take a
        fresh one per connection), and every device behind one subscriber line gets its own
        address out of the same /64 anyway. Counting the full 128 bits therefore counts
        something that changes on its own, so `FREE_PER_DAY` was not a daily limit for any
        v6 client -- it was a per-address limit on an address they get a new one of for free.
        billing.py says the free tier exists to close "the unbounded-cost-with-zero-revenue
        case"; on v6 it was wide open, and analysis is 0.036s of CPU and up to 4.95 MB of RSS
        per second of audio on a single serialised slot.

        The /64 is the right unit because it is what gets delegated to a subscriber: smaller
        and the rotation defeats it, larger and one ISP's customers share a bucket.

        IPv4-mapped addresses (::ffff:1.2.3.4, which is what a dual-stack listener reports for
        a v4 client) resolve to the v4 address, so the same caller is one identity whichever
        socket family it arrives on rather than two.
        """
        try:
            import ipaddress
            ip = ipaddress.ip_address(addr.strip())
            if getattr(ip, "ipv4_mapped", None):
                return str(ip.ipv4_mapped)
            if ip.version == 6:
                return str(ipaddress.ip_network(str(ip) + "/64", strict=False))
            return str(ip)
        except ValueError:
            # Not an address we can parse -- a hostname from a header, say. Counting it as
            # itself is still better than counting nothing.
            return (addr or "").strip()[:64]

    def _ip(self):
        """The free tier counts per IP, so behind a proxy the header is the only
        real client address -- but it is client-settable, so it is trusted ONLY
        when ATONAL_TRUST_PROXY says a proxy is in front of us. Trusting it
        unconditionally would make the free-tier limit one header away from
        infinite."""
        if os.environ.get("ATONAL_TRUST_PROXY"):
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return self._bucket(fwd.split(",")[0][:64])
        return self._bucket(self.client_address[0])

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
                # The key the BROWSER already holds, if any, in a header rather than the query
                # string -- it is a bearer credential and a query string reaches access logs and
                # Referer headers. It is only ever compared against a hash here, never stored;
                # see the note in billing.claim.
                have = (self.headers.get("X-Render-Key") or "").strip() or None
                self._json(200, billing.claim(sid, have_key=have))
            except Exception as e:
                traceback.print_exc()
                self._json(400, {"error": "could not confirm that purchase"})
            return True
        return False

    @staticmethod
    def _too_long(seconds):
        """One wording, whether the probe caught it or the backstop did.

        It names the track's own length as well as the limit, because "too long" without a
        number leaves the customer guessing which of their files is the problem, and it says
        nothing was charged, because that is the first thing anyone who has paid will wonder.
        """
        lim = analyze.MAX_SECONDS
        return {"error": f"That track is {seconds/60:.0f} minutes long and the limit is "
                         f"{lim/60:.0f}. Trim it, or split it into parts and analyse those. "
                         f"Nothing was charged for this.",
                "code": "too_long",
                "minutes": round(seconds / 60.0, 1),
                "limit_minutes": round(lim / 60.0, 1)}

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
                # Returned WITHOUT taking the analysis slot: a cache hit does no CPU work, so queueing it
                # behind a running analysis would stall it for no reason.
                print(f"[cache] {name} -> {digest[:12]}", flush=True)
                del data
                return self._json(200, hit)
            # Written HERE, above the gate, and that ordering is the point: ffmpeg needs a
            # path to read a header from, and the length check below has to happen before a
            # credit is taken. Writing a temp file is bounded by MAX_UPLOAD and costs a disk
            # write we were going to do anyway.
            fd, tmp = tempfile.mkstemp(suffix=ext)      # mkstemp, not mktemp: no race, and we own the fd
            with os.fdopen(fd, "wb") as fp: fp.write(data)
            del data                                     # drop the upload copy before analysis allocates
            # ---- THE LENGTH CHECK ----
            # ABOVE THE GATE, because a track we refuse is a track nobody should pay for. It
            # costs ~11ms (analyze.probe_duration reads the container header and stops), against
            # an analysis that is 0.036s and 4.95 MB of RSS for every second of audio -- so a
            # five-hour upload that would have taken the process down is now a 413 with a
            # sentence the customer can act on, and their credit is untouched.
            secs = analyze.probe_duration(tmp)
            if secs is not None and secs > analyze.MAX_SECONDS:
                # Logged like every other outcome. A refusal is a thing an operator needs to be
                # able to see -- if the cap is set too low, the only evidence is these lines.
                print(f"[too long] {name}: {secs/60:.1f} min "
                      f"(limit {analyze.MAX_SECONDS/60:.0f})", flush=True)
                return self._json(413, self._too_long(secs))
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
                    return self._json(402, {"error": "That render key is not recognised.",
                                            "code": "bad_key"})
                if not billing.spend(key, "analyze " + name[:60], "an:" + attempt):
                    return self._json(402, {"error": "No credits left on this key.",
                                            "code": "no_credits", "balance": 0})
                charged = (key, attempt)
            else:
                if not billing.free_take(self._ip()):
                    return self._json(402, {
                        "error": f"That is your free "
                                 f"{'track' if billing.FREE_PER_DAY == 1 else 'tracks'} for today. "
                                 f"A credit unlocks the next one.",
                        "code": "free_used"})
                freed = (self._ip(), billing.utc_day())
            print(f"[analyze] {name} ({n/1024:.0f} KB{'' if secs is None else f', {secs/60:.1f} min'})", flush=True)
            _progress_set(job, {"stage": "queued", "p": 0.0, "next": 0.0, "eta": 0.0})
            with _Slot(job, secs, prio=bool(key) and billing.has_priority(key)):
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
        except analyze.TooLong as e:
            # The probe above catches this before the gate; this is the backstop firing, for a
            # container whose header did not carry a duration. Same answer, same refund -- what
            # must not happen is that it arrives as the generic "analysis failed", which tells a
            # customer nothing and reads as our fault rather than a limit they can work around.
            if charged:
                billing.refund(charged[0], "refund: over the length limit", "rf:" + charged[1])
            elif freed:
                billing.free_refund(*freed)
            print(f"[too long] {name}: {e}", flush=True)
            self._json(413, self._too_long(e.seconds))
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
