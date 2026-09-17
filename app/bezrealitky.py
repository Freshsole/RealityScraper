from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from app.sreality import Listing, format_price

GRAPHQL_URL = "https://api.bezrealitky.cz/graphql/"
SITE = "https://www.bezrealitky.cz"
PAGE_SIZE = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Origin": SITE,
    "Referer": f"{SITE}/",
}

OFFERS = {"PRONAJEM", "PRODEJ"}
ESTATES = {"BYT", "DUM", "POZEMEK", "GARAZ", "KANCELAR", "NEBYTOVY_PROSTOR", "REKREACNI_OBJEKT"}
DISPOSITIONS = {
    "DISP_1_KK",
    "DISP_1_1",
    "DISP_2_KK",
    "DISP_2_1",
    "DISP_3_KK",
    "DISP_3_1",
    "DISP_4_KK",
    "DISP_4_1",
    "DISP_5_KK",
    "DISP_5_1",
    "DISP_6_KK",
    "DISP_6_1",
    "DISP_7_KK",
    "DISP_7_1",
    "DISP_ATYP",
    "GARSONIERA",
    "OSTATNI",
}
OWNERSHIP = {"DRUZSTEVNI", "OBECNI", "OSOBNI", "OSTATNI"}
CONDITIONS = {
    "AFTER_PARTIAL_RECONSTRUCTION",
    "AFTER_RECONSTRUCTION",
    "BAD",
    "BEFORE_RECONSTRUCTION",
    "CONSTRUCTION",
    "DEMOLITION",
    "GOOD",
    "IN_RECONSTRUCTION",
    "NEW",
    "PROJECT",
    "VERY_GOOD",
}
CONSTRUCTIONS = {"BRICK", "MIXED", "PANEL", "PREFAB", "SKELET", "STONE"}
EQUIPPED = {"CASTECNE", "NEVYBAVENY", "VYBAVENY"}
CURRENCIES = {"CZK", "EUR"}
ORDERS = {"TIMEORDER_DESC", "TIMEORDER_ASC", "PRICE_ASC", "PRICE_DESC"}
OSM_RE = re.compile(r"^R\d+$")
BOOL_KEYS = (
    "balcony",
    "loggia",
    "cellar",
    "terrace",
    "barrierFree",
    "garage",
    "lift",
    "parking",
    "petFriendly",
    "searchPriceWithCharges",
    "discountedOnly",
)
SURFACE_KEYS = (
    ("balconyFrom", "balconySurfaceFrom"),
    ("balconyTo", "balconySurfaceTo"),
    ("loggiaFrom", "loggiaSurfaceFrom"),
    ("loggiaTo", "loggiaSurfaceTo"),
    ("cellarFrom", "cellarSurfaceFrom"),
    ("cellarTo", "cellarSurfaceTo"),
    ("terraceFrom", "terraceSurfaceFrom"),
    ("terraceTo", "terraceSurfaceTo"),
    ("frontGardenFrom", "frontGardenSurfaceFrom"),
    ("frontGardenTo", "frontGardenSurfaceTo"),
)
INT_KEYS = (
    ("priceFrom", "priceFrom"),
    ("priceTo", "priceTo"),
    ("surfaceFrom", "surfaceFrom"),
    ("surfaceTo", "surfaceTo"),
    ("annuityFrom", "annuityFrom"),
    ("annuityTo", "annuityTo"),
    ("availableFrom", "availableFrom"),
    ("locationRadius", "locationRadius"),
    ("neighborhood", "locationRadius"),
)
LIST_FIELDS = """
      id
      uri
      estateType
      offerType
      disposition
      surface
      price
      charges
      currency
      originalPrice
      isDiscounted
      isNew
      reserved
      imageAltText(locale: CS)
      address(locale: CS)
      gps { lat lng }
      mainImage { url(filter: RECORD_MAIN) }
      publicImages(limit: 50) { url(filter: RECORD_MAIN) }
      balcony
      loggia
      cellar
      terrace
      garage
      lift
      parking
      petFriendly
      barrierFree
      roommate
      shortTerm
      construction
      frontGarden
"""

