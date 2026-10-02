"""Eurobydleni.cz list/detail scraper."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, parse_price
from app.sreality import Listing

SITE = "https://www.eurobydleni.cz"

CARD_SPLIT_RE = re.compile(
    r'<li[^>]*class="list-items__item "[^>]*data-deleted="0"[^>]*itemid="([^"]*?/detail/(\d+)/)"',
    re.I,
)
TITLE_RE = re.compile(
    r'<a[^>]*itemprop="url"[^>]*>(.*?)</a>', re.S | re.I
)
PRICE_RE = re.compile(r'(\d[\d\s\xa0]*)\s*Kč', re.I)
IMG_RE = re.compile(
    r'<figure class="list-items__item__image">.*?<img src="([^"]+)"', re.S | re.I
)
ADDR_RE = re.compile(r'<meta itemprop="addressLocality" content="([^"]+)">', re.I)
STREET_RE = re.compile(r'<meta itemprop="streetAddress" content="([^"]+)">', re.I)


class EurobydleniClient(HtmlPortalClient):
    SITE = SITE
    PAGE_SIZE = 12

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "prodej" in path else "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        base = (self.search_url or "").rstrip("/")
        if page <= 1:
            return base + "/"
        return f"{base}/page-{page}/"

    def _parse_list(self, html: str) -> list[Listing]:
        out: list[Listing] = []
        ctx = self._context()
        # Rozdělíme podle začátků karet
        parts = CARD_SPLIT_RE.split(html or "")
        # parts[0] = před první kartou, pak trojice (itemid, lid, blok)
        for i in range(1, len(parts), 3):
            if i + 1 >= len(parts):
                break
            itemid, lid = parts[i], parts[i + 1]
            block = parts[i + 2][:8000]  # blok karty
            title_m = TITLE_RE.search(block)
            title = clean(title_m.group(1)) if title_m else ""
            url = abs_url(f"/detail/{lid}/", SITE)
            href_m = re.search(r'href="([^"]*?/detail/' + lid + r'/)"', block)
            if href_m:
                url = abs_url(href_m.group(1), SITE)
            price_m = PRICE_RE.search(block)
            price = None
            if price_m:
                num = price_m.group(1).replace("\xa0", "").replace(" ", "")
                price = int(num) if num.isdigit() else None
            img_m = IMG_RE.search(block)
            img = img_m.group(1) if img_m else ""
            if img.startswith("//"):
                img = "https:" + img
            addr_m = ADDR_RE.search(block)
            street_m = STREET_RE.search(block)
            locality = addr_m.group(1) if addr_m else ""
            if street_m:
                locality = f"{street_m.group(1)}, {locality}" if locality else street_m.group(1)
            out.append(listing_from_card(
                listing_id=abs(hash(f"eurobydleni_{lid}")) % (10 ** 12),
                name=title,
                url=url,
                price_czk=price,
                price_label=f"{price} Kč" if price else "",
                locality=locality,
                image_url=img,
                offer=ctx,
            ))
        return out

    def _parse_total(self, html: str) -> int:
        m = re.search(r'data-max-page="(\d+)"', html or "")
        if m:
            return int(m.group(1)) * self.PAGE_SIZE
        return super()._parse_total(html)
