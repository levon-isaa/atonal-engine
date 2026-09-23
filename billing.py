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
# `priority` is the pricing page's "Priority queue" line, and it is here rather than only in the
# markup so the claim and the behaviour cannot drift apart -- server.py reads this same flag to
# order the analysis queue. It was markup only for a while, which meant the Pack of 50 advertised
# a feature that did not exist anywhere in the code.
PACKS = {
    "single": {"credits": 1,  "amount": 600,   "label": "Single track"},
    "ten":    {"credits": 10, "amount": 4500,  "label": "Pack of 10"},
    "fifty":  {"credits": 50, "amount": 17500, "label": "Pack of 50", "priority": True},
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
            # reissued_from: the hash this key REPLACED, written by reissue(). It is the only
            # link between a retired key and its successor, and three things read it -- the
            # priority walk, the retired() check, and an operator asking what happened. Added
            # by ALTER rather than in the CREATE above so a database from before this exists
            # keeps its rows; SQLite has no IF NOT EXISTS for a column, so the duplicate is
            # caught and ignored.
            try:
                c.execute("ALTER TABLE keys ADD COLUMN reissued_from TEXT")
            except sqlite3.OperationalError:
                pass        # already there
            c.execute("CREATE INDEX IF NOT EXISTS keys_reissued ON keys(reissued_from)")
            # Fold any address stored before _norm_email existed into the same form, so the
            # lookup in _grant_for_session keeps using the index and an older key is still
            # found. Touches only rows that actually differ, and runs once per process.
            c.execute("UPDATE keys SET email=LOWER(TRIM(email)) "
                      "WHERE email IS NOT NULL AND email <> LOWER(TRIM(email))")
        _ready = True


CLAIM_TTL = 24 * 3600


def _norm_email(e):
    """The form an address is stored and matched in: trimmed and lower-cased.

    A REPEAT PURCHASE IS MATCHED BY EMAIL, so whatever the customer typed has to reduce to
    one identity or _grant_for_session's whole promise fails. It was an exact string
    compare, and Paddle hands back the address as entered -- so "Levon.Isaa@Example.com"
    from a desktop and "levon.isaa@example.com" from a phone are two people to it.
    REPRODUCED: three purchases differing only in case and a trailing space issued THREE
    keys with 10 credits each. The customer had paid for 30 and the largest balance they
    could see was 10, which is exactly the juggling the docstring below says not to do.

    Lower-casing the local part is technically not RFC-safe -- "A@x.com" and "a@x.com" MAY
    be different mailboxes -- but no mail provider in practice treats them so, and the
    alternative here is not correctness, it is splitting a paying customer's balance across
    keys they did not know they had.
    """
    e = (e or "").strip().lower()
    return e or None


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
        email = _norm_email(email)
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


def has_priority(key: str) -> bool:
    """True when this key has ever bought a pack carrying `priority`.

    Ever, not currently: someone who bought a Pack of 50, spent it and topped up with a
    single does not lose the thing they paid for. The queue in server.py reads this.
    """
    init()
    want = tuple("purchase:%s:" % p for p, v in PACKS.items() if v.get("priority"))
    if not want:
        return False
    # ACROSS A REISSUE TOO. A replacement key holds the balance but not the history -- its only
    # credit row says the balance was moved, not what it was bought with -- so a customer who
    # lost the key to a Pack of 50 would have silently lost the queue position they paid for.
    # Walked rather than copied, because writing "purchase:fifty:" onto the new key would put a
    # purchase in the ledger that never happened. Bounded by the visited set; the cap is belt
    # and braces for a cycle that the append-only chain cannot produce.
    kh, seen = _hash(key), set()
    with _conn() as c:
        while kh and kh not in seen and len(seen) < 16:
            seen.add(kh)
            rows = c.execute("SELECT reason FROM ledger WHERE key_hash=? AND delta>0",
                             (kh,)).fetchall()
            if any((r[0] or "").startswith(want) for r in rows):
                return True
            row = c.execute("SELECT reissued_from FROM keys WHERE key_hash=?", (kh,)).fetchone()
            kh = row[0] if row else None
    return False


# ---------------------------------------------------------------- reissue
#
# WHY THIS IS A LOCAL TOOL AND NOT AN ENDPOINT. Three places on the site promise a customer who
# has lost their key that we will issue a replacement against their purchase, and until this
# existed nothing could: keys are stored hashed and the plaintext is wiped after CLAIM_TTL, by
# design, so the answer to "send me my key again" is necessarily a NEW key. That is a transfer
# of a balance from one bearer token to another on nothing but an email, which is exactly the
# shape of an account-takeover -- so the decision that the person asking is the person who paid
# is a HUMAN one, made against the provider's own receipt, and there is deliberately no route
# to it over the network. tools_reissue.py is the interface; this is the transaction.
#
# WHAT MOVES. The balance, the email, and the priority. Moving only the balance was the version
# that looked right and was not: the email lookup in _grant_purchase takes the OLDEST key on an
# address, so the customer's NEXT purchase would have topped up the key they had just been told
# to stop using, and has_priority reads the reasons on one hash, so a replaced Pack of 50 would
# have quietly lost its queue position. See the notes on each.


def retired(key: str) -> bool:
    """True when this key has been replaced. Distinct from "no credits left", which is what a
    retired key would otherwise look like once its balance has moved."""
    init()
    with _conn() as c:
        return c.execute("SELECT 1 FROM keys WHERE reissued_from=?",
                         (_hash(key),)).fetchone() is not None


def key_records(email: str = None, key: str = None, key_hash: str = None) -> list:
    """The rows an operator can identify a customer by, oldest first.

    `email` is what a support request actually arrives with; `key` is for the case where they
    still have the key and want it rotated; `key_hash` (a prefix is enough) is for picking one
    out of a list this function just printed. Never returns anything secret -- the plaintext is
    not in the database to return.
    """
    init()
    if key:
        key_hash = _hash(key)
    where, args = [], []
    if email:
        where.append("k.email = ?"); args.append(_norm_email(email))
    if key_hash:
        where.append("k.key_hash LIKE ?"); args.append(key_hash.strip().lower() + "%")
    if not where:
        return []
    with _conn() as c:
        rows = c.execute(
            "SELECT k.key_hash, k.email, k.created, k.reissued_from,"
            "       COALESCE((SELECT SUM(delta) FROM ledger WHERE key_hash=k.key_hash),0),"
            # The two halves of a reissue are excluded from the COUNTS but kept in `reasons`:
            # they are a move and not a purchase or an analysis, and a retired key reading
            # "4 spends" against three analyses is a number an operator would have to explain.
            "       COALESCE((SELECT COUNT(*) FROM ledger WHERE key_hash=k.key_hash AND delta>0"
            "                 AND COALESCE(reason,'') NOT LIKE 'reissue%'),0),"
            "       COALESCE((SELECT COUNT(*) FROM ledger WHERE key_hash=k.key_hash AND delta<0"
            "                 AND COALESCE(reason,'') NOT LIKE 'reissue%'),0),"
            "       (SELECT MAX(created) FROM ledger WHERE key_hash=k.key_hash),"
            "       (SELECT key_hash FROM keys WHERE reissued_from=k.key_hash)"
            " FROM keys k WHERE " + " AND ".join(where) + " ORDER BY k.created", args).fetchall()
        out = []
        for r in rows:
            out.append({"key_hash": r[0], "email": r[1], "created": r[2], "reissued_from": r[3],
                        "balance": int(r[4] or 0), "grants": int(r[5]), "spends": int(r[6]),
                        "last": r[7], "reissued_to": r[8],
                        "reasons": [x[0] for x in c.execute(
                            "SELECT reason FROM ledger WHERE key_hash=? AND delta>0"
                            " ORDER BY created", (r[0],)).fetchall()]})
    return out


def reissue(key_hash: str, note: str = None) -> dict:
    """Retire a key and mint its replacement. Returns the new key IN PLAINTEXT, once.

    Nothing stores what comes back. The caller prints it, the operator sends it, and from then
    on the database holds a hash like every other key -- which is the same promise the success
    page makes, kept on the support path too.

    ONE TRANSACTION, because a balance that has left one key and not arrived at the other is
    money destroyed. The balance is read inside it for the same reason spend() reads inside its
    own: between a read and a write, an analysis can land.
    """
    init()
    key_hash = (key_hash or "").strip().lower()
    if not key_hash:
        raise ValueError("no key to reissue")
    new_plain = new_key()
    new_hash = _hash(new_plain)
    now = time.time()
    with _conn() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            # BOTH CHECKS INSIDE THE TRANSACTION, not before it. They are the same
            # read-then-write that spend() takes an IMMEDIATE lock for: two operators working
            # the same support thread would otherwise both find one key and mint two
            # replacements, and the customer would be sent the one whose ledger rows lost.
            row = c.execute("SELECT email FROM keys WHERE key_hash=?", (key_hash,)).fetchone()
            if not row:
                raise ValueError("no such key")
            if c.execute("SELECT 1 FROM keys WHERE reissued_from=?", (key_hash,)).fetchone():
                # Reissuing a key that was already replaced would move a balance of zero onto a
                # third key and leave the customer holding the second one. Say so instead.
                raise ValueError("that key was already replaced -- reissue its replacement instead")
            email = row[0]
            bal = int((c.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE key_hash=?",
                                 (key_hash,)).fetchone() or [0])[0] or 0)
            c.execute("INSERT INTO keys(key_hash,email,created,reissued_from) VALUES(?,?,?,?)",
                      (new_hash, email, now, key_hash))
            # THE EMAIL MOVES WITH THE BALANCE. _grant_purchase resolves a repeat purchase by
            # `SELECT key_hash FROM keys WHERE email=? ORDER BY created LIMIT 1` -- the OLDEST
            # -- so leaving the address on the retired row would send the customer's next pack
            # to the key we have just told them to stop using. Cleared rather than deleted: the
            # row is what retired() and the priority walk hang off.
            c.execute("UPDATE keys SET email=NULL WHERE key_hash=?", (key_hash,))
            if bal > 0:
                # Two rows, never one. The ledger is append-only and its balance is SUM(delta),
                # so a move is a pair and the refs are derived from the NEW hash, which is 256
                # bits of urandom and therefore unique without a counter.
                c.execute("INSERT INTO ledger(key_hash,delta,reason,ref,created)"
                          " VALUES(?,?,?,?,?)",
                          (key_hash, -bal, "reissue: retired, balance moved to %s" % new_hash[:8],
                           "ri-out:" + new_hash[:32], now))
                c.execute("INSERT INTO ledger(key_hash,delta,reason,ref,created)"
                          " VALUES(?,?,?,?,?)",
                          (new_hash, bal, "reissue: balance moved from %s%s"
                           % (key_hash[:8], (" -- " + note) if note else ""),
                           "ri-in:" + new_hash[:32], now))
            # A claim row inside its 24 hours still holds the RETIRED key in plaintext. It can
            # no longer spend anything, but it is a bearer token for a key we have just revoked
            # and there is no reason to keep it.
            c.execute("UPDATE claims SET key_plain=NULL WHERE key_hash=?", (key_hash,))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    return {"key": new_plain, "key_hash": new_hash, "from": key_hash,
            "credits": bal, "email": email}


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

