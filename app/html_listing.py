"""Shared HTML listing helpers used by the extra Czech portals."""

from __future__ import annotations

import asyncio
import hashlib
import html as html_lib
import re
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.sreality import Listing, ListingGone, format_price

JS_SAFE_ID = (1 << 53) - 1
# Require m² / m2 / bare "m" not followed by a letter (avoids "721990100 Mail" → 990100).
AREA_RE = re.compile(r"(?<!\d)(\d{1,5}(?:[.,]\d{1,2})?)\s*(?:m²|m2|m(?![a-záčďéěíňóřšťúůýž]))", re.I)
HA_RE = re.compile(r"(?<!\d)(\d{1,4}(?:[.,]\d{1,2})?)\s*ha\b", re.I)
FLAT_AREA_MAX_M2 = 10_000
LAND_AREA_MAX_M2 = 5_000_000
PRICE_RE = re.compile(r"(\d{1,3}(?:[\s\u00a0.]\d{3}){1,4}|\d{4,8})\s*Kč", re.I)
DISP_RE = re.compile(r"(\d{1,2})\s*\+\s*(kk|1)|(\d{1,2})\s*kk|garson|atyp|pokoj", re.I)
COUNT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[\s\u00a0.]\d{3}){0,4}|\d{1,7})\s+"
    r"(?:inzerát|nemovitost|nabídek|výsled)",
    re.I,
)
_PARSE_TOTAL_CHARS = 32_000
GONE_HINTS = (
    "inzerát byl stažen",
    "inzerát neexistuje",
    "stránka nenalezena",
    "nenalezeno",
    "nabídka byla stažena",
    "nabídka již není",
    "404",
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}

OFFER_LABEL = {"pronajem": "Pronájem", "prodej": "Prodej", "drazba": "Dražba"}
ESTATE_LABEL = {"byt": "Byt", "byty": "Byt", "dum": "Dům", "domy": "Dům", "pozemek": "Pozemek"}
FLAG_WORDS = {
    "balkon": "balcony",
    "balkón": "balcony",
    "lodži": "loggia",
    "lodzi": "loggia",
    "teras": "terrace",
    "sklep": "cellar",
    "garáž": "garage",
    "garaz": "garage",
    "parkov": "parking",
    "výtah": "lift",
    "vytah": "lift",
    "zahrad": "garden",
    "bezbariér": "barrier_free",
}


def hash_id(value: str) -> int:
    digest = hashlib.blake2s((value or "").encode(), digest_size=8).digest()
    number = int.from_bytes(digest, "big") & JS_SAFE_ID
    return number or 1


def numeric_id(value: str | int | None, fallback: str = "") -> int:
    raw = str(value or "").strip()
    digits = re.sub(r"\D", "", raw)
    if digits and len(digits) <= 15:
        try:
            number = int(digits)
            if 0 < number <= JS_SAFE_ID:
                return number
        except ValueError:
            pass
    return hash_id(raw or fallback)


_IMG_ATTR_RE = re.compile(
    r'(?:src|data-src|data-img)=["\']([^"\']{1,500}\.(?:jpg|jpeg|webp|png)[^"\']{0,200})["\']',
    re.I,
)
_CSS_URL_RE = re.compile(r"url\((['\"]?)([^\"')\s]{1,500})\1\)", re.I)
_PHOTO_ATTR_RE = re.compile(
    r'(?:src|href)=["\']([^"\']{1,500}\.(?:jpg|jpeg|webp)[^"\']{0,200})["\']',
    re.I,
)
_OG_DESC_RE = re.compile(r'property="og:description"\s+content="([^"]{1,4000})"', re.I)
_LAT_RE = re.compile(r'"lat(?:itude)?"\s*:\s*(-?\d{1,3}\.\d{1,10})')
_LON_RE = re.compile(r'"l(?:on|ng)(?:itude)?"\s*:\s*(-?\d{1,3}\.\d{1,10})')
_STRIP_TAGS_CHARS = 80_000
_PARSE_PRICE_CHARS = 8_000
_DETAIL_MARKERS = (
    'id="detail"',
    "id='detail'",
    'id="content"',
    "application/ld+json",
    "__NEXT_DATA__",
    'itemtype="http://schema.org/Offer"',
    'itemtype="https://schema.org/Offer"',
    'class="inzeraty"',
    'id="inzerat"',
    "s-result",
)


