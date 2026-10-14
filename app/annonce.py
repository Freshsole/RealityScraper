"""Annonce reality list/detail scraper."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price, with_page
from app.portal_urls import annonce_url
from app.sreality import Listing

SITE = annonce_url.site
CARD_START_RE = re.compile(r'<div class="box q ext-item[^"]{0,80}">', re.I)
# The card list ends at the pager comment / "load more" button.
CARD_END_RE = re.compile(r'<!--\s*-t-\s*DDv actualPage|<div class="center">\s*<a class="btn next-queue-page|class="rows js-more', re.I)


def split_cards(html: str) -> list[str]:
    """Slice the page into one HTML block per listing card (adjacent cards must not be consumed twice)."""
    raw = html or ""
    starts = [m.start() for m in CARD_START_RE.finditer(raw)]
    blocks: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(len(raw), start + 40_000)
        if i + 1 == len(starts):
            tail = CARD_END_RE.search(raw, start + 1, end)
            if tail:
                end = tail.start()
        blocks.append(raw[start:end])
    return blocks


INZERAT_RE = re.compile(r'href="(/inzerat/[^"]{1,300}-(\d{6,12})-[a-z0-9]+\.html)"', re.I)
TITLE_RE = re.compile(r"<h2[^>]{0,120}>\s*<a[^>]{0,300}>(.{0,500}?)</a>", re.S | re.I)
DATE_RE = re.compile(r'class="ad-date"[^>]{0,80}>(.{0,200}?)</div>', re.S | re.I)
IMG_RE = re.compile(r'<img[^>]{1,400}src="(https://static\.annonce\.cz/[^"]{1,400})"', re.I)
ATTR_RE = re.compile(r"<th[^>]{0,80}>\s*([^<:]{1,80}):\s*</th>\s*<td[^>]{0,80}>(.{0,400}?)</td>", re.S | re.I)
# Pager state is left in an HTML comment: "actualPage: 1, pageCount: 75, totalCount: 1485, position: 0, perPage: 20".
PAGER_RE = re.compile(r"actualPage:\s*(\d+),\s*pageCount:\s*(\d+),\s*totalCount:\s*(\d+)")
PRICE_RE = re.compile(r'class="[^"]{0,80}(?:price|mini-sticker)[^"]{0,80}"[^>]{0,80}>(?:\s*<span[^>]{0,80}>)?(.{0,400}?)</', re.S | re.I)
LOCALITY_RE = re.compile(r'<a href="[^"]{0,200}-f-R\d+\.html"[^>]{0,80}>([^<]{1,120})</a>', re.I)


def parse_annonce_date(text: str) -> str | None:
    match = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", clean(text))
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return datetime(year, month, day, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


class AnnonceClient(HtmlPortalClient):
    SITE = SITE
    # Real pager param is "page" ("strana" is silently ignored -> page 1 every time).
    # Pages past the last one render page 1 again (actualPage in PAGER_RE tells).
    PAGE_PARAM = "page"
    PAGE_SIZE = 20
    FULL_LIST_HTML = True
    # Annonce blokuje datacentrové IP - zkusíme vypadat jako reálný prohlížeč
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
        # Přidáme browser-like hlavičky
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
            await self._client.get("https://www.annonce.cz/", timeout=15)
            await asyncio.sleep(2)  # pauza jako člověk
        except Exception:
            pass

    async def fetch_page(self, page: int = 1, **kwargs):
        await self._warmup()
        return await super().fetch_page(page, **kwargs)

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "prodej" in path else "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        # Default order is "ageasc" (newest / most recently refreshed first).
        return with_page(self.search_url, page, self.PAGE_PARAM)

    def _parse_total(self, html: str) -> int:
        match = PAGER_RE.search(html or "")
        return int(match.group(3)) if match else 0

    def _parse_blob(self, blob: bytes, page: int):
        listings, total = super()._parse_blob(blob, page)
        match = PAGER_RE.search(blob.decode("utf-8", "replace"))
        if match and page > 1 and int(match.group(1)) != page:
            # Out-of-range page: the portal falls back to page 1; do not return it as page N.
            return [], total
        return listings, total

    def _parse_list(self, html: str) -> list[Listing]:
        blocks = split_cards(html)
        if not blocks:
            blocks = [html or ""]
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for block in blocks:
            listing = self._parse_card(block, offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        return items

    def _parse_card(self, html: str, offer: str) -> Listing | None:
        match = INZERAT_RE.search(html)
        if not match:
            return None
        path, listing_id = match.group(1), match.group(2)
        url = abs_url(path, SITE)
        title_m = TITLE_RE.search(html)
        title = clean(title_m.group(1) if title_m else "")
        attrs = {clean(key).casefold(): clean(value) for key, value in ATTR_RE.findall(html)}
        locality = attrs.get("lokalita") or attrs.get("obec") or attrs.get("umístění") or attrs.get("umisteni") or ""
        if not locality:
            loc_m = LOCALITY_RE.search(html)
            locality = clean(loc_m.group(1)) if loc_m else ""
        disp = attrs.get("dispozice") or ""
        area = None
        area_raw = attrs.get("plocha") or attrs.get("výměra") or attrs.get("vymera") or ""
        if area_raw:
            digits = re.sub(r"[^\d]", "", area_raw.split("m")[0])
            area = int(digits) if digits else None
        price_m = PRICE_RE.search(html)
        if price_m:
            price_czk, price_label = parse_price(price_m.group(1))
        else:
            # Never fall back to the whole card HTML — that stores title/scripts as price_label.
            price_czk, price_label = None, ""
        img_m = IMG_RE.search(html)
        img = img_m.group(1) if img_m else ""
        date_m = DATE_RE.search(html)
        created = parse_annonce_date(date_m.group(1) if date_m else "")
        if title:
            title = re.sub(r"\s*ID nabídky:\s*$", "", title, flags=re.I).strip(" .–-")
        return listing_from_card(
            listing_id=numeric_id(listing_id, url),
            name=title,
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            disposition=disp,
            area_m2=area,
            image_url=img,
            photos=[img] if img else [],
            offer=offer,
            created_on=created,
            extras={"agency": attrs.get("inzerent") or attrs.get("firma") or ""},
        )
