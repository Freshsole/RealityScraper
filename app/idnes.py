from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import json
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.html_listing import PRICE_RE, clean as _clean, extract_listing_html
from app.sreality import Listing, ListingGone, format_price

SITE = "https://reality.idnes.cz"
PAGE_SIZE = 25
OID_RE = re.compile(r"/detail/(pronajem|prodej|drazba)/([^/]+)/([^/]+)/([0-9a-f]{24})/", re.I)
COUNT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[\s\u00a0.]\d{3}){0,4}|\d{1,7})\s+inzerát",
    re.I,
)
AREA_RE = re.compile(r"(\d{1,6})\s*m", re.I)
DISP_RE = re.compile(r"(\d{1,2}\s*\+\s*(?:kk|1)|pokoj|atyp)", re.I)
FANCY_PHOTO_RE = re.compile(
    r'data-fancybox="images"[^>]{0,300}href="([^"]{1,500})"|href="([^"]{1,500})"[^>]{0,300}data-fancybox="images"',
    re.I,
)
THUMB_PHOTO_RE = re.compile(
    r"https://sta-reality2\.1gr\.cz/sta/compile/thumbs/[0-9a-f/]{1,200}\.(?:jpg|jpeg|webp)(?:\?[^\"'\s]{0,200})?",
    re.I,
)
LAT_RE = re.compile(r'"listing_lat"\s*:\s*([0-9.]{1,20})')
LON_RE = re.compile(r'"listing_lon"\s*:\s*([0-9.]{1,20})')
PRICE_DL_RE = re.compile(r'"listing_price"\s*:\s*([0-9]{1,12})')
AREA_DL_RE = re.compile(r'"listing_area"\s*:\s*([0-9]{1,8})')
NAME_DL_RE = re.compile(r'"listing_name"\s*:\s*"([^"]{1,400})"')
BRAND_DL_RE = re.compile(r'"listing_brand"\s*:\s*"([^"]{1,200})"')
CAT_DL_RE = re.compile(r'"listing_category"\s*:\s*"([^"]{1,80})"')
VAR_DL_RE = re.compile(r'"listing_variant"\s*:\s*"([^"]{1,80})"')
DT_RE = re.compile(r"<dt[^>]{0,120}>(.{0,400}?)</dt>\s*<dd[^>]{0,120}>(.{0,800}?)</dd>", re.S | re.I)
DESC_RE = re.compile(r'<div[^>]{0,200}class="[^"]{0,200}b-desc[^"]{0,200}"[^>]{0,80}>(.{0,20000}?)</div>', re.S | re.I)
DESC2_RE = re.compile(r'<p[^>]{0,200}class="[^"]{0,200}description[^"]{0,200}"[^>]{0,80}>(.{0,20000}?)</p>', re.S | re.I)
ARTICLE_RE = re.compile(r"<article\b.{0,80000}?</article>", re.S | re.I)
RESULTS_RE = re.compile(
    r'id="snippet-s-result-articles"[^>]{0,200}>([\s\S]{0,400000}?)<div[^>]*id="snippet-s-result-paginator',
    re.I,
)
CARD_TITLE_RE = re.compile(r'class="c-products__title"[^>]{0,120}>(.{0,500}?)</h2>', re.S)
CARD_ALT_RE = re.compile(r'alt="([^"]{1,400})"')
CARD_INFO_RE = re.compile(r'class="c-products__info"[^>]{0,120}>(.{0,400}?)</p>', re.S)
CARD_PRICE_RE = re.compile(r'class="c-products__price"[^>]{0,120}>(.{0,400}?)</p>', re.S)
CARD_SRC_RE = re.compile(r'data-src="([^"]{1,500})"')
CARD_BG_RE = re.compile(r"background-image:\s*url\('([^']{1,500})'\)")
CARD_BRAND_RE = re.compile(r'data-brand="([^"]{1,200})"')
OG_DESC_RE = re.compile(r'property="og:description" content="([^"]{1,4000})"')
JS_SAFE_ID = (1 << 53) - 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    "Origin": SITE,
    "Referer": f"{SITE}/",
}

