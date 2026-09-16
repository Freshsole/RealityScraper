"""UlovDomov scraper — JSON API first, HTML/_next/data fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from app.block_page import PortalBlocked
from app.html_listing import HtmlPortalClient, abs_url, listing_from_card, numeric_id, parse_price
from app.portal_urls import ulovdomov_url
from app.sreality import Listing

SITE = ulovdomov_url.site
API = "https://ud.api.ulovdomov.cz/v1/offer/find"
BOUNDS = {"northEast": {"lat": 51.06, "lng": 18.87}, "southWest": {"lat": 48.55, "lng": 12.09}}
HREF_RE = re.compile(
    r'href="((?:https://www\.ulovdomov\.cz)?/(?:pronajem|prodej)/[^"]+/\d+[^"]*)"',
    re.I,
)
HREF2_RE = re.compile(r'href="((?:https://www\.ulovdomov\.cz)?/inzerat/[^"]+)"', re.I)
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
BUILD_ID_RE = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
OFFER_KEYS = ("offers", "results", "items", "list", "adverts", "estates", "hits")


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


class UlovdomovClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "page"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _offer_type_id(self) -> int:
        return 2 if self._context() == "prodej" else 1

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

    async def _fetch_next_data(self, page: int, html: str = "") -> tuple[list[Listing], int]:
        offer = self._context()
        build_id = ""
        match = BUILD_ID_RE.search(html or "")
        if match:
            build_id = match.group(1)
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
        try:
            listings, total = await super().fetch_page(page, newest=newest)
        except PortalBlocked:
            raise
        if listings:
            return listings, total
        next_listings, next_total = await self._fetch_next_data(page)
        if next_listings:
            return next_listings, next_total
        if api_blocked:
            raise api_blocked
        return listings, total
