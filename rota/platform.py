"""Rota's own records: the companies that signed up, and Rota's admins.

data/platform.db holds the list of studios (address, owner, pilot dates, status) and the admin
accounts that see that list. Each studio's own records live in data/studios/<address>.db.

The platform database reuses the studio schema's `users` and `sessions` tables for Rota's
admins, so the same sign-in code serves both.
"""
from __future__ import annotations

import os
import re
import secrets
import time
from datetime import date as Date, datetime, timedelta, timezone

from rota import auth, booking, sample, templates
from rota.store import Db, Store

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL DEFAULT '',
  pw TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'admin',
  created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL,
  expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS studios (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'other',
  owner_name TEXT NOT NULL DEFAULT '',
  owner_email TEXT NOT NULL DEFAULT '',
  owner_phone TEXT NOT NULL DEFAULT '',
  lang TEXT NOT NULL DEFAULT 'hy',
  status TEXT NOT NULL DEFAULT 'pilot',
  pilot_ends TEXT,
  notes TEXT NOT NULL DEFAULT '',
  created TEXT NOT NULL,
  signup_ip TEXT NOT NULL DEFAULT ''
);
"""

STATUSES = ("pilot", "active", "paused", "demo")
DEMO = "demo"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,28})[a-z0-9]$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
# Addresses a studio can't take: Rota's own, and ones that would confuse people or mail.
RESERVED = set("""
www api app admin administrator mail email smtp imap pop ftp ns ns1 ns2 dns mx desk demo help support
status static assets cdn blog docs rota book booking bookings start signup sign-up login logout account
accounts billing pay payment payments dashboard test dev staging root about team info news shop store
security abuse postmaster webmail m mobile img images media files download downloads legal privacy terms
""".split())
TICKET_MINUTES = 15


class SignupError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, field: str | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.field = code, message, status, field


class Platform(Db):
    schema = PLATFORM_SCHEMA

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.studios_dir = os.path.join(data_dir, "studios")
        os.makedirs(self.studios_dir, exist_ok=True)
        super().__init__(os.path.join(data_dir, "platform.db"))

    def studio_path(self, slug: str) -> str:
        return os.path.join(self.studios_dir, f"{slug}.db")

    @staticmethod
    def studio(c, slug: str):
        return c.execute("SELECT * FROM studios WHERE slug = ?", (slug,)).fetchone()

    def secret(self, configured: str | None) -> str:
        if configured:
            return configured
        with self.tx() as c:
            v = self.meta_get(c, "secret")
            if not v:
                v = secrets.token_urlsafe(32)
                self.meta_set(c, "secret", v)
            return v


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_slug(platform: Platform, slug) -> str:
    """The cleaned address, or a SignupError saying why it can't be used."""
    slug = (slug if isinstance(slug, str) else "").strip().lower()
    if not SLUG_RE.match(slug) or "--" in slug:
        raise SignupError("bad_slug", "Use 3 to 30 Latin letters, digits or hyphens.", field="slug")
    if slug in RESERVED:
        raise SignupError("slug_taken", "That address is taken. Try another.", 409, field="slug")
    with platform.read() as c:
        if Platform.studio(c, slug) or os.path.exists(platform.studio_path(slug)):
            raise SignupError("slug_taken", "That address is taken. Try another.", 409, field="slug")
    return slug


def _text(v, limit: int, field: str, label: str, minimum: int = 1) -> str:
    v = (v if isinstance(v, str) else "").strip()
    if len(v) < minimum:
        raise SignupError(f"missing_{field}", f"Enter {label}.", field=field)
    if len(v) > limit:
        raise SignupError(f"long_{field}", f"That {label} is too long.", field=field)
    return v


