#!/usr/bin/env python3
"""A real ATONAL server with Gumroad configured and ONE thing stubbed: Gumroad itself.

    python tests/site_stub.py <port> <db-path>
    ATONAL_STUB_PADDLE=1 python tests/site_stub.py <port> <db-path>   # + a fake Paddle

Used by render_bench's `site` arm, and useful by hand when you want to click through the
purchase pages without a Gumroad account or a payment.

WHAT IS STUBBED AND WHAT IS NOT. Only the two calls that leave the machine:
`billing._gumroad_verify`, and -- when ATONAL_STUB_PADDLE is set -- `billing._paddle`, which is
the ONE function through which everything Paddle-side goes. The ledger, the claims table, the
ownership race, `/redeem`, `/claim`, `/packs`, `/credits` and all three pages are the shipped
ones. That is the point: a stub that reached further in would be testing itself. In particular
`_grant_for_session` and `_customer_email` stay real, which is why the fake transactions below
carry a `customer_id` and no inline address -- the same shape Paddle answers with, so the second
call the grant makes to resolve an email is exercised rather than skipped.

PADDLE IS OFF BY DEFAULT, and not out of caution: `billing_ready()` reads PADDLE_API_KEY, and
pricing.html renders a different page depending on it. The `site` arm measures that page in its
Gumroad-only state, so turning Paddle on here would silently re-point an existing measurement.
The `claim` arm sets ATONAL_STUB_PADDLE=1 and gets its own server.

The database is whatever path you pass, and it is expected to be a throwaway. This never
touches out/billing.db; the arm that drives it creates a temp directory per run.
"""
import os
import sys
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8772
DB = sys.argv[2] if len(sys.argv) > 2 else "/tmp/atonal-site-stub.db"

os.environ["ATONAL_DB"] = DB
os.environ["ATONAL_NO_WARM"] = "1"          # nothing here analyses audio
os.environ["ATONAL_PORT"] = str(PORT)
# Two packs on sale through Gumroad and one deliberately not, so the pages can be checked for
# the thing that is easy to get wrong: offering a pack that has no product id behind it.
os.environ["ATONAL_GUMROAD_TEN"] = "prod_ten"
os.environ["ATONAL_GUMROAD_LINK_TEN"] = "https://example.gumroad.com/l/atonal10"
os.environ["ATONAL_GUMROAD_FIFTY"] = "prod_fifty"
os.environ["ATONAL_GUMROAD_LINK_FIFTY"] = "https://example.gumroad.com/l/atonal50"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import billing   # noqa: E402
import server    # noqa: E402

# The licences this stub knows about. A good one, and one Gumroad still reports as valid after
# the money went back -- which it does, and which redeem() has to read rather than assume.
LICENCES = {
    "ATONAL-TEST-LICENCE-0001": {"_p": "prod_ten", "success": True,
                                 "purchase": {"sale_id": "sale_good",
                                              "email": "buyer@example.com"}},
    "ATONAL-REFUNDED-0002": {"_p": "prod_ten", "success": True,
                             "purchase": {"sale_id": "sale_refunded",
                                          "email": "refund@example.com",
                                          "refunded": True}},
    "ATONAL-BULK-0003": {"_p": "prod_fifty", "success": True,
                         "purchase": {"sale_id": "sale_bulk", "email": "bulk@example.com",
                                      "quantity": 2}},
}


def _verify(product_id, license_key, timeout=20):
    """None for a licence that is not this product's, which is what Gumroad answers with a 404
    -- the caller tries each configured product in turn and must not treat that as an error."""
    d = LICENCES.get((license_key or "").strip())
    return d if (d and d["_p"] == product_id) else None


billing._gumroad_verify = _verify


