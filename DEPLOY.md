# Going live

You need a domain, a small Ubuntu server and about 30 minutes. One command sets the server up;
Caddy gets HTTPS certificates for the sales site and for every studio's address by itself.

## 1. A server

Any provider's smallest Ubuntu 24.04 machine is enough to start: 1 vCPU, 1–2 GB of memory, 20 GB
of disk. That is about €5 a month at Hetzner or DigitalOcean.

Rota stores salons' client names and phone numbers, so where the server sits matters under
Armenia's personal data law. Ask your lawyer about the country before you rent one (the go-to-market
plan has the details).

## 2. DNS

At the company where you bought the domain, add two records pointing to the server's IP address:

| Name | Type | Value |
|---|---|---|
| `rota.am` (often written `@`) | A | your server's IPv4 address |
| `*.rota.am` (often written `*`) | A | the same address |

The `*` record is what makes every studio's address (`nairi.rota.am`) work without touching DNS
again. If your registrar's DNS can't do `*` records, move the domain's DNS to Cloudflare (free) and
add them there, with the proxy (orange cloud) switched off.

## 3. Set it up

Copy the code to the server and run the setup script:

```bash
ssh root@YOUR_SERVER
git clone --branch rota --single-branch https://github.com/levon-isaa/atonal-engine.git rota
cd rota
sudo deploy/setup.sh rota.am you@example.com
```

If the repository is private, either add a [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys)
and clone over SSH, or copy the folder from your computer: `scp -r rota-app root@YOUR_SERVER:rota`.

The script installs Python and Caddy, creates a `rota` user, copies the code to `/srv/rota/app`,
writes `/etc/rota/rota.env` with a new secret, starts the server, a nightly backup and an hourly
demo reset, and configures Caddy. Then:

```bash
sudo rota create-admin you@example.com     # your account for https://rota.am/admin/
sudo nano /etc/rota/rota.env               # fill in ROTA_CONTACT_PHONE and ROTA_CONTACT_EMAIL, in quotes
sudo systemctl restart rota
```

Open `https://rota.am`, `https://demo.rota.am` and `https://rota.am/admin/`. The first visit to
each address takes a few seconds while its certificate is issued.

## Updating

```bash
cd ~/rota && git pull && sudo deploy/setup.sh rota.am you@example.com
```

The script replaces the code and keeps the settings, the secret and every studio's data.

## Backups

Every night at 03:30 the server copies every database to `/var/backups/rota/<date>/` and keeps 14
days. **That is on the same machine: copy it somewhere else too.** Your provider's automatic server
backups (about 20% extra a month) are the easiest way; `rclone` to any cloud storage also works.

Keep a copy of `/etc/rota/rota.env` with the backups. Without its `ROTA_SECRET`, clients' existing
booking links stop working (the bookings themselves are fine).

To restore: `sudo systemctl stop rota`, copy `platform.db` and the `studios/` folder from a backup
into `/srv/rota/data/`, `sudo chown -R rota:rota /srv/rota/data`, `sudo systemctl start rota`.
Try this once before you have real customers.

## Keeping an eye on it

- `systemctl status rota` and `journalctl -u rota -f` show the server and its requests.
- Point a free uptime monitor (UptimeRobot, Better Stack) at `https://rota.am/api/health`, so you
  hear about an outage before your studios do.
- `sudo rota list-studios` lists every company from the command line; the admin page shows more.

## Day-to-day commands

```bash
sudo rota set-password --studio nairi anna@nairi.am    # an owner forgot their password
sudo rota set-status nairi paused                      # stop a studio's online booking
sudo rota set-status nairi active                      # a paying customer
sudo rota delete-studio nairi --yes                    # remove a company (a copy stays in data/deleted/)
```

## Limits to know

- **One server.** Rate limits live in memory and the data on one disk, so run a single copy.
  One small server handles hundreds of studios.
- **New studio certificates.** Let's Encrypt issues at most 50 certificates a week for one domain,
  so at most about 50 new studios a week can get their address working. Past that, switch Caddy to
  one wildcard certificate for `*.rota.am`; that needs your DNS provider's API.
- **Email.** Rota sends none: no sign-up confirmations, no password-reset emails.

## Option B: a container

The `Dockerfile` runs Rota on port 8780 with its data in `/data`:

```bash
docker build -t rota .
docker run -d --name rota -p 127.0.0.1:8780:8780 -v rota-data:/data \
  -e ROTA_PUBLIC_URL=https://rota.am -e ROTA_SECRET=<a long random string> rota
docker exec -it rota python manage.py create-admin you@example.com
docker exec rota python manage.py reset-demo
```

In front of it you need a proxy that serves HTTPS for `rota.am` and `*.rota.am` and passes the
client's address as `X-Real-IP`. `deploy/Caddyfile` does exactly that. A hosting platform has to
support wildcard domains to do the same. Run one instance, on a persistent volume.
