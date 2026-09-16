"""Reality.cz list/detail scraper. The live site is often in maintenance; parsers are fixture-tested."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price
from app.portal_urls import realitycz_url
from app.sreality import Listing

SITE = realitycz_url.site
HREF_RE = re.compile(
    r'href="((?:https://www\.reality\.cz)?/(?:detail|nemovitost|inzerat)/[^"]+)"',
    re.I,
)
HREF2_RE = re.compile(r'href="((?:https://www\.reality\.cz)?/(?:pronajem|prodej)/[^"]+/\d+[^"]*)"', re.I)
ID_RE = re.compile(r"/(\d{4,})")
TITLE_RE = re.compile(r"<h[123][^>]*>(.*?)</h[123]>", re.S | re.I)
IMG_RE = re.compile(r'(?:src|data-src)="([^"]+\.(?:jpg|jpeg|webp)[^"]*)"', re.I)


class RealityczClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _parse_list(self, html: str) -> list[Listing]:
        if "údržba server" in (html or "").casefold() or "udrzba server" in (html or "").casefold():
            return []
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        hrefs = HREF_RE.findall(html or "") + HREF2_RE.findall(html or "")
        for href in hrefs:
            url = abs_url(href.split("?")[0], SITE)
            if url in seen or "/moje-reality/" in url:
                continue
            id_m = ID_RE.search(url)
            if not id_m:
                continue
            seen.add(url)
            idx = html.find(href)
            window = html[max(0, idx - 350) : idx + 800] if idx >= 0 else href
            title_m = TITLE_RE.search(window)
            title = clean(title_m.group(1) if title_m else url.rstrip("/").split("/")[-1].replace("-", " "))
            price_czk, price_label = parse_price(window)
            img_m = IMG_RE.search(window)
            img = abs_url(img_m.group(1), SITE) if img_m else ""
            locality = ""
            loc_m = re.search(r"(Praha[^<,]*|Brno[^<,]*|Ostrava[^<,]*)", window)
            if loc_m:
                locality = clean(loc_m.group(1))
            elif "," in title:
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
                    offer="prodej" if "/prodej/" in url else offer,
                )
            )
        return items
