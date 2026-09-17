from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from app import config
from app.sources import portal_of


@dataclass
class _PortalWindow:
    http_403: int = 0
    http_429: int = 0
    fetch_ok: int = 0
    fetch_fail: int = 0
    window: deque[tuple[float, str]] = field(default_factory=deque, repr=False)

    def record(self, kind: str) -> None:
        now = time.monotonic()
        self.window.append((now, kind))
        cutoff = now - 300.0
        while self.window and self.window[0][0] < cutoff:
            self.window.popleft()
        if kind == "403":
            self.http_403 += 1
        elif kind == "429":
            self.http_429 += 1
        elif kind == "ok":
            self.fetch_ok += 1
        else:
            self.fetch_fail += 1

    def error_rate_5m(self) -> float:
        if not self.window:
            return 0.0
        errors = sum(1 for _ts, kind in self.window if kind in {"403", "429", "fail"})
        return errors / max(1, len(self.window))

    def snapshot(self) -> dict[str, Any]:
        return {
            "http_403": self.http_403,
            "http_429": self.http_429,
            "fetch_ok": self.fetch_ok,
            "fetch_fail": self.fetch_fail,
            "error_rate_5m": round(self.error_rate_5m(), 4),
        }


class ScrapeMetrics:
    def __init__(self) -> None:
        self.concurrency_current: int = 0
        self.tick_deferred_pages: int = 0
        self.tick_duration_ms: float = 0.0
        self._portals: dict[str, _PortalWindow] = defaultdict(_PortalWindow)
        self._all = _PortalWindow()

    def record(self, kind: str, portal: str = "") -> None:
        key = (portal or "*").strip().lower() or "*"
        self._all.record(kind)
        self._portals[key].record(kind)

    def error_rate_5m(self, portal: str | None = None) -> float:
        if portal:
            return self._portals[portal.strip().lower()].error_rate_5m()
        return self._all.error_rate_5m()

    def portal_error_rates(self) -> dict[str, float]:
        return {name: window.error_rate_5m() for name, window in self._portals.items()}

    @property
    def http_403(self) -> int:
        return self._all.http_403

    @property
    def http_429(self) -> int:
        return self._all.http_429

    @property
    def fetch_ok(self) -> int:
        return self._all.fetch_ok

    @property
    def fetch_fail(self) -> int:
        return self._all.fetch_fail

    def snapshot(self) -> dict[str, Any]:
        return {
            "http_403": self._all.http_403,
            "http_429": self._all.http_429,
            "fetch_ok": self._all.fetch_ok,
            "fetch_fail": self._all.fetch_fail,
            "concurrency_current": self.concurrency_current,
            "tick_deferred_pages": self.tick_deferred_pages,
            "tick_duration_ms": self.tick_duration_ms,
            "error_rate_5m": round(self._all.error_rate_5m(), 4),
            "limit": None,
            "portals": {name: window.snapshot() for name, window in sorted(self._portals.items())},
        }


