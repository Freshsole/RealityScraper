from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from app import config
from app.monitor_index import MonitorIndex
from app.scrape_engine import AdaptiveLimiter, LimiterRegistry, ScrapeEngine, ScrapeMetrics
from app.sreality import Listing


class ScrapeEngineTests(unittest.TestCase):
    def test_scrape_metrics_error_rate(self):
        metrics = ScrapeMetrics()
        for _ in range(9):
            metrics.record("ok")
        metrics.record("429")
        self.assertTrue(0.05 <= metrics.error_rate_5m() <= 0.2)
        snap = metrics.snapshot()
        self.assertEqual(snap["fetch_ok"], 9)
        self.assertEqual(snap["http_429"], 1)

    def test_scrape_metrics_per_portal_window(self):
        metrics = ScrapeMetrics()
        for _ in range(10):
            metrics.record("ok", portal="sreality")
        metrics.record("429", portal="mmreality")
        metrics.record("403", portal="mmreality")
        self.assertEqual(metrics.error_rate_5m("sreality"), 0.0)
        self.assertEqual(metrics.error_rate_5m("mmreality"), 1.0)
        self.assertEqual(metrics.http_429, 1)
        self.assertIn("mmreality", metrics.snapshot()["portals"])
        self.assertEqual(metrics.portal_error_rates()["mmreality"], 1.0)

    def test_adaptive_limiter_shrinks_on_429(self):
        async def _run() -> int:
            limiter = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
            await limiter.acquire()
            await limiter.release(status_code=429, ok=False)
            return limiter.limit

        self.assertEqual(asyncio.run(_run()), 8)

    def test_adaptive_limiter_serves_monitor_before_deep_waiter(self):
        async def _run() -> list[str]:
            limiter = AdaptiveLimiter(initial=1, floor=1, ceiling=1)
            order: list[str] = []
            await limiter.acquire()

            async def waiter(name: str, priority: int) -> None:
                await limiter.acquire(priority)
                order.append(name)
                await limiter.release(ok=True)

            deep = asyncio.create_task(waiter("deep", 2))
            await asyncio.sleep(0)
            monitor = asyncio.create_task(waiter("monitor", 0))
            await asyncio.sleep(0)
            await limiter.release(ok=True)
            await asyncio.gather(deep, monitor)
            return order

        self.assertEqual(asyncio.run(_run()), ["monitor", "deep"])

    def test_limiter_registry_uses_overrides(self):
        previous = dict(config.SCRAPE_CONCURRENCY_OVERRIDES)
        try:
            config.SCRAPE_CONCURRENCY_OVERRIDES["sreality"] = 24
            config.SCRAPE_CONCURRENCY_OVERRIDES["mmreality"] = 4
            registry = LimiterRegistry()
            self.assertEqual(registry.for_portal("sreality").ceiling, 24)
            self.assertEqual(registry.for_portal("mmreality").ceiling, 4)
            self.assertEqual(registry.for_portal("bazos").ceiling, config.SCRAPE_CONCURRENCY)
            self.assertIs(registry.for_portal("sreality"), registry.for_portal("Sreality"))
        finally:
            config.SCRAPE_CONCURRENCY_OVERRIDES.clear()
            config.SCRAPE_CONCURRENCY_OVERRIDES.update(previous)

    def test_per_portal_limiters_do_not_share_slots(self):
        async def _run() -> float:
            registry = LimiterRegistry()
            busy = registry.for_portal("sreality")
            busy.limit = 1
            await busy.acquire(2)
            engine = ScrapeEngine(registry=registry, priority=0, pipeline="monitor")

            async def fetch_page(_page: int):
                return [], 0

            started = time.monotonic()
            result = await engine.fetch_one_page(fetch_page, 1, portal="bazos")
            elapsed = time.monotonic() - started
            self.assertIsNone(result.error)
            return elapsed

        self.assertLess(asyncio.run(_run()), 0.2)

    def test_429_on_one_portal_does_not_shrink_another(self):
        async def _run() -> tuple[int, int, int]:
            registry = LimiterRegistry()
            engine = ScrapeEngine(registry=registry)
            sreality = registry.for_portal("sreality")
            initial_sreality = sreality.limit

            async def boom(_page: int):
                class Exc(Exception):
                    def __init__(self) -> None:
                        super().__init__("HTTP 429")
                        self.response = type("Resp", (), {"status_code": 429})()

                raise Exc()

            with patch("app.scrape_engine.asyncio.sleep", new_callable=AsyncMock):
                await engine.fetch_one_page(boom, 1, portal="mmreality")
            return registry.for_portal("mmreality").limit, sreality.limit, initial_sreality

        mm_limit, sreality_limit, initial_sreality = asyncio.run(_run())
        self.assertLess(mm_limit, initial_sreality)
        self.assertEqual(sreality_limit, initial_sreality)

    def test_global_cap_is_process_wide(self):
        async def _run() -> int:
            registry = LimiterRegistry()
            registry.global_limiter.limit = 1
            registry.global_limiter.ceiling = 1
            registry.global_limiter.floor = 1
            engine = ScrapeEngine(registry=registry)
            peak = 0
            current = 0

            async def fetch_page(_page: int):
                nonlocal peak, current
                current += 1
                peak = max(peak, current)
                await asyncio.sleep(0.03)
                current -= 1
                return [], 0

            await asyncio.gather(
                engine.fetch_one_page(fetch_page, 1, portal="sreality"),
                engine.fetch_one_page(fetch_page, 1, portal="bazos"),
            )
            return peak

        self.assertEqual(asyncio.run(_run()), 1)

    def test_fetch_pages_parallel_defers_after_deadline(self):
        async def _run() -> None:
            engine = ScrapeEngine(AdaptiveLimiter(initial=4, floor=2, ceiling=4))
            calls: list[int] = []

            async def fetch_page(page: int):
                calls.append(page)
                await asyncio.sleep(0.01)
                listings = [
                    Listing(
                        id=page * 100 + i,
                        name=f"Byt {page}-{i} 2+kk",
                        price_czk=10000,
                        price_label="10 000 Kč/měsíc",
                        disposition="2+kk",
                        area_m2=50,
                        locality="Praha 1",
                        url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{page * 100 + i}",
                        image_url=None,
                    )
                    for i in range(3)
                ]
                return listings, 30

            expired = await engine.fetch_pages_parallel(
                shard_key="test:shard-expired",
                fetch_page=fetch_page,
                max_pages=5,
                deadline_monotonic=asyncio.get_running_loop().time() - 1.0,
            )
            self.assertEqual(expired.pages_ok, 0)
            self.assertEqual(expired.deferred_pages, [1, 2, 3, 4, 5])
            self.assertEqual(calls, [])

            ok = await engine.fetch_pages_parallel(
                shard_key="test:shard-expired",
                fetch_page=fetch_page,
                max_pages=1,
                deadline_monotonic=asyncio.get_running_loop().time() + 2.0,
            )
            self.assertGreaterEqual(ok.pages_ok, 1)
            self.assertIn(1, calls)

        asyncio.run(_run())

    def test_monitor_index_buckets_not_all_monitors(self):
        monitors = [
            {
                "id": "m-praha-rent",
                "enabled": True,
                "portals": "sreality",
                "search_url": (
                    "https://www.sreality.cz/hledani/pronajem/byty/praha"
                    "?velikost=2%2Bkk&razeni=nejnovejsi"
                ),
                "created_at": "2020-01-01T00:00:00+00:00",
            },
            {
                "id": "m-brno-sale",
                "enabled": True,
                "portals": "sreality",
                "search_url": (
                    "https://www.sreality.cz/hledani/prodej/byty/brno"
                    "?velikost=3%2Bkk&razeni=nejnovejsi"
                ),
                "created_at": "2020-01-01T00:00:00+00:00",
            },
        ]
        index = MonitorIndex(monitors)
        listing = Listing(
            id=1,
            name="Pronájem bytu 2+kk 55 m²",
            price_czk=20000,
            price_label="20 000 Kč/měsíc",
            disposition="2+kk",
            area_m2=55,
            locality="Praha 3 - Žižkov",
            url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1",
            image_url=None,
        )
        candidates = index.candidate_monitors(listing)
        ids = {item["id"] for item in candidates}
        self.assertIn("m-praha-rent", ids)
        self.assertTrue("m-brno-sale" not in ids or len(candidates) < len(monitors))

    def test_batch_commit_constant_from_config(self):
        self.assertGreaterEqual(config.SCRAPE_BATCH_COMMIT, 50)
        self.assertGreaterEqual(config.SCRAPE_CONCURRENCY, config.SCRAPE_CONCURRENCY_FLOOR)


if __name__ == "__main__":
    unittest.main()
