from __future__ import annotations

import math
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from app.sources import PORTAL_LABELS, portal_of as portal_from_sources

MAX_MATCH_M = 45.0
MAX_AREA_DELTA = 1
MAX_AREA_RATIO = 0.03
MAX_PRICE_RATIO = 0.08

_DISP_RE = re.compile(r"(\d+)\s*\+?\s*(kk|1)", re.I)
_NP_RE = re.compile(r"(-?\d+)\s*\.?\s*np\b", re.I)
_PATRO_RE = re.compile(r"(-?\d+)\s*\.?\s*patro", re.I)
_FLOOR_RE = re.compile(r"(-?\d+)")


def listing_key(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "").rstrip("/").lower()
    return f"{host}{path}"


def portal_from_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return "web"
    portal = portal_from_sources(raw)
    if portal != "sreality" or "sreality" in raw.lower():
        return portal
    host = (urlparse(raw).hostname or "").lower().removeprefix("www.")
    if host in {"reality.cz"} or host.endswith(".reality.cz"):
        return "realitycz"
    if host:
        return host.split(".")[0]
    return "web"


def portal_label(portal: str) -> str:
    key = (portal or "").lower()
    if key in PORTAL_LABELS:
        return PORTAL_LABELS[key]
    return (portal or "Web").title()


def _fold(value: str) -> str:
    raw = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in raw if not unicodedata.combining(ch)).casefold().strip()


_ESTATE_LAND = {
    "pozemek",
    "pozemky",
    "pozemku",
    "land",
    "plot",
    "housing",
    "parcela",
}
_ESTATE_HOUSE = {
    "dum",
    "domy",
    "domu",
    "house",
    "vila",
    "villa",
    "chalupa",
    "chata",
    "statek",
}
_ESTATE_FLAT = {"byt", "byty", "bytu", "apartment", "flat"}
_LAND_PATH_RE = re.compile(r"/(?:pozemek|pozemky|pozemku)(?:/|$|\.)", re.I)
_HOUSE_PATH_RE = re.compile(r"/(?:dum|domy|rodinne-domy|domy-a-vily)(?:/|$|\.)", re.I)
_FLAT_PATH_RE = re.compile(r"/(?:byt|byty)(?:/|$|\.)", re.I)
_LAND_SLUG_RE = re.compile(r"(?:^|-)(?:housing|pozemek|pozemky|pozemku|parcela)(?:-|$)", re.I)
_HOUSE_SLUG_RE = re.compile(r"(?:^|-)(?:dum|vila|vilach|chalupa|chata|statek|rodinn)(?:-|$)", re.I)
_FALSE_HOUSE_SLUG_RE = re.compile(r"(?:^|-)(?:u-[a-z0-9-]*domu|koldum)(?:-|$)", re.I)


def _estate_token(value: str) -> str:
    token = _fold(value)
    if not token:
        return ""
    if token in _ESTATE_LAND or "pozem" in token:
        return "pozemek"
    if token in _ESTATE_HOUSE or token.startswith("dom"):
        return "dum"
    if token in _ESTATE_FLAT:
        return "byt"
    return ""