OFFER_LABEL = {"pronajem": "Pronájem", "prodej": "Prodej", "drazba": "Dražba"}
ESTATE_LABEL = {
    "byt": "Byt",
    "dum": "Dům",
    "pozemek": "Pozemek",
    "projekt": "Projekt",
    "komercni": "Komerční",
    "komercni-nemovitost": "Komerční",
    "ostatni": "Ostatní",
    "maly-objekt-nebo-garaz": "Garáž",
}
FLAG_LABELS = {
    "balkon": "balcony",
    "lodzie": "loggia",
    "terasa": "terrace",
    "zahrada": "garden",
    "sklep": "cellar",
    "garaz": "garage",
    "parkovani": "parking",
    "vytah": "lift",
    "bezbarierovy": "barrier_free",
    "telefon": "phone",
    "kabelova": "cable_tv",
    "internet": "internet",
}


def oid_to_int(oid: str) -> int:
    digest = hashlib.blake2s((oid or "").encode(), digest_size=8).digest()
    value = int.from_bytes(digest, "big") & JS_SAFE_ID
    return value or 1


def _abs_img(url: str) -> str:
    raw = url.replace("\\/", "/").strip()
    if raw.startswith("//"):
        return "https:" + raw
    return raw


def upgrade_photo(url: str) -> str:
    raw = _abs_img(url).split("#")[0].strip()
    if "/sta/compile/thumbs/" not in raw:
        return raw
    if re.search(r"\.(?:jpg|jpeg)(?:$|\?)", raw, re.I) and "gt=r" not in raw.casefold():
        return raw + ("&" if "?" in raw else "?") + "gt=r"
    return raw


def parse_photos(html: str) -> list[str]:
    photos: list[str] = []

    def add(url: str) -> None:
        raw = upgrade_photo(url)
        if "/sta/compile/thumbs/" not in raw or "/ui/" in raw:
            return
        key = raw.split("?")[0]
        if any(item.split("?")[0] == key for item in photos):
            return
        photos.append(raw)

    for left, right in FANCY_PHOTO_RE.findall((html or "")[:180_000]):
        add(left or right)
    if len(photos) < 2:
        for raw in THUMB_PHOTO_RE.findall((html or "")[:180_000]):
            add(raw)
    return photos[:40]


