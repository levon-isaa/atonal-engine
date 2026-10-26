#!/usr/bin/env python3
"""Rota admin commands.

    python manage.py create-user owner@studio.am --name "Anna" --role owner
    python manage.py set-password owner@studio.am
    python manage.py list-users
    python manage.py remove-user someone@studio.am
    python manage.py seed-sample [--force]      # load the Atelier Nairi sample studio
    python manage.py backup /path/to/rota-backup.db

Passwords are read from the terminal, or from standard input with --password-stdin.
The database is ROTA_DB (default data/rota.db).
"""
from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from datetime import datetime

from rota import auth, booking, config as configmod, sample
from rota.store import Store


def _password(args) -> str:
    if args.password_stdin:
        pw = sys.stdin.readline().rstrip("\n")
    else:
        pw = getpass.getpass("Password: ")
        if getpass.getpass("Again: ") != pw:
            sys.exit("The two passwords don't match.")
    if len(pw) < 10:
        sys.exit("Use at least 10 characters.")
    return pw


def main(argv=None):
    p = argparse.ArgumentParser(description="Rota admin commands")
    sub = p.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user", help="add a staff account")
    cu.add_argument("email")
    cu.add_argument("--name", default="")
    cu.add_argument("--role", choices=("owner", "staff"), default="staff")
    cu.add_argument("--password-stdin", action="store_true")
    sp = sub.add_parser("set-password", help="change a staff password and sign them out everywhere")
    sp.add_argument("email")
    sp.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list-users")
    ru = sub.add_parser("remove-user")
    ru.add_argument("email")
    ss = sub.add_parser("seed-sample", help="load the sample studio")
    ss.add_argument("--force", action="store_true", help="replace studio data that is already there")
    bk = sub.add_parser("backup", help="copy the database safely while the server runs")
    bk.add_argument("path")
    args = p.parse_args(argv)

    cfg = configmod.from_env()
    store = Store(cfg.db_path)

    if args.cmd == "create-user":
        pw = _password(args)
        with store.tx() as c:
            if Store.user_by_email(c, args.email):
                sys.exit(f"{args.email} already has an account.")
            c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, ?, ?, ?)",
                      (args.email.strip().lower(), args.name, auth.hash_password(pw), args.role))
        print(f"Created {args.role} account for {args.email}.")
    elif args.cmd == "set-password":
        pw = _password(args)
        with store.tx() as c:
            row = Store.user_by_email(c, args.email)
            if not row:
                sys.exit(f"No account for {args.email}.")
            c.execute("UPDATE users SET pw = ? WHERE id = ?", (auth.hash_password(pw), row["id"]))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        print(f"Password changed for {args.email}; their other sessions are signed out.")
    elif args.cmd == "list-users":
        with store.read() as c:
            for row in c.execute("SELECT email, name, role, created FROM users ORDER BY id"):
                print(f"{row['email']:32} {row['role']:6} {row['name']:20} {row['created']}")
    elif args.cmd == "remove-user":
        with store.tx() as c:
            n = c.execute("DELETE FROM users WHERE email = ?", (args.email.strip(),)).rowcount
        print("Removed." if n else f"No account for {args.email}.")
    elif args.cmd == "seed-sample":
        with store.tx() as c:
            existing = c.execute("SELECT COUNT(*) FROM docs WHERE deleted = 0 AND kind != 'settings'").fetchone()[0]
            if existing and not args.force:
                sys.exit(f"The studio already has {existing} records. Use --force to replace them with the sample.")
            now = datetime.now(booking.studio_tz({"timezone": sample.SETTINGS["timezone"]}, cfg.timezone))
            if args.force:
                for row in c.execute("SELECT kind, id FROM docs WHERE deleted = 0").fetchall():
                    store.delete(c, row["kind"], row["id"])
            docs = sample.build(now.date(), now.hour * 60 + now.minute)
            n = 0
            for kind, items in docs.items():
                for doc_id, body in items.items():
                    store.put(c, kind, doc_id, body)
                    n += 1
        print(f"Loaded the sample studio: {n} records ({len(docs['appointments'])} appointments).")
    elif args.cmd == "backup":
        src = sqlite3.connect(cfg.db_path)
        dst = sqlite3.connect(args.path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        print(f"Backed up to {args.path}.")


if __name__ == "__main__":
    main()
