"""ČeskéReality list/detail scraper (HTML cards + image IDs)."""

from __future__ import annotations

import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price
from app.portal_urls import ceskereality_url
from app.sreality import Listing

SITE = ceskereality_url.site
CARD_RE = re.compile(r'(<article[^>]*class="[^"]*i-estate[^"]*"[^>]*>.*?</article>)', re.S | re.I)
CARD2_RE = re.compile(r'(class="[^"]*i-estate[^"]*".*?)(?:class="[^"]*i-estate[^"]*"|$)', re.S | re.I)
LISTING_HREF_RE = re.compile(
    r"""href=["']((?:https://www\.ceskereality\.cz)?/(?:pronajem|prodej)/[^"']+)["']""",
    re.I,
)
ID_ATTR_RE = re.compile(r'id-nemovitosti=["\'](\d+)["\']', re.I)
ID_RE = re.compile(r"(?:-|/)(\d{5,})(?:\.html(?:$|[?#])|[-/]|$)")
IMG_ID_RE = re.compile(r"img-cache\.ceskereality\.cz/nemovitosti/(?![\w-]*x[\w-]*/)[^/]+/(\d+)/", re.I)
TITLE_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
ALT_RE = re.compile(r'alt="([^"]+)"')
PRICE_BOX_RE = re.compile(
    r"(?:i-estate__footer-price-value|i-estate__price|price)[^>]*>(.*?)</",
    re.S | re.I,
)
TOTAL_RE = re.compile(r"(?:vybírat ze|máme tady)\s+([\d\s\u00a0]+)\s+byt", re.I)
SKIP_HREF = ("muj-profil", "redirect=", "/mapa/", "?sff=", "nejnovejsi/", "nejlevnejsi/")


class CeskerealityClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _parse_total(self, html: str) -> int:
        match = TOTAL_RE.search((html or "").replace("\xa0", " "))
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)
        return super()._parse_total(html)

    def _parse_list(self, html: str) -> list[Listing]:
        blocks = CARD_RE.findall(html or "") or CARD2_RE.findall(html or "")
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

    def _listing_href(self, html: str) -> str:
        for href in LISTING_HREF_RE.findall(html or ""):
            raw = href.split("#")[0]
            folded = raw.casefold()
            if any(skip in folded for skip in SKIP_HREF):
                continue
            if ".html" in folded or ID_RE.search(raw):
                return raw
        return ""

    def _parse_card(self, html: str, offer: str) -> Listing | None:
        href = self._listing_href(html)
        url = abs_url(href, SITE) if href else ""
        listing_id = ""
        attr = ID_ATTR_RE.search(html or "")
        if attr:
            listing_id = attr.group(1)
        if not listing_id and url:
            id_m = ID_RE.search(url)
            listing_id = id_m.group(1) if id_m else ""
        if not listing_id:
            img_id = IMG_ID_RE.search(html or "")
            if img_id:
                listing_id = img_id.group(1)
                url = url or f"{SITE}/{offer}/byty/{listing_id}/"
        if not listing_id:
            return None
        if not url or "muj-profil" in url:
            url = f"{SITE}/{offer}/byty/{listing_id}/"
        title_m = TITLE_RE.search(html) or ALT_RE.search(html)
        title = clean(title_m.group(1) if title_m else "")
        price_m = PRICE_BOX_RE.search(html)
        price_czk, price_label = parse_price(price_m.group(1) if price_m else html)
        locality = ""
        if title:
            parts = re.split(r"\s+\d+\s*m", title, maxsplit=1)
            if len(parts) > 1:
                locality = clean(re.sub(r"^²\s*", "", parts[1]))
            elif "," in title:
                locality = title.split(",")[-1].strip()
        img = ""
        img_m = re.search(r'src="(https://img-cache\.ceskereality\.cz/[^"]+)"', html)
        if img_m:
            img = img_m.group(1).replace("/320x320_", "/640x640_").replace("/32x32_", "/640x640_")
        return listing_from_card(
            listing_id=numeric_id(listing_id, url),
            name=title or f"{'Pronájem' if offer == 'pronajem' else 'Prodej'} bytu",
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            image_url=img,
            photos=[img] if img else [],
            offer=offer,
        )
