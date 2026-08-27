"""
ATONAL — credits, keys and Stripe.

WHAT A CREDIT BUYS, AND WHY IT IS THE ANALYSIS.
The export runs entirely in the browser: viewer.html encodes through WebCodecs
and muxes the MP4 itself, and makes no server call to do it. So "pay per export"
cannot be enforced — once the director JSON is in the page there is nothing left
to withhold. The analysis is the opposite: it is a POST to this server, it is
about ten seconds of CPU and ~2GB of RAM, and it is the ONLY thing a visitor can
cost us. Price follows cost, enforcement follows the network boundary.

A credit therefore buys one analysed track, and re-rendering or re-exporting that
track afterwards is free and unlimited — which is generous to the customer and
costs us nothing, because the render was never ours to pay for.

CACHE HITS ARE FREE. The analysis cache is keyed on audio content, so the same
file uploaded again does no work; charging for it would be charging for a
dictionary lookup. See the gate in server.py.

NO ACCOUNTS. Stripe Checkout already collects an email and already proves
payment, so a second identity system on top of it would be pure liability. A
purchase issues one long random RENDER KEY; the viewer stores it and sends it
with each analysis. There is no password to reset and no session to steal.

Keys are stored HASHED, like passwords — the database never holds a usable key
after the claim window closes. The one deliberate exception is documented on
`claims` below.
"""

import os, sqlite3, secrets, hashlib, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ATONAL_DB", os.path.join(HERE, "out", "billing.db"))

# Free analyses per IP per day, before a key is needed. The point is not to be
# generous, it is to let someone try the thing before paying while keeping the
# unbounded-cost-with-zero-revenue case closed.
FREE_PER_DAY = int(os.environ.get("ATONAL_FREE_PER_DAY", "2"))

# The packs. Amounts are in cents and are PLACEHOLDERS until real Stripe Prices
# exist; ATONAL_PRICE_<PACK> overrides each with a Stripe price id, which is what
# should be used in production so the amount lives in Stripe rather than here.
PACKS = {
    "single": {"credits": 1,  "amount": 600,   "label": "Single track"},
    "ten":    {"credits": 10, "amount": 4500,  "label": "Pack of 10"},
    "fifty":  {"credits": 50, "amount": 17500, "label": "Pack of 50"},
}
CURRENCY = os.environ.get("ATONAL_CURRENCY", "eur")

