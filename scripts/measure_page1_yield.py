#!/usr/bin/env python3
"""Live page-1 yield + field-fill probe for healthy discovery portals.

Worker-path `fetch_page` only; not used by InstantSiteASGI /hry*.
M&M is excluded (opt-in SCRAPE_HTTP_PROXY on the worker).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog_sync import HEALTHY_DISCOVERY_PORTALS, extra_portal_recent_shards, sreality_recent_shards
from app.sources import client_for


def _kind(shard: dict[str, str]) -> str:
    key = shard.get("shard_key") or ""
    url = (shard.get("search_url") or "").lower()
    if (
        key.endswith(":pozemky")
        or ":pozemek:" in key.casefold()
        or "/pozem" in url
        or "estatetype=pozemek" in url
        or "pozemky.html" in url
    ):
        return "pozemky"
    if (
        key.endswith(":domy")
        or ":dum:" in key.casefold()
        or "/domy" in url
        or "/dum/" in url
        or "estatetype=dum" in url
        or "domy-a-vily" in url
        or "domy-k-" in url
        or "domy-na-" in url
        or "rodinne-domy" in url
    ):
        return "houses"
    return "byty"


def _offer(shard: dict[str, str]) -> str:
    key = (shard.get("shard_key") or "").lower()
    url = (shard.get("search_url") or "").lower()
    if "prodej" in key or "prodej" in url or "prodam" in url or "na-prodej" in url:
        return "sale"
    return "rent"


def selected_shards(portals: set[str]) -> list[dict[str, str]]:
    """One rent/sale apartment shard per portal, plus house/plot shards when present."""
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in extra_portal_recent_shards() + sreality_recent_shards():
        portal = item.get("portal") or ""
        if portal not in portals:
            continue
        offer = _offer(item)
        kind = _kind(item)
        ident = (portal, offer, kind)
        if ident in seen:
            continue
        seen.add(ident)
        rows.append({**item, "label": f"{portal}:{offer}:{kind}"})
    return rows


def _fill(listings, pred) -> int:
    return sum(1 for item in listings if pred(item))


async def measure_one(shard: dict[str, str], timeout: float) -> dict:
    client = client_for(shard["search_url"])
    started = time.perf_counter()
    error = None
    listings = []
    total = 0
    try:
        listings, total = await asyncio.wait_for(client.fetch_page(1, newest=True), timeout=timeout)
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
    n = len(listings)
    sample = [
        {
            "id": item.id,
            "name": item.name,
            "price_czk": item.price_czk,
            "locality": item.locality,
            "url": item.url,
            "lat": item.lat,
            "lon": item.lon,
            "estate": (item.extras or {}).get("estate"),
        }
        for item in listings[:2]
    ]
    estates = Counter(str((item.extras or {}).get("estate") or "") for item in listings)
    return {
        "label": shard["label"],
        "portal": shard["portal"],
        "shard_key": shard["shard_key"],
        "search_url": shard["search_url"],
        "page1": n,
        "total": total,
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
        "price": _fill(listings, lambda item: item.price_czk not in (None,)),
        "image": _fill(listings, lambda item: bool(item.image_url)),
        "locality": _fill(listings, lambda item: bool((item.locality or "").strip())),
        "gps": _fill(listings, lambda item: item.lat is not None and item.lon is not None),
        "estate": dict(estates),
        "sample": sample,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    parser.add_argument("--portals", default="", help="Comma-separated portal ids (default: healthy set)")
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()
    wanted = {item.strip().lower() for item in args.portals.split(",") if item.strip()}
    portals = wanted or set(HEALTHY_DISCOVERY_PORTALS)
    shards = selected_shards(portals)
    rows = [await measure_one(item, args.timeout) for item in shards]
    report = {
        "timeout_sec": args.timeout,
        "shards": len(rows),
        "page1": sum(item["page1"] for item in rows),
        "rows": rows,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
