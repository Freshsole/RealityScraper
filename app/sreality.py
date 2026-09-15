from __future__ import annotations

import asyncio
import json
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


class ListingGone(Exception):
    """Portal listing is no longer available."""


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
    photos: list[str] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    created_on: str | None = None
    edited_on: str | None = None
    views: int | None = None
    old_price_czk: int | None = None
    advert_code: str | None = None
    description: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    kind: str = "new"
    changes: list[tuple[str, str, str]] = field(default_factory=list)

    def maps_url(self) -> str | None:
        return google_maps_url(self.lat, self.lon, self.locality)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "maps_url":
            value = self.maps_url()
            return default if value is None else value
        if key not in self.__dataclass_fields__:
            return default
        value = getattr(self, key)
        return default if value is None else value

    def keys(self):
        return list(self.__dataclass_fields__) + ["maps_url"]

    def __contains__(self, key: object) -> bool:
        return key in self.__dataclass_fields__ or key == "maps_url"

    def __getitem__(self, key: str) -> Any:
        if key not in self:
            raise KeyError(key)
        return self.get(key)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["maps_url"] = self.maps_url()
        return data


def listing_from_dict(data: dict[str, Any] | Listing | None) -> Listing:
    if isinstance(data, Listing):
        return data
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        if hasattr(data, "to_dict"):
            data = data.to_dict()
        else:
            try:
                data = dict(data)
            except (TypeError, ValueError):
                data = {}
    allowed = {key: data[key] for key in Listing.__dataclass_fields__ if key in data}
    changes = allowed.get("changes") or []
    allowed["changes"] = [tuple(item) for item in changes]
    extras = allowed.get("extras")
    if extras is None:
        allowed["extras"] = {}
    elif isinstance(extras, str):
        try:
            allowed["extras"] = json.loads(extras) if extras else {}
        except json.JSONDecodeError:
            allowed["extras"] = {}
    elif not isinstance(extras, dict):
        allowed["extras"] = {}
    return Listing(**allowed)


_SHARED_BUILD_ID: str | None = None


class SrealityClient:
    def __init__(self, search_url: str) -> None:
        self.search_url = search_url
        self._client = httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            follow_redirects=True,
            timeout=25.0,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
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

    async def fetch_all(self, newest: bool = True, max_pages: int | None = None) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        page = 1
        while True:
            if max_pages is not None and page > max_pages:
                break
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
            page += 1
            await asyncio.sleep(0.15)
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
            self._clear_build_id()
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
            self._set_build_id(str(build_id))
        return parse_search_payload(data.get("props", {}).get("pageProps", {}))

    def _set_build_id(self, value: str | None) -> None:
        global _SHARED_BUILD_ID
        _SHARED_BUILD_ID = value

    def _clear_build_id(self) -> None:
        self._set_build_id(None)

    async def _resolve_build_id(self) -> str:
        if _SHARED_BUILD_ID:
            return _SHARED_BUILD_ID
        response = await self._client.get(self._html_url(1, True), headers={"Accept": "text/html"})
        response.raise_for_status()
        match = re.search(r'"buildId":"([^"]+)"', response.text)
        if not match:
            raise RuntimeError("Could not resolve Sreality buildId")
        self._set_build_id(match.group(1))
        return match.group(1)

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
        except ListingGone:
            raise
        except Exception:
            return listing

    async def fetch_listing_url(self, url: str) -> Listing:
        stub = stub_listing_from_url(url)
        if not stub.id:
            raise ValueError("Neplatná Sreality URL")
        listing = await self._fetch_detail_next(stub)
        if not (listing.name or "").strip() or listing.name.startswith("Inzerát "):
            # Still usable if price/disposition filled; otherwise fail soft
            if listing.price_czk is None and not listing.disposition:
                raise ValueError("Nepodařilo se načíst detail inzerátu")
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
            self._clear_build_id()
            build_id = await self._resolve_build_id()
            url = f"https://www.sreality.cz/_next/data/{build_id}/cs{path}.json"
            response = await self._client.get(
                url,
                headers={"Accept": "application/json", "x-nextjs-data": "1"},
            )
        if response.status_code == 404:
            raise ListingGone(listing.url)
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
    merged = listing_from_estate(estate, listing.url or "")
    if merged is not None:
        # Prefer freshly scraped core fields; keep stub url/id if needed.
        for field_name in (
            "id",
            "name",
            "price_czk",
            "price_label",
            "disposition",
            "area_m2",
            "locality",
            "image_url",
            "photos",
            "lat",
            "lon",
            "created_on",
            "edited_on",
            "views",
            "old_price_czk",
            "advert_code",
            "description",
            "extras",
        ):
            value = getattr(merged, field_name)
            if value not in (None, "", [], {}):
                setattr(listing, field_name, value)
        if merged.url:
            listing.url = merged.url
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
    extra = image_urls(estate.get("images") or params.get("images") or [])
    if extra:
        listing.photos = extra
        listing.image_url = listing.photos[0]
    text = (estate.get("description") or "").replace("\xa0", " ").strip()
    if text:
        listing.description = text
    listing.extras = extras_from_sreality(estate)
    from app.places import refine_listing_location

    refine_listing_location(listing)
    return listing


