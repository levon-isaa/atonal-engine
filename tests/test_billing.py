#!/usr/bin/env python3
"""Tests for the money path — the ledger, the free tier, and the Paddle webhook.

    python tests/test_billing.py

Dependency-free and offline, for the reason test_director.py gives, plus one of its own: this
suite must never need a network or a real provider, or it will not be run and the code it
covers is the code where a regression costs the customer money rather than a frame.

NOTHING HERE TOUCHES out/billing.db. billing.DB_PATH is repointed at a throwaway file per test
and billing._ready reset with it, so every test starts from an empty schema. The real database
is the operator's revenue record; a test suite has no business opening it.

WHAT IS ASSERTED IS WHAT THE MODULE PROMISES IN ITS OWN COMMENTS. Each test names the sentence
it is holding the code to, so a change that quietly stops honouring one fails here rather than
in production: idempotency on a provider retry, a balance that cannot go negative under
concurrent uploads, a free tier that is refunded when the analysis fails, a signature check
that is not optional, and a purchase whose credits land on the key the customer is shown.
"""
import hashlib
import hmac
import itertools
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="atonal-billing-")
os.environ["ATONAL_DB"] = os.path.join(TMP, "unused.db")
os.environ.setdefault("PADDLE_WEBHOOK_SECRET", "test-secret-not-a-real-one")

import billing  # noqa: E402

FAILURES = []
_n = itertools.count()


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILURES.append(msg)


def fresh():
    """A new, empty database. _ready has to be cleared too or init() short-circuits and the
    next test runs against the previous test's rows."""
    billing.DB_PATH = os.path.join(TMP, "b%d.db" % next(_n))
    billing._ready = False
    billing.init()


def balance_of_hash(kh):
    with billing._conn() as c:
        return c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE key_hash=?",
                         (kh,)).fetchone()[0] or 0


def sign(payload: bytes, ts=None, secret=None):
    """The header Paddle sends: ts=<unix>;h1=<hmac over `<ts>:<raw body>`>."""
    ts = str(int(time.time() if ts is None else ts))
    secret = secret or os.environ["PADDLE_WEBHOOK_SECRET"]
    mac = hmac.new(secret.encode(), (ts + ":").encode() + payload, hashlib.sha256).hexdigest()
    return "ts=%s;h1=%s" % (ts, mac)


# ------------------------------------------------------------------ the ledger

def test_ledger_basics():
    print("\nledger")
    fresh()
    k = billing.new_key()
    check(not billing.key_exists(k), "an unissued key does not exist")
    check(billing.balance(k) == 0, "and has a balance of zero rather than raising")

    kh = billing._hash(k)
    check(billing.grant(kh, 10, "test", "ref-1") is True, "a first grant applies")
    check(billing.balance(k) == 10, "and the balance is the sum of the ledger")
    # "Returns False if `ref` was already applied -- which is the normal, expected outcome of
    # a provider webhook retry, not an error."
    check(billing.grant(kh, 10, "test", "ref-1") is False, "the same ref twice grants nothing")
    check(billing.balance(k) == 10, "and leaves the balance alone -- a webhook retry is free")

    check(billing.spend(k, "analyze", "s-1") is True, "a spend with credits succeeds")
    check(billing.balance(k) == 9, "and takes exactly one")
    for i in range(9):
        billing.spend(k, "analyze", "s-%d" % (i + 2))
    check(billing.balance(k) == 0, "spending down reaches exactly zero")
    check(billing.spend(k, "analyze", "s-99") is False, "and the next spend is refused")
    check(billing.balance(k) == 0, "with nothing deducted -- the balance never goes negative")

    # "a crash on our side must never cost them"
    billing.grant(kh, 1, "test", "ref-2")
    billing.spend(k, "analyze", "s-100")
    billing.refund(k, "refund: failed", "rf-1")
    check(billing.balance(k) == 1, "a refund gives the credit back")
    billing.refund(k, "refund: failed", "rf-1")
    check(billing.balance(k) == 1, "and a repeated refund on one ref cannot mint a second")


