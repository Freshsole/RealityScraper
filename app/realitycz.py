"""Reality.cz list/detail scraper. Search HTML uses vypis cards; maintenance is detected up-stack."""

from __future__ import annotations

import re

from app.block_page import classify_block
from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card, numeric_id, parse_price
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
ID_RE = re.compile(r"/(\d{4,})")
TITLE_RE = re.compile(r"<h[123][^>]*>(.*?)</h[123]>", re.S | re.I)
IMG_RE = re.compile(r'(?:src|data-src)="([^"]+\.(?:jpg|jpeg|webp)[^"]*)"', re.I)
VYPIS_TITLE_RE = re.compile(r'class="vypisnaz"[^>]*>\s*<a[^>]*>(.*?)</a>', re.S | re.I)
LOKALITA_RE = re.compile(r'class="lokalita[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
SKIP_CODES = {"cena-0", "cena-1", "cena-2", "cena-3", "cena-5", "cena-7"}


class RealityczClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "/prodej/" in path else "pronajem"

    def _parse_list(self, html: str) -> list[Listing]:
        signal = classify_block(200, html or "")
        if signal is not None:
            return []
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for listing in self._parse_vypis(html or "", offer):
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
            img_m = IMG_RE.search(window)
            img = abs_url(img_m.group(1), SITE) if img_m else ""
            locality = ""
            loc_m = re.search(r"(Praha[^<,]*|Brno[^<,]*|Ostrava[^<,]*)", window)
            if loc_m:
                locality = clean(loc_m.group(1))
            elif "," in title:
                locality = title.split(",")[-1].strip()
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
                )
            )
        return items

    def _codes(self, html: str) -> list[str]:
        found: list[str] = []
        seen: set[str] = set()
        for match in list(CODE_HREF_RE.findall(html)) + list(BOOKMARK_RE.findall(html)):
            code = str(match).upper()
            if code in seen or code.casefold() in SKIP_CODES:
                continue
            if "-" not in code or not re.search(r"\d", code):
                continue
            if "REALITY" in code or code.startswith("CENA"):
                continue
            seen.add(code)
            found.append(code)
        return found

    def _parse_vypis(self, html: str, offer: str) -> list[Listing]:
        items: list[Listing] = []
        for code in self._codes(html):
            needle = code
            idx = html.upper().find(needle)
            if idx < 0:
                idx = html.find(f"bookmark-{code}") 
            window = html[max(0, idx - 200) : idx + 900] if idx >= 0 else ""
            title_m = VYPIS_TITLE_RE.search(window) or re.search(
                rf'href="[^"]*{re.escape(code)}[^"]*"[^>]*>(.*?)</a>',
                window,
                re.S | re.I,
            )
            title = clean(title_m.group(1) if title_m else code.replace("-", " "))
            locality = ""
            if "," in title:
                locality = title.split(",", 1)[-1].strip()
            if not locality:
                loc_m = re.search(r"(Praha[^<,]*|Brno[^<,]*|Ostrava[^<,]*)", title + " " + window)
                if loc_m:
                    locality = clean(loc_m.group(1))
            spec_m = LOKALITA_RE.search(window)
            spec = clean(spec_m.group(1)) if spec_m else ""
            if spec and not title:
                title = spec
            price_czk, price_label = parse_price(window)
            if "/měs" in (price_label or "").casefold() or "měs" in (price_label or "").casefold():
                card_offer = "pronajem"
            elif price_czk and price_czk >= 400_000:
                card_offer = "prodej"
            else:
                card_offer = offer
            img_m = IMG_RE.search(window)
            img = abs_url(img_m.group(1), SITE) if img_m else ""
            url = f"{SITE}/{code}/"
            items.append(
                listing_from_card(
                    listing_id=numeric_id(code, url),
                    name=title or f"{'Pronájem' if card_offer == 'pronajem' else 'Prodej'} {locality}".strip(),
                    url=url,
                    price_czk=price_czk,
                    price_label=price_label,
                    locality=locality,
                    image_url=img,
                    photos=[img] if img else [],
                    offer=card_offer,
                    advert_code=code,
                )
            )
        return items
