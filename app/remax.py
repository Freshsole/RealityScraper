"""RE/MAX Czech list/detail scraper (structured data-* cards)."""

from __future__ import annotations

import html as html_lib
import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_area, parse_disposition, parse_price
from app.portal_urls import remax_url
from app.sreality import Listing

SITE = remax_url.site
CARD_RE = re.compile(r'<div class="pl-items__item"([^>]*)>(.*?)</div>\s*(?=<div class="pl-items__item"|<nav|$)', re.S | re.I)
ATTR_RE = re.compile(r'data-([a-z\-]+)="([^"]*)"', re.I)
DETAIL_RE = re.compile(r"/reality/detail/(\d+)/([^\"'\s]+)")


def _unescape(value: str) -> str:
    return html_lib.unescape((value or "").replace("&#039;", "'")).replace("\xa0", " ")


class RemaxClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "stranka"
    PAGE_SIZE = 20

    def _context(self) -> str:
        raw = (self.search_url or "").lower()
        if "sale=1" in raw or "/prodej" in raw:
            return "prodej"
        return "pronajem"

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for attrs, body in CARD_RE.findall(html or "") or [("", html or "")]:
            listing = self._parse_card(attrs + " " + body, offer)
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
