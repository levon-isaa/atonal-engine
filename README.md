# Rota

Online booking and an appointment desk for salons and other appointment businesses, run as one
service that many companies sign up to.

- **Sales site** at `https://rota.am/` (your domain), in Armenian, Russian and English: what Rota
  does, a live demo, the free pilot, and sign-up at `/start`. A company picks its address
  (`nairi.rota.am`) and its kind of business, and lands in its own desk, signed in, with starter
  services for that kind of studio and a getting-started checklist.
- **Each studio's booking site** at `https://<studio>.rota.am/`. Clients pick services, a specialist
  (or anyone free) and a time, in Armenian, Russian or English, with no account. Each booking comes
  with a private link (`/b/…`) to change or cancel it.
- **Each studio's desk** at `https://<studio>.rota.am/desk/`. A day calendar with a column per
  specialist, clients and their history, payments, WhatsApp reminders in each client's language,
  services, team hours, reports, settings, the people who can sign in, and spreadsheet downloads
  of clients and bookings.
- **The demo** at `https://demo.rota.am/`: the sample studio Atelier Nairi, with a desk anyone can
  open without a password. It goes back to the sample every hour.
- **Rota's admin page** at `https://rota.am/admin/`, for you: every company, its owner and phone,
  how far it got with setup, its bookings this week, when it last used the desk, its pilot dates,
  notes, and pause or resume.

One Python process, the standard library and SQLite. No third-party packages, no build step.

## Run it locally

Python 3.10 or newer. Browsers send `*.localhost` to your own machine, so studios work locally as
`http://<studio>.localhost:8780`.

```bash
python manage.py reset-demo                          # the demo studio, at demo.localhost:8780
python manage.py create-admin you@example.com        # for http://localhost:8780/admin/ (asks for a password)
python server.py                                     # http://localhost:8780
```

Then sign a studio up at <http://localhost:8780/start>, or make one by hand:

```bash
python manage.py create-studio nairi --business "Atelier Nairi" --owner anna@example.com --kind hair --sample
```

## How companies are kept apart

| Concern | How it is handled |
|---|---|
| One studio seeing another's data | Every studio is its own SQLite file (`data/studios/<address>.db`). A request to `nairi.rota.am` can only open Nairi's file. |
| A session or link used at another studio | Sessions live in the studio's own file, in a cookie only that address receives (`__Host-` on https). Booking links are signed with a key derived for each studio. Tests try all of these across two studios. |
| One studio's pages writing to another's | Every write needs an `X-Rota` header and must come from the exact address it writes to (Origin check); no CORS. |
| Clients seeing each other | Public endpoints return free times, never names or numbers. |
| Double booking | The free-time check and the save happen in one SQLite write transaction; a test books one slot from eight threads at once and exactly one wins. |
| Staff passwords | scrypt, per-user salt; sessions are random tokens stored hashed, HttpOnly, SameSite. |
| Guessing passwords | 8 wrong passwords per account per 15 minutes; successful sign-ins never count. |
| Spam | Limits per address on bookings, sign-ups (`ROTA_SIGNUPS_PER_DAY`) and attempts; at most 4 upcoming online visits per phone number; hidden fields bots fill in. |
| Certificates for made-up addresses | Caddy asks `/api/tls-check` before getting one; only real studios pass. |
| Spreadsheet formulas in downloads | Clients type their own names, so cells that start like a formula are made plain text. |
| Who can change what | Owners change settings, services, the team and who can sign in; staff run the calendar, clients and payments. |

## Settings

Environment variables; see `rota/config.py`. On a server they live in `/etc/rota/rota.env`.

| Variable | Default | |
|---|---|---|
| `ROTA_PUBLIC_URL` | `http://localhost:8780` | The sales site, e.g. `https://rota.am`. Studios get `https://<studio>.rota.am`. |
| `ROTA_DATA` | `data/` | `platform.db` (companies, admins) and `studios/*.db`. Back it up. |
| `ROTA_SECRET` | generated | Signs booking links. If unset, one is generated and kept in `platform.db`. Changing it breaks existing links. |
| `ROTA_HOST`, `ROTA_PORT` | `127.0.0.1`, `8780` | Keep the host on loopback behind Caddy. |
| `ROTA_TRUST_PROXY` | off | Read the client address from `X-Real-IP`. Only behind Caddy or a platform proxy. |
| `ROTA_CONTACT_PHONE`, `ROTA_CONTACT_EMAIL` | empty | Shown on the sales site and to paused studios. |
| `ROTA_PILOT_DAYS` | `60` | Length of the free pilot a new company gets. |
| `ROTA_SIGNUPS_PER_DAY` | `5` | New companies per client address per day. |
| `ROTA_BOOKINGS_PER_HOUR` | `10` | Successful online bookings per client address, per studio, per hour. |
| `ROTA_TZ` | `Asia/Yerevan` | Fallback time zone; each studio's own setting wins. |

## Admin commands

On a server, run these as `sudo rota <command>` (deploy/setup.sh installs it with the settings loaded).
Locally, `python manage.py <command>`.

```bash
create-admin EMAIL                       # an account for the admin page
list-studios
create-studio ADDRESS --business NAME --owner EMAIL [--kind hair|barber|nails|brows|skin|massage|other] [--sample]
set-status ADDRESS pilot|active|paused [--until YYYY-MM-DD]
delete-studio ADDRESS --yes              # keeps a copy in data/deleted/
reset-demo
backup DIR [--keep DAYS]                 # every database, safe while the server runs
create-user --studio ADDRESS EMAIL [--role owner|staff]     # owners can also do this in Settings
set-password --studio ADDRESS EMAIL      # e.g. an owner who forgot theirs
list-users --studio ADDRESS
remove-user --studio ADDRESS EMAIL
seed-sample --studio ADDRESS [--force]
```

## Going live

See [DEPLOY.md](DEPLOY.md): a domain, a small Ubuntu server, two DNS records and
`sudo deploy/setup.sh rota.am you@example.com`.

## Tests

```bash
python -m unittest discover -s tests       # API: 59 tests, about 45 seconds
npm install --no-save playwright && npx playwright install chromium
node tests/e2e/smoke.js                    # browser: sign-up, first booking, manage link, desk, demo, admin
node tools/site_text.js --check            # the Armenian text in web/site/*.html matches web/site/i18n.js
```

After editing the sales site's text in `web/site/i18n.js`, run `node tools/site_text.js`. After
changing how the booking page or desk looks, `node tools/site_screenshots.js` retakes the
screenshots on the sales site.

## Not built yet

- **The desk in Armenian and Russian.** The sales site and booking pages are translated; the desk
  is English. This is the first thing to build for selling in Armenia.
- **Automatic reminders.** The desk opens WhatsApp with the message ready; someone presses Send.
  Sending on its own needs a WhatsApp Business API provider and a scheduler.
- **Billing.** The pilot is free; the admin page tracks pilot dates and status by hand.
- **Email.** No sign-up confirmation or password-reset emails; you reset passwords with
  `sudo rota set-password`.
- **Phone verification, online payment, payroll, stock, loyalty, several locations per company.**

The Armenian and Russian text was written without a native-speaker review. Have one read
`web/site/i18n.js`, `web/book/i18n.js` and the reminder templates before launch.
