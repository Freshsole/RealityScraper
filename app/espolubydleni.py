"""eSpolubydleni.cz (spolubydlení) list/detail scraper."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card
from app.sreality import Listing

SITE_HTTPS = "https://www.espolubydleni.cz"

DETAIL_RE = re.compile(
    r'<a href="(/podnajem-spolubydlici/(\d+)-[^"]+\.htm)" title="([^"]+)"', re.I
)
TITLE_RE = re.compile(r'<h3 class="popis"[^>]*>(.*?)</h3>', re.S | re.I)
PRICE_RE = re.compile(r'([\d\.]+)\s*Kč/měsíc', re.I)
CITY_RE = re.compile(r'title="city/quarter[^"]*"[^>]*>(.*?)</', re.S | re.I)


def _parse_price(text: str) -> tuple[int | None, str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, ""
    label = m.group(0).strip()
    num = m.group(1).replace(".", "").strip()
    return (int(num) if num.isdigit() else None), label


class EspolubydleniClient(HtmlPortalClient):
    SITE = SITE_HTTPS
    PAGE_SIZE = 8

    def _context(self) -> str:
        return "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        base = "https://www.espolubydleni.cz/podnajem-spolubydlici"
        if page <= 1:
            return base + "/"
        return f"{base}/{page}"

    def _parse_list(self, html: str) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        for href, lid, title_attr in DETAIL_RE.findall(html or ""):
            if lid in seen:
                continue
            seen.add(lid)
            url = abs_url(href, SITE_HTTPS)
            # title="Nabízím 1x místo 1L pokoj, Cihlový byt 5+1(kk)\n Plzeň\n8.000Kč/měsíc"
            parts = title_attr.split("\n")
            title = clean(parts[0]) if parts else ""
            locality = clean(parts[1]) if len(parts) > 1 else ""
            price, price_label = _parse_price(title_attr)
            out.append(listing_from_card(
                listing_id=abs(hash(f"espolubydleni_{lid}")) % (10 ** 12),
                name=title or f"Spolubydlení {locality}",
                url=url,
                price_czk=price,
                price_label=price_label,
                locality=locality,
                offer="pronajem",
            ))
        return out
