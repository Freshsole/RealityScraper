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


class OfferType(str, Enum):
    pronajem = "pronajem"
    prodej = "prodej"


class SortType(str, Enum):
    newest = "newest"
    cheapest = "cheapest"
    best_value = "best_value"


def _annotations(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


mcp = FastMCP(
    name="Realitify",
    instructions=(
        "Realitify is a read-only Czech real-estate catalog aggregator. "
        "Use these tools when the user asks about flats/apartments/houses to rent or buy "
        "in the Czech Republic, current listings, prices, or availability in a city/district. "
        "Example queries: 'hledám byt', 'pronájem Praha', '2+kk Brno', 'flat in Prague under 20000'. "
        "Every listing includes source_url (portal) and realitify_url. No broker phones or names."
    ),
)


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
        # Health checks must not burn the rate budget and must stay simple for load balancers.
        if request.url.path in {"/health", "/"}:
            return await call_next(request)
        ip = self._client_ip(request)
        now = time.monotonic()
        window = self._hits[ip]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.limit:
            return JSONResponse(
                {"error": "Rate limit exceeded. Try again in a minute."},
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
        "City or district in Czechia, e.g. Praha, Prague 5, Smíchov, Brno, Ostrava",
    ] = "",
    offer_type: Annotated[
        OfferType | None,
        "pronajem = rent, prodej = sale. Required for precise results when known.",
    ] = None,
    max_price: Annotated[int | None, "Maximum price in CZK"] = None,
    min_price: Annotated[int | None, "Minimum price in CZK"] = None,
    disposition: Annotated[
        str,
        "Layout: 2+kk, 2kk, dvoupokojový, 3+1, etc. Comma-separated allowed",
    ] = "",
    min_area: Annotated[int | None, "Minimum floor area in m²"] = None,
    limit: Annotated[int, "Max results (1-20)"] = 10,
    sort: Annotated[
        SortType,
        "newest | cheapest | best_value (lowest CZK/m² vs locality average)",
    ] = SortType.newest,
) -> dict[str, Any]:
    """Use when the user is looking for a flat/apartment/house to rent or buy in the Czech Republic,
    asks about current listings, prices, or what's available in a city/district.

    Examples: "hledám byt v Praze", "pronájem 2+kk Praha 5 do 20000", "flat in Prague under 20000 CZK",
    "prodej bytu Brno", "what's for rent in Ostrava".

    Returns compact listing cards with price, price/m², price_vs_locality_pct, source_url, realitify_url.
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
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("New listings"))
async def new_listings(
    locality: Annotated[str, "City or district, e.g. Praha, Brno"] = "",
    offer_type: Annotated[OfferType, "pronajem or prodej"] = OfferType.pronajem,
    since_hours: Annotated[int, "How many hours back to look (1-168)"] = 24,
    disposition: Annotated[str, "Optional layout filter, e.g. 2+kk"] = "",
    max_price: Annotated[int | None, "Optional max price CZK"] = None,
    min_area: Annotated[int | None, "Optional min m²"] = None,
    limit: Annotated[int, "Max results (1-20)"] = 10,
) -> dict[str, Any]:
    """Use when the user asks what is new / recently published on the Czech market
    ("co je nového", "nové byty dnes", "new listings in Prague last 24 hours").

    Realitify tracks first_seen timestamps across portals — this is not available from a single portal search.
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
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Listing detail"))
async def get_listing(
    listing_id: Annotated[str, "Numeric id or listing_key from search/new_listings results"],
) -> dict[str, Any]:
    """Use when the user wants details for one specific listing already found
    ("otevři tu první", "detail nabídky", "open listing id …").
    """
    try:
        return await fetch_listing(listing_id)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Locality statistics"))
async def locality_stats(
    locality: Annotated[str, "City or district, e.g. Praha, Brno, Praha 5"],
    offer_type: Annotated[OfferType | None, "pronajem or prodej"] = None,
    disposition: Annotated[str, "Optional layout filter, e.g. 2+kk"] = "",
) -> dict[str, Any]:
    """Use when the user asks for median/average rent or sale price, price per m² (median primary), or how many
    active listings are in a locality ("kolik stojí pronájem v Brně", "average rent Prague").
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
    locality: Annotated[str, "City or district for comparison"],
    offer_type: Annotated[OfferType, "pronajem or prodej"],
    price: Annotated[int, "Asked price in CZK"],
    disposition: Annotated[str, "Layout, e.g. 2+kk"] = "",
    area: Annotated[int | None, "Floor area m² for tighter comparables"] = None,
) -> dict[str, Any]:
    """Use when the user asks if a price is fair / overpriced versus the current market
    ("je 25000 za 2+kk v Praze 5 hodně?", "is this rent expensive?").

    Returns median (primary), avg, percentile, vs_median_pct, vs_avg_pct, and comparable_count.
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
        "Two or more localities, e.g. ['Praha', 'Brno'] or ['Praha 5', 'Praha 8']",
    ],
    offer_type: Annotated[OfferType, "pronajem or prodej"] = OfferType.pronajem,
    disposition: Annotated[str, "Optional layout filter"] = "",
) -> dict[str, Any]:
    """Use when the user wants to compare prices or listing counts across cities/districts
    ("Praha vs Brno nájem", "compare Praha 5 and Praha 10").
    """
    try:
        return await fetch_compare(localities, offer_type.value, disposition)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


def create_app():
    # Stateless Streamable HTTP avoids session-init 400s behind proxies / Claude connectors.
    app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)
    app.add_middleware(RateLimitMiddleware, limit_per_min=RATE_LIMIT_PER_MIN)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT)
