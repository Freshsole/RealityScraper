#!/usr/bin/env python3
"""Sweep SCRAPE_BATCH_COMMIT / Hub write-chunk vs catalog JSON p95 + listing yield.

Seeds the same 15k fat listings fixture as measure_catalog_read.py, then times
catalog/search/pins under a NewDiscovery-shaped writer (lock-scoped Hub chunks)
and reports listings/sec for a 1500-row refresh tick (one minute of healthy
page-1 cards). Does not hit InstantSiteASGI /hry* or a residential proxy.

Leave default SCRAPE_BATCH_COMMIT=500 unless a shorter size clearly wins p95
without starving the 12s NewDiscovery write deadline.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import catalog_write_chunk_size
from app.sreality import Listing
from app.store import Store

_SCRIPT_DIR = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "measure_catalog_read", _SCRIPT_DIR / "measure_catalog_read.py"
)
assert _spec and _spec.loader
_catalog_read = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_catalog_read)

_seed = _catalog_read._seed
_listing = _catalog_read._listing
_flush = _catalog_read._flush
_time_calls = _catalog_read._time_calls
_pct = _catalog_read._pct
_summary = _catalog_read._summary
_plans = _catalog_read._plans


def _hub_upsert(store: Store, listings: list[Listing], chunk: int) -> dict[str, int]:
    """Mirror Hub._catalog_upsert: one Store connection per lock-scoped chunk."""
    totals = {"n": 0, "new": 0, "updated": 0, "same": 0}
    for offset in range(0, len(listings), chunk):
        part = store.upsert_catalog_listings_batch(
            listings[offset : offset + chunk],
            kind="refresh",
            commit_every=chunk,
            fast=True,
        )
        for key in totals:
            totals[key] += int(part.get(key) or 0)
    return totals


def _print_block(title: str, samples: dict[str, list[float]]) -> None:
    print(title)
    for name, values in samples.items():
        print(f"  {name:10} {_summary(values)}")


def _writer_loop(
    store: Store,
    *,
    n_rows: int,
    blob: int,
    chunk: int,
    tick: int,
    stop: threading.Event,
    written: list[int],
) -> None:
    cycle = 0
    while not stop.is_set():
        start = (cycle * tick) % max(1, n_rows - tick)
        batch = [_listing(start + k, blob="y" * blob) for k in range(tick)]
        part = _hub_upsert(store, batch, chunk)
        written[0] += int(part.get("n") or 0)
        cycle += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=15_000)
    parser.add_argument("--blob", type=int, default=2500)
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--tick", type=int, default=400)
    parser.add_argument("--yield-n", type=int, default=1500)
    parser.add_argument("--db", type=Path, default=Path("/tmp/scrape-commit-latency.sqlite"))
    parser.add_argument(
        "--chunks",
        default="50,100,250,500",
        help="Hub write-chunk sizes to sweep (comma-separated)",
    )
    args = parser.parse_args()
    chunks = [max(50, int(part.strip())) for part in args.chunks.split(",") if part.strip()]
    tick = max(50, min(args.n, int(args.tick)))
    yield_n = max(50, min(args.n, int(args.yield_n)))

    for suffix in ("", "-wal", "-shm"):
        path = Path(str(args.db) + suffix) if suffix else args.db
        if path.exists():
            path.unlink()

    t0 = time.perf_counter()
    store = Store(args.db)
    _seed(store, args.n, args.blob)
    print(f"seeded {args.n} rows blob={args.blob}B in {(time.perf_counter() - t0) * 1000:.0f}ms")
    print(
        "defaults: SCRAPE_BATCH_COMMIT=500 Store commit_every; "
        f"SCRAPE_WRITE_CHUNK live Hub chunk={catalog_write_chunk_size(500, 100)}"
    )
    _plans(store)

    quiet_miss = _time_calls(store, args.samples, flush=True)
    _print_block("QUIET uncached (no writer)", quiet_miss)

    # Previous-PR harness: 24-row refresh, commit_every=500 (batch ends first).
    stop = threading.Event()
    written = [0]

    def pr51_writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_listing(400 + (n + k) % 80, blob="y" * args.blob) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=500, fast=True)
            written[0] += 24
            n += 1

    thread = threading.Thread(target=pr51_writer, name="rf-job-sim", daemon=True)
    thread.start()
    time.sleep(0.08)
    try:
        pr51_miss = _time_calls(store, args.samples, flush=True)
    finally:
        stop.set()
        thread.join(timeout=12)
    _print_block("WRITER pr51-harness batch=24 commit_every=500 uncached", pr51_miss)

    rows: list[dict[str, float | int | str]] = []
    for chunk in chunks:
        stop = threading.Event()
        written = [0]
        thread = threading.Thread(
            target=_writer_loop,
            kwargs={
                "store": store,
                "n_rows": args.n,
                "blob": args.blob,
                "chunk": chunk,
                "tick": tick,
                "stop": stop,
                "written": written,
            },
            name=f"nd-chunk-{chunk}",
            daemon=True,
        )
        thread.start()
        time.sleep(0.08)
        started = time.perf_counter()
        try:
            writer_miss = _time_calls(store, args.samples, flush=True)
        finally:
            stop.set()
            thread.join(timeout=20)
        overlap_s = max(0.001, time.perf_counter() - started)
        _print_block(f"WRITER NewDiscovery hub-chunk={chunk} tick={tick} uncached", writer_miss)

        yield_batch = [_listing(k, blob="y" * args.blob) for k in range(yield_n)]
        y0 = time.perf_counter()
        part = _hub_upsert(store, yield_batch, chunk)
        yield_s = max(0.001, time.perf_counter() - y0)
        lps = part["n"] / yield_s
        print(
            f"  yield {yield_n} refresh hub-chunk={chunk}: "
            f"{yield_s * 1000:.0f}ms {lps:.0f} listings/s "
            f"n={part['n']} new={part['new']} updated={part['updated']} same={part['same']}"
        )
        deadline_ok = yield_s <= 12.0
        print(f"  12s NewDiscovery write deadline: {'ok' if deadline_ok else 'STARVE'}")
        rows.append(
            {
                "chunk": chunk,
                "catalog_p95": _pct(writer_miss["catalog"], 0.95),
                "search_p95": _pct(writer_miss["search"], 0.95),
                "pins_p95": _pct(writer_miss["pins"], 0.95),
                "pins_tight_p95": _pct(writer_miss["pins_tight"], 0.95),
                "yield_ms": yield_s * 1000,
                "listings_per_s": lps,
                "overlap_listings": written[0],
                "overlap_s": overlap_s,
            }
        )

    print("\nSUMMARY writer p95 (ms) vs yield")
    print(
        f"{'chunk':>8} {'catalog':>10} {'search':>10} {'pins':>10} {'tight':>10} "
        f"{'yield_ms':>10} {'list/s':>10}"
    )
    print(
        f"{'quiet':>8} {_pct(quiet_miss['catalog'], 0.95):10.1f} "
        f"{_pct(quiet_miss['search'], 0.95):10.1f} "
        f"{_pct(quiet_miss['pins'], 0.95):10.1f} "
        f"{_pct(quiet_miss['pins_tight'], 0.95):10.1f} {'-':>10} {'-':>10}"
    )
    print(
        f"{'pr51/24':>8} {_pct(pr51_miss['catalog'], 0.95):10.1f} "
        f"{_pct(pr51_miss['search'], 0.95):10.1f} "
        f"{_pct(pr51_miss['pins'], 0.95):10.1f} "
        f"{_pct(pr51_miss['pins_tight'], 0.95):10.1f} {'-':>10} {'-':>10}"
    )
    for row in rows:
        print(
            f"{int(row['chunk']):8d} {row['catalog_p95']:10.1f} {row['search_p95']:10.1f} "
            f"{row['pins_p95']:10.1f} {row['pins_tight_p95']:10.1f} "
            f"{row['yield_ms']:10.0f} {row['listings_per_s']:10.0f}"
        )

    live = catalog_write_chunk_size()
    best = min(rows, key=lambda row: max(row["catalog_p95"], row["search_p95"], row["pins_p95"]))
    live_row = next((row for row in rows if int(row["chunk"]) == live), None)
    print(
        f"\nlive Hub chunk={live} (SCRAPE_WRITE_CHUNK). "
        f"lowest max(catalog,search,pins) p95 at chunk={int(best['chunk'])}."
    )
    if live_row is not None:
        delta = max(best["catalog_p95"], best["search_p95"], best["pins_p95"]) - max(
            live_row["catalog_p95"], live_row["search_p95"], live_row["pins_p95"]
        )
        print(
            f"best vs live max-p95 delta={delta:.1f}ms "
            "(change default only if this is a clear win without yield starve)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