def create_studio(platform: Platform, data: dict, pilot_days: int, ip: str = "", today: Date | None = None) -> tuple[str, str]:
    """Create a company from the sign-up form. Returns (address, one-time sign-in token)."""
    # Checked in the order the sign-up form shows the fields, so the first error is the first field.
    business = _text(data.get("business"), 80, "business", "your business name", 2)
    slug = check_slug(platform, data.get("slug"))
    name = _text(data.get("name"), 80, "name", "your name")
    email = _text(data.get("email"), 200, "email", "your email").lower()
    if not EMAIL_RE.match(email):
        raise SignupError("bad_email", "Enter an email like name@example.com.", field="email")
    phone = _text(data.get("phone"), 30, "phone", "a phone number")
    if not 8 <= len(re.sub(r"\D", "", phone)) <= 15:
        raise SignupError("bad_phone", "Enter a full phone number, like +374 91 234 567.", field="phone")
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if len(password) < 10:
        raise SignupError("short_password", "Use at least 10 characters.", field="password")
    if len(password) > 200:
        raise SignupError("long_password", "That password is too long.", field="password")
    kind = data.get("kind") if data.get("kind") in templates.KINDS else "other"
    lang = data.get("lang") if data.get("lang") in booking.LANGS else "hy"
    today = today or Date.today()

    # The platform write lock is held while the studio file is made, so two people asking for the
    # same address at once can't both get it.
    path = platform.studio_path(slug)
    try:
        with platform.tx() as pc:
            if Platform.studio(pc, slug) or os.path.exists(path):
                raise SignupError("slug_taken", "That address is taken. Try another.", 409, field="slug")
            store = Store(path)
            with store.tx() as c:
                settings = {**{k: v for k, v in booking.DEFAULT_SETTINGS.items() if k != "timezone"},
                            "name": business, "phone": phone, "defaultLang": lang, "timezone": "Asia/Yerevan"}
                store.put(c, "settings", "studio", settings)
                for k, items in templates.starter(kind, name).items():
                    for doc_id, body in items.items():
                        store.put(c, k, doc_id, body)
                c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, ?, ?, 'owner')",
                          (email, name, auth.hash_password(password)))
                uid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                token = new_ticket(c, uid)
            pc.execute(
                "INSERT INTO studios (slug, name, kind, owner_name, owner_email, owner_phone, lang, status, pilot_ends, created, signup_ip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pilot', ?, ?, ?)",
                (slug, business, kind, name, email, phone, lang, (today + timedelta(days=pilot_days)).isoformat(), _utc_now(), ip),
            )
    except SignupError:
        raise
    except BaseException:
        remove_files(path)
        raise
    return slug, token


def new_ticket(c, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    c.execute("DELETE FROM tickets WHERE expires < ?", (time.time(),))
    c.execute("INSERT INTO tickets (token_hash, user_id, expires) VALUES (?, ?, ?)",
              (auth.token_hash(token), user_id, time.time() + TICKET_MINUTES * 60))
    return token


def use_ticket(c, token) -> int | None:
    """The user a one-time sign-in link belongs to, and the link is spent."""
    if not isinstance(token, str) or not 20 <= len(token) <= 200:
        return None
    h = auth.token_hash(token)
    row = c.execute("SELECT user_id FROM tickets WHERE token_hash = ? AND expires > ?", (h, time.time())).fetchone()
    c.execute("DELETE FROM tickets WHERE token_hash = ?", (h,))
    return row["user_id"] if row else None


def remove_files(path: str) -> None:
    for p in (path, path + "-wal", path + "-shm"):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def load_sample(store: Store, c, today: Date, now_min: int) -> int:
    """Replace a studio's records with the Atelier Nairi sample. Returns the number of records."""
    for row in c.execute("SELECT kind, id FROM docs WHERE deleted = 0").fetchall():
        store.delete(c, row["kind"], row["id"])
    n = 0
    for kind, items in sample.build(today, now_min).items():
        for doc_id, body in items.items():
            store.put(c, kind, doc_id, body)
            n += 1
    return n


def reset_demo(platform: Platform, fallback_tz: str) -> int:
    """Create or refresh the public demo studio: fresh sample data, everyone signed out."""
    with platform.tx() as pc:
        if not Platform.studio(pc, DEMO):
            pc.execute("INSERT INTO studios (slug, name, kind, owner_name, status, created) VALUES (?, ?, 'hair', 'Demo', 'demo', ?)",
                       (DEMO, sample.SETTINGS["name"], _utc_now()))
    store = Store(platform.studio_path(DEMO))
    now = datetime.now(booking.studio_tz({"timezone": sample.SETTINGS["timezone"]}, fallback_tz))
    with store.tx() as c:
        # Visitors are signed out, so every desk starts again from a full sync and nobody keeps a
        # stale copy; the records themselves are replaced outright rather than left as tombstones.
        c.execute("DELETE FROM sessions")
        c.execute("DELETE FROM tickets")
        c.execute("DELETE FROM docs")
        n = 0
        for kind, items in sample.build(now.date(), now.hour * 60 + now.minute).items():
            for doc_id, body in items.items():
                store.put(c, kind, doc_id, body)
                n += 1
        if not c.execute("SELECT 1 FROM users WHERE role = 'owner'").fetchone():
            c.execute("INSERT INTO users (email, name, pw, role) VALUES ('demo@rota.invalid', 'Demo owner', ?, 'owner')",
                      (auth.hash_password(secrets.token_urlsafe(24)),))
    return n
