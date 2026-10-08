"""M&M Reality list/detail scraper.

Redesign 2026: the site is a Vue SSR app. Listing cards are server-rendered as
``<a href="https://www.mmreality.cz/nemovitosti/<id>/">`` anchors carrying
``data-realty-id`` / ``data-realty-name`` / ``data-realty-price`` attributes
(``data-realty-name`` looks like "Pronájem, Byt 2+kk, 35 m², Praha, Mirotická").

Search URLs are path-based now: ``/nemovitosti/pronajem/`` (or ``/prodej/``),
paginated with ``?page=N`` (12 cards per page). The old ``typ-nabidky`` query
params are ignored by the site.
"""

from __future__ import annotations

import re

from app.html_listing import (
    HtmlPortalClient,
    abs_url,
    clean,
    listing_from_card,
    numeric_id,
    parse_area,
    parse_disposition,
    parse_price,
)
from app.portal_urls import mmreality_url
from app.sreality import Listing

SITE = mmreality_url.site

HREF_RE = re.compile(r'href="((?:https://www\.mmreality\.cz)?/nemovitosti/(\d{4,12})/?)"', re.I)
NAME_RE = re.compile(r'data-realty-name="([^"]{1,300})"')
PRICE_RE = re.compile(r'data-realty-price="([^"]{1,80})"')
IMG_RE = re.compile(
    r'(?:src|data-src)="(https://cdn\.mmreality\.cz/[^"]{1,300}\.(?:jpg|jpeg|webp)[^"]{0,200})"',
    re.I,
)


class MmrealityClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "page"
    PAGE_SIZE = 12
    # Listing cards start ~300KB into the 480KB page; the shared 180KB list
    # truncation would cut them off.
    FULL_LIST_HTML = True

    # M&M blokuje datacentrové IP (Cloudflare bot protection, HTTP 403) -
    # stejný postup jako u Annonce: browser-like hlavičky + warmup homepage
    # pro získání cookies.
    EXTRA_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"macOS"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    def __init__(self, search_url: str) -> None:
        super().__init__(search_url)
        for k, v in self.EXTRA_HEADERS.items():
            self._client.headers[k] = v
        self._warmed_up = False

    async def _warmup(self) -> None:
        """Warmup: nejdřív homepage pro získání cookies."""
        if self._warmed_up:
            return
        self._warmed_up = True
        try:
            import asyncio

            await self._client.get(self.SITE + "/", timeout=15)
            await asyncio.sleep(2)  # pauza jako člověk
        except Exception:
            pass

    async def fetch_page(self, page: int = 1, **kwargs):
        await self._warmup()
        return await super().fetch_page(page, **kwargs)

    def _context(self) -> str:
        raw = (self.search_url or "").lower()
        return "prodej" if "/prodej" in raw else "pronajem"

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for match in HREF_RE.finditer(html or ""):
            href, listing_id = match.group(1), match.group(2)
            url = abs_url(href, SITE)
            if url in seen:
                continue
            seen.add(url)
            window = html[match.start() : match.start() + 8000]
            name_m = NAME_RE.search(window)
            raw_name = clean(name_m.group(1)) if name_m else ""
            parts = [part.strip() for part in raw_name.split(",") if part.strip()]
            locality = parts[-2] if len(parts) >= 3 else (parts[-1] if parts else "")
            price_m = PRICE_RE.search(window)
            price_czk, price_label = parse_price(price_m.group(1) if price_m else "")
            img_m = IMG_RE.search(window)
            img = img_m.group(1) if img_m else ""
            items.append(
                listing_from_card(
                    listing_id=numeric_id(listing_id, url),
                    name=raw_name or f"Nemovitost {listing_id}",
                    url=url,
                    price_czk=price_czk,
                    price_label=price_label,
                    locality=locality,
                    image_url=img,
                    photos=[img] if img else [],
                    offer=offer,
                    area_m2=parse_area(raw_name),
                    disposition=parse_disposition(raw_name),
                )
            )
        return items
