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
import shutil
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


def post_json(path, obj, raw=None):
    """A JSON POST to an arbitrary endpoint; `raw` sends bytes verbatim, for the malformed cases."""
    data = raw if raw is not None else json.dumps(obj or {}).encode()
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as f:
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


def test_redeem_endpoint():
    """/redeem is public and unauthenticated, exactly like /claim. The only thing between it and
    free credits is that Gumroad is the one answering, so what is asserted here is that it
    refuses everything it can refuse before it ever gets that far."""
    print("\nredeem endpoint")
    reset()
    os.environ.pop("ATONAL_GUMROAD_TEN", None)
    st, body = post_json("/redeem", {"license": "anything"})
    check(st == 503 and "Gumroad" in str(body),
          "with no product configured it is a 503, not a 500 (%d %s)" % (st, body))

    os.environ["ATONAL_GUMROAD_TEN"] = "prod_ten"
    # STUBBED so this suite stays offline. Without it the unrecognised-licence case posts a
    # made-up key to api.gumroad.com -- slow, flaky without a network, and an outbound request
    # to somebody else's service from a test run. It returns None, which is what Gumroad answers
    # for a licence that is not theirs, so the path under test is the same one.
    real_verify = billing._gumroad_verify
    billing._gumroad_verify = lambda product_id, license_key, timeout=20: None
    try:
        st, _ = post_json("/redeem", {})
        check(st == 400, "a body with no licence is a 400 (%d)" % st)
        st, _ = post_json("/redeem", None, raw=b"{not json")
        check(st == 400, "and so is a body that is not JSON (%d)" % st)
        st, body = post_json("/redeem", {"license": "L-NOPE"})
        check(st == 400 and "not recognised" in str(body),
              "an unrecognised licence is refused (%d %s)" % (st, body))
        check(billing.free_left(IP) == billing.FREE_PER_DAY,
              "and none of it touched the free allowance")
        with billing._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        check(n == 0, "nor granted a credit")
    finally:
        billing._gumroad_verify = real_verify
        os.environ.pop("ATONAL_GUMROAD_TEN", None)


def test_truncated_upload():
    """A body that stops short of its Content-Length is a 400, and costs nothing.

    The upload is streamed to disk a megabyte at a time rather than read whole, so this case
    exists where it did not before: read() returns empty at the hang-up instead of raising, and
    without the short-read check the server would have gone on to analyse a truncated file --
    charging for it, and very likely failing inside ffmpeg where the error means nothing to
    anyone. Nothing has been charged at this point, and the assertions below say so.
    """
    print("truncated upload")
    import socket
    before_free = billing.free_left(IP)
    with billing._conn() as c:
        rows_before = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
    body = b"x" * 2048
    s.sendall(b"POST /analyze HTTP/1.1\r\nHost: x\r\n"
              b"Content-Length: 999999\r\nX-Filename: short.mp3\r\n\r\n" + body)
    s.shutdown(socket.SHUT_WR)          # promised 999999, sent 2048, then hung up
    resp = b""
    try:
        while True:
            b2 = s.recv(4096)
            if not b2:
                break
            resp += b2
    except OSError:
        pass
    s.close()
    status = resp.split(b" ")[1].decode() if resp.startswith(b"HTTP/") else "(no response)"
    check(status == "400", "a short body is answered 400, not dropped (%s)" % status)
    check(b"ended early" in resp, "and the reason says so")
    check(billing.free_left(IP) == before_free,
          "a truncated upload does not touch the free allowance")
    with billing._conn() as c:
        rows_after = c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    check(rows_after == rows_before, "nor writes a ledger row")


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANARY = b"CANARY-should-never-be-served"


def get(path):
    """(status, body). A refusal is a 404 here; anything else is the interesting case."""
    try:
        r = urllib.request.urlopen("http://127.0.0.1:%d%s" % (PORT, path), timeout=5)
        return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:                       # a 500, a reset, a traceback in the handler
        return type(e).__name__, str(e).encode()[:120]


