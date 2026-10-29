"""API tests. Each test gets its own server and data folder with one studio, Atelier Nairi (the
sample), at nairi.<domain>, and the clock fixed at 09:00 studio time so free times are predictable.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from rota import auth, booking, config as configmod, platform, sample  # noqa: E402
from rota.platform import Platform  # noqa: E402
from rota.store import Store  # noqa: E402

TZ = ZoneInfo("Asia/Yerevan")
OWNER, STAFF, PW = "owner@test.am", "desk@test.am", "correct horse battery"
ADMIN = "admin@rota.test"


class Base(unittest.TestCase):
    public_url = "http://localhost"
    env: dict = {}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = configmod.from_env({"ROTA_DATA": self.tmp.name, "ROTA_PORT": "0", "ROTA_PUBLIC_URL": self.public_url,
                                  "ROTA_SECRET": "test-secret", "ROTA_BOOKINGS_PER_HOUR": "5", **self.env})
        self.srv, self.app = server.make_server(cfg)
        self.domain = cfg.domain
        today = datetime.now(TZ).date()
        self.now = datetime.combine(today, datetime.min.time(), tzinfo=TZ) + timedelta(hours=9)
        self.app.now = lambda s: self.now
        with self.app.platform.tx() as c:
            c.execute("INSERT INTO studios (slug, name, kind, status, pilot_ends, created) VALUES ('nairi', 'Atelier Nairi', 'hair', 'pilot', ?, '2026-10-01T00:00:00Z')",
                      ((today + timedelta(days=30)).isoformat(),))
            c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, 'Admin', ?, 'admin')", (ADMIN, auth.hash_password(PW)))
        self.store = self.app.store("nairi")
        self.secret = self.app.studio_secret("nairi")
        with self.store.tx() as c:
            for kind, items in sample.build(today, 9 * 60).items():
                for doc_id, body in items.items():
                    self.store.put(c, kind, doc_id, body)
            for email, role in ((OWNER, "owner"), (STAFF, "staff")):
                c.execute("INSERT INTO users (email, name, pw, role) VALUES (?, ?, ?, ?)", (email, role, auth.hash_password(PW), role))
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    # ---------------------------------------------------------------- helpers
    def host(self, studio="nairi"):
        return f"{studio}.{self.domain}" if studio else self.domain

    def req(self, method, path, body=None, headers=None, cookie=None, raw=False, studio="nairi"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": self.host(studio)}
        if method != "GET":
            h["X-Rota"] = "1"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        if cookie:
            h["Cookie"] = cookie
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        text = res.read().decode()
        conn.close()
        if raw:
            return res, text
        return res.status, (json.loads(text) if text and res.getheader("Content-Type", "").startswith("application/json") else text)

    def cookie_from(self, res):
        cookie = res.getheader("Set-Cookie")
        self.assertTrue(cookie, "no cookie set")
        return cookie.split(";")[0]

    def login(self, email=OWNER, studio="nairi", path="/api/auth/login"):
        res, text = self.req("POST", path, {"email": email, "password": PW}, raw=True, studio=studio)
        self.assertEqual(res.status, 200, text)
        return self.cookie_from(res)

    def tomorrow(self):
        return (self.now.date() + timedelta(days=1)).isoformat()

    def free(self, services="sv-mani", staff="any", date=None, studio="nairi"):
        st, av = self.req("GET", f"/api/public/availability?services={services}&staff={staff}&from={date or self.tomorrow()}&days=1", studio=studio)
        self.assertEqual(st, 200, av)
        return av["days"][0]["slots"]

    def book(self, slot_t, services=("sv-mani",), staff="any", date=None, phone="+374 91 555 111", name="Test Client", studio="nairi", **extra):
        body = {"services": list(services), "staff": staff, "date": date or self.tomorrow(), "start": slot_t,
                "name": name, "phone": phone, "lang": "hy", **extra}
        return self.req("POST", "/api/public/bookings", body, studio=studio)

    def signup(self, slug="lumen", **extra):
        body = {"business": "Lumen Nails", "slug": slug, "kind": "nails", "name": "Lusine", "email": "lusine@lumen.am",
                "phone": "+374 91 222 333", "password": "lumen-password-1", "lang": "ru", **extra}
        return self.req("POST", "/api/site/signup", body, studio=None)

    def ticket_login(self, desk_url, slug="lumen"):
        ticket = desk_url.split("#welcome=")[1]
        res, text = self.req("POST", "/api/auth/ticket", {"ticket": ticket}, raw=True, studio=slug)
        return res, text


class Api(Base):
    # ---------------------------------------------------------------- public reads
    def test_security_headers_on_every_response(self):
        for path in ("/", "/api/health", "/desk/"):
            res, _ = self.req("GET", path, raw=True)
            self.assertIn("default-src 'self'", res.getheader("Content-Security-Policy"))
            self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(res.getheader("X-Frame-Options"), "DENY")

    def test_public_endpoints_never_expose_clients(self):
        _, studio = self.req("GET", "/api/public/studio")
        res, avail = self.req("GET", f"/api/public/availability?services=sv-wcut&staff=any&from={self.now.date()}&days=14&next=1", raw=True)
        with self.store.read() as c:
            clients = self.store.all(c, "clients")
        blob = json.dumps(studio, ensure_ascii=False) + avail
        for cl in clients:
            self.assertNotIn(cl["name"], blob)
            self.assertNotIn(cl["phone"], blob)
        self.assertNotIn("clientName", blob)
        self.assertEqual({s["id"] for s in studio["staff"]}, {"st-mariam", "st-davit", "st-lilit", "st-elina"})
        self.assertIn("hy", studio["services"][0]["i18n"])

    def test_busy_times_and_lead_time_are_not_offered(self):
        tomorrow = self.tomorrow()
        with self.store.read() as c:
            taken = [a for a in self.store.appointments_on(c, tomorrow, "st-lilit") if a["status"] != "cancelled"]
        slots = {s["t"] for s in self.free("sv-mani", "st-lilit")}
        for a in taken:
            for t in slots:
                self.assertFalse(t < a["start"] + a["duration"] and t + 45 > a["start"], f"{t} overlaps {a['start']}")
        # Today, nothing earlier than 09:30 (now + 30 minutes of notice).
        today_slots = self.free("sv-mcut", "st-davit", date=self.now.date().isoformat())
        self.assertTrue(all(s["t"] >= 9 * 60 + 30 for s in today_slots))

    def test_day_off_is_closed(self):
        # Lilit doesn't work Saturdays.
        d = self.now.date()
        while d.weekday() != 5:
            d += timedelta(days=1)
        _, av = self.req("GET", f"/api/public/availability?services=sv-gel&staff=st-lilit&from={d}&days=1")
        self.assertEqual(av["days"][0]["state"], "closed")
        self.assertEqual(av["days"][0]["slots"], [])

    # ---------------------------------------------------------------- booking
    def test_book_then_the_slot_is_gone(self):
        slot = self.free()[0]
        st, view = self.book(slot["t"])
        self.assertEqual(st, 201, view)
        self.assertEqual(view["status"], "booked")
        self.assertTrue(view["token"])
        self.assertIn(view["staff"]["id"], slot["staff"])
        st2, err = self.book(slot["t"], staff=view["staff"]["id"], phone="+374 91 555 222")
        self.assertEqual(st2, 409)
        self.assertEqual(err["error"], "slot_taken")

    def test_simultaneous_bookings_of_one_slot_only_one_wins(self):
        slot = next(s for s in self.free("sv-mani", "st-lilit"))
        results = []
        def go(i):
            results.append(self.book(slot["t"], staff="st-lilit", phone=f"+374 91 600 0{i:02d}")[0])
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results).count(201), 1, results)
        self.assertEqual(results.count(409), 7, results)

    def test_input_is_checked(self):
        t = self.free()[0]["t"]
        cases = [
            ({"name": ""}, "missing_name"), ({"phone": "123"}, "bad_phone"), ({"services": ["nope"]}, "unknown_service"),
            ({"services": []}, "bad_services"), ({"start": "10:00"}, "bad_time"), ({"date": "2026-13-40"}, "bad_date"),
        ]
        for patch, code in cases:
            st, err = self.book(t, **patch)
            self.assertEqual((st, err.get("error")), (400, code), patch)
        with self.store.tx() as c:
            svc = self.store.get(c, "services", "sv-facial")
            svc["online"] = False
            self.store.put(c, "services", "sv-facial", {k: v for k, v in svc.items() if k != "id"})
        st, err = self.book(t, services=("sv-facial",))
        self.assertEqual(err["error"], "unknown_service")
        st, err = self.book(t, website="http://spam")
        self.assertEqual(st, 400)
        yesterday = (self.now.date() - timedelta(days=1)).isoformat()
        st, err = self.book(600, date=yesterday)
        self.assertEqual((st, err["error"]), (409, "slot_taken"))

    def test_writes_need_the_app_header_and_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/public/bookings", body="name=x", headers={"Content-Type": "application/x-www-form-urlencoded", "Host": self.host()})
        res = conn.getresponse()
        self.assertEqual(res.status, 403)
        res.read()
        st, _ = self.req("POST", "/api/public/bookings", None, headers={"Content-Type": "text/plain", "Content-Length": "2"})
        self.assertIn(st, (400, 415))

    def test_one_phone_cannot_hoard_the_week(self):
        self.app.cfg.bookings_per_hour = 50
        slots = self.free("sv-mcut", "st-davit", date=(self.now.date() + timedelta(days=2)).isoformat())
        codes = [self.book(s["t"], services=("sv-mcut",), staff="st-davit", date=(self.now.date() + timedelta(days=2)).isoformat(), phone="+374 99 123 456")[0] for s in slots[::4][:5]]
        self.assertEqual(codes[:4], [201] * 4)
        self.assertEqual(codes[4], 429)

    def test_booking_rate_limit_per_address(self):
        # Mistakes don't use up the allowance...
        for _ in range(6):
            self.assertEqual(self.book(600, name="")[0], 400)
        # ...but only five bookings an hour succeed from one address (ROTA_BOOKINGS_PER_HOUR=5 here).
        codes = []
        for i in range(6):
            day = (self.now.date() + timedelta(days=2 + i)).isoformat()
            slot = self.free("sv-mani", "any", date=day)[0]
            codes.append(self.book(slot["t"], date=day, phone=f"+374 98 700 {i:03d}")[0])
        self.assertEqual(codes, [201] * 5 + [429])

    # ---------------------------------------------------------------- manage links
    def test_manage_cancel_and_reschedule(self):
        slots = self.free("sv-mani", "st-lilit")
        _, view = self.book(slots[0]["t"], staff="st-lilit")
        tok = view["token"]
        st, got = self.req("GET", f"/api/public/bookings/{tok}")
        self.assertEqual((st, got["canChange"]), (200, True))
        self.assertEqual(self.req("GET", f"/api/public/bookings/{tok[:-2]}xx")[0], 404)
        # Move to a later free time the same day.
        later = self.free("sv-mani", "st-lilit")[-1]["t"]
        st, moved = self.req("POST", f"/api/public/bookings/{tok}/reschedule", {"date": self.tomorrow(), "start": later})
        self.assertEqual((st, moved["start"]), (200, later), moved)
        appt_id = booking.parse_token(self.secret, tok)
        with self.store.read() as c:
            stored = self.store.get(c, "appointments", appt_id)
        self.assertEqual(stored["clientAction"], "rescheduled")
        self.assertIsNone(stored.get("remindedAt"))
        st, done = self.req("POST", f"/api/public/bookings/{tok}/cancel", {})
        self.assertEqual((st, done["status"], done["cancelledBy"]), (200, "cancelled", "client"))
        self.assertEqual(self.req("POST", f"/api/public/bookings/{tok}/cancel", {})[0], 409)

    def test_cannot_reschedule_into_a_taken_slot(self):
        slots = self.free("sv-mani", "st-lilit")
        _, a = self.book(slots[0]["t"], staff="st-lilit", phone="+374 91 100 001")
        _, b = self.book(slots[-1]["t"], staff="st-lilit", phone="+374 91 100 002")
        st, err = self.req("POST", f"/api/public/bookings/{a['token']}/reschedule", {"date": self.tomorrow(), "start": slots[-1]["t"]})
        self.assertEqual((st, err["error"]), (409, "slot_taken"))

    def test_too_close_to_the_visit_to_change_online(self):
        start = self.now.hour * 60 + 60  # an hour from now; the sample allows changes until 3 hours before
        with self.store.tx() as c:
            self.store.put(c, "appointments", "a_soon", {"date": self.now.date().isoformat(), "start": start, "duration": 45,
                                                            "staffId": "st-davit", "serviceIds": ["sv-mcut"], "status": "booked", "clientName": "X"})
        tok = booking.manage_token(self.secret, "a_soon")
        _, view = self.req("GET", f"/api/public/bookings/{tok}")
        self.assertFalse(view["canChange"])
        st, err = self.req("POST", f"/api/public/bookings/{tok}/cancel", {})
        self.assertEqual((st, err["error"]), (409, "too_late"))

    # ---------------------------------------------------------------- staff
    def test_desk_needs_sign_in(self):
        self.assertEqual(self.req("GET", "/api/desk/sync?rev=0")[0], 401)
        self.assertEqual(self.req("POST", "/api/auth/login", {"email": OWNER, "password": "wrong"})[0], 401)
        self.assertEqual(self.req("POST", "/api/auth/login", {"email": "nobody@x.am", "password": PW})[0], 401)

    def test_session_cookie_flags(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/auth/login", body=json.dumps({"email": OWNER, "password": PW}), headers={"X-Rota": "1", "Content-Type": "application/json", "Host": self.host()})
        res = conn.getresponse()
        res.read()
        c = res.getheader("Set-Cookie")
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Lax", c)

    def test_sync_sees_online_bookings_and_deletions(self):
        cookie = self.login()
        st, first = self.req("GET", "/api/desk/sync?rev=0", cookie=cookie)
        self.assertEqual(st, 200)
        kinds = {r["kind"] for r in first["rows"]}
        self.assertTrue({"settings", "staff", "services", "clients", "appointments"} <= kinds)
        self.assertTrue(all("manage" in r["body"] for r in first["rows"] if r["kind"] == "appointments"))
        _, view = self.book(self.free()[0]["t"])
        _, delta = self.req("GET", f"/api/desk/sync?rev={first['rev']}", cookie=cookie)
        new = [r for r in delta["rows"] if r["kind"] == "appointments"]
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["body"]["source"], "online")
        self.assertEqual(self.req("DELETE", f"/api/desk/appointments/{new[0]['id']}", cookie=cookie)[0], 200)
        _, delta2 = self.req("GET", f"/api/desk/sync?rev={delta['rev']}", cookie=cookie)
        self.assertTrue(delta2["rows"][0]["deleted"])

    def test_desk_writes_are_validated(self):
        cookie = self.login()
        ok = {"date": self.tomorrow(), "start": 600, "duration": 60, "staffId": "st-mariam", "serviceIds": ["sv-wcut"],
              "clientName": "Walk-in", "status": "booked", "price": 12000, "mystery": "dropped"}
        self.assertEqual(self.req("PUT", "/api/desk/appointments/a_desk1", ok, cookie=cookie)[0], 200)
        with self.store.read() as c:
            self.assertNotIn("mystery", self.store.get(c, "appointments", "a_desk1"))
        for bad in ({**ok, "start": 2000}, {**ok, "date": "tomorrow"}, {**ok, "status": "lost"}, {**ok, "duration": "1h"}):
            self.assertEqual(self.req("PUT", "/api/desk/appointments/a_desk2", bad, cookie=cookie)[0], 400, bad)
        self.assertEqual(self.req("PUT", "/api/desk/potions/x", {}, cookie=cookie)[0], 404)
        self.assertEqual(self.req("PUT", "/api/desk/appointments/bad%20id%21", ok, cookie=cookie)[0], 400)

    def test_only_owners_change_the_studio(self):
        staff = self.login(STAFF)
        owner = self.login(OWNER)
        body = {"name": "Renamed"}
        self.assertEqual(self.req("PUT", "/api/desk/settings/studio", body, cookie=staff)[0], 403)
        self.assertEqual(self.req("DELETE", "/api/desk/staff/st-davit", cookie=staff)[0], 403)
        self.assertEqual(self.req("PUT", "/api/desk/settings/studio", body, cookie=owner)[0], 200)

    def test_login_attempts_are_limited(self):
        codes = [self.req("POST", "/api/auth/login", {"email": OWNER, "password": f"guess{i}"})[0] for i in range(9)]
        self.assertEqual(codes[:8], [401] * 8)
        self.assertEqual(codes[8], 429)

    def test_successful_sign_ins_never_lock_an_account(self):
        for _ in range(12):
            self.login()

    def test_logout_ends_the_session(self):
        cookie = self.login()
        self.assertEqual(self.req("POST", "/api/auth/logout", {}, cookie=cookie)[0], 200)
        self.assertEqual(self.req("GET", "/api/auth/me", cookie=cookie)[0], 401)

    def test_unread_bodies_do_not_break_the_next_request(self):
        # Logout ignores its body; the next request on the same connection must still work.
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for _ in range(3):
            conn.request("POST", "/api/auth/logout", body=b"{}", headers={"X-Rota": "1", "Content-Type": "application/json", "Host": self.host()})
            res = conn.getresponse(); res.read()
            self.assertEqual(res.status, 200)
        conn.request("GET", "/api/health", headers={"Host": self.host()})
        res = conn.getresponse(); res.read()
        self.assertEqual(res.status, 200)
        conn.close()

    # ---------------------------------------------------------------- static
    def test_pages_and_no_path_traversal(self):
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(self.req("GET", "/b/some-link")[0], 200)
        self.assertEqual(self.req("GET", "/desk", raw=True)[0].status, 301)
        self.assertEqual(self.req("GET", "/assets/../server.py")[0], 404)
        self.assertEqual(self.req("GET", "/assets/%2e%2e/server.py")[0], 404)


class OriginCheck(Api):
    public_url = "https://book.example.am"

    def test_foreign_origin_is_refused(self):
        st, err = self.req("POST", "/api/public/bookings", {"services": ["sv-mani"]}, headers={"Origin": "https://evil.example"})
        self.assertEqual((st, err["error"]), (403, "bad_origin"))

    def test_another_studios_pages_cannot_write_here(self):
        st, err = self.req("POST", "/api/public/bookings", {"services": ["sv-mani"]}, headers={"Origin": "https://other.book.example.am"})
        self.assertEqual((st, err["error"]), (403, "bad_origin"))
        st, _ = self.req("POST", "/api/auth/login", {"email": OWNER, "password": PW}, headers={"Origin": "https://nairi.book.example.am"})
        self.assertEqual(st, 200)

    def test_secure_cookie_on_https_sites(self):
        res, _ = self.req("POST", "/api/auth/login", {"email": OWNER, "password": PW}, raw=True)
        cookie = res.getheader("Set-Cookie")
        self.assertIn("Secure", cookie)
        self.assertTrue(cookie.startswith("__Host-rota="), cookie)  # a sibling studio can't set or overwrite it


class Companies(Base):
    env = {"ROTA_SIGNUPS_PER_DAY": "3"}

    def test_addresses_decide_what_is_served(self):
        self.assertEqual(self.req("GET", "/", studio=None)[0], 200)                       # sales site
        self.assertEqual(self.req("GET", "/start", studio=None)[0], 200)
        self.assertEqual(self.req("GET", "/admin/", studio=None)[0], 200)
        self.assertEqual(self.req("GET", "/")[0], 200)                                    # a studio's booking site
        self.assertEqual(self.req("GET", "/", studio="nobody")[0], 404)                   # no such studio
        self.assertEqual(self.req("GET", "/api/public/studio", studio="nobody")[0], 404)
        self.assertEqual(self.req("GET", "/api/public/studio", studio=None)[0], 404)      # studio API only on studio addresses
        self.assertEqual(self.req("GET", "/api/site/info")[0], 404)                       # and sales API only on the main one
        self.assertEqual(self.req("GET", "/", headers={"Host": "evil.example"})[0], 404)
        self.assertEqual(self.req("GET", "/api/health", headers={"Host": "10.0.0.1"})[0], 200)

    def test_tls_check_only_approves_rotas_addresses(self):
        ok = lambda d: self.req("GET", f"/api/tls-check?domain={d}", headers={"Host": "127.0.0.1:8780"})[0]
        self.assertEqual(ok(self.domain), 200)
        self.assertEqual(ok("www." + self.domain), 200)
        self.assertEqual(ok("nairi." + self.domain), 200)
        self.assertEqual(ok("nobody." + self.domain), 404)
        self.assertEqual(ok("a.b." + self.domain), 404)
        self.assertEqual(ok("evil.example"), 404)

    def test_sign_up_gives_a_working_studio_and_a_one_time_sign_in(self):
        st, out = self.signup()
        self.assertEqual(st, 201, out)
        self.assertEqual(out["slug"], "lumen")
        self.assertIn("lumen." + self.domain, out["desk"])
        _, studio = self.req("GET", "/api/public/studio", studio="lumen")
        self.assertEqual(studio["name"], "Lumen Nails")
        self.assertEqual(studio["defaultLang"], "ru")
        self.assertIn("Gel manicure", [s["name"] for s in studio["services"]])
        self.assertTrue(self.free("sv-gel", studio="lumen"))                              # bookable right away
        res, text = self.ticket_login(out["desk"])
        self.assertEqual(res.status, 200, text)
        me = json.loads(text)
        self.assertEqual((me["role"], me["studio"]["status"]), ("owner", "pilot"))
        _, me2 = self.req("GET", "/api/auth/me", cookie=self.cookie_from(res), studio="lumen")
        self.assertEqual(me2["email"], "lusine@lumen.am")
        self.assertEqual(self.ticket_login(out["desk"])[0].status, 401)                   # spent
        res, _ = self.req("POST", "/api/auth/login", {"email": "lusine@lumen.am", "password": "lumen-password-1"}, raw=True, studio="lumen")
        self.assertEqual(res.status, 200)

    def test_sign_up_checks_its_input(self):
        cases = [({"slug": "ab"}, "bad_slug"), ({"slug": "-lumen"}, "bad_slug"), ({"slug": "lu--men"}, "bad_slug"),
                 ({"slug": "Lumen Nails"}, "bad_slug"), ({"slug": "admin"}, "slug_taken"), ({"slug": "nairi"}, "slug_taken"),
                 ({"password": "short"}, "short_password"), ({"email": "nope"}, "bad_email"), ({"phone": "12"}, "bad_phone"),
                 ({"business": ""}, "missing_business")]
        for patch, code in cases:
            st, err = self.signup(**patch)
            self.assertEqual(err.get("error"), code, patch)
            self.assertIn(st, (400, 409))
        self.assertEqual(self.signup(website="http://spam")[0], 400)
        _, ok = self.req("GET", "/api/site/slug?s=Lumen", studio=None)
        self.assertEqual((ok["ok"], ok["slug"]), (True, "lumen"))
        _, taken = self.req("GET", "/api/site/slug?s=nairi", studio=None)
        self.assertFalse(taken["ok"])

    def test_sign_ups_per_address_are_limited(self):
        codes = [self.signup(f"studio-{i}")[0] for i in range(4)]
        self.assertEqual(codes, [201, 201, 201, 429])

    def test_studios_cannot_see_each_other(self):
        _, out = self.signup()
        lumen = self.cookie_from(self.ticket_login(out["desk"])[0])
        nairi = self.login()
        # A session belongs to one studio.
        self.assertEqual(self.req("GET", "/api/desk/sync?rev=0", cookie=nairi, studio="lumen")[0], 401)
        self.assertEqual(self.req("GET", "/api/desk/sync?rev=0", cookie=lumen)[0], 401)
        # Lumen's desk holds only Lumen's records.
        _, sync = self.req("GET", "/api/desk/sync?rev=0", cookie=lumen, studio="lumen")
        with self.store.read() as c:
            nairi_names = {x["name"] for x in self.store.all(c, "clients")}
        self.assertFalse(nairi_names & {r["body"].get("name") for r in sync["rows"] if r["kind"] == "clients"})
        self.assertNotIn("Atelier Nairi", json.dumps(sync, ensure_ascii=False))
        # A manage link only opens a booking at the studio that issued it.
        _, view = self.book(self.free()[0]["t"])
        self.assertEqual(self.req("GET", f"/api/public/bookings/{view['token']}")[0], 200)
        self.assertEqual(self.req("GET", f"/api/public/bookings/{view['token']}", studio="lumen")[0], 404)
        # Nairi's staff emails don't exist at Lumen.
        self.assertEqual(self.req("POST", "/api/auth/login", {"email": OWNER, "password": PW}, studio="lumen")[0], 401)

    def test_a_paused_studio_stops_taking_bookings(self):
        slot = self.free()[0]["t"]
        _, view = self.book(slot)
        cookie = self.login()
        with self.app.platform.tx() as c:
            c.execute("UPDATE studios SET status = 'paused' WHERE slug = 'nairi'")
        _, studio = self.req("GET", "/api/public/studio")
        self.assertTrue(studio["paused"])
        self.assertNotIn("services", studio)
        self.assertEqual(self.req("GET", f"/api/public/availability?services=sv-mani&from={self.tomorrow()}&days=1")[1]["error"], "paused")
        self.assertEqual(self.book(slot + 60)[1]["error"], "paused")
        self.assertEqual(self.req("POST", "/api/auth/login", {"email": OWNER, "password": PW})[1]["error"], "paused")
        self.assertEqual(self.req("GET", "/api/desk/sync?rev=0", cookie=cookie)[1]["error"], "paused")
        # Clients can still look up and cancel what they booked.
        self.assertEqual(self.req("GET", f"/api/public/bookings/{view['token']}")[0], 200)
        self.assertEqual(self.req("POST", f"/api/public/bookings/{view['token']}/cancel", {})[0], 200)

    def test_rotas_admin_page(self):
        self.assertEqual(self.req("GET", "/api/admin/studios", studio=None)[0], 401)
        self.assertEqual(self.req("POST", "/api/admin/login", {"email": OWNER, "password": PW}, studio=None)[0], 401)  # studio staff aren't admins
        self.assertEqual(self.req("POST", "/api/admin/login", {"email": ADMIN, "password": PW})[0], 404)           # only on the main address
        admin = self.login(ADMIN, studio=None, path="/api/admin/login")
        self.assertEqual(self.req("GET", "/api/admin/studios", cookie=admin)[0], 404)
        self.assertEqual(self.req("GET", "/api/desk/sync?rev=0", cookie=admin)[0], 401)                             # and admins aren't studio staff
        self.book(self.free()[0]["t"])
        _, out = self.req("GET", "/api/admin/studios", cookie=admin, studio=None)
        nairi = next(s for s in out["studios"] if s["slug"] == "nairi")
        self.assertEqual((nairi["staff"], nairi["online7"] >= 1), (4, True))
        self.assertTrue(nairi["url"].startswith("http://nairi."))
        st, upd = self.req("POST", "/api/admin/studios/nairi", {"status": "active", "pilotEnds": "2027-02-01", "notes": "Pays yearly"}, cookie=admin, studio=None)
        self.assertEqual((st, upd["status"], upd["pilot_ends"]), (200, "active", "2027-02-01"))
        self.assertEqual(self.req("POST", "/api/admin/studios/nairi", {"status": "deleted"}, cookie=admin, studio=None)[0], 400)
        self.assertEqual(self.req("POST", "/api/admin/studios/nobody", {"notes": "x"}, cookie=admin, studio=None)[0], 404)

    def test_the_demo_signs_anyone_in_and_resets(self):
        self.assertEqual(self.req("POST", "/api/auth/demo", {})[0], 404)                  # not at a real studio
        platform.reset_demo(self.app.platform, "Asia/Yerevan")
        res, text = self.req("POST", "/api/auth/demo", {}, raw=True, studio="demo")
        self.assertEqual(res.status, 200, text)
        cookie = self.cookie_from(res)
        st, sync = self.req("GET", "/api/desk/sync?rev=0", cookie=cookie, studio="demo")
        self.assertEqual(st, 200)
        self.assertTrue(any(r["kind"] == "appointments" for r in sync["rows"]))
        self.assertTrue(self.req("GET", "/api/public/studio", studio="demo")[1]["demo"])
        platform.reset_demo(self.app.platform, "Asia/Yerevan")                            # the hourly reset signs everyone out
        self.assertEqual(self.req("GET", "/api/desk/sync?rev=0", cookie=cookie, studio="demo")[0], 401)

    def test_owners_can_download_their_data_safely(self):
        _, view = self.book(self.free()[0]["t"], name="=HYPERLINK(\"http://evil\")", phone="+374 91 777 000")
        owner, staff = self.login(), self.login(STAFF)
        self.assertEqual(self.req("GET", "/api/desk/export/clients.csv", cookie=staff)[0], 403)
        res, text = self.req("GET", "/api/desk/export/clients.csv", cookie=owner, raw=True)
        self.assertEqual(res.status, 200)
        self.assertIn("attachment", res.getheader("Content-Disposition"))
        self.assertTrue(text.startswith("\ufeffName,Phone"))
        self.assertIn("'=HYPERLINK", text)                                                 # not a live formula
        res, text = self.req("GET", "/api/desk/export/appointments.csv", cookie=owner, raw=True)
        self.assertEqual(res.status, 200)
        self.assertIn("+374 91 777 000", text)


if __name__ == "__main__":
    unittest.main()
