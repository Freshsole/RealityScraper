from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit

BASE = "https://www.sreality.cz/hledani"

OFFERS = [
    ("pronajem", "Pronájem"),
    ("prodej", "Prodej"),
    ("drazby", "Dražby"),
    ("podily", "Podíly"),
]

CATEGORIES = [("byty", "Byty")]

SIZES = [
    ("1+1", "1+1"),
    ("1+kk", "1+kk"),
    ("2+1", "2+1"),
    ("2+kk", "2+kk"),
    ("3+1", "3+1"),
    ("3+kk", "3+kk"),
    ("4+1", "4+1"),
    ("4+kk", "4+kk"),
    ("5+1", "5+1"),
    ("5+kk", "5+kk"),
    ("6-a-vice", "6 pokojů a více"),
    ("atypicky", "Atypický"),
    ("pokoj", "Pokoj / spolubydlení"),
]

DISTRICTS = [(f"praha-{i}", f"Praha {i}") for i in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]

OWNERSHIP = [
    ("druzstevni", "Družstevní"),
    ("osobni", "Osobní"),
    ("statni-obecni", "Státní / obecní"),
]

CONDITIONS = [
    ("developerske-projekty", "Projekt"),
    ("dobry-stav", "Dobrý"),
    ("k-demolici", "K demolici"),
    ("novostavby", "Novostavba"),
    ("po-rekonstrukci", "Po rekonstrukci"),
    ("pred-rekonstrukci", "Před rekonstrukcí"),
    ("spatny-stav", "Špatný"),
    ("v-rekonstrukci", "V rekonstrukci"),
    ("ve-vystavbe", "Ve výstavbě"),
    ("velmi-dobry-stav", "Velmi dobrý"),
]

EXTRAS = [
    ("balkon", "Balkón"),
    ("bezbarierovy", "Bezbariérový"),
    ("garaz", "Garáž"),
    ("lodzie", "Lodžie"),
    ("parkovani", "Parkování"),
    ("sklep", "Sklep"),
    ("terasa", "Terasa"),
    ("vytah", "Výtah"),
    ("zahrada", "Zahrada"),
]

BUILDINGS = [
    ("cihlova", "Cihlová"),
    ("ostatni", "Ostatní (dřevostavba, kamenná, montovaná, skeletová, smíšená, modulární)"),
    ("panelova", "Panelová"),
]

ENERGY = [
    ("1", "A — Mimořádně úsporná"),
    ("2", "B — Velmi úsporná"),
    ("3", "C — Úsporná"),
    ("4", "D — Méně úsporná"),
    ("5", "E — Nehospodárná"),
    ("6", "F — Velmi nehospodárná"),
    ("7", "G — Mimořádně nehospodárná"),
]

POIS = [
    ("1", "Autobusová zastávka"),
    ("10", "Malý obchod"),
    ("11", "Restaurace, hospoda"),
    ("12", "Dětské hřiště"),
    ("13", "Metro"),
    ("2", "Vlakové nádraží"),
    ("3", "Pošta"),
    ("4", "Bankomat"),
    ("5", "Praktický lékař"),
    ("6", "Veterinář"),
    ("7", "Základní škola"),
    ("8", "Mateřská škola"),
    ("9", "Supermarket"),
]

SORTS = [
    ("nejnovejsi", "Nejnovější"),
    ("nejlevnejsi", "Nejlevnější"),
]


def catalog() -> dict:
    return {
        "offers": OFFERS,
        "categories": CATEGORIES,
        "sizes": SIZES,
        "districts": DISTRICTS,
        "ownership": OWNERSHIP,
        "conditions": CONDITIONS,
        "extras": EXTRAS,
        "buildings": BUILDINGS,
        "energy": ENERGY,
        "pois": POIS,
        "sorts": SORTS,
    }


def default_filters() -> dict:
    return {
        "source": "sreality",
        "offers": ["pronajem"],
        "category": "byty",
        "sizes": ["2+kk", "2+1", "3+kk", "3+1", "4+kk", "4+1", "5+kk", "5+1"],
        "districts": ["praha"],
        "ownership": [],
        "conditions": [],
        "extras": [],
        "buildings": [],
        "energy": [],
        "pois": [],
        "poi_distance": 2,
        "price_from": None,
        "price_to": 25759,
        "area_from": 45,
        "area_to": None,
        "floor_from": None,
        "floor_to": None,
        "sort": "nejlevnejsi",
    }


def sample_filters() -> dict:
    return {
        "source": "sreality",
        "offers": ["drazby", "podily", "prodej", "pronajem"],
        "category": "byty",
        "sizes": [key for key, _ in SIZES],
        "districts": [key for key, _ in DISTRICTS],
        "ownership": [key for key, _ in OWNERSHIP],
        "conditions": [key for key, _ in CONDITIONS],
        "extras": [key for key, _ in EXTRAS],
        "buildings": [key for key, _ in BUILDINGS],
        "energy": [key for key, _ in ENERGY],
        "pois": [key for key, _ in POIS],
        "poi_distance": 2,
        "price_from": 1999,
        "price_to": 25759,
        "area_from": 45,
        "area_to": 2000,
        "floor_from": 1,
        "floor_to": 100,
        "sort": "nejlevnejsi",
    }


