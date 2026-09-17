#!/usr/bin/env python3
"""Trace SQL statements for catalog upsert (audit: 15 queries / listing)."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT_DIR = Path(__file__).resolve().parent / "out"


def _sample_listings(store, limit: int):
    from app.sreality import listing_from_dict

    listings = []
    with sqlite3.connect(str(store.path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM catalog_listings WHERE lat IS NOT NULL AND lon IS NOT NULL LIMIT ?",
            (limit * 2,),
        ).fetchall()
    for row in rows:
        try:
            listings.append(listing_from_dict(dict(row)))
        except Exception:
            continue
        if len(listings) >= limit:
            break
    return listings


def run(n: int = 30) -> dict:
    from app import config
    from app.store import Store

    store = Store(config.DB_PATH)
    listings = _sample_listings(store, n)
    if not listings:
        payload = {
            "listings": 0,
            "elapsed_ms": 0.0,
            "ms_per_listing": None,
            "queries": 0,
            "queries_per_listing": None,
            "stats": {},
            "sql_verbs": {},
            "sql_sample": [],
            "skipped": "no catalog listings to trace",
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "hotpaths.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return payload

    queries: list[str] = []
    conn = store.connect()
    conn.set_trace_callback(lambda sql: queries.append(sql))
    t0 = time.perf_counter()
    try:
        conn.execute("BEGIN")
        stats = store.upsert_catalog_listings_batch(listings, kind="refresh", fast=True, conn=conn)
        conn.rollback()
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    per = elapsed_ms / max(1, len(listings))
    sql_kinds = [item.strip().split()[0].upper() if item.strip() else "" for item in queries]
    payload = {
        "listings": len(listings),
        "elapsed_ms": round(elapsed_ms, 2),
        "ms_per_listing": round(per, 3),
        "queries": len(queries),
        "queries_per_listing": round(len(queries) / max(1, len(listings)), 2),
        "stats": stats,
        "sql_verbs": {verb: sql_kinds.count(verb) for verb in sorted(set(sql_kinds))},
        "sql_sample": queries[:24],
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "hotpaths.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=30)
    args = parser.parse_args()
    payload = run(args.n)
    print(
        f"upsert {payload['listings']} listings  {payload['ms_per_listing']} ms/ea  "
        f"{payload['queries_per_listing']} SQL/ea  total_sql={payload['queries']}"
    )


if __name__ == "__main__":
    main()