def listing_from_estate(estate: dict[str, Any], url: str = "") -> Listing | None:
    """Build a Listing from Sreality detail `estate` JSON."""
    listing_id = estate.get("id")
    try:
        listing_id = int(listing_id)
    except (TypeError, ValueError):
        listing_id = None
    if not listing_id and url:
        match = re.search(r"/(\d+)/?$", urlsplit(url).path)
        if match:
            listing_id = int(match.group(1))
    name = (estate.get("name") or estate.get("title") or "").replace("\xa0", " ").strip()
    if not listing_id:
        return None
    if not name:
        name = f"Inzerát {listing_id}"

    disposition = _param_label(estate.get("categorySubCb")) or ""
    if not disposition:
        disposition = ((estate.get("categorySubCb") or {}).get("name") or "").strip() if isinstance(estate.get("categorySubCb"), dict) else ""

    price = estate.get("priceCzk")
    if price is None:
        price = estate.get("priceSummaryCzk")
    try:
        price_czk = int(price) if price is not None else None
    except (TypeError, ValueError):
        price_czk = None
    unit = _param_label(estate.get("priceUnitCb")) or "měsíc"
    if "/prodej/" in (url or "").lower():
        unit = unit if unit and unit != "měsíc" else "ks"
    price_label = format_price(price_czk, f"za {unit}" if not str(unit).startswith("za ") else unit)

    params = estate.get("params") or {}
    area = None
    for key in ("usableArea", "estateArea", "area", "floorArea"):
        raw = params.get(key) if isinstance(params, dict) else None
        if raw is None:
            raw = estate.get(key)
        try:
            if raw is not None:
                area = int(float(raw))
                break
        except (TypeError, ValueError):
            continue
    if area is None:
        area = parse_area(name, price_czk, estate.get("priceCzkPerSqM"))

    loc = estate.get("locality") or {}
    locality = format_locality(loc) if isinstance(loc, dict) else str(loc or "")
    lat, lon = coords_from_locality(loc) if isinstance(loc, dict) else (None, None)
    photos = image_urls(estate.get("images") or params.get("images") or [])
    detail_url = (url or "").strip() or build_detail_url(estate if "locality" in estate else {**estate, "id": listing_id})
    if not detail_url.startswith("http"):
        detail_url = urljoin("https://www.sreality.cz", detail_url)

    old_price = estate.get("priceSummaryOldCzk")
    try:
        old_price_czk = int(old_price) if old_price is not None else None
    except (TypeError, ValueError):
        old_price_czk = None

    listing = Listing(
        id=listing_id,
        name=name,
        price_czk=price_czk,
        price_label=price_label,
        disposition=disposition or "byt",
        area_m2=area,
        locality=locality,
        url=detail_url.split("?")[0].rstrip("/"),
        image_url=photos[0] if photos else None,
        photos=photos,
        lat=lat,
        lon=lon,
        created_on=_as_date(params.get("since")),
        edited_on=_as_date(params.get("edited")),
        views=None,
        old_price_czk=old_price_czk,
        advert_code=str(params["advertCode"]) if params.get("advertCode") not in (None, "") else None,
        description=(estate.get("description") or "").replace("\xa0", " ").strip() or None,
        extras=extras_from_sreality(estate),
    )
    try:
        listing.views = int(params["stats"]) if params.get("stats") is not None else None
    except (TypeError, ValueError):
        listing.views = None
    return listing


