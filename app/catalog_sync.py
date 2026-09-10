from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import bezrealitky_url, localities, url_builder
from app.sources import is_bezrealitky

KRAJ_HINTS = {
    "praha": ["praha"],
    "stredocesky-kraj": ["středočesk", "stredocesk", "kladno", "kolín", "mladá boleslav", "benešov", "beroun", "mělník", "nymburk", "příbram", "rakovník", "kutná hora"],
    "jihocesky-kraj": ["jihočesk", "jihocesk", "české budějovice", "český krumlov", "písek", "tábor", "strakonice", "prachatice", "jindřichův hradec"],
    "plzensky-kraj": ["plzeň", "plzen", "domažlice", "klatovy", "rokycany", "tachov"],
    "karlovarsky-kraj": ["karlovar", "karlovy vary", "cheb", "sokolov"],
    "ustecky-kraj": ["ústeck", "usteck", "ústí nad labem", "děčín", "chomutov", "most", "teplice", "louny", "litoměřice"],
    "liberecky-kraj": ["liberec", "jablonec", "česká lípa", "semily"],
    "kralovehradecky-kraj": ["královéhradeck", "kralovehradeck", "hradec králové", "jicin", "jičín", "náchod", "trutnov", "rychnov"],
    "pardubicky-kraj": ["pardubic", "chrudim", "svitavy", "ústí nad orlicí"],
    "vysocina-kraj": ["vysočina", "vysocina", "jihlava", "třebíč", "havlíčkův brod", "žďár", "pelhřimov"],
    "olomoucky-kraj": ["olomouc", "prostějov", "přerov", "šumperk", "jeseník"],
    "moravskoslezsky-kraj": ["moravskoslez", "ostrava", "opava", "karviná", "frýdek", "nový jičín", "bruntál"],
    "jihomoravsky-kraj": ["jihomorav", "brno", "znojmo", "hodonín", "břeclav", "vyškov", "blansko"],
    "zlinsky-kraj": ["zlín", "zlin", "uherské hradiště", "vsetín", "kroměříž"],
}


def fold(value: str) -> str:
    return (
        str(value or "")
        .casefold()
        .replace("á", "a")
        .replace("č", "c")
        .replace("ď", "d")
        .replace("é", "e")
        .replace("ě", "e")
        .replace("í", "i")
        .replace("ň", "n")
        .replace("ó", "o")
        .replace("ř", "r")
        .replace("š", "s")
        .replace("ť", "t")
        .replace("ú", "u")
        .replace("ů", "u")
        .replace("ý", "y")
        .replace("ž", "z")
    )


def normalize_search_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if is_bezrealitky(raw):
        filters = bezrealitky_url.parse_url(raw)
        filters["sort"] = "TIMEORDER_DESC"
        return bezrealitky_url.build_url(filters)
    filters = url_builder.parse_url(raw)
    filters["sort"] = "nejnovejsi"
    built = url_builder.build_url(filters)
    split = urlsplit(built)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    query.pop("noredirect", None)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query, doseq=True), ""))


def daily_shards() -> list[dict[str, str]]:
    shards: list[dict[str, str]] = []
    for offer in ("pronajem", "prodej"):
        for region in localities.SREALITY_CZECH_REGIONS:
            url = url_builder.build_url(
                {
                    "source": "sreality",
                    "offers": [offer],
                    "category": "byty",
                    "districts": [region],
                    "sizes": [],
                    "sort": "nejnovejsi",
                    "price_from": None,
                    "price_to": None,
                    "area_from": None,
                    "area_to": None,
                }
            )
            shards.append(
                {
                    "kind": "catalog_daily",
                    "portal": "sreality",
                    "shard_key": f"sreality:byty:{offer}:{region}",
                    "search_url": url,
                }
            )
    for offer in ("PRONAJEM", "PRODEJ"):
        for size, _label in bezrealitky_url.SIZES:
            url = bezrealitky_url.build_url(
                {
                    "source": "bezrealitky",
                    "offers": [offer],
                    "estates": ["BYT"],
                    "sizes": [size],
                    "districts": [localities.CZECH_OSM],
                    "osm_value": "Česko",
                    "boundary_points": localities.CZ_BOUNDARY_POINTS,
                    "flags": ["includeImports", "includeShortTerm"],
                    "currency": "CZK",
                    "location": "exact",
                    "price_from": None,
                    "price_to": None,
                    "area_from": None,
                    "area_to": None,
                }
            )
            shards.append(
                {
                    "kind": "catalog_daily",
                    "portal": "bezrealitky",
                    "shard_key": f"bezrealitky:BYT:{offer}:{size}",
                    "search_url": url,
                }
            )
    return shards


