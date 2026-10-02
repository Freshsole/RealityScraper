"""Public read-only Realitify MCP server (Streamable HTTP on /mcp)."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from enum import Enum
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from catalog_client import (
    CATALOG_BASE_URL,
    CatalogError,
    compare_localities as fetch_compare,
    get_listing as fetch_listing,
    locality_stats as fetch_locality_stats,
    new_listings as fetch_new,
    price_check as fetch_price_check,
    search_listings as fetch_search,
)

RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN") or "60")
PORT = int(os.environ.get("PORT") or "8000")

INSTRUCTIONS = """
Realitify MCP is a read-only connector for Czech real-estate listings (Czech Republic only).
Currency is CZK; floor area is m²; price_per_m2 is CZK/m². Responses include data_as_of (ISO time of newest catalog last_seen), units, source, and source_url.

Six tools — pick one:
1) search_listings — current apartments to rent/buy matching filters (locality, price, disposition). Default use for “hledám byt”, “flat in Prague under 20000”.
2) new_listings — what first appeared in the last N hours (Realitify first_seen). Use for “co je nového”, “new rentals today”. Do not use for general browsing without a time window.
3) get_listing — one listing by id/listing_key from a prior search. Do not guess ids.
4) locality_stats — active count + median/avg price and CZK/m² for a locality. Use for “kolik stojí nájem v Brně”.
5) price_check — is an asking price fair vs comparables (median primary). Use for “je 25000 za 2+kk hodně?”.
6) compare_localities — side-by-side stats for 2+ localities. Use for “Praha vs Brno”.