def estate_kind(
    url: str = "",
    extras: Any = None,
    name: str = "",
    disposition: str = "",
) -> str:
    """byt / dum / pozemek from extras, URL, title, or disposition. Default byt.

    Marketing games keep apartment rounds on flats unless the caller asked for mixed.
    Land/plots never look like a pedagogical 2+kk vs 1+kk contrast.
    """
    extras = extras if isinstance(extras, dict) else parse_extras(extras)
    mapped = _estate_token(str(extras.get("estate") or extras.get("estateType") or ""))
    if mapped:
        return mapped

    raw_url = str(url or "")
    path, _, query = raw_url.lower().partition("?")
    query = query.casefold()
    if "estatetype=pozemek" in query or "estatetype=plot" in query:
        return "pozemek"
    if "estatetype=dum" in query or "estatetype=house" in query:
        return "dum"
    if "estatetype=byt" in query or "estatetype=flat" in query:
        return "byt"

    clipped = path.replace("/nemovitosti-byty-domy/", "/")
    if _LAND_PATH_RE.search(clipped) or "pozemky.html" in clipped:
        return "pozemek"
    if (
        _HOUSE_PATH_RE.search(clipped)
        or "domy-k-pronajmu" in clipped
        or "domy-na-prodej" in clipped
        or "/pronajmu/dum" in clipped
        or "/prodam/dum" in clipped
    ):
        return "dum"
    if (
        _FLAT_PATH_RE.search(clipped)
        or "byty-k-pronajmu" in clipped
        or "byty-na-prodej" in clipped
        or "/pronajmu/byt" in clipped
        or "/prodam/byt" in clipped
    ):
        return "byt"

    slug = clipped.rstrip("/").rsplit("/", 1)[-1]
    if _LAND_SLUG_RE.search(slug):
        return "pozemek"
    if _HOUSE_SLUG_RE.search(slug) and not _FALSE_HOUSE_SLUG_RE.search(slug):
        return "dum"

    folded_name = _fold(name)
    if any(token in folded_name for token in ("pozemek", "pozemku", "pozemky", "parcela")):
        return "pozemek"
    if "byt" in folded_name:
        return "byt"
    if (
        any(token in folded_name for token in ("rodinn", "vila", "chalup", "chata"))
        or "domu" in folded_name
        or re.search(r"(?:^|\b)dum(?:\b|$)", folded_name)
    ):
        return "dum"

    disp = _fold(disposition)
    if any(token in disp for token in ("pozem", "housing", "parcela")):
        return "pozemek"
    if disp in {"familyhouse", "villa", "dum", "vila"} or "rodinn" in disp:
        return "dum"
    return "byt"


def offer_kind(url: str = "", extras: Any = None, price_label: str = "") -> str:
    extras = extras if isinstance(extras, dict) else {}
    offer = str(extras.get("offer") or "").casefold()
    path = (url or "").lower()
    label = (price_label or "").casefold()
    if "draz" in offer or "/drazby/" in path or "/drazba/" in path:
        return "auction"
    if offer in {"prodej", "sale"} or "/prodej/" in path or "/prodam/" in path or "nemovitost" in label:
        return "sale"
    if offer in {"pronájem", "pronajem", "rent"} or "/pronajem/" in path or "/pronajmu/" in path or "měsíc" in label or "mesic" in label:
        return "rent"
    return "rent" if "kč" in label else "unknown"


def norm_disposition(value: str) -> str:
    text = _fold(value).replace(" ", "")
    if not text:
        return ""
    if "garson" in text or text == "pokoj":
        return "1kk"
    if "atyp" in text:
        return "atyp"
    match = _DISP_RE.search(text)
    if match:
        kind = "kk" if match.group(2).lower() == "kk" else "1"
        return f"{match.group(1)}+{kind}"
    return text