def stub_listing_from_url(url: str) -> Listing:
    raw = (url or "").strip()
    if raw.startswith("/"):
        raw = urljoin("https://www.sreality.cz", raw)
    path = urlsplit(raw).path.rstrip("/")
    match = re.search(r"/(\d+)$", path)
    listing_id = int(match.group(1)) if match else 0
    return Listing(
        id=listing_id or 0,
        name="",
        price_czk=None,
        price_label="",
        disposition="",
        area_m2=None,
        locality="",
        url=raw.split("?")[0].rstrip("/"),
        image_url=None,
    )


def extras_from_sreality(estate: dict[str, Any]) -> dict[str, Any]:
    params = estate.get("params") or {}
    flags: list[str] = []
    specs: list[dict[str, str]] = []

    def add_spec(label: str, value: Any) -> None:
        text = _param_label(value)
        if text:
            specs.append({"label": label, "value": text})

    if params.get("balcony"):
        flags.append("balcony")
        area = params.get("balconyArea")
        specs.append({"label": "Balkon", "value": f"{area} m²" if area else "ano"})
    if params.get("loggia"):
        flags.append("loggia")
        area = params.get("loggiaArea")
        specs.append({"label": "Lodžie", "value": f"{area} m²" if area else "ano"})
    if params.get("cellar"):
        flags.append("cellar")
        area = params.get("cellarArea")
        specs.append({"label": "Sklep", "value": f"{area} m²" if area else "ano"})
    if params.get("terrace"):
        flags.append("terrace")
        area = params.get("terraceArea")
        specs.append({"label": "Terasa", "value": f"{area} m²" if area else "ano"})
    if params.get("garage") or params.get("garageCount"):
        flags.append("garage")
        add_spec("Garáž", params.get("garageCount") or True)
    if _param_truthy(params.get("elevator")):
        flags.append("lift")
        specs.append({"label": "Výtah", "value": "ano"})
    if params.get("parkingLots") or _param_truthy(params.get("parking")):
        flags.append("parking")
        add_spec("Parkování", params.get("parkingLots") or params.get("parking"))
    if _param_truthy(params.get("easyAccess")):
        flags.append("barrier_free")
        add_spec("Bezbariérový", params.get("easyAccess"))
    if params.get("garden") or params.get("gardenArea"):
        flags.append("garden")
        area = params.get("gardenArea")
        specs.append({"label": "Zahrada", "value": f"{area} m²" if area else "ano"})
    if _param_truthy(params.get("pets")) or _param_truthy(params.get("animals")):
        flags.append("pets")
        specs.append({"label": "Mazlíčci", "value": "povolení"})

    floor_no = params.get("floorNumber")
    floors = params.get("floors")
    if floor_no not in (None, ""):
        specs.append({"label": "Podlaží", "value": f"{floor_no}/{floors}" if floors else str(floor_no)})
    add_spec("Vlastnictví", params.get("ownership"))
    add_spec("Stav", params.get("buildingCondition"))
    add_spec("Konstrukce", params.get("buildingType"))
    add_spec("Vybavení", params.get("furnished"))
    add_spec("Energetická náročnost", params.get("energyEfficiencyRating"))
    add_spec("Kauce", params.get("refundableDeposit"))
    if params.get("costOfLiving"):
        add_spec("Poplatky", params.get("costOfLiving"))
    if params.get("priceNote"):
        add_spec("Poznámka k ceně", params.get("priceNote"))
    internet = params.get("internetConnectionTypeSet") or []
    if isinstance(internet, list) and internet:
        names = [name for item in internet if (name := _param_label(item))]
        if names:
            specs.append({"label": "Internet", "value": ", ".join(names)})
    offer = _param_label(estate.get("categoryTypeCb"))
    estate_kind = _param_label(estate.get("categoryMainCb"))
    sub = _param_label(estate.get("categorySubCb")) or ""
    if "pokoj" in (estate_kind or "").lower() or sub.lower() == "pokoj":
        flags.append("roommate")
        specs.append({"label": "Spolubydlení", "value": "ano"})
    return {
        "offer": offer,
        "estate": estate_kind,
        "flags": flags,
        "specs": specs,
    }


