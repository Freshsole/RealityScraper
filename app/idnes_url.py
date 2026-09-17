from __future__ import annotations

from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from app import localities

BASE = "https://reality.idnes.cz/s"

OFFERS = [
    ("pronajem", "Pronájem"),
    ("prodej", "Prodej"),
    ("drazba", "Dražba"),
]
CATEGORIES = [
    ("byty", "Byty"),
    ("domy", "Domy"),
    ("pozemky", "Pozemky"),
    ("komercni-nemovitosti", "Komerční"),
    ("male-objekty-garaze", "Garáže a objekty"),
    ("projekty", "Projekty"),
]
# Sreality/Bazoš singulars 404 on iDNES list paths (/s/pronajem/dum/, /s/pronajem/byt/).
CATEGORY_ALIASES = {
    "byt": "byty",
    "dum": "domy",
    "pozemek": "pozemky",
    "komercni": "komercni-nemovitosti",
    "ostatni": "male-objekty-garaze",
    "garaze": "male-objekty-garaze",
}
CAT_KEYS = {key for key, _ in CATEGORIES}
SIZES = [
    ("1-kk", "1+kk"),
    ("1-1", "1+1"),
    ("2-kk", "2+kk"),
    ("2-1", "2+1"),
    ("3-kk", "3+kk"),
    ("3-1", "3+1"),
    ("4-kk", "4+kk"),
    ("4-1", "4+1"),
    ("5-kk", "5+kk"),
    ("5-1", "5+1"),
    ("6-kk-a-vetsi", "6+kk a větší"),
    ("pokoj", "Pokoj"),
    ("atypicke", "Atypické"),
]
OWNERSHIP = [
    ("osobni", "Osobní"),
    ("druzstevni", "Družstevní"),
    ("s-r-o", "S.r.o."),
    ("podilove", "Podílové"),
    ("jine", "Jiné"),
]
CONDITIONS = [
    ("novostavba", "Novostavba"),
    ("projekt", "Projekt"),
    ("ve-vystavbe", "Ve výstavbě"),
    ("dobry-stav", "Dobrý stav"),
    ("udrzovany", "Udržovaný"),
    ("spatny-stav", "Špatný stav"),
    ("po-rekonstrukci", "Po rekonstrukci"),
    ("v-rekonstrukci", "V rekonstrukci"),
    ("pred-rekonstrukci", "Před rekonstrukcí"),
    ("k-demolici", "K demolici"),
]
BUILDINGS = [
    ("cihlova", "Cihlová"),
    ("panelova", "Panelová"),
    ("drevena", "Dřevěná"),
    ("kamenna", "Kamenná"),
    ("skeletova", "Skeletová"),
    ("montovana", "Montovaná"),
    ("smisena", "Smíšená"),
]
EQUIPPED = [
    ("zarizeny", "Zařízený"),
    ("castecne", "Částečně zařízený"),
    ("nezarizeny", "Nezařízený"),
]
EXTRAS = [
    ("balkon", "Balkon"),
    ("lodzie", "Lodžie"),
    ("terasa", "Terasa"),
    ("zahrada", "Zahrada"),
    ("sklep", "Sklep"),
    ("garaz", "Garáž"),
    ("parkovani", "Parkování"),
    ("vytah", "Výtah"),
    ("bezbarierovy", "Bezbariérový"),
    ("telefon", "Telefon"),
    ("kabelova-tv", "Kabelová televize"),
    ("internet", "Internet"),
]
AGES = [("", "Bez omezení"), ("1", "Den"), ("7", "Týden"), ("30", "Měsíc")]
FLAGS = [("video", "Video"), ("sale", "Zlevněno"), ("openHouse", "Den otevřených dveří")]
SORTS = [("nejnovejsi", "Nejnovější"), ("nejlevnejsi", "Nejlevnější"), ("nejdrazsi", "Nejdražší")]

SIZE_KEYS = [key for key, _ in SIZES]
OWNER_KEYS = [key for key, _ in OWNERSHIP]
COND_KEYS = [key for key, _ in CONDITIONS]
BUILD_KEYS = [key for key, _ in BUILDINGS]
EQUIP_KEYS = [key for key, _ in EQUIPPED]
EXTRA_KEYS = [key for key, _ in EXTRAS]


