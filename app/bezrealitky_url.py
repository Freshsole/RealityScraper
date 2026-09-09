from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

OFFERS = [("PRONAJEM", "Pronájem"), ("PRODEJ", "Prodej")]
ESTATES = [
    ("BYT", "Byt"),
    ("DUM", "Dům"),
    ("GARAZ", "Garáž"),
    ("KANCELAR", "Kancelář"),
    ("NEBYTOVY_PROSTOR", "Nebytový prostor"),
    ("POZEMEK", "Pozemek"),
    ("REKREACNI_OBJEKT", "Chaty a chalupy"),
]
SIZES = [
    ("DISP_1_1", "1+1"),
    ("DISP_1_KK", "1+kk"),
    ("DISP_2_1", "2+1"),
    ("DISP_2_KK", "2+kk"),
    ("DISP_3_1", "3+1"),
    ("DISP_3_KK", "3+kk"),
    ("DISP_4_1", "4+1"),
    ("DISP_4_KK", "4+kk"),
    ("DISP_5_1", "5+1"),
    ("DISP_5_KK", "5+kk"),
    ("DISP_6_1", "6+1"),
    ("DISP_6_KK", "6+kk"),
    ("DISP_7_1", "7+1"),
    ("DISP_7_KK", "7+kk"),
    ("GARSONIERA", "Garsoniéra"),
    ("OSTATNI", "Ostatní"),
]
DISTRICTS = [
    ("R435514", "Praha"),
    ("R15107966", "Praha 1"),
    ("R19999122", "Praha 2"),
    ("R19999121", "Praha 3"),
    ("R19999068", "Praha 4"),
    ("R19999086", "Praha 5"),
    ("R19999115", "Praha 6"),
    ("R19999114", "Praha 7"),
    ("R19999109", "Praha 8"),
    ("R19999082", "Praha 9"),
    ("R19999075", "Praha 10"),
    ("R438171", "Brno"),
    ("R437354", "Ostrava"),
    ("R438344", "Plzeň"),
]
OWNERSHIP = [
    ("DRUZSTEVNI", "Družstevní"),
    ("OBECNI", "Obecní"),
    ("OSOBNI", "Osobní"),
    ("OSTATNI", "Ostatní"),
]
TRANSFERS = [("true", "Převod do OV: ano"), ("false", "Převod do OV: ne")]
CONDITIONS = [
    ("AFTER_PARTIAL_RECONSTRUCTION", "Po částečné rekonstrukci"),
    ("AFTER_RECONSTRUCTION", "Po rekonstrukci"),
    ("BAD", "Špatný"),
    ("BEFORE_RECONSTRUCTION", "Před rekonstrukcí"),
    ("CONSTRUCTION", "Ve výstavbě"),
    ("DEMOLITION", "K demolici"),
    ("GOOD", "Dobrý"),
    ("IN_RECONSTRUCTION", "V rekonstrukci"),
    ("NEW", "Novostavba"),
    ("PROJECT", "Projekt"),
    ("VERY_GOOD", "Velmi dobrý"),
]
BUILDINGS = [
    ("BRICK", "Cihla"),
    ("MIXED", "Smíšená"),
    ("PANEL", "Panel"),
    ("PREFAB", "Montovaná"),
    ("SKELET", "Skeletová"),
    ("STONE", "Kamenná"),
]
EQUIPPED = [
    ("CASTECNE", "Částečně"),
    ("NEVYBAVENY", "Nevybaveno"),
    ("VYBAVENY", "Vybaveno"),
]
EXTRAS = [
    ("balcony", "Balkón"),
    ("loggia", "Lodžie"),
    ("cellar", "Sklep"),
    ("terrace", "Terasa"),
    ("barrierFree", "Bezbariérový přístup"),
    ("garage", "Garážové stání"),
    ("lift", "Výtah"),
    ("parking", "Parkování před domem"),
    ("petFriendly", "Domácí mazlíčci vítáni"),
    ("searchPriceWithCharges", "Včetně energií a služeb"),
]
FLAGS = [
    ("discountedOnly", "Pouze zlevněné"),
    ("includeImports", "Zobrazit i nabídky správců"),
    ("includeShortTerm", "Včetně krátkodobých pronájmů"),
]
ROOMMATE = [
    ("", "Včetně spolubydlení"),
    ("false", "Bez spolubydlení"),
    ("true", "Jen spolubydlení"),
]
CURRENCIES = [("CZK", "Kč"), ("EUR", "EUR")]
NEIGHBORHOODS = [
    (0, "0 km"),
    (1000, "+1 km"),
    (2000, "+2 km"),
    (5000, "+5 km"),
    (10000, "+10 km"),
    (15000, "+15 km"),
    (20000, "+20 km"),
]
DISTRICT_LABELS = {key: label for key, label in DISTRICTS}


