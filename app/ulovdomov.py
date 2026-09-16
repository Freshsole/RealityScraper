"""UlovDomov scraper — JSON API first, sitemap hydration, HTML/_next/data last."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.block_page import PortalBlocked
from app.html_listing import HtmlPortalClient, abs_url, listing_from_card, numeric_id, parse_disposition, parse_price
from app.portal_urls import ulovdomov_url
from app.sreality import Listing

SITE = ulovdomov_url.site
API = "https://ud.api.ulovdomov.cz/v1/offer/find"
SITEMAP_OFFERS = f"{SITE}/sitemap-offers.xml"
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

_sitemap_rows: list[tuple[str, str, int, str]] | None = None
_sitemap_at = 0.0
_sitemap_ok = False
_build_id = ""


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
        if folded in {"kk", "housing", "byt", "bytu"} or folded.isdigit():
            break
        if "+" in folded:
            break
        locality_parts.append(part)
        if len(locality_parts) >= 4:
            break
    locality = " ".join(word[:1].upper() + word[1:] for word in locality_parts if word)
    label = "Pronájem" if offer == "pronajem" else "Prodej"
    name = f"{label} bytu {disp}".strip() if disp else f"{label} {raw.replace('-', ' ')}".strip()
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

    def listing_from_sitemap_url(self, url: str, offer: str, slug: str = "", listing_id: int | None = None) -> Listing | None:
        match = INZERAT_RE.search(url or "")
        if match:
            slug = slug or match.group(1)
            listing_id = listing_id or numeric_id(match.group(2), url)
        if not listing_id:
            return None
        name, locality, disp = fields_from_inzerat_slug(slug, offer)
        return listing_from_card(
            listing_id=int(listing_id),
            name=name,
            url=url,
            price_czk=None,
            price_label="",
            locality=locality,
            disposition=disp,
            offer=offer,
        )

    async def _load_sitemap_rows(self) -> list[tuple[str, str, int, str]]:
        global _sitemap_rows, _sitemap_at, _sitemap_ok
        now = time.monotonic()
        if _sitemap_ok and _sitemap_rows is not None and now - _sitemap_at < SITEMAP_TTL_SEC:
            return _sitemap_rows
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
        rows = [item for item in await self._load_sitemap_rows() if item[1] == offer]
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
