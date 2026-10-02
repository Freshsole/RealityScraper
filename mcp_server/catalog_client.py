"""HTTP client for Realitify catalog API. No local filtering, no cache."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal
from urllib.parse import urlencode

import httpx

CATALOG_BASE_URL = (os.environ.get("CATALOG_BASE_URL") or "https://realitify.cz").rstrip("/")
PUBLIC_WEB_URL = (os.environ.get("PUBLIC_WEB_URL") or "https://realitify.cz").rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("CATALOG_TIMEOUT_SEC") or "25")
MAX_RESPONSE_CHARS = int(os.environ.get("MAX_RESPONSE_CHARS") or "14000")

REALITIFY_TIP = (
    "Realitify paid plans send instant alerts when a new listing matches your filters "
    f"({PUBLIC_WEB_URL}/#cenik)."
)

PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3}[\s\-]?\d{2,4}[\s\-]?\d{2,4}"
)

OfferType = Literal["pronajem", "prodej"]
SortType = Literal["newest", "cheapest", "best_value"]


class CatalogError(Exception):
    """Raised when the catalog API fails or returns an error."""


def _as_int(value: Any, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _offer_from_item(item: dict[str, Any]) -> str:
    extras = item.get("extras")
    if isinstance(extras, str):
        try:
            extras = json.loads(extras) if extras else {}
        except json.JSONDecodeError:
            extras = {}
    if isinstance(extras, dict):
        raw = str(extras.get("offer") or "").strip().casefold()
        if "pronáj" in raw or "pronaj" in raw:
            return "pronajem"
        if "prodej" in raw:
            return "prodej"
    label = str(item.get("price_label") or "").casefold()
    if "měsíc" in label or "mesic" in label:
        return "pronajem"
    url = str(item.get("url") or "").casefold()
    if "/pronajem/" in url:
        return "pronajem"
    if "/prodej/" in url:
        return "prodej"
    return ""


def _floor_from_item(item: dict[str, Any]) -> str:
    extras = item.get("extras")
    if isinstance(extras, str):
        try:
            extras = json.loads(extras) if extras else {}
        except json.JSONDecodeError:
            extras = {}
    if not isinstance(extras, dict):
        return ""
    for spec in extras.get("specs") or []:
        if not isinstance(spec, dict):
            continue
        label = str(spec.get("label") or "").casefold()
        if "podla" in label or label in {"floor", "patro"}:
            return str(spec.get("value") or "").strip()
    return ""


def realitify_url(item: dict[str, Any]) -> str:
    listing_key = str(item.get("listing_key") or item.get("canonical_key") or "").strip()
    if listing_key:
        return f"{PUBLIC_WEB_URL}/nabidka?listing_key={listing_key}"
    source = str(item.get("url") or "").strip()
    if source:
        return f"{PUBLIC_WEB_URL}/nabidka?url={source}"
    listing_id = item.get("id")
    if listing_id not in (None, ""):
        return f"{PUBLIC_WEB_URL}/nabidka?id={listing_id}"
    return PUBLIC_WEB_URL


def sanitize_text(value: Any, max_len: int = 400) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = PHONE_RE.sub("[redacted]", text)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _price_m2(item: dict[str, Any]) -> float | None:
    try:
        price = float(item.get("price_czk"))
        area = float(item.get("area_m2"))
    except (TypeError, ValueError):
        return None
    if price <= 0 or area <= 0:
        return None
    return round(price / area, 2)


def _price_vs_locality(price_m2: float | None, avg_m2: float | None) -> float | None:
    if price_m2 is None or avg_m2 is None or avg_m2 <= 0:
        return None
    return round(100.0 * (price_m2 - avg_m2) / avg_m2, 1)


def compact_listing(item: dict[str, Any], avg_m2: float | None = None) -> dict[str, Any]:
    price_m2 = _price_m2(item)
    image = item.get("image_url") or ""
    if not image and isinstance(item.get("photos"), list) and item["photos"]:
        image = str(item["photos"][0] or "")
    return {
        "id": item.get("id"),
        "listing_key": item.get("listing_key") or item.get("canonical_key") or "",
        "title": sanitize_text(item.get("name") or item.get("title") or "", 160),
        "price_czk": item.get("price_czk"),
        "price_label": sanitize_text(item.get("price_label") or "", 80),
        "price_per_m2": price_m2,
        "price_vs_locality_pct": _price_vs_locality(price_m2, avg_m2),
        "disposition": sanitize_text(item.get("disposition") or "", 40),
        "area_m2": item.get("area_m2"),
        "locality": sanitize_text(item.get("locality") or "", 120),
        "floor": _floor_from_item(item) or None,
        "first_seen": item.get("first_seen") or "",
        "portal": item.get("portal") or "",
        "image_url": image or None,
        "offer": _offer_from_item(item),
        "source_url": str(item.get("url") or "").strip(),
        "realitify_url": realitify_url(item),
    }


def compact_detail(item: dict[str, Any], avg_m2: float | None = None) -> dict[str, Any]:
    payload = compact_listing(item, avg_m2)
    description = sanitize_text(item.get("description") or "", 600)
    if description:
        lines = []
        for line in description.splitlines():
            lower = line.casefold()
            if any(key in lower for key in ("telefon", "tel.", "makléř", "makler", "@")):
                continue
            lines.append(line)
        payload["description"] = "\n".join(lines).strip()[:600]
    return payload


def _wrap(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["realitify_tip"] = REALITIFY_TIP
    return payload


async def _get(path: str, params: dict[str, Any]) -> Any:
    query = {key: value for key, value in params.items() if value not in (None, "")}
    url = f"{CATALOG_BASE_URL}{path}"
    if query:
        url = f"{url}?{urlencode(query, doseq=True)}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.TimeoutException as exc:
        raise CatalogError(f"Catalog API timeout after {REQUEST_TIMEOUT}s") from exc
    except httpx.HTTPError as exc:
        raise CatalogError(f"Catalog API unavailable: {exc}") from exc
    if response.status_code == 404:
        raise CatalogError("Listing not found")
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:200]
        raise CatalogError(
            f"Catalog API unavailable: HTTP {response.status_code}"
            + (f" ({detail})" if detail else "")
        )
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise CatalogError("Catalog API returned invalid JSON") from exc


def _normalize_offer(offer_type: str) -> str:
    raw = (offer_type or "").strip().casefold()
    if raw in {"", "any", "all"}:
        return ""
    if raw in {"pronájem", "pronajem", "rent", "rental"}:
        return "pronajem"
    if raw in {"prodej", "sale", "buy"}:
        return "prodej"
    raise CatalogError("offer_type must be 'pronajem' or 'prodej'")


async def search_listings(
    *,
    locality: str = "",
    offer_type: str = "",
    max_price: int | None = None,
    min_price: int | None = None,
    disposition: str = "",
    min_area: int | None = None,
    limit: int = 10,
    sort: str = "newest",
    since_hours: int | None = None,
) -> dict[str, Any]:
    limit_n = min(max(_as_int(limit, 10) or 10, 1), 20)
    offer = _normalize_offer(offer_type)
    sort_n = (sort or "newest").strip()
    if sort_n not in {"newest", "cheapest", "best_value"}:
        sort_n = "newest"
    data = await _get(
        "/api/public/catalog",
        {
            "district": (locality or "").strip(),
            "offer": offer,
            "price_from": min_price if min_price is not None else "",
            "price_to": max_price if max_price is not None else "",
            "disposition": (disposition or "").strip(),
            "area_from": min_area if min_area is not None else "",
            "limit": limit_n,
            "facets": "0",
            "sort": sort_n,
            "since_hours": since_hours if since_hours else "",
        },
    )
    if data.get("error") == "no_listings_for_locality":
        raise CatalogError(str(data.get("message") or "No listings for locality") + f" Suggestions: {data.get('suggestions')}")
    avg_m2 = data.get("avg_price_per_m2_locality")
    try:
        avg_m2_f = float(avg_m2) if avg_m2 is not None else None
    except (TypeError, ValueError):
        avg_m2_f = None
    items = [
        compact_listing(item, avg_m2_f)
        for item in (data.get("items") or [])
        if isinstance(item, dict)
    ]
    if sort_n == "best_value":
        items.sort(
            key=lambda row: (
                row.get("price_vs_locality_pct") is None,
                row.get("price_vs_locality_pct") if row.get("price_vs_locality_pct") is not None else 0,
            )
        )
    return _wrap(
        {
            "count": len(items),
            "total": data.get("total") if data.get("total") is not None else len(items),
            "locality": data.get("locality") or locality,
            "offer": offer,
            "sort": sort_n,
            "avg_price_per_m2_locality": avg_m2_f,
            "items": items,
        }
    )


async def new_listings(
    *,
    locality: str = "",
    offer_type: str = "pronajem",
    since_hours: int = 24,
    disposition: str = "",
    max_price: int | None = None,
    min_area: int | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    hours = min(max(_as_int(since_hours, 24) or 24, 1), 168)
    return await search_listings(
        locality=locality,
        offer_type=offer_type,
        max_price=max_price,
        disposition=disposition,
        min_area=min_area,
        limit=limit,
        sort="newest",
        since_hours=hours,
    )


async def get_listing(listing_id: str) -> dict[str, Any]:
    raw = str(listing_id or "").strip()
    if not raw:
        raise CatalogError("listing id is required")
    params: dict[str, Any] = {"enrich": "0"}
    if raw.isdigit():
        params["id"] = raw
    else:
        params["listing_key"] = raw
    data = await _get("/api/public/catalog/item", params)
    if not isinstance(data, dict):
        raise CatalogError("Catalog API returned invalid listing payload")
    return _wrap(compact_detail(data))


async def locality_stats(locality: str, offer_type: str = "", disposition: str = "") -> dict[str, Any]:
    district = (locality or "").strip()
    if not district:
        raise CatalogError("locality is required")
    offer = _normalize_offer(offer_type)
    data = await _get(
        "/api/public/catalog/stats",
        {"district": district, "offer": offer, "disposition": disposition},
    )
    if not isinstance(data, dict):
        raise CatalogError("Catalog API returned invalid stats payload")
    return _wrap(data)


async def price_check(
    *,
    locality: str,
    offer_type: str,
    price: int,
    disposition: str = "",
    area: int | None = None,
) -> dict[str, Any]:
    district = (locality or "").strip()
    if not district:
        raise CatalogError("locality is required")
    offer = _normalize_offer(offer_type)
    if not offer:
        raise CatalogError("offer_type must be 'pronajem' or 'prodej'")
    data = await _get(
        "/api/public/catalog/price-check",
        {
            "district": district,
            "offer": offer,
            "price": price,
            "disposition": disposition,
            "area": area if area else "",
        },
    )
    if not isinstance(data, dict):
        raise CatalogError("Catalog API returned invalid price-check payload")
    return _wrap(data)


async def compare_localities(
    localities: list[str],
    offer_type: str = "pronajem",
    disposition: str = "",
) -> dict[str, Any]:
    locs = [str(x).strip() for x in localities if str(x).strip()]
    if len(locs) < 2:
        raise CatalogError("provide at least two localities")
    offer = _normalize_offer(offer_type)
    data = await _get(
        "/api/public/catalog/compare",
        {
            "localities": ",".join(locs),
            "offer": offer,
            "disposition": disposition,
        },
    )
    if not isinstance(data, dict):
        raise CatalogError("Catalog API returned invalid compare payload")
    return _wrap(data)
