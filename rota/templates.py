"""Starter services for a new studio, by kind of business, so its booking page works the minute
the owner signs up. Prices are rough Yerevan figures in dram, meant to be edited in the desk."""
from __future__ import annotations

# kind: (category (en, hy, ru), specialist's role (en, hy, ru), [(key, minutes, price, en, hy, ru), ...])
KINDS = {
    "hair": (
        ("Hair", "Մազեր", "Волосы"),
        ("Hair stylist", "Վարսահարդար", "Парикмахер"),
        [
            ("wcut", 60, 8000, "Women's haircut", "Կանացի սանրվածք", "Женская стрижка"),
            ("mcut", 45, 5000, "Men's haircut", "Տղամարդու սանրվածք", "Мужская стрижка"),
            ("blow", 45, 6000, "Blow-dry & styling", "Հարդարում ֆենով", "Укладка феном"),
            ("root", 90, 15000, "Root color", "Արմատների ներկում", "Окрашивание корней"),
            ("color", 120, 25000, "Full color", "Ամբողջական ներկում", "Полное окрашивание"),
            ("updo", 60, 12000, "Evening hairstyle", "Երեկոյան սանրվածք", "Вечерняя причёска"),
        ],
    ),
    "barber": (
        ("Barber", "Բարբեր", "Барбер"),
        ("Barber", "Բարբեր", "Барбер"),
        [
            ("mcut", 45, 5000, "Men's haircut", "Տղամարդու սանրվածք", "Мужская стрижка"),
            ("beard", 30, 3000, "Beard trim", "Մորուքի խնամք", "Оформление бороды"),
            ("combo", 75, 7000, "Haircut & beard", "Սանրվածք և մորուք", "Стрижка и борода"),
            ("kids", 30, 3500, "Kids' haircut", "Մանկական սանրվածք", "Детская стрижка"),
            ("shave", 30, 4000, "Head shave", "Գլխի սափրում", "Бритьё головы"),
        ],
    ),
    "nails": (
        ("Nails", "Եղունգներ", "Ногти"),
        ("Nail artist", "Մատնահարդար", "Мастер маникюра"),
        [
            ("mani", 45, 5000, "Manicure", "Մատնահարդարում", "Маникюр"),
            ("gel", 75, 8000, "Gel manicure", "Գել-լաքով մատնահարդարում", "Маникюр с гель-лаком"),
            ("off", 20, 2000, "Gel removal", "Գել-լաքի հեռացում", "Снятие гель-лака"),
            ("pedi", 60, 9000, "Pedicure", "Ոտնահարդարում", "Педикюр"),
            ("gelpedi", 90, 12000, "Gel pedicure", "Ոտնահարդարում գել-լաքով", "Педикюр с гель-лаком"),
            ("ext", 120, 15000, "Nail extensions", "Եղունգների երկարացում", "Наращивание ногтей"),
        ],
    ),
    "brows": (
        ("Brows & lashes", "Հոնքեր և թարթիչներ", "Брови и ресницы"),
        ("Brow & lash artist", "Հոնքերի և թարթիչների վարպետ", "Мастер бровей и ресниц"),
        [
            ("brow", 30, 4000, "Brow shaping", "Հոնքերի ձևավորում", "Коррекция бровей"),
            ("tint", 45, 6000, "Brow shape & tint", "Հոնքերի ձևավորում և ներկում", "Коррекция и окрашивание бровей"),
            ("lami", 60, 10000, "Brow lamination", "Հոնքերի լամինացիա", "Ламинирование бровей"),
            ("lift", 60, 12000, "Lash lift", "Թարթիչների լիֆթինգ", "Ламинирование ресниц"),
            ("lashes", 120, 15000, "Classic lash extensions", "Թարթիչների դասական երկարացում", "Классическое наращивание ресниц"),
        ],
    ),
    "skin": (
        ("Skin & beauty", "Կոսմետոլոգիա", "Косметология"),
        ("Beautician", "Կոսմետոլոգ", "Косметолог"),
        [
            ("clean", 60, 12000, "Facial cleansing", "Դեմքի մաքրում", "Чистка лица"),
            ("peel", 45, 10000, "Peel", "Պիլինգ", "Пилинг"),
            ("facemsg", 30, 7000, "Face massage", "Դեմքի մերսում", "Массаж лица"),
            ("wax", 45, 8000, "Leg waxing", "Ոտքերի դեպիլյացիա", "Депиляция ног"),
            ("makeup", 60, 12000, "Makeup", "Դիմահարդարում", "Макияж"),
        ],
    ),
    "massage": (
        ("Massage", "Մերսում", "Массаж"),
        ("Massage therapist", "Մերսող", "Массажист"),
        [
            ("classic", 60, 12000, "Classic massage", "Դասական մերսում", "Классический массаж"),
            ("back", 30, 7000, "Back massage", "Մեջքի մերսում", "Массаж спины"),
            ("relax", 90, 16000, "Relaxing massage", "Հանգստացնող մերսում", "Расслабляющий массаж"),
            ("anticell", 60, 14000, "Anti-cellulite massage", "Հակացելյուլիտային մերսում", "Антицеллюлитный массаж"),
        ],
    ),
    "other": (
        ("Services", "Ծառայություններ", "Услуги"),
        ("Specialist", "Մասնագետ", "Специалист"),
        [
            ("consult", 30, 5000, "Consultation", "Խորհրդատվություն", "Консультация"),
            ("visit", 60, 10000, "Standard visit", "Ստանդարտ այց", "Стандартный визит"),
        ],
    ),
}

# Monday to Saturday, 10:00 to 19:00; Sunday off. Days are 0 = Sunday, as the desk stores them.
DEFAULT_HOURS = {"0": None, **{str(d): [600, 1140] for d in range(1, 7)}}


def starter(kind: str, owner_name: str) -> dict:
    """Services and one specialist (the owner) for a new studio, as {kind: {id: body}}."""
    cat, role, items = KINDS.get(kind) or KINDS["other"]
    services = {}
    for i, (key, minutes, price, en, hy, ru) in enumerate(items):
        services[f"sv-{key}"] = {
            "name": en, "category": cat[0], "description": "", "duration": minutes, "price": price, "online": True,
            "order": i, "i18n": {"hy": {"name": hy, "category": cat[1]}, "ru": {"name": ru, "category": cat[2]}},
        }
    staff = {"st-owner": {
        "name": owner_name, "role": role[0], "bio": "", "color": 0, "services": list(services), "hours": dict(DEFAULT_HOURS),
        "order": 0, "i18n": {"hy": {"role": role[1]}, "ru": {"role": role[2]}},
    }}
    return {"services": services, "staff": staff}
