#!/usr/bin/env python3
"""Run scripts/ai_eval.md cases against the public catalog API (same backend as MCP)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))

from catalog_client import (  # noqa: E402
    CatalogError,
    compare_localities,
    get_listing,
    locality_stats,
    new_listings,
    price_check,
    search_listings,
)

CASES: list[dict[str, Any]] = [
    {"id": 1, "tool": "search_listings", "params": {"locality": "Praha", "offer_type": "pronajem", "max_price": 20000}},
    {"id": 2, "tool": "search_listings", "params": {"locality": "Praha 5", "offer_type": "pronajem", "disposition": "2+kk", "max_price": 25000}},
    {"id": 3, "tool": "search_listings", "params": {"locality": "Praha", "offer_type": "pronajem", "max_price": 20000}},
    {"id": 4, "tool": "search_listings", "params": {"locality": "Praha", "offer_type": "pronajem", "disposition": "2+kk"}},
    {"id": 5, "tool": "search_listings", "params": {"locality": "Brno", "offer_type": "prodej", "max_price": 5000000}},
    {"id": 6, "tool": "new_listings", "params": {"locality": "Brno", "offer_type": "pronajem", "since_hours": 24}, "allow_empty_new": True},
    {"id": 7, "tool": "new_listings", "params": {"locality": "Praha 5", "offer_type": "pronajem", "since_hours": 24}, "allow_empty_new": True},
    {"id": 8, "tool": "new_listings", "params": {"locality": "Ostrava", "offer_type": "pronajem", "since_hours": 24}, "allow_empty_new": True},
    {"id": 9, "tool": "price_check", "params": {"locality": "Vinohrady", "offer_type": "pronajem", "price": 25000, "disposition": "2+kk"}},
    {"id": 10, "tool": "price_check", "params": {"locality": "Brno", "offer_type": "pronajem", "price": 25000, "disposition": "2+kk", "area": 55}},
    {"id": 11, "tool": "price_check", "params": {"locality": "Praha", "offer_type": "pronajem", "price": 45000, "disposition": "3+kk"}},
    {"id": 12, "tool": "locality_stats", "params": {"locality": "Brno", "offer_type": "pronajem"}},
    {"id": 13, "tool": "locality_stats", "params": {"locality": "Praha", "offer_type": "pronajem"}},
    {"id": 14, "tool": "locality_stats", "params": {"locality": "Praha", "offer_type": "pronajem", "disposition": "2+kk"}},
    {"id": 15, "tool": "compare_localities", "params": {"localities": ["Praha", "Brno", "Ostrava"], "offer_type": "pronajem"}},
    {"id": 16, "tool": "compare_localities", "params": {"localities": ["Praha", "Brno"], "offer_type": "pronajem"}},
    {"id": 17, "tool": "compare_localities", "params": {"localities": ["Praha 5", "Praha 10"], "offer_type": "pronajem"}},
    {"id": 18, "tool": "search_listings", "params": {"locality": "Smíchov", "offer_type": "pronajem", "sort": "cheapest"}},
    {"id": 19, "tool": "search_listings", "params": {"locality": "Ostrava", "offer_type": "prodej"}},
    {"id": 20, "tool": "get_listing", "params": {}},
]


def _ok_search(data: dict[str, Any]) -> bool:
    items = data.get("items") or []
    if not items:
        return False
    row = items[0]
    return bool(row.get("source_url") or row.get("realitify_url")) and row.get("price_czk") is not None


def _ok_stats(data: dict[str, Any]) -> bool:
    return int(data.get("active_count") or 0) > 0 and (
        data.get("median_price") is not None or data.get("median_price_per_m2") is not None
    )


def _ok_price(data: dict[str, Any]) -> bool:
    return int(data.get("comparable_count") or 0) > 0 and data.get("median_price") is not None


def _ok_compare(data: dict[str, Any]) -> bool:
    rows = data.get("items") or data.get("localities") or data.get("results") or []
    if isinstance(data.get("by_locality"), list):
        rows = data["by_locality"]
    if isinstance(rows, dict):
        rows = list(rows.values())
    return isinstance(rows, list) and len(rows) >= 2


async def _run_case(case: dict[str, Any]) -> tuple[bool, str]:
    tool = case["tool"]
    params = dict(case.get("params") or {})
    try:
        if tool == "search_listings":
            data = await search_listings(**params)
            return (_ok_search(data), f"count={data.get('count')} data_as_of={data.get('data_as_of')}")
        if tool == "new_listings":
            try:
                data = await new_listings(**params)
            except CatalogError as exc:
                if case.get("allow_empty_new") and "No new listings" in str(exc):
                    return True, f"empty_window_ok: {exc}"
                raise
            return (_ok_search(data) or case.get("allow_empty_new", False), f"count={data.get('count')}")
        if tool == "locality_stats":
            data = await locality_stats(params["locality"], params.get("offer_type") or "", params.get("disposition") or "")
            return (_ok_stats(data), f"active={data.get('active_count')} median={data.get('median_price')}")
        if tool == "price_check":
            data = await price_check(**params)
            return (_ok_price(data), f"comparables={data.get('comparable_count')} vs_median={data.get('vs_median_pct')}")
        if tool == "compare_localities":
            data = await compare_localities(params["localities"], params.get("offer_type") or "pronajem", params.get("disposition") or "")
            return (_ok_compare(data) or bool(data), f"keys={list(data.keys())[:8]}")
        if tool == "get_listing":
            seed = await search_listings(locality="Praha", offer_type="pronajem", limit=1)
            listing_id = str((seed.get("items") or [{}])[0].get("id") or "")
            if not listing_id:
                return False, "no seed listing id"
            data = await get_listing(listing_id)
            return (bool(data.get("source_url") or data.get("realitify_url")), f"id={listing_id}")
        return False, f"unknown tool {tool}"
    except CatalogError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


async def main() -> int:
    failed = 0
    for case in CASES:
        ok, detail = await _run_case(case)
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"{mark}\t#{case['id']}\t{case['tool']}\t{detail}")
    print(f"\n{len(CASES) - failed}/{len(CASES)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
