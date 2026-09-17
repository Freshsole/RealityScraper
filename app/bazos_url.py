from __future__ import annotations

from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from app import localities

BASE = "https://reality.bazos.cz"
PAGE_SIZE = 20

OFFERS = [
    ("pronajem", "Pronájem"),
    ("prodej", "Prodej"),
]
OFFER_PATH = {"pronajem": "pronajmu", "prodej": "prodam"}
PATH_OFFER = {value: key for key, value in OFFER_PATH.items()}
# Sreality-style /pronajem/byty/ and /prodej/domy/ 404 into a mixed catalog on Bazoš.
OFFER_TOKENS = frozenset(OFFER_PATH) | frozenset(PATH_OFFER)

CATEGORIES = [
    ("byt", "Byty"),
    ("dum", "Domy"),
    ("pozemek", "Pozemky"),
    ("projekty", "Nové projekty"),
    ("garaz", "Garáže"),
    ("kancelar", "Kanceláře"),
    ("prostory", "Obchodní prostory"),
    ("restaurace", "Hotely a restaurace"),
    ("chata", "Chalupy a chaty"),
    ("sklad", "Sklady"),
    ("zahrada", "Zahrady"),
    ("podnajem", "Podnájem"),
    ("ostatni", "Ostatní"),
]
CATEGORY_ALIASES = {
    "byty": "byt",
    "domy": "dum",
    "pozemky": "pozemek",
    "komercni": "kancelar",
    "komercni-nemovitosti": "kancelar",
    "male-objekty-garaze": "garaz",
    "garaze": "garaz",
}
SIZES = [
    ("1+kk", "1+kk"),
    ("1+1", "1+1"),
    ("2+kk", "2+kk"),
    ("2+1", "2+1"),
    ("3+kk", "3+kk"),
    ("3+1", "3+1"),
    ("4+kk", "4+kk"),
    ("4+1", "4+1"),
    ("5+kk", "5+kk"),
    ("5+1", "5+1"),
    ("6-a-vice", "6 a více"),
    ("pokoj", "Pokoj"),
    ("atypicky", "Atypický"),
]
SIZE_QUERY = {
    "1+kk": "1+kk",
    "1-kk": "1+kk",
    "1+1": "1+1",
    "1-1": "1+1",
    "2+kk": "2+kk",
    "2-kk": "2+kk",
    "2+1": "2+1",
    "2-1": "2+1",
    "3+kk": "3+kk",
    "3-kk": "3+kk",
    "3+1": "3+1",
    "3-1": "3+1",
    "4+kk": "4+kk",
    "4-kk": "4+kk",
    "4+1": "4+1",
    "4-1": "4+1",
    "5+kk": "5+kk",
    "5-kk": "5+kk",
    "5+1": "5+1",
    "5-1": "5+1",
    "6-a-vice": "6+kk",
    "6-kk-a-vetsi": "6+kk",
    "pokoj": "pokoj",
    "atypicky": "atypicky",
    "atypicke": "atypicky",
}
SIZE_KEYS = [key for key, _ in SIZES]
CAT_KEYS = {key for key, _ in CATEGORIES}


def catalog() -> dict:
    return {
        "offers": OFFERS,
        "categories": CATEGORIES,
        "sizes": SIZES,
        "ownership": [],
        "conditions": [],
        "buildings": [],
        "equipped": [],
        "extras": [],
        "ages": [],
        "flags": [],
        "energy": [],
        "pois": [],
        "single_keys": ["category"],
    }


def default_filters() -> dict:
    return {
        "source": "bazos",
        "offers": ["pronajem"],
        "category": "byt",
        "sizes": [],
        "districts": ["praha"],
        "ownership": [],
        "conditions": [],
        "buildings": [],
        "equipped": [],
        "extras": [],
        "flags": [],
        "price_from": None,
        "price_to": None,
        "area_from": None,
        "area_to": None,
        "radius": 20,
        "hledat": "",
        "sort": "nejnovejsi",
    }