def floor_token(extras: Any) -> str:
    extras = extras if isinstance(extras, dict) else {}
    for spec in extras.get("specs") or []:
        if not isinstance(spec, dict):
            continue
        label = _fold(str(spec.get("label") or ""))
        if "podlaz" not in label and "patro" not in label:
            continue
        value = str(spec.get("value") or "")
        folded = _fold(value)
        np_match = _NP_RE.search(folded)
        if np_match:
            return np_match.group(1)
        patro_match = _PATRO_RE.search(folded)
        if patro_match:
            try:
                return str(int(patro_match.group(1)) + 1)
            except ValueError:
                return patro_match.group(1)
        if "prizem" in folded:
            return "1"
        match = _FLOOR_RE.search(value)
        if match:
            return match.group(1)
    return ""


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def parse_extras(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        import json

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def fingerprint(row: dict[str, Any]) -> str | None:
    extras = parse_extras(row.get("extras"))
    offer = offer_kind(str(row.get("url") or ""), extras, str(row.get("price_label") or ""))
    disp = norm_disposition(str(row.get("disposition") or ""))
    area = _as_int(row.get("area_m2"))
    lat = _as_float(row.get("lat"))
    lon = _as_float(row.get("lon"))
    if offer == "unknown" or not disp or area is None or lat is None or lon is None:
        return None
    floor = floor_token(extras)
    if not floor:
        return None
    return f"g:{offer}:{disp}:{area}:{round(lat, 4)}:{round(lon, 4)}:{floor}"


def url_canonical(url: str) -> str:
    return f"url:{listing_key(url)}"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def same_listing(left: dict[str, Any], right: dict[str, Any]) -> bool:
    extras_a = parse_extras(left.get("extras"))
    extras_b = parse_extras(right.get("extras"))
    url_a, url_b = str(left.get("url") or ""), str(right.get("url") or "")
    if listing_key(url_a) and listing_key(url_a) == listing_key(url_b):
        return True
    same_portal = portal_from_url(url_a) == portal_from_url(url_b)
    offer_a = offer_kind(url_a, extras_a, str(left.get("price_label") or ""))
    offer_b = offer_kind(url_b, extras_b, str(right.get("price_label") or ""))
    if offer_a != offer_b or offer_a == "unknown":
        return False
    disp_a = norm_disposition(str(left.get("disposition") or ""))
    disp_b = norm_disposition(str(right.get("disposition") or ""))
    if not disp_a or disp_a != disp_b:
        return False
    area_a, area_b = _as_int(left.get("area_m2")), _as_int(right.get("area_m2"))
    if area_a is None or area_b is None:
        return False
    delta = abs(area_a - area_b)
    if delta > MAX_AREA_DELTA and delta / max(area_a, area_b) > MAX_AREA_RATIO:
        return False
    floor_a, floor_b = floor_token(extras_a), floor_token(extras_b)
    if floor_a and floor_b and floor_a != floor_b:
        return False
    lat1, lon1 = _as_float(left.get("lat")), _as_float(left.get("lon"))
    lat2, lon2 = _as_float(right.get("lat")), _as_float(right.get("lon"))
    if None in (lat1, lon1, lat2, lon2):
        return False
    if haversine_m(lat1, lon1, lat2, lon2) > MAX_MATCH_M:
        return False
    price_a, price_b = _as_int(left.get("price_czk")), _as_int(right.get("price_czk"))
    if price_a and price_b and abs(price_a - price_b) / max(price_a, price_b) > MAX_PRICE_RATIO:
        return False
    if floor_a and floor_b:
        return True
    if same_portal and not (price_a and price_b):
        return False
    return True


def listing_identity(row: dict[str, Any]) -> str:
    return (
        str(row.get("canonical_key") or "").strip()
        or str(row.get("listing_key") or "").strip()
        or listing_key(str(row.get("url") or ""))
        or f"{row.get('monitor_id')}:{row.get('id')}"
    )


def link_payload(url: str, *, native_id: Any = None, extras: Any = None, last_seen: str = "", gone: bool = False) -> dict[str, Any]:
    extras = parse_extras(extras)
    agency = str(extras.get("agency") or extras.get("seller") or extras.get("company") or "").strip()
    for spec in extras.get("specs") or []:
        if not isinstance(spec, dict):
            continue
        label = _fold(str(spec.get("label") or ""))
        if any(token in label for token in ("rk", "kancelar", "inzerent", "firma", "makler", "spolecnost")):
            agency = str(spec.get("value") or "").strip() or agency
            break
    portal = portal_from_url(url)
    return {
        "url": (url or "").strip(),
        "url_key": listing_key(url),
        "portal": portal,
        "label": portal_label(portal),
        "native_id": str(native_id) if native_id not in (None, "") else "",
        "agency": agency,
        "last_seen": last_seen,
        "gone": bool(gone),
    }
