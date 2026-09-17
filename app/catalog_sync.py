from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import bazos_url, bezrealitky_url, idnes_url, localities, url_builder
from app.identity import portal_from_url
from app.portal_urls import MODULES as EXTRA_URLS
from app.sources import PORTAL_IDS, is_bazos, is_bezrealitky, is_idnes, portal_of

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

PRAGUE_DISTRICTS = {
    "1": ["stare mesto", "josefov", "mala strana", "hradcany", "nove mesto"],
    "2": ["vinohrady", "nove mesto", "vysehrad", "nusle"],
    "3": ["zizkov", "vinohrady"],
    "4": ["nusle", "podoli", "branik", "hodkovicky", "krc", "lhotka", "kamyk", "kunratice"],
    "5": ["smichov", "kosire", "motol", "radlice", "jinonice", "hlubocepy"],
    "6": ["dejvice", "bubenec", "stresovice", "brevnov", "veleslavin", "vokovice", "liboc", "ruzyne", "lysolaje", "sedlec", "suchdol", "nebusice"],
    "7": ["holesovice", "bubny", "letna", "troja"],
    "8": ["karlin", "liben", "bohnice", "kobylisy", "cimice", "dablice", "dolni chabry", "troja"],
    "9": ["vysocany", "prosek", "strizkov", "hloubetin", "hrdlorezy", "kbely"],
    "10": ["vrsovice", "strasnice", "malesice", "zabehlice", "michle"],
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
    portal = portal_of(raw)
    extra = EXTRA_URLS.get(portal)
    if extra:
        filters = extra.parse_url(raw)
        filters["sort"] = "nejnovejsi"
        return extra.build_url(filters)
    if is_idnes(raw):
        filters = idnes_url.parse_url(raw)
        filters["sort"] = "nejnovejsi"
        return idnes_url.build_url(filters)
    if is_bazos(raw):
        filters = bazos_url.parse_url(raw)
        return bazos_url.build_url(filters)
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
    # Sreality: never crawl whole Praha as one shard (portal page depth cannot cover it).
    # Split Praha into districts and further by disposition so upserts can reach portal totals.
    sreality_regions = [item for item in localities.SREALITY_CZECH_REGIONS if item != "praha"] + [
        f"praha-{i}" for i in range(1, 11)
    ]
    large_regions = {
        "stredocesky-kraj",
        "jihomoravsky-kraj",
        "moravskoslezsky-kraj",
        "ustecky-kraj",
        *[f"praha-{i}" for i in range(1, 11)],
    }
    for offer in ("pronajem", "prodej"):
        for region in sreality_regions:
            size_batches: list[list[str]] = (
                [[size] for size, _label in url_builder.SIZES] if region in large_regions else [[]]
            )
            for sizes in size_batches:
                url = url_builder.build_url(
                    {
                        "source": "sreality",
                        "offers": [offer],
                        "category": "byty",
                        "districts": [region],
                        "sizes": sizes,
                        "sort": "nejnovejsi",
                        "price_from": None,
                        "price_to": None,
                        "area_from": None,
                        "area_to": None,
                    }
                )
                size_key = sizes[0] if sizes else "all"
                shards.append(
                    {
                        "kind": "catalog_daily",
                        "portal": "sreality",
                        "shard_key": f"sreality:byty:{offer}:{region}:{size_key}",
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
    # iDNES stops returning new pages around ~150; Praha-wide searches exceed that.
    # "projekty" is a mixed view of the same ads and is too large to paginate.
    idnes_localities = [item for item in localities.SREALITY_CZECH_REGIONS if item != "praha"] + [
        f"praha-{i}" for i in range(1, 11)
    ]
    idnes_categories = [key for key, _ in idnes_url.CATEGORIES if key != "projekty"]
    for offer in ("pronajem", "prodej", "drazba"):
        for category in idnes_categories:
            for region in idnes_localities:
                url = idnes_url.build_url(
                    {
                        "source": "idnes",
                        "offers": [offer],
                        "category": category,
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
                        "portal": "idnes",
                        "shard_key": f"idnes:{category}:{offer}:{region}",
                        "search_url": url,
                    }
                )
    # Nationwide category crawls cover the whole of Bazos realty (including Praha).
    for offer in ("pronajem", "prodej"):
        for category, _label in bazos_url.CATEGORIES:
            shards.append(
                {
                    "kind": "catalog_daily",
                    "portal": "bazos",
                    "shard_key": f"bazos:{category}:{offer}:cz",
                    "search_url":                     bazos_url.build_url(
                        {
                            "source": "bazos",
                            "offers": [offer],
                            "category": category,
                            "districts": list(localities.SREALITY_CZECH_REGIONS),
                            "sizes": [],
                            "price_from": None,
                            "price_to": None,
                            "radius": 0,
                        }
                    ),
                }
            )
    for portal_id, urls in EXTRA_URLS.items():
        for offer in ("pronajem", "prodej"):
            shards.append(
                {
                    "kind": "catalog_daily",
                    "portal": portal_id,
                    "shard_key": f"{portal_id}:byty:{offer}:cz",
                    "search_url": urls.build_url({"source": portal_id, "offers": [offer], "category": "byty"}),
                }
            )
        if portal_id in {"annonce", "realitycz"}:
            for offer in ("pronajem", "prodej"):
                shards.append(
                    {
                        "kind": "catalog_daily",
                        "portal": portal_id,
                        "shard_key": f"{portal_id}:domy:{offer}:cz",
                        "search_url": urls.build_url(
                            {"source": portal_id, "offers": [offer], "category": "domy"}
                        ),
                    }
                )
    return shards


def sreality_recent_shards() -> list[dict[str, str]]:
    """Small newest-first shards for continuous Sreality catalog refresh (~60s)."""
    shards: list[dict[str, str]] = []
    for offer in ("pronajem", "prodej"):
        for size, _label in url_builder.SIZES:
            url = url_builder.build_url(
                {
                    "source": "sreality",
                    "offers": [offer],
                    "category": "byty",
                    "districts": list(localities.SREALITY_CZECH_REGIONS),
                    "sizes": [size],
                    "sort": "nejnovejsi",
                    "price_from": None,
                    "price_to": None,
                    "area_from": None,
                    "area_to": None,
                }
            )
            shards.append(
                {
                    "kind": "catalog_recent",
                    "portal": "sreality",
                    "shard_key": f"sreality:recent:{offer}:{size}",
                    "search_url": url,
                }
            )
    return shards


def extra_portal_recent_shards() -> list[dict[str, str]]:
    """Newest-first nationwide apartment shards for every non-Sreality portal."""
    shards: list[dict[str, str]] = []
    extras = {
        "bezrealitky": lambda offer: bezrealitky_url.build_url(
            {
                "source": "bezrealitky",
                "offers": ["PRONAJEM" if offer == "pronajem" else "PRODEJ"],
                "estates": ["BYT"],
                "sizes": [],
                "districts": [localities.CZECH_OSM],
                "osm_value": "Česko",
                "boundary_points": localities.CZ_BOUNDARY_POINTS,
                "flags": ["includeImports", "includeShortTerm"],
                "currency": "CZK",
                "location": "exact",
                "sort": "TIMEORDER_DESC",
                "price_from": None,
                "price_to": None,
                "area_from": None,
                "area_to": None,
            }
        ),
        "idnes": lambda offer: idnes_url.build_url(
            {
                "source": "idnes",
                "offers": [offer],
                "category": "byty",
                "districts": list(localities.SREALITY_CZECH_REGIONS),
                "sizes": [],
                "sort": "nejnovejsi",
                "price_from": None,
                "price_to": None,
                "area_from": None,
                "area_to": None,
            }
        ),
        "bazos": lambda offer: bazos_url.build_url(
            {
                "source": "bazos",
                "offers": [offer],
                "category": "byt",
                "districts": list(localities.SREALITY_CZECH_REGIONS),
                "sizes": [],
                "price_from": None,
                "price_to": None,
                "radius": 0,
            }
        ),
    }
    extras.update(
        {
            key: (lambda offer, urls=urls: urls.build_url({"offers": [offer], "category": "byty"}))
            for key, urls in EXTRA_URLS.items()
        }
    )
    for portal, builder in extras.items():
        for offer in ("pronajem", "prodej"):
            shards.append(
                {
                    "kind": "catalog_recent",
                    "portal": portal,
                    "shard_key": f"{portal}:recent:{offer}:byty",
                    "search_url": builder(offer),
                }
            )
    # Annonce / Reality.cz houses use a different list path; one extra newest shard per offer.
    for portal_id in ("annonce", "realitycz"):
        for offer in ("pronajem", "prodej"):
            shards.append(
                {
                    "kind": "catalog_recent",
                    "portal": portal_id,
                    "shard_key": f"{portal_id}:recent:{offer}:domy",
                    "search_url": EXTRA_URLS[portal_id].build_url({"offers": [offer], "category": "domy"}),
                }
            )
    return shards


def recent_shards() -> list[dict[str, str]]:
    """Minute NewDiscovery shards: Sreality size slices + newest apartments on every other portal."""
    return sreality_recent_shards() + extra_portal_recent_shards()


# Portals that already return list cards from this app's parsers.
# M&M stays out of the healthy set until a live residential proxy actually yields cards.
HEALTHY_DISCOVERY_PORTALS = frozenset(
    {
        "sreality",
        "idnes",
        "bazos",
        "bezrealitky",
        "annonce",
        "remax",
        "ceskereality",
        "realitycz",
        "ulovdomov",
    }
)


def order_recent_shards(shards: list[dict[str, str]]) -> list[dict[str, str]]:
    """Nationwide extras first so a global deadline cannot starve their page 1 behind 26 Sreality sizes."""
    extras = [item for item in shards if (item.get("portal") or "") != "sreality"]
    sreality = [item for item in shards if (item.get("portal") or "") == "sreality"]
    return extras + sreality


def filter_ready_shards(shards: list[dict[str, str]], cooldown: Any | None) -> list[dict[str, str]]:
    """Drop shards whose portal is already in a per-portal cooldown (no HTTP, no gate slot)."""
    if cooldown is None:
        return list(shards)
    ready: list[dict[str, str]] = []
    for item in shards:
        portal = (item.get("portal") or "").strip().lower()
        if portal and hasattr(cooldown, "active") and cooldown.active(portal):
            continue
        ready.append(item)
    return ready


def prepare_discovery_shards(cooldown: Any | None = None) -> list[dict[str, str]]:
    """NewDiscovery batch: extras first, skip portals already cooling."""
    return filter_ready_shards(order_recent_shards(recent_shards()), cooldown)


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
    if "draz" in offer:
        return "drazba"
    if "pronáj" in offer or "pronaj" in offer:
        return "pronajem"
    if "prodej" in offer:
        return "prodej"
    label = str(_field(listing, "price_label") or "")
    if "měsíc" in label.casefold() or "mesic" in fold(label):
        return "pronajem"
    path = str(_field(listing, "url") or "").lower()
    if "/drazba/" in path:
        return "drazba"
    if (
        "/pronajmu/" in path
        or "/pronajem/" in path
        or "byty-k-pronajmu" in path
        or "typ-nabidky=pronajem" in path
        or "sale=2" in path
    ):
        return "pronajem"
    if (
        "/prodam/" in path
        or "/prodej/" in path
        or "byty-na-prodej" in path
        or "typ-nabidky=prodej" in path
        or "sale=1" in path
    ):
        return "prodej"
    return "prodej"


def listing_matches_filters(listing: Any, filters: dict[str, Any] | None, *, ignore_source: bool = False) -> bool:
    data = filters or {}
    price = _field(listing, "price_czk")
    area = _field(listing, "area_m2")
    locality = str(_field(listing, "locality") or "")
    disposition = str(_field(listing, "disposition") or "")
    url = str(_field(listing, "url") or "")
    source = str(data.get("source") or "")
    if not ignore_source and source in PORTAL_IDS:
        if portal_of(url) != source:
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
            elif "draz" in folded:
                mapped.append("drazba")
            elif "prodej" in folded:
                mapped.append("prodej")
        if mapped and got not in mapped:
            return False
    sizes = [str(item).casefold() for item in (data.get("sizes") or []) if item]
    if sizes:
        disp = fold(disposition).replace(" ", "")
        if disp and not _disposition_matches(disp, sizes):
            return False
    districts = [str(item) for item in (data.get("districts") or []) if item]
    if districts and not _locality_matches(locality, districts):
        return False
    return True


def listing_matches_search(listing: Any, search_url: str, *, ignore_source: bool = False) -> bool:
    url = (search_url or "").strip()
    if not url:
        return False
    portal = portal_of(url)
    extra = EXTRA_URLS.get(portal)
    if extra:
        return listing_matches_filters(listing, extra.parse_url(url), ignore_source=ignore_source)
    if is_idnes(url):
        return listing_matches_filters(listing, idnes_url.parse_url(url), ignore_source=ignore_source)
    if is_bazos(url):
        return listing_matches_filters(listing, bazos_url.parse_url(url), ignore_source=ignore_source)
    if is_bezrealitky(url):
        return listing_matches_filters(listing, bezrealitky_url.parse_url(url), ignore_source=ignore_source)
    return listing_matches_filters(listing, url_builder.parse_url(url), ignore_source=ignore_source)


def normalize_portals(value: str | None) -> str:
    raw = (value or "all").strip().lower()
    if raw in PORTAL_IDS:
        return raw
    return "all"


def listing_matches_monitor(listing: Any, monitor: dict[str, Any]) -> bool:
    listing_url = str(_field(listing, "url") or "").lower()
    listing_portal = portal_from_url(listing_url)
    for target in monitor_search_targets(monitor):
        if target["portal"] != listing_portal:
            continue
        if listing_matches_search(listing, target["search_url"], ignore_source=True):
            return True
    return False


def monitor_search_targets(monitor: dict[str, Any]) -> list[dict[str, str]]:
    from app.filter_bridge import search_urls_for_portals

    url = normalize_search_url(monitor.get("search_url") or "")
    if not url:
        return []
    portals = normalize_portals(monitor.get("portals"))
    urls = search_urls_for_portals(url)
    return [
        {"portal": portal, "search_url": search}
        for portal, search in urls.items()
        if search and portals in {"all", portal}
    ]


def _disposition_matches(disp: str, sizes: list[str]) -> bool:
    compact = disp.replace("pokoj", "").replace("bytu", "").replace("-", "+")
    for size in sizes:
        raw = size.replace("disp_", "")
        if "6-a-vice" in raw or "6-kk-a-vetsi" in raw or raw in {"disp_6_1", "disp_6_kk", "disp_7_1", "disp_7_kk"}:
            if any(token in compact for token in ("6+", "7+", "8+", "9+", "6kk", "7kk")):
                return True
            continue
        if "atyp" in raw or raw in {"ostatni", "disp_ostatni"}:
            if "atyp" in compact:
                return True
            continue
        if "garson" in raw or raw == "pokoj":
            if "garson" in compact or "pokoj" in fold(disp):
                return True
            continue
        label = raw.replace("_kk", "+kk").replace("_", "+")
        if label in compact or raw.replace("_", "") in compact.replace("+", ""):
            return True
    return False


def _prague_district_number(district: str) -> str | None:
    raw = str(district)
    slug = localities.OSM_TO_SREALITY.get(raw) or raw
    if slug.startswith("praha-") and slug.split("-")[-1].isdigit():
        return slug.split("-")[-1]
    return None


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
        number = _prague_district_number(raw)
        if number:
            if f"praha {number}" in text or f"praha-{number}" in text or f"praha{number}" in text.replace(" ", ""):
                return True
            if any(fold(part) in text for part in PRAGUE_DISTRICTS.get(number, [])):
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
