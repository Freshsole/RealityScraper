"""Response timing, rate limits, and scrape ↔ app wiring.

No live portal HTTP. Fetch is mocked; SQLite uses a temp file.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config
from app.catalog_sync import daily_shards, recent_shards
from app.monitor import Hub
from app.monitor_index import MonitorIndex
from app.scrape_engine import AdaptiveLimiter, LimiterRegistry, ScrapeEngine, ScrapeMetrics
from app.sreality import Listing
from app.sources import PORTAL_IDS, client_for, portal_of
from app.store import CATALOG_MONITOR_ID, Store


def _listing(n: int, *, locality: str = "Praha 1", disposition: str = "2+kk") -> Listing:
    return Listing(
        id=n,
        name=f"Byt {n} {disposition}",
        price_czk=18000,
        price_label="18 000 Kč/měsíc",
        disposition=disposition,
        area_m2=52,
        locality=locality,
        url=f"https://www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{n}",
        image_url=None,
    )


class _HttpExc(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.response = type("Resp", (), {"status_code": code})()


class RateLimitTests(unittest.TestCase):
    def test_concurrency_never_exceeds_limit(self) -> None:
        async def _run() -> int:
            limiter = AdaptiveLimiter(initial=2, floor=1, ceiling=2)
            peak = 0

            async def worker() -> None:
                nonlocal peak
                await limiter.acquire()
                peak = max(peak, limiter._active)
                await asyncio.sleep(0.01)
                await limiter.release(ok=True)

            await asyncio.gather(*(worker() for _ in range(12)))
            return peak

        self.assertEqual(asyncio.run(_run()), 2)

    def test_403_halves_limit_like_429(self) -> None:
        async def _run() -> tuple[int, int]:
            a = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
            b = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
            await a.acquire()
            await a.release(status_code=403, ok=False)
            await b.acquire()
            await b.release(status_code=429, ok=False)
            return a.limit, b.limit

        left, right = asyncio.run(_run())
        self.assertEqual(left, 8)
        self.assertEqual(right, 8)

    def test_limit_ramps_after_twenty_ok(self) -> None:
        async def _run() -> int:
            limiter = AdaptiveLimiter(initial=4, floor=4, ceiling=8)
            for _ in range(20):
                await limiter.acquire()
                await limiter.release(ok=True)
            return limiter.limit

        self.assertEqual(asyncio.run(_run()), 5)

    def test_floor_stops_repeated_429(self) -> None:
        async def _run() -> int:
            limiter = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
            for _ in range(8):
                await limiter.acquire()
                await limiter.release(status_code=429, ok=False)
            return limiter.limit

        self.assertEqual(asyncio.run(_run()), 4)

    def test_deep_sreality_does_not_queue_monitor_bazos(self) -> None:
        async def _run() -> float:
            registry = LimiterRegistry()
            sreality = registry.for_portal("sreality")
            sreality.limit = 1
            await sreality.acquire(2)
            monitor = ScrapeEngine(registry=registry, priority=0, pipeline="monitor")

            async def fetch_page(_page: int):
                await asyncio.sleep(0.01)
                return [_listing(1)], 1

            started = time.monotonic()
            result = await monitor.fetch_one_page(fetch_page, 1, portal="bazos")
            elapsed = time.monotonic() - started
            self.assertIsNone(result.error)
            self.assertEqual(result.page, 1)
            return elapsed

        self.assertLess(asyncio.run(_run()), 0.2)

    def test_fetch_one_page_records_429_and_backs_off(self) -> None:
        async def _run() -> tuple[str | None, int, bool]:
            engine = ScrapeEngine(AdaptiveLimiter(initial=8, floor=4, ceiling=8))

            async def boom(_page: int):
                raise _HttpExc(429)

            with patch("app.scrape_engine.asyncio.sleep", new_callable=AsyncMock) as slept:
                result = await engine.fetch_one_page(boom, 1)
                slept.assert_awaited()
            return result.error, engine.limiter.limit, engine.metrics.http_429 == 1

        error, limit, counted = asyncio.run(_run())
        self.assertIsNotNone(error)
        self.assertEqual(limit, 4)
        self.assertTrue(counted)

    def test_error_rate_window_trips_alert_threshold(self) -> None:
        metrics = ScrapeMetrics()
        for _ in range(9):
            metrics.record("ok")
        metrics.record("403")
        self.assertGreaterEqual(metrics.error_rate_5m(), 0.09)
        self.assertLess(metrics.error_rate_5m(), config.SCRAPE_ERROR_RATE_ALERT + 0.05)


class ResponseTimeTests(unittest.TestCase):
    def test_parallel_pages_faster_than_sequential(self) -> None:
        async def _run() -> tuple[float, int]:
            engine = ScrapeEngine(AdaptiveLimiter(initial=8, floor=4, ceiling=8))

            async def fetch_page(page: int):
                await asyncio.sleep(0.03)
                return [_listing(page * 10 + 1)], 40

            started = time.monotonic()
            result = await engine.fetch_pages_parallel(
                shard_key="bench:parallel",
                fetch_page=fetch_page,
                max_pages=6,
                deadline_monotonic=time.monotonic() + 5.0,
            )
            return time.monotonic() - started, result.pages_ok

        elapsed, pages_ok = asyncio.run(_run())
        self.assertEqual(pages_ok, 6)
        # Page 1 serial (~30ms) then the rest in one batch (~30ms) ≪ 6×30ms sequential.
        self.assertLess(elapsed, 0.22)

    def test_deadline_skips_http_and_defers(self) -> None:
        async def _run() -> tuple[list[int], int]:
            calls: list[int] = []
            engine = ScrapeEngine(AdaptiveLimiter(initial=4, floor=2, ceiling=4))

            async def fetch_page(page: int):
                calls.append(page)
                return [_listing(page)], 10

            result = await engine.fetch_pages_parallel(
                shard_key="bench:expired",
                fetch_page=fetch_page,
                max_pages=8,
                deadline_monotonic=time.monotonic() - 1.0,
            )
            return calls, len(result.deferred_pages)

        calls, deferred = asyncio.run(_run())
        self.assertEqual(calls, [])
        self.assertEqual(deferred, 8)

    def test_slow_page_does_not_block_sibling_shard_forever(self) -> None:
        async def _run() -> list[int]:
            engine = ScrapeEngine(AdaptiveLimiter(initial=8, floor=4, ceiling=8))

            class Client:
                def __init__(self, url: str) -> None:
                    self.url = url

                async def fetch_page(self, page: int, newest: bool = True):
                    if "slow" in self.url:
                        await asyncio.sleep(0.12)
                    else:
                        await asyncio.sleep(0.01)
                    return [_listing(hash(self.url) % 10_000 + page)], 5

            started = time.monotonic()
            results = await engine.fetch_shards(
                [
                    {"shard_key": "fast", "search_url": "https://www.sreality.cz/fast"},
                    {"shard_key": "slow", "search_url": "https://www.sreality.cz/slow"},
                ],
                client_factory=Client,
                max_pages=1,
                deadline_sec=2.0,
            )
            elapsed = time.monotonic() - started
            pages = [item.pages_ok for item in results]
            self.assertLess(elapsed, 0.4)
            return pages

        pages = asyncio.run(_run())
        self.assertEqual(sorted(pages), [1, 1])


class AppBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "scrape.sqlite"
        self.store = Store(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_upsert_lands_in_catalog_api_table(self) -> None:
        listings = [_listing(i) for i in range(1, 6)]
        stats = self.store.upsert_catalog_listings_batch(listings, kind="seeded", commit_every=50)
        self.assertEqual(stats["new"], 5)
        catalog = self.store.catalog({"limit": 20, "offset": 0, "facets": "0", "include_pins": "0"})
        urls = {item.get("url") for item in catalog.get("items") or []}
        self.assertTrue(any("/1" in (url or "") for url in urls))
        rows = self.store.connect().execute(
            "SELECT COUNT(*) AS n FROM listings WHERE monitor_id = ?",
            (CATALOG_MONITOR_ID,),
        ).fetchone()
        self.assertGreaterEqual(int(rows["n"]), 5)

    def test_tick_is_visible_on_status_payload(self) -> None:
        tick = {
            "kind": "new_discovery",
            "ms": 42,
            "discovery": {"listings": 3, "new": 1},
            "refresh": {},
        }
        self.store.record_scrape_tick(tick)
        raw = self.store.get_meta("scrape_worker_tick")
        payload = json.loads(str(raw))
        self.assertEqual(payload["kind"], "new_discovery")
        self.assertEqual(payload["discovery"]["new"], 1)
        history = self.store.list_scrape_ticks(limit=5)
        self.assertGreaterEqual(len(history), 1)

    def test_web_role_queues_manual_scrape_in_meta(self) -> None:
        with (
            patch.object(config, "DB_PATH", self.db),
            patch.object(config, "SCRAPE_ROLE", "web"),
        ):
            hub = Hub()
            result = hub.start_scrape_search_url(
                "https://www.sreality.cz/hledani/pronajem/byty?razeni=nejnovejsi",
                max_pages=6,
            )
        self.assertTrue(result.get("queued"))
        raw = hub.store.get_meta("scrape_url_request")
        body = json.loads(str(raw))
        self.assertEqual(body["max_pages"], 6)
        self.assertIn("sreality.cz", body["url"])

    def test_web_role_queues_catalog_sync_in_meta(self) -> None:
        with (
            patch.object(config, "DB_PATH", self.db),
            patch.object(config, "SCRAPE_ROLE", "web"),
        ):
            hub = Hub()
            result = hub.start_catalog_sync(portals=["sreality"])
        self.assertTrue(result.get("queued"))
        portals = json.loads(str(hub.store.get_meta("catalog_sync_request")))
        self.assertEqual(portals, ["sreality"])

    def test_web_start_skips_crawl_loops(self) -> None:
        async def _run() -> tuple[bool, bool, bool]:
            with (
                patch.object(config, "DB_PATH", self.db),
                patch.object(config, "SCRAPE_ROLE", "web"),
                patch.object(config, "DISCORD_BOT_TOKEN", ""),
            ):
                hub = Hub()
                await hub.start()
                try:
                    return (
                        hub._recent_catalog_task is None,
                        hub._deep_catalog_task is None,
                        hub._task is not None,
                    )
                finally:
                    await hub.stop()

        recent_off, deep_off, web_loop_on = asyncio.run(_run())
        self.assertTrue(recent_off)
        self.assertTrue(deep_off)
        self.assertTrue(web_loop_on)

    def test_client_for_routes_url_to_portal_client(self) -> None:
        url = "https://reality.idnes.cz/s/pronajem/byty/"
        self.assertEqual(portal_of(url), "idnes")
        client = client_for(url)
        self.assertEqual(type(client).__name__, "IdnesClient")

    def test_monitor_index_reads_same_shape_as_upserted_listing(self) -> None:
        listing = _listing(99, locality="Praha 3 - Žižkov")
        self.store.upsert_catalog_listings_batch([listing], kind="seeded")
        monitors = [
            {
                "id": "default",
                "enabled": True,
                "portals": "sreality",
                "search_url": (
                    "https://www.sreality.cz/hledani/pronajem/byty/praha"
                    "?velikost=2%2Bkk&razeni=nejnovejsi"
                ),
                "created_at": "2020-01-01T00:00:00+00:00",
            }
        ]
        matched = MonitorIndex(monitors).matching_monitors(listing)
        self.assertTrue(any(item["id"] == "default" for item in matched))

    def test_recent_shards_cover_every_registered_portal(self) -> None:
        portals = {item["portal"] for item in recent_shards()}
        self.assertEqual(portals, set(PORTAL_IDS))
        deep = daily_shards()
        self.assertGreater(len(deep), 100)
        self.assertTrue(all("search_url" in item and "shard_key" in item for item in deep[:5]))

    def test_fetch_shards_uses_app_client_factory_contract(self) -> None:
        async def _run() -> int:
            engine = ScrapeEngine(AdaptiveLimiter(initial=4, floor=2, ceiling=4))

            class Client:
                def __init__(self, url: str) -> None:
                    self.url = url

                async def fetch_page(self, page: int, newest: bool = True):
                    if not newest:
                        raise AssertionError("fetch_page must request newest listings")
                    return [_listing(7)], 1

            results = await engine.fetch_shards(
                [{"shard_key": "app:one", "search_url": "https://www.sreality.cz/hledani/pronajem/byty"}],
                client_factory=Client,
                max_pages=1,
                deadline_sec=2.0,
            )
            return results[0].pages_ok

        self.assertEqual(asyncio.run(_run()), 1)

    def test_worker_handshake_consumes_scrape_url_request(self) -> None:
        async def _run() -> str:
            with (
                patch.object(config, "DB_PATH", self.db),
                patch.object(config, "SCRAPE_ROLE", "worker"),
            ):
                from app.scrape_worker import ScrapeWorker

                worker = ScrapeWorker()
                worker.hub.store.set_meta(
                    "scrape_url_request",
                    json.dumps({"url": "https://www.sreality.cz/hledani/pronajem/byty", "max_pages": 3}),
                )
                seen: list[tuple[str, int]] = []

                async def fake_run(url: str, pages: int) -> None:
                    seen.append((url, pages))

                worker.hub._run_scrape_search_url = fake_run  # type: ignore[method-assign]
                await worker._maybe_scrape_url_request()
                self.assertIsNone(worker.hub.store.get_meta("scrape_url_request"))
                self.assertEqual(seen[0][1], 3)
                return seen[0][0]

        url = asyncio.run(_run())
        self.assertIn("sreality.cz", url)

    def test_scrape_metrics_log_roundtrip(self) -> None:
        n = self.store.insert_scrape_metrics(
            [
                {
                    "ts": "2026-09-17T16:00:00+00:00",
                    "kind": "page",
                    "portal": "sreality",
                    "pipeline": "deep",
                    "shard_key": "sreality:test",
                    "page": 1,
                    "fetch_ms": 120.5,
                    "parse_ms": 8.2,
                    "upsert_ms": None,
                    "listings_count": 20,
                    "status_code": 200,
                    "limiter_at_call": 16,
                    "error": None,
                    "deferred": 0,
                    "has_etag": 0,
                    "has_last_modified": 0,
                }
            ]
        )
        self.assertEqual(n, 1)
        rows = self.store.load_scrape_metrics(since="2026-09-17T00:00:00+00:00")
        self.assertEqual(rows[0]["portal"], "sreality")
        self.assertEqual(rows[0]["listings_count"], 20)


if __name__ == "__main__":
    unittest.main()
