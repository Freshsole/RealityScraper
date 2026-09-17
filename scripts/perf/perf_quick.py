#!/usr/bin/env python3
"""Sub-minute regression check: catalog cProfile + traced upsert + HTTP bench."""
from __future__ import annotations

import cProfile
import io
import json
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "out"
BASELINE = HERE / "baseline.json"


def _load_baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def _delta(current: float | None, baseline: float | None) -> str:
    if current is None or baseline in (None, 0):
        return ""
    pct = (current - baseline) / baseline * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


def profile_catalog() -> dict:
    from app import config
    from app.store import Store

    store = Store(config.DB_PATH)
    store._facets_cache = None
    filters = {"sort": "newest", "limit": 36, "offset": 0, "include_pins": "0", "offer": "pronajem", "estate": "byt"}
    profiler = cProfile.Profile()
    profiler.enable()
    t0 = time.perf_counter()
    result = store.catalog(filters)
    elapsed = (time.perf_counter() - t0) * 1000
    profiler.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("tottime")
    stats.print_stats(8)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "cprofile_catalog.txt").write_text(stream.getvalue(), encoding="utf-8")
    return {
        "catalog_fn_ms": round(elapsed, 2),
        "catalog_total": result.get("total"),
        "catalog_items": len(result.get("items") or []),
        "catalog_pins": len(result.get("pins") or []),
    }


def main() -> None:
    started = time.perf_counter()
    from scripts.perf import hotpaths, http_bench, sql_timings

    catalog = profile_catalog()
    upsert = hotpaths.run(30)
    try:
        sql = sql_timings.run()
    except Exception as exc:
        sql = {"queries": []}
        print(f"SQL timings skipped: {exc}")
    try:
        http_rows = http_bench.run(quick=True)
    except Exception as exc:
        http_rows = []
        print(f"HTTP bench skipped: {exc}")

    catalog_http = next((row for row in http_rows if "include_pins=1" in (row.get("path") or "")), None)
    catalog_http0 = next((row for row in http_rows if "include_pins=0" in (row.get("path") or "")), None)
    last = {
        "elapsed_s": round(time.perf_counter() - started, 2),
        "catalog_fn_ms": catalog["catalog_fn_ms"],
        "upsert_ms_per_listing": upsert["ms_per_listing"],
        "upsert_queries_per_listing": upsert["queries_per_listing"],
        "catalog_http_ms_median": (catalog_http or {}).get("ms_median"),
        "catalog_no_pins_http_ms_median": (catalog_http0 or {}).get("ms_median"),
        "sql": {item["label"]: {"ms": item["ms_median"], "scan": item["scan"], "plan": item["plan"]} for item in sql["queries"]},
        "http": http_rows,
        "catalog": catalog,
        "upsert": {k: v for k, v in upsert.items() if k != "sql_sample"},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "last.json").write_text(json.dumps(last, indent=2), encoding="utf-8")

    base = _load_baseline()
    print("make perf-quick  vs baseline")
    rows = [
        ("upsert ms/listing", last["upsert_ms_per_listing"], base["upsert_ms_per_listing"]),
        ("upsert SQL/listing", last["upsert_queries_per_listing"], base["upsert_queries_per_listing"]),
        ("catalog() ms", last["catalog_fn_ms"], base["catalog_fn_ms"]),
        ("HTTP /api/catalog pins=1 ms", last["catalog_http_ms_median"], base["catalog_http_ms_median"]),
        ("HTTP /api/catalog pins=0 ms", last["catalog_no_pins_http_ms_median"], base["catalog_no_pins_http_ms_median"]),
    ]
    for label, current, baseline in rows:
        print(f"  {label:32} {current}  (baseline {baseline}  {_delta(current, baseline)})")
    print(f"  wall {last['elapsed_s']}s")
    if last["elapsed_s"] > 60:
        print("WARNING: perf-quick exceeded 60s")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
