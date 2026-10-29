#!/usr/bin/env python3
"""Rota admin commands.

Rota itself
    python manage.py create-admin you@rota.am [--name "Your name"]   # signs in at https://<domain>/admin/
    python manage.py list-studios
    python manage.py create-studio nairi --business "Atelier Nairi" --owner anna@nairi.am [--kind hair] [--sample]
    python manage.py set-status nairi pilot|active|paused [--until 2027-01-31]
    python manage.py delete-studio nairi --yes                      # moves its file to data/deleted/
    python manage.py reset-demo                                     # the public demo at demo.<domain>
    python manage.py backup /var/backups/rota [--keep 14]

One studio's staff
    python manage.py create-user --studio nairi desk@nairi.am [--name NAME] [--role owner|staff]
    python manage.py set-password --studio nairi desk@nairi.am      # also signs them out everywhere
    python manage.py list-users --studio nairi
    python manage.py remove-user --studio nairi desk@nairi.am
    python manage.py seed-sample --studio nairi [--force]

Passwords are read from the terminal, or from standard input with --password-stdin.
Data lives in ROTA_DATA (default data/).
"""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sqlite3
import sys
from datetime import date as Date, datetime, timedelta

from rota import auth, booking, config as configmod, platform, templates
from rota.platform import Platform, SignupError
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


def _studio_store(plat: Platform, slug: str) -> Store:
    with plat.read() as c:
        if not Platform.studio(c, slug):
            sys.exit(f"No studio called {slug}. See: python manage.py list-studios")
    return Store(plat.studio_path(slug))