def extract_listing_html(html: str, limit: int = 180_000) -> str:
    raw = html or ""
    if len(raw) <= 40_000:
        return raw
    head = raw[:8_000]
    lowered = raw.casefold()
    for marker in _DETAIL_MARKERS:
        idx = lowered.find(marker.casefold())
        if idx < 0:
            continue
        start = max(0, idx - 2_000)
        return head + "\n" + raw[start : start + limit]
    return raw[:limit]


def strip_tags(text: str) -> str:
    """Linear tag strip. CPython `re.sub(r'<[^>]+>', …)` is O(n²) on a long run of `<`."""
    raw = text or ""
    if len(raw) > _STRIP_TAGS_CHARS:
        raw = raw[:_STRIP_TAGS_CHARS]
    parts: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        lt = raw.find("<", i)
        if lt < 0:
            parts.append(raw[i:])
            break
        if lt > i:
            parts.append(raw[i:lt])
        gt = raw.find(">", lt + 1)
        if gt < 0:
            parts.append(raw[lt:])
            break
        if gt == lt + 1:
            parts.append("<")
            i = lt + 1
            continue
        parts.append(" ")
        i = gt + 1
    return html_lib.unescape("".join(parts))


def clean(text: str) -> str:
    return " ".join(strip_tags(text).replace("\xa0", " ").replace("&zwj;", "").split())


def abs_url(url: str, site: str) -> str:
    raw = (url or "").replace("\\/", "/").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.split("#")[0]
    return urljoin(site.rstrip("/") + "/", raw).split("#")[0]


def parse_disposition(text: str) -> str:
    match = DISP_RE.search(text or "")
    if not match:
        return ""
    if match.group(1) and match.group(2):
        kind = "kk" if match.group(2).lower() == "kk" else "1"
        return f"{match.group(1)}+{kind}"
    if match.group(3):
        return f"{match.group(3)}+kk"
    folded = (text or "").casefold()
    if "garson" in folded:
        return "1+kk"
    if "atyp" in folded:
        return "atypický"
    if "pokoj" in folded:
        return "pokoj"
    return ""


def _estate_is_land(estate: str = "") -> bool:
    folded = (estate or "").casefold()
    return any(token in folded for token in ("pozem", "land", "zahrad", "pole", "orná", "orna"))


def sanitize_area_m2(value: int | None, *, estate: str = "") -> int | None:
    """Drop impossible areas (phone digits, HTML junk). Land may be larger."""
    if value is None:
        return None
    try:
        area = int(value)
    except (TypeError, ValueError):
        return None
    if area <= 0:
        return None
    cap = LAND_AREA_MAX_M2 if _estate_is_land(estate) else FLAT_AREA_MAX_M2
    if area > cap:
        return None
    return area


def parse_area(text: str, *, estate: str = "") -> int | None:
    blob = (text or "").replace(",", ".")
    match = AREA_RE.search(blob)
    if match:
        try:
            return sanitize_area_m2(int(round(float(match.group(1)))), estate=estate)
        except ValueError:
            pass
    ha = HA_RE.search(blob)
    if ha:
        try:
            return sanitize_area_m2(int(round(float(ha.group(1)) * 10_000)), estate=estate or "pozemek")
        except ValueError:
            return None
    return None


def parse_price(text: str) -> tuple[int | None, str]:
    raw = clean(text)
    if len(raw) > _PARSE_PRICE_CHARS:
        raw = raw[:_PARSE_PRICE_CHARS]
    folded = raw.casefold()
    if (
        "dohod" in folded
        or "info v rk" in folded
        or "cena v rk" in folded
        or "na dotaz" in folded
        or "cenudotaz" in folded.replace(" ", "")
    ):
        # Keep short "dohodou" labels; never return a whole HTML blob.
        short = raw if len(raw) <= 48 else "Cena dohodou"
        return None, short or "Cena dohodou"
    normalized = raw.replace("\xa0", " ")
    match = PRICE_RE.search(normalized)
    if not match:
        # Only keep a label when it already looks like a short price string.
        if len(raw) <= 48 and re.search(r"\d", raw):
            return None, raw
        return None, ""
    digits = re.sub(r"\D", "", match.group(1))
    amount = int(digits) if digits else None
    if amount is None or amount <= 0:
        return None, "Cena na dotaz" if amount == 0 else ""
    # Always return a compact display label — never the surrounding card HTML.
    label = f"{amount:,} Kč".replace(",", " ")
    return amount, label


