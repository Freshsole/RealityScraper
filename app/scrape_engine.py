from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from app import config
from app.block_page import PortalBlocked, PortalCooldown


@dataclass
class ScrapeMetrics:
    http_403: int = 0
    http_429: int = 0
    fetch_ok: int = 0
    fetch_fail: int = 0
    concurrency_current: int = 0
    tick_deferred_pages: int = 0
    tick_duration_ms: float = 0.0
    _window: deque[tuple[float, str]] = field(default_factory=deque, repr=False)

    def record(self, kind: str) -> None:
        now = time.monotonic()
        self._window.append((now, kind))
        cutoff = now - 300.0
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()
        if kind == "403":
            self.http_403 += 1
        elif kind == "429":
            self.http_429 += 1
        elif kind == "ok":
            self.fetch_ok += 1
        else:
            self.fetch_fail += 1

    def error_rate_5m(self) -> float:
        if not self._window:
            return 0.0
        errors = sum(1 for _ts, kind in self._window if kind in {"403", "429", "fail"})
        return errors / max(1, len(self._window))

    def snapshot(self) -> dict[str, Any]:
        return {
            "http_403": self.http_403,
            "http_429": self.http_429,
            "fetch_ok": self.fetch_ok,
            "fetch_fail": self.fetch_fail,
            "concurrency_current": self.concurrency_current,
            "tick_deferred_pages": self.tick_deferred_pages,
            "tick_duration_ms": self.tick_duration_ms,
            "error_rate_5m": round(self.error_rate_5m(), 4),
            "limit": None,
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
        self.cooldown = PortalCooldown()

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

    def waiting_below(self, priority: int) -> int:
        """How many acquire() waiters are strictly higher priority (lower number)."""
        waiting = getattr(self, "_waiting", None) or {}
        return sum(int(count) for queued, count in waiting.items() if queued < priority and count)


def should_yield_deep(
    *,
    discovery_inflight: bool,
    waiting_below: int = 0,
    enabled: bool = True,
) -> bool:
    """Rolling deep must not start a tick that would steal slots from NewDiscovery."""
    if not enabled:
        return False
    return bool(discovery_inflight or int(waiting_below) > 0)


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


@dataclass
class ShardFetchResult:
    shard_key: str
    search_url: str
    listings: list[Any]
    total: int
    pages_ok: int
    deferred_pages: list[int]
    error: str | None = None


FetchPageFn = Callable[[int], Awaitable[tuple[list[Any], int]]]


def merge_shard_results(first: ShardFetchResult, rest: ShardFetchResult) -> ShardFetchResult:
    """Combine page-1 and pages-2..N results for the same shard."""
    seen: set[int] = set()
    listings: list[Any] = []
    for item in list(first.listings or []) + list(rest.listings or []):
        item_id = getattr(item, "id", None)
        if item_id is None or item_id in seen:
            continue
        seen.add(int(item_id))
        listings.append(item)
    return ShardFetchResult(
        shard_key=first.shard_key or rest.shard_key,
        search_url=first.search_url or rest.search_url,
        listings=listings,
        total=rest.total or first.total,
        pages_ok=int(first.pages_ok or 0) + int(rest.pages_ok or 0),
        deferred_pages=list(rest.deferred_pages or first.deferred_pages or []),
        error=rest.error or first.error,
    )


class ScrapeEngine:
    def __init__(self, limiter: AdaptiveLimiter | None = None, *, priority: int = 1) -> None:
        self.limiter = limiter or AdaptiveLimiter()
        self.metrics = self.limiter.metrics
        self.priority = max(0, int(priority))
        self.deferred: dict[str, list[int]] = {}

    def _take_deferred(self, shard_key: str) -> list[int]:
        pages = self.deferred.pop(shard_key, [])
        return pages[: config.SCRAPE_DEFERRED_MAX_PER_SHARD]

    def _store_deferred(self, shard_key: str, pages: list[int]) -> None:
        if not pages:
            return
        existing = self.deferred.get(shard_key, [])
        merged = sorted(set(existing + pages))[: config.SCRAPE_DEFERRED_MAX_PER_SHARD]
        self.deferred[shard_key] = merged
        self.metrics.tick_deferred_pages += len(pages)

    def _cooldown(self) -> PortalCooldown:
        cooldown = getattr(self.limiter, "cooldown", None)
        if isinstance(cooldown, PortalCooldown):
            return cooldown
        if not hasattr(self, "_local_cooldown"):
            self._local_cooldown = PortalCooldown()
        return self._local_cooldown

    @staticmethod
    def _is_block_error(error: str | None) -> bool:
        raw = (error or "").lower()
        return raw.startswith("blocked:") or raw.startswith("cooling:")

    async def fetch_one_page(self, fetch_page: FetchPageFn, page: int, *, portal: str = "") -> PageResult:
        await self.limiter.acquire(self.priority)
        try:
            batch, total = await fetch_page(page)
            self.metrics.record("ok")
            await self.limiter.release(ok=True)
            return PageResult(page=page, listings=list(batch or []), total=int(total or 0))
        except PortalBlocked as exc:
            if not exc.portal and portal:
                exc.portal = portal
            if exc.kind == "cloudflare" or exc.status_code == 403:
                self.metrics.record("403")
            elif exc.kind == "rate_limit" or exc.status_code == 429:
                self.metrics.record("429")
            else:
                self.metrics.record("fail")
            # Per-portal skip only — do not shrink the shared limiter or sleep the tick.
            await self.limiter.release(ok=False)
            self._cooldown().note_block(exc)
            return PageResult(page=page, listings=[], total=0, error=str(exc)[:240])
        except Exception as exc:
            code = _status_from_exc(exc)
            if code == 403:
                self.metrics.record("403")
            elif code == 429:
                self.metrics.record("429")
            else:
                self.metrics.record("fail")
            await self.limiter.release(status_code=code, ok=False)
            if code in (403, 429):
                await asyncio.sleep(min(20.0, 0.5 + (self.limiter.ceiling - self.limiter.limit) * 0.25))
            return PageResult(page=page, listings=[], total=0, error=str(exc)[:240])

    async def fetch_pages_parallel(
        self,
        *,
        shard_key: str,
        fetch_page: FetchPageFn,
        max_pages: int,
        deadline_monotonic: float,
        prioritize_first: bool = True,
        portal: str = "",
        start_page: int = 1,
        include_deferred: bool = True,
    ) -> ShardFetchResult:
        """Fetch pages start_page..max_pages (plus deferred) until deadline; leftover → deferred queue."""
        first_page = max(1, int(start_page))
        last_page = max(first_page, int(max_pages))
        pending = list(range(first_page, last_page + 1))
        if include_deferred:
            for page in self._take_deferred(shard_key):
                if page < first_page:
                    continue
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

        if portal and self._cooldown().active(portal):
            reason = self._cooldown().reason(portal) or "blocked"
            return ShardFetchResult(
                shard_key=shard_key,
                search_url="",
                listings=[],
                total=0,
                pages_ok=0,
                deferred_pages=[],
                error=f"cooling:{reason}:{portal}",
            )

        async def run_page(page: int) -> PageResult:
            if time.monotonic() >= deadline_monotonic:
                return PageResult(page=page, listings=[], total=0, deferred=True)
            return await self.fetch_one_page(fetch_page, page, portal=portal)

        # Always try page 1 first when present (alpha SLA).
        if pending and pending[0] == 1:
            first = await run_page(1)
            pending = pending[1:]
            if first.deferred:
                deferred.append(1)
            elif first.error:
                last_error = first.error
                if self._is_block_error(first.error):
                    # Cooldown covers the portal — do not queue pages 2..N for a dead list.
                    pending = []
                    deferred = []
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
                break
            batch_n = min(len(pending), max(1, self.limiter.limit))
            chunk = pending[:batch_n]
            pending = pending[batch_n:]
            results = await asyncio.gather(*(run_page(page) for page in chunk))
            blocked = False
            for result in results:
                if result.deferred:
                    deferred.append(result.page)
                    continue
                if result.error:
                    last_error = result.error
                    if self._is_block_error(result.error):
                        blocked = True
                    else:
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
            if blocked:
                pending = []
                deferred = []

        deferred = sorted(set(deferred))
        self._store_deferred(shard_key, deferred)
        return ShardFetchResult(
            shard_key=shard_key,
            search_url="",
            listings=listings,
            total=total,
            pages_ok=pages_ok,
            deferred_pages=deferred,
            error=last_error,
        )

    def _cooling_result(self, shard: dict[str, str]) -> ShardFetchResult:
        portal = (shard.get("portal") or "").strip().lower()
        reason = self._cooldown().reason(portal) or "blocked"
        return ShardFetchResult(
            shard_key=shard.get("shard_key") or shard.get("search_url") or "",
            search_url=shard.get("search_url") or "",
            listings=[],
            total=0,
            pages_ok=0,
            deferred_pages=[],
            error=f"cooling:{reason}:{portal}",
        )

    async def fetch_shards(
        self,
        shards: list[dict[str, str]],
        *,
        client_factory: Callable[[str], Any],
        max_pages: int,
        deadline_sec: float,
        page1_across: bool | None = None,
        min_shard_sec: float | None = None,
    ) -> list[ShardFetchResult]:
        started = time.monotonic()
        deadline = started + max(0.1, float(deadline_sec))
        if page1_across is None:
            page1_across = bool(config.SCRAPE_PAGE1_ACROSS_SHARDS)
        if min_shard_sec is None:
            min_shard_sec = float(config.SCRAPE_MIN_SHARD_SEC)

        cooldown = self._cooldown()
        ready: list[dict[str, str]] = []
        skipped: list[ShardFetchResult] = []
        for shard in shards:
            portal = (shard.get("portal") or "").strip().lower()
            if portal and cooldown.active(portal):
                skipped.append(self._cooling_result(shard))
            else:
                ready.append(shard)

        def shard_deadline(until: float) -> float:
            if min_shard_sec <= 0:
                return until
            return max(until, time.monotonic() + float(min_shard_sec))

        async def run_shard(
            shard: dict[str, str],
            *,
            pages: int,
            start_page: int,
            include_deferred: bool,
            until: float,
        ) -> ShardFetchResult:
            url = shard["search_url"]
            portal = (shard.get("portal") or "").strip().lower()
            client = client_factory(url)

            async def fetch_page(page: int) -> tuple[list[Any], int]:
                return await client.fetch_page(page, newest=True)

            result = await self.fetch_pages_parallel(
                shard_key=shard.get("shard_key") or url,
                fetch_page=fetch_page,
                max_pages=pages,
                deadline_monotonic=shard_deadline(until),
                portal=portal,
                start_page=start_page,
                include_deferred=include_deferred,
            )
            result.search_url = url
            return result

        # Bound fan-out of shards roughly to concurrency.
        gate = asyncio.Semaphore(max(4, self.limiter.limit))

        if not ready:
            results = skipped
        elif page1_across and max_pages > 1:
            frac = float(config.SCRAPE_PAGE1_BUDGET_FRAC)
            page1_until = min(deadline, started + max(8.0, float(deadline_sec) * frac))

            async def gated_page1(shard: dict[str, str]) -> ShardFetchResult:
                async with gate:
                    return await run_shard(
                        shard,
                        pages=1,
                        start_page=1,
                        include_deferred=False,
                        until=page1_until,
                    )

            first = list(await asyncio.gather(*(gated_page1(shard) for shard in ready)))
            rest_pairs: list[tuple[dict[str, str], ShardFetchResult]] = []
            for shard, result in zip(ready, first):
                if result.error and self._is_block_error(result.error):
                    continue
                if result.pages_ok <= 0:
                    continue
                rest_pairs.append((shard, result))

            if rest_pairs and time.monotonic() < deadline:

                async def run_rest(pair: tuple[dict[str, str], ShardFetchResult]) -> ShardFetchResult:
                    shard, first_result = pair
                    rest = await run_shard(
                        shard,
                        pages=max_pages,
                        start_page=2,
                        include_deferred=True,
                        until=deadline,
                    )
                    return merge_shard_results(first_result, rest)

                rest_by_key = {item.shard_key: item for item in await asyncio.gather(*(run_rest(pair) for pair in rest_pairs))}
                ready_results = [
                    rest_by_key.get(shard.get("shard_key") or shard["search_url"], result)
                    for shard, result in zip(ready, first)
                ]
            else:
                ready_results = first
            by_key = {item.shard_key: item for item in ready_results + skipped}
            results = []
            for shard in shards:
                key = shard.get("shard_key") or shard.get("search_url") or ""
                if key in by_key:
                    results.append(by_key.pop(key))
            results.extend(by_key.values())
        else:

            async def gated(shard: dict[str, str]) -> ShardFetchResult:
                async with gate:
                    return await run_shard(
                        shard,
                        pages=max_pages,
                        start_page=1,
                        include_deferred=True,
                        until=deadline,
                    )

            ready_results = list(await asyncio.gather(*(gated(shard) for shard in ready)))
            by_key = {item.shard_key: item for item in ready_results + skipped}
            results = []
            for shard in shards:
                key = shard.get("shard_key") or shard.get("search_url") or ""
                if key in by_key:
                    results.append(by_key.pop(key))
            results.extend(by_key.values())

        self.metrics.tick_duration_ms = (time.monotonic() - started) * 1000.0
        snap = self.metrics.snapshot()
        snap["limit"] = self.limiter.limit
        if self.metrics.error_rate_5m() >= config.SCRAPE_ERROR_RATE_ALERT:
            print(
                f"scrape throttle alert: error_rate_5m={snap['error_rate_5m']} "
                f"403={snap['http_403']} 429={snap['http_429']} limit={self.limiter.limit}",
                flush=True,
            )
        return results