def utc_day() -> str:
    """The day free_use is bucketed by. Exposed so a caller can record which day it
    took from and hand the same one back to free_refund — a take at 23:59:59 and the
    failure that follows it must not credit tomorrow."""
    return time.strftime("%Y-%m-%d", time.gmtime())


def free_take(ip: str) -> bool:
    """One free analysis for this IP today, if any are left."""
    init()
    day = utc_day()
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


def free_refund(ip: str, day: str) -> None:
    """Give a free analysis back, for the same reason `refund` gives a credit back.

    THE PROMISE WAS ONLY BEING KEPT FOR PEOPLE WHO PAID. refund() above says a crash
    on our side must never cost the customer, and the analyse path called it — but only
    on the branch where a KEY was charged. A free upload that failed to decode took the
    day's allowance and never gave it back, so the person most likely to be evaluating
    the product lost their try to a file we could not read, and could not try another
    until tomorrow. Verified against the running server before this existed: reset the
    counter to 0, POST an undecodable body, get 500 "could not decode audio", and the
    counter reads 1.

    Floored at zero and scoped to the day the take was recorded against, so a double
    call cannot mint allowance and a refund that crosses UTC midnight cannot credit a
    day that was never charged.
    """
    init()
    with _conn() as c:
        c.execute("UPDATE free_use SET n=MAX(0,n-1) WHERE ip=? AND day=?", (ip, day))