def canonical_category(raw, default: str = "byty") -> str:
    key = CATEGORY_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip().lower())
    return key if key in CAT_KEYS else default


def _category(raw) -> str:
    value = raw[0] if isinstance(raw, list) and raw else raw
    return canonical_category(value, "byty")


def catalog() -> dict:
    return {
        "offers": OFFERS,
        "categories": CATEGORIES,
        "sizes": SIZES,
        "ownership": OWNERSHIP,
        "conditions": CONDITIONS,
        "buildings": BUILDINGS,
        "equipped": EQUIPPED,
        "extras": EXTRAS,
        "ages": AGES,
        "flags": FLAGS,
        "sorts": SORTS,
        "energy": [],
        "pois": [],
        "single_keys": ["category", "article_age"],
    }


def default_filters() -> dict:
    return {
        "source": "idnes",
        "offers": ["pronajem"],
        "category": "byty",
        "sizes": [],
        "districts": ["praha"],
        "ownership": [],
        "conditions": [],
        "buildings": [],
        "equipped": [],
        "extras": [],
        "flags": [],
        "article_age": "",
        "price_from": None,
        "price_to": None,
        "area_from": None,
        "area_to": None,
        "floor_from": None,
        "floor_to": None,
        "sort": "nejnovejsi",
    }


def sample_filters() -> dict:
    return default_filters()


def is_idnes(url: str) -> bool:
    raw = (url or "").lower()
    return "reality.idnes.cz" in raw or "idnes.cz/s/" in raw or "idnes.cz/detail/" in raw


def build_url(filters: dict) -> str:
    offer = (filters.get("offers") or ["pronajem"])[0]
    if offer not in {key for key, _ in OFFERS}:
        offer = "pronajem"
    category = _category(filters.get("category") or "byty")
    districts = [str(item).strip("/") for item in (filters.get("districts") or []) if str(item).strip()]
    locality = _locality(districts)
    parts = [offer, category]
    if locality:
        parts.append(locality)
    query: list[tuple[str, str]] = []
    sizes = _selected(filters.get("sizes"), SIZE_KEYS)
    if sizes:
        query.append(("dispozice", "|".join(sizes)))
    conditions = _selected(filters.get("conditions"), COND_KEYS)
    if conditions:
        query.append(("stav", "|".join(conditions)))
    ownership = _selected(filters.get("ownership"), OWNER_KEYS)
    if ownership:
        query.append(("vlastnictvi", "|".join(ownership)))
    buildings = _selected(filters.get("buildings"), BUILD_KEYS)
    if buildings:
        query.append(("konstrukce", "|".join(buildings)))
    amenity = _selected(filters.get("extras"), EXTRA_KEYS)
    equipped = _selected(filters.get("equipped"), EQUIP_KEYS)
    vybaveni = amenity + equipped
    if vybaveni:
        query.append(("vybaveni", "|".join(vybaveni)))
    _add_qc(query, "priceMin", filters.get("price_from"))
    _add_qc(query, "priceMax", filters.get("price_to"))
    _add_qc(query, "usableAreaMin", filters.get("area_from"))
    _add_qc(query, "usableAreaMax", filters.get("area_to"))
    age = filters.get("article_age") or ""
    if isinstance(age, list):
        age = age[0] if age else ""
    age = str(age).strip()
    if age in {"1", "7", "30"}:
        query.append(("s-qc[articleAge]", age))
    flags = set(filters.get("flags") or [])
    if "video" in flags:
        query.append(("s-qc[video]", "1"))
    if "sale" in flags or "discounted" in flags:
        query.append(("s-qc[sale]", "1"))
    if "openHouse" in flags:
        query.append(("s-qc[openHouse]", "1"))
    sort = filters.get("sort") or ""
    if sort in {"nejlevnejsi", "nejdrazsi"}:
        query.append(("sort", sort))
    path = "/".join(parts)
    encoded = urlencode(query, doseq=True, safe="[]|")
    return f"{BASE}/{path}/" + (f"?{encoded}" if encoded else "")


