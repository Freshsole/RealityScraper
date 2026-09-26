#!/usr/bin/env python3
"""HTTP timings against the already-running local server."""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "out"
BASE = os.getenv("PERF_BASE", "http://127.0.0.1:8080")
QUICK_PATHS = [
    "/api/public/stats",
    "/api/public/gone-fast",
    "/api/catalog?limit=36&offset=0&sort=newest&include_pins=1",
    "/api/catalog?limit=36&offset=0&sort=newest&include_pins=0",
]
FULL_PATHS = [
    "/",
    "/api/public/stats",
    "/api/public/gone-fast",
    "/api/public/games/higher-lower",
    "/api/listings",
    "/api/status",
    "/api/catalog?limit=36&offset=0&sort=newest&include_pins=1",
    "/api/catalog?limit=36&offset=0&sort=newest&include_pins=0",
    "/api/catalog/pins?south=49.1&north=49.3&west=16.5&east=16.7",
]


def bench(path: str, n: int = 3) -> dict:
    times: list[float] = []
    sizes: list[int] = []
    status = None
    err = None
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                response = client.get(BASE + path)
                times.append((time.perf_counter() - t0) * 1000)
                sizes.append(len(response.content))
                status = response.status_code
            except Exception as exc:
                times.append((time.perf_counter() - t0) * 1000)
                err = str(exc)
    return {
        "path": path,
        "n": n,
        "status": status,
        "error": err,
        "ms_min": round(min(times), 1) if times else None,
        "ms_median": round(statistics.median(times), 1) if times else None,
        "ms_mean": round(statistics.mean(times), 1) if times else None,
        "ms_max": round(max(times), 1) if times else None,
        "bytes_median": int(statistics.median(sizes)) if sizes else 0,
        "samples_ms": [round(x, 1) for x in times],
    }


def run(quick: bool = True, n: int | None = None) -> list[dict]:
    paths = QUICK_PATHS if quick else FULL_PATHS
    repeats = n if n is not None else (3 if quick else 5)
    rows = [bench(path, n=repeats) for path in paths]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "http_bench.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    rows = run(quick=not args.full)
    for row in rows:
        print(f"{row['ms_median']:>8} ms  {row['status']}  {row['path']}  err={row['error']}")


if __name__ == "__main__":
    main()
