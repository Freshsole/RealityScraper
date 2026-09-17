"""RE/MAX Czech list/detail scraper (structured data-* cards)."""

from __future__ import annotations

import html as html_lib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.html_listing import (
    HtmlPortalClient,
    abs_url,
    clean,
    first_img,
    listing_from_card,
    numeric_id,
    parse_area,
    parse_disposition,
    parse_price,
)
from app.portal_urls import remax_url
from app.sreality import Listing

SITE = remax_url.site
CARD_OPEN_RE = re.compile(r'<div class="pl-items__item"', re.I)
ATTR_RE = re.compile(r'data-([a-z\-]+)="(.*?)"\s*(?=data-|>)', re.S | re.I)
DETAIL_RE = re.compile(r"/reality/detail/(\d+)/([^\"'\s>]+)")
TOTAL_RE = re.compile(r"z celkem\s*(?:<span[^>]*>\s*)?([\d\s\u00a0.]+)", re.I)
INFO_RE = re.compile(r'class="pl-items__item-info"[^>]*>(.*?)(?:<div class="pl-items__item-virtual"|$)', re.S | re.I)
LOC_P_RE = re.compile(r"<p>(.*?)</p>", re.S | re.I)
TITLE_RE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
DMS_PART = r"(-?\d+)\s*°\s*(\d+)\s*[\'’]\s*(\d+(?:\.\d+)?)\s*[\"”]?\s*([NSEWnsew])"
GPS_DMS_RE = re.compile(DMS_PART + r"\s*,\s*" + DMS_PART)
GPS_DEC_RE = re.compile(r"(-?\d+(?:\.\d+))\s*[,;]\s*(-?\d+(?:\.\d+))")
HOUSE_SLUG_RE = re.compile(r"domu|dum|dům|vila|chalup", re.I)


def _unescape(value: str) -> str:
    return html_lib.unescape((value or "").replace("&#039;", "'")).replace("\xa0", " ")


def _dms(deg: str, minutes: str, seconds: str, hemi: str) -> float:
    value = int(deg) + int(minutes) / 60.0 + float(seconds) / 3600.0
    if hemi.upper() in {"S", "W"}:
        value = -value
    return value


def parse_remax_gps(text: str) -> tuple[float, float] | None:
    raw = _unescape(text or "").replace("\xa0", " ").strip()
    if not raw:
        return None
    match = GPS_DMS_RE.search(raw)
    if match:
        lat = _dms(*match.group(1, 2, 3, 4))
        lon = _dms(*match.group(5, 6, 7, 8))
        if abs(lat) <= 90 and abs(lon) <= 180:
            return lat, lon
    match = GPS_DEC_RE.search(raw)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if abs(lat) <= 90 and abs(lon) <= 180:
            return lat, lon
    return None


def _card_chunks(html: str) -> list[str]:
    starts = [match.start() for match in CARD_OPEN_RE.finditer(html or "")]
    return [html[start : starts[index + 1] if index + 1 < len(starts) else len(html)] for index, start in enumerate(starts)]


def _locality_from_card(html: str, data: dict[str, str]) -> str:
    info = INFO_RE.search(html)
    blob = info.group(1) if info else html
    para = LOC_P_RE.search(blob)
    if para:
        loc = clean(para.group(1))
        loc = re.sub(r"(?i)^ulice\s+", "", loc)
        if loc:
            return loc
    return clean(data.get("display_address") or "")


class RemaxClient(HtmlPortalClient):
    SITE = SITE
    PAGE_PARAM = "stranka"
    PAGE_SIZE = 21

    def _context(self) -> str:
        raw = (self.search_url or "").lower()
        if "sale=1" in raw or "/prodej" in raw:
            return "prodej"
        return "pronajem"

    def _estate(self) -> str:
        path = (self.search_url or "").lower()
        if "domy" in path or "vily" in path:
            return "dum"
        return "byt"

    def _page_url(self, page: int, newest: bool = True) -> str:
        split = urlsplit(self.search_url or SITE)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        if newest:
            query.pop("order_by_price", None)
            query["order_by_published_date"] = "0"
        if page <= 1:
            query.pop(self.PAGE_PARAM, None)
        else:
            query[self.PAGE_PARAM] = str(page)
        return urlunsplit((split.scheme or "https", split.netloc or "www.remax-czech.cz", split.path, urlencode(query), ""))

    def _parse_total(self, html: str) -> int:
        match = TOTAL_RE.search((html or "").replace("\xa0", " "))
        if match:
            digits = re.sub(r"\D", "", match.group(1))
            return int(digits) if digits else 0
        return super()._parse_total(html)

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer = self._context()
        for chunk in _card_chunks(html or ""):
            listing = self._parse_card(chunk, offer)
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
                    estate=self._estate(),
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
        title_m = TITLE_RE.search(html)
        title = clean(data.get("title") or (title_m.group(1) if title_m else "") or slug.replace("-", " "))
        locality = _locality_from_card(html, data)
        price_czk, price_label = parse_price(data.get("price") or "")
        img = abs_url(data.get("img") or first_img(html) or "", SITE)
        gps = parse_remax_gps(data.get("gps") or "")
        inferred_offer = "pronajem" if "pronajem" in slug or "pronájem" in title.casefold() else offer
        if "prodej" in slug:
            inferred_offer = "prodej"
        estate = "dum" if HOUSE_SLUG_RE.search(slug) else self._estate()
        lat = gps[0] if gps else None
        lon = gps[1] if gps else None
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
            estate=estate,
            lat=lat,
            lon=lon,
        )
