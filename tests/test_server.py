#!/usr/bin/env python3
"""Tests for the upload path in server.py — the gate, and what happens to a credit when the
analysis fails.

    python tests/test_server.py

The server is started IN PROCESS on an ephemeral port, with billing.DB_PATH and
server.CACHE_DIR both repointed at a throwaway directory. Nothing here touches out/billing.db
or out/cache: the first is the operator's revenue record and the second is analyses that were
paid for. (Both were learned the hard way. An earlier session deleted eleven cached analyses
with a wildcard and could not put them back.)

WHAT THIS COVERS THAT test_billing.py CANNOT. billing.py knows how to refund; it does not know
whether the upload path actually calls it. The promise "a crash on our side must never cost the
customer a credit" lives in an `except` clause in do_POST, and free_refund's own docstring
records that it was checked once, by hand, against a running server -- reset the counter, POST
an undecodable body, read the counter back. That is exactly the check that should not depend on
someone remembering to do it again.

An undecodable body is used rather than a mocked failure, because the mapping from "ffmpeg said
no" to a 500 that reads `could not decode audio` is part of what is being asserted. It costs
about a tenth of a second: ffmpeg rejects the bytes without librosa ever being imported.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix="atonal-server-")
os.environ["ATONAL_DB"] = os.path.join(TMP, "billing.db")
os.environ["ATONAL_NO_WARM"] = "1"      # no librosa warm-up thread; nothing here analyses audio

import analyze   # noqa: E402
import billing   # noqa: E402
import server    # noqa: E402

server.CACHE_DIR = os.path.join(TMP, "cache")

FAILURES = []
GARBAGE = b"this is not audio and ffmpeg will say so." * 40
IP = "127.0.0.1"


def check(cond, msg):
    if cond:
        print(f"  ok   {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILURES.append(msg)


def start():
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


SRV, PORT = start()


def post(body, key=None, name="upload.mp3", length=None):
    """Returns (status, parsed json). `length` overrides Content-Length, for the header tests."""
    h = {"X-Filename": name}
    if key:
        h["X-Render-Key"] = key
    if length is not None:
        h["Content-Length"] = str(length)
    req = urllib.request.Request("http://127.0.0.1:%d/analyze" % PORT, data=body,
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as f:
            return f.status, json.loads(f.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}


def reset():
    """Empty billing between tests. Every request here comes from 127.0.0.1, so the free
    counter carries over otherwise and the second test to run starts with none left."""
    billing.init()
    with billing._conn() as c:
        c.execute("DELETE FROM free_use")
        c.execute("DELETE FROM ledger")
        c.execute("DELETE FROM keys")
        c.execute("DELETE FROM claims")
    os.makedirs(server.CACHE_DIR, exist_ok=True)
    for f in os.listdir(server.CACHE_DIR):
        os.remove(os.path.join(server.CACHE_DIR, f))


def funded(n=3):
    k = billing.new_key()
    billing.grant(billing._hash(k), n, "test", "t-%s" % k[-8:])
    return k


# ------------------------------------------------------------------ the gate

def test_bad_requests_are_free():
    """Nothing malformed may reach the gate: a request that never had a chance to be analysed
    must not cost an allowance."""
    print("\nmalformed requests")
    reset()
    st, _ = post(b"")
    check(st == 400, "an empty body is a 400 (got %d)" % st)
    st, _ = post(b"x" * 10, length="not-a-number")
    check(st == 400, "a non-numeric Content-Length is a 400, not a dropped connection (%d)" % st)
    old = server.MAX_UPLOAD
    try:
        server.MAX_UPLOAD = 64
        st, body = post(b"x" * 4096)
        check(st == 413 and "too large" in str(body), "an oversize body is a 413 (%d %s)"
              % (st, body))
    finally:
        server.MAX_UPLOAD = old
    check(billing.free_left(IP) == billing.FREE_PER_DAY,
          "and none of the three touched the free allowance (%d of %d left)"
          % (billing.free_left(IP), billing.FREE_PER_DAY))


def test_bad_key_is_rejected_before_charging():
    print("\nkey checks")
    reset()
    st, body = post(GARBAGE, key="atk_not_a_real_key")
    check(st == 402 and body.get("code") == "bad_key",
          "an unknown key is refused with bad_key (%d %s)" % (st, body.get("code")))
    check(billing.free_left(IP) == billing.FREE_PER_DAY,
          "and a rejected key does not silently spend the free allowance instead")

    k = billing.new_key()
    billing.grant(billing._hash(k), 1, "test", "t-empty")
    billing.spend(k, "test", "t-spend")
    st, body = post(GARBAGE, key=k)
    check(st == 402 and body.get("code") == "no_credits",
          "a key with no credits is refused with no_credits (%d %s)" % (st, body.get("code")))


# ------------------------------------------------------------------ the refunds

def test_failed_analysis_refunds_a_credit():
    """"A crash on our side must never cost the customer a credit.\""""
    print("\nrefund on failure, paid")
    reset()
    k = funded(3)
    st, body = post(GARBAGE, key=k)
    check(st == 500 and body.get("error") == "could not decode audio",
          "an undecodable upload is a 500 that names the cause (%d %s)" % (st, body.get("error")))
    check(billing.balance(k) == 3,
          "and the credit is given back: balance %d, expected 3" % billing.balance(k))
    # the raw exception carried absolute paths and the ffmpeg command line; it must not ship
    check("ffmpeg" not in json.dumps(body).lower() and "/" not in body.get("error", ""),
          "the response carries no filesystem path or command line (%s)" % body)


