#!/usr/bin/env python3
"""A real ATONAL server with Gumroad configured and ONE thing stubbed: Gumroad itself.

    python tests/site_stub.py <port> <db-path>

Used by render_bench's `site` arm, and useful by hand when you want to click through the
purchase pages without a Gumroad account or a payment.

WHAT IS STUBBED AND WHAT IS NOT. Only `billing._gumroad_verify` -- the single call that leaves
the machine. The ledger, the claims table, the ownership race, `/redeem`, `/packs`, `/credits`
and both pages are the shipped ones. That is the point: a stub that reached further in would be
testing itself.

The database is whatever path you pass, and it is expected to be a throwaway. This never
touches out/billing.db; the arm that drives it creates a temp directory per run.
"""
import os
import sys

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

if __name__ == "__main__":
    srv = server.ThreadingHTTPServer(("127.0.0.1", PORT), server.H)
    print("site stub on http://127.0.0.1:%d  (db %s)" % (PORT, DB), flush=True)
    server._billing_banner()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
