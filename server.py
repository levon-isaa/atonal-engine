#!/usr/bin/env python3
"""Rota server: the sales site, every company's booking site and staff desk, and the JSON API.

The address decides what is served. With ROTA_PUBLIC_URL=https://rota.am:

  https://rota.am/                 sales site; /start to sign up; /admin/ for Rota's own admins
  https://<studio>.rota.am/        that studio's booking site (/b/<link> for a client's manage link)
  https://<studio>.rota.am/desk/   that studio's staff desk

  Sales site API     GET /api/site/info, GET /api/site/slug?s=, POST /api/site/signup
                     POST /api/admin/login | /logout, GET /api/admin/me | /studios, POST /api/admin/studios/<slug>
  Studio API         GET /api/public/studio | /availability, POST /api/public/bookings,
                     GET /api/public/bookings/<t>, POST /api/public/bookings/<t>/cancel | /reschedule
                     POST /api/auth/login | /logout | /ticket | /demo, GET /api/auth/me
                     GET /api/desk/sync?rev=N, PUT|DELETE /api/desk/<kind>/<id>, GET /api/desk/export/<what>.csv
  Anywhere           GET /api/health, GET /api/tls-check?domain= (asked by Caddy before it gets a certificate)

Each studio's records are a separate SQLite file, and sessions are per studio and per address, so
staff signed in to one studio can't read another. Standard library only; run it on 127.0.0.1
behind Caddy (deploy/), which adds TLS for every studio address.

    python server.py            # http://localhost:8780, studios at http://<studio>.localhost:8780
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
from datetime import date as Date, datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rota import __version__, auth, booking, config as configmod, platform, templates
from rota.platform import Platform, SignupError
from rota.store import KINDS, Store

MAX_BODY = 64 * 1024
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'; object-src 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
# Owners manage the studio itself; other staff run the day-to-day book.
OWNER_ONLY = {("PUT", "settings"), ("PUT", "staff"), ("DELETE", "staff"), ("PUT", "services"), ("DELETE", "services"), ("DELETE", "clients")}
PAUSED = "Online booking is paused for this studio. Please call them to book."
LAST_SEEN_EVERY = 600  # seconds between "the desk was used" stamps per studio


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


class Studio:
    """One company, as seen by a request to its address."""

    def __init__(self, app: "App", row):
        self.slug = row["slug"]
        self.row = row
        self.store = app.store(self.slug)
        self.secret = app.studio_secret(self.slug)

    @property
    def status(self) -> str:
        return self.row["status"]

    def settings(self, c) -> dict:
        return booking.settings(c, self.store)


class App:
    def __init__(self, cfg: configmod.Config):
        self.cfg = cfg
        self.platform = Platform(cfg.data_dir)
        self.limiter = auth.RateLimiter()
        self.secret = self.platform.secret(cfg.secret)
        self._stores: dict[str, Store] = {}
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def store(self, slug: str) -> Store:
        with self._lock:
            st = self._stores.get(slug)
            if st is None:
                st = self._stores[slug] = Store(self.platform.studio_path(slug))
            return st

    def forget(self, slug: str) -> None:
        with self._lock:
            self._stores.pop(slug, None)

    def studio_secret(self, slug: str) -> str:
        # Each studio signs its manage links with its own key, so a link can't be replayed elsewhere.
        return hmac.new(self.secret.encode(), b"studio:" + slug.encode(), hashlib.sha256).hexdigest()

    def studio_row(self, slug: str):
        with self.platform.read() as c:
            return Platform.studio(c, slug)

    def now(self, s: dict) -> datetime:
        return datetime.now(booking.studio_tz(s, self.cfg.timezone))

    def seen(self, studio: Studio) -> None:
        t = time.time()
        if t - self._seen.get(studio.slug, 0) < LAST_SEEN_EVERY:
            return
        self._seen[studio.slug] = t
        with studio.store.tx() as c:
            Store.meta_set(c, "last_seen", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


ROUTES = [
    ("GET", r"/api/health", "health", "any"),
    ("GET", r"/api/tls-check", "tls_check", "any"),
    # sales site
    ("GET", r"/api/site/info", "site_info", "site"),
    ("GET", r"/api/site/slug", "site_slug", "site"),
    ("POST", r"/api/site/signup", "site_signup", "site"),
    ("POST", r"/api/admin/login", "admin_login", "site"),
    ("POST", r"/api/admin/logout", "admin_logout", "site"),
    ("GET", r"/api/admin/me", "admin_me", "site"),
    ("GET", r"/api/admin/studios", "admin_studios", "site"),
    ("POST", r"/api/admin/studios/(?P<slug>[a-z0-9-]+)", "admin_studio_update", "site"),
    # a studio
    ("GET", r"/api/public/studio", "pub_studio", "studio"),
    ("GET", r"/api/public/availability", "pub_availability", "studio"),
    ("POST", r"/api/public/bookings", "pub_book", "studio"),
    ("GET", r"/api/public/bookings/(?P<token>[^/]+)", "pub_get", "studio"),
    ("POST", r"/api/public/bookings/(?P<token>[^/]+)/cancel", "pub_cancel", "studio"),
    ("POST", r"/api/public/bookings/(?P<token>[^/]+)/reschedule", "pub_reschedule", "studio"),
    ("POST", r"/api/auth/login", "login", "studio"),
    ("POST", r"/api/auth/logout", "logout", "studio"),
    ("POST", r"/api/auth/ticket", "ticket", "studio"),
    ("POST", r"/api/auth/demo", "demo_login", "studio"),
    ("GET", r"/api/auth/me", "me", "studio"),
    ("POST", r"/api/auth/password", "password", "studio"),
    ("GET", r"/api/desk/users", "users", "studio"),
    ("POST", r"/api/desk/users", "user_add", "studio"),
    ("POST", r"/api/desk/users/(?P<uid>\d+)/password", "user_password", "studio"),
    ("DELETE", r"/api/desk/users/(?P<uid>\d+)", "user_remove", "studio"),
    ("GET", r"/api/desk/sync", "desk_sync", "studio"),
    ("GET", r"/api/desk/export/(?P<what>clients|appointments)\.csv", "desk_export", "studio"),
    ("PUT", r"/api/desk/(?P<kind>[a-z]+)/(?P<id>[^/]+)", "desk_put", "studio"),
    ("DELETE", r"/api/desk/(?P<kind>[a-z]+)/(?P<id>[^/]+)", "desk_delete", "studio"),
]
ROUTES = [(m, re.compile(p + "$"), h, scope) for m, p, h, scope in ROUTES]


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    protocol_version = "HTTP/1.1"
    server_version = "Rota"
    sys_version = ""
    timeout = 30

    # ---------------------------------------------------------------- plumbing
    def log_request(self, code="-", size="-"):
        path = re.sub(r"/(bookings|b)/[^/?]+", r"/\1/…", self.path.split("?")[0])[:120]  # manage links are secrets
        sys.stderr.write(f"{self.client_ip()} {self.host_name() or '-'} {self.command} {path} {code}\n")

    def log_error(self, fmt, *args):
        pass

    def client_ip(self) -> str:
        if self.app.cfg.trust_proxy:
            ip = (self.headers.get("X-Real-IP") or "").strip()
            if ip:
                return ip
        return self.client_address[0]

    def host_name(self) -> str:
        return (self.headers.get("Host") or "").split(":")[0].strip().lower().rstrip(".")

    def resolve_host(self):
        """("site", None), ("studio", Studio), ("missing", slug) or ("foreign", None)."""
        host, domain = self.host_name(), self.app.cfg.domain
        if host in (domain, "www." + domain):
            return "site", None
        if host.endswith("." + domain):
            label = host[: -len(domain) - 1]
            if platform.SLUG_RE.match(label) and "--" not in label:
                row = self.app.studio_row(label)
                return ("studio", Studio(self.app, row)) if row else ("missing", label)
        return "foreign", None

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None, head=False):
        self.send_response(status)
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            if isinstance(v, list):
                for item in v:
                    self.send_header(k, item)
            else:
                self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def json(self, status: int, obj, extra: dict | None = None):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        self._send(status, body, "application/json; charset=utf-8", {"Cache-Control": "no-store", **(extra or {})})

    def read_raw(self):
        # Read the whole body of every write, even for endpoints that ignore it: bytes left unread
        # on a keep-alive connection would be parsed as the start of the next request.
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_length", "Bad Content-Length.")
        if n > MAX_BODY:
            raise ApiError(413, "too_large", "That request is too large.")
        if n > 0 and "application/json" not in (self.headers.get("Content-Type") or ""):
            raise ApiError(415, "json_only", "Send JSON.")
        self._raw = self.rfile.read(n) if n > 0 else b""

    def body(self) -> dict:
        raw = self._raw
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "bad_json", "That isn't valid JSON.")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "Send a JSON object.")
        return data

    def check_write_origin(self):
        # A custom header can't be added by a plain cross-site form, and we never allow CORS, so
        # requiring it (plus SameSite cookies) keeps other sites from posting on a user's behalf.
        # Studios share a parent domain, so the Origin must also be this exact address: one studio's
        # pages can't write to another's.
        if self.headers.get("X-Rota") != "1":
            raise ApiError(403, "missing_header", "Requests must come from the Rota app.")
        origin = self.headers.get("Origin")
        if origin and origin.rstrip("/").lower() != f"{self.app.cfg.scheme}://{(self.headers.get('Host') or '').lower()}":
            raise ApiError(403, "bad_origin", "Requests must come from this site.")

    def limit(self, name: str, limit: int, window: float, key: str | None = None):
        scope = self.studio.slug if self.studio else "site"
        if not self.app.limiter.allow(f"{name}:{scope}:{key or self.client_ip()}", limit, window):
            raise ApiError(429, "slow_down", "Too many requests. Wait a few minutes and try again.")

    @property
    def cookie_name(self) -> str:
        # __Host- cookies can't be set or overwritten by a sibling subdomain (another studio).
        return "__Host-rota" if self.app.cfg.secure_cookies else "rota_session"

    def cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie(raw)
        except CookieError:
            return None
        m = jar.get(self.cookie_name)
        return m.value if m else None

    def session_cookie(self, token: str, max_age: int) -> str:
        parts = [f"{self.cookie_name}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self.app.cfg.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def signed_in(self, token: str, row) -> None:
        self.json(200, self.me_view(row), {"Set-Cookie": self.session_cookie(token, auth.SESSION_DAYS * 86400)})

    # ---------------------------------------------------------------- dispatch
    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("GET", head=True)

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method: str, head=False):
        url = urlparse(self.path)
        path = url.path
        self._raw = b""
        self.studio = None
        try:
            kind, found = self.resolve_host()
            if kind == "studio":
                self.studio = found
            if path.startswith("/api/"):
                for m, rx, name, scope in ROUTES:
                    match = rx.match(path)
                    if match and m == method and scope in ("any", kind):
                        if method != "GET":
                            self.check_write_origin()
                            self.read_raw()
                        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                        return getattr(self, "api_" + name)(q, **match.groupdict())
                raise ApiError(404, "not_found", "No such endpoint.")
            if method != "GET":
                raise ApiError(405, "method", "Method not allowed.")
            return self.static(kind, path, head)
        except (ApiError, booking.BookingError, SignupError) as e:
            if method != "GET":
                self.close_connection = True  # the body may be unread; don't parse it as the next request
            err = {"error": e.code, "message": e.message}
            if getattr(e, "field", None):
                err["field"] = e.field
            self.json(e.status, err)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.json(500, {"error": "server", "message": "Something went wrong on our side. Try again in a minute."})
            except Exception:
                pass

    # ---------------------------------------------------------------- static files
    def static(self, kind: str, path: str, head: bool):
        root = self.app.cfg.web_root
        if path == "/favicon.svg":
            return self.file(os.path.join(root, "shared", "favicon.svg"), head)
        if path.startswith("/assets/"):
            full = os.path.normpath(os.path.join(root, path[len("/assets/"):]))
            if full.startswith(os.path.normpath(root) + os.sep) and os.path.isfile(full):
                return self.file(full, head)
        elif kind == "site":
            pages = {"/": "site/index.html", "/index.html": "site/index.html", "/start": "site/start.html",
                     "/admin/": "admin/index.html"}
            if path in pages:
                return self.file(os.path.join(root, pages[path]), head, html=True)
            if path == "/admin":
                return self._send(301, b"", "text/plain", {"Location": "/admin/"}, head)
        elif kind == "studio":
            if path in ("/", "/index.html") or path.startswith("/b/"):
                return self.file(os.path.join(root, "book", "index.html"), head, html=True)
            if path == "/desk":
                return self._send(301, b"", "text/plain", {"Location": "/desk/"}, head)
            if path == "/desk/":
                return self.file(os.path.join(root, "desk", "index.html"), head, html=True)
        elif kind == "missing" and (path in ("/", "/desk", "/desk/") or path.startswith("/b/")):
            return self.file(os.path.join(root, "site", "missing.html"), head, html=True, status=404)
        self._send(404, "Not found".encode(), "text/plain; charset=utf-8", {"Cache-Control": "no-store"}, head)

    def file(self, full: str, head: bool, html=False, status=200):
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        cache = "no-cache" if html else "public, max-age=300"
        self._send(status, data, ctype, {"Cache-Control": cache}, head)

    # ---------------------------------------------------------------- anywhere
    def api_health(self, q):
        with self.app.platform.read() as c:
            c.execute("SELECT 1").fetchone()
        self.json(200, {"ok": True, "version": __version__})

    def api_tls_check(self, q):
        # Caddy asks this before getting a certificate for an address, so nobody can make the
        # server request certificates for names that aren't Rota's.
        host = (q.get("domain") or "").strip().lower().rstrip(".")
        domain = self.app.cfg.domain
        ok = host in (domain, "www." + domain)
        if not ok and host.endswith("." + domain):
            label = host[: -len(domain) - 1]
            ok = bool(platform.SLUG_RE.match(label)) and self.app.studio_row(label) is not None
        self.json(200 if ok else 404, {"ok": ok})

    # ---------------------------------------------------------------- sales site
    def api_site_info(self, q):
        cfg = self.app.cfg
        demo = self.app.studio_row(platform.DEMO)
        self.json(200, {
            "domain": cfg.domain, "pilotDays": cfg.pilot_days, "kinds": list(templates.KINDS),
            "contactPhone": cfg.contact_phone, "contactEmail": cfg.contact_email,
            "demo": cfg.studio_url(platform.DEMO) if demo else None,
        })

    def api_site_slug(self, q):
        self.limit("slug", 120, 600)
        try:
            slug = platform.check_slug(self.app.platform, q.get("s"))
        except SignupError as e:
            return self.json(200, {"ok": False, "error": e.code, "message": e.message})
        self.json(200, {"ok": True, "slug": slug, "url": self.app.cfg.studio_url(slug)})

    def api_site_signup(self, q):
        self.limit("signup-try", 30, 3600)
        ok_key = f"signup-ok:{self.client_ip()}"
        if not self.app.limiter.peek(ok_key, self.app.cfg.signups_per_day, 86400):
            raise ApiError(429, "slow_down", "Too many sign-ups from this connection today. Write to us and we'll set you up.")
        data = self.body()
        if data.get("website"):  # a field people never see; only form-filling bots set it
            raise ApiError(400, "rejected", "That sign-up couldn't be completed.")
        slug, ticket = platform.create_studio(self.app.platform, data, self.app.cfg.pilot_days, self.client_ip())
        self.app.limiter.allow(ok_key, self.app.cfg.signups_per_day, 86400)
        url = self.app.cfg.studio_url(slug)
        self.json(201, {"slug": slug, "url": url, "desk": f"{url}/desk/#welcome={ticket}"})

    def admin(self):
        with self.app.platform.read() as c:
            row = auth.session_user(c, self.cookie_token())
        if not row:
            raise ApiError(401, "signed_out", "Sign in to continue.")
        return row

    def api_admin_login(self, q):
        data = self.body()
        email = str(data.get("email") or "").strip().lower()[:200]
        password = str(data.get("password") or "")[:500]
        self.limit("admin-login", 20, 900)
        fail_key = f"admin-fail:{email}"
        if not self.app.limiter.peek(fail_key, 5, 900):
            raise ApiError(429, "slow_down", "Too many wrong passwords. Wait 15 minutes and try again.")
        with self.app.platform.tx() as c:
            row = Store.user_by_email(c, email) if email else None
            if not auth.check_login(row, password):
                self.app.limiter.allow(fail_key, 5, 900)
                raise ApiError(401, "bad_login", "That email and password don't match.")
            token = auth.create_session(c, row["id"])
        self.json(200, {"email": row["email"], "name": row["name"]},
                  {"Set-Cookie": self.session_cookie(token, auth.SESSION_DAYS * 86400)})

    def api_admin_logout(self, q):
        with self.app.platform.tx() as c:
            auth.end_session(c, self.cookie_token())
        self.json(200, {"ok": True}, {"Set-Cookie": self.session_cookie("", 0)})

    def api_admin_me(self, q):
        row = self.admin()
        self.json(200, {"email": row["email"], "name": row["name"]})

    def api_admin_studios(self, q):
        self.admin()
        with self.app.platform.read() as c:
            rows = c.execute("SELECT * FROM studios ORDER BY created DESC").fetchall()
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for r in rows:
            item = {k: r[k] for k in ("slug", "name", "kind", "owner_name", "owner_email", "owner_phone", "lang", "status", "pilot_ends", "notes", "created")}
            item["url"] = self.app.cfg.studio_url(r["slug"])
            try:
                with self.app.store(r["slug"]).read() as sc:
                    count = lambda kind: sc.execute("SELECT COUNT(*) FROM docs WHERE kind = ? AND deleted = 0", (kind,)).fetchone()[0]
                    week = sc.execute(
                        "SELECT COUNT(*) AS n, COALESCE(SUM(json_extract(body, '$.source') = 'online'), 0) AS online FROM docs "
                        "WHERE kind = 'appointments' AND deleted = 0 AND json_extract(body, '$.createdAt') >= ?", (week_ago,)).fetchone()
                    item.update(staff=count("staff"), services=count("services"), clients=count("clients"),
                                bookings7=week["n"], online7=week["online"], lastSeen=Store.meta_get(sc, "last_seen"))
            except Exception as e:  # one broken file shouldn't hide the rest of the list
                item["problem"] = str(e)[:200]
            out.append(item)
        self.json(200, {"studios": out, "today": Date.today().isoformat()})

    def api_admin_studio_update(self, q, slug):
        self.admin()
        data = self.body()
        sets, args = [], []
        if "status" in data:
            if data["status"] not in ("pilot", "active", "paused"):
                raise ApiError(400, "bad_status", "Status must be pilot, active or paused.")
            sets.append("status = ?"); args.append(data["status"])
        if "pilotEnds" in data:
            v = data["pilotEnds"]
            if v is not None:
                booking.parse_date(v)
            sets.append("pilot_ends = ?"); args.append(v)
        if "notes" in data:
            if not isinstance(data["notes"], str) or len(data["notes"]) > 4000:
                raise ApiError(400, "bad_notes", "Notes must be text, at most 4,000 characters.")
            sets.append("notes = ?"); args.append(data["notes"])
        if not sets:
            raise ApiError(400, "nothing", "Nothing to change.")
        with self.app.platform.tx() as c:
            row = Platform.studio(c, slug)
            if not row:
                raise ApiError(404, "no_studio", "No studio with that address.")
            if row["status"] == "demo":
                raise ApiError(400, "demo", "The demo studio can't be changed here.")
            c.execute(f"UPDATE studios SET {', '.join(sets)} WHERE slug = ?", (*args, slug))
            row = Platform.studio(c, slug)
        self.json(200, {k: row[k] for k in ("slug", "status", "pilot_ends", "notes")})

    # ---------------------------------------------------------------- a studio: public
    def open_studio(self):
        if self.studio.status == "paused":
            raise ApiError(403, "paused", PAUSED)
        return self.studio

    def api_pub_studio(self, q):
        self.limit("studio", 300, 600)
        st = self.studio
        with st.store.read() as c:
            s = st.settings(c)
            if st.status == "paused":
                return self.json(200, {"paused": True, "name": s.get("name"), "phone": s.get("phone"), "i18n": s.get("i18n") or {},
                                       "defaultLang": s.get("defaultLang") if s.get("defaultLang") in booking.LANGS else "hy"})
            res = booking.public_studio(c, st.store, s, self.app.now(s))
        res["timezone"] = s.get("timezone") or self.app.cfg.timezone
        res["demo"] = st.status == "demo"
        self.json(200, res)

    def api_pub_availability(self, q):
        self.limit("avail", 300, 600)
        st = self.open_studio()
        services = [x for x in (q.get("services") or "").split(",") if x]
        ignore = None
        if q.get("link"):
            ignore = booking.parse_token(st.secret, q["link"])
        try:
            days = int(q.get("days") or 14)
        except ValueError:
            raise ApiError(400, "bad_days", "days must be a number.")
        with st.store.read() as c:
            s = st.settings(c)
            res = booking.availability(c, st.store, s, self.app.now(s), services, q.get("staff") or "any",
                                       q.get("from") or None, days, ignore_id=ignore, with_next=q.get("next") == "1")
        self.json(200, res)

    def api_pub_book(self, q):
        # Two limits: attempts (stops scripts hammering the form) and successful bookings (stops one
        # address filling the book). Typos don't count toward the second, so people behind a shared
        # office or mobile-carrier address aren't locked out by someone else's mistakes.
        st = self.open_studio()
        self.limit("book-try", 40, 600)
        ok_key = f"book-ok:{st.slug}:{self.client_ip()}"
        if not self.app.limiter.peek(ok_key, self.app.cfg.bookings_per_hour, 3600):
            raise ApiError(429, "slow_down", "Too many bookings from this connection. Try again later or call the studio.")
        data = self.body()
        if data.get("website"):
            raise ApiError(400, "rejected", "That booking couldn't be made.")
        view = booking.create_booking(st.store, st.secret, st.settings, self.app.now, data)
        self.app.limiter.allow(ok_key, self.app.cfg.bookings_per_hour, 3600)
        self.json(201, view)

    def api_pub_get(self, q, token):
        self.limit("manage", 120, 600)
        st = self.studio  # a client can still see a booking while booking is paused
        appt_id = booking.parse_token(st.secret, token)
        with st.store.read() as c:
            appt = st.store.get(c, "appointments", appt_id)
            if not appt:
                raise ApiError(404, "bad_link", "This booking no longer exists.")
            s = st.settings(c)
            self.json(200, booking.booking_view(c, st.store, s, self.app.now(s), appt, token))

    def api_pub_cancel(self, q, token):
        self.limit("change", 20, 3600)
        st = self.studio  # cancelling is allowed while paused: it only frees the studio's time
        self.json(200, booking.cancel_booking(st.store, st.secret, st.settings, self.app.now, token))

    def api_pub_reschedule(self, q, token):
        self.limit("change", 20, 3600)
        st = self.open_studio()
        data = self.body()
        self.json(200, booking.reschedule_booking(st.store, st.secret, st.settings, self.app.now, token, data))

    # ---------------------------------------------------------------- a studio: staff sign-in
    def me_view(self, row) -> dict:
        r = self.studio.row
        return {"email": row["email"], "name": row["name"], "role": row["role"],
                "studio": {"slug": r["slug"], "status": r["status"], "pilotEnds": r["pilot_ends"],
                           "bookingUrl": self.app.cfg.studio_url(r["slug"]), "site": self.app.cfg.site_url,
                           "contactPhone": self.app.cfg.contact_phone, "contactEmail": self.app.cfg.contact_email}}

    def paused_error(self) -> ApiError:
        contact = " or ".join(x for x in (self.app.cfg.contact_phone, self.app.cfg.contact_email) if x)
        return ApiError(403, "paused", "This studio's account is paused. Contact Rota" + (f" ({contact})" if contact else "") + " to turn it back on.")

    def user(self):
        st = self.studio
        with st.store.read() as c:
            row = auth.session_user(c, self.cookie_token())
        if not row:
            raise ApiError(401, "signed_out", "Sign in to continue.")
        if st.status == "paused":
            raise self.paused_error()
        return row

    def api_login(self, q):
        data = self.body()
        email = str(data.get("email") or "").strip().lower()[:200]
        password = str(data.get("password") or "")[:500]
        # Per address, every attempt counts; per account, only failures do, so staff signing in on
        # several devices never lock themselves out, but guessing one account's password stops fast.
        self.limit("login-ip", 30, 900)
        fail_key = f"login-fail:{self.studio.slug}:{email}"
        if not self.app.limiter.peek(fail_key, 8, 900):
            raise ApiError(429, "slow_down", "Too many wrong passwords. Wait 15 minutes and try again.")
        with self.studio.store.tx() as c:
            row = Store.user_by_email(c, email) if email else None
            if not auth.check_login(row, password):
                self.app.limiter.allow(fail_key, 8, 900)
                raise ApiError(401, "bad_login", "That email and password don't match.")
            if self.studio.status == "paused":
                raise self.paused_error()
            token = auth.create_session(c, row["id"])
        self.signed_in(token, row)

    def api_ticket(self, q):
        # The one-time link a new owner follows from the sign-up page into their desk.
        self.limit("ticket", 30, 900)
        data = self.body()
        with self.studio.store.tx() as c:
            uid = platform.use_ticket(c, data.get("ticket"))
            row = Store.user_by_id(c, uid) if uid else None
            if not row:
                raise ApiError(401, "bad_ticket", "This sign-in link has expired. Sign in with your email and password.")
            token = auth.create_session(c, row["id"])
        self.signed_in(token, row)

    def api_demo_login(self, q):
        if self.studio.status != "demo":
            raise ApiError(404, "not_found", "No such endpoint.")
        self.limit("demo", 30, 3600)
        with self.studio.store.tx() as c:
            row = c.execute("SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").fetchone()
            if not row:
                raise ApiError(503, "demo_off", "The demo is being reset. Try again in a minute.")
            token = auth.create_session(c, row["id"])
        self.signed_in(token, row)

    def api_logout(self, q):
        with self.studio.store.tx() as c:
            auth.end_session(c, self.cookie_token())
        self.json(200, {"ok": True}, {"Set-Cookie": self.session_cookie("", 0)})

    def api_me(self, q):
        self.json(200, self.me_view(self.user()))

    def api_password(self, q):
        row = self.user()
        self.limit("password", 10, 900, key=str(row["id"]))
        data = self.body()
        with self.studio.store.tx() as c:
            if not auth.check_login(row, str(data.get("current") or "")[:500]):
                raise ApiError(401, "bad_password", "Your current password isn't right.", field="current")
            pw = _new_password(data.get("password"))
            c.execute("UPDATE users SET pw = ? WHERE id = ?", (auth.hash_password(pw), row["id"]))
            # Every other device signs in again; this one stays signed in.
            c.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (row["id"], auth.token_hash(self.cookie_token() or "")))
        self.json(200, {"ok": True})

    # ---------------------------------------------------------------- a studio: who can sign in
    def owner(self):
        row = self.user()
        if row["role"] != "owner":
            raise ApiError(403, "owner_only", "Only the studio owner can manage sign-ins.")
        return row

    def api_users(self, q):
        me = self.owner()
        with self.studio.store.read() as c:
            rows = c.execute("SELECT id, email, name, role, created FROM users ORDER BY role DESC, id").fetchall()
        self.json(200, {"users": [{**dict(r), "you": r["id"] == me["id"]} for r in rows]})

    def api_user_add(self, q):
        self.owner()
        self.limit("user-add", 30, 3600)
        data = self.body()
        email = str(data.get("email") or "").strip().lower()
        if not platform.EMAIL_RE.match(email) or len(email) > 200:
            raise ApiError(400, "bad_email", "Enter an email like name@example.com.", field="email")
        name = str(data.get("name") or "").strip()[:80]
        role = data.get("role") if data.get("role") in ("owner", "staff") else "staff"
        pw = _new_password(data.get("password"))
        with self.studio.store.tx() as c:
            if Store.user_by_email(c, email):
                raise ApiError(409, "email_taken", "Someone at this studio already signs in with that email.", field="email")
            c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, ?, ?, ?)", (email, name, auth.hash_password(pw), role))
        self.json(201, {"ok": True})

    def _other_user(self, c, uid):
        me = self.owner()
        row = Store.user_by_id(c, int(uid))
        if not row:
            raise ApiError(404, "no_user", "That person no longer has a sign-in.")
        if row["id"] == me["id"]:
            raise ApiError(400, "yourself", "Use Change your password for your own account.")
        return row

    def api_user_password(self, q, uid):
        data = self.body()
        with self.studio.store.tx() as c:
            row = self._other_user(c, uid)
            pw = _new_password(data.get("password"))
            c.execute("UPDATE users SET pw = ? WHERE id = ?", (auth.hash_password(pw), row["id"]))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        self.json(200, {"ok": True})

    def api_user_remove(self, q, uid):
        with self.studio.store.tx() as c:
            row = self._other_user(c, uid)
            c.execute("DELETE FROM users WHERE id = ?", (row["id"],))
        self.json(200, {"ok": True})

    # ---------------------------------------------------------------- a studio: desk
    def api_desk_sync(self, q):
        self.user()
        st = self.studio
        try:
            since = max(0, int(q.get("rev") or 0))
        except ValueError:
            raise ApiError(400, "bad_rev", "rev must be a number.")
        with st.store.read() as c:
            rev, rows = Store.changes(c, since)
            s = st.settings(c)
            now = self.app.now(s)
        for r in rows:
            if r["kind"] == "appointments" and not r["deleted"]:
                r["body"]["manage"] = booking.manage_token(st.secret, r["id"])
        self.app.seen(st)
        self.json(200, {"rev": rev, "rows": rows, "today": now.date().isoformat(), "nowMin": now.hour * 60 + now.minute})

    def api_desk_export(self, q, what):
        row = self.user()
        if row["role"] != "owner":
            raise ApiError(403, "owner_only", "Only the studio owner can download the studio's data.")
        self.limit("export", 30, 3600)
        st = self.studio
        with st.store.read() as c:
            clients = st.store.all(c, "clients")
            appts = st.store.all(c, "appointments")
            staff = {x["id"]: x.get("name", "") for x in st.store.all(c, "staff")}
            services = {x["id"]: x.get("name", "") for x in st.store.all(c, "services")}
        out = io.StringIO()
        w = csv.writer(out)
        if what == "clients":
            visits: dict = {}
            for a in appts:
                if a.get("status") != "cancelled":
                    v = visits.setdefault(a.get("clientId"), [0, ""])
                    v[0] += 1
                    v[1] = max(v[1], a.get("date") or "")
            w.writerow(["Name", "Phone", "Email", "Notes", "Visits", "Last visit", "Added", "Source", "Language"])
            for cl in sorted(clients, key=lambda x: x.get("name", "").lower()):
                v = visits.get(cl["id"], [0, ""])
                w.writerow([_cell(cl.get(k)) for k in ("name", "phone", "email", "notes")] + [v[0], v[1]]
                           + [_cell(cl.get(k)) for k in ("createdAt", "source", "lang")])
        else:
            w.writerow(["Date", "Time", "Minutes", "Specialist", "Services", "Client", "Phone", "Price", "Paid", "Status", "Source", "Note", "Booked at"])
            for a in sorted(appts, key=lambda x: (x.get("date", ""), x.get("start", 0))):
                start = int(a.get("start") or 0)
                w.writerow([a.get("date"), f"{start // 60:02d}:{start % 60:02d}", a.get("duration"), _cell(staff.get(a.get("staffId"), "")),
                            _cell(", ".join(services.get(i, "") for i in a.get("serviceIds") or [])), _cell(a.get("clientName")),
                            _cell(a.get("clientPhone")), a.get("price"), a.get("paid"), a.get("status"), a.get("source"),
                            _cell(a.get("note")), a.get("createdAt")])
        name = f"rota-{st.slug}-{what}-{Date.today().isoformat()}.csv"
        # A byte-order mark so Excel opens Armenian and Russian text correctly.
        self._send(200, ("﻿" + out.getvalue()).encode(), "text/csv; charset=utf-8",
                   {"Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="{name}"'})

    def _desk_target(self, kind, doc_id, method):
        row = self.user()
        if kind not in KINDS:
            raise ApiError(404, "bad_kind", "Unknown kind of record.")
        if not booking.ID_RE.match(doc_id) or (kind == "settings" and doc_id != "studio"):
            raise ApiError(400, "bad_id", "Invalid record id.")
        if (method, kind) in OWNER_ONLY and row["role"] != "owner":
            raise ApiError(403, "owner_only", "Only the studio owner can change this.")
        self.limit("desk-write", 600, 600, key=str(row["id"]))
        return row

    def api_desk_put(self, q, kind, id):
        self._desk_target(kind, id, "PUT")
        doc = booking.clean_doc(kind, self.body())
        with self.studio.store.tx() as c:
            rev = self.studio.store.put(c, kind, id, doc)
        self.json(200, {"rev": rev})

    def api_desk_delete(self, q, kind, id):
        self._desk_target(kind, id, "DELETE")
        with self.studio.store.tx() as c:
            rev = self.studio.store.delete(c, kind, id)
        self.json(200, {"rev": rev})


def _new_password(v) -> str:
    if not isinstance(v, str) or len(v) < 10:
        raise ApiError(400, "short_password", "Use at least 10 characters.", field="password")
    if len(v) > 200:
        raise ApiError(400, "long_password", "That password is too long.", field="password")
    return v


def _cell(v) -> str:
    """Text for a spreadsheet cell. Clients type their own names on the booking site, so a value
    that a spreadsheet would run as a formula gets a leading apostrophe."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return  # the client went away; nothing to report
        super().handle_error(request, client_address)


def make_server(cfg: configmod.Config):
    app = App(cfg)
    handler = type("RotaHandler", (Handler,), {"app": app})
    srv = Server((cfg.host, cfg.port), handler)
    srv.daemon_threads = True
    return srv, app


def main():
    cfg = configmod.from_env()
    if cfg.host not in ("127.0.0.1", "localhost", "::1") and not cfg.trust_proxy:
        print("warning: listening beyond this machine without ROTA_TRUST_PROXY. Put Caddy in front (DEPLOY.md).", file=sys.stderr)
    srv, app = make_server(cfg)
    with app.platform.read() as c:
        admins = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        studios = c.execute("SELECT COUNT(*) FROM studios").fetchone()[0]
    print(f"Rota {__version__} on http://{cfg.host}:{cfg.port}  sales site {cfg.site_url}, "
          f"{studios} studio(s), data in {cfg.data_dir}", file=sys.stderr)
    if not admins:
        print("No Rota admin yet. Create one with: python manage.py create-admin you@example.com", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