class AdaptiveLimiter:
    """Bounded concurrency that shrinks on 403/429 and slowly ramps back up."""

    def __init__(
        self,
        initial: int | None = None,
        floor: int | None = None,
        ceiling: int | None = None,
        metrics: ScrapeMetrics | None = None,
    ) -> None:
        self.floor = floor if floor is not None else config.SCRAPE_CONCURRENCY_FLOOR
        self.ceiling = ceiling if ceiling is not None else config.SCRAPE_CONCURRENCY
        self.limit = max(self.floor, min(self.ceiling, initial if initial is not None else config.SCRAPE_CONCURRENCY))
        self._active = 0
        self._cond = asyncio.Condition()
        self.metrics = metrics or ScrapeMetrics()
        self._ok_streak = 0

    async def acquire(self, priority: int = 1) -> None:
        async with self._cond:
            priority = max(0, int(priority))
            waiting = getattr(self, "_waiting", None)
            if waiting is None:
                waiting = self._waiting = {}
            waiting[priority] = waiting.get(priority, 0) + 1
            try:
                while self._active >= self.limit or any(
                    count > 0 and queued_priority < priority
                    for queued_priority, count in waiting.items()
                ):
                    await self._cond.wait()
                self._active += 1
                self.metrics.concurrency_current = self._active
            finally:
                waiting[priority] = max(0, waiting.get(priority, 1) - 1)
                self._cond.notify_all()

    async def release(self, *, status_code: int | None = None, ok: bool = False) -> None:
        async with self._cond:
            self._active = max(0, self._active - 1)
            self.metrics.concurrency_current = self._active
            if status_code in (403, 429):
                self.limit = max(self.floor, self.limit // 2)
                self._ok_streak = 0
            elif ok:
                self._ok_streak += 1
                if self._ok_streak >= 20 and self.limit < self.ceiling:
                    self.limit += 1
                    self._ok_streak = 0
            self._cond.notify_all()


class LimiterRegistry:
    """One AdaptiveLimiter per portal so a busy crawl does not stall another site."""

    def __init__(self, metrics: ScrapeMetrics | None = None) -> None:
        self.metrics = metrics or ScrapeMetrics()
        self._by_portal: dict[str, AdaptiveLimiter] = {}
        global_n = max(1, int(config.SCRAPE_GLOBAL_CONCURRENCY))
        # Own metrics so a 403/429 never halves the process-wide cap.
        self.global_limiter = AdaptiveLimiter(
            initial=global_n,
            floor=global_n,
            ceiling=global_n,
            metrics=ScrapeMetrics(),
        )

    @staticmethod
    def portal_key(portal: str) -> str:
        return (portal or "unknown").strip().lower() or "unknown"

    @staticmethod
    def ceiling_for(portal: str) -> int:
        key = LimiterRegistry.portal_key(portal)
        raw = config.SCRAPE_CONCURRENCY_OVERRIDES.get(key)
        if raw is None:
            return int(config.SCRAPE_CONCURRENCY)
        return max(1, min(64, int(raw)))

    def for_portal(self, portal: str) -> AdaptiveLimiter:
        key = self.portal_key(portal)
        limiter = self._by_portal.get(key)
        if limiter is not None:
            return limiter
        ceiling = self.ceiling_for(key)
        floor = max(1, min(int(config.SCRAPE_CONCURRENCY_FLOOR), ceiling))
        limiter = AdaptiveLimiter(
            initial=ceiling,
            floor=floor,
            ceiling=ceiling,
            metrics=self.metrics,
        )
        self._by_portal[key] = limiter
        return limiter

    def limits(self) -> dict[str, int]:
        out = {name: item.limit for name, item in sorted(self._by_portal.items())}
        out["*global*"] = self.global_limiter.limit
        return out


def _status_from_exc(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    text = str(exc)
    if "403" in text:
        return 403
    if "429" in text:
        return 429
    return None


@dataclass
class PageResult:
    page: int
    listings: list[Any]
    total: int
    error: str | None = None
    deferred: bool = False
    status_code: int | None = None
    fetch_ms: float = 0.0
    parse_ms: float = 0.0


@dataclass
class ShardFetchResult:
    shard_key: str
    search_url: str
    listings: list[Any]
    total: int
    pages_ok: int
    deferred_pages: list[int]
    error: str | None = None
    portal: str = ""


FetchPageFn = Callable[[int], Awaitable[tuple[list[Any], int]]]


class ScrapeEngine:
    def __init__(
        self,
        limiter: AdaptiveLimiter | None = None,
        *,
        priority: int = 1,
        pipeline: str = "unknown",
        registry: LimiterRegistry | None = None,
    ) -> None:
        self.registry = registry
        if registry is not None:
            self.metrics = registry.metrics
            self.limiter = limiter or AdaptiveLimiter(metrics=registry.metrics)
        else:
            self.limiter = limiter or AdaptiveLimiter()
            self.metrics = self.limiter.metrics
        self.priority = max(0, int(priority))
        self.pipeline = pipeline or "unknown"
        self.deferred: dict[str, list[int]] = {}
        self._deferred_at: dict[tuple[str, int], float] = {}

    def limiter_for(self, portal: str = "") -> AdaptiveLimiter:
        if self.registry is not None:
            return self.registry.for_portal(portal)
        return self.limiter

    def metrics_snapshot(self) -> dict[str, Any]:
        snap = self.metrics.snapshot()
        if self.registry is not None:
            limits = self.registry.limits()
            snap["limits"] = limits
            snap["limit"] = max(limits.values()) if limits else int(config.SCRAPE_CONCURRENCY)
        else:
            snap["limit"] = self.limiter.limit
        return snap

    def _shard_fanout(self, shards: list[dict[str, str]]) -> int:
        if self.registry is None:
            return max(4, int(self.limiter.limit))
        seen: set[str] = set()
        total = 0
        for shard in shards:
            portal = shard.get("portal") or portal_of(shard.get("search_url") or "")
            key = LimiterRegistry.portal_key(portal)
            if key in seen:
                continue
            seen.add(key)
            total += self.limiter_for(key).limit
        global_cap = max(4, int(self.registry.global_limiter.limit))
        return max(4, min(global_cap, total, max(len(shards), 1)))

    def _take_deferred(self, shard_key: str) -> list[int]:
        pages = self.deferred.pop(shard_key, [])
        return pages[: config.SCRAPE_DEFERRED_MAX_PER_SHARD]

    def _store_deferred(self, shard_key: str, pages: list[int]) -> None:
        if not pages:
            return
        existing = self.deferred.get(shard_key, [])
        merged = sorted(set(existing + pages))[: config.SCRAPE_DEFERRED_MAX_PER_SHARD]
        self.deferred[shard_key] = merged
        now = time.monotonic()
        for page in merged:
            self._deferred_at.setdefault((shard_key, page), now)
        self.metrics.tick_deferred_pages += len(pages)

    def _deferred_wait_ms(self, shard_key: str, page: int) -> float | None:
        started = self._deferred_at.pop((shard_key, page), None)
        if started is None:
            return None
        return max(0.0, (time.monotonic() - started) * 1000.0)

    def _log_page(
        self,
        *,
        portal: str,
        shard_key: str,
        page: int,
        result: PageResult,
        limiter_at: int,
        wait_ms: float | None,
    ) -> None:
        from app.scrape_timing import log_row

        status = result.status_code
        if result.deferred and status is None:
            status = 0
        elif result.error and status is None:
            status = _status_from_exc(RuntimeError(result.error))
        log_row(
            {
                "kind": "deferred" if result.deferred else "page",
                "portal": portal,
                "pipeline": self.pipeline,
                "shard_key": shard_key,
                "page": page,
                "fetch_ms": round(result.fetch_ms, 2),
                "parse_ms": round(result.parse_ms, 2),
                "upsert_ms": None,
                "listings_count": len(result.listings),
                "status_code": status,
                "limiter_at_call": limiter_at,
                "error": result.error,
                "deferred": 1 if result.deferred else 0,
                "deferred_wait_ms": round(wait_ms, 1) if wait_ms is not None else None,
                "has_etag": None,
                "has_last_modified": None,
            }
        )

    async def fetch_one_page(
        self,
        fetch_page: FetchPageFn,
        page: int,
        *,
        portal: str = "",
        shard_key: str = "",
        wait_ms: float | None = None,
    ) -> PageResult:
        from app.scrape_timing import begin, take
        from app import portal_health

        if portal_health.is_disabled(portal):
            return PageResult(
                page=page,
                listings=[],
                total=0,
                error="portal-disabled",
                status_code=0,
                fetch_ms=0.0,
            )

        limiter = self.limiter_for(portal)
        global_lim = self.registry.global_limiter if self.registry is not None else None
        await limiter.acquire(self.priority)
        limiter_at = limiter.limit
        ok = False
        code: int | None = None
        backoff = False
        result: PageResult | None = None
        try:
            if global_lim is not None:
                await global_lim.acquire(self.priority)
            try:
                begin()
                t0 = time.monotonic()
                try:
                    batch, total = await fetch_page(page)
                    wall_ms = (time.monotonic() - t0) * 1000.0
                    extra = take()
                    network_ms = extra.get("network_ms")
                    parse_ms = extra.get("parse_ms")
                    if parse_ms is None and network_ms is not None:
                        parse_ms = max(0.0, wall_ms - float(network_ms))
                    fetch_ms = float(network_ms) if network_ms is not None else wall_ms
                    self.metrics.record("ok", portal=portal)
                    ok = True
                    portal_health.record_ok(portal)
                    result = PageResult(
                        page=page,
                        listings=list(batch or []),
                        total=int(total or 0),
                        status_code=int(extra.get("status_code") or 200),
                        fetch_ms=fetch_ms,
                        parse_ms=float(parse_ms or 0.0),
                    )
                    from app.scrape_timing import log_row

                    log_row(
                        {
                            "kind": "page",
                            "portal": portal,
                            "pipeline": self.pipeline,
                            "shard_key": shard_key,
                            "page": page,
                            "fetch_ms": round(fetch_ms, 2),
                            "parse_ms": round(float(parse_ms or 0.0), 2),
                            "upsert_ms": None,
                            "listings_count": len(result.listings),
                            "status_code": result.status_code,
                            "limiter_at_call": limiter_at,
                            "error": None,
                            "deferred": 0,
                            "deferred_wait_ms": round(wait_ms, 1) if wait_ms is not None else None,
                            "has_etag": extra.get("has_etag"),
                            "has_last_modified": extra.get("has_last_modified"),
                        }
                    )
                except Exception as exc:
                    wall_ms = (time.monotonic() - t0) * 1000.0
                    extra = take()
                    raw_code = extra.get("status_code") or _status_from_exc(exc)
                    code = raw_code if isinstance(raw_code, int) else None
                    if code == 403:
                        self.metrics.record("403", portal=portal)
                    elif code == 429:
                        self.metrics.record("429", portal=portal)
                    else:
                        self.metrics.record("fail", portal=portal)
                    if portal_health.is_hard_fail(code, exc):
                        kind = "timeout" if portal_health.is_timeout(exc) else str(code or "fail")
                        portal_health.record_fail(portal, kind)
                    backoff = code == 429
                    result = PageResult(
                        page=page,
                        listings=[],
                        total=0,
                        error=str(exc)[:240],
                        status_code=code,
                        fetch_ms=wall_ms,
                        parse_ms=float(extra.get("parse_ms") or 0.0),
                    )
                    self._log_page(
                        portal=portal,
                        shard_key=shard_key,
                        page=page,
                        result=result,
                        limiter_at=limiter_at,
                        wait_ms=wait_ms,
                    )
            finally:
                if global_lim is not None:
                    await global_lim.release(ok=True)
        finally:
            await limiter.release(status_code=code, ok=ok)
        if backoff and limiter:
            await asyncio.sleep(min(20.0, 0.5 + (limiter.ceiling - limiter.limit) * 0.25))
        return result if result is not None else PageResult(page=page, listings=[], total=0, error="no-result")

    async def fetch_pages_parallel(
        self,
        *,
        shard_key: str,
        fetch_page: FetchPageFn,
        max_pages: int,
        deadline_monotonic: float,
        prioritize_first: bool = True,
        portal: str = "",
        search_url: str = "",
    ) -> ShardFetchResult:
        """Fetch pages 1..max_pages (plus deferred) until deadline; leftover → deferred queue."""
        pending = list(range(1, max(1, max_pages) + 1))
        for page in self._take_deferred(shard_key):
            if page not in pending:
                pending.append(page)
        pending = sorted(set(pending))
        if prioritize_first and 1 in pending:
            pending.remove(1)
            pending.insert(0, 1)

        listings: list[Any] = []
        seen_ids: set[int] = set()
        total = 0
        pages_ok = 0
        deferred: list[int] = []
        last_error: str | None = None
        portal = portal or (portal_of(search_url) if search_url else "")
        limiter = self.limiter_for(portal)

        async def run_page(page: int) -> PageResult:
            wait_ms = self._deferred_wait_ms(shard_key, page)
            if time.monotonic() >= deadline_monotonic:
                result = PageResult(page=page, listings=[], total=0, deferred=True)
                self._log_page(
                    portal=portal,
                    shard_key=shard_key,
                    page=page,
                    result=result,
                    limiter_at=limiter.limit,
                    wait_ms=wait_ms,
                )
                return result
            return await self.fetch_one_page(
                fetch_page,
                page,
                portal=portal,
                shard_key=shard_key,
                wait_ms=wait_ms,
            )

        # Always try page 1 first when present (alpha SLA).
        if pending and pending[0] == 1:
            first = await run_page(1)
            pending = pending[1:]
            if first.deferred:
                deferred.append(1)
            elif first.error:
                last_error = first.error
                if first.error == "portal-disabled":
                    pending = []
                else:
                    deferred.append(1)
            else:
                pages_ok += 1
                total = first.total or total
                for item in first.listings:
                    item_id = getattr(item, "id", None)
                    if item_id is None or item_id in seen_ids:
                        continue
                    seen_ids.add(int(item_id))
                    listings.append(item)
                page_size = max(len(first.listings), 1)
                if total:
                    needed = (int(total) + page_size - 1) // page_size
                    pending = [p for p in pending if p <= min(needed, max_pages)]

        while pending:
            if time.monotonic() >= deadline_monotonic:
                deferred.extend(pending)
                for page in pending:
                    self._log_page(
                        portal=portal,
                        shard_key=shard_key,
                        page=page,
                        result=PageResult(page=page, listings=[], total=0, deferred=True),
                        limiter_at=limiter.limit,
                        wait_ms=self._deferred_wait_ms(shard_key, page),
                    )
                break
            batch_n = min(len(pending), max(1, limiter.limit))
            chunk = pending[:batch_n]
            pending = pending[batch_n:]
            results = await asyncio.gather(*(run_page(page) for page in chunk))
            for result in results:
                if result.deferred:
                    deferred.append(result.page)
                    continue
                if result.error:
                    last_error = result.error
                    if result.error != "portal-disabled":
                        deferred.append(result.page)
                    continue
                pages_ok += 1
                total = result.total or total
                for item in result.listings:
                    item_id = getattr(item, "id", None)
                    if item_id is None or item_id in seen_ids:
                        continue
                    seen_ids.add(int(item_id))
                    listings.append(item)

        deferred = sorted(set(deferred))
        self._store_deferred(shard_key, deferred)
        return ShardFetchResult(
            shard_key=shard_key,
            search_url=search_url,
            listings=listings,
            total=total,
            pages_ok=pages_ok,
            deferred_pages=deferred,
            error=last_error,
            portal=portal,
        )

    async def fetch_shards(
        self,
        shards: list[dict[str, str]],
        *,
        client_factory: Callable[[str], Any],
        max_pages: int,
        deadline_sec: float,
    ) -> list[ShardFetchResult]:
        started = time.monotonic()
        deadline = started + deadline_sec
        results: list[ShardFetchResult] = []

        async def one(shard: dict[str, str]) -> ShardFetchResult:
            from app import portal_health

            url = shard["search_url"]
            portal = shard.get("portal") or portal_of(url)
            if portal_health.is_disabled(portal):
                return ShardFetchResult(
                    shard_key=shard.get("shard_key") or url,
                    search_url=url,
                    listings=[],
                    total=0,
                    pages_ok=0,
                    deferred_pages=[],
                    error="portal-disabled",
                    portal=portal,
                )
            client = client_factory(url)

            async def fetch_page(page: int) -> tuple[list[Any], int]:
                return await client.fetch_page(page, newest=True)

            result = await self.fetch_pages_parallel(
                shard_key=shard.get("shard_key") or url,
                fetch_page=fetch_page,
                max_pages=max_pages,
                deadline_monotonic=deadline,
                portal=portal,
                search_url=url,
            )
            result.search_url = url
            result.portal = portal
            return result

        # Bound fan-out to the sum of per-portal ceilings so mixed portals
        # are not re-serialized behind one global slot count.
        gate = asyncio.Semaphore(self._shard_fanout(shards))

        async def gated(shard: dict[str, str]) -> ShardFetchResult:
            async with gate:
                return await one(shard)

        results = list(await asyncio.gather(*(gated(shard) for shard in shards)))
        self.metrics.tick_duration_ms = (time.monotonic() - started) * 1000.0
        snap = self.metrics_snapshot()
        if self.metrics.error_rate_5m() >= config.SCRAPE_ERROR_RATE_ALERT:
            print(
                f"scrape throttle alert: error_rate_5m={snap['error_rate_5m']} "
                f"403={snap['http_403']} 429={snap['http_429']} limit={snap.get('limit')} limits={snap.get('limits')}",
                flush=True,
            )
        for portal, rate in self.metrics.portal_error_rates().items():
            if rate >= config.SCRAPE_ERROR_RATE_ALERT:
                portal_snap = snap.get("portals", {}).get(portal) or {}
                print(
                    f"scrape throttle alert portal={portal} error_rate_5m={rate:.4f} "
                    f"403={portal_snap.get('http_403')} 429={portal_snap.get('http_429')}",
                    flush=True,
                )
        return results