def _param_label(value: Any) -> str | None:
    if value in (None, "", False):
        return None
    if value is True:
        return "ano"
    if isinstance(value, dict):
        name = str(value.get("name") or "").replace("\xa0", " ").strip()
        if not name or name.startswith("-"):
            return None
        return name
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def _param_truthy(value: Any) -> bool:
    if value in (None, "", False, 0):
        return False
    if isinstance(value, dict):
        raw = value.get("value")
        name = str(value.get("name") or "")
        if name.startswith("-"):
            return False
        return raw not in (None, "", False, 0)
    return True


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
        # Keep listing — empty disposition used to drop valid flats from catalog.
        disposition = "byt"
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
    photos = image_urls(raw.get("images") or [])
    extras = {"flags": ["roommate"]} if disposition.lower() == "pokoj" else {}
    return Listing(
        id=int(listing_id),
        name=name,
        price_czk=price_czk,
        price_label=price_label,
        disposition=disposition,
        area_m2=area,
        locality=locality,
        url=url,
        image_url=photos[0] if photos else None,
        photos=photos,
        lat=lat,
        lon=lon,
        extras=extras,
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
    number = _locality_house_number(loc)
    part = (loc.get("cityPart") or "").strip()
    district = (loc.get("district") or "").strip()
    city = (loc.get("city") or "Praha").strip()
    street_line = f"{street} {number}".strip() if number else street
    if street_line and part:
        return f"{street_line}, {city} – {part}"
    if street_line:
        return f"{street_line}, {city}"
    if part:
        return f"{city} – {part}"
    return district or city


def _locality_house_number(loc: dict[str, Any]) -> str:
    for key in (
        "streetNumber",
        "houseNumber",
        "houseNo",
        "streetNo",
        "descNumber",
        "descriptiveNumber",
        "orientationNumber",
        "evidenceNumber",
        "cp",
        "co",
    ):
        value = loc.get(key)
        if value not in (None, ""):
            return str(value).strip()
    desc = loc.get("descNo") or loc.get("cisloPopisne")
    ori = loc.get("oriNo") or loc.get("cisloOrientacni")
    if desc and ori:
        return f"{str(desc).strip()}/{str(ori).strip()}"
    if desc:
        return str(desc).strip()
    if ori:
        return str(ori).strip()
    return ""


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


def cdn_image_url(url: str | None) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if "fl=" in raw:
        return raw
    if "sdn.cz" in raw or "sreality" in raw:
        sep = "&" if "?" in raw else "?"
        return f"{raw}{sep}{IMAGE_TRANSFORM}"
    return raw


def first_image_url(images: list[dict[str, Any]]) -> str | None:
    urls = image_urls(images)
    return urls[0] if urls else None


def _image_src(item: Any) -> str | None:
    if isinstance(item, str):
        return cdn_image_url(item)
    if not isinstance(item, dict):
        return None
    for key in ("url", "href", "src"):
        raw = item.get(key)
        if raw:
            return cdn_image_url(raw)
    links = item.get("_links") or {}
    if isinstance(links, dict):
        for name in ("gallery", "self", "dynamic", "image", "view"):
            node = links.get(name)
            href = node.get("href") if isinstance(node, dict) else None
            if href:
                return cdn_image_url(href)
    return None


def image_urls(images: list[dict[str, Any]], limit: int = 80) -> list[str]:
    rows = list(images or [])
    if rows and all(isinstance(item, dict) and item.get("order") is not None for item in rows):
        rows = sorted(rows, key=lambda item: item.get("order") or 0)
    result: list[str] = []
    for item in rows:
        raw = _image_src(item)
        if raw and raw not in result:
            result.append(raw)
        if len(result) >= limit:
            break
    return result


async def download_image(client: httpx.AsyncClient, url: str) -> tuple[bytes, str] | None:
    url = cdn_image_url(url) or url
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
