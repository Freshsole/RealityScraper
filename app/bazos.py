from __future__ import annotations

import asyncio
import html as html_lib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.sreality import Listing, ListingGone, format_price

SITE = "https://reality.bazos.cz"
PAGE_SIZE = 20
CATALOG_CONCURRENCY = 6
ID_RE = re.compile(r"/inzerat/(\d+)/([^\"'?]+)")
COUNT_RE = re.compile(r"Zobrazeno\s+\d+[–-]\d+\s+inzerátů z\s+([\d\s]+)", re.I)
PRICE_RE = re.compile(r"([\d\s]+)\s*Kč", re.I)
AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m", re.I)
DISP_RE = re.compile(r"(\d+)\s*\+\s*(kk|1)|(\d+)\s*kk|garson|atyp|pokoj", re.I)
DATE_RE = re.compile(r"\[(\d{1,2})\.(\d{1,2})\.\s*(\d{4})\]")
MAPS_RE = re.compile(r"maps/place/(-?\d+\.\d+),(-?\d+\.\d+)")
PHOTO_RE = re.compile(r"https://www\.bazos\.cz/img/(\d+)t?/(\d+)/(\d+)\.(?:jpg|jpeg|webp)(?:\?[^\"'\s]*)?", re.I)
CARD_RE = re.compile(r'<div class="inzeraty inzeratyflex">(.*?)<div class="inzeratyakce">', re.S | re.I)
TITLE_RE = re.compile(r"<h2[^>]*class=['\"]?nadpis['\"]?[^>]*>.*?<a[^>]*>(.*?)</a>", re.S | re.I)
TITLE2_RE = re.compile(r"<h1[^>]*class=['\"]?nadpisdetail['\"]?[^>]*>(.*?)</h1>", re.S | re.I)
LOC_RE = re.compile(r'class="inzeratylok"[^>]*>(.*?)</div>', re.S | re.I)
PRICE_BOX_RE = re.compile(r'class="inzeratycena"[^>]*>(.*?)</div>', re.S | re.I)
VIEWS_RE = re.compile(r'class="inzeratyview"[^>]*>([\d\s]+)', re.I)
NAME_RE = re.compile(r"<td[^>]*>Jméno:.*?<b[^>]*>.*?<span[^>]*>(.*?)</span>", re.S | re.I)
POPIS_RE = re.compile(r"class=['\"]?popis(?:detail)?['\"]?[^>]*>(.*?)</div>", re.S | re.I)

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

