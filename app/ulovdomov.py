"""UlovDomov scraper — JSON API first, HTML/_next/data fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from app.html_listing import HtmlPortalClient, abs_url, listing_from_card, numeric_id, parse_price
from app.portal_urls import ulovdomov_url
from app.sreality import Listing

SITE = ulovdomov_url.site
API = "https://ud.api.ulovdomov.cz/v1/offer/find"
BOUNDS = {"northEast": {"lat": 51.06, "lng": 18.87}, "southWest": {"lat": 48.55, "lng": 12.09}}
HREF_RE = re.compile(
    r'href="((?:https://www\.ulovdomov\.cz)?/(?:pronajem|prodej)/[^"]{0,300}/\d{1,12}[^"]{0,200})"',
    re.I,
)
HREF2_RE = re.compile(r'href="((?:https://www\.ulovdomov\.cz)?/inzerat/[^"]{1,400})"', re.I)


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


def offers_from_payload(payload: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict):
        return [], 0
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(payload.get("data"), list):
        rows = payload["data"]
        total = _as_int(payload.get("total") or payload.get("count")) or len(rows)
        return [item for item in rows if isinstance(item, dict)], total
    for key in ("offers", "results", "items", "list"):
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
    return [], _as_int(payload.get("count") or (data.get("count") if isinstance(data, dict) else None)) or 0


def _next_data_json(html: str) -> dict[str, Any]:
    raw = html or ""
    start = raw.find('<script id="__NEXT_DATA__"')
    if start < 0:
        start = raw.find('id="__NEXT_DATA__"')
    if start < 0:
        return {}
    gt = raw.find(">", start)
    if gt < 0:
        return {}
    end = raw.find("</script>", gt + 1)
    if end < 0:
        return {}
    try:
        payload = json.loads(raw[gt + 1 : end])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


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

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        payload = _next_data_json(html or "")
        if payload:
            props = payload.get("props", {}).get("pageProps", payload.get("pageProps") or {})
            rows, _total = offers_from_payload(props)
            for raw in rows:
                listing = self.listing_from_offer(raw, offer)
                if listing and listing.url not in seen:
                    seen.add(listing.url)
                    items.append(listing)
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
            title_m = re.search(r">(Pronájem[^<]{1,200}|Prodej[^<]{1,200})<", window)
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

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        from app.scrape_http import request_with_log

        offer = self._context()
        body = self._find_body(page)
        response = await request_with_log(
            self._client,
            "POST",
            API,
            portal="ulovdomov",
            params={"page": page, "perPage": self.PAGE_SIZE, "sorting": "latest"},
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if response.status_code == 200:
            rows, total = offers_from_payload(response.json())
            listings = []
            seen: set[str] = set()
            for raw in rows:
                listing = self.listing_from_offer(raw, offer)
                if listing and listing.url not in seen:
                    seen.add(listing.url)
                    listings.append(listing)
            return listings, total or len(listings)
        # 403/500 will not improve on an HTML fallback of the same listing.
        response.raise_for_status()
        return [], 0
