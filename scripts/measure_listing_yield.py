#!/usr/bin/env python3
"""Live listing-yield probe for UlovDomov + M&M. Worker-path only; not used by /hry*."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.block_page import classify_block
from app.mmreality import MmrealityClient
from app.scrape_proxy import redacted, url_for as scrape_proxy_url_for
from app.ulovdomov import UlovdomovClient, needs_hydrate, reset_ulov_caches
from app import ulov_hydrate


def _sample(listings):
    return [
        {
            "id": item.id,
            "price_czk": item.price_czk,
            "image": bool(item.image_url),
            "locality": item.locality,
            "name": item.name,
        }
        for item in listings[:3]
    ]


async def _hydrate(client: UlovdomovClient, listings, *, concurrency: int, deadline: float) -> dict:
    pending = [item for item in listings if needs_hydrate(item)]
    started = time.perf_counter()
    result = await client.hydrate_listings(
        pending,
        concurrency=concurrency,
        delay_sec=0.12,
        deadline_sec=deadline,
        fail_fast=True,
    )
    return {**result.as_dict(), "candidates": len(pending), "ms": round((time.perf_counter() - started) * 1000, 1), "sample": _sample(result.listings)}


async def measure_ulov() -> dict:
    reset_ulov_caches()
    rent = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    sale = UlovdomovClient("https://www.ulovdomov.cz/prodej/byty")
    rent_houses = UlovdomovClient("https://www.ulovdomov.cz/pronajem/domy")
    sale_houses = UlovdomovClient("https://www.ulovdomov.cz/prodej/domy")
    started = time.perf_counter()
    try:
        listings, total = await rent.fetch_page(1)
        rent_ms = (time.perf_counter() - started) * 1000
        sales, sale_total = await sale.fetch_page(1)
        t_houses = time.perf_counter()
        rent_house_list, rent_house_total = await rent_houses.fetch_page(1)
        sale_house_list, sale_house_total = await sale_houses.fetch_page(1)
        houses = rent_house_list + sale_house_list
        house_ms = (time.perf_counter() - t_houses) * 1000
        mixed = await ulov_hydrate.collect_candidates(type("S", (), {"unpriced_ulov_listings": lambda self, limit=32: []})(), rent, limit=32)
        return {
            "portal": "ulovdomov",
            "rent_page1": len(listings),
            "rent_total": total,
            "sale_page1": len(sales),
            "sale_total": sale_total,
            "rent_houses_page1": len(rent_house_list),
            "rent_houses_total": rent_house_total,
            "sale_houses_page1": len(sale_house_list),
            "sale_houses_total": sale_house_total,
            "page1_ms": round(rent_ms, 1),
            "houses_ms": round(house_ms, 1),
            "sample": [{"id": item.id, "url": item.url, "name": item.name, "price_czk": item.price_czk} for item in listings[:3]],
            "hydrate": {
                "rent20": await _hydrate(rent, listings[:20], concurrency=6, deadline=18),
                "sale20": await _hydrate(sale, sales[:20], concurrency=6, deadline=18),
                "houses": await _hydrate(rent, houses, concurrency=6, deadline=18),
                "mixed32": await _hydrate(rent, mixed, concurrency=6, deadline=18),
            },
        }
    finally:
        await rent.aclose()
        await sale.aclose()
        await rent_houses.aclose()
        await sale_houses.aclose()
        reset_ulov_caches()


async def measure_mm() -> dict:
    url = "https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem&typ-nemovitosti=byt&razeni=nejnovejsi"
    client = MmrealityClient(url)
    started = time.perf_counter()
    try:
        try:
            listings, total = await client.fetch_page(1)
            error = None
        except Exception as exc:
            listings, total, error = [], 0, str(exc)[:200]
        ms = (time.perf_counter() - started) * 1000
        raw = await client._client.get(url)
        signal = classify_block(raw.status_code, raw.text, raw.headers)
        proxy = scrape_proxy_url_for("mmreality") or client._proxy_url
        return {
            "portal": "mmreality",
            "listings": len(listings),
            "total": total,
            "error": error,
            "http_status": raw.status_code,
            "cf_kind": getattr(signal, "kind", None),
            "cf_detail": getattr(signal, "detail", None),
            "proxy": redacted(proxy) if proxy else "",
            "page1_ms": round(ms, 1),
        }
    finally:
        await client.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    report = {
        "ulovdomov": await measure_ulov(),
        "mmreality": await measure_mm(),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