def test_spend_is_atomic():
    """"Read-then-write across two statements is the classic way to let two concurrent uploads
    both see a balance of 1 and both spend it." The BEGIN IMMEDIATE is the claim; this is the
    only test here that can falsify it."""
    print("\nconcurrent spending")
    fresh()
    k = billing.new_key()
    billing.grant(billing._hash(k), 20, "test", "ref-c")
    won = []
    start = threading.Barrier(40)

    def go(i):
        start.wait()
        if billing.spend(k, "analyze", "c-%d" % i):
            won.append(i)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check(len(won) == 20, "40 threads against 20 credits: %d spends succeeded, expected 20"
          % len(won))
    check(billing.balance(k) == 0, "and the balance landed on zero, not below (%d)"
          % billing.balance(k))


def test_priority_is_ever_not_currently():
    """"someone who bought a Pack of 50, spent it and topped up with a single does not lose the
    thing they paid for.\""""
    print("\npriority")
    fresh()
    k = billing.new_key()
    kh = billing._hash(k)
    check(not billing.has_priority(k), "a key that bought nothing has no priority")
    billing.grant(kh, 50, "purchase:fifty:txn_a", "paddle:txn_a")
    check(billing.has_priority(k), "the pack carrying priority grants it")
    for i in range(50):
        billing.spend(k, "analyze", "p-%d" % i)
    check(billing.balance(k) == 0 and billing.has_priority(k),
          "and spending the pack to zero does not take it away")
    # "no pack is named 'txn_...', so has_priority cannot confuse the two"
    fresh()
    k2 = billing.new_key()
    billing.grant(billing._hash(k2), 10, "purchase:txn_legacy", "paddle:txn_legacy")
    check(not billing.has_priority(k2), "an old-style reason is not mistaken for a pack name")


# ------------------------------------------------------------------ the free tier

def test_free_tier():
    print("\nfree tier")
    fresh()
    n = billing.FREE_PER_DAY
    ip = "203.0.113.7"
    got = sum(1 for _ in range(n + 3) if billing.free_take(ip))
    check(got == n, "an IP gets exactly FREE_PER_DAY (%d) analyses, took %d" % (n, got))
    check(billing.free_left(ip) == 0, "and free_left reports none remaining")
    check(billing.free_take("198.51.100.9") is True, "a different IP is unaffected")

    # "a free upload that failed to decode took the day's allowance and never gave it back"
    day = billing.utc_day()
    billing.free_refund(ip, day)
    check(billing.free_take(ip) is True, "a refunded free analysis can be taken again")
    # "Floored at zero ... so a double call cannot mint allowance"
    for _ in range(10):
        billing.free_refund(ip, day)
    got = sum(1 for _ in range(n + 3) if billing.free_take(ip))
    check(got == n, "ten refunds cannot mint more than the day's allowance (took %d, cap %d)"
          % (got, n))
    # "a refund that crosses UTC midnight cannot credit a day that was never charged"
    billing.free_refund(ip, "1999-01-01")
    check(billing.free_take(ip) is False, "a refund against another day credits nothing today")


# ------------------------------------------------------------------ the webhook

def test_webhook_signature():
    """"Signature verification is not optional: without it this endpoint is an unauthenticated
    'give me credits' API, and the URL is public.\""""
    print("\nwebhook signature")
    fresh()
    body = b'{"event_type":"transaction.completed","data":{"id":"txn_sig","status":"completed",' \
           b'"custom_data":{"pack":"ten","credits":10}}}'

    def rejected(sig, _label=""):
        try:
            billing.webhook(body, sig)
            return False, "accepted"
        except ValueError as e:
            return True, str(e)
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e)

    ok, why = rejected("", "empty")
    check(ok, "an unsigned request is refused (%s)" % why)
    ok, why = rejected("ts=%d;h1=%s" % (time.time(), "0" * 64), "wrong mac")
    check(ok, "a wrong signature is refused (%s)" % why)
    ok, why = rejected(sign(body, secret="not-the-secret"), "wrong secret")
    check(ok, "a signature made with another secret is refused (%s)" % why)
    ok, why = rejected(sign(body + b" "))
    check(ok, "a body altered after signing is refused (%s)" % why)

    # "A correctly signed request replayed a day later is still a replay." And the two checks
    # are separate on purpose, so a stale replay does not report as a malformed timestamp.
    ok, why = rejected(sign(body, ts=time.time() - billing.PADDLE_MAX_SKEW - 60))
    check(ok and "window" in why,
          "a stale timestamp is refused as a window failure, not a parse one (%s)" % why)
    ok, why = rejected("ts=notanumber;h1=" + "0" * 64)
    check(ok and "timestamp" in why and "window" not in why,
          "and an unparseable timestamp still reports as one (%s)" % why)

    check(billing.webhook(body, sign(body)).get("ok") is True,
          "a correctly signed request is accepted")


