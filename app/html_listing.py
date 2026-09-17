"""Shared HTML listing helpers used by the extra Czech portals."""

from __future__ import annotations

import hashlib
import html as html_lib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.block_page import PortalBlocked, classify_block
from app.scrape_proxy import httpx_kwargs as scrape_httpx_kwargs, url_for as scrape_proxy_url_for
from app.sreality import Listing, ListingGone, format_price

JS_SAFE_ID = (1 << 53) - 1
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m", re.I)
PRICE_RE = re.compile(r"(\d{1,3}(?:[\s\u00a0.]\d{3})+|\d{4,8})\s*Kč", re.I)
DISP_RE = re.compile(r"(\d+)\s*\+\s*(kk|1)|(\d+)\s*kk|garson|atyp|pokoj", re.I)
COUNT_RE = re.compile(
    r"([\d\s\u00a0]+)\s+(?:inzerát|nemovitost|nabídek|výsled|byt)",
    re.I,
)
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
ESTATE_LABEL = {
    "byt": "Byt",
    "byty": "Byt",
    "dum": "Dům",
    "domy": "Dům",
    "pozemek": "Pozemek",
    "pozemky": "Pozemek",
    "pozemku": "Pozemek",
}
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


def clean(text: str) -> str:
    raw = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", raw.replace("\xa0", " ").replace("&zwj;", "")).strip()


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


def parse_area(text: str) -> int | None:
    match = AREA_RE.search((text or "").replace(",", "."))
    if not match:
        return None
    try:
        return int(round(float(match.group(1))))
    except ValueError:
        return None


def parse_price(text: str) -> tuple[int | None, str]:
    label = clean(text)
    folded = label.casefold()
    if "dohod" in folded or "info v rk" in folded or "cena v rk" in folded:
        return None, label or "Cena dohodou"
    match = PRICE_RE.search(label.replace("\xa0", " "))
    if not match:
        return None, label
    digits = re.sub(r"\D", "", match.group(1))
    return (int(digits) if digits else None), label


def parse_total(html: str) -> int:
    match = COUNT_RE.search((html or "").replace("\xa0", " "))
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
    match = re.search(r'(?:src|data-src|data-img)=["\']([^"\']+\.(?:jpg|jpeg|webp|png)[^"\']*)["\']', html or "", re.I)
    if match:
        return match.group(1)
    match = re.search(r'url\((["\']?)([^"\')]+)\1\)', html or "", re.I)
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
    TIMEOUT = 25.0

    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        headers = dict(BROWSER_HEADERS)
        if self.SITE:
            headers["Referer"] = self.SITE.rstrip("/") + "/"
            headers["Origin"] = self.SITE.rstrip("/")
        portal = self._portal_id()
        self._proxy_url = scrape_proxy_url_for(portal)
        self._client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=self.TIMEOUT,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            **scrape_httpx_kwargs(portal),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _page_url(self, page: int, newest: bool = True) -> str:
        return with_page(self.search_url, page, self.PAGE_PARAM)

    def _parse_total(self, html: str) -> int:
        return parse_total(html)

    def _parse_list(self, html: str) -> list[Listing]:
        raise NotImplementedError

    def _attach_local_coords(self, listings: list[Listing]) -> None:
        """Pin list cards from local city centers. Network geocode belongs on detail, not page-1."""
        missing = [item for item in listings if item.lat is None or item.lon is None]
        if not missing:
            return
        from app.places import approx_point_from_locality

        for item in missing:
            point = approx_point_from_locality((item.locality or item.name or "").strip())
            if point:
                item.lat, item.lon = point

    def _parse_detail(self, listing: Listing, html: str) -> Listing:
        photos = []
        for match in re.findall(r'(?:src|href)=["\']([^"\']+\.(?:jpg|jpeg|webp)[^"\']*)["\']', html or "", re.I):
            url = abs_url(match, self.SITE)
            if url and url not in photos and "logo" not in url.casefold() and "icon" not in url.casefold():
                photos.append(url)
        if photos:
            listing.photos = photos[:40]
            listing.image_url = photos[0]
        desc = ""
        og = re.search(r'property="og:description"\s+content="([^"]+)"', html or "", re.I)
        if og:
            desc = html_lib.unescape(og.group(1))
        if desc:
            listing.description = clean(desc)
        price_czk, price_label = parse_price(html)
        if price_czk:
            listing.price_czk = price_czk
            listing.price_label = price_label
        if not listing.disposition:
            listing.disposition = parse_disposition(listing.name + " " + (listing.description or ""))
        if listing.area_m2 is None:
            listing.area_m2 = parse_area(listing.name + " " + (listing.description or ""))
        lat = re.search(r'"lat(?:itude)?"\s*:\s*(-?\d+\.\d+)', html or "")
        lon = re.search(r'"l(?:on|ng)(?:itude)?"\s*:\s*(-?\d+\.\d+)', html or "")
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

    def _portal_id(self) -> str:
        host = (self.SITE or "").lower()
        for needle, name in (
            ("mmreality", "mmreality"),
            ("ceskereality", "ceskereality"),
            ("ulovdomov", "ulovdomov"),
            ("reality.cz", "realitycz"),
            ("remax", "remax"),
            ("annonce", "annonce"),
        ):
            if needle in host:
                return name
        return ""

    def _raise_if_blocked(self, response: httpx.Response) -> None:
        signal = classify_block(response.status_code, response.text, response.headers)
        if signal is None:
            return
        raise PortalBlocked(
            signal.kind,
            signal.status_code or response.status_code,
            portal=self._portal_id(),
            retry_after=signal.retry_after,
            detail=signal.detail,
        )

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        url = self._page_url(page, newest=newest)
        response = await self._client.get(url, headers={"Accept": "text/html,application/json;q=0.9"})
        if response.status_code in {404, 410}:
            return [], 0
        self._raise_if_blocked(response)
        response.raise_for_status()
        html = response.text
        if page <= 1:
            self.search_url = str(response.url).split("#")[0]
        listings = self._parse_list(html)
        self._attach_local_coords(listings)
        parsed_total = self._parse_total(html)
        total = parsed_total or (len(listings) if page == 1 else 0)
        return listings, total

    async def fetch_pages(self, pages: int, newest: bool = True) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        for page in range(1, max(1, int(pages or 1)) + 1):
            try:
                batch, page_total = await self.fetch_page(page, newest=newest)
            except PortalBlocked:
                break
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
            try:
                batch, page_total = await self.fetch_page(page, newest=newest)
            except PortalBlocked:
                break
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
        if self._is_gone(response.text, response.status_code):
            raise ListingGone(listing.url)
        response.raise_for_status()
        return self._parse_detail(listing, response.text)
