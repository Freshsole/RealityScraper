"""Reality.cz list/detail scraper.

Listing pages are server-rendered: real result lists live at locality URLs
(e.g. /pronajem/byty/hlavni-mesto-Praha/), each offer in a
``<div class="xvypis ...">`` block with an EVC link like ``DMQ-003730/?c=...``.
The bare /{offer}/byty/ URL is only a catalogue homepage, not results.
"""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price
from app.portal_urls import realitycz_url
from app.sreality import Listing

SITE = realitycz_url.site
# Listing card blocks: <div class="xvypis ..."> ... </div>
CARD_RE = re.compile(
    r'<div class="xvypis[^"]*"[^>]{0,200}>(.{0,12000}?)</div>\s*(?=<div class="xvypis|<div class="cll|$)',
    re.S | re.I,
)
# EVC code links: DMQ-003730/?c=..., BTU-N05169/?strana=2&c=...
EVC_RE = re.compile(r'href="([A-Z]{2,4}-[A-Z0-9]{3,8})/\?[^"]{0,300}"', re.I)
TITLE_RE = re.compile(
    r'<p class="vypisnaz"[^>]{0,120}>\s*<a[^>]{0,400}>(.{1,400}?)</a>',
    re.S | re.I,
)
DESC_RE = re.compile(
    r'<p class="lokalita[^"]*"[^>]{0,120}>(.{1,400}?)</p>',
    re.S | re.I,
)
PRICE_RE = re.compile(
    r'<p class="vypiscena"[^>]{0,120}>(.{1,400}?)</p>',
    re.S | re.I,
)
IMG_RE = re.compile(r'<img[^>]{0,400}src="(/thumb/[^"]{1,200}\.(?:jpg|jpeg|webp))"', re.I)
IMG_ABS_RE = re.compile(r'(?:src|data-src)="(https://[^"]{1,400}\.(?:jpg|jpeg|webp)[^"]{0,200})"', re.I)


class RealityczClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    # Site-side pagination via URL params does not advance the result set
    # (JS-driven); one page carries ~25 offers.
    PAGE_SIZE = 25
    FULL_LIST_HTML = True

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _parse_list(self, html: str) -> list[Listing]:
        text = html or ""
        if "údržba server" in text.casefold() or "udrzba server" in text.casefold():
            return []
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        blocks = CARD_RE.findall(text)
        if not blocks:
            # Fallback: scan whole page for EVC links (older markup).
            blocks = [text]
        for block in blocks:
            listing = self._parse_card(block, offer)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        return items

    def _parse_card(self, html: str, offer: str) -> Listing | None:
        evc_m = EVC_RE.search(html or "")
        if not evc_m:
            return None
        evc = evc_m.group(1).upper()
        url = f"{SITE}/{evc}/"
        title_m = TITLE_RE.search(html)
        title = clean(title_m.group(1)) if title_m else evc
        desc_m = DESC_RE.search(html)
        desc = clean(desc_m.group(1)) if desc_m else ""
        price_m = PRICE_RE.search(html)
        price_czk, price_label = parse_price(price_m.group(1)) if price_m else (None, "")
        locality = ""
        if "," in title:
            locality = title.split(",")[-1].strip()
        img = ""
        img_m = IMG_RE.search(html) or IMG_ABS_RE.search(html)
        if img_m:
            img = abs_url(img_m.group(1), SITE)
        name = title
        if desc and desc not in title:
            name = f"{title} - {desc}"
        return listing_from_card(
            listing_id=numeric_id(evc, url),
            name=name or evc,
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            image_url=img,
            photos=[img] if img else [],
            offer=offer,
        )
