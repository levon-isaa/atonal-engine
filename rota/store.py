"""SQLite storage.

Every company has its own database file (data/studios/<address>.db), so one studio's records can
never show up in another's, and a company can be exported, backed up or removed as one file.
Rota's own records (the list of companies, Rota's admins) live in data/platform.db; see platform.py.

Studio data (settings, staff, services, clients, appointments) lives in one `docs` table as JSON
documents, each stamped with a revision number from a single counter. The desk syncs by asking for
everything newer than the last revision it saw, so a deleted document stays behind as a tombstone
(deleted = 1, empty body) until the desk has heard about it.

Every write goes through `tx()`, which opens the transaction with BEGIN IMMEDIATE. SQLite then lets
one writer in at a time, which is what makes "check the slot is free, then book it" safe when two
clients pick the same time in the same second.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

KINDS = ("settings", "staff", "services", "clients", "appointments")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs (
  kind TEXT NOT NULL,
  id TEXT NOT NULL,
  body TEXT NOT NULL,
  rev INTEGER NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS docs_rev ON docs(rev);
CREATE INDEX IF NOT EXISTS docs_day ON docs(kind, json_extract(body, '$.date'));
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT NOT NULL DEFAULT '',
  pw TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff',
  created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created REAL NOT NULL,
  expires REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expires ON sessions(expires);
-- One-time sign-in links, handed out right after a company signs up on the sales site.
CREATE TABLE IF NOT EXISTS tickets (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires REAL NOT NULL
);
"""


class Db:
    """One SQLite file: short-lived connections, WAL, and BEGIN IMMEDIATE write transactions."""

    schema = ""

    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self._init_lock = threading.Lock()
        with self._init_lock:
            c = self.connect()
            try:
                c.execute("PRAGMA journal_mode=WAL")
                c.executescript(self.schema)
                self.init(c)
            finally:
                c.close()

    def init(self, c) -> None:
        pass

    def connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        return c

    @contextmanager
    def tx(self):
        """A write transaction. Only one runs at a time across the whole database."""
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            c.execute("COMMIT")
        finally:
            c.close()

    @contextmanager
    def read(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()

    # ---------------------------------------------------------------- meta
    @staticmethod
    def meta_get(c, k: str) -> str | None:
        row = c.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return row["v"] if row else None

    @staticmethod
    def meta_set(c, k: str, v: str) -> None:
        c.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))

    # ---------------------------------------------------------------- users (studio staff, or Rota's admins in platform.db)
    @staticmethod
    def user_by_email(c, email: str):
        return c.execute("SELECT * FROM users WHERE email = ?", (email.strip(),)).fetchone()

    @staticmethod
    def user_by_id(c, uid: int):
        return c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


class Store(Db):
    """One studio's records."""

    schema = SCHEMA

    def init(self, c) -> None:
        c.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('rev', '0')")
        c.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('schema', '2')")

    @staticmethod
    def _next_rev(c) -> int:
        c.execute("UPDATE meta SET v = CAST(v AS INTEGER) + 1 WHERE k = 'rev'")
        return int(c.execute("SELECT v FROM meta WHERE k = 'rev'").fetchone()["v"])

    @staticmethod
    def current_rev(c) -> int:
        return int(c.execute("SELECT v FROM meta WHERE k = 'rev'").fetchone()["v"])

    # ---------------------------------------------------------------- documents
    def put(self, c, kind: str, doc_id: str, body: dict) -> int:
        assert kind in KINDS, kind
        rev = self._next_rev(c)
        c.execute(
            "INSERT INTO docs (kind, id, body, rev, deleted) VALUES (?, ?, ?, ?, 0) "
            "ON CONFLICT(kind, id) DO UPDATE SET body = excluded.body, rev = excluded.rev, deleted = 0",
            (kind, doc_id, json.dumps(body, ensure_ascii=False, separators=(",", ":")), rev),
        )
        return rev

    def delete(self, c, kind: str, doc_id: str) -> int | None:
        if not c.execute("SELECT 1 FROM docs WHERE kind = ? AND id = ? AND deleted = 0", (kind, doc_id)).fetchone():
            return None
        rev = self._next_rev(c)
        c.execute("UPDATE docs SET body = '{}', rev = ?, deleted = 1 WHERE kind = ? AND id = ?", (rev, kind, doc_id))
        return rev

    @staticmethod
    def get(c, kind: str, doc_id: str) -> dict | None:
        row = c.execute("SELECT body FROM docs WHERE kind = ? AND id = ? AND deleted = 0", (kind, doc_id)).fetchone()
        if not row:
            return None
        doc = json.loads(row["body"])
        doc["id"] = doc_id
        return doc

    @staticmethod
    def all(c, kind: str) -> list[dict]:
        out = []
        for row in c.execute("SELECT id, body FROM docs WHERE kind = ? AND deleted = 0 ORDER BY id", (kind,)):
            doc = json.loads(row["body"])
            doc["id"] = row["id"]
            out.append(doc)
        return out

    @staticmethod
    def appointments_on(c, date: str, staff_id: str | None = None) -> list[dict]:
        out = []
        rows = c.execute(
            "SELECT id, body FROM docs WHERE kind = 'appointments' AND deleted = 0 AND json_extract(body, '$.date') = ?",
            (date,),
        )
        for row in rows:
            doc = json.loads(row["body"])
            if staff_id is not None and doc.get("staffId") != staff_id:
                continue
            doc["id"] = row["id"]
            out.append(doc)
        return out

    @staticmethod
    def appointments_between(c, first: str, last: str) -> list[dict]:
        out = []
        rows = c.execute(
            "SELECT id, body FROM docs WHERE kind = 'appointments' AND deleted = 0 "
            "AND json_extract(body, '$.date') BETWEEN ? AND ?",
            (first, last),
        )
        for row in rows:
            doc = json.loads(row["body"])
            doc["id"] = row["id"]
            out.append(doc)
        return out

    @staticmethod
    def changes(c, since: int) -> tuple[int, list[dict]]:
        rev = Store.current_rev(c)
        rows = []
        for row in c.execute("SELECT kind, id, body, rev, deleted FROM docs WHERE rev > ? ORDER BY rev", (since,)):
            rows.append({
                "kind": row["kind"], "id": row["id"], "rev": row["rev"],
                "deleted": bool(row["deleted"]),
                "body": None if row["deleted"] else json.loads(row["body"]),
            })
        return rev, rows
