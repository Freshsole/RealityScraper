from __future__ import annotations

import re
import unicodedata
from typing import Any

from app import bezrealitky_url

CZECH_OSM = "R51684"
SREALITY_CZECH_REGIONS = [
    "jihocesky-kraj",
    "jihomoravsky-kraj",
    "karlovarsky-kraj",
    "kralovehradecky-kraj",
    "liberecky-kraj",
    "moravskoslezsky-kraj",
    "olomoucky-kraj",
    "pardubicky-kraj",
    "plzensky-kraj",
    "praha",
    "stredocesky-kraj",
    "ustecky-kraj",
    "vysocina-kraj",
    "zlinsky-kraj",
]
CZ_BOUNDARY_POINTS = [
    {"lat": 51.6418825664758, "lng": 19.03659837248884},
    {"lat": 51.6418825664758, "lng": 11.91322992751114},
    {"lat": 47.92673016315618, "lng": 11.91322992751114},
    {"lat": 47.92673016315618, "lng": 19.03659837248884},
    {"lat": 51.6418825664758, "lng": 19.03659837248884},
]
COUNTRY_SLUGS = {"cesko", "ceska-republika", "czech-republic", "czechia"}

OSM_TO_SREALITY = {
    CZECH_OSM: ",".join(SREALITY_CZECH_REGIONS),
    "R435514": "praha",
    "R15107966": "praha-1",
    "R19999122": "praha-2",
    "R19999121": "praha-3",
    "R19999068": "praha-4",
    "R19999086": "praha-5",
    "R19999115": "praha-6",
    "R19999114": "praha-7",
    "R19999109": "praha-8",
    "R19999082": "praha-9",
    "R19999075": "praha-10",
    "R438171": "brno",
    "R437354": "ostrava",
    "R438344": "plzen",
}
SREALITY_TO_OSM = {slug: ident for ident, slug in OSM_TO_SREALITY.items() if "," not in slug}
OSM_LABELS = dict(bezrealitky_url.DISTRICTS)
PRAHA_N = re.compile(r"^praha[\s-]+(\d+)$", re.I)


def catalog_map() -> dict[str, Any]:
    return {
        "osm_to_sreality": OSM_TO_SREALITY,
        "sreality_to_osm": SREALITY_TO_OSM,
        "osm_labels": OSM_LABELS,
        "quick": [
            (ident, label)
            for ident, label in bezrealitky_url.DISTRICTS
            if label in {"Česko", "Praha", "Brno", "Plzeň"}
        ],
    }


def short_label(label: str | None) -> str:
    return str(label or "").split(",")[0].strip()


def slugify(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = text.replace("ß", "ss")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def is_czech_country(ident: str | None = None, label: str | None = None) -> bool:
    if str(ident or "").strip() == CZECH_OSM:
        return True
    return slugify(short_label(label)) in COUNTRY_SLUGS


def sreality_slug(ident: str | None = None, label: str | None = None, address: dict[str, Any] | None = None) -> str:
    ident = str(ident or "").strip()
    if ident in OSM_TO_SREALITY:
        return OSM_TO_SREALITY[ident]
    name = short_label(label)
    numbered = PRAHA_N.match(name.replace("–", "-").replace("—", "-"))
    if numbered:
        return f"praha-{numbered.group(1)}"
    addr = address or {}
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or ""
    ).strip()
    suburb = (
        addr.get("suburb")
        or addr.get("city_district")
        or addr.get("quarter")
        or addr.get("neighbourhood")
        or ""
    ).strip()
    city_slug = slugify(city)
    suburb_num = PRAHA_N.match(slugify(suburb).replace("-", " ") if suburb else "")
    if city_slug in {"praha", "prague"}:
        if suburb_num:
            return f"praha-{suburb_num.group(1)}"
        numbered_city = PRAHA_N.match(slugify(f"{city} {suburb}") if suburb else city_slug)
        if numbered_city:
            return f"praha-{numbered_city.group(1)}"
        if suburb:
            part = slugify(suburb)
            if part and part not in {"praha", "prague"}:
                return f"praha-{part}"
        return "praha"
    if city_slug and suburb:
        part = slugify(suburb)
        if part and part != city_slug:
            return f"{city_slug}-{part}"
    if city_slug:
        return city_slug
    return slugify(name)


def locality_item(ident: str, label: str, address: dict[str, Any] | None = None) -> dict[str, str]:
    ident = str(ident or "").strip()
    label = str(label or ident).strip()
    if is_czech_country(ident, label):
        return {
            "id": CZECH_OSM,
            "label": "Česko",
            "sreality": ",".join(SREALITY_CZECH_REGIONS),
            "osm_value": "Česko",
        }
    return {
        "id": ident,
        "label": label,
        "sreality": sreality_slug(ident, label, address),
        "osm_value": short_label(label) or OSM_LABELS.get(ident, ident),
    }


def localities_from_districts(districts: list | None, osm_value: str | None = None) -> list[dict[str, str]]:
    values = [str(raw or "").strip() for raw in (districts or []) if str(raw or "").strip()]
    hint = short_label(osm_value)
    if any(is_czech_country(value, hint or value) for value in values) or set(values) >= set(SREALITY_CZECH_REGIONS):
        return [locality_item(CZECH_OSM, "Česko")]
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in districts or []:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        if value in OSM_TO_SREALITY or (value.startswith("R") and value[1:].isdigit()):
            items.append(locality_item(value, OSM_LABELS.get(value) or hint or value))
            continue
        osm = SREALITY_TO_OSM.get(value)
        if osm:
            items.append(locality_item(osm, OSM_LABELS.get(osm) or hint or value))
            continue
        numbered = PRAHA_N.match(value)
        if numbered:
            slug = f"praha-{numbered.group(1)}"
            osm = SREALITY_TO_OSM.get(slug)
            items.append(locality_item(osm or slug, f"Praha {numbered.group(1)}"))
            continue
        items.append(
            {
                "id": value,
                "label": hint or value,
                "sreality": slugify(value),
                "osm_value": hint or value,
            }
        )
    return items


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(filters or {})
    locs = [item for item in (data.get("localities") or []) if isinstance(item, dict) and item.get("id")]
    if not locs:
        locs = localities_from_districts(data.get("districts"), data.get("osm_value"))
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in locs:
        packed = locality_item(str(item.get("id") or ""), str(item.get("label") or item.get("id") or ""), None)
        if item.get("sreality") and packed["id"] != CZECH_OSM:
            packed["sreality"] = str(item["sreality"])
        if packed["id"] in seen:
            continue
        seen.add(packed["id"])
        cleaned.append(packed)
    data["localities"] = cleaned
    if any(item["id"] == CZECH_OSM for item in cleaned):
        cleaned = [item for item in cleaned if item["id"] == CZECH_OSM][:1]
        data["localities"] = cleaned
    source = str(data.get("source") or "")
    if source == "bezrealitky":
        data["districts"] = [item["id"] for item in cleaned if str(item["id"]).startswith("R")]
        data["osm_value"] = cleaned[0]["osm_value"] if cleaned else str(data.get("osm_value") or "")
        if cleaned and cleaned[0]["id"] == CZECH_OSM:
            data["boundary_points"] = CZ_BOUNDARY_POINTS
            data["osm_value"] = "Česko"
    else:
        slugs: list[str] = []
        for item in cleaned:
            for slug in str(item.get("sreality") or "").split(","):
                slug = slug.strip()
                if slug and slug not in slugs:
                    slugs.append(slug)
        data["districts"] = slugs
    return data
