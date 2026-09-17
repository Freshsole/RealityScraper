"""Annonce reality list/detail scraper."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price, with_page
from app.portal_urls import annonce_url

SITE = annonce_url.site
# Lookahead so the next card opener is not consumed (apartments: ext-item; houses: slideshow item).
CARD_RE = re.compile(
    r'<div class="box q (?:ext-item|slideshow item)[^"]*">(.*?)(?=<div class="box q (?:ext-item|slideshow item)|<div class="rows js-more|$)',
    re.S | re.I,
)
CARD2_RE = re.compile(
    r'class="box q (?:ext-item|slideshow item)[^"]*"(.*?)(?=class="box q (?:ext-item|slideshow item)|js-more-ads|$)',
    re.S | re.I,
)
INZERAT_RE = re.compile(r"""href=["'](/inzerat/[^"']*?-(\d{6,})-[a-z0-9]+\.html)["']""", re.I)
TITLE_RE = re.compile(r"<h2[^>]*>\s*<a[^>]*>(.*?)</a>", re.S | re.I)
DATE_RE = re.compile(r'class="ad-date"[^>]*>(.*?)</div>', re.S | re.I)
IMG_RE = re.compile(r'<img[^>]+src="(https://static\.annonce\.cz/[^"]+)"', re.I)
ATTR_RE = re.compile(r"<th[^>]*>\s*([^<:]+):\s*</th>\s*<td[^>]*>(.*?)</td>", re.S | re.I)
PRICE_RE = re.compile(
    r'class="[^"]*(?:price|mini-sticker)[^"]*"[^>]*>\s*(?:<span[^>]*>)?(.*?)</',
    re.S | re.I,
)
CITY_RE = re.compile(
    r'<td[^>]*class="[^"]*right[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>',
    re.S | re.I,
)
DEMAND_RE = re.compile(r'class="request-type"[^>]*>\s*Poptávka', re.S | re.I)


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
    PAGE_PARAM = "page"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "prodej" in path else "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        return with_page(self.search_url, page, self.PAGE_PARAM)

    def _parse_list(self, html: str) -> list[Listing]:
        blocks = CARD_RE.findall(html or "") or CARD2_RE.findall(html or "")
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for block in blocks:
            listing = self._parse_card(block, offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        for listing in self._parse_hrefs(html or "", offer, seen):
            seen.add(listing.url)
            items.append(listing)
        return items

    def _parse_hrefs(self, html: str, offer: str, seen: set[str]) -> list[Listing]:
        """Houses and changed markup expose /inzerat/ ids without box wrappers."""
        items: list[Listing] = []
        for path, _listing_id in INZERAT_RE.findall(html):
            url = abs_url(path, SITE)
            if url in seen:
                continue
            idx = html.find(path)
            window = html[max(0, idx - 500) : idx + 1600] if idx >= 0 else f'href="{path}"'
            listing = self._parse_card(window, offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        return items

    def _parse_card(self, html: str, offer: str) -> Listing | None:
        if DEMAND_RE.search(html):
            return None
        match = INZERAT_RE.search(html)
        if not match:
            return None
        path, listing_id = match.group(1), match.group(2)
        url = abs_url(path, SITE)
        title_m = TITLE_RE.search(html)
        title = clean(title_m.group(1) if title_m else "")
        attrs = {clean(key).casefold(): clean(value) for key, value in ATTR_RE.findall(html)}
        locality = (
            attrs.get("lokalita")
            or attrs.get("obec")
            or attrs.get("město")
            or attrs.get("mesto")
            or attrs.get("umístění")
            or attrs.get("umisteni")
            or ""
        )
        if not locality:
            city_m = CITY_RE.search(html)
            locality = clean(city_m.group(1) if city_m else "")
        disp = attrs.get("dispozice") or ""
        area = None
        area_raw = attrs.get("plocha") or attrs.get("výměra") or attrs.get("vymera") or ""
        if area_raw:
            digits = re.sub(r"[^\d]", "", area_raw.split("m")[0])
            area = int(digits) if digits else None
        price_m = PRICE_RE.search(html)
        price_czk, price_label = parse_price(price_m.group(1) if price_m else html)
        img_m = IMG_RE.search(html)
        img = img_m.group(1) if img_m else ""
        date_m = DATE_RE.search(html)
        created = parse_annonce_date(date_m.group(1) if date_m else "")
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
