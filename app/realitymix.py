"""RealityMIX.cz list/detail scraper."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card
from app.sreality import Listing

SITE = "https://realitymix.cz"

DETAIL_RE = re.compile(
    r'href="(https://realitymix\.cz/detail/[^"]+-(\d{7})\.html)"', re.I
)
TITLE_RE = re.compile(
    r'class="[^"]*text-lg sm:text-xl font-extrabold[^"]*"[^>]*>(.*?)<',
    re.S | re.I,
)
PRICE_RE = re.compile(r'([\d\s\xa0]{4,12})\s*Kč', re.I)
ADDR_RE = re.compile(
    r'class="[^"]*text-sm sm:text-base text-body-light[^"]*"[^>]*>(.*?)<',
    re.S | re.I,
)
IMG_RE = re.compile(
    r'<img[^>]*class="[^"]*absolute inset-0[^"]*"[^>]*src="([^"]+)"', re.I
)


def _parse_price(text: str) -> tuple[int | None, str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, ""
    label = m.group(0).strip()
    num = m.group(1).replace("\xa0", "").replace(" ", "").strip()
    return (int(num) if num.isdigit() else None), label


class RealitymixClient(HtmlPortalClient):
    SITE = SITE
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "prodej" in path else "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        base = self.search_url or ""
        if page <= 1:
            return base
        sep = "&" if "?" in base else "?"
        return f"{base}{sep}stranka={page}"

    def _parse_list(self, html: str) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        ctx = self._context()
        blocks = re.findall(
            r'<li class="[^"]*advert-item[^"]*">(.*?)</li>', html or "", re.S | re.I
        )
        for block in blocks:
            dm = DETAIL_RE.search(block)
            if not dm:
                continue
            url, lid = dm.group(1), dm.group(2)
            if lid in seen:
                continue
            seen.add(lid)
            tm = TITLE_RE.search(block)
            title = clean(tm.group(1)) if tm else ""
            price, price_label = _parse_price(block)
            am = ADDR_RE.search(block)
            locality = clean(am.group(1)) if am else ""
            im = IMG_RE.search(block)
            img = im.group(1) if im else ""
            out.append(listing_from_card(
                listing_id=abs(hash(f"realitymix_{lid}")) % (10 ** 12),
                name=title,
                url=url,
                price_czk=price,
                price_label=price_label,
                locality=locality,
                image_url=img,
                offer=ctx,
            ))
        return out