def page_url(search_url: str, page: int = 1, newest: bool = True) -> str:
    """Canonical list URL: /s/pronajem/domy/, never the 404 /s/pronajem/dum/."""
    filters = parse_url(search_url)
    built = build_url(filters)
    split = urlsplit(built)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    if newest:
        query.pop("sort", None)
    page = max(1, int(page or 1))
    if page <= 1:
        query.pop("page", None)
    else:
        query["page"] = str(page - 1)
    return urlunsplit(
        (split.scheme or "https", split.netloc or "reality.idnes.cz", split.path, urlencode(query, safe="[]|"), "")
    )


def parse_url(url: str) -> dict:
    filters = default_filters()
    # Nationwide list URLs have no locality segment; do not keep the form default (Praha).
    filters["districts"] = []
    split = urlsplit(url)
    parts = [item for item in split.path.split("/") if item and item != "s"]
    if parts and parts[0] in {key for key, _ in OFFERS}:
        filters["offers"] = [parts[0]]
    if len(parts) > 1:
        filters["category"] = canonical_category(parts[1])
    if len(parts) > 2:
        filters["districts"] = [parts[2]]
    query = parse_qs(split.query, keep_blank_values=True)
    filters["sizes"] = _split_pipe(query.get("dispozice", [""])[0])
    filters["conditions"] = _split_pipe(query.get("stav", [""])[0])
    filters["ownership"] = _split_pipe(query.get("vlastnictvi", [""])[0])
    filters["buildings"] = _split_pipe(query.get("konstrukce", [""])[0])
    vybaveni = _split_pipe(query.get("vybaveni", [""])[0])
    filters["extras"] = [item for item in vybaveni if item in EXTRA_KEYS]
    filters["equipped"] = [item for item in vybaveni if item in EQUIP_KEYS]
    filters["price_from"] = _int_or_none(_first(query, "s-qc[priceMin]", "priceMin"))
    filters["price_to"] = _int_or_none(_first(query, "s-qc[priceMax]", "priceMax"))
    filters["area_from"] = _int_or_none(_first(query, "s-qc[usableAreaMin]", "usableAreaMin"))
    filters["area_to"] = _int_or_none(_first(query, "s-qc[usableAreaMax]", "usableAreaMax"))
    filters["article_age"] = str(_first(query, "s-qc[articleAge]", "articleAge") or "")
    flags = []
    if str(_first(query, "s-qc[video]", "video") or "") == "1":
        flags.append("video")
    if str(_first(query, "s-qc[sale]", "sale") or "") == "1":
        flags.append("sale")
    if str(_first(query, "s-qc[openHouse]", "openHouse") or "") == "1":
        flags.append("openHouse")
    filters["flags"] = flags
    filters["sort"] = (query.get("sort") or ["nejnovejsi"])[0]
    return filters


def _locality(districts: list[str]) -> str:
    slugs = []
    for item in districts:
        raw = str(item).strip("/")
        if not raw:
            continue
        if raw == localities.CZECH_OSM or localities.is_czech_country(raw, raw) or raw in {"cesko", "ceska-republika"}:
            return ""
        mapped = localities.OSM_TO_SREALITY.get(raw) or raw
        for part in str(mapped).split(","):
            slug = part.strip()
            if slug:
                slugs.append(slug)
    unique = list(dict.fromkeys(slugs))
    if not unique or set(unique) >= set(localities.SREALITY_CZECH_REGIONS):
        return ""
    if all(item.startswith("praha") for item in unique):
        return unique[0] if len(unique) == 1 else "praha"
    if len(unique) == 1:
        return unique[0]
    return ""


def _selected(values: list | None, allowed: list[str]) -> list[str]:
    if not values:
        return []
    allow = set(allowed)
    return [str(item) for item in values if str(item) in allow]


def _add_qc(query: list[tuple[str, str]], key: str, value) -> None:
    if value in (None, "", False):
        return
    query.append((f"s-qc[{key}]", str(int(value))))


def _split_pipe(raw: str) -> list[str]:
    return [part for part in (raw or "").replace("%7C", "|").split("|") if part]


def _first(query: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        if query.get(key):
            return query[key][0]
    return None


def _int_or_none(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
