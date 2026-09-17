#!/usr/bin/env python3
"""Compare NewDiscovery tick yield: held-gate vs page-1-across (healthy portals).

Does not hit InstantSiteASGI /hry*. Optional --live probes page 1 of healthy portals.
M&M is excluded from the live healthy set (opt-in SCRAPE_HTTP_PROXY on the worker).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog_sync import HEALTHY_DISCOVERY_PORTALS, extra_portal_recent_shards, sreality_recent_shards
from app.scrape_engine import AdaptiveLimiter, ScrapeEngine
from app.sreality import Listing


def _listing(n: int) -> Listing:
    return Listing(
        id=n,
        name=f"Byt {n}",
        price_czk=10000,
        price_label="10 000 Kč",
        disposition="2+kk",
        area_m2=40,
        locality="Praha",
        url=f"https://www.sreality.cz/detail/{n}",
        image_url=None,
    )


def _healthy_shards() -> list[dict[str, str]]:
    return [
        item
        for item in extra_portal_recent_shards() + sreality_recent_shards()
        if (item.get("portal") or "") in HEALTHY_DISCOVERY_PORTALS
    ]


async def simulate_tick(*, page1_across: bool, deadline_sec: float, max_pages: int) -> dict:
    """Synthetic healthy-portal minute: extras slower than Sreality JSON."""
    limiter = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
    engine = ScrapeEngine(limiter)
    shards = _healthy_shards()
    calls: list[tuple[str, int]] = []

    class Client:
        def __init__(self, portal: str, shard_id: int) -> None:
            self.portal = portal
            self.shard_id = shard_id

        async def fetch_page(self, page: int, newest: bool = True):
            calls.append((self.portal, page))
            delay = 0.04 if self.portal == "sreality" else 0.30
            await asyncio.sleep(delay)
            batch = [_listing(self.shard_id * 100 + page * 10 + i) for i in range(20)]
            return batch, 80

    clients = {
        shard["search_url"]: Client(shard["portal"], index) for index, shard in enumerate(shards)
    }
    started = time.perf_counter()
    results = await engine.fetch_shards(
        shards,
        client_factory=lambda url: clients[url],
        max_pages=max_pages,
        deadline_sec=deadline_sec,
        page1_across=page1_across,
        min_shard_sec=0,
    )
    ms = (time.perf_counter() - started) * 1000
    page1_portals = {portal for portal, page in calls if page == 1}
    return {
        "page1_across": page1_across,
        "shards": len(shards),
        "page1_ok": sum(1 for _portal, page in calls if page == 1),
        "page1_portals": sorted(page1_portals),
        "pages_ok": sum(item.pages_ok for item in results),
        "listings": sum(len(item.listings) for item in results),
        "deferred_shards": sum(1 for item in results if item.deferred_pages),
        "ms": round(ms, 1),
    }


async def measure_live(portals: set[str]) -> list[dict]:
    from app.sources import client_for
    from app.catalog_sync import extra_portal_recent_shards, sreality_recent_shards

    shards = [
        item
        for item in extra_portal_recent_shards() + sreality_recent_shards()[:1]
        if (item.get("portal") or "") in portals
    ]
    # One newest shard per healthy portal (rent).
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in shards:
        portal = item["portal"]
        if portal in seen:
            continue
        seen.add(portal)
        unique.append(item)

    rows: list[dict] = []
    for item in unique:
        client = client_for(item["search_url"])
        started = time.perf_counter()
        error = None
        listings = []
        total = 0
        try:
            listings, total = await asyncio.wait_for(client.fetch_page(1, newest=True), timeout=12.0)
        except TimeoutError:
            error = "timeout"
        except Exception as exc:
            error = str(exc)[:200] or exc.__class__.__name__
        finally:
            closer = getattr(client, "aclose", None)
            if closer is not None:
                try:
                    await asyncio.wait_for(closer(), timeout=2.0)
                except Exception:
                    pass
        rows.append(
            {
                "portal": item["portal"],
                "page1": len(listings),
                "total": total,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "error": error,
            }
        )
    return rows


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--portals", default="", help="Comma-separated live portal ids (default: healthy set)")
    parser.add_argument("--deadline", type=float, default=0.7)
    parser.add_argument("--pages", type=int, default=4)
    args = parser.parse_args()

    before = await simulate_tick(page1_across=False, deadline_sec=args.deadline, max_pages=args.pages)
    after = await simulate_tick(page1_across=True, deadline_sec=args.deadline, max_pages=args.pages)
    report: dict = {
        "deadline_sec": args.deadline,
        "max_pages": args.pages,
        "before_held_gate": before,
        "after_page1_across": after,
        "listing_delta": after["listings"] - before["listings"],
        "page1_delta": after["page1_ok"] - before["page1_ok"],
    }
    if args.live:
        wanted = {item.strip().lower() for item in args.portals.split(",") if item.strip()}
        report["live_page1"] = await measure_live(wanted or set(HEALTHY_DISCOVERY_PORTALS))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