def test_webhook_grants_once():
    """The UNIQUE ref "is the whole idempotency story: the payment provider retries webhooks,
    sometimes for days.\""""
    print("\nwebhook idempotency")
    fresh()
    body = b'{"event_type":"transaction.completed","data":{"id":"txn_rep","status":"completed",' \
           b'"customer":{"email":"Buyer@Example.COM"},' \
           b'"custom_data":{"pack":"ten","credits":10}}}'
    for _ in range(4):
        billing.webhook(body, sign(body))
    with billing._conn() as c:
        rows = c.execute("SELECT key_hash, SUM(delta) FROM ledger GROUP BY key_hash").fetchall()
    check(len(rows) == 1 and rows[0][1] == 10,
          "four deliveries of one transaction grant 10 credits once (%s)" % (rows,))

    # "A REPEAT PURCHASE TOPS UP THE EXISTING KEY rather than issuing a second one."
    body2 = b'{"event_type":"transaction.completed","data":{"id":"txn_rep2","status":"completed",' \
            b'"customer":{"email":"buyer@example.com"},' \
            b'"custom_data":{"pack":"single","credits":1}}}'
    billing.webhook(body2, sign(body2))
    with billing._conn() as c:
        rows = c.execute("SELECT key_hash, SUM(delta) FROM ledger GROUP BY key_hash").fetchall()
    check(len(rows) == 1 and rows[0][1] == 11,
          "a second purchase from the same address tops the same key up to 11 (%s)" % (rows,))

    # an event that is not a completed transaction must move nothing
    fresh()
    idle = b'{"event_type":"transaction.updated","data":{"id":"txn_x","status":"ready",' \
           b'"custom_data":{"pack":"ten","credits":10}}}'
    billing.webhook(idle, sign(idle))
    with billing._conn() as c:
        n = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    check(n == 0, "an event that is not a completed transaction grants nothing")