DETAIL_FIELDS = (
    LIST_FIELDS
    + """
      description
      floor
      condition
      ownership
      equipped
      availableFrom
      balconySurface
      loggiaSurface
      cellarSurface
      terraceSurface
      visitCount
"""
)

BR_OFFERS = {"PRONAJEM": "Pronájem", "PRODEJ": "Prodej"}
BR_ESTATES = {
    "BYT": "Byt",
    "DUM": "Dům",
    "POZEMEK": "Pozemek",
    "GARAZ": "Garáž",
    "KANCELAR": "Kancelář",
    "NEBYTOVY_PROSTOR": "Nebytový prostor",
    "REKREACNI_OBJEKT": "Rekreační objekt",
}
BR_CONDITIONS = {
    "AFTER_PARTIAL_RECONSTRUCTION": "Po částečné rekonstrukci",
    "AFTER_RECONSTRUCTION": "Po rekonstrukci",
    "BAD": "Špatný",
    "BEFORE_RECONSTRUCTION": "Před rekonstrukcí",
    "CONSTRUCTION": "Ve výstavbě",
    "DEMOLITION": "K demolici",
    "GOOD": "Dobrý",
    "IN_RECONSTRUCTION": "V rekonstrukci",
    "NEW": "Novostavba",
    "PROJECT": "Projekt",
    "VERY_GOOD": "Velmi dobrý",
}
BR_OWNERSHIP = {"DRUZSTEVNI": "Družstevní", "OBECNI": "Obecní", "OSOBNI": "Osobní", "OSTATNI": "Ostatní"}
BR_CONSTRUCTIONS = {
    "BRICK": "Cihlová",
    "PANEL": "Panelová",
    "MIXED": "Smíšená",
    "PREFAB": "Montovaná",
    "SKELET": "Skeletová",
    "STONE": "Kamenná",
}
BR_EQUIPPED = {"CASTECNE": "Částečně vybavený", "NEVYBAVENY": "Nevybavený", "VYBAVENY": "Vybavený"}
BR_FLOORS = {
    "BASEMENT": "Suterén",
    "LOW_GROUND": "Snížené přízemí",
    "GROUND": "Přízemí",
    "RAISED_GROUND": "Zvýšené přízemí",
    "FIRST": "1. patro",
    "SECOND": "2. patro",
    "THIRD": "3. patro",
    "FOURTH": "4. patro",
    "FIFTH": "5. patro",
    "SIXTH": "6. patro",
    "HIGH": "Vyšší patro",
}


def format_disposition(raw: str | None) -> str:
    if not raw:
        return ""
    body = raw.replace("DISP_", "")
    if raw in {"GARSONIERA"}:
        return "garsoniéra"
    if raw in {"OSTATNI"}:
        return "ostatní"
    if body.endswith("_KK"):
        return f"{body[:-3].replace('_', '+')}+kk"
    if body == "ATYP":
        return "atypický"
    return body.replace("_", "+")


def gql_list(values: list[str]) -> str:
    return "[" + ", ".join(values) + "]"


