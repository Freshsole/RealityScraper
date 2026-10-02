"""Realingo.cz list/detail scraper (JSON-LD based)."""

from __future__ import annotations

import json
import re

from app.html_listing import HtmlPortalClient, abs_url, clean, listing_from_card
from app.sreality import Listing

SITE = "https://www.realingo.cz"

JSONLD_RE = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.S | re.I
)
URL_ID_RE = re.compile(r'/(\d{4,})/?$')
PRICE_RE = re.compile(r'([\d\xa0\s]+)\s*Kč', re.I)


def _parse_price(text: str) -> tuple[int | None, str]:
    m = PRICE_RE.search(text or "")
    if not m:
        return None, ""
    label = m.group(0).strip()
    num = m.group(1).replace("\xa0", "").replace(" ", "").strip()
    return (int(num) if num.isdigit() else None), label


class RealingoClient(HtmlPortalClient):
    SITE = SITE
    PAGE_SIZE = 40

    def _context(self) -> str:
        path = (self.search_url or "").lower()
        return "prodej" if "prodej" in path else "pronajem"

    def _page_url(self, page: int, newest: bool = True) -> str:
        base = (self.search_url or "").rstrip("/")
        if page <= 1:
            return base + "/"
        return f"{base}/{page}_strana/"

    def _parse_list(self, html: str) -> list[Listing]:
        out: list[Listing] = []
        seen: set[str] = set()
        ctx = self._context()
        for jm in JSONLD_RE.finditer(html or ""):
            try:
                data = json.loads(jm.group(1))
            except (json.JSONDecodeError, AttributeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("@type") not in ("SingleFamilyResidence", "Apartment", "House"):
                continue
            url_path = data.get("url", "")
            id_m = URL_ID_RE.search(url_path or "")
            if not id_m:
                continue
            lid = id_m.group(1)
            if lid in seen:
                continue
            seen.add(lid)
            url = abs_url(url_path, SITE)
            title = clean(data.get("name", ""))
            img = data.get("image", "")
            if img and img.startswith("/"):
                img = SITE + img
            locality = clean(data.get("address", ""))
            area = None
            fs = data.get("floorSize", {})
            if isinstance(fs, dict):
                try:
                    area = int(float(str(fs.get("value", "")).replace(",", ".")))
                except ValueError:
                    pass
            lat, lon = None, None
            geo = data.get("geo", {})
            if isinstance(geo, dict):
                lat = geo.get("latitude")
                lon = geo.get("longitude")
            # Cena není v JSON-LD, zkusíme najít v okolí
            idx = (html or "").find(url_path)
            snippet = (html or "")[max(0, idx - 2000):idx + 2000]
            price, price_label = _parse_price(snippet)
            out.append(listing_from_card(
                listing_id=abs(hash(f"realingo_{lid}")) % (10 ** 12),
                name=title,
                url=url,
                price_czk=price,
                price_label=price_label,
                locality=locality,
                area_m2=area,
                image_url=img,
                offer=ctx,
                lat=lat,
                lon=lon,
            ))
        return out