def free_left(ip: str) -> int:
    init()
    day = utc_day()
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


def _grant_purchase(sid, credits: int, pack, email, ref: str) -> dict:
    """The half of a purchase that has nothing to do with who took the money.

    Paddle hands us a transaction, Gumroad hands us a verified licence, and from here they are
    the same thing: an id that must grant exactly once, a credit count, and an address that ties
    a repeat purchase to the key its owner already has. Both providers route through this so the
    ledger, the claims row and the ownership race are settled in ONE place -- the race in
    particular took 38 of 300 concurrent trials before it was fixed, and having two copies of
    that logic is how it comes back on the path nobody re-measured.
    """
    init()
    if not sid:
        raise ValueError("purchase has no id")
    with _conn() as c:
        row = c.execute("SELECT key_plain, credits, key_hash FROM claims WHERE session_id=?",
                        (sid,)).fetchone()
    if row:
        # key_hash comes back with it so claim() can confirm a key the CLIENT already holds
        # when key_plain has been wiped; it is popped before anything is sent.
        return {"key": row[0], "credits": int(row[1]), "fresh": False, "key_hash": row[2]}
    if int(credits) <= 0:
        raise ValueError("purchase carries no credit count")
    email = _norm_email(email)
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

    # WHICH KEY THE MONEY LANDS ON IS DECIDED BY THE CLAIMS ROW, and it has to be decided
    # before the grant rather than recorded after it. Two callers -- a webhook and a success
    # page, or two browser tabs -- both read no claims row, both mint a fresh key, one wins the
    # ledger and the other wins the write, so the customer is shown a key with a balance of zero
    # while the credits sit on a hash whose plaintext was a local variable in the thread that
    # lost. REPRODUCED at 38 of 300 two-thread trials before this; 0 of 300 after. OR IGNORE plus
    # a read-back inside one IMMEDIATE transaction makes the first writer the owner.
    with _conn() as c:
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR IGNORE INTO claims(session_id,key_plain,key_hash,credits,created)"
                      " VALUES(?,?,?,?,?)", (sid, key_plain, key_hash, int(credits), time.time()))
            row = c.execute("SELECT key_plain, credits, key_hash FROM claims WHERE session_id=?",
                            (sid,)).fetchone()
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    key_plain, credits, key_hash = row[0], int(row[1]), row[2]
    # The PACK goes in the reason, because "what did this key buy" is a ledger fact and the
    # ledger is append-only -- so priority cannot be granted or lost by an UPDATE somewhere.
    grant(key_hash, credits, "purchase:%s:%s" % (pack or "?", sid), ref, email=email)
    return {"key": key_plain, "credits": credits, "fresh": True, "key_hash": key_hash}


