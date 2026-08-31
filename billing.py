"""
ATONAL — credits, keys and Paddle.

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

NO ACCOUNTS. Paddle checkout already collects an email and already proves
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

# The packs. `amount` is in cents and is for DISPLAY ONLY -- the pricing page
# reads it so the numbers can be rendered without a round trip. Paddle will not
# accept an inline amount, so the charged price always comes from the Paddle
# price id in ATONAL_PRICE_<PACK>. If the two ever disagree, Paddle is right and
# this is a stale label; see checkout_url.
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
              -- UNIQUE, and this is the whole idempotency story: the payment
              -- provider retries webhooks, sometimes for days. The transaction id
              -- goes here, so a repeat delivery hits the constraint and grants
              -- nothing.
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
    normal, expected outcome of a provider webhook retry, not an error."""
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


# ---------------------------------------------------------------- paddle
#
# PADDLE, NOT STRIPE, AND THE REASON IS GEOGRAPHY. Stripe does not open merchant
# accounts in Armenia, so the previous implementation could never have taken a
# payment however correct it was. Paddle is a MERCHANT OF RECORD: it sells to the
# customer and we sell to Paddle, which means no local merchant account is needed
# and, just as importantly, Paddle files the EU VAT. Prices here are in EUR and
# most buyers will be in the EU, so that second point is not a detail -- it is a
# liability we would otherwise own.
#
# It costs more than raw card processing. That is what the VAT handling and the
# country coverage are being bought with.
#
# NO SDK. Paddle's REST API is plain JSON over HTTPS and its webhook signature is
# an HMAC we can compute with hashlib, so this needs `requests` (already a
# dependency for the tagger) and nothing else. The stripe package is gone from
# requirements.txt with this change.

PADDLE_ENV = os.environ.get("PADDLE_ENV", "sandbox").strip().lower()
PADDLE_API = ("https://api.paddle.com" if PADDLE_ENV == "production"
              else "https://sandbox-api.paddle.com")
# How long a webhook timestamp may lag before it is refused, in seconds. A replay
# of a genuine, correctly signed request is still a replay.
PADDLE_MAX_SKEW = 300


def billing_ready():
    """True when a payment can actually be taken. Everything else on the server
    runs without it: the site serves, tracks analyse, and the free tier works."""
    return bool(os.environ.get("PADDLE_API_KEY"))


# Kept so an older page or bookmark does not break on the rename.
def stripe_ready():
    return billing_ready()


def _paddle(method: str, path: str, body=None, timeout=20):
    """One place that talks to Paddle, so the auth header and the error shape are
    not repeated five times. Raises on anything that is not 2xx -- a silent
    failure here would look like a customer who paid and got nothing."""
    import requests
    key = os.environ.get("PADDLE_API_KEY")
    if not key:
        raise RuntimeError("PADDLE_API_KEY is not set")
    r = requests.request(
        method, PADDLE_API + path,
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 # Pinning the API version stops a Paddle-side change from
                 # altering the response shape under a running server.
                 "Paddle-Version": "1"},
        json=body, timeout=timeout)
    if r.status_code // 100 != 2:
        raise RuntimeError("paddle %s %s -> %s %s" % (method, path, r.status_code, r.text[:300]))
    return r.json().get("data") or {}


def checkout_url(pack: str, origin: str) -> str:
    """Create a transaction and hand back its hosted checkout URL.

    PADDLE NEEDS A PRICE ID AND WILL NOT TAKE AN INLINE AMOUNT. Stripe let us
    send price_data with a number in it, which is why PACKS carried amounts at
    all. Paddle prices live in Paddle, attached to a product, so each pack needs
    ATONAL_PRICE_<PACK> set to a `pri_...` id. That is the better arrangement --
    an amount defined in two places is an amount that will eventually disagree
    with itself -- but it does mean this raises rather than inventing a price.
    """
    if pack not in PACKS:
        raise ValueError("unknown pack")
    price_id = (os.environ.get("ATONAL_PRICE_" + pack.upper()) or "").strip()
    if not price_id:
        raise RuntimeError(
            "ATONAL_PRICE_%s is not set. Paddle prices are created in Paddle and "
            "referenced by id; there is no inline amount to fall back to." % pack.upper())
    p = PACKS[pack]
    txn = _paddle("POST", "/transactions", {
        "items": [{"price_id": price_id, "quantity": 1}],
        # Read back in _grant_for_session. Paddle returns custom_data verbatim on
        # the transaction and on every webhook about it.
        "custom_data": {"pack": pack, "credits": str(p["credits"])},
        # Paddle appends ?_ptxn=<transaction id> to this, which the success page
        # reads and passes to /claim.
        "checkout": {"url": origin + "/site/success.html"},
    })
    url = ((txn.get("checkout") or {}).get("url") or "").strip()
    if not url:
        # Happens when the Paddle account has no default payment link configured,
        # and the message says so because the API error alone does not.
        raise RuntimeError(
            "Paddle returned no checkout URL. Set a default payment link under "
            "Checkout > Settings in the Paddle dashboard.")
    return url


