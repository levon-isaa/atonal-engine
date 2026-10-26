"""A sample studio for trying Rota out: Atelier Nairi, four specialists, a month of history and a
week of bookings ahead, dated around today. Clearly marked as sample data in both apps."""
from __future__ import annotations

import random
from datetime import date as Date, datetime, timedelta, timezone

SETTINGS = {
    "name": "Atelier Nairi", "tagline": "Hair, nails and brows on Abovyan Street", "address": "14 Abovyan St, Yerevan",
    "phone": "+374 10 000 000", "currency": "֏", "countryCode": "374", "open": 600, "close": 1200, "step": 15,
    "bookingDays": 14, "leadMin": 30, "cancelHours": 3, "timezone": "Asia/Yerevan", "defaultLang": "hy", "sample": True,
    "i18n": {
        "hy": {"name": "Ատելիե Նաիրի", "tagline": "Մազեր, եղունգներ և հոնքեր Աբովյան փողոցում", "address": "Երևան, Աբովյան 14"},
        "ru": {"name": "Ателье Наири", "tagline": "Волосы, ногти и брови на улице Абовяна", "address": "Ереван, ул. Абовяна 14"},
    },
}

CATS = {"Hair": ("Մազեր", "Волосы"), "Barber": ("Բարբեր", "Барбер"), "Nails": ("Եղունգներ", "Ногти"),
        "Brows & lashes": ("Հոնքեր և թարթիչներ", "Брови и ресницы"), "Skin": ("Մաշկ", "Уход за кожей")}

# id, category, minutes, price, (en name, en description), (hy...), (ru...)
SERVICES = [
    ("sv-wcut", "Hair", 60, 12000, ("Women's haircut", "Consultation, wash, cut and a simple blow-dry."),
     ("Կանացի սանրվածք", "Խորհրդատվություն, լվացում, սանրվածք և պարզ հարդարում։"),
     ("Женская стрижка", "Консультация, мытьё, стрижка и простая укладка.")),
    ("sv-blow", "Hair", 45, 8000, ("Blow-dry & styling", "Wash and a smooth or bouncy blow-dry for any length."),
     ("Հարդարում ֆենով", "Լվացում և հարթ կամ ծավալուն հարդարում ցանկացած երկարության համար։"),
     ("Укладка феном", "Мытьё и гладкая или объёмная укладка на любую длину.")),
    ("sv-root", "Hair", 90, 22000, ("Root color", "Color on up to 3 cm of regrowth, matched to your lengths."),
     ("Արմատների ներկում", "Մինչև 3 սմ աճած հատվածի ներկում՝ համապատասխանեցված երկարությանը։"),
     ("Окрашивание корней", "Окрашивание отросших корней до 3 см в тон длины.")),
    ("sv-color", "Hair", 120, 32000, ("Full color", "All-over single-process color with a gloss to finish."),
     ("Ամբողջական ներկում", "Միագույն ներկում ամբողջ երկարությամբ և փայլեցում։"),
     ("Полное окрашивание", "Однотонное окрашивание по всей длине с глянцеванием.")),
    ("sv-mcut", "Barber", 45, 7000, ("Men's haircut", "Clipper or scissor cut, washed and styled."),
     ("Տղամարդու սանրվածք", "Մեքենայով կամ մկրատով սանրվածք, լվացում և հարդարում։"),
     ("Мужская стрижка", "Стрижка машинкой или ножницами, мытьё и укладка.")),
    ("sv-beard", "Barber", 30, 4000, ("Beard trim", "Shape, line-up and a hot towel."),
     ("Մորուքի խնամք", "Ձևավորում, եզրագծում և տաք սրբիչ։"),
     ("Оформление бороды", "Форма, окантовка и горячее полотенце.")),
    ("sv-combo", "Barber", 75, 10000, ("Haircut & beard", "A full haircut and beard trim in one visit."),
     ("Սանրվածք և մորուք", "Սանրվածք և մորուքի խնամք մեկ այցով։"),
     ("Стрижка и борода", "Стрижка и оформление бороды за один визит.")),
    ("sv-mani", "Nails", 45, 7000, ("Manicure", "Shape, cuticle care and a classic polish."),
     ("Մատնահարդարում", "Ձևավորում, կուտիկուլայի խնամք և դասական լաք։"),
     ("Маникюр", "Форма, уход за кутикулой и классическое покрытие.")),
    ("sv-gel", "Nails", 75, 11000, ("Gel manicure", "Long-wear gel color that lasts about three weeks."),
     ("Գել-լաքով մատնահարդարում", "Երկարատև գել ծածկույթ, որը պահպանվում է մոտ երեք շաբաթ։"),
     ("Маникюр с гель-лаком", "Стойкое гелевое покрытие, держится около трёх недель.")),
    ("sv-pedi", "Nails", 60, 12000, ("Pedicure", "Soak, file, cuticle and callus care, finished with polish."),
     ("Ոտնահարդարում", "Թրջում, ձևավորում, կուտիկուլայի և կոշտուկների խնամք, լաք։"),
     ("Педикюр", "Ванночка, опил, уход за кутикулой и огрубевшей кожей, покрытие.")),
    ("sv-brow", "Brows & lashes", 30, 5000, ("Brow shaping", "Wax and tweeze to a shape that suits your face."),
     ("Հոնքերի ձևավորում", "Մոմով և ունելիով ձևավորում՝ ըստ դեմքի ձևի։"),
     ("Коррекция бровей", "Воск и пинцет, форма под ваше лицо.")),
    ("sv-tint", "Brows & lashes", 45, 8000, ("Brow shape & tint", "Shaping plus a tint for fuller-looking brows."),
     ("Հոնքերի ձևավորում և ներկում", "Ձևավորում և ներկում՝ ավելի խիտ տեսքի համար։"),
     ("Коррекция и окрашивание бровей", "Коррекция и окрашивание для более густого вида.")),
    ("sv-lash", "Brows & lashes", 60, 14000, ("Lash lift", "Lifts and curls your natural lashes for six to eight weeks."),
     ("Թարթիչների լիֆթինգ", "Բնական թարթիչների բարձրացում և ոլորում 6–8 շաբաթով։"),
     ("Ламинирование ресниц", "Подъём и изгиб натуральных ресниц на 6–8 недель.")),
    ("sv-facial", "Skin", 45, 15000, ("Express facial", "Cleanse, exfoliate and mask for a quick glow."),
     ("Էքսպրես դեմքի խնամք", "Մաքրում, պիլինգ և դիմակ՝ արագ թարմության համար։"),
     ("Экспресс-уход за лицом", "Очищение, пилинг и маска для быстрого сияния.")),
]