# ---------------------------------------------------------------- paddle, optionally
# The transactions the success page can be pointed at. They are the four outcomes /claim has --
# unpaid, a first purchase, a repeat purchase by the same customer, and a transaction that
# carries no credit count -- plus "no such transaction", which is the unknown id falling through
# to the raise below. Paddle reports the buyer as an id and not an address, so resolving it is
# a second call, and that is how it is spelled here.
TXNS = {
    # `ready` is a real Paddle status: the checkout exists and the money has not arrived.
    "txn_unpaid": {"id": "txn_unpaid", "status": "ready", "customer_id": "cus_one",
                   "custom_data": {"pack": "ten", "credits": "10"}},
    "txn_first": {"id": "txn_first", "status": "completed", "customer_id": "cus_one",
                  "custom_data": {"pack": "ten", "credits": "10"}},
    # Same customer, so this one lands on the key the first purchase issued.
    "txn_second": {"id": "txn_second", "status": "completed", "customer_id": "cus_one",
                   "custom_data": {"pack": "ten", "credits": "10"}},
    # `paid` rather than `completed`: on some payment methods that is the state the customer
    # comes back on, and the money is ours in both.
    "txn_paid_state": {"id": "txn_paid_state", "status": "paid", "customer_id": "cus_two",
                       "custom_data": {"pack": "fifty", "credits": "50"}},
    # Paid, and nothing says what was bought. Whatever this is, it is not a key.
    "txn_nopack": {"id": "txn_nopack", "status": "completed", "customer_id": "cus_two",
                   "custom_data": {}},
}

# A TRANSACTION THAT SETTLES A MOMENT AFTER THE REDIRECT. Paddle sends the browser back to the
# return URL as soon as the checkout is done with it, and the transaction reaches its terminal
# state separately -- immediately for a card that authorises, several seconds later for one that
# goes through 3-D Secure, longer for a bank transfer. Until then a read of it answers `ready`,
# which is the customer's money on its way and not a failure.
#
# ON THE CLOCK, NOT ON A COUNT OF READS, because that is the thing the success page's retry
# schedule has to be long enough for and a count is not: a page that asks once and gives up
# passes a "settles on the third read" stub as easily as one that waits half a minute. The
# default is set to a 3-D Secure round trip.
SETTLE_SECS = float(os.environ.get("ATONAL_STUB_SETTLE_SECS", "9.0"))
_settling = {"t0": None}
CUSTOMERS = {"cus_one": "buyer@example.com", "cus_two": "second@example.com",
             "cus_three": "settles@example.com"}


def _paddle(method, path, body=None, timeout=20):
    """Raises on anything it does not know, because that is what the real one does with a 404 --
    and a stub that answered {} instead would be hiding the case where a success page is pointed
    at an id that never existed."""
    if method == "GET" and path.startswith("/transactions/"):
        tid = path.rsplit("/", 1)[-1]
        if tid == "txn_settling":
            if _settling["t0"] is None:
                _settling["t0"] = time.time()
            done = (time.time() - _settling["t0"]) >= SETTLE_SECS
            return {"id": tid, "customer_id": "cus_three",
                    "status": "completed" if done else "ready",
                    "custom_data": {"pack": "ten", "credits": "10"}}
        t = TXNS.get(tid)
        if t:
            return t
    elif method == "GET" and path.startswith("/customers/"):
        e = CUSTOMERS.get(path.rsplit("/", 1)[-1])
        if e:
            return {"id": path.rsplit("/", 1)[-1], "email": e}
    raise RuntimeError("paddle %s %s -> 404 (stub)" % (method, path))


if os.environ.get("ATONAL_STUB_PADDLE"):
    # billing_ready() reads this and nothing else; the value is never sent anywhere because
    # _paddle above is what would have sent it.
    os.environ["PADDLE_API_KEY"] = "pdl_stub_key_not_a_real_one"
    billing._paddle = _paddle

if __name__ == "__main__":
    srv = server.ThreadingHTTPServer(("127.0.0.1", PORT), server.H)
    print("site stub on http://127.0.0.1:%d  (db %s)" % (PORT, DB), flush=True)
    server._billing_banner()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