def test_grant_races_agree_on_one_key():
    """"the webhook and the success page can both call it and only one wins."

    They could not. The ledger's UNIQUE ref made the GRANT single and nothing made the claims
    row single, so both callers minted a fresh key, one won the ledger and the other won the
    claims row -- and the customer was shown a key holding zero of the credits they had paid
    for, with the funded key's plaintext discarded. MEASURED against the code before the fix:
    38 of 300 two-thread trials, 0 of 300 after.
    """
    print("\nconcurrent claim")
    bad = 0
    trials = 60
    for i in range(trials):
        fresh()
        txn = {"id": "txn_race_%d" % i, "custom_data": {"credits": 50, "pack": "fifty"}}
        start = threading.Barrier(2)
        errs = []

        def go():
            try:
                start.wait()
                billing._grant_for_session(dict(txn))
            except Exception as e:      # a raised grant is a different failure; record it
                errs.append(e)

        ts = [threading.Thread(target=go) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        with billing._conn() as c:
            row = c.execute("SELECT key_hash, credits FROM claims WHERE session_id=?",
                            (txn["id"],)).fetchone()
        if not row or errs or balance_of_hash(row[0]) != row[1]:
            bad += 1
    check(bad == 0,
          "%d concurrent claims: the key the customer is shown holds the credits (%d wrong)"
          % (trials, bad))


def test_claim_plaintext_expires():
    """"After this the database holds nothing that can be used to spend credits.\""""
    print("\nclaim expiry")
    fresh()
    out = billing._grant_for_session({"id": "txn_exp",
                                      "custom_data": {"credits": 10, "pack": "ten"}})
    check(bool(out.get("key")), "a fresh purchase returns a usable key")
    billing.expire_claims()
    with billing._conn() as c:
        still = c.execute("SELECT key_plain FROM claims WHERE session_id=?",
                          ("txn_exp",)).fetchone()[0]
    check(still is not None, "a claim inside the TTL keeps its plaintext for the success page")
    with billing._conn() as c:
        c.execute("UPDATE claims SET created=? WHERE session_id=?",
                  (time.time() - billing.CLAIM_TTL - 60, "txn_exp"))
    billing.expire_claims()
    with billing._conn() as c:
        gone = c.execute("SELECT key_plain FROM claims WHERE session_id=?",
                         ("txn_exp",)).fetchone()[0]
    check(gone is None, "and past the TTL the plaintext is dropped")
    check(billing.balance(out["key"]) == 10,
          "while the credits themselves survive on the hash")


def test_claim():
    """claim() is what actually hands a customer their key, and it has four outcomes: not paid,
    a fresh purchase, a repeat purchase whose plaintext has been wiped, and the same again from
    a browser that still holds its copy.

    _paddle is stubbed. It is the one call in this module that leaves the machine, and the point
    of claim() is that it re-reads the transaction FROM the provider rather than trusting the
    return URL -- so what is asserted is that a transaction the provider does not call paid
    yields nothing, whatever the query string said.
    """
    print("\nclaim")
    real = billing._paddle
    txns = {}

    def fake(method, path, body=None, timeout=20):
        return txns.get(path.rsplit("/", 1)[-1], {})
    billing._paddle = fake
    try:
        fresh()
        txns["txn_unpaid"] = {"id": "txn_unpaid", "status": "ready",
                              "custom_data": {"pack": "ten", "credits": 10}}
        out = billing.claim("txn_unpaid")
        check(out.get("error") == "not paid", "a transaction the provider has not marked paid "
              "is refused (%s)" % out)
        with billing._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        check(n == 0, "and grants nothing -- the return URL is just a redirect anyone can craft")

        txns["txn_a"] = {"id": "txn_a", "status": "completed",
                         "customer": {"email": "buyer@example.com"},
                         "custom_data": {"pack": "ten", "credits": 10}}
        out = billing.claim("txn_a")
        key = out.get("key")
        check(bool(key) and out.get("credits") == 10 and out.get("balance") == 10,
              "a fresh purchase returns the key, its credits and its balance (%s)"
              % {k: v for k, v in out.items() if k != "key"})
        check("key_hash" not in out, "and never the key_hash -- it is popped before the response")

        # the webhook and the success page racing is the ordinary case, not the exotic one
        again = billing.claim("txn_a")
        check(again.get("key") == key and billing.balance(key) == 10,
              "claiming the same transaction twice returns the same key and grants once (%d)"
              % billing.balance(key))

        # ---- the repeat purchase, past the window where the plaintext still exists ----
        with billing._conn() as c:
            c.execute("UPDATE claims SET created=? WHERE session_id=?",
                      (time.time() - billing.CLAIM_TTL - 60, "txn_a"))
        txns["txn_b"] = {"id": "txn_b", "status": "completed",
                         "customer": {"email": "buyer@example.com"},
                         "custom_data": {"pack": "single", "credits": 1}}
        out = billing.claim("txn_b")
        check(out.get("topped_up") is True and not out.get("key") and out.get("balance") == 11,
              "a repeat purchase with no key to show is a top-up, not an error (%s)" % out)
        check(billing.balance(key) == 11, "and the credits landed on the original key (%d)"
              % billing.balance(key))

        # the browser still holds its copy: confirm, do not reveal
        with billing._conn() as c:
            c.execute("DELETE FROM claims WHERE session_id=?", ("txn_b",))
        out = billing.claim("txn_b", have_key=key)
        check(out.get("restored") is True and out.get("key") == key,
              "a browser that still holds the key gets it confirmed (%s)" % out.get("restored"))

        # AND A WRONG ONE MUST NOT BE. This is the security property in the branch: the compare
        # is against a hash, so a guess must come back with no key and no confirmation.
        with billing._conn() as c:
            c.execute("DELETE FROM claims WHERE session_id=?", ("txn_b",))
        out = billing.claim("txn_b", have_key=billing.new_key())
        check(not out.get("key") and not out.get("restored"),
              "a key that is not this customer's is neither confirmed nor revealed (%s)" % out)
        with billing._conn() as c:
            c.execute("DELETE FROM claims WHERE session_id=?", ("txn_b",))
        out = billing.claim("txn_b", have_key=key[:-1] + ("x" if key[-1] != "x" else "y"))
        check(not out.get("key") and not out.get("restored"),
              "nor is one that differs by a single character (%s)" % out)
    finally:
        billing._paddle = real


if __name__ == "__main__":
    print("billing tests — throwaway database under %s" % TMP)
    for fn in (test_ledger_basics, test_spend_is_atomic, test_priority_is_ever_not_currently,
               test_free_tier, test_webhook_signature, test_webhook_grants_once,
               test_grant_races_agree_on_one_key, test_claim_plaintext_expires,
               test_claim):
        fn()
    print()
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all passed")