def _hours(days, start, end, extra=None):
    h = {str(d): ([start, end] if d in days else None) for d in range(7)}
    h.update(extra or {})
    return h


# id, (en name, hy name, ru name), (en role, hy, ru), (en bio, hy, ru), color, services, hours, weights
STAFF = [
    ("st-mariam", ("Mariam Hakobyan", "Մարիամ Հակոբյան", "Мариам Акопян"), ("Senior stylist", "Ավագ ստիլիստ", "Старший стилист"),
     ("Twelve years behind the chair. Known for soft, lived-in color.", "Տասներկու տարվա փորձ։ Հայտնի է բնական, մեղմ երանգներով ներկումներով։", "Двенадцать лет за креслом. Известна мягким, естественным окрашиванием."),
     0, ["sv-wcut", "sv-blow", "sv-root", "sv-color"], _hours({2, 3, 4, 5, 6}, 600, 1140), [("sv-wcut", 5), ("sv-blow", 3), ("sv-root", 3), ("sv-color", 2)]),
    ("st-davit", ("Davit Arakelyan", "Դավիթ Առաքելյան", "Давид Аракелян"), ("Barber", "Բարբեր", "Барбер"),
     ("Classic cuts, skin fades and straight-razor beard work.", "Դասական սանրվածքներ, ֆեյդեր և մորուքի խնամք ածելիով։", "Классические стрижки, фейды и работа с бородой опасной бритвой."),
     2, ["sv-mcut", "sv-beard", "sv-combo"], _hours({1, 2, 3, 4, 5, 6}, 660, 1200), [("sv-mcut", 5), ("sv-beard", 2), ("sv-combo", 4)]),
    ("st-lilit", ("Lilit Asatryan", "Լիլիթ Ասատրյան", "Лилит Асатрян"), ("Nail artist", "Մատնահարդար", "Мастер маникюра"),
     ("Clean, long-lasting gel work and detailed nail art.", "Կոկիկ, երկարատև գել ծածկույթներ և նրբագեղ դիզայն։", "Аккуратное стойкое покрытие и детальный нейл-арт."),
     1, ["sv-mani", "sv-gel", "sv-pedi"], _hours({1, 2, 3, 4, 5}, 600, 1080, {"0": [660, 1020]}), [("sv-mani", 3), ("sv-gel", 5), ("sv-pedi", 3)]),
    ("st-elina", ("Elina Movsisyan", "Էլինա Մովսիսյան", "Элина Мовсисян"), ("Brow & skin specialist", "Հոնքերի և մաշկի մասնագետ", "Специалист по бровям и коже"),
     ("Brows, lashes and facials. Takes time over first-visit consultations.", "Հոնքեր, թարթիչներ և դեմքի խնամք։ Առաջին այցին ժամանակ է հատկացնում խորհրդատվությանը։", "Брови, ресницы и уход за лицом. Уделяет время консультации при первом визите."),
     4, ["sv-brow", "sv-tint", "sv-lash", "sv-facial", "sv-mani"], _hours({0, 3, 4, 5, 6}, 600, 1140), [("sv-brow", 4), ("sv-tint", 4), ("sv-lash", 3), ("sv-facial", 3), ("sv-mani", 1)]),
]
COMBOS = {"sv-root": "sv-blow", "sv-tint": "sv-lash", "sv-gel": "sv-pedi", "sv-wcut": "sv-blow"}

