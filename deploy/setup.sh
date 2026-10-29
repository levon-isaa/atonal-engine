#!/usr/bin/env bash
# Sets Rota up on a fresh Ubuntu 24.04 server, or updates it when run again.
#
#   sudo deploy/setup.sh rota.am you@example.com
#
# Run it from a copy of this code on the server (git clone or scp; not from /srv/rota/app). It
# installs Python and Caddy, creates the rota user, copies the code to /srv/rota/app, writes
# /etc/rota/rota.env once (with a new secret), installs the systemd services, timers and the Caddy
# config, starts everything, and refreshes the demo studio. Studio data in /srv/rota/data is
# never touched. The email is for the certificate authority's expiry notices.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
if [ "$(id -u)" != 0 ]; then echo "Run it as root: sudo $0 $*" >&2; exit 1; fi
if ! [[ "$DOMAIN" =~ ^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$ ]] || ! [[ "$EMAIL" =~ ^[^@[:space:]/]+@[^@[:space:]/]+$ ]]; then
  echo "Usage: sudo $0 your-domain.am you@example.com" >&2; exit 1
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$SRC" = /srv/rota/app ]; then echo "Run this from your own copy of the code, not from /srv/rota/app." >&2; exit 1; fi

step() { printf '\n== %s\n' "$*"; }

step "Packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 caddy sqlite3 ca-certificates tzdata
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "Rota needs Python 3.10 or newer." >&2; exit 1; }

step "User and folders"
id rota >/dev/null 2>&1 || useradd --system --home-dir /srv/rota --shell /usr/sbin/nologin rota
install -d -o rota -g rota -m 0750 /srv/rota /srv/rota/data /var/backups/rota

step "Code (to /srv/rota/app)"
rm -rf /srv/rota/app.new
mkdir /srv/rota/app.new
cp -r "$SRC/server.py" "$SRC/manage.py" "$SRC/rota" "$SRC/web" /srv/rota/app.new/
find /srv/rota/app.new -name __pycache__ -prune -exec rm -rf {} +
chown -R root:rota /srv/rota/app.new
chmod -R u=rwX,g=rX,o= /srv/rota/app.new
rm -rf /srv/rota/app.old
if [ -d /srv/rota/app ]; then mv /srv/rota/app /srv/rota/app.old; fi
mv /srv/rota/app.new /srv/rota/app

step "Settings (/etc/rota/rota.env)"
install -d -o root -g rota -m 0750 /etc/rota
if [ ! -f /etc/rota/rota.env ]; then
  ( umask 027
    cat > /etc/rota/rota.env <<ENV
ROTA_PUBLIC_URL=https://$DOMAIN
ROTA_DATA=/srv/rota/data
ROTA_TRUST_PROXY=1
# Signs clients' booking links. Keep a copy with your backups; changing it breaks existing links.
ROTA_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
# Shown on the sales site and to paused studios. Fill these in, then: sudo systemctl restart rota
ROTA_CONTACT_PHONE=""
ROTA_CONTACT_EMAIL=""
ENV
  )
  chown root:rota /etc/rota/rota.env
  echo "Wrote /etc/rota/rota.env with a new secret."
else
  sed -i "s|^ROTA_PUBLIC_URL=.*|ROTA_PUBLIC_URL=https://$DOMAIN|" /etc/rota/rota.env
  echo "Kept the existing /etc/rota/rota.env."
fi
install -m 0755 "$SRC/deploy/rota" /usr/local/bin/rota

step "Services"
cp "$SRC"/deploy/rota.service "$SRC"/deploy/rota-backup.service "$SRC"/deploy/rota-backup.timer \
   "$SRC"/deploy/rota-demo.service "$SRC"/deploy/rota-demo.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable rota.service rota-backup.timer rota-demo.timer
systemctl restart rota.service
systemctl start rota-backup.timer rota-demo.timer
# Straight to the local server, past any HTTP proxy this machine is configured with.
healthy() { python3 -c "import urllib.request as u; u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8780/api/health', timeout=2)" 2>/dev/null; }
for _ in $(seq 1 40); do healthy && break; sleep 0.5; done
healthy || { echo "Rota didn't start. See: journalctl -u rota -n 50" >&2; exit 1; }
rota reset-demo

step "Caddy (HTTPS)"
sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__EMAIL__/$EMAIL/g" "$SRC/deploy/Caddyfile" > /etc/caddy/Caddyfile.new
caddy validate --config /etc/caddy/Caddyfile.new --adapter caddyfile
mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
systemctl enable caddy
systemctl reload caddy || systemctl restart caddy
rm -rf /srv/rota/app.old

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
IP="${IP:-<this server IP>}"
cat <<DONE

Rota is running. Next:
  1. DNS, at your domain registrar: two A records pointing to this server
       $DOMAIN        A  $IP
       *.$DOMAIN      A  $IP
  2. Your admin account:      sudo rota create-admin $EMAIL
  3. Contact details:         edit /etc/rota/rota.env, then: sudo systemctl restart rota
  4. Open https://$DOMAIN, https://demo.$DOMAIN and https://$DOMAIN/admin/
     (the first visit to each address takes a few seconds while its certificate is issued)
DONE
