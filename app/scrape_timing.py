"""Optional per-page scrape timings. Gated by SCRAPE_METRICS_DETAIL."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from typing import Any

from app import config

_timing: ContextVar[dict[str, Any] | None] = ContextVar("scrape_page_timing", default=None)
_buf_lock = threading.Lock()
_buffer: deque[dict[str, Any]] = deque(maxlen=20_000)
_lags: deque[float] = deque(maxlen=600)
_lag_max = 0.0
_watchdog_events: deque[dict[str, Any]] = deque(maxlen=80)


def enabled() -> bool:
    return bool(config.SCRAPE_METRICS_DETAIL)


def begin() -> None:
    _timing.set({})


def note(**kwargs: Any) -> None:
    cur = _timing.get()
    if cur is None:
        return
    for key, value in kwargs.items():
        if value is not None:
            cur[key] = value


def take() -> dict[str, Any]:
    cur = _timing.get() or {}
    _timing.set(None)
    return cur


def note_httpx(response: Any) -> None:
    if not enabled() and _timing.get() is None:
        return
    elapsed = getattr(response, "elapsed", None)
    network_ms = None
    if elapsed is not None:
        try:
            network_ms = float(elapsed.total_seconds()) * 1000.0
        except Exception:
            network_ms = None
    headers = getattr(response, "headers", None) or {}
    try:
        etag = headers.get("etag") or headers.get("ETag")
        last_mod = headers.get("last-modified") or headers.get("Last-Modified")
    except Exception:
        etag = last_mod = None
    note(
        status_code=getattr(response, "status_code", None),
        network_ms=network_ms,
        has_etag=1 if etag else 0,
        has_last_modified=1 if last_mod else 0,
        etag=etag,
        last_modified=last_mod,
    )


def log_row(row: dict[str, Any]) -> None:
    if not enabled():
        return
    payload = dict(row)
    payload.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    with _buf_lock:
        _buffer.append(payload)


def drain(max_n: int = 800) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with _buf_lock:
        while _buffer and len(out) < max_n:
            out.append(_buffer.popleft())
    return out


async def watchdog_sample(sleep_s: float = 0.1) -> float:
    t0 = time.monotonic()
    await asyncio.sleep(0)
    spin = time.monotonic() - t0
    t1 = time.monotonic()
    await asyncio.sleep(sleep_s)
    lag = time.monotonic() - t1 - sleep_s
    worst = max(spin, lag)
    global _lag_max
    _lags.append(worst)
    if worst > _lag_max:
        _lag_max = worst
    if worst >= 2.0:
        ev = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
            "lag_ms": round(worst * 1000, 1),
        }
        _watchdog_events.append(ev)
        print(f"scrape watchdog lag_ms={ev['lag_ms']} at={ev['at']}", flush=True)
    return worst


def watchdog_snapshot() -> dict[str, Any]:
    lags = list(_lags)
    if not lags:
        return {"n": 0, "max_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "last_ms": 0.0, "events": []}
    ordered = sorted(lags)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    p50 = ordered[len(ordered) // 2]
    return {
        "n": len(lags),
        "max_ms": round(_lag_max * 1000, 1),
        "p50_ms": round(p50 * 1000, 1),
        "p95_ms": round(p95 * 1000, 1),
        "last_ms": round(lags[-1] * 1000, 1),
        "over_50ms": sum(1 for item in lags if item >= 0.05),
        "over_500ms": sum(1 for item in lags if item >= 0.5),
        "over_2000ms": sum(1 for item in lags if item >= 2.0),
        "events": list(_watchdog_events),
    }


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return round(ordered[idx], 2)


def summarize_rows(rows: list[dict[str, Any]], *, seconds: float) -> dict[str, Any]:
    by_portal: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "pages": 0,
        "listings": 0,
        "ok": 0,
        "http_403": 0,
        "http_429": 0,
        "fail": 0,
        "deferred": 0,
        "fetch_ms": [],
        "parse_ms": [],
        "upsert_ms": [],
        "deferred_wait_ms": [],
        "has_etag": 0,
        "has_last_modified": 0,
        "header_samples": 0,
        "detail": 0,
        "pipelines": defaultdict(lambda: {"pages": 0, "listings": 0}),
    })
    shards_seen: set[str] = set()
    deferred_wait = []
    for row in rows:
        portal = str(row.get("portal") or "unknown")
        bucket = by_portal[portal]
        kind = str(row.get("kind") or "page")
        pipeline = str(row.get("pipeline") or "unknown")
        if kind == "detail":
            bucket["detail"] += 1
            continue
        if kind == "attempt":
            continue
        if kind == "upsert":
            if row.get("upsert_ms") is not None:
                bucket["upsert_ms"].append(float(row["upsert_ms"]))
            continue
        bucket["pages"] += 1
        bucket["pipelines"][pipeline]["pages"] += 1
        listings = int(row.get("listings_count") or 0)
        bucket["listings"] += listings
        bucket["pipelines"][pipeline]["listings"] += listings
        shard = str(row.get("shard_key") or "")
        if shard:
            shards_seen.add(shard)
        code = row.get("status_code")
        if row.get("deferred"):
            bucket["deferred"] += 1
        elif code == 403:
            bucket["http_403"] += 1
        elif code == 429:
            bucket["http_429"] += 1
        elif row.get("error"):
            bucket["fail"] += 1
        else:
            bucket["ok"] += 1
        if row.get("fetch_ms") is not None:
            bucket["fetch_ms"].append(float(row["fetch_ms"]))
        if row.get("parse_ms") is not None:
            bucket["parse_ms"].append(float(row["parse_ms"]))
        if row.get("deferred_wait_ms") is not None:
            wait = float(row["deferred_wait_ms"])
            bucket["deferred_wait_ms"].append(wait)
            deferred_wait.append(wait)
        if row.get("has_etag") is not None or row.get("has_last_modified") is not None:
            bucket["header_samples"] += 1
            bucket["has_etag"] += int(row.get("has_etag") or 0)
            bucket["has_last_modified"] += int(row.get("has_last_modified") or 0)

    def pack(bucket: dict[str, Any]) -> dict[str, Any]:
        pages = bucket["pages"]
        errors = bucket["http_403"] + bucket["http_429"] + bucket["fail"]
        pipelines = {
            name: {
                "pages": item["pages"],
                "listings": item["listings"],
                "pages_per_s": round(item["pages"] / seconds, 3) if seconds else 0,
                "listings_per_s": round(item["listings"] / seconds, 3) if seconds else 0,
            }
            for name, item in bucket["pipelines"].items()
        }
        return {
            "pages": pages,
            "listings": bucket["listings"],
            "pages_per_s": round(pages / seconds, 3) if seconds else 0,
            "listings_per_s": round(bucket["listings"] / seconds, 3) if seconds else 0,
            "ok": bucket["ok"],
            "http_403": bucket["http_403"],
            "http_429": bucket["http_429"],
            "fail": bucket["fail"],
            "deferred": bucket["deferred"],
            "error_rate": round(errors / max(1, pages), 4),
            "detail_fetches": bucket["detail"],
            "list_to_detail": round(pages / bucket["detail"], 2) if bucket["detail"] else None,
            "fetch_ms_p50": percentile(bucket["fetch_ms"], 0.50),
            "fetch_ms_p95": percentile(bucket["fetch_ms"], 0.95),
            "fetch_ms_p99": percentile(bucket["fetch_ms"], 0.99),
            "parse_ms_p50": percentile(bucket["parse_ms"], 0.50),
            "parse_ms_p95": percentile(bucket["parse_ms"], 0.95),
            "upsert_ms_p50": percentile(bucket["upsert_ms"], 0.50),
            "upsert_ms_p95": percentile(bucket["upsert_ms"], 0.95),
            "deferred_wait_ms_p50": percentile(bucket["deferred_wait_ms"], 0.50),
            "deferred_wait_ms_p95": percentile(bucket["deferred_wait_ms"], 0.95),
            "etag_pct": round(100.0 * bucket["has_etag"] / bucket["header_samples"], 1) if bucket["header_samples"] else None,
            "last_modified_pct": round(100.0 * bucket["has_last_modified"] / bucket["header_samples"], 1) if bucket["header_samples"] else None,
            "header_samples": bucket["header_samples"],
            "pipelines": pipelines,
        }

    portals = {name: pack(bucket) for name, bucket in sorted(by_portal.items())}
    all_pages = sum(item["pages"] for item in portals.values())
    all_listings = sum(item["listings"] for item in portals.values())
    return {
        "seconds": round(seconds, 1),
        "pages": all_pages,
        "listings": all_listings,
        "pages_per_s": round(all_pages / seconds, 3) if seconds else 0,
        "listings_per_s": round(all_listings / seconds, 3) if seconds else 0,
        "unique_shards": len(shards_seen),
        "deferred_rows": sum(item["deferred"] for item in portals.values()),
        "deferred_wait_ms_p50": percentile(deferred_wait, 0.50),
        "deferred_wait_ms_p95": percentile(deferred_wait, 0.95),
        "portals": portals,
    }