WOMEN = ["Ani", "Mane", "Nare", "Lusine", "Gayane", "Anahit", "Tatevik", "Armine", "Sona", "Meri", "Elen", "Hasmik",
         "Milena", "Kristine", "Narine", "Astghik", "Liana", "Syuzanna", "Marine", "Inga", "Arpi"]
MEN = ["Aram", "Tigran", "Narek", "Hayk", "Gor", "Vahe", "Artur", "Karen", "Sevak", "Arsen", "Ruben", "Edgar"]
SURN = ["Petrosyan", "Harutyunyan", "Sargsyan", "Hovhannisyan", "Khachatryan", "Vardanyan", "Gevorgyan", "Karapetyan",
        "Mkrtchyan", "Ghazaryan", "Manukyan", "Avagyan", "Martirosyan", "Danielyan", "Poghosyan", "Baghdasaryan",
        "Abrahamyan", "Stepanyan", "Melkonyan", "Simonyan", "Aslanyan", "Galstyan", "Tadevosyan", "Mirzoyan"]
NOTES = ["Prefers Mariam. Ammonia-free color only, sensitive scalp.", "Usually runs ten minutes late, book with a buffer.",
         "Allergic to acetone remover. Use the gentle one.", "Gel: nude shades. Removes at home, check nail damage."]