OFFER_LABEL = {"pronajem": "Pronájem", "prodej": "Prodej"}
ESTATE_LABEL = {
    "byt": "Byt",
    "dum": "Dům",
    "pozemek": "Pozemek",
    "projekty": "Projekt",
    "garaz": "Garáž",
    "kancelar": "Kancelář",
    "prostory": "Obchodní prostor",
    "restaurace": "Restaurace",
    "chata": "Chata",
    "sklad": "Sklad",
    "zahrada": "Zahrada",
    "podnajem": "Podnájem",
    "ostatni": "Ostatní",
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


def _clean(text: str) -> str:
    raw = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", raw.replace("\xa0", " ")).strip()


def _abs(url: str) -> str:
    raw = (url or "").replace("\\/", "/").strip()
    if raw.startswith("//"):
        return "https:" + raw
    if raw.startswith("/"):
        return urljoin(SITE + "/", raw)
    return raw


def upgrade_photo(url: str) -> str:
    raw = _abs(url).split("#")[0]
    return re.sub(r"/img/(\d+)t/", r"/img/\1/", raw)


def parse_photos(html: str) -> list[str]:
    photos: list[str] = []
    seen: set[str] = set()
    for index, folder, listing_id in PHOTO_RE.findall(html or ""):
        url = upgrade_photo(f"https://www.bazos.cz/img/{index}/{folder}/{listing_id}.jpg")
        key = url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        photos.append(url)
    return photos[:40]


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
    label = _clean(text)
    if "dohod" in label.casefold() or "inzerát" in label.casefold():
        return None, label or "Cena dohodou"
    match = PRICE_RE.search(label.replace("\xa0", " "))
    if not match:
        return None, label
    digits = re.sub(r"\D", "", match.group(1))
    return (int(digits) if digits else None), label


def parse_created(text: str) -> str | None:
    match = DATE_RE.search(text or "")
    if not match:
        return None
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    try:
        stamp = datetime(year, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
    return stamp.isoformat()


def specs_from_text(text: str) -> tuple[list[str], list[dict[str, str]]]:
    folded = (text or "").casefold()
    flags = [name for needle, name in FLAG_WORDS.items() if needle in folded]
    flags = list(dict.fromkeys(flags))
    specs: list[dict[str, str]] = []
    floor = re.search(r"(-?\d+)\s*\.\s*(?:podlaž|patro|np)\b", folded)
    if floor:
        specs.append({"label": "Podlaží", "value": floor.group(1)})
    if "družstev" in folded:
        specs.append({"label": "Vlastnictví", "value": "Družstevní"})
    elif "osobní" in folded:
        specs.append({"label": "Vlastnictví", "value": "Osobní"})
    if "panel" in folded:
        specs.append({"label": "Konstrukce", "value": "Panelová"})
    elif "cihl" in folded:
        specs.append({"label": "Konstrukce", "value": "Cihlová"})
    return flags, specs


class BazosClient:
    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=20.0,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=32),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _attach_coords(self, listings: list[Listing], *, network: bool = False) -> None:
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

    def _context(self) -> tuple[str, str]:
        parts = [part for part in urlsplit(self.search_url).path.split("/") if part and not part.isdigit()]
        offer = "pronajem"
        estate = "byt"
        if parts:
            from app.bazos_url import PATH_OFFER

            offer = PATH_OFFER.get(parts[0], "pronajem")
        if len(parts) > 1:
            estate = parts[1]
        return offer, estate

    def _page_url(self, page: int) -> str:
        split = urlsplit(self.search_url)
        parts = [part for part in split.path.split("/") if part and not part.isdigit()]
        path = "/" + "/".join(parts) + "/" if parts else "/"
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        query.pop("order", None)
        query["kitx"] = "ano"
        if page > 1:
            query["crp"] = str((page - 1) * PAGE_SIZE)
        else:
            query.pop("crp", None)
        return urlunsplit((split.scheme or "https", split.netloc or "reality.bazos.cz", path, urlencode(query), ""))

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        url = self._page_url(page)
        response = await self._client.get(url, headers={"Accept": "text/html"})
        if response.status_code in {404, 410}:
            return [], 0
        response.raise_for_status()
        html = response.text
        if page <= 1:
            self.search_url = str(response.url).split("#")[0]
        listings = self._parse_list(html)
        await self._attach_coords(listings)
        parsed_total = self._parse_total(html)
        total = parsed_total or (len(listings) if page == 1 else 0)
        return listings, total

    async def fetch_catalog(self, gate: asyncio.Semaphore | None = None) -> tuple[list[Listing], int]:
        first, total = await self.fetch_page(1)
        needed = max(1, (int(total) + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
        listings = list(first)
        seen = {item.id for item in first}
        if needed <= 1:
            return listings, total
        gate = gate or asyncio.Semaphore(CATALOG_CONCURRENCY)

        async def one(page: int) -> tuple[list[Listing], int]:
            async with gate:
                try:
                    return await self.fetch_page(page)
                except Exception:
                    await asyncio.sleep(0.25)
                    return await self.fetch_page(page)

        pages = list(range(2, needed + 1))
        failed = 0
        for start in range(0, len(pages), 6):
            rest = await asyncio.gather(
                *(one(page) for page in pages[start : start + 6]),
                return_exceptions=True,
            )
            for item in rest:
                if isinstance(item, Exception):
                    failed += 1
                    continue
                batch, page_total = item
                total = page_total or total
                for listing in batch:
                    if listing.id in seen:
                        continue
                    seen.add(listing.id)
                    listings.append(listing)
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
            needed = (int(total) + PAGE_SIZE - 1) // PAGE_SIZE if total else page
            if page >= needed:
                break
            page += 1
        return listings, total

    async def fetch_detail(self, listing: Listing) -> Listing:
        response = await self._client.get(listing.url, headers={"Accept": "text/html"})
        if response.status_code in {404, 410}:
            raise ListingGone(listing.url)
        response.raise_for_status()
        html = response.text
        folded = html.casefold()
        if "inzerát byl stažen" in folded or "inzerát neexistuje" in folded or "stránka nenalezena" in folded:
            raise ListingGone(listing.url)
        return self._parse_detail(listing, html)

    def _parse_total(self, html: str) -> int:
        match = COUNT_RE.search((html or "").replace("\xa0", " "))
        if not match:
            return 0
        digits = re.sub(r"\D", "", match.group(1))
        return int(digits) if digits else 0

    def _parse_list(self, html: str) -> list[Listing]:
        items: list[Listing] = []
        seen: set[str] = set()
        offer, estate = self._context()
        for block in CARD_RE.findall(html or ""):
            listing = self._parse_card(block, offer, estate)
            if listing and listing.url not in seen:
                seen.add(listing.url)
                items.append(listing)
        return items

    def _parse_card(self, html: str, offer: str, estate: str) -> Listing | None:
        match = ID_RE.search(html)
        if not match:
            return None
        listing_id, slug = match.groups()
        url = f"{SITE}/inzerat/{listing_id}/{slug}"
        title_m = TITLE_RE.search(html)
        title = _clean(title_m.group(1) if title_m else "")
        if not title:
            alt = re.search(r'alt="([^"]+)"', html)
            title = html_lib.unescape(alt.group(1)) if alt else f"{OFFER_LABEL.get(offer, offer)} {estate}"
        loc_m = LOC_RE.search(html)
        locality = _clean((loc_m.group(1) if loc_m else "").replace("<br>", " ").replace("<br/>", " "))
        price_m = PRICE_BOX_RE.search(html)
        price_czk, price_label = parse_price(price_m.group(1) if price_m else "")
        popis_m = POPIS_RE.search(html)
        popis = _clean(popis_m.group(1) if popis_m else "")
        blob = f"{title} {popis}"
        disp = parse_disposition(blob)
        area = parse_area(blob)
        photos = parse_photos(html)
        img = photos[0] if photos else None
        views_m = VIEWS_RE.search(html)
        views = int(re.sub(r"\D", "", views_m.group(1))) if views_m and re.sub(r"\D", "", views_m.group(1)) else None
        flags, specs = specs_from_text(blob)
        rent = offer == "pronajem" or "pronáj" in title.casefold()
        extras = {
            "offer": "Pronájem" if rent else "Prodej",
            "estate": ESTATE_LABEL.get(estate, estate.title()),
            "flags": flags,
            "specs": specs,
            "agency": "",
        }
        return Listing(
            id=int(listing_id),
            name=title,
            price_czk=price_czk,
            price_label=price_label or format_price(price_czk, "měsíc" if rent else "ks"),
            disposition=disp,
            area_m2=area,
            locality=locality,
            url=url,
            image_url=img,
            photos=photos,
            extras=extras,
            advert_code=listing_id,
            created_on=parse_created(html),
            views=views,
            description=popis or None,
        )

    def _parse_detail(self, listing: Listing, html: str) -> Listing:
        title_m = TITLE2_RE.search(html)
        if title_m:
            listing.name = _clean(title_m.group(1)) or listing.name
        loc_m = LOC_RE.search(html)
        if loc_m:
            listing.locality = _clean(loc_m.group(1).replace("<br>", " ")) or listing.locality
        price_m = PRICE_BOX_RE.search(html)
        if price_m:
            price_czk, price_label = parse_price(price_m.group(1))
            if price_czk is not None:
                listing.price_czk = price_czk
            if price_label:
                listing.price_label = price_label
        popis_m = POPIS_RE.search(html)
        if popis_m:
            listing.description = _clean(popis_m.group(1))
        blob = f"{listing.name} {listing.description or ''}"
        listing.disposition = listing.disposition or parse_disposition(blob)
        listing.area_m2 = listing.area_m2 or parse_area(blob)
        maps = MAPS_RE.search(html)
        if maps:
            listing.lat = float(maps.group(1))
            listing.lon = float(maps.group(2))
        photos = parse_photos(html)
        if photos:
            listing.photos = photos
            listing.image_url = photos[0]
        flags, specs = specs_from_text(blob)
        extras = dict(listing.extras or {})
        extras["flags"] = list(dict.fromkeys((extras.get("flags") or []) + flags))
        extras["specs"] = specs or extras.get("specs") or []
        name_m = NAME_RE.search(html)
        if name_m:
            extras["agency"] = _clean(name_m.group(1))
        listing.extras = extras
        listing.created_on = listing.created_on or parse_created(html)
        return listing
