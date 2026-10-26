#!/usr/bin/env python3
"""Rota server: the public booking site, the staff desk, and the JSON API behind both.

  GET  /                          booking site (also /b/<link> for a client's manage link)
  GET  /desk/                     staff desk
  GET  /api/public/studio         studio, services and team as clients see them
  GET  /api/public/availability   free times; never names or phone numbers
  POST /api/public/bookings       book; returns the booking and its private manage link
  GET  /api/public/bookings/<t>   one booking, for whoever holds its link
  POST /api/public/bookings/<t>/cancel | /reschedule
  POST /api/auth/login | /logout, GET /api/auth/me
  GET  /api/desk/sync?rev=N       everything changed since revision N (staff only)
  PUT|DELETE /api/desk/<kind>/<id>

Standard library only. It is a ThreadingHTTPServer, which is fine for one studio but not built to
face the internet alone: run it on 127.0.0.1 behind nginx, which adds TLS, request limits and slow-
client timeouts. See DEPLOY.md.

    python server.py            # http://127.0.0.1:8780
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import sys
import traceback
from datetime import datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from rota import __version__, auth, booking, config as configmod
from rota.store import KINDS, Store

MAX_BODY = 64 * 1024
COOKIE = "rota_session"
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


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class App:
    def __init__(self, cfg: configmod.Config):
        self.cfg = cfg
        self.store = Store(cfg.db_path)
        self.limiter = auth.RateLimiter()
        self.secret = cfg.secret or self._stored_secret()

    def _stored_secret(self) -> str:
        with self.store.tx() as c:
            v = Store.meta_get(c, "secret")
            if not v:
                v = secrets.token_urlsafe(32)
                Store.meta_set(c, "secret", v)
            return v

    def settings(self, c) -> dict:
        return booking.settings(c, self.store)

    def now(self, s: dict) -> datetime:
        return datetime.now(booking.studio_tz(s, self.cfg.timezone))


ROUTES = [
    ("GET", r"/api/health", "health"),
    ("GET", r"/api/public/studio", "pub_studio"),
    ("GET", r"/api/public/availability", "pub_availability"),
    ("POST", r"/api/public/bookings", "pub_book"),
    ("GET", r"/api/public/bookings/(?P<token>[^/]+)", "pub_get"),
    ("POST", r"/api/public/bookings/(?P<token>[^/]+)/cancel", "pub_cancel"),
    ("POST", r"/api/public/bookings/(?P<token>[^/]+)/reschedule", "pub_reschedule"),
    ("POST", r"/api/auth/login", "login"),
    ("POST", r"/api/auth/logout", "logout"),
    ("GET", r"/api/auth/me", "me"),
    ("GET", r"/api/desk/sync", "desk_sync"),
    ("PUT", r"/api/desk/(?P<kind>[a-z]+)/(?P<id>[^/]+)", "desk_put"),
    ("DELETE", r"/api/desk/(?P<kind>[a-z]+)/(?P<id>[^/]+)", "desk_delete"),
]
ROUTES = [(m, re.compile(p + "$"), h) for m, p, h in ROUTES]


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by make_server
    protocol_version = "HTTP/1.1"
    server_version = "Rota"
    sys_version = ""
    timeout = 30

    # ---------------------------------------------------------------- plumbing
    def log_request(self, code="-", size="-"):
        path = re.sub(r"/(bookings|b)/[^/?]+", r"/\1/…", self.path.split("?")[0])[:120]  # manage links are secrets
        sys.stderr.write(f"{self.client_ip()} {self.command} {path} {code}\n")

    def log_error(self, fmt, *args):
        pass

    def client_ip(self) -> str:
        if self.app.cfg.trust_proxy:
            ip = (self.headers.get("X-Real-IP") or "").strip()
            if ip:
                return ip
        return self.client_address[0]

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
        # A custom header can't be added by a plain cross-site form, and we never allow CORS,
        # so requiring it (plus SameSite cookies) keeps other sites from posting on a user's behalf.
        if self.headers.get("X-Rota") != "1":
            raise ApiError(403, "missing_header", "Requests must come from the Rota app.")
        origin = self.headers.get("Origin")
        if origin and self.app.cfg.origin and origin.rstrip("/") != self.app.cfg.origin:
            raise ApiError(403, "bad_origin", "Requests must come from this site.")

    def limit(self, name: str, limit: int, window: float, key: str | None = None):
        if not self.app.limiter.allow(f"{name}:{key or self.client_ip()}", limit, window):
            raise ApiError(429, "slow_down", "Too many requests. Wait a few minutes and try again.")

    def cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        try:
            jar = SimpleCookie(raw)
        except CookieError:
            return None
        m = jar.get(COOKIE)
        return m.value if m else None

    def session_cookie(self, token: str, max_age: int) -> str:
        parts = [f"{COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self.app.cfg.secure_cookies:
            parts.append("Secure")
        return "; ".join(parts)

    def user(self, required=True):
        with self.app.store.read() as c:
            row = auth.session_user(c, self.cookie_token())
        if not row and required:
            raise ApiError(401, "signed_out", "Sign in to continue.")
        return row

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
        try:
            if path.startswith("/api/"):
                for m, rx, name in ROUTES:
                    match = rx.match(path)
                    if match and m == method:
                        if method != "GET":
                            self.check_write_origin()
                            self.read_raw()
                        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                        return getattr(self, "api_" + name)(q, **match.groupdict())
                raise ApiError(404, "not_found", "No such endpoint.")
            if method != "GET":
                raise ApiError(405, "method", "Method not allowed.")
            return self.static(path, head)
        except (ApiError, booking.BookingError) as e:
            if method != "GET":
                self.close_connection = True  # the body may be unread; don't parse it as the next request
            self.json(e.status, {"error": e.code, "message": e.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.json(500, {"error": "server", "message": "Something went wrong on our side. Try again in a minute."})
            except Exception:
                pass

    # ---------------------------------------------------------------- static files
    def static(self, path: str, head: bool):
        root = self.app.cfg.web_root
        if path in ("/", "/index.html") or path.startswith("/b/"):
            return self.file(os.path.join(root, "book", "index.html"), head, html=True)
        if path == "/desk":
            return self._send(301, b"", "text/plain", {"Location": "/desk/"}, head)
        if path == "/desk/":
            return self.file(os.path.join(root, "desk", "index.html"), head, html=True)
        if path == "/favicon.svg":
            return self.file(os.path.join(root, "shared", "favicon.svg"), head)
        if path.startswith("/assets/"):
            full = os.path.normpath(os.path.join(root, path[len("/assets/"):]))
            if full.startswith(os.path.normpath(root) + os.sep) and os.path.isfile(full):
                return self.file(full, head)
        self._send(404, "Not found".encode(), "text/plain; charset=utf-8", {"Cache-Control": "no-store"}, head)

    def file(self, full: str, head: bool, html=False):
        with open(full, "rb") as f:
            data = f.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        cache = "no-cache" if html else "public, max-age=300"
        self._send(200, data, ctype, {"Cache-Control": cache}, head)

    # ---------------------------------------------------------------- public API
    def api_health(self, q):
        with self.app.store.read() as c:
            Store.current_rev(c)
        self.json(200, {"ok": True, "version": __version__})

    def api_pub_studio(self, q):
        self.limit("studio", 300, 600)
        with self.app.store.read() as c:
            s = self.app.settings(c)
            res = booking.public_studio(c, self.app.store, s, self.app.now(s))
        res["timezone"] = s.get("timezone") or self.app.cfg.timezone
        self.json(200, res)

    def api_pub_availability(self, q):
        self.limit("avail", 300, 600)
        services = [x for x in (q.get("services") or "").split(",") if x]
        ignore = None
        if q.get("link"):
            ignore = booking.parse_token(self.app.secret, q["link"])
        try:
            days = int(q.get("days") or 14)
        except ValueError:
            raise ApiError(400, "bad_days", "days must be a number.")
        with self.app.store.read() as c:
            s = self.app.settings(c)
            res = booking.availability(c, self.app.store, s, self.app.now(s), services, q.get("staff") or "any",
                                       q.get("from") or None, days, ignore_id=ignore, with_next=q.get("next") == "1")
        self.json(200, res)

    def api_pub_book(self, q):
        # Two limits: attempts (stops scripts hammering the form) and successful bookings (stops one
        # address filling the book). Typos don't count toward the second, so people behind a shared
        # office or mobile-carrier address aren't locked out by someone else's mistakes.
        self.limit("book-try", 40, 600)
        ok_key = f"book-ok:{self.client_ip()}"
        if not self.app.limiter.peek(ok_key, self.app.cfg.bookings_per_hour, 3600):
            raise ApiError(429, "slow_down", "Too many bookings from this connection. Try again later or call the studio.")
        data = self.body()
        if data.get("website"):  # a field people never see; only form-filling bots set it
            raise ApiError(400, "rejected", "That booking couldn't be made.")
        view = booking.create_booking(self.app.store, self.app.secret, self.app.settings, self.app.now, data)
        self.app.limiter.allow(ok_key, self.app.cfg.bookings_per_hour, 3600)
        self.json(201, view)

    def api_pub_get(self, q, token):
        self.limit("manage", 120, 600)
        appt_id = booking.parse_token(self.app.secret, token)
        with self.app.store.read() as c:
            appt = self.app.store.get(c, "appointments", appt_id)
            if not appt:
                raise ApiError(404, "bad_link", "This booking no longer exists.")
            s = self.app.settings(c)
            self.json(200, booking.booking_view(c, self.app.store, s, self.app.now(s), appt, token))

    def api_pub_cancel(self, q, token):
        self.limit("change", 20, 3600)
        self.json(200, booking.cancel_booking(self.app.store, self.app.secret, self.app.settings, self.app.now, token))

    def api_pub_reschedule(self, q, token):
        self.limit("change", 20, 3600)
        data = self.body()
        self.json(200, booking.reschedule_booking(self.app.store, self.app.secret, self.app.settings, self.app.now, token, data))

    # ---------------------------------------------------------------- staff sign-in
    def api_login(self, q):
        data = self.body()
        email = str(data.get("email") or "").strip().lower()[:200]
        password = str(data.get("password") or "")[:500]
        # Per address, every attempt counts; per account, only failures do, so staff signing in on
        # several devices never lock themselves out, but guessing one account's password stops fast.
        self.limit("login-ip", 30, 900)
        fail_key = f"login-fail:{email}"
        if not self.app.limiter.peek(fail_key, 8, 900):
            raise ApiError(429, "slow_down", "Too many wrong passwords. Wait 15 minutes and try again.")
        with self.app.store.tx() as c:
            row = Store.user_by_email(c, email) if email else None
            if not auth.check_login(row, password):
                self.app.limiter.allow(fail_key, 8, 900)
                raise ApiError(401, "bad_login", "That email and password don't match.")
            token = auth.create_session(c, row["id"])
        self.json(200, {"email": row["email"], "name": row["name"], "role": row["role"]},
                  {"Set-Cookie": self.session_cookie(token, auth.SESSION_DAYS * 86400)})

    def api_logout(self, q):
        with self.app.store.tx() as c:
            auth.end_session(c, self.cookie_token())
        self.json(200, {"ok": True}, {"Set-Cookie": self.session_cookie("", 0)})

    def api_me(self, q):
        row = self.user()
        self.json(200, {"email": row["email"], "name": row["name"], "role": row["role"]})

    # ---------------------------------------------------------------- desk API
    def api_desk_sync(self, q):
        self.user()
        try:
            since = max(0, int(q.get("rev") or 0))
        except ValueError:
            raise ApiError(400, "bad_rev", "rev must be a number.")
        with self.app.store.read() as c:
            rev, rows = Store.changes(c, since)
            s = self.app.settings(c)
            now = self.app.now(s)
        for r in rows:
            if r["kind"] == "appointments" and not r["deleted"]:
                r["body"]["manage"] = booking.manage_token(self.app.secret, r["id"])
        self.json(200, {"rev": rev, "rows": rows, "today": now.date().isoformat(), "nowMin": now.hour * 60 + now.minute})

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
        with self.app.store.tx() as c:
            rev = self.app.store.put(c, kind, id, doc)
        self.json(200, {"rev": rev})

    def api_desk_delete(self, q, kind, id):
        self._desk_target(kind, id, "DELETE")
        with self.app.store.tx() as c:
            rev = self.app.store.delete(c, kind, id)
        self.json(200, {"rev": rev})


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
        print("warning: listening beyond this machine without ROTA_TRUST_PROXY. Put nginx in front (DEPLOY.md).", file=sys.stderr)
    srv, app = make_server(cfg)
    with app.store.read() as c:
        users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    print(f"Rota {__version__} on http://{cfg.host}:{cfg.port}  (database {cfg.db_path})", file=sys.stderr)
    if not users:
        print("No staff accounts yet. Create one with: python manage.py create-user you@example.com --role owner", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
