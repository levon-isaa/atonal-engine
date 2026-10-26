# Rota

Online booking and an appointment desk for salons and other appointment businesses.

- **Booking site** at `/`, for clients, in Armenian, Russian and English. Clients pick services, a
  specialist (or anyone free) and a time, and leave a name and phone number. No account needed:
  each booking comes with a private link (`/b/…`) to change or cancel it, and the browser remembers
  the bookings made on it under **My bookings**.
- **Staff desk** at `/desk/`, behind a staff sign-in. A day calendar with a column per specialist
  (click a free slot to book, drag to move), client profiles and history, payments, WhatsApp
  reminders in each client's language with their manage link, services, team hours, reports and
  settings.

One Python process, the standard library and SQLite. No third-party packages, no build step.

## Run it locally

Python 3.10 or newer.

```bash
python manage.py seed-sample                                   # optional: the Atelier Nairi sample studio
python manage.py create-user you@example.com --role owner      # asks for a password
python server.py                                               # http://127.0.0.1:8780
```

Open <http://127.0.0.1:8780/> for the booking site and <http://127.0.0.1:8780/desk/> for the desk.
The sample studio is marked as sample data in both. To start from nothing instead, skip
`seed-sample` and add your team and services in the desk.

## What protects what

| Concern | How it is handled |
|---|---|
| Clients seeing each other | Public endpoints return free times, never names or numbers. Only signed-in staff read client data. |
| Double booking | The free-time check and the save happen in one SQLite write transaction (`BEGIN IMMEDIATE`); a test books one slot from eight threads at once and exactly one wins. |
| Booking links | Signed with a server secret (HMAC). A link opens one booking and nothing else. |
| Staff passwords | scrypt, per-user salt. Sessions are random tokens stored hashed, in an HttpOnly, SameSite cookie (Secure on https). |
| Guessing passwords | 8 wrong passwords per account per 15 minutes; successful sign-ins never count. |
| Spam bookings | Per-address limits (attempts, and successful bookings per hour, `ROTA_BOOKINGS_PER_HOUR`), at most 4 upcoming online visits per phone number, and a hidden field bots fill in. |
| Other sites acting for a user | Every write needs an `X-Rota` header and JSON; the Origin is checked when `ROTA_ORIGIN` is set; no CORS. |
| Script injection | All text is escaped on render; a Content-Security-Policy allows scripts from this site only. |
| Who can change what | Owners change settings, team and services; other staff run the calendar, clients and payments. |

## Settings

Everything is environment variables; see `rota/config.py`.

| Variable | Default | |
|---|---|---|
| `ROTA_DB` | `data/rota.db` | SQLite file. Back it up. |
| `ROTA_HOST`, `ROTA_PORT` | `127.0.0.1`, `8780` | Keep the host on loopback behind nginx. |
| `ROTA_ORIGIN` | | Your public address, e.g. `https://book.example.am`. Turns on the Origin check and Secure cookies. |
| `ROTA_SECRET` | generated | Signs booking links. If unset, one is generated and kept in the database. Changing it breaks existing links. |
| `ROTA_TRUST_PROXY` | off | Read the client address from `X-Real-IP`. Turn on only behind nginx or a platform proxy. |
| `ROTA_TZ` | `Asia/Yerevan` | Fallback time zone; the studio's own setting wins. |
| `ROTA_BOOKINGS_PER_HOUR` | `10` | Successful online bookings per client address per hour. |

## Admin commands

```bash
python manage.py create-user EMAIL [--name NAME] [--role owner|staff]
python manage.py set-password EMAIL        # also signs that person out everywhere
python manage.py list-users
python manage.py remove-user EMAIL
python manage.py seed-sample [--force]
python manage.py backup /path/to/copy.db   # safe while the server runs
```

## Tests

```bash
python -m unittest discover -s tests       # API: 48 tests, about 30 seconds
npm install --no-save playwright && npx playwright install chromium
node tests/e2e/smoke.js                    # browser: booking, manage link, desk, reminders
```

## Not built yet

- **Automatic reminders.** The desk opens WhatsApp with the message ready; someone presses Send.
  Sending on its own needs a WhatsApp Business API provider and a scheduler.
- **Phone verification.** Anyone can book with any number; the limits above keep abuse small.
- **Online payment.** Payments are recorded at the desk, not charged online.
- Payroll, stock, loyalty and gift cards, more than one location.
- **The desk in Armenian and Russian.** The booking site is translated; the desk is English.

The Armenian and Russian text was written without a native-speaker review. Have one read
`web/book/i18n.js` and the reminder templates in Settings before launch.