class IdnesClient:
    def __init__(self, search_url: str) -> None:
        from app.scrape_http import scrape_timeout

        self.search_url = search_url
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, max_redirects=3, timeout=scrape_timeout()
        )

    async def _attach_coords(self, listings: list[Listing]) -> None:
        missing = [item for item in listings if item.lat is None or item.lon is None]
        if not missing:
            return
        from app.places import geocode_locality

        keys = list(dict.fromkeys((item.locality or "").strip() for item in missing if (item.locality or "").strip()))
        found: dict[str, tuple[float, float] | None] = {}
        lock = asyncio.Semaphore(4)

        async def locate(key: str) -> None:
            async with lock:
                found[key] = await geocode_locality(key)

        await asyncio.gather(*(locate(key) for key in keys))
        for item in missing:
            point = found.get((item.locality or "").strip())
            if point:
                item.lat, item.lon = point

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        from app.scrape_http import request_with_log

        url = self._page_url(page, newest)
        if page <= 1:
            response = await request_with_log(
                self._client, "GET", url, portal="idnes", headers={"Accept": "text/html"}
            )
        else:
            response = await request_with_log(
                self._client,
                "GET",
                url,
                portal="idnes",
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            )
        if response.status_code == 404:
            from app.scrape_timing import note_httpx

            note_httpx(response)
            return [], 0
        response.raise_for_status()
        from app.scrape_timing import note, note_httpx

        note_httpx(response)
        if page <= 1:
            self._remember_search_url(str(response.url))
        raw = response.content

        def _decode_and_parse() -> tuple[list[Listing], int]:
            text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
            body = text
            if page > 1:
                try:
                    payload = json.loads(text)
                    body = (payload.get("snippets") or {}).get("snippet-s-result-articles") or text
                except Exception:
                    body = text
            listings = self._parse_list(self._results_html(body))
            total = self._parse_total(text) or self._parse_total(body) or (len(listings) if page == 1 else 0)
            return listings, total

        parse_started = time.monotonic()
        listings, total = await asyncio.to_thread(_decode_and_parse)
        note(parse_ms=(time.monotonic() - parse_started) * 1000.0)
        await self._attach_coords(listings)
        return listings, total

    async def fetch_pages(self, pages: int, newest: bool = True) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        for page in range(1, pages + 1):
            batch, page_total = await self.fetch_page(page, newest=newest)
            total = page_total or total
            if not batch:
                break
            for item in batch:
                if item.id in seen:
                    continue
                seen.add(item.id)
                listings.append(item)
        return listings, total

    async def fetch_all(self, newest: bool = True, max_pages: int | None = None) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        page = 1
        while True:
            if max_pages is not None and page > max_pages:
                break
            batch, page_total = await self.fetch_page(page, newest=newest)
            total = page_total or total
            if not batch:
                break
            fresh = [item for item in batch if item.id not in seen]
            if page > 1 and not fresh:
                break
            for item in fresh:
                seen.add(item.id)
                listings.append(item)
            needed = (int(total) + PAGE_SIZE - 1) // PAGE_SIZE if total else page
            if page >= needed:
                break
            page += 1
        return listings, total

    async def fetch_detail(self, listing: Listing) -> Listing:
        response = await self._client.get(listing.url, headers={"Accept": "text/html"})
        if response.status_code == 404:
            raise ListingGone(listing.url)
        response.raise_for_status()
        raw = response.content

        def _parse(blob: bytes) -> Listing:
            html = blob.decode("utf-8", "replace")
            folded = html.casefold()
            missing = "listing_id" not in html and "listing_lat" not in html
            if missing and (
                "inzerát byl stažen" in folded
                or "inzerát neexistuje" in folded
                or "stránka nenalezena" in folded
            ):
                raise ListingGone(listing.url)
            return self._parse_detail(listing, extract_listing_html(html))

        listing = await asyncio.to_thread(_parse, raw)
        from app.places import refine_listing_location_async

        return await refine_listing_location_async(listing)

    def _remember_search_url(self, final_url: str) -> None:
        split = urlsplit(final_url)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        query.pop("page", None)
        self.search_url = urlunsplit(
            (split.scheme or "https", split.netloc, split.path, urlencode(query, safe="[]|"), "")
        )

    def _results_html(self, html: str) -> str:
        match = RESULTS_RE.search(html)
        return match.group(1) if match else html

    def _page_url(self, page: int, newest: bool) -> str:
        split = urlsplit(self.search_url)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        if newest:
            query.pop("sort", None)
        if page <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page - 1)
        return urlunsplit((split.scheme or "https", split.netloc or "reality.idnes.cz", split.path, urlencode(query, safe="[]|"), ""))

    def _parse_total(self, html: str) -> int:
        raw = (html or "").replace("\xa0", " ")
        if len(raw) > 32_000:
            raw = raw[:32_000]
        match = COUNT_RE.search(raw)
        if not match:
            return 0
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else 0

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        blocks = ARTICLE_RE.findall(html) or [html]
        for block in blocks:
            listing = self._parse_card(block)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        return items

    def _parse_card(self, html: str) -> Listing | None:
        match = OID_RE.search(html)
        if not match:
            return None
        offer, estate, _slug, oid = match.groups()
        url = f"{SITE}/detail/{offer}/{estate}/{_slug}/{oid}/"
        title_m = CARD_TITLE_RE.search(html)
        if title_m:
            title = _clean(title_m.group(1))
        else:
            alt = CARD_ALT_RE.search(html)
            title = html_lib.unescape(alt.group(1)) if alt else f"{OFFER_LABEL.get(offer.casefold(), offer)} {estate}"
        loc_m = CARD_INFO_RE.search(html)
        locality = _clean(loc_m.group(1)) if loc_m else ""
        price_m = CARD_PRICE_RE.search(html)
        price_text = _clean(price_m.group(1)) if price_m else ""
        price_czk = None
        pm = PRICE_RE.search(price_text.replace("\xa0", " "))
        if pm:
            digits = re.sub(r"\D", "", pm.group(1))
            price_czk = int(digits) if digits else None
        disp = ""
        dm = DISP_RE.search(title.casefold().replace(" ", ""))
        if dm:
            disp = dm.group(1).replace(" ", "")
            if disp.endswith("kk") and "+" not in disp:
                disp = disp.replace("kk", "+kk")
        area = None
        am = AREA_RE.search(title)
        if am:
            area = int(am.group(1))
        img = None
        im = CARD_SRC_RE.search(html) or CARD_BG_RE.search(html)
        if im:
            img = upgrade_photo(im.group(1).replace("\\/", "/").replace("\\", ""))
        photos = parse_photos(html)
        if photos:
            img = photos[0]
        brand_m = CARD_BRAND_RE.search(html)
        extras = {
            "offer": OFFER_LABEL.get(offer.casefold(), offer),
            "estate": ESTATE_LABEL.get(estate.casefold(), estate.title()),
            "flags": [],
            "specs": [],
            "agency": _clean(brand_m.group(1)) if brand_m else "",
        }
        return Listing(
            id=oid_to_int(oid),
            name=title,
            price_czk=price_czk,
            price_label=price_text or format_price(price_czk, "měsíc" if offer.casefold() == "pronajem" else "ks"),
            disposition=disp,
            area_m2=area,
            locality=locality,
            url=url,
            image_url=img,
            extras=extras,
            advert_code=oid,
            photos=photos,
        )

    def _parse_detail(self, listing: Listing, html: str) -> Listing:
        lat_m = LAT_RE.search(html)
        lon_m = LON_RE.search(html)
        if lat_m:
            listing.lat = float(lat_m.group(1))
        if lon_m:
            listing.lon = float(lon_m.group(1))
        price_m = PRICE_DL_RE.search(html)
        if price_m:
            listing.price_czk = int(price_m.group(1))
        area_m = AREA_DL_RE.search(html)
        if area_m:
            listing.area_m2 = int(area_m.group(1))
        name_m = NAME_DL_RE.search(html)
        if name_m:
            listing.name = html_lib.unescape(name_m.group(1)).replace(" m2", " m²")
        cat_m = CAT_DL_RE.search(html)
        var_m = VAR_DL_RE.search(html)
        brand_m = BRAND_DL_RE.search(html)
        extras = dict(listing.extras or {})
        specs: list[dict[str, str]] = []
        flags: list[str] = list(extras.get("flags") or [])
        for raw_label, raw_value in DT_RE.findall(html):
            label = _clean(raw_label)
            value = _clean(raw_value)
            if not label or not value or "reklam" in label.casefold() or "spočít" in label.casefold():
                continue
            specs.append({"label": label, "value": value})
            folded = label.casefold()
            if "vlastnict" in folded:
                extras["ownership"] = value
            if "konstrukc" in folded:
                extras["building"] = value
            if "stav" in folded:
                extras["condition"] = value
            if folded == "vybavení":
                extras["equipped"] = value
            low = value.casefold()
            for token, flag in FLAG_LABELS.items():
                if token in folded or token in low:
                    if flag not in flags:
                        flags.append(flag)
        extras["specs"] = specs
        extras["flags"] = flags
        if cat_m:
            parts = html_lib.unescape(cat_m.group(1)).split("/")
            if parts:
                extras["estate"] = parts[0]
            if len(parts) > 1 and not listing.disposition:
                listing.disposition = parts[1]
        if var_m:
            extras["offer"] = html_lib.unescape(var_m.group(1))
        if brand_m:
            extras["agency"] = html_lib.unescape(brand_m.group(1))
            extras["seller"] = extras["agency"]
        listing.extras = extras
        photos = parse_photos(html)
        if photos:
            listing.photos = photos
            listing.image_url = photos[0]
        desc = ""
        dm = DESC_RE.search(html) or DESC2_RE.search(html)
        if dm:
            desc = _clean(dm.group(1))
        if not desc:
            og = OG_DESC_RE.search((html or "")[:80_000])
            if og:
                desc = html_lib.unescape(og.group(1))
        if desc:
            listing.description = desc
        if listing.price_czk and not listing.price_label:
            unit = "měsíc" if "/pronajem/" in listing.url else "ks"
            listing.price_label = format_price(listing.price_czk, unit)
        from app.places import refine_listing_location

        refine_listing_location(listing)
        return listing

    async def aclose(self) -> None:
        await self._client.aclose()
