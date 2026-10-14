"""Reality.cz list/detail scraper.

Listing pages are server-rendered: real result lists live at locality URLs
(e.g. /pronajem/byty/hlavni-mesto-Praha/), each offer in a
``<div class="xvypis ...">`` block with an EVC link like ``DMQ-003730/?c=...``.
The bare /{offer}/byty/ URL is only a catalogue homepage, not results.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, hash_id, parse_price
from app.portal_urls import realitycz_url
from app.sreality import Listing

SITE = realitycz_url.site
# Listing cards: <div class="xvypis vypismd ... gpsx50.06 gpsy14.39" id="id...">
CARD_START_RE = re.compile(r'<div class="xvypis[^"]*"[^>]{0,200}>', re.I)
GPS_RE = re.compile(r"gpsx(-?\d+\.\d+)\s+gpsy(-?\d+\.\d+)")
TOTAL_RE = re.compile(r'<span class="bld fsbg">\s*([\d\.\s\xa0]+?)\s*</span>\s*(?:&nbsp;|\s)*nabíd', re.I)
# EVC code links: DMQ-003730/?c=..., BTU-N05169/?strana=2&c=...
# (agency prefix is 3 alphanumerics: BNB-, B51-, 862-, 668-, ...)
EVC_RE = re.compile(r'href="([A-Z0-9]{2,4}-[A-Z0-9]{3,8})/\?[^"]{0,300}"', re.I)
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
    # reality.cz ignores ?strana=N / ?sort=N (that is why pages 2..n were copies
    # of page 1). Real paging is ``?g=K`` = K-th "Dalších 25 nabídek" page
    # (page N -> g=N-1; the cursor tail ``-2-291_A_B`` is optional). Page 1 has
    # 2 random "makléři doporučují" ads + 23 organic offers, later pages 25.
    PAGE_PARAM = "g"
    PAGE_SIZE = 25
    FULL_LIST_HTML = True

    def _page_url(self, page: int, newest: bool = True) -> str:
        split = urlsplit(self.search_url or "")
        query = [(k, v) for k, v in parse_qsl(split.query, keep_blank_values=True) if k not in {"g", "strana"}]
        if page > 1:
            query.append(("g", str(page - 1)))
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))

    def _parse_total(self, html: str) -> int:
        m = TOTAL_RE.search((html or "")[:200_000])
        if not m:
            return 0
        digits = re.sub(r"\D", "", m.group(1))
        return int(digits) if digits else 0

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
        starts = [m.start() for m in CARD_START_RE.finditer(text)]
        ends = [m.start() for m in CARD_START_RE.finditer(text)][1:] + [len(text)]
        blocks = []
        for a, b in zip(starts, ends):
            block = text[a:b]
            cut = block.find('<div class="strankovani')
            blocks.append(block[:cut] if cut >= 0 else block[:12_000])
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
        lat = lon = None
        gps = GPS_RE.search(html[:400])
        if gps and float(gps.group(1)) and float(gps.group(2)):
            lat, lon = float(gps.group(1)), float(gps.group(2))
        name = title
        if desc and desc not in title:
            name = f"{title} - {desc}"
        return listing_from_card(
            # digits-only ids collide across agencies (DMQ-003736 vs BNB-003736)
            listing_id=hash_id(evc),
            name=name or evc,
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            image_url=img,
            photos=[img] if img else [],
            offer=offer,
            lat=lat,
            lon=lon,
        )
