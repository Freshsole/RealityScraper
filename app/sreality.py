from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    "Referer": "https://www.sreality.cz/",
}

IMAGE_TRANSFORM = "fl=res,800,600,3|shr,,20|jpg,80"
AREA_RE = re.compile(r"(\d+)\s*m", re.IGNORECASE)


@dataclass
class Listing:
    id: int
    name: str
    price_czk: int | None
    price_label: str
    disposition: str
    area_m2: int | None
    locality: str
    url: str
    image_url: str | None
    lat: float | None = None
    lon: float | None = None
    created_on: str | None = None
    edited_on: str | None = None
    views: int | None = None
    old_price_czk: int | None = None
    advert_code: str | None = None
    kind: str = "new"
    changes: list[tuple[str, str, str]] = field(default_factory=list)

    def maps_url(self) -> str | None:
        return google_maps_url(self.lat, self.lon, self.locality)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["maps_url"] = self.maps_url()
        return data


class SrealityClient:
    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        self._build_id: str | None = None
        self._client = httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            follow_redirects=True,
            timeout=25.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_pages(self, pages: int, newest: bool = True) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        for page in range(1, pages + 1):
            batch, page_total = await self.fetch_page(page, newest=newest)
            total = page_total
            ids = [item.id for item in batch]
            if not batch or (page > 1 and all(i in seen for i in ids)):
                break
            for item in batch:
                if item.id not in seen:
                    seen.add(item.id)
                    listings.append(item)
        return listings, total

    async def fetch_all(self, newest: bool = True, max_pages: int = 40) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        for page in range(1, max_pages + 1):
            batch, page_total = await self.fetch_page(page, newest=newest)
            total = page_total
            ids = [item.id for item in batch]
            if not batch or (page > 1 and all(i in seen for i in ids)):
                break
            for item in batch:
                if item.id not in seen:
                    seen.add(item.id)
                    listings.append(item)
            if total and len(listings) >= total:
                break
            await asyncio.sleep(0.2)
        return listings, total

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        try:
            return await self._fetch_next_data(page, newest)
        except Exception:
            return await self._fetch_html(page, newest)

    async def _fetch_next_data(self, page: int, newest: bool) -> tuple[list[Listing], int]:
        build_id = await self._resolve_build_id()
        path, query = self._search_parts(newest)
        if page > 1:
            query["strana"] = str(page)
        data_path = f"/_next/data/{build_id}/cs/hledani/{path}.json"
        url = urljoin("https://www.sreality.cz", data_path) + "?" + urlencode(query, doseq=True)
        response = await self._client.get(
            url,
            headers={"Accept": "application/json", "x-nextjs-data": "1"},
        )
        if response.status_code == 404:
            self._build_id = None
            build_id = await self._resolve_build_id()
            data_path = f"/_next/data/{build_id}/cs/hledani/{path}.json"
            url = urljoin("https://www.sreality.cz", data_path) + "?" + urlencode(query, doseq=True)
            response = await self._client.get(
                url,
                headers={"Accept": "application/json", "x-nextjs-data": "1"},
            )
        response.raise_for_status()
        payload = response.json()
        return parse_search_payload(payload.get("pageProps") or payload)

    async def _fetch_html(self, page: int, newest: bool) -> tuple[list[Listing], int]:
        url = self._html_url(page, newest)
        response = await self._client.get(url, headers={"Accept": "text/html"})
        response.raise_for_status()
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            response.text,
        )
        if not match:
            raise RuntimeError("Sreality HTML does not contain __NEXT_DATA__")
        import json

        data = json.loads(match.group(1))
        build_id = data.get("buildId")
        if build_id:
            self._build_id = str(build_id)
        return parse_search_payload(data.get("props", {}).get("pageProps", {}))

    async def _resolve_build_id(self) -> str:
        if self._build_id:
            return self._build_id
        response = await self._client.get(self._html_url(1, True), headers={"Accept": "text/html"})
        response.raise_for_status()
        match = re.search(r'"buildId":"([^"]+)"', response.text)
        if not match:
            raise RuntimeError("Could not resolve Sreality buildId")
        self._build_id = match.group(1)
        return self._build_id

    def _search_parts(self, newest: bool) -> tuple[str, dict[str, str]]:
        split = urlsplit(self.search_url)
        path = split.path.rstrip("/")
        prefix = "/hledani/"
        if not path.startswith(prefix):
            raise ValueError(f"Unsupported search URL: {self.search_url}")
        slug = path[len(prefix) :]
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        if newest:
            query["razeni"] = "nejnovejsi"
        query["noredirect"] = "1"
        return slug, query

    def _html_url(self, page: int, newest: bool) -> str:
        split = urlsplit(self.search_url)
        query = dict(parse_qsl(split.query, keep_blank_values=True))
        if newest:
            query["razeni"] = "nejnovejsi"
        query["noredirect"] = "1"
        if page > 1:
            query["strana"] = str(page)
        return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))

    async def fetch_detail(self, listing: Listing) -> Listing:
        try:
            return await self._fetch_detail_next(listing)
        except Exception:
            return listing

    async def _fetch_detail_next(self, listing: Listing) -> Listing:
        build_id = await self._resolve_build_id()
        path = urlsplit(listing.url).path.rstrip("/")
        url = f"https://www.sreality.cz/_next/data/{build_id}/cs{path}.json"
        response = await self._client.get(
            url,
            headers={"Accept": "application/json", "x-nextjs-data": "1"},
        )
        if response.status_code == 404:
            self._build_id = None
            build_id = await self._resolve_build_id()
            url = f"https://www.sreality.cz/_next/data/{build_id}/cs{path}.json"
            response = await self._client.get(
                url,
                headers={"Accept": "application/json", "x-nextjs-data": "1"},
            )
        response.raise_for_status()
        return apply_detail(listing, response.json())