def catalog() -> dict:
    return {
        "offers": OFFERS,
        "estates": ESTATES,
        "sizes": SIZES,
        "districts": DISTRICTS,
        "ownership": OWNERSHIP,
        "transfers": TRANSFERS,
        "conditions": CONDITIONS,
        "buildings": BUILDINGS,
        "equipped": EQUIPPED,
        "extras": EXTRAS,
        "flags": FLAGS,
        "roommate": ROOMMATE,
        "currencies": CURRENCIES,
        "neighborhoods": NEIGHBORHOODS,
        "energy": [],
        "pois": [],
        "single_keys": ["offers", "estates", "roommate"],
    }


def default_filters() -> dict:
    return {
        "source": "bezrealitky",
        "offers": ["PRONAJEM"],
        "estates": ["BYT"],
        "sizes": ["DISP_2_KK", "DISP_2_1", "DISP_3_KK", "DISP_3_1", "DISP_4_KK", "DISP_4_1", "DISP_5_KK", "DISP_5_1"],
        "districts": ["R435514"],
        "ownership": [],
        "transfers": [],
        "conditions": [],
        "buildings": [],
        "equipped": [],
        "extras": [],
        "flags": ["includeImports", "includeShortTerm"],
        "roommate": [""],
        "price_from": None,
        "price_to": 25759,
        "area_from": 45,
        "area_to": None,
        "annuity_from": None,
        "annuity_to": None,
        "balcony_from": None,
        "balcony_to": None,
        "loggia_from": None,
        "loggia_to": None,
        "cellar_from": None,
        "cellar_to": None,
        "terrace_from": None,
        "terrace_to": None,
        "garden_from": None,
        "garden_to": None,
        "neighborhood": 0,
        "currency": "CZK",
        "osm_value": "Praha, Česko",
        "available_from": None,
        "advert_id": "",
        "boundary_points": None,
        "location": "exact",
        "floor_from": None,
        "floor_to": None,
        "poi_distance": None,
        "sort": "TIMEORDER_DESC",
    }


def sample_filters() -> dict:
    return default_filters()


def _num(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes"}


def _add_num(params: list[tuple[str, str]], key: str, value) -> None:
    number = _num(value)
    if number is not None:
        params.append((key, str(number)))


def _unix(value: str | None) -> int | None:
    if not value:
        return None
    if str(value).isdigit():
        return int(value)
    try:
        parsed = datetime.fromisoformat(str(value)[:10]).replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _date_from_unix(raw: str | None) -> str | None:
    if not raw:
        return None
    if not str(raw).isdigit():
        return str(raw)[:10]
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc).date().isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def build_url(filters: dict) -> str:
    params: list[tuple[str, str]] = []
    for offer in filters.get("offers") or ["PRONAJEM"]:
        params.append(("offerType", offer))
    for estate in filters.get("estates") or ["BYT"]:
        params.append(("estateType", estate))
    for size in filters.get("sizes") or []:
        params.append(("disposition", size))
    districts = filters.get("districts") or []
    for district in districts:
        params.append(("regionOsmIds", district))
    osm_value = (filters.get("osm_value") or "").strip()
    if not osm_value and districts:
        osm_value = DISTRICT_LABELS.get(districts[0], districts[0])
    if osm_value:
        params.append(("osm_value", osm_value))
    if districts:
        params.append(("location", filters.get("location") or "exact"))
    for item in filters.get("ownership") or []:
        params.append(("ownership", item))
    for item in filters.get("transfers") or []:
        params.append(("transferToPersonalOwnership", item))
    for item in filters.get("conditions") or []:
        params.append(("condition", item))
    for item in filters.get("buildings") or []:
        params.append(("construction", item))
    for item in filters.get("equipped") or []:
        params.append(("equipped", item))
    extras = set(filters.get("extras") or [])
    for key, _ in EXTRAS:
        if key in extras:
            params.append((key, "true"))
    flags = set(filters.get("flags") or [])
    if "discountedOnly" in flags:
        params.append(("discountedOnly", "true"))
    if "includeImports" not in flags:
        params.append(("includeImports", "false"))
    if "includeShortTerm" not in flags:
        params.append(("includeShortTerm", "false"))
    roommate = (filters.get("roommate") or [""])[0]
    if roommate in {"true", "false"}:
        params.append(("roommate", roommate))
    currency = filters.get("currency") or "CZK"
    if currency != "CZK":
        params.append(("currency", currency))
    _add_num(params, "priceFrom", filters.get("price_from"))
    _add_num(params, "priceTo", filters.get("price_to"))
    _add_num(params, "surfaceFrom", filters.get("area_from"))
    _add_num(params, "surfaceTo", filters.get("area_to"))
    _add_num(params, "annuityFrom", filters.get("annuity_from"))
    _add_num(params, "annuityTo", filters.get("annuity_to"))
    _add_num(params, "balconyFrom", filters.get("balcony_from"))
    _add_num(params, "balconyTo", filters.get("balcony_to"))
    _add_num(params, "loggiaFrom", filters.get("loggia_from"))
    _add_num(params, "loggiaTo", filters.get("loggia_to"))
    _add_num(params, "cellarFrom", filters.get("cellar_from"))
    _add_num(params, "cellarTo", filters.get("cellar_to"))
    _add_num(params, "terraceFrom", filters.get("terrace_from"))
    _add_num(params, "terraceTo", filters.get("terrace_to"))
    _add_num(params, "frontGardenFrom", filters.get("garden_from"))
    _add_num(params, "frontGardenTo", filters.get("garden_to"))
    neighborhood = _num(filters.get("neighborhood")) or 0
    if neighborhood:
        params.append(("neighborhood", str(neighborhood)))
        params.append(("locationRadius", str(neighborhood)))
    available = _unix(filters.get("available_from"))
    if available:
        params.append(("availableFrom", str(available)))
    advert_id = str(filters.get("advert_id") or "").strip()
    if advert_id:
        params.append(("id", advert_id))
    if filters.get("boundary_points"):
        params.append(("boundaryPoints", str(filters["boundary_points"])))
    params.append(("order", "TIMEORDER_DESC"))
    return "https://www.bezrealitky.cz/vyhledat?" + urlencode(params, doseq=False)