def _copy_db(src_path: str, dst_path: str) -> None:
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Rota admin commands")
    sub = p.add_subparsers(dest="cmd", required=True)

    ca = sub.add_parser("create-admin", help="add an account for Rota's own admin page")
    ca.add_argument("email")
    ca.add_argument("--name", default="")
    ca.add_argument("--password-stdin", action="store_true")
    sub.add_parser("list-studios")
    cs = sub.add_parser("create-studio", help="sign a company up by hand")
    cs.add_argument("slug")
    cs.add_argument("--business", required=True)
    cs.add_argument("--owner", required=True, help="the owner's email; they sign in with it")
    cs.add_argument("--owner-name", default="")
    cs.add_argument("--phone", default="+374 00 000 000")
    cs.add_argument("--kind", choices=list(templates.KINDS), default="other")
    cs.add_argument("--lang", choices=booking.LANGS, default="hy")
    cs.add_argument("--sample", action="store_true", help="load the Atelier Nairi sample instead of starter services")
    cs.add_argument("--password-stdin", action="store_true")
    st = sub.add_parser("set-status")
    st.add_argument("slug")
    st.add_argument("status", choices=("pilot", "active", "paused"))
    st.add_argument("--until", help="pilot end date, YYYY-MM-DD")
    ds = sub.add_parser("delete-studio", help="remove a company (its file is kept in data/deleted/)")
    ds.add_argument("slug")
    ds.add_argument("--yes", action="store_true")
    sub.add_parser("reset-demo", help="create or refresh the public demo studio")
    bk = sub.add_parser("backup", help="copy every database safely while the server runs")
    bk.add_argument("dir")
    bk.add_argument("--keep", type=int, default=0, help="also delete backups older than this many days")

    for name, extra in (("create-user", True), ("set-password", False), ("list-users", None), ("remove-user", False)):
        sp = sub.add_parser(name)
        sp.add_argument("--studio", required=True)
        if extra is not None:
            sp.add_argument("email")
        if extra:
            sp.add_argument("--name", default="")
            sp.add_argument("--role", choices=("owner", "staff"), default="staff")
        if name in ("create-user", "set-password"):
            sp.add_argument("--password-stdin", action="store_true")
    ss = sub.add_parser("seed-sample", help="load the sample studio into a studio")
    ss.add_argument("--studio", required=True)
    ss.add_argument("--force", action="store_true", help="replace records that are already there")
    args = p.parse_args(argv)

    cfg = configmod.from_env()
    plat = Platform(cfg.data_dir)

    if args.cmd == "create-admin":
        pw = _password(args)
        with plat.tx() as c:
            if Store.user_by_email(c, args.email):
                sys.exit(f"{args.email} is already an admin.")
            c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, ?, ?, 'admin')",
                      (args.email.strip().lower(), args.name, auth.hash_password(pw)))
        print(f"Created admin {args.email}. Sign in at {cfg.site_url}/admin/")

    elif args.cmd == "list-studios":
        with plat.read() as c:
            rows = c.execute("SELECT * FROM studios ORDER BY created").fetchall()
        for r in rows:
            print(f"{r['slug']:24} {r['status']:7} {r['pilot_ends'] or '':10}  {r['name'][:28]:28} {r['owner_email']:30} {r['created']}")
        print(f"{len(rows)} studio(s).")

    elif args.cmd == "create-studio":
        pw = _password(args)
        data = {"business": args.business, "slug": args.slug, "name": args.owner_name or args.business, "email": args.owner,
                "phone": args.phone, "password": pw, "kind": args.kind, "lang": args.lang}
        try:
            slug, _ = platform.create_studio(plat, data, cfg.pilot_days, "manage.py")
        except SignupError as e:
            sys.exit(e.message)
        if args.sample:
            store = Store(plat.studio_path(slug))
            now = datetime.now(booking.studio_tz({}, cfg.timezone))
            with store.tx() as c:
                platform.load_sample(store, c, now.date(), now.hour * 60 + now.minute)
        print(f"Created {cfg.studio_url(slug)} (desk: {cfg.studio_url(slug)}/desk/).")

    elif args.cmd == "set-status":
        until = args.until
        if until:
            booking.parse_date(until)
        with plat.tx() as c:
            row = Platform.studio(c, args.slug)
            if not row or row["status"] == "demo":
                sys.exit(f"No studio called {args.slug}.")
            c.execute("UPDATE studios SET status = ?, pilot_ends = COALESCE(?, pilot_ends) WHERE slug = ?", (args.status, until, args.slug))
        print(f"{args.slug} is now {args.status}.")

    elif args.cmd == "delete-studio":
        if not args.yes:
            sys.exit("This removes the studio's address and data from Rota. Run again with --yes to confirm.")
        path = plat.studio_path(args.slug)
        with plat.tx() as c:
            if not Platform.studio(c, args.slug):
                sys.exit(f"No studio called {args.slug}.")
            dest = os.path.join(cfg.data_dir, "deleted")
            os.makedirs(dest, exist_ok=True)
            kept = os.path.join(dest, f"{args.slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
            if os.path.exists(path):
                _copy_db(path, kept)
            c.execute("DELETE FROM studios WHERE slug = ?", (args.slug,))
        platform.remove_files(path)
        print(f"Deleted {args.slug}. A copy is kept at {kept}; delete it once you're sure.")
        print("Restart the server (systemctl restart rota) so it lets go of the old file.")

    elif args.cmd == "reset-demo":
        n = platform.reset_demo(plat, cfg.timezone)
        print(f"Demo studio ready at {cfg.studio_url(platform.DEMO)} with {n} sample records.")

    elif args.cmd == "backup":
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        target = os.path.join(args.dir, stamp)
        os.makedirs(os.path.join(target, "studios"), exist_ok=True)
        _copy_db(plat.path, os.path.join(target, "platform.db"))
        n = 0
        for name in sorted(os.listdir(plat.studios_dir)):
            if name.endswith(".db"):
                _copy_db(os.path.join(plat.studios_dir, name), os.path.join(target, "studios", name))
                n += 1
        print(f"Backed up the platform and {n} studio(s) to {target}.")
        if args.keep > 0:
            cutoff = datetime.now() - timedelta(days=args.keep)
            for name in sorted(os.listdir(args.dir)):
                full = os.path.join(args.dir, name)
                try:
                    when = datetime.strptime(name, "%Y-%m-%d-%H%M%S")
                except ValueError:
                    continue  # not one of ours
                if when < cutoff and os.path.isdir(full):
                    shutil.rmtree(full)
                    print(f"Removed old backup {name}.")

    elif args.cmd == "create-user":
        store = _studio_store(plat, args.studio)
        pw = _password(args)
        with store.tx() as c:
            if Store.user_by_email(c, args.email):
                sys.exit(f"{args.email} already has an account at {args.studio}.")
            c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, ?, ?, ?)",
                      (args.email.strip().lower(), args.name, auth.hash_password(pw), args.role))
        print(f"Created {args.role} account for {args.email} at {args.studio}.")

    elif args.cmd == "set-password":
        store = _studio_store(plat, args.studio)
        pw = _password(args)
        with store.tx() as c:
            row = Store.user_by_email(c, args.email)
            if not row:
                sys.exit(f"No account for {args.email} at {args.studio}.")
            c.execute("UPDATE users SET pw = ? WHERE id = ?", (auth.hash_password(pw), row["id"]))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        print(f"Password changed for {args.email}; their other sessions are signed out.")

    elif args.cmd == "list-users":
        store = _studio_store(plat, args.studio)
        with store.read() as c:
            for row in c.execute("SELECT email, name, role, created FROM users ORDER BY id"):
                print(f"{row['email']:32} {row['role']:6} {row['name']:20} {row['created']}")

    elif args.cmd == "remove-user":
        store = _studio_store(plat, args.studio)
        with store.tx() as c:
            n = c.execute("DELETE FROM users WHERE email = ?", (args.email.strip(),)).rowcount
        print("Removed." if n else f"No account for {args.email} at {args.studio}.")

    elif args.cmd == "seed-sample":
        store = _studio_store(plat, args.studio)
        with store.tx() as c:
            existing = c.execute("SELECT COUNT(*) FROM docs WHERE deleted = 0 AND kind != 'settings'").fetchone()[0]
            if existing and not args.force:
                sys.exit(f"{args.studio} already has {existing} records. Use --force to replace them with the sample.")
            now = datetime.now(booking.studio_tz({}, cfg.timezone))
            n = platform.load_sample(store, c, now.date(), now.hour * 60 + now.minute)
        print(f"Loaded the sample studio into {args.studio}: {n} records.")


if __name__ == "__main__":
    main()