_init_lock = threading.Lock()
_ready = False


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.execute("PRAGMA journal_mode=WAL")     # ThreadingHTTPServer: concurrent readers
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init():
    """Idempotent. Safe to call on every request; the flag keeps it to one pass."""
    global _ready
    if _ready:
        return
    with _init_lock:
        if _ready:
            return
        with _conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS keys(
              key_hash TEXT PRIMARY KEY,
              email    TEXT,
              created  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS keys_email ON keys(email);

            -- Append only. The balance is SUM(delta); there is no stored total to
            -- drift out of step with its own history, and every credit that ever
            -- appeared or disappeared has a row saying why.
            CREATE TABLE IF NOT EXISTS ledger(
              id       INTEGER PRIMARY KEY AUTOINCREMENT,
              key_hash TEXT NOT NULL,
              delta    INTEGER NOT NULL,
              reason   TEXT,
              -- UNIQUE, and this is the whole idempotency story: Stripe retries
              -- webhooks, sometimes for days. The event id goes here, so a repeat
              -- delivery hits the constraint and grants nothing.
              ref      TEXT UNIQUE,
              created  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ledger_key ON ledger(key_hash);

            -- THE ONE PLACE A USABLE KEY IS STORED, and only briefly. The success
            -- page needs to be able to show the key, and needs to survive a
            -- refresh -- a key shown exactly once and then lost forever is a
            -- support ticket per customer. Cleared after CLAIM_TTL.
            CREATE TABLE IF NOT EXISTS claims(
              session_id TEXT PRIMARY KEY,
              key_plain  TEXT,
              key_hash   TEXT NOT NULL,
              credits    INTEGER NOT NULL,
              created    REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS free_use(
              ip  TEXT NOT NULL,
              day TEXT NOT NULL,
              n   INTEGER NOT NULL,
              PRIMARY KEY(ip, day)
            );
            """)
        _ready = True


CLAIM_TTL = 24 * 3600


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key() -> str:
    """A bearer token. 32 bytes of urandom, prefixed so it is recognisable in a
    support email and greppable if one ever leaks into a log it should not."""
    return "atk_" + secrets.token_urlsafe(32)


# ---------------------------------------------------------------- balances

def balance(key: str) -> int:
    init()
    with _conn() as c:
        row = c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE key_hash=?",
                        (_hash(key),)).fetchone()
    return int(row[0] or 0)


def key_exists(key: str) -> bool:
    init()
    with _conn() as c:
        return c.execute("SELECT 1 FROM keys WHERE key_hash=?", (_hash(key),)).fetchone() is not None


def grant(key_hash: str, credits: int, reason: str, ref: str, email=None) -> bool:
    """Add credits. Returns False if `ref` was already applied — which is the
    normal, expected outcome of a Stripe webhook retry, not an error."""
    init()
    now = time.time()
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO keys(key_hash,email,created) VALUES(?,?,?)",
                  (key_hash, email, now))
        if email:
            c.execute("UPDATE keys SET email=COALESCE(email,?) WHERE key_hash=?", (email, key_hash))
        try:
            c.execute("INSERT INTO ledger(key_hash,delta,reason,ref,created) VALUES(?,?,?,?,?)",
                      (key_hash, int(credits), reason, ref, now))
        except sqlite3.IntegrityError:
            return False
    return True


def spend(key: str, reason: str, ref: str) -> bool:
    """Take one credit, atomically.

    The check and the insert are one IMMEDIATE transaction. Read-then-write
    across two statements is the classic way to let two concurrent uploads both
    see a balance of 1 and both spend it.
    """
    init()
    kh = _hash(key)
    with _conn() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            bal = c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE key_hash=?",
                            (kh,)).fetchone()[0] or 0
            if bal < 1:
                c.execute("ROLLBACK")
                return False
            c.execute("INSERT INTO ledger(key_hash,delta,reason,ref,created) VALUES(?,?,?,?,?)",
                      (kh, -1, reason, ref, time.time()))
            c.execute("COMMIT")
            return True
        except sqlite3.IntegrityError:
            # Same ref twice: this track was already charged for. Not a failure.
            c.execute("ROLLBACK")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise


def refund(key: str, reason: str, ref: str):
    """Analysis failed after the credit was taken. Give it back — the customer
    got nothing, and a crash on our side must never cost them."""
    init()
    with _conn() as c:
        try:
            c.execute("INSERT INTO ledger(key_hash,delta,reason,ref,created) VALUES(?,?,?,?,?)",
                      (_hash(key), 1, reason, ref, time.time()))
        except sqlite3.IntegrityError:
            pass


# ---------------------------------------------------------------- free tier

def free_take(ip: str) -> bool:
    """One free analysis for this IP today, if any are left."""
    init()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _conn() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT n FROM free_use WHERE ip=? AND day=?", (ip, day)).fetchone()
            n = int(row[0]) if row else 0
            if n >= FREE_PER_DAY:
                c.execute("ROLLBACK")
                return False
            c.execute("INSERT INTO free_use(ip,day,n) VALUES(?,?,1) "
                      "ON CONFLICT(ip,day) DO UPDATE SET n=n+1", (ip, day))
            c.execute("COMMIT")
            return True
        except Exception:
            c.execute("ROLLBACK")
            raise


def free_left(ip: str) -> int:
    init()
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _conn() as c:
        row = c.execute("SELECT n FROM free_use WHERE ip=? AND day=?", (ip, day)).fetchone()
    return max(0, FREE_PER_DAY - (int(row[0]) if row else 0))


# ---------------------------------------------------------------- stripe

def stripe_ready():
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _stripe():
    """Imported lazily and by name, so the server starts, serves the site and
    analyses tracks with the library absent and no keys configured. Billing is
    the only thing that degrades."""
    import stripe
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def checkout_url(pack: str, origin: str) -> str:
    if pack not in PACKS:
        raise ValueError("unknown pack")
    s = _stripe()
    p = PACKS[pack]
    price_id = os.environ.get("ATONAL_PRICE_" + pack.upper())
    if price_id:
        line = {"price": price_id, "quantity": 1}
    else:
        # Inline price. Fine for test mode; in production define real Stripe
        # Prices so the amount is not duplicated in two places that can disagree.
        line = {"quantity": 1, "price_data": {
            "currency": CURRENCY,
            "unit_amount": p["amount"],
            "product_data": {"name": "ATONAL — " + p["label"],
                             "description": f'{p["credits"]} track analysis credit'
                                            f'{"s" if p["credits"] != 1 else ""}'},
        }}
    sess = s.checkout.Session.create(
        mode="payment",
        line_items=[line],
        # Stripe substitutes the real id. The success page hands it to /claim.
        success_url=origin + "/site/success.html?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=origin + "/site/pricing.html?cancelled=1",
        # Credits are attached to whoever paid, so we need the address on file.
        customer_creation="always",
        metadata={"pack": pack, "credits": str(p["credits"])},
    )
    return sess.url


def _grant_for_session(sess) -> dict:
    """Turn a paid Checkout Session into credits. Idempotent on the session id,
    so the webhook and the success page can both call it and only one wins.

    A REPEAT PURCHASE TOPS UP THE EXISTING KEY rather than issuing a second one.
    Handing someone a new key per purchase means juggling several, and the
    balance they can see is never the balance they have.
    """
    init()
    sid = sess["id"]
    with _conn() as c:
        row = c.execute("SELECT key_plain, credits FROM claims WHERE session_id=?", (sid,)).fetchone()
    if row:
        return {"key": row[0], "credits": int(row[1]), "fresh": False}

    credits = int((sess.get("metadata") or {}).get("credits") or 0)
    if credits <= 0:
        pack = (sess.get("metadata") or {}).get("pack")
        credits = PACKS.get(pack, {}).get("credits", 0)
    if credits <= 0:
        raise ValueError("session carries no credit count")

    details = sess.get("customer_details") or {}
    email = details.get("email")

    key_plain, key_hash = None, None
    if email:
        with _conn() as c:
            prior = c.execute("SELECT key_hash FROM keys WHERE email=? ORDER BY created LIMIT 1",
                              (email,)).fetchone()
        if prior:
            key_hash = prior[0]
            # Top-up: the key itself is hashed and unrecoverable, so the plaintext
            # can only be re-shown if a claim row from a purchase inside the TTL
            # still holds it.
            with _conn() as c:
                r2 = c.execute("SELECT key_plain FROM claims WHERE key_hash=? AND key_plain IS NOT NULL"
                               " ORDER BY created DESC LIMIT 1", (key_hash,)).fetchone()
            key_plain = r2[0] if r2 else None
    if key_hash is None:
        key_plain = new_key()
        key_hash = _hash(key_plain)

    grant(key_hash, credits, "purchase:" + sid, "stripe:" + sid, email=email)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO claims(session_id,key_plain,key_hash,credits,created)"
                  " VALUES(?,?,?,?,?)", (sid, key_plain, key_hash, credits, time.time()))
    return {"key": key_plain, "credits": credits, "fresh": True}


def claim(session_id: str) -> dict:
    """Called by the success page.

    It retrieves the session from Stripe rather than trusting the query string,
    because success_url is just a redirect the browser can be pointed at with any
    session id in it. Payment status comes from Stripe or not at all.

    It also GRANTS if the webhook has not arrived yet. Webhooks are asynchronous
    and occasionally slow; the customer is already looking at the success page.
    Both paths are idempotent on the session id, so whichever runs first wins and
    the other becomes a no-op.
    """
    init()
    expire_claims()
    s = _stripe()
    sess = s.checkout.Session.retrieve(session_id, expand=["customer_details"])
    if sess.get("payment_status") != "paid":
        return {"error": "not paid"}
    out = _grant_for_session(sess)
    if not out.get("key"):
        return {"error": "key no longer retrievable", "credits": out.get("credits", 0)}
    return out


def webhook(payload: bytes, sig_header: str) -> dict:
    """Signature verification is not optional: without it this endpoint is an
    unauthenticated 'give me credits' API, and the URL is public."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set")
    s = _stripe()
    event = s.Webhook.construct_event(payload, sig_header, secret)
    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        if sess.get("payment_status") == "paid":
            _grant_for_session(sess)
    return {"ok": True, "type": event["type"]}


def expire_claims():
    """Drop plaintext keys once the claim window has passed. After this the
    database holds nothing that can be used to spend credits."""
    with _conn() as c:
        c.execute("UPDATE claims SET key_plain=NULL WHERE key_plain IS NOT NULL AND created < ?",
                  (time.time() - CLAIM_TTL,))
