"""Reality.cz list/detail scraper. Search HTML uses vypis cards; maintenance is detected up-stack."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.block_page import classify_block
from app.html_listing import (
    HtmlPortalClient,
    abs_url,
    clean,
    listing_from_card,
    numeric_id,
    parse_area,
    parse_disposition,
    parse_price,
)
from app.portal_urls import realitycz_url
from app.sreality import Listing

SITE = realitycz_url.site
HREF_RE = re.compile(
    r'href="((?:https://www\.reality\.cz)?/(?:detail|nemovitost|inzerat)/[^"]+)"',
    re.I,
)
HREF2_RE = re.compile(r'href="((?:https://www\.reality\.cz)?/(?:pronajem|prodej)/[^"]+/\d+[^"]*)"', re.I)
CODE_HREF_RE = re.compile(
    r"""href=["'](?:https://www\.reality\.cz/)?/?([A-Z0-9]{2,4}-[A-Z0-9]{3,})/?""",
    re.I,
)
BOOKMARK_RE = re.compile(r"/(?:bookmark|hide)-([A-Z0-9]{2,4}-[A-Z0-9]{3,})", re.I)
CARD_RE = re.compile(
    r'(<div class="xvypis[^"]*".*?)(?=<div class="xvypis\b|id="strankovani"|$)',
    re.S | re.I,
)
GPS_RE = re.compile(r"gpsx(-?\d+(?:\.\d+)?)\s+gpsy(-?\d+(?:\.\d+)?)", re.I)
CODE_IN_CARD_RE = re.compile(
    r"(?:bookmark-|hide-|href=\"(?:https://www\.reality\.cz/)?/?)([A-Z0-9]{2,4}-[A-Z0-9]{3,})",
    re.I,
)
ID_RE = re.compile(r"/(\d{4,})")
TITLE_RE = re.compile(r"<h[123][^>]*>(.*?)</h[123]>", re.S | re.I)
IMG_RE = re.compile(r'(?:src|data-src)="([^"]+\.(?:jpg|jpeg|webp)[^"]*)"', re.I)
THUMB_RE = re.compile(r'(?:src|data-src)="([^"]*/thumb/[^"]+)"', re.I)
VYPIS_TITLE_RE = re.compile(r'class="vypisnaz"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S | re.I)
LOKALITA_RE = re.compile(r'class="lokalita[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
PRICE_BOX_RE = re.compile(r'class="vypiscena"[^>]*>(.*?)</p>', re.S | re.I)
TOTAL_RE = re.compile(
    r"(?:<span class=\"bld fsbg\">([\d\s\u00a0.]+)</span>\s*(?:&nbsp;)?\s*nabídek|([\d\s\u00a0.]+)\s+nabídek)",
    re.I,
)
SKIP_IMG = ("/images/ikony/", "/photo/", "makler", "logo", "icon")
SKIP_CODES = {"cena-0", "cena-1", "cena-2", "cena-3", "cena-5", "cena-7"}
SPEC_TITLE_RE = re.compile(r"^\s*byt\b|\d+\s*\+\s*(kk|1)|m\s*²|garson", re.I)


class RealityczClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "g"
    PAGE_SIZE = 25

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _estate(self) -> str:
        path = (self.search_url or "").lower()
        if "/domy/" in path or "/dum/" in path:
            return "dum"
        if "/pozem" in path:
            return "pozemek"
        return "byt"

    def _page_url(self, page: int, newest: bool = True) -> str:
        split = urlsplit(self.search_url or SITE)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        query.pop("strana", None)
        query.pop("sort", None)
        if newest:
            query["s"] = "2"
        if page <= 1:
            query.pop("g", None)
        else:
            query["g"] = f"{page - 1}-2"
        return urlunsplit(
            (
                split.scheme or "https",
                split.netloc or "www.reality.cz",
                split.path or "/",
                urlencode(query, doseq=True),
                "",
            )
        )

    def _parse_total(self, html: str) -> int:
        match = TOTAL_RE.search((html or "").replace("\xa0", " "))
        if match:
            digits = re.sub(r"\D", "", match.group(1) or match.group(2) or "")
            if digits:
                return int(digits)
        return super()._parse_total(html)

    def _parse_list(self, html: str) -> list[Listing]:
        signal = classify_block(200, html or "")
        if signal is not None:
            return []
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        estate = self._estate()
        for listing in self._parse_cards(html or "", offer, estate):
            if listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        if items:
            return items
        for listing in self._parse_vypis(html or "", offer, estate):
            if listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        if items:
            return items
        hrefs = HREF_RE.findall(html or "") + HREF2_RE.findall(html or "")
        for href in hrefs:
            url = abs_url(href.split("?")[0], SITE)
            if url in seen or "/moje-reality/" in url:
                continue
            id_m = ID_RE.search(url)
            if not id_m:
                continue
            seen.add(url)
            idx = html.find(href)
            window = html[max(0, idx - 350) : idx + 800] if idx >= 0 else href
            title_m = TITLE_RE.search(window)
            title = clean(title_m.group(1) if title_m else url.rstrip("/").split("/")[-1].replace("-", " "))
            price_czk, price_label = parse_price(window)
            img = self._card_image(window)
            locality = self._locality_from_title(title)
            items.append(
                listing_from_card(
                    listing_id=numeric_id(id_m.group(1), url),
                    name=title,
                    url=url,
                    price_czk=price_czk,
                    price_label=price_label,
                    locality=locality,
                    image_url=img,
                    photos=[img] if img else [],
                    offer="prodej" if "/prodej/" in url else offer,
                    estate=estate,
                )
            )
        return items

    def _usable_code(self, code: str) -> bool:
        raw = (code or "").upper()
        if not raw or raw.casefold() in SKIP_CODES:
            return False
        if "REALITY" in raw or raw.startswith("CENA"):
            return False
        return "-" in raw

    def _codes(self, html: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for match in list(CODE_HREF_RE.findall(html)) + list(BOOKMARK_RE.findall(html)):
            code = str(match).upper()
            if code in seen or not self._usable_code(code):
                continue
            seen.add(code)
            found.append(code)
        return found

    def _parse_cards(self, html: str, offer: str, estate: str) -> list[Listing]:
        items: list[Listing] = []
        for block in CARD_RE.findall(html or ""):
            listing = self._parse_card(block, offer, estate)
            if listing:
                items.append(listing)
        return items

    def _card_code(self, html: str) -> str:
        for match in CODE_IN_CARD_RE.findall(html or ""):
            code = str(match).upper()
            if self._usable_code(code):
                return code
        return ""

    def _card_image(self, html: str) -> str:
        for match in list(THUMB_RE.findall(html or "")) + list(IMG_RE.findall(html or "")):
            url = abs_url(match, SITE)
            folded = url.casefold()
            if any(skip in folded for skip in SKIP_IMG):
                continue
            return url
        return ""

    def _locality_from_title(self, title: str) -> str:
        title = clean(title)
        if not title or SPEC_TITLE_RE.search(title):
            return ""
        if "," in title:
            tail = title.split(",", 1)[-1].strip()
            if tail and not SPEC_TITLE_RE.search(tail):
                return tail
        return title

    def _card_gps(self, html: str) -> tuple[float | None, float | None]:
        match = GPS_RE.search(html or "")
        if not match:
            return None, None
        try:
            lat, lon = float(match.group(1)), float(match.group(2))
        except ValueError:
            return None, None
        if abs(lat) < 0.01 and abs(lon) < 0.01:
            return None, None
        return lat, lon

    def _parse_card(self, html: str, offer: str, estate: str) -> Listing | None:
        code = self._card_code(html)
        if not code:
            return None
        title_m = VYPIS_TITLE_RE.search(html)
        title = clean(title_m.group(1) if title_m else "")
        spec_m = LOKALITA_RE.search(html)
        spec = clean(spec_m.group(1) if spec_m else "")
        locality = self._locality_from_title(title)
        name = title or spec or code.replace("-", " ")
        price_m = PRICE_BOX_RE.search(html)
        price_czk, price_label = parse_price(price_m.group(1) if price_m else html)
        if "/měs" in (price_label or "").casefold() or "měs" in (price_label or "").casefold():
            card_offer = "pronajem"
        elif price_czk and price_czk >= 400_000:
            card_offer = "prodej"
        else:
            card_offer = offer
        img = self._card_image(html)
        lat, lon = self._card_gps(html)
        url = f"{SITE}/{code}/"
        blob = f"{name} {spec}"
        return listing_from_card(
            listing_id=numeric_id(code, url),
            name=name,
            url=url,
            price_czk=price_czk,
            price_label=price_label,
            locality=locality,
            disposition=parse_disposition(blob),
            area_m2=parse_area(blob),
            image_url=img,
            photos=[img] if img else [],
            offer=card_offer,
            estate=estate,
            lat=lat,
            lon=lon,
            advert_code=code,
        )

    def _parse_vypis(self, html: str, offer: str, estate: str = "byt") -> list[Listing]:
        items: list[Listing] = []
        for code in self._codes(html):
            needle = code
            idx = html.upper().find(needle)
            if idx < 0:
                idx = html.find(f"bookmark-{code}")
            window = html[max(0, idx - 400) : idx + 1400] if idx >= 0 else ""
            listing = self._parse_card(window, offer, estate) if window else None
            if listing:
                items.append(listing)
                continue
            title_m = VYPIS_TITLE_RE.search(window) or re.search(
                rf'href="[^"]*{re.escape(code)}[^"]*"[^>]*>(.*?)</a>',
                window,
                re.S | re.I,
            )
            title = clean(title_m.group(1) if title_m else code.replace("-", " "))
            spec_m = LOKALITA_RE.search(window)
            spec = clean(spec_m.group(1) if spec_m else "")
            locality = self._locality_from_title(title)
            if spec and not title:
                title = spec
            price_czk, price_label = parse_price(window)
            if "/měs" in (price_label or "").casefold() or "měs" in (price_label or "").casefold():
                card_offer = "pronajem"
            elif price_czk and price_czk >= 400_000:
                card_offer = "prodej"
            else:
                card_offer = offer
            img = self._card_image(window)
            lat, lon = self._card_gps(window)
            url = f"{SITE}/{code}/"
            blob = f"{title} {spec}"
            items.append(
                listing_from_card(
                    listing_id=numeric_id(code, url),
                    name=title or f"{'Pronájem' if card_offer == 'pronajem' else 'Prodej'} {locality}".strip(),
                    url=url,
                    price_czk=price_czk,
                    price_label=price_label,
                    locality=locality,
                    disposition=parse_disposition(blob),
                    area_m2=parse_area(blob),
                    image_url=img,
                    photos=[img] if img else [],
                    offer=card_offer,
                    estate=estate,
                    lat=lat,
                    lon=lon,
                    advert_code=code,
                )
            )
        return items
