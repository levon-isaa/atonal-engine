# Going live

Two ways. Pick one.

- **Your own server** (a small VM): Rota runs under systemd on 127.0.0.1, nginx in front does TLS
  and request limits. Same shape as most single-box Python apps.
- **A container platform** (Fly.io, Render, Railway, or Docker on a VM): the `Dockerfile` runs Rota
  on port 8780 and the platform does TLS.

Either way, the whole studio lives in one SQLite file. **Put it on persistent storage and back it up.**

## What one studio needs

Very little. One process, a few threads, SQLite. The smallest VM any provider sells is enough:
1 vCPU, 512 MB RAM, a few hundred MB of disk. Python 3.10+, no packages to install.

## Option A: your own server

**1. User and code**

```bash
sudo adduser --system --group --home /srv/rota rota
sudo -u rota git clone <your remote> /srv/rota       # the rota branch, or its own repo
sudo -u rota mkdir -p /srv/rota/data
```

**2. Settings, outside the repo**

```bash
sudo install -d -m 0750 /etc/rota
sudo install -m 0600 /dev/null /etc/rota/rota.env
```

`/etc/rota/rota.env`:

```ini
ROTA_ORIGIN=https://book.example.am
ROTA_TRUST_PROXY=1
ROTA_SECRET=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
```

`ROTA_TRUST_PROXY=1` is required behind nginx; without it every client looks like 127.0.0.1 and
they all share one rate limit. Never set it when nothing sits in front of the app: anyone could
then claim any address.

**3. First staff account**

```bash
cd /srv/rota && sudo -u rota python3 manage.py create-user you@studio.am --role owner
```

Then either `seed-sample` to explore, or nothing, and set the studio up in the desk.

**4. systemd**

```bash
sudo cp deploy/rota.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now rota
curl -s http://127.0.0.1:8780/api/health
```

**5. nginx and a certificate**

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/rota     # change book.example.am to your domain
sudo ln -s /etc/nginx/sites-available/rota /etc/nginx/sites-enabled/
sudo certbot certonly --webroot -w /var/www/certbot -d book.example.am
sudo nginx -t && sudo systemctl reload nginx
```

**6. Backups**

The database is `data/rota.db`. Copy it safely while the server runs:

```bash
sudo -u rota python3 /srv/rota/manage.py backup /srv/rota/data/backup-$(date +%F).db
```

Run that from cron daily and copy the file off the machine. Keep the `ROTA_SECRET` with the
backups: without it, clients' existing booking links stop working (the bookings themselves are fine).

## Option B: a container

```bash
docker build -t rota .
docker run -d --name rota -p 127.0.0.1:8780:8780 -v rota-data:/data \
  -e ROTA_ORIGIN=https://book.example.am -e ROTA_SECRET=<secret> rota
docker exec -it rota python manage.py create-user you@studio.am --role owner
```

On Fly.io, Render or Railway: deploy from this repo's Dockerfile, attach a persistent volume at
`/data`, set `ROTA_ORIGIN` and `ROTA_SECRET`, and run the `create-user` command once from the
platform's console. The image already sets `ROTA_TRUST_PROXY=1`, so check that your platform puts
the real client address in `X-Real-IP`; if it uses another header, rate limits will be shared.

Run **one** instance. Rota keeps rate limits in memory and SQLite on one disk; two copies would
each have their own.

## After launch

- Share `https://book.example.am/` with clients: on Instagram, Google Maps, WhatsApp Business.
- Staff sign in at `/desk/`. Add people with `manage.py create-user`; only owners change settings,
  the team and services.
- Have a native speaker read the Armenian and Russian text (`web/book/i18n.js` and the reminder
  templates in Settings) before you announce it.
