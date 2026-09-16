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
from app.ulovdomov import UlovdomovClient, reset_ulov_caches
from app.mmreality import MmrealityClient


async def measure_ulov() -> dict:
    reset_ulov_caches()
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    started = time.perf_counter()
    try:
        listings, total = await client.fetch_page(1)
        ms = (time.perf_counter() - started) * 1000
        sale = UlovdomovClient("https://www.ulovdomov.cz/prodej/byty")
        try:
            sales, sale_total = await sale.fetch_page(1)
        finally:
            await sale.aclose()
        return {
            "portal": "ulovdomov",
            "rent_page1": len(listings),
            "rent_total": total,
            "sale_page1": len(sales),
            "sale_total": sale_total,
            "page1_ms": round(ms, 1),
            "sample": [{"id": item.id, "url": item.url, "name": item.name} for item in listings[:3]],
        }
    finally:
        await client.aclose()
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
        return {
            "portal": "mmreality",
            "listings": len(listings),
            "total": total,
            "error": error,
            "http_status": raw.status_code,
            "cf_kind": getattr(signal, "kind", None),
            "cf_detail": getattr(signal, "detail", None),
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