def apply_detail(listing: Listing, payload: dict[str, Any]) -> Listing:
    page_props = payload.get("pageProps") or payload
    queries = (page_props.get("dehydratedState") or {}).get("queries") or []
    estate = None
    for query in queries:
        key = query.get("queryKey") or []
        if key and key[0] == "estate":
            estate = (query.get("state") or {}).get("data")
            break
    if not isinstance(estate, dict):
        return listing
    params = estate.get("params") or {}
    listing.created_on = _as_date(params.get("since"))
    listing.edited_on = _as_date(params.get("edited"))
    try:
        listing.views = int(params["stats"]) if params.get("stats") is not None else listing.views
    except (TypeError, ValueError):
        pass
    listing.advert_code = str(params["advertCode"]) if params.get("advertCode") not in (None, "") else None
    old_price = estate.get("priceSummaryOldCzk")
    try:
        listing.old_price_czk = int(old_price) if old_price is not None else None
    except (TypeError, ValueError):
        listing.old_price_czk = None
    lat, lon = coords_from_locality(estate.get("locality") or {})
    if lat is not None and lon is not None:
        listing.lat = lat
        listing.lon = lon
    return listing


def _as_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text[:10] if text else None


def is_recently_created(created_on: str | None, max_age_days: int) -> bool:
    if not created_on:
        return True
    try:
        created = date.fromisoformat(created_on[:10])
    except ValueError:
        return True
    today = datetime.now(timezone.utc).date()
    return (today - created).days <= max_age_days


def format_cz_date(value: str | None) -> str:
    if not value:
        return "neznámé"
    try:
        parsed = date.fromisoformat(value[:10])
        return f"{parsed.day}. {parsed.month}. {parsed.year}"
    except ValueError:
        return value


def parse_search_payload(page_props: dict[str, Any]) -> tuple[list[Listing], int]:
    queries = (
        page_props.get("dehydratedState", {}).get("queries", [])
        if isinstance(page_props, dict)
        else []
    )
    for query in queries:
        key = query.get("queryKey") or []
        if key and key[0] == "estatesSearch":
            data = (query.get("state") or {}).get("data") or {}
            results = data.get("results") or []
            total = int((data.get("pagination") or {}).get("total") or 0)
            listings = [item for raw in results if (item := listing_from_raw(raw))]
            return listings, total
    return [], 0


