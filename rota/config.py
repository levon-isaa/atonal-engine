"""Settings read from the environment. Nothing secret belongs in the repo; see DEPLOY.md."""
from __future__ import annotations

import os
from dataclasses import dataclass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    db_path: str
    host: str
    port: int
    origin: str            # public address, e.g. https://book.example.am, used for links and the Origin check
    secret: str | None     # signs the manage-booking links; generated and kept in the database when unset
    trust_proxy: bool      # read the client address from X-Real-IP (set it only behind nginx)
    secure_cookies: bool   # Secure flag on the staff session cookie
    timezone: str          # studio time zone when the settings don't name one
    web_root: str
    bookings_per_hour: int # successful online bookings allowed per client address per hour


def from_env(env=None) -> Config:
    env = os.environ if env is None else env
    origin = (env.get("ROTA_ORIGIN") or "").rstrip("/")
    return Config(
        db_path=env.get("ROTA_DB") or os.path.join(ROOT, "data", "rota.db"),
        host=env.get("ROTA_HOST") or "127.0.0.1",
        port=int(env.get("ROTA_PORT") or 8780),
        origin=origin,
        secret=env.get("ROTA_SECRET") or None,
        trust_proxy=_flag(env.get("ROTA_TRUST_PROXY"), False),
        secure_cookies=_flag(env.get("ROTA_SECURE_COOKIES"), origin.startswith("https://")),
        timezone=env.get("ROTA_TZ") or "Asia/Yerevan",
        web_root=env.get("ROTA_WEB_ROOT") or os.path.join(ROOT, "web"),
        bookings_per_hour=int(env.get("ROTA_BOOKINGS_PER_HOUR") or 10),
    )
