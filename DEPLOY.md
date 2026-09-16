# Going live

What this server is, what it is not, and the shortest correct path to a public deployment.

## The shape of it

```
   browser ──TLS──▶ nginx ──plain HTTP──▶ server.py (127.0.0.1:8770)
                      │                        │
                      │                        ├── out/billing.db   the ledger
                      └ TLS, rate limits,      └── out/cache/       analyses already paid for
                        body cap, slow-client
                        timeouts, static files
```

**server.py is a `http.server.ThreadingHTTPServer`, and the Python standard library says
plainly that this is not for production.** That is not a reason to rewrite it — it is a reason
to put the things it does not do in front of it. It does one job well: it serialises analyses,
meters credits against a ledger that cannot go negative, and serves the viewer. TLS, rate
limiting, the body cap and slow-client handling belong to nginx, and `deploy/nginx.conf` is
written to do exactly those.

Do not run it with `ATONAL_HOST` set on a public box. It will start, it will warn you twice,
and it will be a plain-HTTP upload endpoint with no rate limiting facing the internet.

## What one box needs

One small VM. The analysis is **one track at a time** by design (`_Slot` in server.py), so
cores past two or three buy nothing; memory and disk are the constraints.

| | |
|---|---|
| RAM | **Set by your track-length cap, not by traffic.** `analyze.py` measured it: peak RSS is `1495 MB + 4.95 MB × seconds of audio`. At the default 20-minute cap that is **7.4 GB**, so an 8 GB box. See the table below. |
| Disk | The repo, a venv (~760 MB), `~/panns_data` (~310 MB), plus the cache at roughly **1 MB per analysed track**, growing without bound. |
| CPU | 2 vCPU. PANNs tagging runs on CPU alongside layer 1. |
| Python | 3.9+, and `ffmpeg` on PATH. |

Pick the cap and the box together. Analysis is serialised, so this is the peak for **one**
analysis — concurrency does not multiply it, track length does:

| `ATONAL_MAX_MINUTES` | peak RSS | box | `MemoryMax` |
|---|---|---|---|
| 6 | 3.3 GB | 4 GB | `4G` |
| 10 | 4.5 GB | 6 GB | `5G` |
| 20 (default) | 7.4 GB | 8 GB | `8G` |

`deploy/atonal.service` ships `MemoryMax=8G` to match the default. **If you lower the cap,
lower `MemoryMax` with it; if you raise either, raise both.** Set it too low and systemd
OOM-kills the analysis mid-track: the customer's credit is refunded by the ledger, and nothing
anywhere explains what happened.

## Steps

**1. User, code, venv**

```bash
sudo adduser --system --group --home /srv/atonal atonal
sudo -u atonal git clone <your remote> /srv/atonal
cd /srv/atonal && sudo -u atonal ./setup.sh
sudo -u atonal mkdir -p /srv/atonal/out
```

**2. Secrets, outside the repo**

```bash
sudo install -d -m 0750 /etc/atonal
sudo install -m 0600 /dev/null /etc/atonal/atonal.env
```

`/etc/atonal/atonal.env` — root-owned, `0600`, never committed:

```ini
ATONAL_ORIGIN=https://atonal.example     # locks CORS to your site; see _cors()
ATONAL_TRUST_PROXY=1                     # REQUIRED behind nginx; see below
PADDLE_API_KEY=...
PADDLE_ENV=production
PADDLE_WEBHOOK_SECRET=...
ATONAL_PRICE_TEN=pri_...                 # one per pack, or /checkout raises for it
ATONAL_GUMROAD_TEN=your_product_id       # if you sell through Gumroad instead
ATONAL_GUMROAD_LINK_TEN=https://...
```

The boot banner prints which channels are live and which packs are **half** configured. A link
with no product id is a pack that can be bought and then not redeemed — that one takes money.

**3. nginx and a certificate**

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/atonal
sudo ln -s /etc/nginx/sites-available/atonal /etc/nginx/sites-enabled/
# edit server_name and the two certificate paths first
sudo certbot --nginx -d atonal.example
sudo nginx -t && sudo systemctl reload nginx
```

**4. The service**

```bash
sudo cp deploy/atonal.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now atonal
journalctl -u atonal -f
```

**5. Read the preflight.** Every boot prints one. It is the only place several of these
settings are visible at all:

```
  socket timeout: 30s (a stalled connection is dropped, not held)
  upload cap: 300 MB, max track 20 min
  CORS: locked to https://atonal.example
  free tier identity: X-Forwarded-For -- correct ONLY with a proxy in front ...
  ledger: /srv/atonal/out/billing.db (writable)
  analysis cache: 0 entries, 0 MB, no automatic bound
```

## The two settings that are wrong by default behind a proxy

**`ATONAL_TRUST_PROXY`.** The free tier counts per IP. Without this the identity is the socket
address, which behind nginx is nginx — so every visitor on earth shares one bucket and the free
tier is two analyses a day in total. With it, the identity is `X-Forwarded-For`, which is
client-settable, so it is only safe when something in front **overwrites** it.
`deploy/nginx.conf` does (`$proxy_add_x_forwarded_for`). Set it, and never expose the server
directly with it set.

**`ATONAL_ORIGIN`.** Unset, CORS answers `*` — right for a dev server, wrong for a deployment.
Set it and the `Access-Control-Allow-Origin` header goes only to your own site.

## What is not handled, and is yours to decide

- **Backups.** `out/billing.db` is the revenue record and nothing backs it up. It is SQLite in
  WAL mode, so copy it with `sqlite3 out/billing.db ".backup '/backup/billing-$(date +%F).db'"`
  — not `cp`, which can catch a torn write. Off the box, on a schedule.
- **Cache growth.** `out/cache/` grows about 1 MB per analysed track and nothing prunes it.
  Entries are content-addressed and re-analysing is free to the customer, so deleting the oldest
  is safe. Watch it or cap it with a cron job.
- **Monitoring.** `/health` returns `{ok, panns, build}`. Point an uptime check at it. There is
  no metrics endpoint and no alerting.
- **Logs.** Everything goes to stdout, so journald owns rotation. There is no request log — add
  one in nginx if you want per-request visibility.
- **Legal.** Both payment providers are merchants of record, which is why VAT is theirs and not
  yours. Terms, privacy policy and a refund policy are not in this repo.
- **Uploads are other people's music.** Nothing here deletes the temp file's contents from the
  disk's free list, and the cache holds the derived analysis indefinitely. Decide your retention
  and say it on the site.

## Before you open the door

```bash
python tests/run.py                      # must be green; no Chrome needed
curl -sS https://atonal.example/health   # {"ok": true, ...}
```

- [ ] `journalctl -u atonal` preflight shows CORS locked and the proxy identity you intended
- [ ] a real purchase in Paddle/Gumroad **sandbox** grants credits end to end
- [ ] the webhook secret is set — without it the webhook refuses everything, silently
- [ ] `curl -X POST https://atonal.example/analyze` with no body returns JSON, not nginx HTML
- [ ] an oversize upload returns the JSON 413, not nginx's page (means the two caps agree)
- [ ] the ledger backup has run once and you have restored it somewhere as a test
