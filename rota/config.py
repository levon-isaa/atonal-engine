"""Settings read from the environment. Nothing secret belongs in the repo; see DEPLOY.md."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    data_dir: str          # platform.db (companies, Rota's own admins) and studios/<address>.db, one file per company
    host: str
    port: int
    public_url: str        # the sales site, e.g. https://rota.am; each studio is https://<address>.rota.am
    scheme: str            # from public_url
    domain: str            # from public_url, without the port
    port_suffix: str       # ":8780" in development, "" behind a proxy on 443
    secret: str | None     # signs the manage-booking links; generated and kept in platform.db when unset
    trust_proxy: bool      # read the client address from X-Real-IP (set it only behind a proxy)
    secure_cookies: bool   # Secure flag (and the __Host- name) on session cookies
    timezone: str          # studio time zone when the settings don't name one
    web_root: str
    bookings_per_hour: int # successful online bookings allowed per client address per studio per hour
    signups_per_day: int   # new companies allowed per address per day
    pilot_days: int        # length of the free pilot given at sign-up
    contact_phone: str     # shown on the sales site; WhatsApp link when it is a mobile number
    contact_email: str

    def studio_url(self, slug: str) -> str:
        return f"{self.scheme}://{slug}.{self.domain}{self.port_suffix}"

    @property
    def site_url(self) -> str:
        return f"{self.scheme}://{self.domain}{self.port_suffix}"


def read_env_file(path: str) -> dict:
    """KEY=value lines, as systemd's EnvironmentFile reads them (quotes optional, # comments)."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k.strip()] = v
    return out


def from_env(env=None) -> Config:
    env = os.environ if env is None else env
    if env.get("ROTA_ENV_FILE"):  # the server's settings file, for admin commands run by hand
        env = {**read_env_file(env["ROTA_ENV_FILE"]), **{k: v for k, v in env.items() if v}}
    port = int(env.get("ROTA_PORT") or 8780)
    url = urlparse((env.get("ROTA_PUBLIC_URL") or f"http://localhost:{port}").rstrip("/"))
    if url.scheme not in ("http", "https") or not url.hostname:
        raise SystemExit("ROTA_PUBLIC_URL must look like https://rota.am")
    return Config(
        data_dir=env.get("ROTA_DATA") or os.path.join(ROOT, "data"),
        host=env.get("ROTA_HOST") or "127.0.0.1",
        port=port,
        public_url=f"{url.scheme}://{url.netloc}",
        scheme=url.scheme,
        domain=url.hostname.lower(),
        port_suffix=f":{url.port}" if url.port else "",
        secret=env.get("ROTA_SECRET") or None,
        trust_proxy=_flag(env.get("ROTA_TRUST_PROXY"), False),
        secure_cookies=_flag(env.get("ROTA_SECURE_COOKIES"), url.scheme == "https"),
        timezone=env.get("ROTA_TZ") or "Asia/Yerevan",
        web_root=env.get("ROTA_WEB_ROOT") or os.path.join(ROOT, "web"),
        bookings_per_hour=int(env.get("ROTA_BOOKINGS_PER_HOUR") or 10),
        signups_per_day=int(env.get("ROTA_SIGNUPS_PER_DAY") or 5),
        pilot_days=int(env.get("ROTA_PILOT_DAYS") or 60),
        contact_phone=(env.get("ROTA_CONTACT_PHONE") or "").strip(),
        contact_email=(env.get("ROTA_CONTACT_EMAIL") or "").strip(),
    )