def get_noredirect(path):
    """(status, Location). urlopen FOLLOWS a 301, which would hide the redirect entirely."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        r = urllib.request.build_opener(_NoRedirect).open(
            "http://127.0.0.1:%d%s" % (PORT, path), timeout=5)
        return r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location")
    except Exception as e:
        return type(e).__name__, str(e)[:120]


def test_static_allowlist():
    """What the server will hand out, and what it will not.

    _serve_static has no other test and its own comment records TWO breaks, both of which served
    source: a directory traversal ("assets/../server.py" passes a prefix test on the request and
    a containment test on the result while having left the prefix entirely), and a prefix match
    on the file entry, which also matched viewer.html.bak, viewer.html~ and viewer.html.orig --
    the editor leavings that collect beside exactly that file. Neither is reachable now. The
    point of writing it down is that both were reachable once, in a handler nobody was testing.

    The canaries are real files, planted and removed, because a 404 for a path that does not
    exist proves nothing at all about a path that does.

    MUTATION TESTED, six ways: serving on the raw request instead of the resolved path, a prefix
    match on the file entries, the trailing comma dropped from _STATIC_FILES, abspath instead of
    realpath, and the allowlist removed outright -- all five caught, 2 to 21 checks each.
    The sixth survives and should: deleting the `inside == os.pardir` containment check changes
    nothing, because realpath runs first, so a path that escaped the root arrives as "../..." and
    the ALLOWLIST is what refuses it. The containment check is the invariant stated plainly and
    is worth keeping; it is simply not the line doing the work.
    """
    print("static allowlist")
    for path, what in (("/", "the viewer at the root"),
                       ("/viewer.html", "the viewer by name"),
                       ("/site/pricing.html", "a page under site/"),
                       ("/assets/mesh_meta.json", "a file under assets/")):
        st, body = get(path)
        check(st == 200 and len(body) > 0, "%s is served (%s)" % (what, st))

    # Source, secrets and the operator's data. .env holds the Paddle and Gumroad keys and
    # out/billing.db is the revenue ledger.
    for path in ("/server.py", "/billing.py", "/analyze.py", "/tagger.py",
                 "/.env", "/out/billing.db", "/requirements.txt", "/tests/test_server.py"):
        st, body = get(path)
        check(st == 404, "%s is refused (%s)" % (path, st))

    # The traversal, in the spellings that reach the handler differently: the path is unquoted
    # BEFORE the allowlist runs, so the encoded forms arrive as the literal ones.
    for path in ("/assets/../server.py", "/assets/../../etc/passwd", "/%2e%2e%2fserver.py",
                 "/assets%2f..%2fserver.py", "/assets/./../server.py", "/./server.py",
                 "/site/../billing.py", "/assets/../.env"):
        st, body = get(path)
        check(st == 404 and CANARY not in body and b"import" not in body[:200],
              "traversal %s is refused (%s)" % (path, st))

    planted = []
    try:
        # Exact match on the file entries, not a prefix and not a substring. Without its trailing
        # comma _STATIC_FILES is a plain string and `rel_posix not in ...` becomes a substring
        # test, which serves any existing path that is a substring of "viewer.html". "r.html" and
        # ".html" are two; "ew.htm", which server.py's note used to give as the example, is not a
        # substring of viewer.html at all and would never have demonstrated anything. These are
        # planted as real files because the bug only serves paths that EXIST.
        for name in ("r.html", ".html"):
            p = os.path.join(ROOT, name)
            with open(p, "wb") as fh:
                fh.write(CANARY)
            planted.append(p)
            st, body = get("/" + name)
            check(st == 404 and CANARY not in body,
                  "%r is not matched as a substring of viewer.html (%s)" % (name, st))

        for name in ("viewer.html.bak", "viewer.html~", "viewer.html.orig", "viewer.html.rej"):
            p = os.path.join(ROOT, name)
            with open(p, "wb") as fh:
                fh.write(CANARY)
            planted.append(p)
            st, body = get("/" + name)
            check(st == 404 and CANARY not in body,
                  "the editor backup %s is refused even though it exists (%s)" % (name, st))

        # realpath collapses symlinks, so a link planted inside an allowed directory cannot
        # redirect out of it -- as a file, and as a directory somewhere along the path.
        link = os.path.join(ROOT, "assets", "_t_link.json")
        os.symlink(os.path.join(ROOT, "server.py"), link)
        planted.append(link)
        st, body = get("/assets/_t_link.json")
        check(st == 404 and b"import" not in body[:200],
              "a symlink out of assets/ is refused (%s)" % st)

        d = os.path.join(ROOT, "assets", "_t_dir")
        os.makedirs(d, exist_ok=True)
        dl = os.path.join(d, "up")
        os.symlink(ROOT, dl)
        planted.append(dl)
        planted.append(d)
        st, body = get("/assets/_t_dir/up/server.py")
        check(st == 404 and b"import" not in body[:200],
              "a symlinked DIRECTORY out of assets/ is refused (%s)" % st)
    finally:
        for p in reversed(planted):
            try:
                if os.path.islink(p) or os.path.isfile(p):
                    os.remove(p)
                elif os.path.isdir(p):
                    shutil.rmtree(p)
            except OSError:
                pass
        left = [p for p in planted if os.path.exists(p) or os.path.islink(p)]
        check(not left, "every planted canary was removed" + (" -- LEFT: %s" % left if left else ""))

    # Odd paths must fail closed rather than raise out of the handler: an unhandled exception
    # there is a traceback and a dropped connection, not a 404.
    for path in ("/assets/%00", "/assets/a%00b.json", "/assets/", "//server.py",
                 "/" + "a" * 300 + ".json"):
        st, _ = get(path)
        check(st == 404, "%r fails closed with a 404, not an error (%s)" % (path[:40], st))

    # A DIRECTORY WITH AN index.html SERVES IT; a directory without one does not become a
    # listing, and a directory the allowlist refuses does not become a redirect either. That
    # last one is the leak worth naming: deciding the 301 before the allowlist would answer
    # "/deploy" with a 301 to "/deploy/" and 404 only on the second request, which reports
    # that the directory exists to anyone who asks. /assets/ stays in the list above -- it is
    # allowlisted and has no index.html, so it still fails closed.
    st, body = get("/site/")
    check(st == 200 and b"<title>" in body.lower(),
          "/site/ serves site/index.html (%s)" % st)
    st_named, named = get("/site/index.html")
    check(st_named == 200 and named == body,
          "and it is byte-for-byte the same file as /site/index.html (%s)" % st_named)

    for path in ("/site", "/site?a=1"):
        st, loc = get_noredirect(path)
        check(st == 301 and loc == "/site/" + ("?a=1" if "?" in path else ""),
              "%s redirects to the slash so relative links resolve (%s %r)" % (path, st, loc))

    # Real directories in the project root, neither of them allowlisted.
    for path in ("/deploy", "/tests", "/deploy/"):
        st, loc = get_noredirect(path)
        check(st == 404,
              "%s is a directory the allowlist refuses, with no 301 first (%s %r)" % (path, st, loc))


if __name__ == "__main__":
    print("server tests — throwaway db and cache under %s" % TMP)
    for fn in (test_bad_requests_are_free, test_bad_key_is_rejected_before_charging,
               test_failed_analysis_refunds_a_credit, test_failed_analysis_refunds_the_free_tier,
               test_failure_leaves_nothing_behind, test_cache_hit_is_free,
               test_stale_entry_is_not_charged_again, test_redeem_endpoint,
               test_static_allowlist, test_truncated_upload):
        fn()
    print()
    if FAILURES:
        print("FAILED (%d):" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all passed")