class BezrealitkyClient:
    def __init__(self, search_url: str) -> None:
        from app.scrape_http import scrape_timeout

        self.search_url = search_url
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, max_redirects=3, timeout=scrape_timeout()
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _args(self, page: int) -> str:
        query = parse_qs(urlsplit(self.search_url).query, keep_blank_values=False)
        parts = [
            f"limit: {PAGE_SIZE}",
            f"offset: {max(0, (page - 1) * PAGE_SIZE)}",
        ]
        offers = [item for item in query.get("offerType", ["PRONAJEM"]) if item in OFFERS]
        estates = [item for item in query.get("estateType", ["BYT"]) if item in ESTATES]
        disps = [item for item in query.get("disposition", []) if item in DISPOSITIONS]
        regions = [item for item in query.get("regionOsmIds", []) if OSM_RE.match(item)]
        ownership = [item for item in query.get("ownership", []) if item in OWNERSHIP]
        conditions = [item for item in query.get("condition", []) if item in CONDITIONS]
        constructions = [item for item in query.get("construction", []) if item in CONSTRUCTIONS]
        equipped = [item for item in query.get("equipped", []) if item in EQUIPPED]
        order = query.get("order", ["TIMEORDER_DESC"])[0]
        if order not in ORDERS:
            order = "TIMEORDER_DESC"
        if offers:
            parts.append(f"offerType: {gql_list(offers)}")
        if estates:
            parts.append(f"estateType: {gql_list(estates)}")
        if disps:
            parts.append(f"disposition: {gql_list(disps)}")
        if regions:
            quoted = ", ".join(f'"{item}"' for item in regions)
            parts.append(f"regionOsmIds: [{quoted}]")
        if ownership:
            parts.append(f"ownership: {gql_list(ownership)}")
        if conditions:
            parts.append(f"condition: {gql_list(conditions)}")
        if constructions:
            parts.append(f"construction: {gql_list(constructions)}")
        if equipped:
            parts.append(f"equipped: {gql_list(equipped)}")
        parts.append(f"order: {order}")
        used_ints: set[str] = set()
        for key, gql in INT_KEYS + SURFACE_KEYS:
            if gql in used_ints:
                continue
            raw = (query.get(key) or [None])[0]
            if raw and str(raw).lstrip("-").isdigit():
                parts.append(f"{gql}: {int(raw)}")
                used_ints.add(gql)
        for key in BOOL_KEYS:
            if (query.get(key) or [""])[0].lower() in {"1", "true"}:
                parts.append(f"{key}: true")
        if (query.get("balcony") or [""])[0].lower() in {"1", "true"}:
            parts.append("balcony: true")
        roommate = (query.get("roommate") or [""])[0].lower()
        if roommate == "true":
            parts.append("roommate: true")
        elif roommate == "false":
            parts.append("roommate: false")
        transfers = {(item or "").lower() for item in query.get("transferToPersonalOwnership", [])}
        if transfers == {"true"}:
            parts.append("transferToPersonalOwnership: true")
        elif transfers == {"false"}:
            parts.append("transferToPersonalOwnership: false")
        if (query.get("includeImports") or ["true"])[0].lower() == "false":
            parts.append("includeImports: false")
        if (query.get("includeShortTerm") or ["true"])[0].lower() == "false":
            parts.append("includeShortTerm: false")
        currency = (query.get("currency") or ["CZK"])[0]
        if currency in CURRENCIES and currency != "CZK":
            parts.append(f"currency: {currency}")
        advert_ids = [item for item in query.get("id", []) + query.get("ids", []) if item.isdigit()]
        if advert_ids:
            parts.append("ids: [" + ", ".join(advert_ids) + "]")
        raw_points = (query.get("boundaryPoints") or [None])[0]
        if raw_points:
            polygon = _gql_points(unquote(raw_points))
            if polygon:
                parts.append(f"boundaryPoints: {polygon}")
        return ", ".join(parts)

    async def _graphql(self, query: str) -> dict[str, Any]:
        from app.scrape_http import request_with_log
        from app.scrape_timing import note, note_httpx

        response = await request_with_log(
            self._client,
            "POST",
            GRAPHQL_URL,
            portal="bezrealitky",
            json={"query": query},
        )
        note_httpx(response)
        if response.status_code == 403:
            raise RuntimeError("Bezrealitky GraphQL 403")
        response.raise_for_status()
        parse_started = time.monotonic()
        payload = response.json()
        note(parse_ms=(time.monotonic() - parse_started) * 1000.0)
        if payload.get("errors") and not payload.get("data"):
            raise RuntimeError(payload["errors"][0].get("message") or "Bezrealitky GraphQL error")
        return payload.get("data") or {}

    async def fetch_page(self, page: int = 1, newest: bool = True) -> tuple[list[Listing], int]:
        data = await self._graphql(
            f"query {{ listAdverts({self._args(page)}) {{ totalCount list {{ {LIST_FIELDS} }} }} }}"
        )
        block = data.get("listAdverts") or {}
        listings = [self._parse(item) for item in block.get("list") or [] if item]
        return [item for item in listings if item], int(block.get("totalCount") or 0)

    async def fetch_pages(self, pages: int | None, newest: bool = True) -> tuple[list[Listing], int]:
        listings: list[Listing] = []
        seen: set[int] = set()
        total = 0
        page = 1
        while True:
            if pages is not None and page > pages:
                break
            batch, total = await self.fetch_page(page, newest=newest)
            if not batch:
                break
            for item in batch:
                if item.id not in seen:
                    seen.add(item.id)
                    listings.append(item)
            if total and len(listings) >= total:
                break
            page += 1
            await asyncio.sleep(0.12)
        return listings, total

    async def fetch_all(self, newest: bool = True, max_pages: int | None = None) -> tuple[list[Listing], int]:
        listings, total = await self.fetch_pages(max_pages, newest=newest)
        return listings, total

    async def fetch_detail(self, listing: Listing) -> Listing:
        from app.sreality import ListingGone

        data = await self._graphql(
            f"""
            query {{
              advert(id: "{listing.id}") {{
                {DETAIL_FIELDS}
              }}
            }}
            """
        )
        raw = data.get("advert") if data else None
        if not raw:
            raise ListingGone(listing.url)
        parsed = self._parse(raw)
        if not parsed:
            return listing
        from app.places import refine_listing_location_async

        await refine_listing_location_async(parsed)
        if parsed.views is None:
            parsed.views = listing.views
        if not parsed.description:
            parsed.description = listing.description
        if listing.extras and not parsed.extras:
            parsed.extras = listing.extras
        return parsed

    def _parse(self, raw: dict[str, Any]) -> Listing | None:
        listing_id = raw.get("id")
        if listing_id is None:
            return None
        disposition = format_disposition(raw.get("disposition"))
        area = raw.get("surface")
        try:
            area_m2 = int(area) if area is not None else None
        except (TypeError, ValueError):
            area_m2 = None
        price = raw.get("price")
        try:
            price_czk = int(price) if price is not None else None
        except (TypeError, ValueError):
            price_czk = None
        unit = "měsíc" if raw.get("offerType") == "PRONAJEM" else "ks"
        price_label = format_price(price_czk, unit)
        charges = raw.get("charges")
        try:
            charges_n = int(charges) if charges else 0
        except (TypeError, ValueError):
            charges_n = 0
        if charges_n:
            extra = f"{charges_n:,}".replace(",", " ")
            price_label = f"{price_label} (+{extra} Kč)"
        old_price = raw.get("originalPrice")
        try:
            old_price_czk = int(old_price) if old_price is not None else None
        except (TypeError, ValueError):
            old_price_czk = None
        if old_price_czk == price_czk:
            old_price_czk = None
        name = (raw.get("imageAltText") or "").strip()
        if not name:
            bits = [raw.get("offerType") == "PRONAJEM" and "Pronájem" or "Prodej", "bytu" if raw.get("estateType") == "BYT" else "nemovitosti"]
            if disposition:
                bits.append(disposition)
            if area_m2:
                bits.append(f"{area_m2} m²")
            name = " ".join(str(bit) for bit in bits if bit)
        uri = raw.get("uri") or str(listing_id)
        gps = raw.get("gps") or {}
        photos = []
        main = ((raw.get("mainImage") or {}).get("url") or "").strip()
        if main:
            photos.append(main)
        for item in raw.get("publicImages") or []:
            url = ((item or {}).get("url") or "").strip()
            if url and url not in photos:
                photos.append(url)
        image = photos[0] if photos else None
        views = raw.get("visitCount")
        try:
            views_n = int(views) if views is not None else None
        except (TypeError, ValueError):
            views_n = None
        text = (raw.get("description") or "").replace("\xa0", " ").strip()
        return Listing(
            id=int(listing_id),
            name=name,
            price_czk=price_czk,
            price_label=price_label,
            disposition=disposition,
            area_m2=area_m2,
            locality=(raw.get("address") or "").strip(),
            url=f"{SITE}/nemovitosti-byty-domy/{uri}",
            image_url=image,
            photos=photos,
            lat=gps.get("lat"),
            lon=gps.get("lng"),
            views=views_n,
            old_price_czk=old_price_czk,
            description=text or None,
            extras=extras_from_bezrealitky(raw),
        )


