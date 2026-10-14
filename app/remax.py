"""RE/MAX Czech list/detail scraper (structured data-* cards)."""

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.html_listing import HtmlPortalClient, with_page, abs_url, clean, listing_from_card, numeric_id, parse_area, parse_disposition, parse_price
from app.portal_urls import remax_url
from app.sreality import Listing

SITE = remax_url.site
CARD_START_RE = re.compile(r'<div class="pl-items__item"', re.I)
TOTAL_RE = re.compile(r"z\s+celkem\s*<span>\s*([\d\s\xa0]+?)\s*</span>", re.I)
ATTR_RE = re.compile(r'data-([a-z\-]{1,40})="([^"]{0,500})"', re.I)
DETAIL_RE = re.compile(r"/reality/detail/(\d{4,12})/([^\"'\s]{1,300})")


def _unescape(value: str) -> str:
    return html_lib.unescape((value or "").replace("&#039;", "'")).replace("\xa0", " ")


class RemaxClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "stranka"
    # 21 cards per page (the parser may drop a card without a detail link).
    PAGE_SIZE = 21
    # The list page is ~260 kB and cards run to the very end; the shared 180 kB
    # truncation cut it to 8 cards.
    FULL_LIST_HTML = True

    def _context(self) -> str:
        raw = (self.search_url or "").lower()
        if "sale=1" in raw or "/prodej" in raw:
            return "prodej"
        return "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        # Legacy ``/reality/byty/?sale=2&order_by_price=0`` 301-redirects to
        # ``/reality/byty/pronajem/?order_by_price=0`` (= most expensive first) and
        # drops nothing but sorts wrongly; go straight to the canonical path.
        split = urlsplit(self.search_url or "")
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        if split.path.rstrip("/") == "/reality/byty" and query.get("sale") in {"1", "2"}:
            path = "/reality/byty/prodej/" if query["sale"] == "1" else "/reality/byty/pronajem/"
            query.pop("sale", None)
            query.pop("order_by_price", None)
            base = urlunsplit((split.scheme or "https", split.netloc or "www.remax-czech.cz", path, urlencode(query), ""))
            return with_page(base, page, self.PAGE_PARAM)
        return super()._page_url(page, newest=newest)

    def _parse_total(self, html: str) -> int:
        m = TOTAL_RE.search((html or "")[:400_000])
        if not m:
            return 0
        digits = re.sub(r"\D", "", m.group(1))
        return int(digits) if digits else 0

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        text = html or ""
        starts = [m.start() for m in CARD_START_RE.finditer(text)]
        blocks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]
        for block in blocks or [text]:
            listing = self._parse_card(block[:60_000], offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        if items:
            return items
        for listing_id, slug in DETAIL_RE.findall(html or ""):
            url = f"{SITE}/reality/detail/{listing_id}/{slug}"
            if url in seen:
                continue
            seen.add(url)
            items.append(
                listing_from_card(
                    listing_id=int(listing_id),
                    name=slug.replace("-", " "),
                    url=url,
                    price_czk=None,
                    price_label="",
                    locality="",
                    offer="pronajem" if "pronajem" in slug else "prodej",
                )
            )
        return items

    def _parse_card(self, html: str, offer: str) -> Listing | None:
        data = {key.replace("-", "_"): _unescape(value) for key, value in ATTR_RE.findall(html)}
        path = data.get("url") or ""
        detail = DETAIL_RE.search(path) or DETAIL_RE.search(html)
        if not detail:
            return None
        listing_id, slug = detail.group(1), detail.group(2)
        url = abs_url(path or f"/reality/detail/{listing_id}/{slug}", SITE)
        title = clean(data.get("title") or slug.replace("-", " "))
        locality = clean(data.get("display_address") or data.get("display-address") or "")
        price_czk, price_label = parse_price(data.get("price") or "")
        img = abs_url(data.get("img") or "", SITE)
        inferred_offer = "pronajem" if "pronajem" in slug or "pronájem" in title.casefold() else offer
        if "prodej" in slug:
            inferred_offer = "prodej"
        return listing_from_card(
            listing_id=numeric_id(listing_id, url),
            name=title,
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            disposition=parse_disposition(title),
            area_m2=parse_area(title),
            image_url=img,
            photos=[img] if img else [],
            offer=inferred_offer,
        )