def _customer_email(customer_id):
    """Paddle puts the customer id on the transaction but not always the address.
    Email is what ties a repeat purchase to an existing key, so it is worth the
    extra call -- and worth not failing the grant if that call does not work."""
    if not customer_id:
        return None
    try:
        return (_paddle("GET", "/customers/" + str(customer_id)) or {}).get("email")
    except Exception:
        return None


def _grant_for_session(txn) -> dict:
    """Turn a completed Paddle transaction into credits. Idempotent on the
    transaction id, so the webhook and the success page can both call it and only
    one wins.

    A REPEAT PURCHASE TOPS UP THE EXISTING KEY rather than issuing a second one.
    Handing someone a new key per purchase means juggling several, and the
    balance they can see is never the balance they have.
    """
    init()
    sid = txn.get("id")
    if not sid:
        raise ValueError("transaction has no id")
    with _conn() as c:
        row = c.execute("SELECT key_plain, credits FROM claims WHERE session_id=?", (sid,)).fetchone()
    if row:
        return {"key": row[0], "credits": int(row[1]), "fresh": False}

    custom = txn.get("custom_data") or {}
    try:
        credits = int(custom.get("credits") or 0)
    except (TypeError, ValueError):
        credits = 0
    if credits <= 0:
        credits = PACKS.get(custom.get("pack"), {}).get("credits", 0)
    if credits <= 0:
        raise ValueError("transaction carries no credit count")

    email = ((txn.get("customer") or {}).get("email")
             or _customer_email(txn.get("customer_id")))

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

    grant(key_hash, credits, "purchase:" + sid, "paddle:" + sid, email=email)
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO claims(session_id,key_plain,key_hash,credits,created)"
                  " VALUES(?,?,?,?,?)", (sid, key_plain, key_hash, credits, time.time()))
    return {"key": key_plain, "credits": credits, "fresh": True}


def claim(session_id: str) -> dict:
    """Called by the success page with the _ptxn Paddle put in the return URL.

    It re-reads the transaction FROM PADDLE rather than trusting the query
    string, because the return URL is just a redirect the browser can be pointed
    at with any id in it. Paid status comes from Paddle or not at all.

    It also GRANTS if the webhook has not arrived yet. Webhooks are asynchronous
    and occasionally slow; the customer is already looking at the success page.
    Both paths are idempotent on the transaction id, so whichever runs first wins
    and the other becomes a no-op.
    """
    init()
    expire_claims()
    txn = _paddle("GET", "/transactions/" + str(session_id))
    # `completed` is the terminal paid state. `paid` can appear first on some
    # payment methods, and both mean the money is ours.
    if txn.get("status") not in ("completed", "paid"):
        return {"error": "not paid"}
    out = _grant_for_session(txn)
    if not out.get("key"):
        return {"error": "key no longer retrievable", "credits": out.get("credits", 0)}
    return out


def webhook(payload: bytes, sig_header: str) -> dict:
    """Signature verification is not optional: without it this endpoint is an
    unauthenticated 'give me credits' API, and the URL is public.

    Paddle signs as `ts=<unix>;h1=<hex>` where the HMAC-SHA256 covers the exact
    bytes `<ts>:<raw body>`. The raw body matters -- re-serialising the JSON
    changes the bytes and the signature will not match.
    """
    secret = os.environ.get("PADDLE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("PADDLE_WEBHOOK_SECRET is not set")
    parts = dict(kv.split("=", 1) for kv in (sig_header or "").split(";") if "=" in kv)
    ts, h1 = parts.get("ts"), parts.get("h1")
    if not ts or not h1:
        raise ValueError("malformed Paddle-Signature")
    # A correctly signed request replayed a day later is still a replay. The
    # parse and the window are separate checks on purpose: folded together, the
    # except swallowed the window's own ValueError and reported every stale
    # replay as a malformed timestamp, which is a different fault entirely.
    try:
        ts_i = int(ts)
    except (TypeError, ValueError) as e:
        raise ValueError("bad signature timestamp") from e
    if abs(time.time() - ts_i) > PADDLE_MAX_SKEW:
        raise ValueError("signature timestamp outside the accepted window")
    import hmac
    mac = hmac.new(secret.encode(), (ts + ":").encode() + payload, hashlib.sha256).hexdigest()
    # compare_digest, not ==, so the comparison does not leak the digest by timing
    if not hmac.compare_digest(mac, h1):
        raise ValueError("signature mismatch")

    import json as _json
    event = _json.loads(payload.decode("utf-8"))
    etype = event.get("event_type", "")
    if etype in ("transaction.completed", "transaction.paid"):
        txn = event.get("data") or {}
        if txn.get("status") in ("completed", "paid"):
            _grant_for_session(txn)
    return {"ok": True, "type": etype}


def expire_claims():
    """Drop plaintext keys once the claim window has passed. After this the
    database holds nothing that can be used to spend credits."""
    with _conn() as c:
        c.execute("UPDATE claims SET key_plain=NULL WHERE key_plain IS NOT NULL AND created < ?",
                  (time.time() - CLAIM_TTL,))
