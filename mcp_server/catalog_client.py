"""HTTP client for Realitify catalog API. No local filtering, no cache."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import urlencode

import httpx

CATALOG_BASE_URL = (os.environ.get("CATALOG_BASE_URL") or "https://realitify.cz").rstrip("/")
PUBLIC_WEB_URL = (os.environ.get("PUBLIC_WEB_URL") or "https://realitify.cz").rstrip("/")
REQUEST_TIMEOUT = float(os.environ.get("CATALOG_TIMEOUT_SEC") or "20")
MAX_RESPONSE_CHARS = int(os.environ.get("MAX_RESPONSE_CHARS") or "12000")

PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]?)?\d{3}[\s\-]?\d{2,4}[\s\-]?\d{2,4}"
)
PII_KEYS = {
    "agency",
    "seller",
    "seller_name",
    "broker",
    "broker_name",
    "agent",
    "agent_name",
    "makler",
    "makléř",
    "phone",
    "telefon",
    "tel",
    "mobile",
    "contact",
    "contact_name",
    "contact_phone",
    "email",
    "company",
}


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


def compact_listing(item: dict[str, Any]) -> dict[str, Any]:
    """Return a short public listing payload without personal data."""
    return {
        "id": item.get("id"),
        "listing_key": item.get("listing_key") or item.get("canonical_key") or "",
        "title": sanitize_text(item.get("name") or item.get("title") or "", 160),
        "price_czk": item.get("price_czk"),
        "price_label": sanitize_text(item.get("price_label") or "", 80),
        "disposition": sanitize_text(item.get("disposition") or "", 40),
        "area_m2": item.get("area_m2"),
        "locality": sanitize_text(item.get("locality") or "", 120),
        "offer": _offer_from_item(item),
        "source_url": str(item.get("url") or "").strip(),
        "realitify_url": realitify_url(item),
    }


def compact_detail(item: dict[str, Any]) -> dict[str, Any]:
    payload = compact_listing(item)
    description = sanitize_text(item.get("description") or "", 600)
    if description:
        # Drop lines that look like contact blocks
        lines = []
        for line in description.splitlines():
            lower = line.casefold()
            if any(key in lower for key in ("telefon", "tel.", "makléř", "makler", "@")):
                continue
            lines.append(line)
        payload["description"] = "\n".join(lines).strip()[:600]
    return payload


def _truncate_json(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) <= MAX_RESPONSE_CHARS:
        return text
    return text[: MAX_RESPONSE_CHARS - 1] + "…"


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
        raise CatalogError(f"Catalog API unavailable: HTTP {response.status_code}" + (f" ({detail})" if detail else ""))
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise CatalogError("Catalog API returned invalid JSON") from exc


async def search_listings(
    *,
    locality: str = "",
    offer_type: str = "",
    max_price: int | None = None,
    min_price: int | None = None,
    disposition: str = "",
    min_area: int | None = None,
    limit: int = 10,
) -> str:
    limit_n = min(max(_as_int(limit, 10) or 10, 1), 20)
    offer = (offer_type or "").strip().casefold()
    if offer in {"pronájem", "pronajem", "rent"}:
        offer = "pronajem"
    elif offer in {"prodej", "sale"}:
        offer = "prodej"
    elif offer:
        raise CatalogError("offer_type must be 'pronajem' or 'prodej'")
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
        },
    )
    items = [compact_listing(item) for item in (data.get("items") or []) if isinstance(item, dict)]
    payload = {
        "count": len(items),
        "total": data.get("total") if data.get("total") is not None else len(items),
        "items": items,
    }
    return _truncate_json(payload)


async def get_listing(listing_id: str) -> str:
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
    return _truncate_json(compact_detail(data))


async def locality_stats(locality: str, offer_type: str = "") -> str:
    district = (locality or "").strip()
    if not district:
        raise CatalogError("locality is required")
    offer = (offer_type or "").strip().casefold()
    if offer in {"pronájem", "pronajem", "rent"}:
        offer = "pronajem"
    elif offer in {"prodej", "sale"}:
        offer = "prodej"
    elif offer:
        raise CatalogError("offer_type must be 'pronajem' or 'prodej'")
    data = await _get("/api/public/catalog/stats", {"district": district, "offer": offer})
    if not isinstance(data, dict):
        raise CatalogError("Catalog API returned invalid stats payload")
    payload = {
        "locality": data.get("locality") or district,
        "offer": data.get("offer") or offer,
        "active_count": data.get("active_count"),
        "avg_price_per_m2": data.get("avg_price_per_m2"),
    }
    return _truncate_json(payload)