Do not invent listings or prices. If a locality is unknown, errors include suggestions — retry with a suggested name.
No writes, no monitors, no account actions via this public MCP.
Realitify (realitify.cz) is not affiliated with Realtify or PriceHubble.
""".strip()


class OfferType(str, Enum):
    pronajem = "pronajem"
    prodej = "prodej"


class SortType(str, Enum):
    newest = "newest"
    cheapest = "cheapest"
    best_value = "best_value"


class EstateScope(str, Enum):
    apartment = "apartment"
    any = "any"


def _annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


mcp = FastMCP(name="Realitify", instructions=INSTRUCTIONS)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, limit_per_min: int = RATE_LIMIT_PER_MIN) -> None:
        super().__init__(app)
        self.limit = max(limit_per_min, 1)
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def _client_ip(self, request: Request) -> str:
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
        if request.client and request.client.host:
            return request.client.host[:64]
        return "unknown"

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path in {"/health", "/"}:
            return await call_next(request)
        ip = self._client_ip(request)
        now = time.monotonic()
        window = self._hits[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.limit:
            return JSONResponse(
                {
                    "error": "Rate limit exceeded. Wait about 60 seconds, then retry with fewer calls.",
                },
                status_code=429,
            )
        window.append(now)
        return await call_next(request)


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "catalog_base_url": CATALOG_BASE_URL})


@mcp.tool(annotations=_annotations("Search listings"))
async def search_listings(
    locality: Annotated[
        str,
        "Czech city/district label, e.g. Praha, Prague 5, Smíchov, Brno. Allowed: Czech localities present in catalog.",
    ] = "",
    offer_type: Annotated[
        OfferType | None,
        "Allowed: pronajem (rent) | prodej (sale). Example: pronajem.",
    ] = None,
    max_price: Annotated[int | None, "Maximum price in CZK. Example: 20000 for rent."] = None,
    min_price: Annotated[int | None, "Minimum price in CZK. Example: 15000."] = None,
    disposition: Annotated[
        str,
        "Layout filter. Examples: 2+kk, 2kk, 3+1, dvoupokojový. Comma-separated allowed.",
    ] = "",
    min_area: Annotated[int | None, "Minimum floor area in m². Example: 45."] = None,
    limit: Annotated[int, "Max results 1–20. Example: 10."] = 10,
    sort: Annotated[
        SortType,
        "Allowed: newest | cheapest | best_value (lowest CZK/m² vs locality). Example: newest.",
    ] = SortType.newest,
    estate_scope: Annotated[
        EstateScope,
        "Allowed: apartment (default flats only) | any (include non-residential when explicitly asked).",
    ] = EstateScope.apartment,
) -> dict[str, Any]:
    """Use when: user looks for current flats/houses to rent or buy in Czechia (“hledám byt”, “flat in Prague under 20000”).

    Do NOT use when: user asks only for statistics (use locality_stats), fairness of one price (use price_check),
    what is new in last N hours (use new_listings), or details of one known id (use get_listing).

    Examples CZ: „hledám 2+kk v Praze do 20000“; „prodej bytu Brno“.
    Examples EN: \"2-room flat in Prague under 20000 CZK\"; \"apartments for sale in Ostrava\".
    """
    try:
        return await fetch_search(
            locality=locality,
            offer_type=offer_type.value if offer_type else "",
            max_price=max_price,
            min_price=min_price,
            disposition=disposition,
            min_area=min_area,
            limit=limit,
            sort=sort.value if isinstance(sort, SortType) else str(sort or "newest"),
            listing_quality=estate_scope.value if isinstance(estate_scope, EstateScope) else str(estate_scope or "apartment"),
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("New listings"))
async def new_listings(
    locality: Annotated[str, "Czech locality. Example: Praha 5."] = "",
    offer_type: Annotated[OfferType, "Allowed: pronajem | prodej. Default pronajem."] = OfferType.pronajem,
    since_hours: Annotated[int, "Hours back (1–168). Example: 24."] = 24,
    disposition: Annotated[str, "Optional layout, e.g. 2+kk."] = "",
    max_price: Annotated[int | None, "Optional max price CZK. Example: 25000."] = None,
    min_area: Annotated[int | None, "Optional min m². Example: 40."] = None,
    limit: Annotated[int, "Max results 1–20. Example: 10."] = 10,
    estate_scope: Annotated[
        EstateScope,
        "Allowed: apartment | any.",
    ] = EstateScope.apartment,
) -> dict[str, Any]:
    """Use when: user asks what is new / recently published (“co je nového”, “new rentals last 24 hours”).

    Do NOT use when: user wants a general search without a time window (use search_listings),
    or only market averages (use locality_stats). Relies on Realitify first_seen, not portal “updated” alone.
    """
    try:
        return await fetch_new(
            locality=locality,
            offer_type=offer_type.value,
            since_hours=since_hours,
            disposition=disposition,
            max_price=max_price,
            min_area=min_area,
            limit=limit,
            listing_quality=estate_scope.value if isinstance(estate_scope, EstateScope) else str(estate_scope or "apartment"),
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Listing detail"))
async def get_listing(
    listing_id: Annotated[
        str,
        "Numeric id or listing_key from search_listings/new_listings. Example: 123456 or a listing_key string.",
    ],
) -> dict[str, Any]:
    """Use when: user wants details for one listing already returned (“otevři tu první”, “open listing id …”).

    Do NOT use when: user has not searched yet — call search_listings first. Do not invent ids.
    """
    try:
        return await fetch_listing(listing_id)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Locality statistics"))
async def locality_stats(
    locality: Annotated[str, "Czech locality. Example: Brno or Praha."],
    offer_type: Annotated[OfferType | None, "Allowed: pronajem | prodej."] = None,
    disposition: Annotated[str, "Optional layout filter, e.g. 2+kk."] = "",
) -> dict[str, Any]:
    """Use when: user asks how many active listings or median/average rent/sale price (incl. CZK/m²) in a locality.

    Do NOT use when: user wants concrete listing cards (use search_listings) or fairness of one asking price (use price_check).
    """
    try:
        return await fetch_locality_stats(
            locality,
            offer_type.value if offer_type else "",
            disposition,
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Price check"))
async def price_check(
    locality: Annotated[str, "Czech locality for comparables. Example: Vinohrady or Brno."],
    offer_type: Annotated[OfferType, "Allowed: pronajem | prodej."],
    price: Annotated[int, "Asked price in CZK. Example: 25000."],
    disposition: Annotated[str, "Layout, e.g. 2+kk."] = "",
    area: Annotated[int | None, "Floor area m² for tighter comparables. Example: 55."] = None,
) -> dict[str, Any]:
    """Use when: user asks if a price is fair/high/low vs the market (“je 25000 za 2+kk hodně?”).

    Do NOT use when: user wants to browse listings (search_listings) or only locality averages without an asking price (locality_stats).
    Returns median (primary), avg, percentile, vs_median_pct, vs_avg_pct, comparable_count.
    """
    try:
        return await fetch_price_check(
            locality=locality,
            offer_type=offer_type.value,
            price=price,
            disposition=disposition,
            area=area,
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Compare localities"))
async def compare_localities(
    localities: Annotated[
        list[str],
        "Two or more Czech localities. Example: [\"Praha\", \"Brno\"] or [\"Praha 5\", \"Praha 8\"].",
    ],
    offer_type: Annotated[OfferType, "Allowed: pronajem | prodej. Default pronajem."] = OfferType.pronajem,
    disposition: Annotated[str, "Optional layout filter, e.g. 2+kk."] = "",
) -> dict[str, Any]:
    """Use when: user wants side-by-side prices/counts for 2+ localities (“Praha vs Brno”).

    Do NOT use when: only one locality (use locality_stats) or user wants listing cards (search_listings).
    """
    try:
        return await fetch_compare(localities, offer_type.value, disposition)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


def create_app():
    app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    app.add_middleware(RateLimitMiddleware, limit_per_min=RATE_LIMIT_PER_MIN)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT)
