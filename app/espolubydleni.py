"""eSpolubydleni.cz (spolubydlení) list/detail scraper."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id
from app.sreality import Listing

SITE_HTTPS = "https://www.espolubydleni.cz"

DETAIL_RE = re.compile(
    r'<a href="(/podnajem-spolubydlici/(\d+)-[^"]+\.htm)"\s+title="([^"]+)"', re.I | re.S
)
TITLE_RE = re.compile(r'<h3 class="popis"[^>]*>(.*?)</h3>', re.S | re.I)
PRICE_RE = re.compile(r'([\d\.]+)\s*Kč/měsíc', re.I)
ROW_RE = re.compile(r'<tr id="tr\d+">(.*?)(?=<tr id="tr\d+">|</table>|$)', re.S | re.I)
ROW_HREF_RE = re.compile(r'href="(/podnajem-spolubydlici/(\d+)-[^"]+\.htm)"', re.I)
KIND_RE = re.compile(r'<strong class="s1"[^>]*>(.*?)</strong>', re.S | re.I)
FLAT_RE = re.compile(r'<b style="font-size:90%[^"]*">(.*?)</b>', re.S | re.I)
LOC_RE = re.compile(r'title="city/quarter[^"]*"[^>]*>\s*<strong[^>]*>(.*?)</strong>(.*?)</div>', re.S | re.I)
ROW_PRICE_RE = re.compile(r'<b>([\d\.]+)\s*Kč/měsíc</b>', re.I)
ADDED_RE = re.compile(r'vloženo dne">(\d{1,2})\.(\d{1,2})\.(\d{4})', re.I)
NEXT_LINK_RE = re.compile(r'/podnajem-spolubydlici/(\d+)"[^>]*>\s*Další', re.I)
CITY_RE = re.compile(r'title="city/quarter[^"]*"[^>]*>(.*?)</', re.S | re.I)


def _parse_price(text: str) -> tuple[int | None, str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, ""
    label = m.group(0).strip()
    num = m.group(1).replace(".", "").strip()
    return (int(num) if num.isdigit() else None), label


class EspolubydleniClient(HtmlPortalClient):
    """Listing pages: /podnajem-spolubydlici/ (page 1), /podnajem-spolubydlici/N.

    8 rows per page, ~250 pages (~2 000 offers), newest first after the paid TOP
    rows. Page 1 additionally carries a strip of 5 TOP thumbnails (dedup by id).
    """

    SITE = SITE_HTTPS
    PAGE_SIZE = 8
    FULL_LIST_HTML = True

    def _context(self) -> str:
        return "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        base = "https://www.espolubydleni.cz/podnajem-spolubydlici"
        if page <= 1:
            return base + "/"
        return f"{base}/{page}"

    _total_cache: tuple[float, int] = (0.0, 0)

    def _parse_total(self, html: str) -> int:
        # The page has no result counter and the pager only shows a window of
        # pages, so the total is derived in fetch_page (see _real_total).
        return 0

    async def _fetch_text(self, url: str) -> str:
        from app.scrape_http import request_with_log
        from app.sources import portal_of

        response = await request_with_log(
            self._client, "GET", url, portal=portal_of(self.SITE), headers={"Accept": "text/html"}
        )
        return response.text if response.status_code == 200 else ""

    async def _real_total(self) -> int:
        """Exact offer count: an out-of-range page links "Další »" to the last page.

        Two cheap requests (/900 -> last page number, /<last> -> its row count),
        cached for 10 minutes for the whole process.
        """
        import time

        stamp, cached = EspolubydleniClient._total_cache
        if cached and time.monotonic() - stamp < 600:
            return cached
        probe = await self._fetch_text(self._page_url(900))
        m = NEXT_LINK_RE.search(probe)
        if not m:
            return 0
        last = int(m.group(1))
        rows = len(self._parse_list(await self._fetch_text(self._page_url(last)))) if last > 1 else 0
        total = max(last - 1, 0) * self.PAGE_SIZE + rows
        if total:
            EspolubydleniClient._total_cache = (time.monotonic(), total)
        return total

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        listings, total = await super().fetch_page(page, newest=newest)
        if page <= 1 and listings:
            try:
                total = await self._real_total() or total
            except Exception:
                pass
        return listings, total

    def _parse_list(self, html: str) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        text = html or ""
        for row in ROW_RE.findall(text):
            hm = ROW_HREF_RE.search(row)
            if not hm or hm.group(2) in seen:
                continue
            href, lid = hm.group(1), hm.group(2)
            seen.add(lid)
            kind_m = KIND_RE.search(row)
            flat_m = FLAT_RE.search(row)
            kind = clean(kind_m.group(1)) if kind_m else ""
            flat = clean(flat_m.group(1)) if flat_m else ""
            loc_m = LOC_RE.search(row)
            city = clean(loc_m.group(1)) if loc_m else ""
            street = clean(loc_m.group(2)).lstrip(", ") if loc_m else ""
            locality = f"{city}, {street}" if city and street else city
            price_m = ROW_PRICE_RE.search(row)
            price = None
            price_label = ""
            if price_m:
                price_label = f"{price_m.group(1)}Kč/měsíc"
                num = price_m.group(1).replace(".", "")
                price = int(num) if num.isdigit() else None
            added_m = ADDED_RE.search(row)
            created = f"{added_m.group(3)}-{int(added_m.group(2)):02d}-{int(added_m.group(1)):02d}" if added_m else None
            title = ", ".join(part for part in (kind, flat) if part)
            out.append(listing_from_card(
                listing_id=numeric_id(lid, href),
                name=title or f"Spolubydlení {locality}",
                url=abs_url(href, SITE_HTTPS),
                price_czk=price,
                price_label=price_label,
                locality=locality,
                offer="pronajem",
                created_on=created,
            ))
        if out:
            return out
        # Fallback: anchors carrying the card text in title="..." (older markup / thumbnail strip).
        for href, lid, title_attr in DETAIL_RE.findall(text):
            if lid in seen:
                continue
            seen.add(lid)
            parts = title_attr.split("\n")
            title = clean(parts[0]) if parts else ""
            locality = clean(parts[1]) if len(parts) > 1 else ""
            price, price_label = _parse_price(title_attr)
            out.append(listing_from_card(
                listing_id=numeric_id(lid, href),
                name=title or f"Spolubydlení {locality}",
                url=abs_url(href, SITE_HTTPS),
                price_czk=price,
                price_label=price_label,
                locality=locality,
                offer="pronajem",
            ))
        return out
