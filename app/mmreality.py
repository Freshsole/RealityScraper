"""M&M Reality list/detail scraper. Live list pages are Cloudflare-gated.

httpx, curl_cffi JA3, and headless Chrome from datacenter IPs all get a WAF
hard-block (403 "Sorry, you have been blocked"). An optional worker-only
browser/JA3 path exists behind SCRAPE_BROWSER_FETCH=1 — never on
SCRAPE_ROLE=web / InstantSiteASGI. Follow-up: residential proxy + persistent
Playwright on the scrape worker. Challenge HTML cools the portal for the rest
of the tick; remaining M&M pages are not deferred.
"""

from __future__ import annotations

import json
import re

from app.block_page import PortalBlocked, classify_block
from app.browser_fetch import fetch_html, should_try
from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price
from app.portal_urls import mmreality_url
from app.sreality import Listing

SITE = mmreality_url.site
HREF_RE = re.compile(r'href="((?:https://www\.mmreality\.cz)?/nemovitosti/[^"]+)"', re.I)
ID_RE = re.compile(r"/nemovitosti/(?:[^/]*-)?(\d{4,})")
TITLE_RE = re.compile(r"<h[123][^>]*>(.*?)</h[123]>", re.S | re.I)
IMG_RE = re.compile(r'(?:src|data-src)="(https://[^"]*mmreality[^"]+\.(?:jpg|jpeg|webp)[^"]*)"', re.I)
JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


class MmrealityClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20

    def _context(self) -> str:
        raw = (self.search_url or "").lower()
        return "prodej" if "prodej" in raw else "pronajem"

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        url = self._page_url(page, newest=newest)
        response = await self._client.get(url, headers={"Accept": "text/html,application/json;q=0.9"})
        if response.status_code in {404, 410}:
            return [], 0
        signal = classify_block(response.status_code, response.text, response.headers)
        html = response.text
        if signal is None:
            response.raise_for_status()
        elif should_try(self._portal_id(), signal):
            fetched = await fetch_html(
                url,
                headers={"Accept": "text/html", "Referer": SITE + "/"},
            )
            retry = classify_block(fetched.status_code, fetched.text, fetched.headers)
            if retry is not None or fetched.backend == "skipped" or not fetched.text:
                raise PortalBlocked(
                    signal.kind,
                    signal.status_code or response.status_code,
                    portal=self._portal_id(),
                    retry_after=signal.retry_after,
                    detail=signal.detail,
                )
            html = fetched.text
        else:
            raise PortalBlocked(
                signal.kind,
                signal.status_code or response.status_code,
                portal=self._portal_id(),
                retry_after=signal.retry_after,
                detail=signal.detail,
            )
        if page <= 1:
            self.search_url = str(getattr(response, "url", url)).split("#")[0]
        listings = self._parse_list(html)
        parsed_total = self._parse_total(html)
        total = parsed_total or (len(listings) if page == 1 else 0)
        return listings, total

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for listing in self._parse_jsonld(html or "", offer):
            if listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        if items:
            return items
        # Prefer per-link cards; M&M markup varies and is often JS-hydrated.
        for href in HREF_RE.findall(html or ""):
            url = abs_url(href.split("?")[0], SITE)
            if url in seen or "/nemovitosti/?" in url or url.rstrip("/").endswith("/nemovitosti"):
                continue
            id_m = ID_RE.search(url)
            if not id_m:
                continue
            seen.add(url)
            idx = html.find(href)
            window = html[max(0, idx - 400) : idx + 900] if idx >= 0 else href
            title_m = TITLE_RE.search(window)
            title = clean(title_m.group(1) if title_m else "")
            if not title:
                slug = url.rstrip("/").split("/")[-1]
                title = clean(slug.replace("-", " "))
            price_czk, price_label = parse_price(window)
            img_m = IMG_RE.search(window) or IMG_RE.search(html[max(0, idx) : idx + 400] if idx >= 0 else "")
            img = img_m.group(1) if img_m else ""
            locality = ""
            if "," in title:
                locality = title.split(",")[-1].strip()
            items.append(
                listing_from_card(
                    listing_id=numeric_id(id_m.group(1), url),
                    name=title,
                    url=url,
                    price_czk=price_czk,
                    price_label=price_label,
                    locality=locality,
                    image_url=img,
                    photos=[img] if img else [],
                    offer=offer,
                )
            )
        return items

    def _parse_jsonld(self, html: str, offer: str) -> list[Listing]:
        items: list[Listing] = []
        for raw in JSONLD_RE.findall(html or ""):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rows = payload if isinstance(payload, list) else [payload]
            for row in rows:
                items.extend(self._listings_from_jsonld(row, offer))
        return items

    def _listings_from_jsonld(self, row: object, offer: str) -> list[Listing]:
        if not isinstance(row, dict):
            return []
        items: list[Listing] = []
        graph = row.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                items.extend(self._listings_from_jsonld(node, offer))
        elements = row.get("itemListElement")
        if isinstance(elements, list):
            for node in elements:
                if isinstance(node, dict):
                    items.extend(self._listings_from_jsonld(node.get("item") or node, offer))
        url = str(row.get("url") or row.get("@id") or "")
        if "/nemovitosti/" in url and ID_RE.search(url):
            name = str(row.get("name") or row.get("headline") or "")
            offers = row.get("offers") if isinstance(row.get("offers"), dict) else {}
            price_raw = offers.get("price") if offers else row.get("price")
            price_czk = None
            try:
                if price_raw not in (None, ""):
                    price_czk = int(round(float(str(price_raw).replace(" ", "").replace(",", "."))))
            except (TypeError, ValueError):
                price_czk = None
            locality = ""
            address = row.get("address")
            if isinstance(address, dict):
                locality = str(address.get("addressLocality") or address.get("name") or "")
            image = row.get("image")
            img = image if isinstance(image, str) else (image[0] if isinstance(image, list) and image else "")
            items.append(
                listing_from_card(
                    listing_id=numeric_id(ID_RE.search(url).group(1), url),
                    name=clean(name) or url.rstrip("/").split("/")[-1].replace("-", " "),
                    url=abs_url(url, SITE),
                    price_czk=price_czk,
                    price_label="",
                    locality=locality,
                    image_url=str(img or ""),
                    photos=[str(img)] if img else [],
                    offer=offer,
                )
            )
        return items
