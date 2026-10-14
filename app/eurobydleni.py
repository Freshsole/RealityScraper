"""Eurobydleni.cz list/detail scraper."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id
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


AJAX_URL = SITE + "/mlift/api/ajaxPageLoad.php"


class EurobydleniClient(HtmlPortalClient):
    """eurobydleni.cz: /byty/{pronajem|prodej}/ [page-N/], 12 cards, newest first (sort_date DESC).

    The list HTML is ~880 kB (0.3-2 s). The site's own "load more" XHR
    (POST /mlift/api/ajaxPageLoad.php, href=<list path with page-N/>) returns the
    same cards as JSON in ~40-130 ms / 87 kB, plus ``max-page``. It answers for the
    search stored in the PHP session by the last list GET, so the first page of a
    client is fetched as HTML (warms the session) and later pages use the XHR.
    Any XHR anomaly (redirect, no cards) falls back to the HTML page.
    """

    SITE = SITE
    PAGE_SIZE = 12
    FULL_LIST_HTML = True
    USE_AJAX = True

    def __init__(self, search_url: str) -> None:
        super().__init__(search_url)
        self._session_warm = False
        self._max_page = 0

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
                listing_id=numeric_id(lid, url),
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
        # Upper bound (max-page * 12); fetch_page refines it with the last page's row count.
        m = re.search(r'data-max-page="(\d+)"', html or "")
        if m:
            return int(m.group(1)) * self.PAGE_SIZE
        return 0

    def _ajax_path(self, page: int) -> str:
        base = "/" + (self.search_url or "").split(SITE, 1)[-1].strip("/") + "/"
        return base if page <= 1 else f"{base}page-{page}/"

    async def _fetch_ajax(self, page: int) -> tuple[list[Listing], int, bool] | None:
        """(listings, max_page, is_last) from the XHR, or None when it did not answer with cards."""
        import json

        from app.scrape_http import request_with_log
        from app.sources import portal_of

        response = await request_with_log(
            self._client,
            "POST",
            AJAX_URL,
            portal=portal_of(SITE),
            data={"href": self._ajax_path(page), "loadMore": "false"},
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": self._page_url(1),
            },
        )
        if response.status_code != 200:
            return None
        try:
            data = json.loads(response.content)
        except ValueError:
            return None
        cards = data.get("properties-box") if isinstance(data, dict) else None
        if not isinstance(cards, str) or "list-items__item" not in cards:
            return None
        listings = self._parse_list(cards)
        return listings, int(data.get("max-page") or 0), bool(data.get("is-last-page"))

    _total_cache: dict[str, tuple[float, int]] = {}

    async def _exact_total(self, max_page: int) -> int:
        """(max_page - 1) * 12 + rows on the last page; one cheap XHR, cached 10 min per search."""
        import time

        key = self._ajax_path(1)
        stamp, cached = EurobydleniClient._total_cache.get(key, (0.0, 0))
        if cached and time.monotonic() - stamp < 600:
            return cached
        got = await self._fetch_ajax(max_page)
        if got is None or not got[0]:
            return max_page * self.PAGE_SIZE
        total = (max_page - 1) * self.PAGE_SIZE + len(got[0])
        EurobydleniClient._total_cache[key] = (time.monotonic(), total)
        return total

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        if page > 1 and self._max_page and page > self._max_page:
            return [], 0  # the site wraps out-of-range pages back to page 1
        if self.USE_AJAX and self._session_warm:
            try:
                got = await self._fetch_ajax(page)
            except Exception:
                got = None
            if got is not None:
                listings, max_page, is_last = got
                self._max_page = max_page or self._max_page
                total = 0
                if page <= 1 and max_page:
                    try:
                        total = await self._exact_total(max_page)
                    except Exception:
                        total = max_page * self.PAGE_SIZE
                return listings, total
        listings, total = await super().fetch_page(page, newest=newest)
        if listings:
            self._session_warm = True
            if total:
                self._max_page = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE
                if page <= 1 and self.USE_AJAX:
                    try:
                        total = await self._exact_total(self._max_page)
                    except Exception:
                        pass
        return listings, total
