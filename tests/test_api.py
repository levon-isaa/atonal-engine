"""API tests. Each test gets its own server, database and sample studio, with the clock fixed at
09:00 studio time so free times are predictable.

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
from rota import auth, booking, config as configmod, sample  # noqa: E402

TZ = ZoneInfo("Asia/Yerevan")
OWNER, STAFF, PW = "owner@test.am", "desk@test.am", "correct horse battery"


class Api(unittest.TestCase):
    origin = ""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = configmod.from_env({"ROTA_DB": os.path.join(self.tmp.name, "t.db"), "ROTA_PORT": "0", "ROTA_ORIGIN": self.origin,
                                  "ROTA_SECRET": "test-secret", "ROTA_BOOKINGS_PER_HOUR": "5"})
        self.srv, self.app = server.make_server(cfg)
        today = datetime.now(TZ).date()
        self.now = datetime.combine(today, datetime.min.time(), tzinfo=TZ) + timedelta(hours=9)
        self.app.now = lambda s: self.now
        with self.app.store.tx() as c:
            for kind, items in sample.build(today, 9 * 60).items():
                for doc_id, body in items.items():
                    self.app.store.put(c, kind, doc_id, body)
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
    def req(self, method, path, body=None, headers=None, cookie=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"X-Rota": "1"} if method != "GET" else {}
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

    def login(self, email=OWNER):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/auth/login", body=json.dumps({"email": email, "password": PW}),
                     headers={"X-Rota": "1", "Content-Type": "application/json"})
        res = conn.getresponse()
        res.read()
        cookie = res.getheader("Set-Cookie")
        conn.close()
        self.assertEqual(res.status, 200)
        return cookie.split(";")[0]

    def tomorrow(self):
        return (self.now.date() + timedelta(days=1)).isoformat()

    def free(self, services="sv-mani", staff="any", date=None):
        st, av = self.req("GET", f"/api/public/availability?services={services}&staff={staff}&from={date or self.tomorrow()}&days=1")
        self.assertEqual(st, 200, av)
        return av["days"][0]["slots"]

    def book(self, slot_t, services=("sv-mani",), staff="any", date=None, phone="+374 91 555 111", name="Test Client", **extra):
        body = {"services": list(services), "staff": staff, "date": date or self.tomorrow(), "start": slot_t,
                "name": name, "phone": phone, "lang": "hy", **extra}
        return self.req("POST", "/api/public/bookings", body)

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
        with self.app.store.read() as c:
            clients = self.app.store.all(c, "clients")
        blob = json.dumps(studio, ensure_ascii=False) + avail
        for cl in clients:
            self.assertNotIn(cl["name"], blob)
            self.assertNotIn(cl["phone"], blob)
        self.assertNotIn("clientName", blob)
        self.assertEqual({s["id"] for s in studio["staff"]}, {"st-mariam", "st-davit", "st-lilit", "st-elina"})
        self.assertIn("hy", studio["services"][0]["i18n"])

    def test_busy_times_and_lead_time_are_not_offered(self):
        tomorrow = self.tomorrow()
        with self.app.store.read() as c:
            taken = [a for a in self.app.store.appointments_on(c, tomorrow, "st-lilit") if a["status"] != "cancelled"]
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
        with self.app.store.tx() as c:
            svc = self.app.store.get(c, "services", "sv-facial")
            svc["online"] = False
            self.app.store.put(c, "services", "sv-facial", {k: v for k, v in svc.items() if k != "id"})
        st, err = self.book(t, services=("sv-facial",))
        self.assertEqual(err["error"], "unknown_service")
        st, err = self.book(t, website="http://spam")
        self.assertEqual(st, 400)
        yesterday = (self.now.date() - timedelta(days=1)).isoformat()
        st, err = self.book(600, date=yesterday)
        self.assertEqual((st, err["error"]), (409, "slot_taken"))

    def test_writes_need_the_app_header_and_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/public/bookings", body="name=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
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
        appt_id = booking.parse_token("test-secret", tok)
        with self.app.store.read() as c:
            stored = self.app.store.get(c, "appointments", appt_id)
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
        with self.app.store.tx() as c:
            self.app.store.put(c, "appointments", "a_soon", {"date": self.now.date().isoformat(), "start": start, "duration": 45,
                                                            "staffId": "st-davit", "serviceIds": ["sv-mcut"], "status": "booked", "clientName": "X"})
        tok = booking.manage_token("test-secret", "a_soon")
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
        conn.request("POST", "/api/auth/login", body=json.dumps({"email": OWNER, "password": PW}), headers={"X-Rota": "1", "Content-Type": "application/json"})
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
        with self.app.store.read() as c:
            self.assertNotIn("mystery", self.app.store.get(c, "appointments", "a_desk1"))
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
            conn.request("POST", "/api/auth/logout", body=b"{}", headers={"X-Rota": "1", "Content-Type": "application/json"})
            res = conn.getresponse(); res.read()
            self.assertEqual(res.status, 200)
        conn.request("GET", "/api/health")
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
    origin = "https://book.example.am"

    def test_foreign_origin_is_refused(self):
        st, err = self.req("POST", "/api/public/bookings", {"services": ["sv-mani"]}, headers={"Origin": "https://evil.example"})
        self.assertEqual((st, err["error"]), (403, "bad_origin"))

    def test_secure_cookie_on_https_sites(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/auth/login", body=json.dumps({"email": OWNER, "password": PW}), headers={"X-Rota": "1", "Content-Type": "application/json"})
        res = conn.getresponse()
        res.read()
        self.assertIn("Secure", res.getheader("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()
