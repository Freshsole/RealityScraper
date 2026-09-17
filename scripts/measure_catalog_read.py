#!/usr/bin/env python3
"""p50/p95 for catalog/list/map/watch JSON under a scrape writer.

Seeds a fat listings table (extras blobs), then times Store hot paths
quietly and while upsert_catalog_listings_batch holds the writer.
Also prints EXPLAIN QUERY PLAN for the default catalog/pin shapes.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.sreality import Listing
from app.store import Store


def _listing(i: int, *, blob: str) -> Listing:
    disposition = ("1+kk", "2+kk", "3+kk", "4+kk")[i % 4]
    price = 12_000 + (i % 40) * 450
    return Listing(
        id=80_000 + i,
        name=f"Pronájem bytu {disposition}",
        price_czk=price,
        price_label=f"{price} Kč/měsíc",
        disposition=disposition,
        area_m2=32 + (i % 9) * 6,
        locality=f"Praha {(i % 8) + 1}",
        url=f"https://www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{80_000 + i}",
        image_url=f"https://img.example/cat-{i}.jpg",
        lat=50.08 + (i % 40) * 0.001,
        lon=14.42 + (i % 40) * 0.001,
        extras={"offer": "Pronájem", "estate": "Byt", "portal": "sreality", "blob": blob},
        description=blob,
    )


def _pct(samples: list[float], p: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def _summary(samples: list[float]) -> str:
    if not samples:
        return "n=0"
    return (
        f"n={len(samples)} p50={_pct(samples, 0.50):.2f}ms "
        f"p95={_pct(samples, 0.95):.2f}ms max={max(samples):.2f}ms"
    )


def _flush(store: Store) -> None:
    store._hot_json_cache.clear()
    store._facets_cache = None
    store._facets_at = 0.0
    store._city_pin_cache.clear()
    store._gone_fast_cache = None
    store._new_today_cache = None
    store._landing_preview_cache = None
    store._listing_user_status_cache.clear()


def _seed(store: Store, n: int, blob_bytes: int) -> None:
    blob = "x" * blob_bytes
    now = datetime.now(timezone.utc).isoformat()
    extras = json.dumps(
        {"offer": "Pronájem", "estate": "Byt", "portal": "sreality", "blob": blob},
        ensure_ascii=False,
    )
    rows = []
    for i in range(n):
        disposition = ("1+kk", "2+kk", "3+kk", "4+kk")[i % 4]
        price = 12_000 + (i % 40) * 450
        url = f"https://www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{80_000 + i}"
        rows.append(
            (
                f"www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{80_000 + i}",
                80_000 + i,
                f"Pronájem bytu {disposition}",
                price,
                f"{price} Kč/měsíc",
                disposition,
                32 + (i % 9) * 6,
                f"Praha {(i % 8) + 1}",
                url,
                f"https://img.example/cat-{i}.jpg",
                now,
                extras,
                now,
                blob,
                50.08 + (i % 40) * 0.001,
                14.42 + (i % 40) * 0.001,
                1 if i % 5 == 0 else 0,
            )
        )
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO catalog_listings(
                listing_key, id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, extras, last_seen, gone, portal,
                description, lat, lon, canonical_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'sreality', ?, ?, ?, ?)
            """,
            [(*row[:13], row[13], row[14], row[15], row[0]) for row in rows],
        )
        conn.executemany(
            """
            INSERT INTO listings(
                id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, extras, last_seen, gone,
                description, lat, lon, listing_key, canonical_key, notified
            ) VALUES (?, '__catalog__', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                    row[15],
                    row[0],
                    row[0],
                    row[16],
                )
                for row in rows
            ],
        )
        conn.execute(
            """
            INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
            VALUES ('m1', 'Praha', 'https://www.sreality.cz/hledani/pronajem/byty', '', 'default', 1, 1, ?)
            """,
            (now,),
        )
        conn.commit()


def _plans(store: Store) -> None:
    queries = {
        "catalog-newest": """
            SELECT listings.* FROM listings
            ORDER BY listings.first_seen DESC LIMIT 96
        """,
        "catalog-q-praha": """
            SELECT listings.* FROM listings
            WHERE listings.name LIKE '%Praha%' OR listings.locality LIKE '%Praha%'
               OR listings.disposition LIKE '%Praha%'
               OR IFNULL(listings.description, '') LIKE '%Praha%'
            ORDER BY listings.first_seen DESC LIMIT 96
        """,
        "pins-bbox": """
            SELECT ROUND(src.lat, 3) AS lat, ROUND(src.lon, 3) AS lon, COUNT(*) AS n
            FROM (
                SELECT listings.lat, listings.lon, listings.listing_key
                FROM listings
                WHERE listings.lat IS NOT NULL AND listings.lon IS NOT NULL
                  AND listings.lat BETWEEN 49.90 AND 50.25
                  AND listings.lon BETWEEN 14.10 AND 14.75
                GROUP BY COALESCE(NULLIF(listings.canonical_key, ''), listings.listing_key)
            ) src
            GROUP BY ROUND(src.lat, 3), ROUND(src.lon, 3)
        """,
        "item-url": """
            SELECT listings.id FROM listings
            WHERE listings.url = ?
            ORDER BY listings.last_seen DESC LIMIT 1
        """,
        "listings-notified": """
            SELECT listings.* FROM listings
            WHERE listings.notified = 1
            ORDER BY listings.first_seen DESC LIMIT 96
        """,
        "watch-counts": """
            SELECT monitor_id, COUNT(*) FROM listings GROUP BY monitor_id
        """,
    }
    url = "https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/80000"
    with store.read() as conn:
        print("EXPLAIN QUERY PLAN")
        for name, sql in queries.items():
            print(f"  {name}")
            params = (url,) if name == "item-url" else ()
            for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params):
                print("   ", tuple(row))
        indexes = sorted(row[1] for row in conn.execute("PRAGMA index_list(listings)"))
        print("  listings indexes:", ", ".join(indexes))


def _time_calls(store: Store, n: int, *, flush: bool) -> dict[str, list[float]]:
    seed_url = "https://www.sreality.cz/detail/pronajem/byt/1+kk/praha/80000"
    samples: dict[str, list[float]] = {
        "catalog": [],
        "search": [],
        "pins": [],
        "listings": [],
        "watch": [],
        "item": [],
        "fresh": [],
    }
    pin_filters = {
        "pins_only": True,
        "south": "49.90",
        "north": "50.25",
        "west": "14.10",
        "east": "14.75",
    }
    for _ in range(n):
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        catalog = store.catalog({"limit": 24, "include_pins": "0"})
        samples["catalog"].append((time.perf_counter() - t0) * 1000)
        assert catalog["items"]
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        search = store.catalog({"q": "Praha", "limit": 24, "include_pins": "0"})
        samples["search"].append((time.perf_counter() - t0) * 1000)
        assert search["items"]
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        pins = store.catalog(pin_filters)
        samples["pins"].append((time.perf_counter() - t0) * 1000)
        assert pins["items"]
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        listings = store.recent_notified(24)
        samples["listings"].append((time.perf_counter() - t0) * 1000)
        assert listings
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        monitors = store.list_monitors()
        samples["watch"].append((time.perf_counter() - t0) * 1000)
        assert monitors
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        item = store.catalog_item("", None, "", seed_url)
        samples["item"].append((time.perf_counter() - t0) * 1000)
        assert item and item.get("url")
        if flush:
            _flush(store)
        t0 = time.perf_counter()
        fresh = store.catalog_freshness()
        samples["fresh"].append((time.perf_counter() - t0) * 1000)
        assert fresh.get("listing_key")
    return samples


def _print_block(title: str, samples: dict[str, list[float]]) -> None:
    print(title)
    for name, values in samples.items():
        print(f"  {name:10} {_summary(values)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=3000)
    parser.add_argument("--blob", type=int, default=2500)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--db", type=Path, default=Path("/tmp/catalog-read-latency.sqlite"))
    args = parser.parse_args()
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(args.db) + suffix) if suffix else args.db
        if path.exists():
            path.unlink()

    t0 = time.perf_counter()
    store = Store(args.db)
    _seed(store, args.n, args.blob)
    print(f"seeded {args.n} rows blob={args.blob}B in {(time.perf_counter() - t0) * 1000:.0f}ms")
    _plans(store)

    quiet_miss = _time_calls(store, args.samples, flush=True)
    quiet_hit = _time_calls(store, args.samples, flush=False)
    _print_block("QUIET uncached", quiet_miss)
    _print_block("QUIET cached", quiet_hit)

    stop = threading.Event()

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_listing(400 + (n + k) % 80, blob="y" * args.blob) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=500, fast=True)
            n += 1

    thread = threading.Thread(target=writer, name="rf-job-sim", daemon=True)
    thread.start()
    time.sleep(0.08)
    try:
        writer_miss = _time_calls(store, args.samples, flush=True)
        writer_hit = _time_calls(store, args.samples, flush=False)
    finally:
        stop.set()
        thread.join(timeout=12)

    _print_block("WRITER uncached", writer_miss)
    _print_block("WRITER cached", writer_hit)

    store.catalog({"limit": 12, "include_pins": "0"})
    store.list_monitors()
    for key, (at, payload) in list(store._hot_json_cache.items()):
        store._hot_json_cache[key] = (at - 5.0, payload)
    locker = sqlite3.connect(store.path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("UPDATE meta SET value = value")
    exclusive: dict[str, list[float]] = {"catalog": [], "watch": []}
    try:
        for _ in range(8):
            t0 = time.perf_counter()
            payload = store.catalog({"limit": 12, "include_pins": "0"})
            exclusive["catalog"].append((time.perf_counter() - t0) * 1000)
            assert payload.get("items") or payload.get("stale")
            t0 = time.perf_counter()
            store.list_monitors()
            exclusive["watch"].append((time.perf_counter() - t0) * 1000)
    finally:
        locker.rollback()
        locker.close()
    _print_block("EXCLUSIVE (stale failover)", exclusive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
