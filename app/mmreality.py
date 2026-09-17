"""M&M Reality list/detail scraper. Live fetches may hit Cloudflare; parsers are fixture-tested."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price
from app.portal_urls import mmreality_url
from app.sreality import Listing

SITE = mmreality_url.site
HREF_RE = re.compile(r'href="((?:https://www\.mmreality\.cz)?/nemovitosti/[^"]+)"', re.I)
ID_RE = re.compile(r"/nemovitosti/(?:[^/]*-)?(\d{4,})")
TITLE_RE = re.compile(r"<h[123][^>]*>(.*?)</h[123]>", re.S | re.I)
IMG_RE = re.compile(r'(?:src|data-src)="(https://[^"]*mmreality[^"]+\.(?:jpg|jpeg|webp)[^"]*)"', re.I)
CARD_SPLIT_RE = re.compile(r'(?:class="[^"]*(?:estate|property|item|card|offer)[^"]*")', re.I)


class MmrealityClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20

    def _context(self) -> str:
        raw = (self.search_url or "").lower()
        return "prodej" if "prodej" in raw else "pronajem"

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
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