def extras_from_bezrealitky(raw: dict[str, Any]) -> dict[str, Any]:
    flags: list[str] = []
    specs: list[dict[str, str]] = []
    amenity_areas = {
        "balcony": ("balconySurface", "Balkon", "balcony"),
        "loggia": ("loggiaSurface", "Lodžie", "loggia"),
        "cellar": ("cellarSurface", "Sklep", "cellar"),
        "terrace": ("terraceSurface", "Terasa", "terrace"),
    }
    for key, (area_key, label, flag) in amenity_areas.items():
        area = raw.get(area_key)
        present = raw.get(key) or area
        if not present:
            continue
        flags.append(flag)
        specs.append({"label": label, "value": f"{area} m²" if area else "ano"})
    if raw.get("garage"):
        flags.append("garage")
        specs.append({"label": "Garáž", "value": "ano"})
    if raw.get("lift"):
        flags.append("lift")
        specs.append({"label": "Výtah", "value": "ano"})
    if raw.get("parking"):
        flags.append("parking")
        specs.append({"label": "Parkování", "value": "ano"})
    if raw.get("petFriendly") is True:
        flags.append("pets")
        specs.append({"label": "Mazlíčci", "value": "povolení"})
    elif raw.get("petFriendly") is False:
        specs.append({"label": "Mazlíčci", "value": "ne"})
    if raw.get("roommate") is True:
        flags.append("roommate")
        specs.append({"label": "Spolubydlení", "value": "ano"})
    if raw.get("shortTerm") is True:
        flags.append("short_term")
        specs.append({"label": "Krátkodobý pronájem", "value": "ano"})
    construction = BR_CONSTRUCTIONS.get(raw.get("construction") or "", None)
    if construction:
        specs.append({"label": "Konstrukce", "value": construction})
    garden = raw.get("frontGarden")
    if garden:
        flags.append("garden")
        specs.append({"label": "Zahrada", "value": f"{garden} m²" if isinstance(garden, (int, float)) and garden not in (0, True, False) else "ano"})
    if raw.get("isDiscounted"):
        flags.append("discounted")
    if raw.get("barrierFree"):
        flags.append("barrier_free")
        specs.append({"label": "Bezbariérový", "value": "ano"})
    floor = BR_FLOORS.get(raw.get("floor") or "", raw.get("floor"))
    if floor:
        specs.append({"label": "Podlaží", "value": str(floor)})
    ownership = BR_OWNERSHIP.get(raw.get("ownership") or "", None)
    if ownership:
        specs.append({"label": "Vlastnictví", "value": ownership})
    condition = BR_CONDITIONS.get(raw.get("condition") or "", None)
    if condition:
        specs.append({"label": "Stav", "value": condition})
    equipped = BR_EQUIPPED.get(raw.get("equipped") or "", None)
    if equipped:
        specs.append({"label": "Vybavení", "value": equipped})
    charges = raw.get("charges")
    try:
        charges_n = int(charges) if charges else None
    except (TypeError, ValueError):
        charges_n = None
    if charges_n:
        specs.append({"label": "Poplatky", "value": f"{charges_n:,} Kč".replace(",", " ")})
    available = raw.get("availableFrom")
    if available:
        try:
            from datetime import datetime, timezone

            stamp = int(available)
            if stamp > 10_000_000_000:
                stamp //= 1000
            when = datetime.fromtimestamp(stamp, timezone.utc)
            specs.append({"label": "Volné od", "value": f"{when.day}. {when.month}. {when.year}"})
        except (TypeError, ValueError, OSError):
            pass
    return {
        "offer": BR_OFFERS.get(raw.get("offerType") or "", None),
        "estate": BR_ESTATES.get(raw.get("estateType") or "", None),
        "flags": flags,
        "specs": specs,
        "charges_czk": charges_n,
    }


def default_search_url() -> str:
    from app.bezrealitky_url import default_search_url as build_default

    return build_default()


def _gql_points(raw: str) -> str | None:
    try:
        points = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(points, list) or len(points) < 3:
        return None
    chunks = []
    for point in points:
        if not isinstance(point, dict):
            continue
        lat, lng = point.get("lat"), point.get("lng")
        if lat is None or lng is None:
            continue
        try:
            chunks.append(f"{{lat: {float(lat)}, lng: {float(lng)}}}")
        except (TypeError, ValueError):
            continue
    if len(chunks) < 3:
        return None
    return "[" + ", ".join(chunks) + "]"
