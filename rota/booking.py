"""Booking rules shared by the public site and the desk.

The public site never sees another client's name or phone. It asks for free times, and the server
works them out from the appointment book; it books, and the server checks the time is still free
inside the same write transaction that saves the booking.

Clients don't have accounts. A booking comes with a private manage link, signed with the server
secret, that lets whoever holds it see, move or cancel that one booking.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

LANGS = ("hy", "ru", "en")
STATUSES = ("booked", "confirmed", "arrived", "noshow", "cancelled")
LIVE = ("booked", "confirmed")
MAX_UPCOMING_PER_PHONE = 4

DEFAULT_SETTINGS = {
    "name": "Your studio", "tagline": "", "address": "", "phone": "", "currency": "֏", "countryCode": "374",
    "open": 600, "close": 1200, "step": 15, "bookingDays": 14, "leadMin": 30, "cancelHours": 3,
    "timezone": None, "defaultLang": "hy", "i18n": {},
    "reminder": {
        "en": "Hi {name}! A reminder of your visit {when} at {time}: {service} with {specialist} at {studio}, {address}. To change or cancel: {link}",
        "hy": "Բարև, {name}։ Հիշեցնում ենք Ձեր այցի մասին՝ {when}, ժամը {time}․ {service}, մասնագետ՝ {specialist}, {studio}, {address}։ Փոխելու կամ չեղարկելու համար՝ {link}",
        "ru": "Здравствуйте, {name}! Напоминаем о визите {when} в {time}: {service}, мастер — {specialist}, {studio}, {address}. Перенести или отменить: {link}",
    },
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,64}$")


class BookingError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------ time
def settings(c, store) -> dict:
    s = dict(DEFAULT_SETTINGS)
    stored = store.get(c, "settings", "studio") or {}
    stored.pop("id", None)
    s.update({k: v for k, v in stored.items() if v is not None})
    if isinstance(s.get("reminder"), str):  # older single-language template
        s["reminder"] = {**DEFAULT_SETTINGS["reminder"], "en": s["reminder"]}
    return s


def studio_tz(s: dict, fallback: str) -> ZoneInfo:
    try:
        return ZoneInfo(s.get("timezone") or fallback)
    except Exception:
        return ZoneInfo(fallback)


def parse_date(d: str) -> Date:
    if not isinstance(d, str) or not DATE_RE.match(d):
        raise BookingError("bad_date", "Use a date like 2026-10-27.")
    try:
        return Date.fromisoformat(d)
    except ValueError:
        raise BookingError("bad_date", "That date does not exist.")


def js_weekday(d: Date) -> int:
    return (d.weekday() + 1) % 7  # 0 = Sunday, as the desk stores working hours


def hours_on(staff: dict, d: Date):
    h = (staff.get("hours") or {}).get(str(js_weekday(d)))
    return h if isinstance(h, list) and len(h) == 2 else None


def window(s: dict, now: datetime) -> tuple[Date, Date]:
    first = now.date()
    return first, first + timedelta(days=max(1, int(s.get("bookingDays") or 14)) - 1)


def earliest_start(s: dict, now: datetime) -> datetime:
    return now + timedelta(minutes=int(s.get("leadMin") or 0))


# ------------------------------------------------------------------ availability
def _online_services(c, store) -> dict:
    return {sv["id"]: sv for sv in store.all(c, "services") if sv.get("online", True) is not False}


def _resolve(c, store, service_ids, staff_choice):
    if not isinstance(service_ids, list) or not 1 <= len(service_ids) <= 6 or not all(isinstance(x, str) for x in service_ids):
        raise BookingError("bad_services", "Pick between one and six services.")
    online = _online_services(c, store)
    services = []
    for sid in dict.fromkeys(service_ids):
        if sid not in online:
            raise BookingError("unknown_service", "One of those services can't be booked online.")
        services.append(online[sid])
    staff = sorted(store.all(c, "staff"), key=lambda st: (st.get("order", 99), st.get("name", "")))
    eligible = [st for st in staff if all(sv["id"] in (st.get("services") or []) for sv in services)]
    if staff_choice in (None, "", "any"):
        pool = eligible
    else:
        pool = [st for st in eligible if st["id"] == staff_choice]
        if not pool:
            raise BookingError("staff_unavailable", "That specialist doesn't offer all of these services.")
    return services, eligible, pool


def _busy(appts, ignore_id=None) -> dict:
    busy: dict = {}
    for a in appts:
        if a.get("status") == "cancelled" or a.get("id") == ignore_id:
            continue
        busy.setdefault((a.get("staffId"), a.get("date")), []).append((int(a.get("start", 0)), int(a.get("start", 0)) + int(a.get("duration", 0))))
    return busy


def _free(staff, d: Date, duration, busy, step, min_dt: datetime):
    h = hours_on(staff, d)
    if not h or duration <= 0:
        return []
    if d < min_dt.date():
        return []
    min_start = min_dt.hour * 60 + min_dt.minute if d == min_dt.date() else 0
    taken = busy.get((staff["id"], d.isoformat()), [])
    out = []
    m = int(h[0])
    while m + duration <= int(h[1]):
        if m >= min_start and not any(m < e and m + duration > s for s, e in taken):
            out.append(m)
        m += step
    return out


def availability(c, store, s, now, service_ids, staff_choice, first: str | None = None, days: int = 14, ignore_id: str | None = None, with_next=False) -> dict:
    services, eligible, pool = _resolve(c, store, service_ids, staff_choice)
    duration = sum(int(sv.get("duration") or 0) for sv in services)
    price = sum(float(sv.get("price") or 0) for sv in services)
    w0, w1 = window(s, now)
    start = max(w0, parse_date(first)) if first else w0
    end = min(w1, start + timedelta(days=max(1, min(int(days), 62)) - 1))
    step = max(5, int(s.get("step") or 15))
    min_dt = earliest_start(s, now)
    busy = _busy(store.appointments_between(c, w0.isoformat(), w1.isoformat()), ignore_id)
    out_days = []
    d = start
    while d <= end:
        slots: dict = {}
        for st in pool:
            for m in _free(st, d, duration, busy, step, min_dt):
                slots.setdefault(m, []).append(st["id"])
        state = "open" if slots else ("closed" if not any(hours_on(st, d) for st in pool) else "full")
        out_days.append({"date": d.isoformat(), "state": state, "slots": [{"t": m, "staff": slots[m]} for m in sorted(slots)]})
        d += timedelta(days=1)
    result = {"duration": duration, "price": price, "days": out_days, "staff": [st["id"] for st in eligible]}
    if with_next:
        nxt = {}
        for st in eligible:
            d = w0
            while d <= w1:
                f = _free(st, d, duration, busy, step, min_dt)
                if f:
                    nxt[st["id"]] = {"date": d.isoformat(), "t": f[0]}
                    break
                d += timedelta(days=1)
        result["next"] = nxt
    return result


# ------------------------------------------------------------------ manage links
def _sig(secret: str, appt_id: str) -> str:
    mac = hmac.new(secret.encode(), b"manage:" + appt_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:16]).decode().rstrip("=")


def manage_token(secret: str, appt_id: str) -> str:
    return f"{appt_id}.{_sig(secret, appt_id)}"


def parse_token(secret: str, token: str) -> str:
    if not isinstance(token, str) or len(token) > 120 or "." not in token:
        raise BookingError("bad_link", "This booking link isn't valid.", 404)
    appt_id, sig = token.rsplit(".", 1)
    if not ID_RE.match(appt_id) or not hmac.compare_digest(sig, _sig(secret, appt_id)):
        raise BookingError("bad_link", "This booking link isn't valid.", 404)
    return appt_id


# ------------------------------------------------------------------ views
def _tr(doc: dict, lang: str, field: str):
    return ((doc.get("i18n") or {}).get(lang) or {}).get(field) or doc.get(field)


def public_studio(c, store, s, now) -> dict:
    staff = sorted(store.all(c, "staff"), key=lambda st: (st.get("order", 99), st.get("name", "")))
    services = sorted(_online_services(c, store).values(), key=lambda sv: (sv.get("order", 999), sv.get("name", "")))
    offered = {sid for st in staff for sid in (st.get("services") or [])}
    hours = {}
    for wd in range(7):
        spans = [st["hours"].get(str(wd)) for st in staff if isinstance((st.get("hours") or {}).get(str(wd)), list)]
        hours[str(wd)] = [min(h[0] for h in spans), max(h[1] for h in spans)] if spans else None
    pick = lambda d, keys: {k: d.get(k) for k in keys if d.get(k) is not None}
    return {
        "name": s.get("name"), "tagline": s.get("tagline"), "address": s.get("address"), "phone": s.get("phone"),
        "currency": s.get("currency"), "countryCode": s.get("countryCode"), "bookingDays": s.get("bookingDays"),
        "cancelHours": s.get("cancelHours"), "defaultLang": s.get("defaultLang") if s.get("defaultLang") in LANGS else "hy",
        "i18n": s.get("i18n") or {}, "sample": bool(s.get("sample")),
        "today": now.date().isoformat(), "nowMin": now.hour * 60 + now.minute, "hours": hours,
        "services": [pick(sv, ("id", "name", "category", "description", "duration", "price", "i18n")) for sv in services if sv["id"] in offered],
        "staff": [pick(st, ("id", "name", "role", "bio", "color", "services", "i18n")) for st in staff],
    }


def booking_view(c, store, s, now, appt: dict, token: str) -> dict:
    svc = {sv["id"]: sv for sv in store.all(c, "services")}
    st = store.get(c, "staff", appt.get("staffId")) or {}
    start_dt = datetime.combine(Date.fromisoformat(appt["date"]), datetime.min.time(), tzinfo=now.tzinfo) + timedelta(minutes=int(appt["start"]))
    end_dt = start_dt + timedelta(minutes=int(appt.get("duration") or 0))
    live = appt.get("status") in LIVE and end_dt > now
    can_change = live and (start_dt - now) >= timedelta(hours=float(s.get("cancelHours") or 0))
    return {
        "token": token, "date": appt["date"], "start": appt["start"], "duration": appt.get("duration"),
        "price": appt.get("price"), "status": appt.get("status"), "cancelledBy": appt.get("cancelledBy"),
        "name": appt.get("clientName"), "lang": appt.get("lang"),
        "services": [{"id": i, "name": (svc.get(i) or {}).get("name", ""), "i18n": (svc.get(i) or {}).get("i18n") or {}} for i in appt.get("serviceIds") or []],
        "staff": {"id": st.get("id"), "name": st.get("name", ""), "i18n": st.get("i18n") or {}},
        "live": live, "canChange": can_change, "cancelHours": s.get("cancelHours"),
    }


# ------------------------------------------------------------------ writes from the public site
def _digits(p) -> str:
    return re.sub(r"\D", "", str(p or ""))


def same_phone(a, b) -> bool:
    x, y = _digits(a), _digits(b)
    return len(x) >= 8 and len(y) >= 8 and x[-8:] == y[-8:]


def _text(v, limit, field, required=False) -> str:
    v = (v if isinstance(v, str) else "").strip()
    if required and not v:
        raise BookingError(f"missing_{field}", f"Enter your {field}.")
    if len(v) > limit:
        raise BookingError(f"long_{field}", f"That {field} is too long.")
    return v


def _utc_iso(now: datetime) -> str:
    return now.astimezone(ZoneInfo("UTC")).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_booking(store, secret, s_fn, now_fn, payload: dict) -> dict:
    name = _text(payload.get("name"), 80, "name", required=True)
    phone = _text(payload.get("phone"), 30, "phone", required=True)
    if not 8 <= len(_digits(phone)) <= 15:
        raise BookingError("bad_phone", "Enter a full phone number, like +374 91 234 567.")
    note = _text(payload.get("note"), 500, "note")
    lang = payload.get("lang") if payload.get("lang") in LANGS else "hy"
    start = payload.get("start")
    if not isinstance(start, int) or not 0 <= start < 1440:
        raise BookingError("bad_time", "Pick a time from the list.")
    date = payload.get("date")
    parse_date(date)
    with store.tx() as c:
        s = s_fn(c)
        now = now_fn(s)
        av = availability(c, store, s, now, payload.get("services"), payload.get("staff"), date, 1)
        day = av["days"][0] if av["days"] and av["days"][0]["date"] == date else None
        slot = next((x for x in (day or {}).get("slots", []) if x["t"] == start), None)
        if not slot:
            raise BookingError("slot_taken", "That time is no longer free. Pick another one.", 409)
        clients = store.all(c, "clients")
        client = next((cl for cl in clients if same_phone(cl.get("phone"), phone)), None)
        if client:
            today = now.date().isoformat()
            upcoming = [a for a in store.appointments_between(c, today, "9999-12-31")
                        if a.get("clientId") == client["id"] and a.get("status") in LIVE and a.get("source") == "online"]
            if len(upcoming) >= MAX_UPCOMING_PER_PHONE:
                raise BookingError("too_many", "This number already has several upcoming visits. Call the studio to book more.", 429)
        else:
            client = {"id": "c_" + secrets.token_hex(6), "name": name, "phone": phone, "email": "", "notes": "",
                      "createdAt": _utc_iso(now), "source": "online", "lang": lang}
            store.put(c, "clients", client["id"], {k: v for k, v in client.items() if k != "id"})
        # "Any specialist": give it to whoever has the least booked time that day.
        day_appts = store.appointments_on(c, date)
        load = lambda sid: sum(int(a.get("duration") or 0) for a in day_appts if a.get("staffId") == sid and a.get("status") != "cancelled")
        order = {sid: i for i, sid in enumerate(av["staff"])}
        staff_id = sorted(slot["staff"], key=lambda sid: (load(sid), order.get(sid, 99)))[0]
        appt_id = "a_" + secrets.token_hex(8)
        appt = {
            "date": date, "start": start, "duration": av["duration"], "staffId": staff_id,
            "serviceIds": list(dict.fromkeys(payload["services"])), "clientId": client["id"], "clientName": name,
            "clientPhone": phone, "price": av["price"], "status": "booked", "paid": 0, "payMethod": None,
            "source": "online", "note": note, "lang": lang, "createdAt": _utc_iso(now),
        }
        store.put(c, "appointments", appt_id, appt)
        appt["id"] = appt_id
        token = manage_token(secret, appt_id)
        return booking_view(c, store, s, now, appt, token)


def _load_for_change(c, store, s, now, secret, token):
    appt_id = parse_token(secret, token)
    appt = store.get(c, "appointments", appt_id)
    if not appt:
        raise BookingError("bad_link", "This booking no longer exists.", 404)
    view = booking_view(c, store, s, now, appt, token)
    if not view["live"]:
        raise BookingError("not_live", "This booking can't be changed any more.", 409)
    if not view["canChange"]:
        raise BookingError("too_late", f"It's less than {s.get('cancelHours')} hours to your visit. Call the studio to change it.", 409)
    return appt


def cancel_booking(store, secret, s_fn, now_fn, token) -> dict:
    with store.tx() as c:
        s = s_fn(c)
        now = now_fn(s)
        appt = _load_for_change(c, store, s, now, secret, token)
        stamp = _utc_iso(now)
        appt.update(status="cancelled", cancelledAt=stamp, cancelledBy="client", clientAction="cancelled", clientActionAt=stamp)
        store.put(c, "appointments", appt["id"], {k: v for k, v in appt.items() if k != "id"})
        return booking_view(c, store, s, now, appt, token)


def reschedule_booking(store, secret, s_fn, now_fn, token, payload) -> dict:
    date, start = payload.get("date"), payload.get("start")
    parse_date(date)
    if not isinstance(start, int) or not 0 <= start < 1440:
        raise BookingError("bad_time", "Pick a time from the list.")
    with store.tx() as c:
        s = s_fn(c)
        now = now_fn(s)
        appt = _load_for_change(c, store, s, now, secret, token)
        av = availability(c, store, s, now, appt.get("serviceIds"), appt.get("staffId"), date, 1, ignore_id=appt["id"])
        day = av["days"][0] if av["days"] and av["days"][0]["date"] == date else None
        if not any(x["t"] == start for x in (day or {}).get("slots", [])):
            raise BookingError("slot_taken", "That time is no longer free. Pick another one.", 409)
        stamp = _utc_iso(now)
        appt.update(movedFrom=f"{appt['date']} {appt['start']}", date=date, start=start, status="booked",
                    remindedAt=None, remindedFor=None, clientAction="rescheduled", clientActionAt=stamp)
        store.put(c, "appointments", appt["id"], {k: v for k, v in appt.items() if k != "id"})
        return booking_view(c, store, s, now, appt, token)


# ------------------------------------------------------------------ desk writes
S, I, N, B, L, O, SO = "str", "int", "num", "bool", "list", "obj", "str_or_obj"
SCHEMAS = {
    "settings": {"name": S, "tagline": S, "address": S, "phone": S, "currency": S, "countryCode": S, "open": I, "close": I,
                 "step": I, "bookingDays": I, "leadMin": I, "cancelHours": N, "timezone": S, "defaultLang": S, "reminder": SO,
                 "i18n": O, "sample": B},
    "staff": {"name": S, "role": S, "bio": S, "color": I, "services": L, "hours": O, "order": N, "i18n": O},
    "services": {"name": S, "category": S, "description": S, "duration": I, "price": N, "online": B, "order": N, "i18n": O},
    "clients": {"name": S, "phone": S, "email": S, "notes": S, "createdAt": S, "source": S, "lang": S},
    "appointments": {"date": S, "start": I, "duration": I, "staffId": S, "serviceIds": L, "clientId": S, "clientName": S,
                     "clientPhone": S, "price": N, "status": S, "paid": N, "payMethod": S, "paidAt": S, "source": S, "note": S,
                     "createdAt": S, "remindedAt": S, "remindedFor": S, "remindedVia": S, "cancelledAt": S, "cancelledBy": S,
                     "movedFrom": S, "clientAction": S, "clientActionAt": S, "lang": S},
}


def _check_obj(v, depth=0):
    if depth > 4:
        raise BookingError("bad_field", "That record is nested too deeply.")
    if isinstance(v, dict):
        for k, x in v.items():
            if not isinstance(k, str) or len(k) > 40:
                raise BookingError("bad_field", "That record has an invalid key.")
            _check_obj(x, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _check_obj(x, depth + 1)
    elif isinstance(v, str) and len(v) > 4000:
        raise BookingError("bad_field", "A text in that record is too long.")


def clean_doc(kind: str, body) -> dict:
    """Keep the known fields of a desk write, with the right types. Unknown fields are dropped."""
    if kind not in SCHEMAS:
        raise BookingError("bad_kind", "Unknown kind of record.", 404)
    if not isinstance(body, dict):
        raise BookingError("bad_body", "Send the record as a JSON object.")
    out = {}
    for field, typ in SCHEMAS[kind].items():
        if field not in body:
            continue
        v = body[field]
        if v is None:
            out[field] = None
            continue
        ok = {
            S: isinstance(v, str) and len(v) <= 4000,
            I: isinstance(v, int) and not isinstance(v, bool),
            N: isinstance(v, (int, float)) and not isinstance(v, bool),
            B: isinstance(v, bool),
            L: isinstance(v, list) and len(v) <= 200 and all(isinstance(x, str) and len(x) <= 64 for x in v),
            O: isinstance(v, dict),
            SO: isinstance(v, (str, dict)),
        }[typ]
        if not ok:
            raise BookingError("bad_field", f"'{field}' has the wrong type.")
        if typ in (O, SO):
            _check_obj(v)
        out[field] = v
    if kind == "appointments":
        parse_date(out.get("date"))
        if not isinstance(out.get("start"), int) or not 0 <= out["start"] < 1440:
            raise BookingError("bad_field", "'start' must be minutes after midnight.")
        if not isinstance(out.get("duration"), int) or not 5 <= out["duration"] <= 720:
            raise BookingError("bad_field", "'duration' must be between 5 and 720 minutes.")
        if out.get("status", "booked") not in STATUSES:
            raise BookingError("bad_field", "Unknown status.")
        if not out.get("staffId"):
            raise BookingError("bad_field", "An appointment needs a specialist.")
    if kind == "staff" and "hours" in out and out["hours"] is not None:
        for k, h in out["hours"].items():
            if k not in {str(i) for i in range(7)} or not (h is None or (isinstance(h, list) and len(h) == 2 and all(isinstance(x, int) and 0 <= x <= 1440 for x in h) and h[0] < h[1])):
                raise BookingError("bad_field", "Working hours must be [start, end] minutes for days 0 to 6.")
    if kind in ("staff", "services", "clients") and not (isinstance(out.get("name"), str) and out["name"].strip()):
        raise BookingError("bad_field", "A name is required.")
    return out