def build_url(filters: dict) -> str:
    offers = _selected(filters.get("offers"), [k for k, _ in OFFERS]) or ["pronajem"]
    category = filters.get("category") or "byty"
    districts = _district_slugs(filters.get("districts"))
    path = "/".join(
        part
        for part in (
            ",".join(offers),
            category,
            ",".join(districts) if districts else "",
        )
        if part
    )
    query: list[tuple[str, str]] = []
    sizes = _selected(filters.get("sizes"), [k for k, _ in SIZES])
    if sizes:
        query.append(("velikost", ",".join(sizes)))
    conditions = _selected(filters.get("conditions"), [k for k, _ in CONDITIONS])
    if conditions:
        query.append(("stav", ",".join(conditions)))
    extras = _selected(filters.get("extras"), [k for k, _ in EXTRAS])
    if extras:
        query.append(("navic", ",".join(extras)))
    buildings = _selected(filters.get("buildings"), [k for k, _ in BUILDINGS])
    if buildings:
        query.append(("stavba", ",".join(buildings)))
    ownership = _selected(filters.get("ownership"), [k for k, _ in OWNERSHIP])
    if ownership:
        query.append(("vlastnictvi", ",".join(ownership)))
    sort = filters.get("sort") or "nejlevnejsi"
    query.append(("razeni", sort))
    _add_num(query, "cena-od", filters.get("price_from"))
    _add_num(query, "cena-do", filters.get("price_to"))
    _add_num(query, "patro-od", filters.get("floor_from"))
    _add_num(query, "patro-do", filters.get("floor_to"))
    _add_num(query, "plocha-od", filters.get("area_from"))
    _add_num(query, "plocha-do", filters.get("area_to"))
    energy = _selected(filters.get("energy"), [k for k, _ in ENERGY])
    if energy:
        query.append(("energy_efficiency_rating_search", "|".join(energy)))
    pois = _selected(filters.get("pois"), [k for k, _ in POIS])
    if pois:
        _add_num(query, "pois_in_place_distance", filters.get("poi_distance") or 2)
        query.append(("pois_in_place", "|".join(pois)))
    return f"{BASE}/{path}?{urlencode(query, doseq=True, safe='')}"


def parse_url(url: str) -> dict:
    filters = default_filters()
    split = urlsplit(url)
    parts = [p for p in split.path.split("/") if p and p != "hledani"]
    if parts:
        filters["offers"] = [p for p in parts[0].split(",") if p]
    if len(parts) > 1:
        filters["category"] = parts[1]
    if len(parts) > 2:
        filters["districts"] = [p for p in parts[2].split(",") if p]
    query = parse_qs(split.query, keep_blank_values=True)
    filters["sizes"] = _split_csv(query.get("velikost", [""])[0])
    filters["conditions"] = _split_csv(query.get("stav", [""])[0])
    filters["extras"] = _split_csv(query.get("navic", [""])[0])
    filters["buildings"] = _split_csv(query.get("stavba", [""])[0])
    filters["ownership"] = _split_csv(query.get("vlastnictvi", [""])[0])
    filters["energy"] = _split_pipe(query.get("energy_efficiency_rating_search", [""])[0])
    filters["pois"] = _split_pipe(query.get("pois_in_place", [""])[0])
    filters["sort"] = query.get("razeni", ["nejlevnejsi"])[0]
    filters["price_from"] = _int_or_none(query.get("cena-od", [None])[0])
    filters["price_to"] = _int_or_none(query.get("cena-do", [None])[0])
    filters["area_from"] = _int_or_none(query.get("plocha-od", [None])[0])
    filters["area_to"] = _int_or_none(query.get("plocha-do", [None])[0])
    filters["floor_from"] = _int_or_none(query.get("patro-od", [None])[0])
    filters["floor_to"] = _int_or_none(query.get("patro-do", [None])[0])
    filters["poi_distance"] = _int_or_none(query.get("pois_in_place_distance", ["2"])[0]) or 2
    return filters


def _district_slugs(values: list | None) -> list[str]:
    slugs: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        slug = str(value or "").strip().strip("/")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
            continue
        if slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
    return slugs


def _selected(values: list | None, allowed: list[str]) -> list[str]:
    if not values:
        return []
    allow = set(allowed)
    return [str(value) for value in values if str(value) in allow]


def _add_num(query: list[tuple[str, str]], key: str, value) -> None:
    if value in (None, "", False):
        return
    query.append((key, str(int(value))))


def _split_csv(raw: str) -> list[str]:
    return [part for part in (raw or "").split(",") if part]


def _split_pipe(raw: str) -> list[str]:
    return [part for part in (raw or "").replace("%7C", "|").split("|") if part]


def _int_or_none(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