def _field(listing: Any, name: str, default: Any = None) -> Any:
    if listing is None:
        return default
    value = default
    if isinstance(listing, Mapping) or hasattr(listing, "get"):
        try:
            value = listing.get(name, default)
        except Exception:
            value = getattr(listing, name, default)
    else:
        try:
            value = listing[name]
        except Exception:
            value = getattr(listing, name, default)
    return default if value is None else value


def _extras(listing: Any) -> dict[str, Any]:
    extras = _field(listing, "extras", {}) or {}
    if isinstance(extras, str):
        import json

        try:
            extras = json.loads(extras)
        except json.JSONDecodeError:
            extras = {}
    return extras if isinstance(extras, dict) else {}


def listing_offer(listing: Any) -> str:
    extras = _extras(listing)
    offer = str(extras.get("offer") or "").casefold()
    if "pronáj" in offer or "pronaj" in offer:
        return "pronajem"
    if "prodej" in offer:
        return "prodej"
    label = str(_field(listing, "price_label") or "")
    if "měsíc" in label.casefold() or "mesic" in fold(label):
        return "pronajem"
    return "prodej"


def listing_matches_filters(listing: Any, filters: dict[str, Any] | None) -> bool:
    data = filters or {}
    price = _field(listing, "price_czk")
    area = _field(listing, "area_m2")
    locality = str(_field(listing, "locality") or "")
    disposition = str(_field(listing, "disposition") or "")
    url = str(_field(listing, "url") or "")
    source = str(data.get("source") or "")
    if source == "bezrealitky" and "bezrealitky" not in url:
        return False
    if source == "sreality" and "sreality" not in url and "bezrealitky" in url:
        return False
    low = data.get("price_from")
    high = data.get("price_to")
    if low not in (None, "") and price is not None and int(price) < int(low):
        return False
    if high not in (None, "") and price is not None and int(price) > int(high):
        return False
    area_from = data.get("area_from")
    area_to = data.get("area_to")
    if area_from not in (None, "") and area is not None and int(area) < int(area_from):
        return False
    if area_to not in (None, "") and area is not None and int(area) > int(area_to):
        return False
    offers = [str(item).casefold() for item in (data.get("offers") or []) if item]
    if offers:
        got = listing_offer(listing)
        mapped = []
        for item in offers:
            folded = fold(item)
            if "pronaj" in folded:
                mapped.append("pronajem")
            elif "prodej" in folded:
                mapped.append("prodej")
        if mapped and got not in mapped:
            return False
    sizes = [str(item).casefold() for item in (data.get("sizes") or []) if item]
    if sizes:
        disp = disposition.casefold().replace(" ", "")
        ok = False
        for size in sizes:
            label = size.replace("disp_", "").replace("_kk", "+kk").replace("_", "+").casefold()
            if label in disp or size.casefold() in disp:
                ok = True
                break
            if "garson" in size and "garson" in disp:
                ok = True
                break
        if not ok:
            return False
    districts = [str(item) for item in (data.get("districts") or []) if item]
    if districts and not _locality_matches(locality, districts):
        return False
    return True


def listing_matches_search(listing: Any, search_url: str) -> bool:
    url = (search_url or "").strip()
    if not url:
        return False
    if is_bezrealitky(url):
        return listing_matches_filters(listing, bezrealitky_url.parse_url(url))
    return listing_matches_filters(listing, url_builder.parse_url(url))


def _locality_matches(locality: str, districts: list[str]) -> bool:
    text = fold(locality)
    if not text:
        return True
    if any(item == localities.CZECH_OSM or localities.is_czech_country(item, item) for item in districts):
        return True
    if set(districts) >= set(localities.SREALITY_CZECH_REGIONS):
        return True
    for district in districts:
        raw = str(district)
        if raw.startswith("praha-") and raw.split("-")[-1].isdigit():
            number = raw.split("-")[-1]
            if f"praha {number}" in fold(locality) or f"praha-{number}" in text:
                return True
            continue
        if raw in KRAJ_HINTS:
            if any(fold(hint) in text for hint in KRAJ_HINTS[raw]):
                return True
            continue
        label = localities.OSM_LABELS.get(raw) or raw
        needle = fold(str(label).split(",")[0])
        if needle and needle in text:
            return True
    return False


def listing_is_new_for_monitor(listing: Any, monitor: dict[str, Any], prev: dict[str, Any] | None) -> bool:
    if prev is not None:
        return False
    created = _field(listing, "created_on")
    first = monitor.get("created_at")
    if not created or not first:
        return True
    try:
        listing_ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        monitor_ts = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
        if listing_ts.tzinfo is None:
            listing_ts = listing_ts.replace(tzinfo=timezone.utc)
        if monitor_ts.tzinfo is None:
            monitor_ts = monitor_ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return listing_ts >= monitor_ts