def parse_url(url: str) -> dict:
    query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    extras = [key for key, _ in EXTRAS if _truthy((query.get(key) or [""])[0])]
    flags = []
    if _truthy((query.get("discountedOnly") or [""])[0]):
        flags.append("discountedOnly")
    if (query.get("includeImports") or ["true"])[0].lower() != "false":
        flags.append("includeImports")
    if (query.get("includeShortTerm") or ["true"])[0].lower() != "false":
        flags.append("includeShortTerm")
    roommate = (query.get("roommate") or [""])[0]
    if roommate not in {"true", "false"}:
        roommate = ""
    advert = (query.get("id") or query.get("ids") or [""])[0]
    boundary = (query.get("boundaryPoints") or [None])[0]
    if boundary:
        boundary = unquote(boundary)
    return {
        "source": "bezrealitky",
        "offers": query.get("offerType") or ["PRONAJEM"],
        "estates": query.get("estateType") or ["BYT"],
        "sizes": query.get("disposition") or [],
        "districts": query.get("regionOsmIds") or [],
        "ownership": query.get("ownership") or [],
        "transfers": query.get("transferToPersonalOwnership") or [],
        "conditions": query.get("condition") or [],
        "buildings": query.get("construction") or [],
        "equipped": query.get("equipped") or [],
        "extras": extras,
        "flags": flags,
        "roommate": [roommate],
        "energy": [],
        "pois": [],
        "price_from": _num((query.get("priceFrom") or [None])[0]),
        "price_to": _num((query.get("priceTo") or [None])[0]),
        "area_from": _num((query.get("surfaceFrom") or [None])[0]),
        "area_to": _num((query.get("surfaceTo") or [None])[0]),
        "annuity_from": _num((query.get("annuityFrom") or [None])[0]),
        "annuity_to": _num((query.get("annuityTo") or [None])[0]),
        "balcony_from": _num((query.get("balconyFrom") or [None])[0]),
        "balcony_to": _num((query.get("balconyTo") or [None])[0]),
        "loggia_from": _num((query.get("loggiaFrom") or [None])[0]),
        "loggia_to": _num((query.get("loggiaTo") or [None])[0]),
        "cellar_from": _num((query.get("cellarFrom") or [None])[0]),
        "cellar_to": _num((query.get("cellarTo") or [None])[0]),
        "terrace_from": _num((query.get("terraceFrom") or [None])[0]),
        "terrace_to": _num((query.get("terraceTo") or [None])[0]),
        "garden_from": _num((query.get("frontGardenFrom") or [None])[0]),
        "garden_to": _num((query.get("frontGardenTo") or [None])[0]),
        "neighborhood": _num((query.get("neighborhood") or query.get("locationRadius") or [0])[0]) or 0,
        "currency": (query.get("currency") or ["CZK"])[0] or "CZK",
        "osm_value": unquote((query.get("osm_value") or [""])[0]),
        "available_from": _date_from_unix((query.get("availableFrom") or [None])[0]),
        "advert_id": advert,
        "boundary_points": boundary,
        "location": (query.get("location") or ["exact"])[0],
        "floor_from": None,
        "floor_to": None,
        "poi_distance": None,
        "sort": (query.get("order") or ["TIMEORDER_DESC"])[0],
    }


def default_search_url() -> str:
    return build_url(default_filters())


DEFAULT_SEARCH_URL = default_search_url()
