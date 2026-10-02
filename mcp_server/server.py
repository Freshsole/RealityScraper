"""Public read-only Realitify MCP server (Streamable HTTP on /mcp)."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Annotated, Any

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
    get_listing as fetch_listing,
    locality_stats as fetch_locality_stats,
    search_listings as fetch_search,
)

RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN") or "60")
PORT = int(os.environ.get("PORT") or "8000")


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
        "Read-only Czech real-estate listings from Realitify. "
        "Search flats for rent or sale, open listing details, and get locality price stats. "
        "Every listing includes a source portal URL and a Realitify deep link."
    ),
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory per-IP rate limiter."""

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
    locality: Annotated[str, "City, district, or area name, e.g. Praha, Brno, Praha 5"] = "",
    offer_type: Annotated[str, "Listing type: pronajem (rent) or prodej (sale)"] = "",
    max_price: Annotated[int | None, "Maximum price in CZK"] = None,
    min_price: Annotated[int | None, "Minimum price in CZK"] = None,
    disposition: Annotated[str, "Flat layout, e.g. 2+kk, 3+1. Comma-separated for multiple"] = "",
    min_area: Annotated[int | None, "Minimum floor area in square meters"] = None,
    limit: Annotated[int, "Max results to return (1-20)"] = 10,
) -> str:
    """Search active Czech real-estate listings in the Realitify catalog.

    Filters are applied server-side. Returns compact JSON with count, total, and items.
    Each item includes source_url (portal) and realitify_url. No broker phones or names.
    """
    try:
        return await fetch_search(
            locality=locality,
            offer_type=offer_type,
            max_price=max_price,
            min_price=min_price,
            disposition=disposition,
            min_area=min_area,
            limit=limit,
        )
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Listing detail"))
async def get_listing(
    listing_id: Annotated[str, "Listing id (numeric) or listing_key from search results"],
) -> str:
    """Get one listing by id or listing_key from the Realitify catalog (database only).

    Returns compact JSON with title, price, locality, source_url, and realitify_url.
    Personal contact data is never included.
    """
    try:
        return await fetch_listing(listing_id)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


@mcp.tool(annotations=_annotations("Locality statistics"))
async def locality_stats(
    locality: Annotated[str, "City or district name, e.g. Praha, Brno"],
    offer_type: Annotated[str, "Optional: pronajem or prodej"] = "",
) -> str:
    """Get active listing count and average price per m² for a locality.

    Aggregation runs on the Realitify catalog API. Returns compact JSON.
    """
    try:
        return await fetch_locality_stats(locality, offer_type)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


def create_app():
    app = mcp.http_app(path="/mcp", stateless_http=True)
    app.add_middleware(RateLimitMiddleware, limit_per_min=RATE_LIMIT_PER_MIN)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=PORT)