def sanitize_price_label(price_czk: int | None, price_label: str | None, *, rent: bool = False) -> str:
    """Rebuild a safe UI price label from known amount / reject scraped junk."""
    label = clean(price_label or "")
    junk_markers = (
        "datalayer",
        "inzerát | inzerce",
        "inzerce na ",
        "<script",
        '{"imports"',
        '{ "imports"',
        "gtm-",
        "id nabídky",
    )
    folded = label.casefold()
    is_junk = len(label) > 64 or any(marker in folded for marker in junk_markers)
    amount = None
    try:
        if price_czk not in (None, ""):
            amount = int(price_czk)
    except (TypeError, ValueError):
        amount = None
    if amount is None and label:
        parsed_amount, _ = parse_price(label)
        amount = parsed_amount
    if amount is not None:
        unit = "měsíc" if rent or "měs" in folded else ""
        if unit:
            return f"{amount:,} Kč/{unit}".replace(",", " ")
        return f"{amount:,} Kč".replace(",", " ")
    if is_junk or not label:
        return "Cena neuvedena"
    return label


def parse_total(html: str) -> int:
    raw = (html or "").replace("\xa0", " ")
    if len(raw) > _PARSE_TOTAL_CHARS:
        raw = raw[:_PARSE_TOTAL_CHARS]
    match = COUNT_RE.search(raw)
    if not match:
        return 0
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else 0


def flags_from_text(text: str) -> list[str]:
    folded = (text or "").casefold()
    return list(dict.fromkeys(name for needle, name in FLAG_WORDS.items() if needle in folded))


def with_page(url: str, page: int, param: str = "strana") -> str:
    split = urlsplit(url)
    query = dict(parse_qsl(split.query, keep_blank_values=True))
    if page <= 1:
        query.pop(param, None)
    else:
        query[param] = str(page)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query, doseq=True), ""))


def first_img(html: str) -> str:
    raw = html or ""
    if len(raw) > _STRIP_TAGS_CHARS:
        raw = raw[:_STRIP_TAGS_CHARS]
    match = _IMG_ATTR_RE.search(raw)
    if match:
        return match.group(1)
    match = _CSS_URL_RE.search(raw)
    return match.group(2) if match else ""


def listing_from_card(
    *,
    listing_id: int,
    name: str,
    url: str,
    price_czk: int | None,
    price_label: str,
    locality: str,
    disposition: str = "",
    area_m2: int | None = None,
    image_url: str | None = None,
    photos: list[str] | None = None,
    offer: str = "pronajem",
    estate: str = "byt",
    extras: dict[str, Any] | None = None,
    created_on: str | None = None,
    description: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    advert_code: str | None = None,
) -> Listing:
    rent = offer in {"pronajem", "Pronájem"} or "pronáj" in (name or "").casefold() or "měsíc" in (price_label or "").casefold()
    blob = f"{name} {locality} {description or ''}"
    disp = disposition or parse_disposition(blob)
    area = area_m2 if area_m2 is not None else parse_area(blob)
    photos = [item for item in (photos or []) if item][:40]
    img = image_url or (photos[0] if photos else None)
    payload = {
        "offer": "Pronájem" if rent else OFFER_LABEL.get(str(offer).casefold(), "Prodej"),
        "estate": ESTATE_LABEL.get(str(estate).casefold(), "Byt"),
        "flags": flags_from_text(blob),
        "specs": [],
        "agency": "",
    }
    if extras:
        payload.update(extras)
    unit = "měsíc" if rent else "ks"
    return Listing(
        id=int(listing_id),
        name=name or f"{payload['offer']} {payload['estate']}",
        price_czk=price_czk,
        price_label=price_label or format_price(price_czk, unit),
        disposition=disp,
        area_m2=area,
        locality=locality,
        url=url,
        image_url=img,
        photos=photos,
        extras=payload,
        created_on=created_on,
        description=description,
        lat=lat,
        lon=lon,
        advert_code=advert_code or str(listing_id),
    )


