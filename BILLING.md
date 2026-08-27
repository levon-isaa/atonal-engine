# Billing — how it works, and how to switch it on

## The model, in one line

**A credit buys one analysed track. Everything after that is free.**

This is deliberately not "pay per export", which is what the site used to say.
The export path runs entirely in the browser — `viewer.html` encodes through
WebCodecs and muxes the MP4 itself, and makes no server call to do it. So:

- **"Pay per export" cannot be enforced.** Once the director JSON is in the page
  there is nothing left to withhold.
- **"Free unlimited analysis" is the whole server cost.** ~10s of CPU and ~2GB
  of RAM per track, given away to anyone with a browser.

Charging for the analysis puts the price on the thing that costs money and the
gate on the thing that crosses the network. It is also more generous in
practice: buy one credit and you can re-render that track forever, in every
form, material and format, and export it as many times as you like.

**Cache hits are free.** The analysis cache is keyed on audio content, so
re-uploading the same file — even renamed — is a dictionary lookup and is never
charged for.

**Failures are refunded** automatically. The ledger is append-only, so a refund
is a visible `+1` row rather than a number being quietly adjusted.

## Pieces

| File | What it does |
|---|---|
| `billing.py` | SQLite ledger, render keys, Stripe calls |
| `server.py` | `/packs` `/credits` `/checkout` `/claim` `/stripe/webhook`, and the gate in `/analyze` |
| `site/pricing.html` | The pricing page. Prices come from `/packs`, so page and ledger cannot disagree |
| `site/success.html` | Post-checkout. Shows the render key once, stores it in `localStorage` |
| `viewer.html` | Credits panel; sends `X-Render-Key` with each upload |

Database: `out/billing.db` (override with `ATONAL_DB`). It holds **hashed** keys.
The one exception is the `claims` table, which keeps the plaintext for 24 hours
so the success page survives a refresh; after that the database contains nothing
that can spend anything.

## Switching it on

1. **Install the SDK** — `pip install -r requirements.txt` (adds `stripe`).

2. **Create a Stripe account** and stay in **test mode** while you try this.

3. **Set the environment** — never commit these:

   ```
   STRIPE_SECRET_KEY=sk_test_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   ATONAL_ORIGIN=https://your-domain            # where Stripe redirects back to
   ATONAL_FREE_PER_DAY=2
   ATONAL_TRUST_PROXY=1                         # ONLY if a proxy is in front
   ```

4. **Forward webhooks locally** while testing:

   ```bash
   stripe listen --forward-to localhost:8770/stripe/webhook
   ```

   That prints the `whsec_...` to use as `STRIPE_WEBHOOK_SECRET`.

5. **Buy something** with Stripe's test card `4242 4242 4242 4242`, any future
   expiry, any CVC. You should land on `success.html` with a key.

6. **For production**, create real Stripe Prices and point at them, so the
   amount lives in Stripe rather than in two places that can drift:

   ```
   ATONAL_PRICE_SINGLE=price_...
   ATONAL_PRICE_TEN=price_...
   ATONAL_PRICE_FIFTY=price_...
   ```

## Things that are deliberate

- **The webhook verifies its signature.** Without that it is an unauthenticated
  "give me credits" endpoint on a public URL. If `STRIPE_WEBHOOK_SECRET` is
  missing the endpoint refuses everything rather than trusting the payload.
- **`/claim` re-reads the session from Stripe**, and does not believe the query
  string — `success_url` is just a redirect anyone can point a browser at.
- **`/claim` will also grant** if the webhook has not landed yet. Both paths are
  idempotent on the session id, so whichever runs first wins.
- **Grants are idempotent on the Stripe event id** (`ledger.ref` is UNIQUE).
  Stripe retries webhooks, sometimes for days.
- **Spending is one `BEGIN IMMEDIATE` transaction.** Read-then-write across two
  statements lets two concurrent uploads both see a balance of 1 and both spend
  it. Tested with 12 threads against a balance of 1: exactly one succeeds.
- **A repeat purchase tops up the existing key** rather than issuing a second
  one, matched on the Stripe email.
- **`X-Forwarded-For` is only trusted when `ATONAL_TRUST_PROXY` is set.** It is
  client-settable, so trusting it unconditionally makes the free-tier limit one
  header away from infinite.

## Still to do before taking real money

- [ ] **Email the key.** The success page says "we have also emailed it to you"
      and nothing does. Either send it (Stripe can, via a receipt with metadata,
      or use any transactional provider) or change that sentence.
- [ ] **Real prices.** €6 / €45 / €175 are placeholders.
- [ ] **A real support address.** `hello@example.com` appears on both pages.
- [ ] **Terms and refund policy** pages; the FAQ promises 14-day refunds on
      unspent credits.
- [ ] **Move the ledger off SQLite** if you ever run more than one instance —
      it is a single file on local disk. One box is fine for a launch.
- [ ] **Back up `out/billing.db`.** It is the only record of who paid you.