def test_failed_analysis_refunds_the_free_tier():
    """free_refund's docstring: "a free upload that failed to decode took the day's allowance
    and never gave it back, so the person most likely to be evaluating the product lost their
    try to a file we could not read". That was verified once by hand. This is that check."""
    print("\nrefund on failure, free tier")
    reset()
    before = billing.free_left(IP)
    st, body = post(GARBAGE)
    check(st == 500, "an undecodable free upload is a 500 (%d)" % st)
    check(billing.free_left(IP) == before,
          "and the day's allowance is untouched: %d left, was %d"
          % (billing.free_left(IP), before))
    # and the allowance still runs out normally, so the refund did not disable the limit
    reset()
    for _ in range(billing.FREE_PER_DAY):
        billing.free_take(IP)
    st, body = post(GARBAGE)
    check(st == 402 and body.get("code") == "free_used",
          "with the day spent, an upload is refused rather than analysed (%d %s)"
          % (st, body.get("code")))


def test_failure_leaves_nothing_behind():
    """"ran only on success before, so every failed upload left its temp file behind\""""
    print("\ncleanup")
    reset()
    d = tempfile.gettempdir()
    before = {f for f in os.listdir(d) if f.endswith(".mp3")}
    for _ in range(5):
        post(GARBAGE, key=funded(1))
    time.sleep(0.2)
    after = {f for f in os.listdir(d) if f.endswith(".mp3")}
    check(not (after - before), "five failed uploads left no temp file behind (%s)"
          % sorted(after - before)[:4])
    check(not server._PROGRESS, "and no progress entry is left for a job that failed (%s)"
          % dict(list(server._PROGRESS.items())[:2]))


# ------------------------------------------------------------------ the cache

def _seed(body, version):
    """Put a cache entry where the digest of `body` will find it."""
    import hashlib
    digest = hashlib.sha256(body).hexdigest()
    server.cache_put(digest, {"meta": {"analysis_version": version}, "sections": [],
                              "tempo": {"bpm": 120}, "genre": {"primary": "test"}})
    return digest


def test_cache_hit_is_free():
    """"a cache hit does no work, so charging for it would be charging for a dictionary lookup\""""
    print("\ncache")
    reset()
    _seed(GARBAGE, analyze.ANALYSIS_VERSION)
    k = funded(2)
    st, body = post(GARBAGE, key=k)
    check(st == 200 and (body.get("meta") or {}).get("analysis_version") == analyze.ANALYSIS_VERSION,
          "a cached entry is served (%d)" % st)
    check(billing.balance(k) == 2, "and costs nothing: balance %d, expected 2"
          % billing.balance(k))
    check(billing.free_left(IP) == billing.FREE_PER_DAY,
          "nor does it take from the free tier")


def test_stale_entry_is_not_charged_again():
    """"Bumping ANALYSIS_VERSION emptied it silently: every track a customer had already paid to
    analyse became chargeable again, through no act of theirs.\"

    The re-analysis fails here, because the bytes are not audio -- which is the point. What is
    asserted is that the GATE was SKIPPED.

    NOT BY THE BALANCE, and this is the whole reason these two assertions look the way they do.
    Written the obvious way -- charge, fail, compare the balance -- the test passes whether the
    gate is skipped or not, because a failed analysis refunds and the net is the same either
    way. Caught by mutation: replacing `if cache_stale(digest)` with `if False` left this test
    green while every other one here went red. So it reads the LEDGER for a spend that should
    never have happened, and the free counter for a row that should never have been written.
    """
    print("\nstale cache entry")
    reset()
    _seed(GARBAGE, analyze.ANALYSIS_VERSION - 1)
    k = funded(2)
    st, _ = post(GARBAGE, key=k)
    check(st == 500, "a stale entry is re-analysed rather than served (%d)" % st)
    with billing._conn() as c:
        spends = c.execute("SELECT COUNT(*) FROM ledger WHERE key_hash=? AND delta<0",
                           (billing._hash(k),)).fetchone()[0]
    check(spends == 0,
          "and no credit is ever taken for it: %d spend rows in the ledger, expected 0" % spends)
    check(billing.balance(k) == 2, "leaving the balance at 2 (%d)" % billing.balance(k))
    reset()
    _seed(GARBAGE, analyze.ANALYSIS_VERSION - 1)
    post(GARBAGE)
    with billing._conn() as c:
        rows = c.execute("SELECT COUNT(*) FROM free_use WHERE ip=?", (IP,)).fetchone()[0]
    check(rows == 0,
          "and on the free tier the day is never taken from: %d free_use rows, expected 0" % rows)

    # "A corrupt or unreadable entry is deliberately NOT stale: it proves nothing about what
    # was analysed, so it falls through and is treated as new."
    reset()
    import hashlib
    with open(server.cache_path(hashlib.sha256(GARBAGE).hexdigest()), "w") as fh:
        fh.write("{not json")
    k = funded(2)
    st, _ = post(GARBAGE, key=k)
    check(st == 500 and billing.balance(k) == 2,
          "a corrupt entry is treated as new -- charged, then refunded on failure (balance %d)"
          % billing.balance(k))


if __name__ == "__main__":
    print("server tests — throwaway db and cache under %s" % TMP)
    for fn in (test_bad_requests_are_free, test_bad_key_is_rejected_before_charging,
               test_failed_analysis_refunds_a_credit, test_failed_analysis_refunds_the_free_tier,
               test_failure_leaves_nothing_behind, test_cache_hit_is_free,
               test_stale_entry_is_not_charged_again):
        fn()
    print()
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all passed")