def listing_from_raw(raw: dict[str, Any]) -> Listing | None:
    listing_id = raw.get("id")
    name = (raw.get("name") or "").replace("\xa0", " ").strip()
    if not listing_id or not name:
        return None
    disposition = ((raw.get("categorySubCb") or {}).get("name") or "").strip()
    if not disposition:
        return None
    price = raw.get("priceCzk")
    try:
        price_czk = int(price) if price is not None else None
    except (TypeError, ValueError):
        price_czk = None
    unit = ((raw.get("priceUnitCb") or {}).get("name") or "za měsíc").strip()
    price_label = format_price(price_czk, unit)
    area = parse_area(name, price_czk, raw.get("priceCzkPerSqM"))
    loc = raw.get("locality") or {}
    locality = format_locality(loc)
    lat, lon = coords_from_locality(loc)
    url = build_detail_url(raw)
    image_url = first_image_url(raw.get("images") or [])
    return Listing(
        id=int(listing_id),
        name=name,
        price_czk=price_czk,
        price_label=price_label,
        disposition=disposition,
        area_m2=area,
        locality=locality,
        url=url,
        image_url=image_url,
        lat=lat,
        lon=lon,
    )


def parse_area(name: str, price: int | None, per_sqm: Any) -> int | None:
    match = AREA_RE.search(name)
    if match:
        return int(match.group(1))
    try:
        if price and per_sqm:
            return int(round(float(price) / float(per_sqm)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def format_price(price: int | None, unit: str) -> str:
    if price is None:
        return "Cena neuvedena"
    formatted = f"{price:,}".replace(",", " ")
    return f"{formatted} Kč/{unit.replace('za ', '')}"


def coords_from_locality(loc: dict[str, Any]) -> tuple[float | None, float | None]:
    raw_lat = loc.get("latitude", loc.get("lat"))
    raw_lon = loc.get("longitude", loc.get("lon", loc.get("lng")))
    try:
        lat = float(raw_lat)
        lon = float(raw_lon)
    except (TypeError, ValueError):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def google_maps_url(lat: float | None, lon: float | None, locality: str = "") -> str | None:
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    text = (locality or "").strip()
    if not text:
        return None
    from urllib.parse import quote

    return f"https://www.google.com/maps/search/?api=1&query={quote(text)}"


def format_locality(loc: dict[str, Any]) -> str:
    street = (loc.get("street") or "").strip()
    part = (loc.get("cityPart") or "").strip()
    district = (loc.get("district") or "").strip()
    city = (loc.get("city") or "Praha").strip()
    if street and part:
        return f"{street}, {city} – {part}"
    if part:
        return f"{city} – {part}"
    return district or city


def build_detail_url(raw: dict[str, Any]) -> str:
    loc = raw.get("locality") or {}
    slug = "-".join(
        part
        for part in (
            loc.get("citySeoName"),
            loc.get("cityPartSeoName"),
            loc.get("streetSeoName"),
        )
        if part
    )
    offer = "pronajem"
    kind = "byt"
    disposition = ((raw.get("categorySubCb") or {}).get("name") or "byt").replace(" ", "")
    return f"https://www.sreality.cz/detail/{offer}/{kind}/{disposition}/{slug}/{raw['id']}"


def first_image_url(images: list[dict[str, Any]]) -> str | None:
    if not images:
        return None
    raw = (images[0].get("url") or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if "fl=" not in raw:
        sep = "&" if "?" in raw else "?"
        raw = f"{raw}{sep}{IMAGE_TRANSFORM}"
    return raw


async def download_image(client: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    try:
        response = await client.get(url, headers={**BROWSER_HEADERS, "Accept": "image/*"})
        response.raise_for_status()
        data = response.content
        if not data or len(data) < 100:
            return None
        content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
        return data, content_type
    except httpx.HTTPError:
        return None