def _grant_for_session(txn) -> dict:
    """A completed Paddle transaction, turned into credits. Idempotent on the transaction id,
    so the webhook and the success page can both call it and only one wins."""
    init()
    sid = txn.get("id")
    if not sid:
        raise ValueError("transaction has no id")
    with _conn() as c:
        row = c.execute("SELECT key_plain, credits, key_hash FROM claims WHERE session_id=?",
                        (sid,)).fetchone()
    if row:
        return {"key": row[0], "credits": int(row[1]), "fresh": False, "key_hash": row[2]}
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
    return _grant_purchase(sid, credits, custom.get("pack"), email, "paddle:" + sid)


def _finish(out: dict, have_key: str = None) -> dict:
    """The tail both redemption paths share: attach the balance, never leak the key_hash, and
    turn "we have no plaintext to show you" into a top-up rather than an error.

    A REPEAT PURCHASE USED TO END ON THE ERROR PAGE. The plaintext key is wiped after CLAIM_TTL,
    deliberately, so the database holds nothing that can spend credits. A returning customer's
    second pack therefore tops up their EXISTING key_hash -- correctly, the credits land -- and
    then had no plaintext to show. TWO ANSWERS, IN ORDER. If the browser still holds the key it
    can send it and we CONFIRM rather than reveal: hash what arrived, compare, hand the same
    string back. The server learns nothing it did not already have. Failing that, the purchase
    succeeded and the balance is known, so say so and let the page render a top-up.
    """
    kh = out.pop("key_hash", None)          # never sent to the client; only compared here
    if kh:
        with _conn() as c:
            out["balance"] = int((c.execute(
                "SELECT COALESCE(SUM(delta),0) FROM ledger WHERE key_hash=?", (kh,)
            ).fetchone() or [0])[0] or 0)
    if not out.get("key"):
        if have_key and kh and secrets.compare_digest(_hash(have_key), kh):
            out["key"] = have_key
            out["restored"] = True          # confirmed from the client's own copy, not from ours
        else:
            out["topped_up"] = True         # paid, credited, key issued on an earlier purchase
    return out


def claim(session_id: str, have_key: str = None) -> dict:
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
        # PENDING IS NOT REFUSED, IT IS UNFINISHED, and the two need telling apart by something
        # better than the wording. Paddle returns the browser to the success page the moment the
        # checkout is done with it and settles the transaction on its own schedule -- instantly
        # for a card that authorises, seconds later through 3-D Secure, longer still for a bank
        # transfer. So this state is what a REAL payment looks like for a moment, and the page
        # that receives it has to keep asking rather than tell someone who has just paid that
        # something is wrong. The flag is what it keys on; the string stays for a human reading
        # a log. Everything else /claim can answer with is final and is retried by nobody.
        return {"error": "not paid", "pending": True}
    return _finish(_grant_for_session(txn), have_key)