class HtmlPortalClient:
    """Minimal HTTP list/detail client. Subclasses implement _parse_list/_page_url."""

    SITE = ""
    PAGE_PARAM = "strana"
    PAGE_SIZE = 20
    TIMEOUT = 8.0
    #: Set True when listing cards render late in a large page and the shared
    #: 180KB list truncation in fetch_page would cut them off.
    FULL_LIST_HTML = False

    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        from app.scrape_http import scrape_timeout

        headers = dict(BROWSER_HEADERS)
        if self.SITE:
            headers["Referer"] = self.SITE.rstrip("/") + "/"
            headers["Origin"] = self.SITE.rstrip("/")
        self._client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            max_redirects=3,
            timeout=scrape_timeout(),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _page_url(self, page: int, newest: bool = True) -> str:
        return with_page(self.search_url, page, self.PAGE_PARAM)

    def _parse_total(self, html: str) -> int:
        return parse_total(html)

    def _parse_list(self, html: str) -> list[Listing]:
        raise NotImplementedError

    def _parse_detail(self, listing: Listing, html: str) -> Listing:
        photos = []
        fragment = extract_listing_html(html or "")
        for match in _PHOTO_ATTR_RE.findall(fragment):
            url = abs_url(match, self.SITE)
            if url and url not in photos and "logo" not in url.casefold() and "icon" not in url.casefold():
                photos.append(url)
        if photos:
            listing.photos = photos[:40]
            listing.image_url = photos[0]
        desc = ""
        og = _OG_DESC_RE.search((html or "")[:_STRIP_TAGS_CHARS])
        if og:
            desc = html_lib.unescape(og.group(1))
        if desc:
            listing.description = clean(desc)
        price_czk, price_label = parse_price(fragment)
        if price_czk:
            listing.price_czk = price_czk
            listing.price_label = price_label
        if not listing.disposition:
            listing.disposition = parse_disposition(listing.name + " " + (listing.description or ""))
        if listing.area_m2 is None:
            listing.area_m2 = parse_area(listing.name + " " + (listing.description or ""))
        lat = _LAT_RE.search(fragment)
        lon = _LON_RE.search(fragment)
        if lat:
            listing.lat = float(lat.group(1))
        if lon:
            listing.lon = float(lon.group(1))
        return listing

    def _is_gone(self, html: str, status_code: int) -> bool:
        if status_code in {404, 410}:
            return True
        folded = (html or "").casefold()
        return any(hint in folded for hint in GONE_HINTS)

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        from app.scrape_http import request_with_log
        from app.sources import portal_of

        url = self._page_url(page, newest=newest)
        portal = portal_of(self.SITE or self.search_url or url)
        response = await request_with_log(
            self._client,
            "GET",
            url,
            portal=portal,
            headers={"Accept": "text/html,application/json;q=0.9"},
        )
        from app.scrape_timing import note, note_httpx

        note_httpx(response)
        if response.status_code in {404, 410}:
            return [], 0
        if response.status_code in {403, 429}:
            # Bot protection / rate limit (e.g. ceskereality.cz): back off and
            # retry a few times before giving up, instead of failing loudly.
            # Delays: 5s, 15s, 30s.
            import asyncio as _asyncio

            delays = (5.0, 15.0, 30.0)
            last = response
            for delay in delays:
                await _asyncio.sleep(delay)
                retry = await request_with_log(
                    self._client,
                    "GET",
                    url,
                    portal=portal,
                    headers={"Accept": "text/html,application/json;q=0.9"},
                )
                note_httpx(retry)
                last = retry
                if retry.status_code not in {403, 429}:
                    break
            response = last
            if response.status_code in {404, 410}:
                return [], 0
            if response.status_code in {403, 429}:
                # Still blocked: return empty instead of raising, so one
                # protected portal does not break the whole sync run.
                return [], 0
        response.raise_for_status()
        raw = response.content
        if page <= 1:
            self.search_url = str(response.url).split("#")[0]

        def _parse(blob: bytes) -> tuple[list[Listing], int]:
            text = blob.decode("utf-8", "replace")
            html = text if self.FULL_LIST_HTML else extract_listing_html(text)
            listings = self._parse_list(html)
            parsed_total = self._parse_total(html)
            total = parsed_total or (len(listings) if page == 1 else 0)
            return listings, total

        parse_started = time.monotonic()
        listings, total = await asyncio.to_thread(_parse, raw)
        note(parse_ms=(time.monotonic() - parse_started) * 1000.0)
        return listings, total

    async def fetch_pages(self, pages: int, newest: bool = True) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        for page in range(1, max(1, int(pages or 1)) + 1):
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
            needed = (int(total) + self.PAGE_SIZE - 1) // self.PAGE_SIZE if total else page
            if page >= needed:
                break
            page += 1
        return listings, total

    async def fetch_detail(self, listing: Listing) -> Listing:
        response = await self._client.get(listing.url, headers={"Accept": "text/html"})
        raw = response.content
        status = response.status_code
        if status not in {404, 410}:
            response.raise_for_status()

        def _parse(blob: bytes) -> Listing:
            text = blob.decode("utf-8", "replace")
            if self._is_gone(text, status):
                raise ListingGone(listing.url)
            return self._parse_detail(listing, extract_listing_html(text))

        return await asyncio.to_thread(_parse, raw)
