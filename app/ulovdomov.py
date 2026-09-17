"""UlovDomov scraper — JSON API first, sitemap list cards, worker detail hydrate."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.block_page import PortalBlocked
from app.html_listing import HtmlPortalClient, abs_url, listing_from_card, numeric_id, parse_disposition, parse_price
from app.portal_urls import ulovdomov_url
from app.sreality import Listing, ListingGone, format_price

SITE = ulovdomov_url.site
API = "https://ud.api.ulovdomov.cz/v1/offer/find"
DETAIL_API = "https://ud.api.ulovdomov.cz/v2/offer/detail"
SITEMAP_OFFERS = f"{SITE}/sitemap-offers.xml"
DETAIL_TIMEOUT_SEC = 8.0
DETAIL_CONSECUTIVE_FAILS = 2
BOUNDS = {"northEast": {"lat": 51.06, "lng": 18.87}, "southWest": {"lat": 48.55, "lng": 12.09}}
HREF_RE = re.compile(
    r'href="((?:https://www\.ulovdomov\.cz)?/(?:pronajem|prodej)/[^"]+/\d+[^"]*)"',
    re.I,
)
HREF2_RE = re.compile(r'href="((?:https://www\.ulovdomov\.cz)?/inzerat/[^"]+)"', re.I)
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
INZERAT_RE = re.compile(r"/inzerat/([^/]+)/(\d+)/?$", re.I)
LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
SLUG_KK_RE = re.compile(r"(?i)(?:^|-)(\d+)-kk(?:-|$)")
SLUG_PLUS1_RE = re.compile(r"(?i)(?:^|-)(\d+)-1(?:-|$)")
OFFER_KEYS = ("offers", "results", "items", "list", "adverts", "estates", "hits")
SITEMAP_TTL_SEC = 480.0
DISPOSITION_NAMES = {
    "onepluskitchenette": "1+kk",
    "oneplusone": "1+1",
    "twopluskitchenette": "2+kk",
    "twoplusone": "2+1",
    "threepluskitchenette": "3+kk",
    "threeplusone": "3+1",
    "fourpluskitchenette": "4+kk",
    "fourplusone": "4+1",
    "fivepluskitchenette": "5+kk",
    "fiveplusone": "5+1",
    "sixpluskitchenette": "6+kk",
    "sixplusone": "6+1",
    "studio": "1+kk",
    "garsonka": "1+kk",
    "garsoniera": "1+kk",
    "atypical": "atypický",
    "atypicky": "atypický",
    "room": "pokoj",
    "pokoj": "pokoj",
    "familyhouse": "dům",
    "villa": "vila",
    "fiveplusrooms": "5+",
}
HOUSE_SLUG_RE = re.compile(
    r"(?:^|-)(?:dum|vila|vilach|chalupa|chata|statek|rodinn)(?:-|$)",
    re.I,
)
FALSE_HOUSE_SLUG_RE = re.compile(r"(?:^|-)(?:u-[a-z0-9-]*domu|koldum)(?:-|$)", re.I)
LAND_SLUG_RE = re.compile(
    r"(?:^|-)(?:housing|pozemek|pozemky|pozemku|parcela)(?:-|$)",
    re.I,
)
SLUG_STOP = {
    "kk",
    "housing",
    "byt",
    "bytu",
    "dum",
    "vila",
    "vilach",
    "chalupa",
    "chata",
    "statek",
    "rodinny",
    "fiveplusrooms",
}
HYDRATE_BUCKETS = ("rent", "sale", "rent_house", "sale_house", "other")

_sitemap_rows: list[tuple[str, str, int, str]] | None = None
_sitemap_at = 0.0
_sitemap_ok = False
_sitemap_lock: asyncio.Lock | None = None
_build_id = ""


def sitemap_cache_fresh() -> bool:
    return bool(_sitemap_ok and _sitemap_rows is not None and (time.monotonic() - _sitemap_at) < SITEMAP_TTL_SEC)


def _sitemap_guard() -> asyncio.Lock:
    global _sitemap_lock
    if _sitemap_lock is None:
        _sitemap_lock = asyncio.Lock()
    return _sitemap_lock


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
        return float(value)
    except (TypeError, ValueError):
        return None


def _looks_like_offer(item: dict[str, Any]) -> bool:
    if not item:
        return False
    if item.get("id") or item.get("offerId") or item.get("seoId"):
        return True
    slug = str(item.get("seoUrl") or item.get("slug") or item.get("url") or "")
    return bool(slug) and ("/" in slug or slug.isdigit())


def _collect_rows(payload: Any, acc: list[dict[str, Any]] | None = None, depth: int = 0) -> list[dict[str, Any]]:
    rows = acc if acc is not None else []
    if depth > 6:
        return rows
    if isinstance(payload, list):
        dicts = [item for item in payload if isinstance(item, dict)]
        if dicts and sum(1 for item in dicts if _looks_like_offer(item)) >= max(1, len(dicts) // 2):
            rows.extend(item for item in dicts if _looks_like_offer(item))
            return rows
        for item in payload[:40]:
            _collect_rows(item, rows, depth + 1)
        return rows
    if not isinstance(payload, dict):
        return rows
    for key in OFFER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            _collect_rows(value, rows, depth + 1)
        elif isinstance(value, dict):
            _collect_rows(value, rows, depth + 1)
    data = payload.get("data")
    if data is not None:
        _collect_rows(data, rows, depth + 1)
    props = payload.get("pageProps") or payload.get("props")
    if props is not None and props is not payload:
        _collect_rows(props, rows, depth + 1)
    state = payload.get("dehydratedState") or payload.get("initialState")
    if isinstance(state, dict):
        queries = state.get("queries")
        if isinstance(queries, list):
            for query in queries:
                if isinstance(query, dict):
                    _collect_rows(query.get("state") or query.get("data") or query, rows, depth + 1)
        else:
            _collect_rows(state, rows, depth + 1)
    return rows


def offers_from_payload(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict):
        if isinstance(payload, list):
            rows = [item for item in payload if isinstance(item, dict) and _looks_like_offer(item)]
            return rows, len(rows)
        return [], 0
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(payload.get("data"), list):
        rows = [item for item in payload["data"] if isinstance(item, dict)]
        total = _as_int(payload.get("total") or payload.get("count")) or len(rows)
        return rows, total
    for key in OFFER_KEYS:
        rows = data.get(key) if isinstance(data, dict) else None
        if isinstance(rows, dict):
            nested = rows.get("offers") or rows.get("items") or rows.get("results")
            if isinstance(nested, list):
                rows = nested
        if isinstance(rows, list):
            total = _as_int(
                (data.get("total") if isinstance(data, dict) else None)
                or payload.get("total")
                or payload.get("count")
                or (data.get("count") if isinstance(data, dict) else None)
            ) or len(rows)
            return [item for item in rows if isinstance(item, dict)], total
    collected = _collect_rows(payload)
    if collected:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in collected:
            key = str(item.get("id") or item.get("seoUrl") or item.get("url") or len(unique))
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        total = _as_int(payload.get("total") or payload.get("count") or (data.get("count") if isinstance(data, dict) else None)) or len(unique)
        return unique, total
    return [], _as_int(payload.get("count") or (data.get("count") if isinstance(data, dict) else None)) or 0


def reset_ulov_caches() -> None:
    global _sitemap_rows, _sitemap_at, _sitemap_ok, _build_id
    _sitemap_rows = None
    _sitemap_at = 0.0
    _sitemap_ok = False
    _build_id = ""


def offer_from_inzerat_slug(slug: str) -> str:
    folded = (slug or "").casefold().lstrip("-")
    if folded.startswith("pronajem"):
        return "pronajem"
    if folded.startswith("prodej"):
        return "prodej"
    if folded.startswith("spolubydleni"):
        return "spolubydleni"
    # Bare / leading-dash slugs on sitemap-offers are sales (flats, houses, land).
    return "prodej"


def estate_from_inzerat_slug(slug: str) -> str:
    """byt vs dum vs pozemek from the sitemap slug. 'housing' is land, not a house."""
    folded = (slug or "").casefold()
    if LAND_SLUG_RE.search(folded):
        return "pozemek"
    if not folded or FALSE_HOUSE_SLUG_RE.search(folded):
        return "byt"
    if HOUSE_SLUG_RE.search(folded) or folded.endswith("-dum"):
        return "dum"
    return "byt"


def hydrate_bucket(offer: str, slug: str) -> str:
    estate = estate_from_inzerat_slug(slug)
    token = (offer or "").casefold()
    if token in {"pronajem", "pronájem", "rent"}:
        return "rent_house" if estate == "dum" else "rent"
    if token in {"prodej", "sale"}:
        return "sale_house" if estate == "dum" else "sale"
    return "other"


def listing_hydrate_bucket(listing: Listing) -> str:
    extras = listing.extras or {}
    slug = ""
    match = INZERAT_RE.search(listing.url or "")
    if match:
        slug = match.group(1)
    offer = str(extras.get("offer") or "").casefold()
    if "pronáj" in offer or "pronaj" in offer or offer == "rent":
        offer_key = "pronajem"
    elif "prodej" in offer or offer == "sale":
        offer_key = "prodej"
    else:
        offer_key = offer_from_inzerat_slug(slug)
    estate = str(extras.get("estate") or "").casefold()
    if estate in {"dům", "dum", "domu", "vila"}:
        slug_for_bucket = f"{slug}-dum" if estate_from_inzerat_slug(slug) != "dum" else slug
        return hydrate_bucket(offer_key, slug_for_bucket)
    return hydrate_bucket(offer_key, slug)


def interleave_hydrate(buckets: dict[str, list], limit: int) -> list:
    """Round-robin rent / sale / houses so a tick is not all newest rent flats."""
    cap = max(1, int(limit or 1))
    out: list = []
    order = HYDRATE_BUCKETS
    while len(out) < cap:
        progressed = False
        for key in order:
            rows = buckets.get(key) or []
            if not rows:
                continue
            out.append(rows.pop(0))
            progressed = True
            if len(out) >= cap:
                break
        if not progressed:
            break
    return out


def parse_sitemap_offers(xml: str) -> list[tuple[str, str, int, str]]:
    rows: list[tuple[str, str, int, str]] = []
    seen: set[int] = set()
    for loc in LOC_RE.findall(xml or ""):
        url = abs_url(loc.strip(), SITE)
        match = INZERAT_RE.search(url)
        if not match:
            continue
        slug, raw_id = match.group(1), match.group(2)
        listing_id = numeric_id(raw_id, url)
        if listing_id in seen:
            continue
        seen.add(listing_id)
        rows.append((url, offer_from_inzerat_slug(slug), listing_id, slug))
    rows.sort(key=lambda item: item[2], reverse=True)
    return rows


@dataclass
class HydrateResult:
    listings: list[Listing] = field(default_factory=list)
    attempted: int = 0
    priced: int = 0
    imaged: int = 0
    gone: int = 0
    failed: int = 0
    aborted: str | None = None
    blocked: PortalBlocked | None = None
    gone_ids: list[int] = field(default_factory=list)
    kinds: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "priced": self.priced,
            "imaged": self.imaged,
            "gone": self.gone,
            "failed": self.failed,
            "aborted": self.aborted,
            "success_rate": round(self.priced / self.attempted, 3) if self.attempted else 0.0,
            "kinds": self.kinds,
        }


def needs_hydrate(listing: Listing | None) -> bool:
    if listing is None:
        return False
    if listing.price_czk in (None, 0):
        return True
    return not bool(listing.image_url)


def _place_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "").strip()
    return str(value or "").strip()


def _param_map(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name not in out:
            out[name] = item.get("value")
    return out


def _disposition_from_detail(raw: dict[str, Any], params: dict[str, Any], title: str) -> str:
    named = str(params.get("disposition") or raw.get("disposition") or "").replace("_", "").replace("-", "")
    mapped = DISPOSITION_NAMES.get(named.casefold())
    if mapped:
        return mapped
    if isinstance(raw.get("disposition"), dict):
        label = str(raw["disposition"].get("name") or raw["disposition"].get("label") or "")
        if label:
            return parse_disposition(label) or label
    return parse_disposition(title) or parse_disposition(named)


def _photos_from_detail(raw: dict[str, Any]) -> list[str]:
    photos: list[str] = []
    for key in ("photos", "images", "photoUrls"):
        rows = raw.get(key) or []
        if not isinstance(rows, list):
            continue
        for item in rows:
            if isinstance(item, str):
                url = abs_url(item, SITE)
            elif isinstance(item, dict):
                url = abs_url(str(item.get("path") or item.get("url") or item.get("src") or ""), SITE)
            else:
                url = ""
            if url and url not in photos:
                photos.append(url)
    return photos


def _offer_from_detail(raw: dict[str, Any], params: dict[str, Any], fallback: str = "") -> str:
    token = str(raw.get("offerTypeId") or params.get("offerType") or fallback or "").casefold()
    if token in {"rent", "1", "pronajem", "pronájem"}:
        return "pronajem"
    if token in {"sale", "2", "prodej"}:
        return "prodej"
    if token in {"coliving", "spolubydleni"}:
        return "spolubydleni"
    seo = str(raw.get("seo") or raw.get("absoluteUrl") or "")
    return offer_from_inzerat_slug(seo) if seo else (fallback or "pronajem")


def listing_from_detail_payload(payload: Any, *, offer: str = "", keep_url: str = "") -> Listing | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(raw, dict) or not raw:
        return None
    if payload.get("success") is False and not raw.get("id"):
        return None
    params = _param_map(raw.get("parameters"))
    listing_id = numeric_id(raw.get("id") or raw.get("offerId"), keep_url or str(raw.get("seo") or ""))
    if not listing_id:
        return None
    offer_kind = _offer_from_detail(raw, params, offer)
    title = str(raw.get("title") or raw.get("name") or raw.get("headline") or "").strip()
    locality_parts = [
        part
        for part in (
            _place_name(raw.get("village")),
            _place_name(raw.get("villagePart")),
            str(params.get("localityCity") or "").strip(),
            _place_name(raw.get("district")),
        )
        if part
    ]
    locality = ", ".join(dict.fromkeys(locality_parts))
    price = raw.get("rentalPrice") if raw.get("rentalPrice") not in (None, "") else raw.get("price")
    if isinstance(price, dict):
        price_czk = _as_int(price.get("value") or price.get("amount") or price.get("czk"))
    else:
        price_czk = _as_int(price)
    if price_czk is None:
        price_czk = _as_int(params.get("price"))
    price_unit = str(raw.get("priceUnit") or params.get("priceUnit") or "").casefold()
    if offer_kind == "pronajem":
        unit = "měsíc"
    elif price_unit in {"persqm", "per_sqm", "m2", "m²"}:
        unit = "m²"
    else:
        unit = "ks"
    price_label = format_price(price_czk, unit)
    note = str(raw.get("priceNote") or params.get("priceTextNote") or "").strip()
    if note and price_czk is not None:
        price_label = f"{price_label} {note}".strip()
    photos = _photos_from_detail(raw)
    gps = raw.get("geoCoordinates") or raw.get("gps") or raw.get("coordinates") or {}
    lat = _as_float(raw.get("lat") or (gps.get("lat") if isinstance(gps, dict) else None))
    lon = _as_float(raw.get("lng") or raw.get("lon") or (gps.get("lng") if isinstance(gps, dict) else None))
    area = _as_int(params.get("floorArea") or raw.get("area") or raw.get("floorArea") or raw.get("surface"))
    disp = _disposition_from_detail(raw, params, title)
    slug = str(raw.get("seo") or "").strip().strip("/")
    if keep_url:
        url = keep_url
    elif slug:
        url = f"{SITE}/inzerat/{slug}/{listing_id}"
    else:
        url = str(raw.get("absoluteUrl") or f"{SITE}/inzerat/{listing_id}").split("#")[0]
    extras = {"source": "offer_detail"}
    ptype = str(params.get("propertyType") or raw.get("propertyType") or "").casefold()
    disp_token = str(params.get("disposition") or "").replace("_", "").replace("-", "").casefold()
    folded_title = title.casefold()
    if ptype in {"house", "villa"} or disp_token in {"familyhouse", "villa"} or "dům" in folded_title:
        estate = "dum"
    elif ptype in {"land", "plot", "housing"}:
        estate = "pozemek"
    else:
        estate = "byt"
    noun = {"dum": "domu", "pozemek": "pozemku"}.get(estate, "bytu")
    listing = listing_from_card(
        listing_id=listing_id,
        name=title or f"{'Pronájem' if offer_kind == 'pronajem' else 'Prodej'} {noun}",
        url=url,
        price_czk=price_czk,
        price_label=price_label,
        locality=locality,
        disposition=disp,
        area_m2=area,
        image_url=photos[0] if photos else None,
        photos=photos,
        offer=offer_kind,
        estate=estate,
        lat=lat,
        lon=lon,
        description=str(raw.get("description") or "").strip() or None,
        extras=extras,
    )
    return listing


def merge_detail(listing: Listing, detailed: Listing) -> Listing:
    """Fill price/photos/geo from detail without changing the catalog URL key."""
    listing.price_czk = detailed.price_czk if detailed.price_czk not in (None, 0) else listing.price_czk
    if detailed.price_label and detailed.price_label != "Cena neuvedena":
        listing.price_label = detailed.price_label
    if detailed.image_url:
        listing.image_url = detailed.image_url
    if detailed.photos:
        listing.photos = detailed.photos
    if detailed.lat is not None:
        listing.lat = detailed.lat
    if detailed.lon is not None:
        listing.lon = detailed.lon
    if detailed.area_m2 is not None:
        listing.area_m2 = detailed.area_m2
    if detailed.disposition:
        listing.disposition = detailed.disposition
    if detailed.locality:
        listing.locality = detailed.locality
    if detailed.name:
        listing.name = detailed.name
    if detailed.description:
        listing.description = detailed.description
    extras = dict(listing.extras or {})
    extras.update(detailed.extras or {})
    extras.pop("sitemap_card", None)
    extras["source"] = "offer_detail"
    listing.extras = extras
    return listing


def fields_from_inzerat_slug(slug: str, offer: str) -> tuple[str, str, str]:
    raw = (slug or "").strip().lstrip("-")
    for prefix in ("pronajem-", "prodej-", "spolubydleni-"):
        if raw.casefold().startswith(prefix):
            raw = raw[len(prefix) :]
            break
    disp = ""
    kk = SLUG_KK_RE.search(f"-{raw}-")
    if kk:
        disp = f"{kk.group(1)}+kk"
    else:
        plus = SLUG_PLUS1_RE.search(f"-{raw}-")
        if plus:
            disp = f"{plus.group(1)}+1"
    words = [part for part in raw.replace("-", " ").split() if part]
    locality_parts: list[str] = []
    for part in words:
        folded = part.casefold()
        if folded in SLUG_STOP or folded.isdigit():
            break
        if "+" in folded:
            break
        locality_parts.append(part)
        if len(locality_parts) >= 4:
            break
    locality = " ".join(word[:1].upper() + word[1:] for word in locality_parts if word)
    label = "Pronájem" if offer == "pronajem" else "Prodej"
    estate = estate_from_inzerat_slug(slug)
    estate_word = {"dum": "domu", "pozemek": "pozemku"}.get(estate, "bytu")
    name = f"{label} {estate_word} {disp}".strip() if disp else f"{label} {estate_word}".strip()
    if locality:
        name = f"{name}, {locality}"
    if not disp:
        disp = parse_disposition(raw.replace("-", " "))
    return name, locality, disp


class UlovdomovClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "page"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _estate_key(self) -> str:
        path = (self.search_url or "").lower()
        if "/pozem" in path:
            return "pozemek"
        if "/domy" in path or "/dum/" in path or "rodinne-domy" in path:
            return "dum"
        return "byt"

    def _offer_type_id(self) -> int:
        return 2 if self._context() == "prodej" else 1

    def _offer_for_listing(self, listing: Listing) -> str:
        extras = listing.extras or {}
        token = str(extras.get("offer") or "").casefold()
        if "pronáj" in token or "pronaj" in token or token == "rent":
            return "pronajem"
        if "prodej" in token or token == "sale":
            return "prodej"
        match = INZERAT_RE.search(listing.url or "")
        if match:
            return offer_from_inzerat_slug(match.group(1))
        return self._context()

    def _find_body(self, page: int) -> dict[str, Any]:
        return {
            "offerTypeId": self._offer_type_id(),
            "bounds": BOUNDS,
            "noCommitionFee": None,
            "dispositions": [],
            "area": {"from": None, "to": None},
            "price": {"from": None, "to": None},
            "furnishing": [],
            "conveniences": [],
            "isBalcony": None,
            "pageIndex": max(0, page - 1),
            "offerAge": None,
            "specialFlag": None,
            "sorting": "latest",
        }

    def listing_from_offer(self, raw: dict[str, Any], offer: str) -> Listing | None:
        listing_id = raw.get("id") or raw.get("offerId") or raw.get("seoId")
        slug = str(raw.get("seoUrl") or raw.get("slug") or raw.get("url") or "").strip()
        if not listing_id and not slug:
            return None
        if slug.startswith("http"):
            url = slug.split("#")[0]
        elif slug.startswith("/"):
            url = abs_url(slug, SITE)
        elif slug:
            url = f"{SITE}/{offer}/{slug.strip('/')}"
        else:
            url = f"{SITE}/{offer}/byt/{listing_id}"
        title = str(raw.get("title") or raw.get("name") or raw.get("headline") or "").strip()
        locality = ""
        address = raw.get("address")
        if isinstance(address, dict):
            locality = ", ".join(
                str(address.get(key) or "") for key in ("street", "city", "district") if address.get(key)
            ).strip(", ")
        elif isinstance(address, str):
            locality = address
        location = raw.get("location")
        if not locality and isinstance(location, dict):
            locality = str(location.get("name") or location.get("city") or "")
        locality = locality or str(raw.get("locality") or raw.get("city") or "")
        price = raw.get("price")
        if isinstance(price, dict):
            price_czk = _as_int(price.get("amount") or price.get("value") or price.get("czk"))
            price_label = str(price.get("label") or "")
        else:
            price_czk = _as_int(price)
            price_label = str(raw.get("priceLabel") or "")
        photos: list[str] = []
        for key in ("photos", "images", "photoUrls"):
            rows = raw.get(key) or []
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, str):
                        photos.append(abs_url(item, SITE))
                    elif isinstance(item, dict):
                        photos.append(abs_url(str(item.get("url") or item.get("src") or ""), SITE))
        photos = [item for item in photos if item]
        gps = raw.get("gps") or raw.get("coordinates") or {}
        lat = _as_float(raw.get("lat") or (gps.get("lat") if isinstance(gps, dict) else None))
        lon = _as_float(raw.get("lng") or raw.get("lon") or (gps.get("lng") if isinstance(gps, dict) else None))
        area = _as_int(raw.get("area") or raw.get("floorArea") or raw.get("surface"))
        disp = str(raw.get("disposition") or raw.get("dispositions") or "")
        if isinstance(raw.get("disposition"), dict):
            disp = str(raw["disposition"].get("name") or raw["disposition"].get("label") or "")
        return listing_from_card(
            listing_id=numeric_id(listing_id, url),
            name=title or f"{'Pronájem' if offer == 'pronajem' else 'Prodej'} bytu",
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=str(locality or ""),
            disposition=disp,
            area_m2=area,
            image_url=photos[0] if photos else None,
            photos=photos,
            offer=offer,
            lat=lat,
            lon=lon,
        )

    def _listings_from_rows(self, rows: list[dict[str, Any]], offer: str) -> list[Listing]:
        listings: list[Listing] = []
        seen: set[str] = set()
        for raw in rows:
            listing = self.listing_from_offer(raw, offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                listings.append(listing)
        return listings

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        match = NEXT_DATA_RE.search(html or "")
        if match:
            try:
                payload = json.loads(match.group(1))
            except json.JSONDecodeError:
                payload = {}
            props = payload.get("props", {}).get("pageProps", payload.get("pageProps") or payload)
            rows, _total = offers_from_payload(props if isinstance(props, dict) else {})
            if not rows and isinstance(payload, dict):
                rows, _total = offers_from_payload(payload)
            items.extend(self._listings_from_rows(rows, offer))
        if items:
            return items
        hrefs = HREF_RE.findall(html or "") + HREF2_RE.findall(html or "")
        for href in hrefs:
            url = abs_url(href, SITE)
            if url in seen:
                continue
            seen.add(url)
            idx = html.find(href)
            window = html[max(0, idx - 200) : idx + 500] if idx >= 0 else href
            price_czk, price_label = parse_price(window)
            title_m = re.search(r">(Pronájem[^<]+|Prodej[^<]+)<", window)
            items.append(
                listing_from_card(
                    listing_id=numeric_id(url, url),
                    name=title_m.group(1) if title_m else url.rstrip("/").split("/")[-1].replace("-", " "),
                    url=url,
                    price_czk=price_czk,
                    price_label=price_label,
                    locality="",
                    offer=offer,
                )
            )
        return items

    def listing_from_sitemap_url(self, url: str, offer: str, slug: str = "", listing_id: int | None = None) -> Listing | None:
        match = INZERAT_RE.search(url or "")
        if match:
            slug = slug or match.group(1)
            listing_id = listing_id or numeric_id(match.group(2), url)
        if not listing_id:
            return None
        name, locality, disp = fields_from_inzerat_slug(slug, offer)
        listing = listing_from_card(
            listing_id=int(listing_id),
            name=name,
            url=url,
            price_czk=None,
            price_label="",
            locality=locality,
            disposition=disp,
            offer=offer,
            estate=estate_from_inzerat_slug(slug),
            extras={"sitemap_card": True, "source": "sitemap"},
        )
        return listing

    async def _load_sitemap_rows(self) -> list[tuple[str, str, int, str]]:
        global _sitemap_rows, _sitemap_at, _sitemap_ok
        if sitemap_cache_fresh():
            return _sitemap_rows or []
        async with _sitemap_guard():
            if sitemap_cache_fresh():
                return _sitemap_rows or []
            now = time.monotonic()
            try:
                response = await self._client.get(
                    SITEMAP_OFFERS,
                    headers={"Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8"},
                    timeout=15.0,
                )
            except Exception:
                _sitemap_ok = False
                return _sitemap_rows or []
            if response.status_code != 200:
                _sitemap_ok = False
                _sitemap_at = now
                return []
            text = response.text or ""
            folded = text.casefold()
            if "<urlset" not in folded and "<loc>" not in folded:
                _sitemap_ok = False
                _sitemap_at = now
                return []
            rows = parse_sitemap_offers(text)
            _sitemap_rows = rows
            _sitemap_at = now
            _sitemap_ok = True
            return rows

    async def _fetch_sitemap_page(self, page: int) -> tuple[list[Listing], int]:
        offer = self._context()
        estate = self._estate_key()
        rows = [
            item
            for item in await self._load_sitemap_rows()
            if item[1] == offer and estate_from_inzerat_slug(item[3]) == estate
        ]
        if not rows:
            return [], 0
        start = max(0, (max(1, page) - 1) * self.PAGE_SIZE)
        chunk = rows[start : start + self.PAGE_SIZE]
        listings: list[Listing] = []
        for url, row_offer, listing_id, slug in chunk:
            listing = self.listing_from_sitemap_url(url, row_offer, slug, listing_id)
            if listing:
                listings.append(listing)
        return listings, len(rows)

    def _remember_build_id(self, html: str) -> str:
        global _build_id
        match = BUILD_ID_RE.search(html or "")
        if match:
            _build_id = match.group(1)
        return _build_id

    async def _fetch_html_page(self, page: int, newest: bool) -> tuple[list[Listing], int, str]:
        url = self._page_url(page, newest=newest)
        response = await self._client.get(url, headers={"Accept": "text/html,application/json;q=0.9"})
        if response.status_code in {404, 410}:
            return [], 0, ""
        self._raise_if_blocked(response)
        response.raise_for_status()
        html = response.text
        self._remember_build_id(html)
        if page <= 1:
            self.search_url = str(response.url).split("#")[0]
        listings = self._parse_list(html)
        parsed_total = self._parse_total(html)
        total = parsed_total or (len(listings) if page == 1 else 0)
        return listings, total, html

    async def _fetch_next_data(self, page: int, html: str = "") -> tuple[list[Listing], int]:
        offer = self._context()
        build_id = self._remember_build_id(html)
        if not build_id:
            return [], 0
        path = "prodej/byty" if offer == "prodej" else "pronajem/byty"
        url = f"{SITE}/_next/data/{build_id}/{path}.json"
        try:
            response = await self._client.get(
                url,
                params={"page": page} if page > 1 else None,
                headers={"Accept": "application/json", "x-nextjs-data": "1"},
            )
        except Exception:
            return [], 0
        if response.status_code != 200:
            return [], 0
        try:
            payload = response.json()
        except ValueError:
            return [], 0
        rows, total = offers_from_payload(payload)
        listings = self._listings_from_rows(rows, offer)
        return listings, total or len(listings)

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        offer = self._context()
        body = self._find_body(page)
        api_blocked: PortalBlocked | None = None
        try:
            response = await self._client.post(
                API,
                params={"page": page, "perPage": self.PAGE_SIZE, "sorting": "latest"},
                json=body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Origin": SITE,
                    "Referer": f"{SITE}/{offer}/byty",
                },
            )
            if response.status_code == 200:
                rows, total = offers_from_payload(response.json())
                listings = self._listings_from_rows(rows, offer)
                if listings:
                    return listings, total or len(listings)
            elif response.status_code in {403, 429}:
                api_blocked = PortalBlocked(
                    "rate_limit" if response.status_code == 429 else "forbidden",
                    response.status_code,
                    portal="ulovdomov",
                )
            elif response.status_code >= 500:
                api_blocked = PortalBlocked("server_error", response.status_code, portal="ulovdomov")
        except Exception:
            pass
        # offer/find is often 500 and SSR/__NEXT_DATA__ is count-only. Sitemap has the cards.
        site_listings, site_total = await self._fetch_sitemap_page(page)
        if site_listings:
            return site_listings, site_total
        if _sitemap_ok:
            # Sitemap parsed; this offer shard is empty. Skip dead SSR and do not cool the portal.
            return [], site_total
        html = ""
        listings: list[Listing] = []
        total = 0
        try:
            listings, total, html = await self._fetch_html_page(page, newest)
        except PortalBlocked:
            raise
        if listings:
            return listings, total
        next_listings, next_total = await self._fetch_next_data(page, html)
        if next_listings:
            return next_listings, next_total
        if api_blocked:
            raise api_blocked
        return listings, total

    def _offer_id(self, listing: Listing) -> int | None:
        if listing.id and 0 < int(listing.id) < (1 << 53):
            return int(listing.id)
        match = INZERAT_RE.search(listing.url or "")
        if match:
            return numeric_id(match.group(2), listing.url)
        return None

    async def fetch_offer_detail(self, listing_id: int, *, keep_url: str = "", offer: str = "") -> Listing:
        response = await self._client.get(
            DETAIL_API,
            params={"offerId": int(listing_id)},
            headers={
                "Accept": "application/json",
                "Origin": SITE,
                "Referer": f"{SITE}/",
            },
            timeout=DETAIL_TIMEOUT_SEC,
        )
        if response.status_code in {404, 410}:
            raise ListingGone(f"{DETAIL_API}?offerId={listing_id}")
        self._raise_if_blocked(response)
        if response.status_code >= 500:
            raise PortalBlocked("server_error", response.status_code, portal="ulovdomov")
        if response.status_code in {403, 429}:
            raise PortalBlocked(
                "rate_limit" if response.status_code == 429 else "forbidden",
                response.status_code,
                portal="ulovdomov",
            )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise PortalBlocked("server_error", response.status_code, portal="ulovdomov") from exc
        detailed = listing_from_detail_payload(payload, offer=offer, keep_url=keep_url)
        if detailed is None:
            raise ListingGone(f"{DETAIL_API}?offerId={listing_id}")
        return detailed

    async def fetch_detail(self, listing: Listing) -> Listing:
        offer_id = self._offer_id(listing)
        if offer_id:
            try:
                detailed = await self.fetch_offer_detail(
                    offer_id, keep_url=listing.url, offer=self._offer_for_listing(listing)
                )
                return merge_detail(listing, detailed)
            except ListingGone:
                raise
            except PortalBlocked:
                raise
            except Exception:
                pass
        return await super().fetch_detail(listing)

    async def hydrate_listings(
        self,
        listings: list[Listing],
        *,
        concurrency: int = 4,
        delay_sec: float = 0.12,
        deadline_sec: float = 15.0,
        fail_fast: bool = True,
    ) -> HydrateResult:
        """Worker-only batch: fill price + image from v2/offer/detail. Not used by /hry*."""
        result = HydrateResult()
        pending = [item for item in listings if item and needs_hydrate(item)]
        if not pending:
            return result
        pending.sort(key=lambda item: (0 if item.price_czk in (None, 0) else 1, -(int(item.id or 0))))
        gate = asyncio.Semaphore(max(1, min(8, int(concurrency or 1))))
        deadline = time.monotonic() + max(1.0, float(deadline_sec))
        abort = asyncio.Event()
        consecutive = 0
        lock = asyncio.Lock()

        async def one(item: Listing) -> None:
            nonlocal consecutive
            if abort.is_set() or time.monotonic() >= deadline:
                return
            offer_id = self._offer_id(item)
            if not offer_id:
                async with lock:
                    result.failed += 1
                return
            async with gate:
                if abort.is_set() or time.monotonic() >= deadline:
                    return
                kind = listing_hydrate_bucket(item)
                async with lock:
                    result.attempted += 1
                    slot = result.kinds.setdefault(kind, {"attempted": 0, "priced": 0, "imaged": 0})
                    slot["attempted"] += 1
                try:
                    detailed = await self.fetch_offer_detail(
                        offer_id, keep_url=item.url, offer=self._offer_for_listing(item)
                    )
                except ListingGone:
                    async with lock:
                        result.gone += 1
                        result.gone_ids.append(int(item.id))
                        consecutive = 0
                    return
                except PortalBlocked as exc:
                    async with lock:
                        result.failed += 1
                        result.blocked = exc
                        result.aborted = str(exc)
                        abort.set()
                    return
                except Exception:
                    async with lock:
                        result.failed += 1
                        consecutive += 1
                        if fail_fast and consecutive >= DETAIL_CONSECUTIVE_FAILS:
                            result.aborted = "consecutive_fail"
                            abort.set()
                    return
                merge_detail(item, detailed)
                async with lock:
                    consecutive = 0
                    result.listings.append(item)
                    slot = result.kinds.setdefault(kind, {"attempted": 0, "priced": 0, "imaged": 0})
                    if item.price_czk not in (None, 0):
                        result.priced += 1
                        slot["priced"] += 1
                    if item.image_url:
                        result.imaged += 1
                        slot["imaged"] += 1
                if delay_sec > 0 and not abort.is_set():
                    await asyncio.sleep(delay_sec)

        tasks = [asyncio.create_task(one(item)) for item in pending]
        try:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=max(1.5, float(deadline_sec) + 1.0))
        except asyncio.TimeoutError:
            result.aborted = result.aborted or "deadline"
            abort.set()
            for task in tasks:
                task.cancel()
        if time.monotonic() >= deadline and not result.aborted:
            result.aborted = "deadline"
        return result
