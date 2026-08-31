# Billing — how it works, and how to switch it on

## The model, in one line

**A credit buys one analysed track. Everything after that is free.**

This is deliberately not "pay per export". The export path runs entirely in the
browser — `viewer.html` encodes through WebCodecs and muxes the MP4 itself, and
makes no server call to do it. So:

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

## Why Paddle

Stripe does not open merchant accounts in Armenia, so the first implementation
could never have taken a payment however correct it was.

Paddle is a **merchant of record**: it sells to the customer and we sell to
Paddle. No local merchant account is needed, and Paddle files the EU VAT — which
matters more than it looks, because prices are in EUR and most buyers will be in
the EU, so that is a liability we would otherwise own. It costs more than raw
card processing; that is what the VAT handling and the country coverage buy.

**No SDK.** Paddle's REST API is JSON over HTTPS and its webhook signature is an
HMAC we compute with `hashlib`, so this needs `requests` (already a dependency
for the tagger) and nothing else.

## Pieces

| File | What it does |
|---|---|
| `billing.py` | SQLite ledger, render keys, Paddle calls |
| `server.py` | `/packs` `/credits` `/checkout` `/claim` `/paddle/webhook`, and the gate in `/analyze` |
| `site/pricing.html` | The pricing page. Prices come from `/packs`, so page and ledger cannot disagree |
| `site/success.html` | Post-checkout. Shows the render key once, stores it in `localStorage` |
| `viewer.html` | Credits panel; sends `X-Render-Key` with each upload |

Database: `out/billing.db` (override with `ATONAL_DB`). It holds **hashed** keys.
The one exception is the `claims` table, which keeps the plaintext for 24 hours
so the success page survives a refresh; after that the database contains nothing
that can spend anything.

The ledger, keys, free tier and the `/analyze` gate never knew what a payment
processor was — only five functions below the provider marker in `billing.py`
did. Changing provider again is a small job, not a rebuild.

## Switching it on

1. **Create a Paddle account** and stay in **sandbox** while you try this.
   `PADDLE_ENV` defaults to `sandbox`; it must be set to `production` explicitly.

2. **Create a product and three prices** in the Paddle dashboard — one per pack.
   Paddle prices live in Paddle and are referenced by `pri_...` id.
   **There is no inline amount to fall back to**: `checkout_url()` raises rather
   than inventing a price, on purpose. An amount defined in two places is an
   amount that will eventually disagree with itself.

3. **Set a default payment link** under *Checkout → Settings*. Without it the
   API returns a transaction with no checkout URL, and the error message says so
   because Paddle's own does not.

4. **Set the environment** — never commit these:

   ```
   PADDLE_API_KEY=...                     # gates everything; absent = billing off
   PADDLE_WEBHOOK_SECRET=...              # from the webhook destination you create
   PADDLE_ENV=sandbox                     # or production
   ATONAL_PRICE_SINGLE=pri_...
   ATONAL_PRICE_TEN=pri_...
   ATONAL_PRICE_FIFTY=pri_...
   ATONAL_ORIGIN=https://your-domain      # where Paddle returns the buyer to
   ATONAL_FREE_PER_DAY=2
   ATONAL_TRUST_PROXY=1                   # ONLY if a proxy is in front
   ```

5. **Add a webhook destination** in Paddle pointing at
   `https://your-domain/paddle/webhook`, subscribed to `transaction.completed`
   (and `transaction.paid` if you want the earlier signal too — both are
   handled, and both are idempotent).

6. **Buy something** with a Paddle sandbox test card — take the current numbers
   from Paddle's own test-payments documentation rather than from here, since
   they change. You should land on `success.html` with a key.

## How a purchase becomes credits

1. The pricing page POSTs `/checkout`; the server creates a Paddle transaction
   with `custom_data` carrying the pack and its credit count, and returns the
   hosted checkout URL.
2. Paddle takes the payment and returns the buyer to
   `ATONAL_ORIGIN/site/success.html?_ptxn=<transaction id>`.
3. The success page calls `/claim`, which **re-reads the transaction from
   Paddle** and grants if the webhook has not landed yet.
4. The webhook grants too. Both paths are idempotent on the transaction id, so
   whichever runs first wins and the other is a no-op.

## Things that are deliberate

- **The webhook verifies its signature.** Paddle signs as `ts=<unix>;h1=<hex>`,
  HMAC-SHA256 over the exact bytes `<ts>:<raw body>` — re-serialising the JSON
  changes the bytes and the check fails. If `PADDLE_WEBHOOK_SECRET` is missing
  the endpoint refuses everything rather than trusting the payload. Without this
  it is an unauthenticated "give me credits" API on a public URL.
- **Timestamps outside a 300s window are refused.** A correctly signed request
  replayed a day later is still a replay.
- **`hmac.compare_digest`, not `==`**, so the comparison does not leak the
  digest by timing.
- **`/claim` re-reads from Paddle** and does not believe the query string — the
  return URL is just a redirect anyone can point a browser at.
- **Grants are idempotent on the transaction id** (`ledger.ref` is UNIQUE).
  Providers retry webhooks, sometimes for days.
- **Spending is one `BEGIN IMMEDIATE` transaction.** Read-then-write across two
  statements lets two concurrent uploads both see a balance of 1 and both spend
  it. Tested with 12 threads against a balance of 1: exactly one succeeds.
- **A repeat purchase tops up the existing key** rather than issuing a second
  one, matched on the customer email.
- **`X-Forwarded-For` is only trusted when `ATONAL_TRUST_PROXY` is set.** It is
  client-settable, so trusting it unconditionally makes the free-tier limit one
  header away from infinite.
- **`Paddle-Version: 1` is pinned**, so a provider-side change cannot alter the
  response shape under a running server.
- **`stripe_ready()` remains as an alias** for `billing_ready()` so an older
  cached page does not break on the rename.

## Still to do before taking real money

- [ ] **Decide whether the key should be emailed.** It currently is not, and the
      success page no longer claims otherwise — it says the page is the only
      place the key is shown. If a customer closes the tab inside the 24h claim
      window they can reload the return URL; after that, the key is gone and it
      is a support conversation. Sending it is the fix.
- [ ] **Real prices.** €6 / €45 / €175 are placeholders, and they now have to
      match the Paddle prices exactly, since `PACKS` amounts are only used for
      display.
- [ ] **A real support address.** `hello@example.com` appears on both pages.
- [ ] **Terms and refund policy** pages; the FAQ promises 14-day refunds on
      unspent credits.
- [ ] **Move the ledger off SQLite** if you ever run more than one instance —
      it is a single file on local disk. One box is fine for a launch.
- [ ] **Back up `out/billing.db`.** It is the only record of who paid you.
