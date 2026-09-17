#!/usr/bin/env python3
"""Measure scrape throughput against local staging. Default 5 minutes, all 3 pipelines."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent
OUT_DEFAULT = HERE / "scrape_baseline.json"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_cycle(jobs: list[dict]) -> dict:
    by_portal: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        by_portal[str(job.get("portal") or "?")].append(job)
    out: dict[str, Any] = {}
    for portal, rows in sorted(by_portal.items()):
        done = [row for row in rows if row.get("status") == "done" and row.get("started_at") and row.get("finished_at")]
        durations = []
        for row in done:
            try:
                start = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(row["finished_at"]).replace("Z", "+00:00"))
                durations.append((end - start).total_seconds())
            except ValueError:
                continue
        out[portal] = {
            "jobs": len(rows),
            "done": len(done),
            "duration_s_p50": round(sorted(durations)[len(durations) // 2], 1) if durations else None,
            "duration_s_max": round(max(durations), 1) if durations else None,
            "duration_s_sum": round(sum(durations), 1) if durations else None,
        }
    return out


async def _probe_headers() -> dict:
    import httpx

    from app.catalog_sync import recent_shards
    from app.html_listing import BROWSER_HEADERS
    from app.sreality import BROWSER_HEADERS as SRE_HEADERS

    samples: dict[str, dict] = {}
    shards = recent_shards()
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for shard in shards:
            portal = shard.get("portal") or ""
            if portal in seen:
                continue
            seen.add(portal)
            url = shard["search_url"]
            headers = dict(SRE_HEADERS if portal == "sreality" else BROWSER_HEADERS)
            try:
                response = await client.get(url, headers=headers)
                samples[portal] = {
                    "url": url[:120],
                    "status": response.status_code,
                    "etag": bool(response.headers.get("etag") or response.headers.get("ETag")),
                    "last_modified": bool(response.headers.get("last-modified") or response.headers.get("Last-Modified")),
                    "cache_control": response.headers.get("cache-control") or response.headers.get("Cache-Control"),
                }
            except Exception as exc:
                samples[portal] = {"url": url[:120], "error": str(exc)[:180]}
    return samples


async def _live(minutes: float) -> dict:
    from app import config
    from app.catalog_sync import daily_shards
    from app.monitor import Hub
    from app.scrape_timing import drain, summarize_rows, watchdog_sample, watchdog_snapshot
    from app.store import utc_now
    from scripts.perf import stall_dump

    config.SCRAPE_METRICS_DETAIL = True
    from app import portal_health

    portal_health.reset()
    dump_dir = ROOT / "perf_audit" / "loop_stall"
    stall_dump.enable_asyncio_debug(dump_dir / "asyncio.log")
    stacks_path = stall_dump.start(dump_dir, interval=1.0)
    print(f"stall dump pid={os.getpid()} stacks={stacks_path}", flush=True)
    hub = Hub()
    hub.running = True
    started = time.monotonic()
    deadline = started + max(15.0, minutes * 60.0)
    since = utc_now()

    async def watch() -> None:
        while time.monotonic() < deadline and hub.running:
            await watchdog_sample()

    async def monitor() -> None:
        while time.monotonic() < deadline and hub.running:
            tick_started = time.monotonic()
            try:
                await hub.check_due()
            except Exception as exc:
                hub.last_error = f"bench-monitor: {exc}"
            await asyncio.sleep(max(0.25, float(config.SCRAPE_MONITOR_LOOP_SEC) - (time.monotonic() - tick_started)))

    async def discovery() -> None:
        while time.monotonic() < deadline and hub.running:
            tick_started = time.monotonic()
            try:
                await hub._recent_catalog_tick()
            except Exception as exc:
                hub.last_error = f"bench-discovery: {exc}"
            await asyncio.sleep(max(0.5, float(config.SCRAPE_DISCOVERY_LOOP_SEC) - (time.monotonic() - tick_started)))

    async def deep() -> None:
        while time.monotonic() < deadline and hub.running:
            try:
                await hub._deep_catalog_tick()
            except Exception as exc:
                hub.last_error = f"bench-deep: {exc}"
            await asyncio.sleep(float(config.SCRAPE_DEEP_LOOP_SEC))

    print(f"scrape_bench live minutes={minutes} db={config.DB_PATH}", flush=True)
    try:
        await asyncio.gather(watch(), monitor(), discovery(), deep())
    finally:
        stall_dump.stop()
    await hub._flush_scrape_metrics()
    leftover = drain()
    if leftover:
        hub.store.insert_scrape_metrics(leftover)
    seconds = time.monotonic() - started
    rows = hub.store.load_scrape_metrics(since=since)
    summary = summarize_rows(rows, seconds=seconds)
    deep_total = len(daily_shards())
    unique_deep = {
        str(row.get("shard_key") or "")
        for row in rows
        if str(row.get("pipeline") or "") == "deep" and row.get("shard_key")
    }
    unique_deep.discard("")
    shards_per_min = len(unique_deep) / (seconds / 60.0) if seconds else 0
    theoretical_s = deep_total / (float(config.SCRAPE_DEEP_SHARDS_PER_TICK) / float(config.SCRAPE_DEEP_LOOP_SEC))
    measured_s = (deep_total / shards_per_min * 60.0) if shards_per_min else None
    payload = {
        "at": _iso(),
        "mode": "live",
        "minutes": minutes,
        "seconds": round(seconds, 1),
        "db": str(config.DB_PATH),
        "concurrency": config.SCRAPE_CONCURRENCY,
        "concurrency_overrides": dict(config.SCRAPE_CONCURRENCY_OVERRIDES),
        "limiter_limits": hub._scrape_registry.limits(),
        "deep_shards_total": deep_total,
        "deep_shards_per_tick": config.SCRAPE_DEEP_SHARDS_PER_TICK,
        "deep_loop_sec": config.SCRAPE_DEEP_LOOP_SEC,
        "coverage_cycle_s_theoretical": round(theoretical_s, 1),
        "coverage_cycle_h_theoretical": round(theoretical_s / 3600.0, 2),
        "deep_unique_shards_in_window": len(unique_deep),
        "coverage_cycle_s_measured": round(measured_s, 1) if measured_s else None,
        "coverage_cycle_h_measured": round(measured_s / 3600.0, 2) if measured_s else None,
        "watchdog": watchdog_snapshot(),
        "portal_health": portal_health.snapshot(),
        "scrape_jobs": _job_cycle(hub.store.scrape_job_durations()),
        "throughput": summary,
        "last_error": hub.last_error,
    }
    await hub.close()
    return payload


async def _attach(minutes: float) -> dict:
    from app import config
    from app.catalog_sync import daily_shards
    from app.scrape_timing import summarize_rows
    from app.store import Store, utc_now

    store = Store(config.DB_PATH)
    since = utc_now()
    print(f"scrape_bench attach minutes={minutes} db={config.DB_PATH}", flush=True)
    await asyncio.sleep(max(15.0, minutes * 60.0))
    rows = store.load_scrape_metrics(since=since)
    seconds = max(15.0, minutes * 60.0)
    summary = summarize_rows(rows, seconds=seconds)
    deep_total = len(daily_shards())
    unique_deep = {
        str(row.get("shard_key") or "")
        for row in rows
        if str(row.get("pipeline") or "") == "deep" and row.get("shard_key")
    }
    unique_deep.discard("")
    shards_per_min = len(unique_deep) / (seconds / 60.0) if seconds else 0
    theoretical_s = deep_total / (float(config.SCRAPE_DEEP_SHARDS_PER_TICK) / float(config.SCRAPE_DEEP_LOOP_SEC))
    measured_s = (deep_total / shards_per_min * 60.0) if shards_per_min else None
    raw_watch = store.get_meta("scrape_watchdog")
    watchdog = json.loads(raw_watch) if raw_watch else {}
    return {
        "at": _iso(),
        "mode": "attach",
        "minutes": minutes,
        "seconds": round(seconds, 1),
        "db": str(config.DB_PATH),
        "concurrency": config.SCRAPE_CONCURRENCY,
        "deep_shards_total": deep_total,
        "coverage_cycle_s_theoretical": round(theoretical_s, 1),
        "coverage_cycle_h_theoretical": round(theoretical_s / 3600.0, 2),
        "deep_unique_shards_in_window": len(unique_deep),
        "coverage_cycle_s_measured": round(measured_s, 1) if measured_s else None,
        "coverage_cycle_h_measured": round(measured_s / 3600.0, 2) if measured_s else None,
        "watchdog": watchdog,
        "scrape_jobs": _job_cycle(store.scrape_job_durations()),
        "throughput": summary,
    }


def compare(current: dict, baseline: dict) -> dict:
    cur_t = current.get("throughput") or {}
    base_t = baseline.get("throughput") or {}

    def pct(now: float | None, then: float | None) -> float | None:
        if now is None or then in (None, 0):
            return None
        return round((now - then) / then * 100.0, 1)

    portals = {}
    for portal, now in (cur_t.get("portals") or {}).items():
        then = (base_t.get("portals") or {}).get(portal) or {}
        portals[portal] = {
            "pages_per_s": {"now": now.get("pages_per_s"), "base": then.get("pages_per_s"), "delta_pct": pct(now.get("pages_per_s"), then.get("pages_per_s"))},
            "listings_per_s": {"now": now.get("listings_per_s"), "base": then.get("listings_per_s"), "delta_pct": pct(now.get("listings_per_s"), then.get("listings_per_s"))},
            "error_rate": {"now": now.get("error_rate"), "base": then.get("error_rate"), "delta_pct": pct(now.get("error_rate"), then.get("error_rate"))},
            "parse_ms_p95": {"now": now.get("parse_ms_p95"), "base": then.get("parse_ms_p95"), "delta_pct": pct(now.get("parse_ms_p95"), then.get("parse_ms_p95"))},
        }
    return {
        "pages_per_s": {
            "now": cur_t.get("pages_per_s"),
            "base": base_t.get("pages_per_s"),
            "delta_pct": pct(cur_t.get("pages_per_s"), base_t.get("pages_per_s")),
        },
        "listings_per_s": {
            "now": cur_t.get("listings_per_s"),
            "base": base_t.get("listings_per_s"),
            "delta_pct": pct(cur_t.get("listings_per_s"), base_t.get("listings_per_s")),
        },
        "coverage_cycle_h_measured": {
            "now": current.get("coverage_cycle_h_measured"),
            "base": baseline.get("coverage_cycle_h_measured"),
            "delta_pct": pct(current.get("coverage_cycle_h_measured"), baseline.get("coverage_cycle_h_measured")),
        },
        "watchdog_p95_ms": {
            "now": (current.get("watchdog") or {}).get("p95_ms"),
            "base": (baseline.get("watchdog") or {}).get("p95_ms"),
            "delta_pct": pct((current.get("watchdog") or {}).get("p95_ms"), (baseline.get("watchdog") or {}).get("p95_ms")),
        },
        "portals": portals,
    }


async def _amain(args: argparse.Namespace) -> dict:
    if args.attach:
        payload = await _attach(args.minutes)
    else:
        payload = await _live(args.minutes)
    if not args.skip_headers:
        payload["conditional_headers"] = await _probe_headers()
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=5.0)
    parser.add_argument("--attach", action="store_true", help="Read metrics written by an already-running worker")
    parser.add_argument("--skip-headers", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--compare", type=Path, default=None, help="Baseline JSON to diff against")
    args = parser.parse_args()
    payload = asyncio.run(_amain(args))
    compare_path = args.compare
    if compare_path and compare_path.exists():
        payload["vs_baseline"] = compare(payload, json.loads(compare_path.read_text(encoding="utf-8")))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    thru = payload.get("throughput") or {}
    print(
        f"wrote {args.out} pages/s={thru.get('pages_per_s')} listings/s={thru.get('listings_per_s')} "
        f"cycle_h={payload.get('coverage_cycle_h_measured')} watchdog_p95={payload.get('watchdog', {}).get('p95_ms')}",
        flush=True,
    )


if __name__ == "__main__":
    main()