# ---------------------------------------------------------------- gumroad
# A SECOND CHANNEL, NOT A REPLACEMENT. Paddle stays the in-app checkout: its cut is smaller and
# it signs its webhooks, which is what makes an unauthenticated public endpoint safe. Gumroad
# earns its place somewhere Paddle cannot go -- a link that works in a post, a video description
# or a DM, with nothing to integrate at the other end. Both are merchants of record, so the VAT
# handling and the payout geography that ruled Stripe out are answered either way.
#
# REDEMPTION, NOT A WEBHOOK, AND THAT IS THE WHOLE SECURITY ARGUMENT. Gumroad's ping is a plain
# form POST with no signature over the body, so an endpoint that granted on one would be the
# unauthenticated "give me credits" API that webhook() above exists to refuse. Gumroad's licence
# verification API is the strong surface: the customer pastes the licence key they were emailed,
# and the server asks GUMROAD whether it is real before anything is granted. Same shape as
# claim() -- the client's word is a hint, the provider's answer is the fact.
GUMROAD_API = "https://api.gumroad.com/v2"


def gumroad_products() -> dict:
    """pack -> Gumroad product id, from ATONAL_GUMROAD_<PACK>. A pack with no id configured is
    simply not on sale through Gumroad, which is a legitimate state: the single might live only
    in the app and the big pack only on the storefront."""
    out = {}
    for p in PACKS:
        v = (os.environ.get("ATONAL_GUMROAD_" + p.upper()) or "").strip()
        if v:
            out[p] = v
    return out


def gumroad_link(pack: str) -> str:
    """The buy page for a pack, if one is set. Gumroad product URLs are static and public, so
    unlike Paddle there is no transaction to create first and no server round trip to buy."""
    return (os.environ.get("ATONAL_GUMROAD_LINK_" + pack.upper()) or "").strip()


def gumroad_ready() -> bool:
    return bool(gumroad_products())


def _gumroad_verify(product_id: str, license_key: str, timeout=20):
    """Ask Gumroad whether this licence is real for this product. None when it is not.

    increment_uses_count is FALSE on purpose. Gumroad's counter is a licence-activation count
    and this is not an activation -- the ledger's UNIQUE ref is what makes a sale grant once, so
    burning a use on every verification would make a customer's own retry look like a second
    install. Not raising on a 404 either: a licence for a DIFFERENT pack answers 404 here, and
    the caller tries each configured product in turn.
    """
    import requests
    r = requests.post(GUMROAD_API + "/licenses/verify",
                      data={"product_id": product_id, "license_key": license_key,
                            "increment_uses_count": "false"}, timeout=timeout)
    if r.status_code == 404:
        return None
    if r.status_code // 100 != 2:
        raise RuntimeError("gumroad verify -> %s %s" % (r.status_code, r.text[:300]))
    d = r.json() or {}
    return d if d.get("success") else None


def redeem(license_key: str, have_key: str = None) -> dict:
    """Turn a Gumroad licence key into credits on an ATONAL key.

    The licence is NOT the render key and is not stored as one. It is exchanged once for an
    atk_ key and the ledger stays the source of truth, so an analysis never depends on Gumroad
    being reachable -- and a customer who buys through both channels ends up with one key and
    one balance rather than two of each.
    """
    init()
    expire_claims()
    lic = (license_key or "").strip()
    if not lic:
        return {"error": "no licence key"}
    prods = gumroad_products()
    if not prods:
        return {"error": "Gumroad is not configured"}
    found = None
    for pack, pid in prods.items():
        d = _gumroad_verify(pid, lic)
        if d:
            found = (pack, d)
            break
    if not found:
        return {"error": "that licence was not recognised"}
    pack, d = found
    pur = d.get("purchase") or {}
    # A REFUNDED SALE IS NOT A SALE. Gumroad keeps the licence valid after a refund or a
    # chargeback and reports it on the purchase, so this has to be read or the money can be
    # taken back while the credits stay.
    if pur.get("refunded") or pur.get("chargebacked") or pur.get("disputed"):
        return {"error": "that purchase was refunded"}
    sid = pur.get("sale_id") or pur.get("id")
    if not sid:
        return {"error": "that licence carries no sale id"}
    try:
        qty = max(1, int(pur.get("quantity") or 1))
    except (TypeError, ValueError):
        qty = 1
    credits = PACKS[pack]["credits"] * qty
    out = _grant_purchase(sid, credits, pack, pur.get("email"), "gumroad:" + str(sid))
    return _finish(out, have_key)


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
