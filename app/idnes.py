from __future__ import annotations

import hashlib
import html as html_lib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app import idnes_url
from app.sreality import Listing, ListingGone, format_price

SITE = "https://reality.idnes.cz"
PAGE_SIZE = 25
OID_RE = re.compile(r"/detail/(pronajem|prodej|drazba)/([^/]+)/([^/]+)/([0-9a-f]{24})/", re.I)
COUNT_RE = re.compile(r"([\d\s]+)\s+inzerát", re.I)
PRICE_RE = re.compile(r"([\d\s]+)\s*Kč", re.I)
AREA_RE = re.compile(r"(\d+)\s*m", re.I)
DISP_RE = re.compile(r"(\d+\s*\+\s*(?:kk|1)|pokoj|atyp)", re.I)
FANCY_PHOTO_RE = re.compile(
    r'data-fancybox="images"[^>]*href="([^"]+)"|href="([^"]+)"[^>]*data-fancybox="images"',
    re.I,
)
THUMB_PHOTO_RE = re.compile(
    r"https://sta-reality2\.1gr\.cz/sta/compile/thumbs/[0-9a-f/]+\.(?:jpg|jpeg|webp)(?:\?[^\"'\s]*)?",
    re.I,
)
LAT_RE = re.compile(r'"listing_lat"\s*:\s*([0-9.]+)')
LON_RE = re.compile(r'"listing_lon"\s*:\s*([0-9.]+)')
PRICE_DL_RE = re.compile(r'"listing_price"\s*:\s*([0-9]+)')
AREA_DL_RE = re.compile(r'"listing_area"\s*:\s*([0-9]+)')
NAME_DL_RE = re.compile(r'"listing_name"\s*:\s*"([^"]+)"')
BRAND_DL_RE = re.compile(r'"listing_brand"\s*:\s*"([^"]+)"')
CAT_DL_RE = re.compile(r'"listing_category"\s*:\s*"([^"]+)"')
VAR_DL_RE = re.compile(r'"listing_variant"\s*:\s*"([^"]+)"')
DT_RE = re.compile(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", re.S | re.I)
DESC_RE = re.compile(r'<div[^>]*class="[^"]*b-desc[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
DESC2_RE = re.compile(r'<p[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
ARTICLE_RE = re.compile(r"<article\b.*?</article>", re.S | re.I)
RESULTS_RE = re.compile(
    r'id="snippet-s-result-articles"[^>]*>([\s\S]*?)<div[^>]*id="snippet-s-result-paginator',
    re.I,
)
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


def _clean(text: str) -> str:
    raw = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", raw.replace("\xa0", " ").replace("&zwj;", "")).strip()


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

    for left, right in FANCY_PHOTO_RE.findall(html or ""):
        add(left or right)
    if len(photos) < 2:
        for raw in THUMB_PHOTO_RE.findall(html or ""):
            add(raw)
    return photos[:40]


class IdnesClient:
    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=20.0,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    async def _attach_coords(self, listings: list[Listing], *, network: bool = False) -> None:
        """Pin list cards from local city centers. Network geocode belongs on detail, not page-1."""
        missing = [item for item in listings if item.lat is None or item.lon is None]
        if not missing:
            return
        from app.places import approx_point_from_locality, geocode_locality

        keys = list(dict.fromkeys((item.locality or "").strip() for item in missing if (item.locality or "").strip()))
        found: dict[str, tuple[float, float] | None] = {}
        for key in keys:
            found[key] = approx_point_from_locality(key)
        if network:
            for key in keys:
                if found.get(key):
                    continue
                found[key] = await geocode_locality(key)
        for item in missing:
            point = found.get((item.locality or "").strip())
            if point:
                item.lat, item.lon = point

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        url = self._page_url(page, newest)
        if page <= 1:
            response = await self._client.get(url, headers={"Accept": "text/html"})
        else:
            response = await self._client.get(
                url,
                headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
            )
        if response.status_code == 404:
            return [], 0
        response.raise_for_status()
        if page <= 1:
            self._remember_search_url(str(response.url))
        raw = response.text
        html = raw
        if page > 1:
            try:
                payload = response.json()
                html = (payload.get("snippets") or {}).get("snippet-s-result-articles") or html
            except Exception:
                pass
        listings = self._parse_list(self._results_html(html))
        await self._attach_coords(listings)
        total = self._parse_total(raw) or self._parse_total(html) or (len(listings) if page == 1 else 0)
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
        html = response.text
        folded = html.casefold()
        missing = "listing_id" not in html and "listing_lat" not in html
        if missing and (
            "inzerát byl stažen" in folded
            or "inzerát neexistuje" in folded
            or "stránka nenalezena" in folded
        ):
            raise ListingGone(listing.url)
        parsed = self._parse_detail(listing, html)
        return parsed

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
        return idnes_url.page_url(self.search_url, page, newest)

    def _parse_total(self, html: str) -> int:
        match = COUNT_RE.search(html.replace("\xa0", " "))
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
        title_m = re.search(r'class="c-products__title"[^>]*>(.*?)</h2>', html, re.S)
        if title_m:
            title = _clean(title_m.group(1))
        else:
            alt = re.search(r'alt="([^"]+)"', html)
            title = html_lib.unescape(alt.group(1)) if alt else f"{OFFER_LABEL.get(offer.casefold(), offer)} {estate}"
        loc_m = re.search(r'class="c-products__info"[^>]*>(.*?)</p>', html, re.S)
        locality = _clean(loc_m.group(1)) if loc_m else ""
        price_m = re.search(r'class="c-products__price"[^>]*>(.*?)</p>', html, re.S)
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
        im = re.search(r'data-src="([^"]+)"', html) or re.search(r"background-image:\s*url\('([^']+)'\)", html)
        if im:
            img = upgrade_photo(im.group(1).replace("\\/", "/").replace("\\", ""))
        photos = parse_photos(html)
        if photos:
            img = photos[0]
        extras = {
            "offer": OFFER_LABEL.get(offer.casefold(), offer),
            "estate": ESTATE_LABEL.get(estate.casefold(), estate.title()),
            "flags": [],
            "specs": [],
            "agency": _clean(re.search(r'data-brand="([^"]+)"', html).group(1)) if re.search(r'data-brand="([^"]+)"', html) else "",
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
            og = re.search(r'property="og:description" content="([^"]+)"', html)
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
