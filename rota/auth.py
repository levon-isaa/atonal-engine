"""Staff passwords, sessions and request rate limits."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time

SESSION_DAYS = 30
_N, _R, _P = 2 ** 14, 8, 1


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=len(_unb64(want)))
        return hmac.compare_digest(got, _unb64(want))
    except (ValueError, TypeError):
        return False


# A real hash to compare against when the email is unknown, so a wrong email takes as long as a
# wrong password and the timing does not reveal which staff emails exist.
_DUMMY = hash_password(secrets.token_hex(8))


def check_login(row, password: str) -> bool:
    if row is None:
        verify_password(password, _DUMMY)
        return False
    return verify_password(password, row["pw"])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(c, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
    c.execute(
        "INSERT INTO sessions (token_hash, user_id, created, expires) VALUES (?, ?, ?, ?)",
        (token_hash(token), user_id, now, now + SESSION_DAYS * 86400),
    )
    return token


def session_user(c, token: str | None):
    if not token or len(token) > 200:
        return None
    row = c.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires > ?",
        (token_hash(token), time.time()),
    ).fetchone()
    return row


def end_session(c, token: str | None) -> None:
    if token:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


class RateLimiter:
    """Sliding-window counters kept in memory. One process serves every studio, so this is enough;
    Caddy in front caps request sizes and slow clients (deploy/Caddyfile)."""

    def __init__(self):
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def peek(self, key: str, limit: int, window: float) -> bool:
        """Would one more hit be allowed? Records nothing."""
        now = time.monotonic()
        with self._lock:
            return len([t for t in self._hits.get(key, ()) if now - t < window]) < limit

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, ()) if now - t < window]
            ok = len(hits) < limit
            if ok:
                hits.append(now)
            self._hits[key] = hits
            if len(self._hits) > 50000:  # forget idle keys so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 3600]:
                    del self._hits[k]
            return ok
