"""Worker-only UlovDomov v2/offer/detail hydrate.

Newest sitemap cards have URL/locality/disposition but no price or photo.
This pass fills those fields for games/map. InstantSiteASGI / SCRAPE_ROLE=web
never import or run this module on a request path.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from app import config
from app.block_page import PortalBlocked
from app.sreality import Listing
from app.store import _listing_from_catalog_dict
from app.ulovdomov import UlovdomovClient, HydrateResult, needs_hydrate


def allowed() -> bool:
    """True only on scrape worker / local all. Never InstantSiteASGI web."""
    if not config.SCRAPE_ULOV_HYDRATE:
        return False
    return config.SCRAPE_ROLE != "web"


def _search_url() -> str:
    return "https://www.ulovdomov.cz/pronajem/byty"


async def _sitemap_candidates(client: UlovdomovClient, *, limit: int, seen: set[int]) -> list[Listing]:
    cap = max(1, int(limit or 1))
    rows = await client._load_sitemap_rows()
    rent = [item for item in rows if item[1] == "pronajem" and item[2] not in seen]
    sale = [item for item in rows if item[1] != "pronajem" and item[2] not in seen]
    listings: list[Listing] = []
    for url, offer, listing_id, slug in rent + sale:
        listing = client.listing_from_sitemap_url(url, offer, slug, listing_id)
        if listing is None or not needs_hydrate(listing):
            continue
        listings.append(listing)
        if len(listings) >= cap:
            break
    return listings


def candidates_from_store(store: Any, limit: int) -> list[Listing]:
    rows = store.unpriced_ulov_listings(limit=limit)
    listings: list[Listing] = []
    for row in rows:
        listing = _listing_from_catalog_dict(row)
        if listing.id and needs_hydrate(listing):
            listings.append(listing)
    return listings


async def collect_candidates(store: Any, client: UlovdomovClient, *, limit: int) -> list[Listing]:
    listings = candidates_from_store(store, limit)
    if listings:
        return listings[:limit]
    # Fresh catalog: take newest sitemap stubs (rent first). Never invent cards.
    return await _sitemap_candidates(client, limit=limit, seen=set())


async def hydrate_batch(client: UlovdomovClient, listings: list[Listing]) -> HydrateResult:
    return await client.hydrate_listings(
        listings,
        concurrency=config.SCRAPE_ULOV_HYDRATE_CONCURRENCY,
        delay_sec=config.SCRAPE_ULOV_HYDRATE_DELAY_SEC,
        deadline_sec=config.SCRAPE_ULOV_HYDRATE_DEADLINE_SEC,
        fail_fast=True,
    )


async def run_tick(hub: Any) -> dict[str, Any]:
    if not allowed():
        return {"skipped": "web-or-disabled"}
    cooldown = getattr(getattr(hub, "_scrape_limiter", None), "cooldown", None)
    remaining = cooldown.remaining("ulovdomov") if cooldown is not None else 0.0
    if remaining > 0:
        return {"skipped": "cooldown", "cooldown_sec": round(remaining, 1)}
    limit = config.SCRAPE_ULOV_HYDRATE_BATCH
    client = hub.client_for(_search_url())
    started = time.monotonic()
    listings = await collect_candidates(hub.store, client, limit=limit)
    if not listings:
        return {"attempted": 0, "priced": 0, "imaged": 0, "ms": 0}
    result = await hydrate_batch(client, listings)
    if result.blocked is not None and cooldown is not None:
        cooldown.note_block(result.blocked)
    if result.listings:
        written, _note = await hub._try_catalog_upsert(
            result.listings,
            kind="refresh",
            timeout_sec=2.0,
            write_deadline_sec=8.0,
        )
        result_stats = result.as_dict()
        result_stats["written"] = written or {"deferred": True}
    else:
        result_stats = result.as_dict()
        result_stats["written"] = {"n": 0}
    for listing_id in result.gone_ids:
        try:
            await hub._job_db(hub.store.mark_catalog_listing_gone_id, listing_id, portal="ulovdomov")
        except Exception:
            continue
    result_stats["ms"] = int((time.monotonic() - started) * 1000)
    result_stats["candidates"] = len(listings)
    return result_stats


async def loop(hub: Any) -> None:
    if not allowed():
        return
    await asyncio.sleep(8)
    while getattr(hub, "running", False):
        try:
            report = await run_tick(hub)
            if report.get("attempted") or report.get("skipped"):
                print(
                    f"ulov_hydrate attempted={report.get('attempted', 0)} "
                    f"priced={report.get('priced', 0)} imaged={report.get('imaged', 0)} "
                    f"aborted={report.get('aborted')} skipped={report.get('skipped')} "
                    f"ms={report.get('ms', 0)}",
                    flush=True,
                )
            try:
                await hub._job_db(hub.store.set_meta, "ulov_hydrate_last", json.dumps(report, ensure_ascii=False))
            except Exception:
                pass
        except asyncio.CancelledError:
            raise
        except PortalBlocked as exc:
            cooldown = getattr(getattr(hub, "_scrape_limiter", None), "cooldown", None)
            if cooldown is not None:
                cooldown.note_block(exc)
            hub.last_error = f"ulov-hydrate: {exc}"
        except Exception as exc:
            hub.last_error = f"ulov-hydrate: {exc}"
        try:
            await asyncio.sleep(float(config.SCRAPE_ULOV_HYDRATE_LOOP_SEC))
        except asyncio.CancelledError:
            raise