def build(today: Date, now_min: int, seed: int = 2610) -> dict:
    """Return {kind: {id: body}} for the sample studio."""
    rnd = random.Random(seed)
    docs = {"settings": {"studio": dict(SETTINGS)}, "services": {}, "staff": {}, "clients": {}, "appointments": {}}
    for i, (sid, cat, dur, price, en, hy, ru) in enumerate(SERVICES):
        docs["services"][sid] = {"name": en[0], "category": cat, "description": en[1], "duration": dur, "price": price,
                                 "online": True, "order": i + 1,
                                 "i18n": {"hy": {"name": hy[0], "description": hy[1], "category": CATS[cat][0]},
                                          "ru": {"name": ru[0], "description": ru[1], "category": CATS[cat][1]}}}
    for i, (sid, names, roles, bios, color, svcs, hours, _w) in enumerate(STAFF):
        docs["staff"][sid] = {"name": names[0], "role": roles[0], "bio": bios[0], "color": color, "services": svcs,
                              "hours": hours, "order": i + 1,
                              "i18n": {"hy": {"name": names[1], "role": roles[1], "bio": bios[1]},
                                       "ru": {"name": names[2], "role": roles[2], "bio": bios[2]}}}
    used, clients = set(), {"w": [], "m": []}
    for pool, key, n in ((WOMEN, "w", 24), (MEN, "m", 12)):
        for i in range(n):
            while True:
                fn, sn = rnd.choice(pool), rnd.choice(SURN)
                if (fn, sn) not in used:
                    used.add((fn, sn))
                    break
            cid = f"c{key}{i + 1:02d}"
            phone = f"+374 {rnd.choice(['91', '93', '94', '95', '96', '98', '99', '77', '55'])} 00{rnd.randint(0, 9)} {rnd.randint(100, 999)}"
            created = datetime.combine(today - timedelta(days=rnd.randint(40, 400)), datetime.min.time(), tzinfo=timezone.utc)
            docs["clients"][cid] = {"name": f"{fn} {sn}", "phone": phone, "email": "", "notes": NOTES[i] if key == "w" and i < len(NOTES) else "",
                                    "createdAt": created.strftime("%Y-%m-%dT%H:%M:%SZ"), "source": rnd.choice(["desk", "online", "phone"]),
                                    "lang": rnd.choice(["hy", "hy", "ru", "en"])}
            clients[key].append(cid)

    def pick(key):
        ids = clients[key]
        return rnd.choices(ids, weights=[3 if i < len(ids) // 3 else 1 for i in range(len(ids))])[0]

    seq = 0
    svc = {s[0]: s for s in SERVICES}
    for offset in range(-27, 8):
        day = today + timedelta(days=offset)
        wd = str((day.weekday() + 1) % 7)
        for sid, names, roles, bios, color, svcs, hours, weights in STAFF:
            h = hours.get(wd)
            if not h:
                continue
            p = 0.24 if offset < 0 else 0.78 if offset == 0 else max(0.2, 0.6 - 0.07 * offset)
            t = h[0] + rnd.choice([0, 0, 15, 30])
            while True:
                main = rnd.choices([w[0] for w in weights], weights=[w[1] for w in weights])[0]
                ids = [main]
                if main in COMBOS and COMBOS[main] in svcs and rnd.random() < 0.18:
                    ids.append(COMBOS[main])
                dur = sum(svc[x][2] for x in ids)
                if t + dur > h[1]:
                    if len(ids) > 1 and t + svc[main][2] <= h[1]:
                        ids, dur = [main], svc[main][2]
                    else:
                        break
                if rnd.random() < p:
                    cid = pick("m" if sid == "st-davit" else "w")
                    cl = docs["clients"][cid]
                    price = sum(svc[x][3] for x in ids)
                    end = t + dur
                    r = rnd.random()
                    status, paid, method = "booked", 0, None
                    if offset < 0:
                        status, paid, method = ("cancelled", 0, None) if r < 0.06 else ("noshow", 0, None) if r < 0.12 else ("arrived", price, "card" if rnd.random() < 0.66 else "cash")
                    elif offset == 0:
                        if end <= now_min:
                            status, paid, method = "arrived", price, "card" if rnd.random() < 0.66 else "cash"
                        elif t <= now_min:
                            status = "arrived"
                        else:
                            status = "confirmed" if r < 0.55 else "booked"
                    else:
                        status = "cancelled" if r < 0.04 else ("confirmed" if offset <= 2 and r < 0.45 else "booked")
                    src = rnd.choices(["online", "desk", "phone"], weights=[45, 30, 25])[0]
                    created = datetime.combine(day - timedelta(days=rnd.randint(1, 9)), datetime.min.time(), tzinfo=timezone.utc) + timedelta(minutes=rnd.randint(540, 1260))
                    seq += 1
                    docs["appointments"][f"a{seq:03d}"] = {
                        "date": day.isoformat(), "start": t, "duration": dur, "staffId": sid, "serviceIds": ids,
                        "clientId": cid, "clientName": cl["name"], "clientPhone": cl["phone"], "price": price,
                        "status": status, "paid": paid, "payMethod": method, "source": src, "note": "",
                        "lang": cl["lang"], "createdAt": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                    t = end + rnd.choice([0, 0, 0, 15, 15, 30])
                else:
                    t += rnd.choice([30, 45, 60])
    return docs