def sample_filters() -> dict:
    data = default_filters()
    data["sizes"] = ["2+kk"]
    data["price_from"] = 10000
    data["price_to"] = 35000
    return data


def is_bazos(url: str) -> bool:
    return "bazos" in (url or "").lower()


def _category(raw) -> str:
    value = raw[0] if isinstance(raw, list) and raw else raw
    key = CATEGORY_ALIASES.get(str(value or "byt"), str(value or "byt"))
    return key if key in CAT_KEYS else "byt"


def canonical_offer(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key in OFFER_PATH:
        return key
    return PATH_OFFER.get(key, "pronajem")


def canonical_category(raw: str, default: str = "byt") -> str:
    key = CATEGORY_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
    return key if key in CAT_KEYS else default


def _offer(filters: dict) -> str:
    offer = (filters.get("offers") or ["pronajem"])[0]
    return canonical_offer(offer)


def list_path(offer: str, category: str, page: int = 1, page_size: int = PAGE_SIZE) -> str:
    offer_seg = OFFER_PATH[canonical_offer(offer)]
    cat_seg = canonical_category(category)
    page = max(1, int(page or 1))
    if page > 1:
        return f"/{offer_seg}/{cat_seg}/{(page - 1) * page_size}/"
    return f"/{offer_seg}/{cat_seg}/"


def path_context(url: str) -> tuple[str, str]:
    parts = [part for part in urlsplit(url or "").path.split("/") if part and not part.isdigit()]
    offer = "pronajem"
    category = "byt"
    if parts and parts[0] in OFFER_TOKENS:
        offer = canonical_offer(parts[0])
        if len(parts) > 1:
            category = canonical_category(parts[1])
    return offer, category


def page_url(search_url: str, page: int = 1, page_size: int = PAGE_SIZE) -> str:
    """Canonical list URL: /pronajmu/byt/ and /pronajmu/byt/20/, never /pronajem/byty/."""
    split = urlsplit(search_url or BASE)
    parts = [part for part in split.path.split("/") if part and not part.isdigit()]
    page = max(1, int(page or 1))
    if parts and parts[0] in OFFER_TOKENS:
        offer = canonical_offer(parts[0])
        if len(parts) > 1:
            path = list_path(offer, parts[1], page, page_size)
        else:
            offer_seg = OFFER_PATH[offer]
            path = f"/{offer_seg}/{(page - 1) * page_size}/" if page > 1 else f"/{offer_seg}/"
    else:
        path = "/" + "/".join(parts) + "/" if parts else "/"
        if page > 1:
            path = f"{path}{(page - 1) * page_size}/"
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.pop("order", None)
    query.pop("crp", None)
    query["kitx"] = "ano"
    return urlunsplit((split.scheme or "https", split.netloc or "reality.bazos.cz", path, urlencode(query), ""))


def locality_query(districts: list[str] | None) -> tuple[str, int]:
    values = [str(item).strip() for item in (districts or []) if str(item).strip()]
    if not values or any(localities.is_czech_country(item, item) for item in values) or set(values) >= set(localities.SREALITY_CZECH_REGIONS):
        return "", 0
    first = values[0]
    slug = localities.OSM_TO_SREALITY.get(first) or first
    if slug.startswith("praha-") and slug.split("-")[-1].isdigit():
        return f"Praha {slug.split('-')[-1]}", 8
    if slug == "praha":
        return "Praha", 20
    label = localities.OSM_LABELS.get(first) or localities.OSM_LABELS.get(slug) or slug.replace("-", " ")
    name = str(label).split(",")[0].strip()
    name = name.replace(" kraj", "").replace("Kraj ", "")
    return name, 50 if "kraj" in slug else 20


def build_url(filters: dict) -> str:
    offer = _offer(filters)
    category = _category(filters.get("category") or "byt")
    path = f"/{OFFER_PATH[offer]}/{category}/"
    query: list[tuple[str, str]] = []
    sizes = [item for item in (filters.get("sizes") or []) if item in SIZE_QUERY or item in SIZE_KEYS]
    sizes = [item if item in SIZE_KEYS else next((key for key in SIZE_KEYS if SIZE_QUERY.get(key) == SIZE_QUERY.get(item)), item) for item in sizes]
    sizes = list(dict.fromkeys(item for item in sizes if item in SIZE_KEYS))
    hledat = str(filters.get("hledat") or "").strip()
    if not hledat and len(sizes) == 1:
        hledat = SIZE_QUERY[sizes[0]]
    if hledat:
        query.append(("hledat", hledat))
    if len(sizes) > 1:
        query.append(("velikost", ",".join(sizes)))
    if filters.get("area_from") not in (None, ""):
        query.append(("plocha-od", str(int(filters["area_from"]))))
    if filters.get("area_to") not in (None, ""):
        query.append(("plocha-do", str(int(filters["area_to"]))))
    place, default_radius = locality_query(filters.get("districts") or [])
    if place:
        query.append(("hlokalita", place))
    try:
        radius = int(filters.get("radius")) if filters.get("radius") not in (None, "") else default_radius
    except (TypeError, ValueError):
        radius = default_radius
    if place:
        query.append(("humkreis", str(max(0, min(radius or default_radius or 20, 400)))))
    if filters.get("price_from") not in (None, ""):
        query.append(("cenaod", str(int(filters["price_from"]))))
    if filters.get("price_to") not in (None, ""):
        query.append(("cenado", str(int(filters["price_to"]))))
    query.append(("kitx", "ano"))
    return urlunsplit(("https", "reality.bazos.cz", path, urlencode(query), ""))


def parse_url(url: str) -> dict:
    data = default_filters()
    split = urlsplit(url or "")
    parts = [part for part in split.path.split("/") if part and not part.isdigit()]
    if parts and parts[0] in OFFER_TOKENS:
        data["offers"] = [canonical_offer(parts[0])]
    if len(parts) > 1:
        mapped = canonical_category(parts[1], default="")
        if mapped:
            data["category"] = mapped
    query = parse_qs(split.query, keep_blank_values=True)
    hledat = (query.get("hledat") or [""])[0].strip()
    data["hledat"] = hledat
    sizes = []
    raw_sizes = (query.get("velikost") or [""])[0]
    for item in raw_sizes.split(","):
        key = item.strip()
        if key in SIZE_KEYS:
            sizes.append(key)
        elif key in SIZE_QUERY:
            mapped = SIZE_QUERY[key]
            sizes.extend(k for k in SIZE_KEYS if SIZE_QUERY.get(k) == mapped)
    compact = hledat.replace(" ", "").casefold()
    if not sizes:
        for key, needle in SIZE_QUERY.items():
            token = needle.replace(" ", "").casefold()
            if token and token in compact and key in SIZE_KEYS:
                sizes.append(key)
    data["sizes"] = list(dict.fromkeys(sizes))
    place = (query.get("hlokalita") or [""])[0].strip()
    if place:
        slug = localities.sreality_slug(label=place) or place.casefold().replace(" ", "-")
        data["districts"] = [slug] if slug else [place]
    try:
        data["radius"] = int((query.get("humkreis") or ["20"])[0] or 20)
    except (TypeError, ValueError):
        data["radius"] = 20
    for key, param in (("price_from", "cenaod"), ("price_to", "cenado")):
        raw = (query.get(param) or [""])[0]
        try:
            data[key] = int(raw) if raw else None
        except ValueError:
            data[key] = None
    for key, param in (("area_from", "plocha-od"), ("area_to", "plocha-do")):
        raw = (query.get(param) or [""])[0]
        try:
            data[key] = int(raw) if raw else None
        except ValueError:
            data[key] = None
    return data
