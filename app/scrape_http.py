"""Shared scrape HTTP timeout + per-attempt logging.

httpx.Timeout(25.0) sets connect, read, write AND pool each to 25s. Those phases
run in sequence, so a hung Cloudflare/TLS handshake plus a hung body easily
becomes ~50s — matching the fetch p95 we measured. Split them.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app import config

SUSPECT_PORTALS = frozenset({"idnes", "bezrealitky", "mmreality", "ulovdomov", "realitycz"})


def scrape_timeout() -> httpx.Timeout:
    connect = float(config.SCRAPE_HTTP_CONNECT_TIMEOUT)
    read = float(config.SCRAPE_HTTP_TIMEOUT)
    return httpx.Timeout(connect=connect, read=read, write=read, pool=min(5.0, connect))


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_attempt(
    *,
    portal: str,
    attempt: int,
    attempts: int,
    phase: str,
    url: str,
    ms: float | None = None,
    status: int | None = None,
    error: str | None = None,
) -> None:
    from app.scrape_timing import enabled, log_row

    portal = (portal or "").strip().lower()
    noisy = portal in SUSPECT_PORTALS or enabled()
    if not noisy:
        return
    print(
        f"scrape attempt portal={portal or '?'} {phase} n={attempt}/{attempts} "
        f"at={_iso()} ms={None if ms is None else round(ms)} status={status} "
        f"err={(error or '')[:120]} url={(url or '')[:90]}",
        flush=True,
    )
    if enabled():
        log_row(
            {
                "kind": "attempt",
                "portal": portal,
                "pipeline": "http",
                "shard_key": phase,
                "page": attempt,
                "fetch_ms": None if ms is None else round(ms, 2),
                "parse_ms": None,
                "upsert_ms": None,
                "listings_count": 0,
                "status_code": status,
                "limiter_at_call": attempts,
                "error": error,
                "deferred": 0,
            }
        )


async def bounded_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Hard wall-clock cap. httpx's own timeout is loop-scheduled and stalls if the loop is busy."""
    import asyncio

    budget = float(config.SCRAPE_HTTP_TIMEOUT)
    try:
        async with asyncio.timeout(budget):
            return await client.request(method, url, **kwargs)
    except TimeoutError as exc:
        if isinstance(exc, httpx.TimeoutException):
            raise
        raise httpx.ReadTimeout(f"wall-clock {budget}s") from exc


async def request_with_log(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    portal: str = "",
    **kwargs: Any,
) -> httpx.Response:
    attempts = max(1, int(config.SCRAPE_HTTP_RETRIES))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        log_attempt(portal=portal, attempt=attempt, attempts=attempts, phase="start", url=url)
        started = time.monotonic()
        try:
            response = await bounded_request(client, method, url, **kwargs)
            log_attempt(
                portal=portal,
                attempt=attempt,
                attempts=attempts,
                phase="end",
                url=url,
                ms=(time.monotonic() - started) * 1000.0,
                status=response.status_code,
            )
            return response
        except Exception as exc:
            last_exc = exc
            log_attempt(
                portal=portal,
                attempt=attempt,
                attempts=attempts,
                phase="error",
                url=url,
                ms=(time.monotonic() - started) * 1000.0,
                error=f"{type(exc).__name__}: {exc}"[:180],
            )
            if attempt >= attempts:
                raise
    raise last_exc or RuntimeError("request_with_log: no attempt")
