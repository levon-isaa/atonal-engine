#!/usr/bin/env python3
"""ATONAL — issue a replacement render key.

    python tools_reissue.py find  <email | key-hash prefix | atk_key>
    python tools_reissue.py issue <key-hash prefix> [--note "..."] [--yes]

WHY THIS EXISTS. Three places on the site tell a customer who has lost their key that we will
issue a replacement against their purchase: the success page, the top-up panel and the pricing
page's FAQ. Until this existed nothing could. Keys are stored hashed and the plaintext is wiped
after 24 hours, deliberately, so there is no copy to resend -- the only honest answer to "send
me my key again" is a NEW key carrying the old one's balance.

WHY IT IS A COMMAND AND NOT AN ENDPOINT. Moving a balance from one bearer token to another on
the strength of an email address is the shape of an account takeover. The decision that the
person asking is the person who paid cannot be made by this program; it is made by a human
against the provider's own receipt. So there is no route to it over the network, and this has
to be run on the machine that holds the ledger.

BEFORE YOU RUN `issue`, ESTABLISH WHO IS ASKING:
  * the request should come from, or quote, the receipt Paddle or Gumroad sent;
  * that receipt's address must be the one `find` shows against the key;
  * a request from a different address is a request to move someone else's credits.
`find` prints the address for exactly this comparison. It prints no secret: after the claim
window the database holds nothing that can spend anything, which is the point of it.

WHAT `issue` DOES. In one transaction: mints a new key, moves the balance with a matching pair
of ledger rows (the ledger is append-only -- nothing is ever edited), moves the email so the
customer's NEXT purchase tops up the new key rather than the retired one, carries priority
across, and clears any plaintext a claim row still holds for the key being retired. The old key
keeps working for nothing: its balance is zero and the server tells its holder it was replaced.

The new key is printed ONCE and stored nowhere. Send it, then close the terminal.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import billing  # noqa: E402


def _age(t):
    if not t:
        return "never"
    d = time.time() - float(t)
    if d < 3600:
        return "%dm ago" % (d // 60)
    if d < 86400:
        return "%dh ago" % (d // 3600)
    return "%dd ago" % (d // 86400)


def _show(r):
    """One record, in the form an operator has to make a decision from."""
    tags = []
    if r["reissued_to"]:
        tags.append("REPLACED BY %s" % r["reissued_to"][:12])
    if r["reissued_from"]:
        tags.append("replaced %s" % r["reissued_from"][:12])
    prio = any((x or "").startswith(tuple("purchase:%s:" % p for p, v in billing.PACKS.items()
                                          if v.get("priority"))) for x in r["reasons"])
    if prio:
        tags.append("priority")
    print("  %s   %s" % (r["key_hash"][:16], r["email"] or "(no address)"))
    print("      balance %-5d  %d grant%s, %d spend%s   issued %s, last used %s%s"
          % (r["balance"], r["grants"], "" if r["grants"] == 1 else "s",
             r["spends"], "" if r["spends"] == 1 else "s",
             _age(r["created"]), _age(r["last"]),
             ("   [" + ", ".join(tags) + "]") if tags else ""))
    for x in r["reasons"]:
        print("      + %s" % x)


def cmd_find(args):
    q = args.query.strip()
    recs = billing.key_records(key=q) if q.startswith("atk_") else (
        billing.key_records(email=q) if "@" in q else billing.key_records(key_hash=q))
    if not recs:
        print("nothing matches %r in %s" % (q, billing.DB_PATH))
        return 1
    print("%d key%s in %s\n" % (len(recs), "" if len(recs) == 1 else "s", billing.DB_PATH))
    for r in recs:
        _show(r)
        print()
    return 0


def cmd_issue(args):
    recs = billing.key_records(key_hash=args.key_hash)
    if not recs:
        print("no key starts with %r -- run `find` first" % args.key_hash)
        return 1
    if len(recs) > 1:
        # A prefix is for typing convenience, not for guessing. Two matches means the operator
        # has to say which, because the wrong one moves a different customer's credits.
        print("%d keys start with %r. Give more of the hash:\n" % (len(recs), args.key_hash))
        for r in recs:
            _show(r)
        return 1
    r = recs[0]
    print("\nabout to replace this key:\n")
    _show(r)
    if r["reissued_to"]:
        print("\nthis key was already replaced by %s. Reissue THAT one if it has also been "
              "lost." % r["reissued_to"][:16])
        return 1
    print("\n  the %d credit%s move to a new key" % (r["balance"], "" if r["balance"] == 1 else "s"))
    print("  %s stops working and its holder is told it was replaced" % r["key_hash"][:16])
    if r["email"]:
        print("  %s will top up the NEW key on any future purchase" % r["email"])
    print("  the new key is shown once, here, and stored nowhere\n")
    if not args.yes:
        # The whole hash prefix, not "y". It is the one thing that proves the operator is
        # looking at the record they think they are.
        want = r["key_hash"][:8]
        got = input("type %s to confirm, anything else to abort: " % want).strip().lower()
        if got != want:
            print("aborted, nothing was changed")
            return 1
    out = billing.reissue(r["key_hash"], note=args.note)
    print("\n" + "=" * 72)
    print("  NEW KEY  %s" % out["key"])
    print("=" * 72)
    print("  %d credit%s, was %s" % (out["credits"], "" if out["credits"] == 1 else "s",
                                     out["from"][:16]))
    if out["email"]:
        print("  send it to %s -- the address on the purchase, and nowhere else" % out["email"])
    print("  it is not recoverable from here. If this scrolls away, the only fix is another"
          "\n  reissue, which is one more key for the customer to keep track of.\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Issue a replacement ATONAL render key.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="look a customer up by address, key hash or key")
    f.add_argument("query")
    f.set_defaults(fn=cmd_find)
    i = sub.add_parser("issue", help="retire a key and mint its replacement")
    i.add_argument("key_hash", help="enough of the hash to be unambiguous (from `find`)")
    i.add_argument("--note", default=None, help="written into the ledger, e.g. a ticket id")
    i.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    i.set_defaults(fn=cmd_issue)
    args = ap.parse_args()
    if not os.path.exists(billing.DB_PATH):
        print("no ledger at %s (set ATONAL_DB)" % billing.DB_PATH)
        return 2
    try:
        return args.fn(args)
    except ValueError as e:
        print("refused: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
