"""Shared live polls (app/live_share.py) and the discovery write backlog."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.live_share import LiveShare
from app.monitor import Hub
from app.scrape_engine import ShardFetchResult
from app.sreality import Listing


def _listing(i: int) -> Listing:
    return Listing(
        id=i, name=f"Byt {i}", price_czk=10000 + i, price_label="x", disposition="2+kk",
        area_m2=40, locality="Praha", url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/x/{i}",
        image_url="https://i/x", lat=50.0, lon=14.4,
    )


def _result(*ids: int, error: str | None = None) -> list[ShardFetchResult]:
    return [ShardFetchResult(shard_key="k", search_url="u", listings=[_listing(i) for i in ids],
                             total=len(ids), pages_ok=1, deferred_pages=[], error=error)]


class LiveShareTest(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_callers_share_one_fetch(self) -> None:
        share = LiveShare()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return _result(1, 2)

        out = await asyncio.gather(*[share.get(("u", 1), 10, fetch) for _ in range(8)])
        self.assertEqual(calls, 1)
        self.assertEqual([len(item[0].listings) for item in out], [2] * 8)
        self.assertEqual(share.joined, 7)

    async def test_callers_get_independent_copies(self) -> None:
        share = LiveShare()

        async def fetch():
            return _result(1)

        first, second = await asyncio.gather(share.get("k", 10, fetch), share.get("k", 10, fetch))
        first[0].listings[0].name = "changed"
        self.assertEqual(second[0].listings[0].name, "Byt 1")
        third = await share.get("k", 10, fetch)
        self.assertEqual(third[0].listings[0].name, "Byt 1")

    async def test_result_reused_within_ttl_then_refetched(self) -> None:
        share = LiveShare()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return _result(calls)

        await share.get("k", 0.2, fetch)
        await share.get("k", 0.2, fetch)
        self.assertEqual(calls, 1)
        await asyncio.sleep(0.25)
        await share.get("k", 0.2, fetch)
        self.assertEqual(calls, 2)

    async def test_errors_are_not_cached(self) -> None:
        share = LiveShare()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return _result(error="boom")

        cacheable = lambda res: not res[0].error
        await share.get("k", 10, fetch, cacheable=cacheable)
        await share.get("k", 10, fetch, cacheable=cacheable)
        self.assertEqual(calls, 2)

    async def test_ttl_zero_disables_sharing(self) -> None:
        share = LiveShare()
        calls = 0

        async def fetch():
            nonlocal calls
            calls += 1
            return _result(1)

        await asyncio.gather(share.get("k", 0, fetch), share.get("k", 0, fetch))
        self.assertEqual(calls, 2)

    async def test_waiter_timeout_does_not_cancel_shared_fetch(self) -> None:
        share = LiveShare()
        done = asyncio.Event()

        async def fetch():
            await asyncio.sleep(0.1)
            done.set()
            return _result(1)

        impatient = asyncio.ensure_future(asyncio.wait_for(share.get("k", 10, fetch), timeout=0.02))
        patient = asyncio.ensure_future(share.get("k", 10, fetch))
        with self.assertRaises(asyncio.TimeoutError):
            await impatient
        result = await patient
        self.assertTrue(done.is_set())
        self.assertEqual(len(result[0].listings), 1)


class DiscoveryBacklogTest(unittest.IsolatedAsyncioTestCase):
    async def test_deferred_discovery_write_is_retried_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            attempts: list[list[int]] = []

            async def fake_fetch(self_engine, shards, **_kw):  # noqa: ANN001
                return _result(1, 2, 3) if not attempts else _result(4)

            async def fake_upsert(listings, **_kw):
                attempts.append(sorted(item.id for item in listings))
                if len(attempts) == 1:
                    return None, "write-deferred:catalog-busy"
                return {"n": len(listings), "new": len(listings), "updated": 0, "same": 0, "deferred_write": 0}, None

            shard = [{"shard_key": "s", "search_url": "https://www.sreality.cz/hledani/pronajem/byty", "kind": "recent",
                      "portal": "sreality"}]
            with patch("app.monitor.recent_shards", return_value=shard), \
                 patch("app.scrape_engine.ScrapeEngine.fetch_shards", new=fake_fetch), \
                 patch.object(hub, "_try_catalog_upsert", new=fake_upsert), \
                 patch.object(hub, "_record_tick", new=lambda tick: asyncio.sleep(0)):
                await hub._recent_catalog_tick()
                self.assertEqual(len(hub._discovery_backlog), 3)
                await hub._recent_catalog_tick()
            self.assertEqual(attempts, [[1, 2, 3], [1, 2, 3, 4]])
            self.assertEqual(len(hub._discovery_backlog), 0)


class PauseFlagTest(unittest.IsolatedAsyncioTestCase):
    async def _run_monitor_loop(self, pause: bool) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            hub.running = True
            hub.catalog_running = True
            calls = 0

            async def fake_check_due():
                nonlocal calls
                calls += 1
                hub.running = False
                return {"ok": True, "results": []}

            with patch.object(config, "SCRAPE_PAUSE_PRIORITY_DURING_CATALOG", pause), \
                 patch.object(hub, "check_due", new=fake_check_due), \
                 patch.object(hub, "maybe_send_digest", new=lambda: asyncio.sleep(0)):
                task = asyncio.ensure_future(hub._loop())
                await asyncio.sleep(1.6)
                hub.running = False
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            return calls

    async def test_monitor_priority_runs_during_catalog_sync_by_default(self) -> None:
        self.assertFalse(config.SCRAPE_PAUSE_PRIORITY_DURING_CATALOG)
        self.assertGreaterEqual(await self._run_monitor_loop(False), 1)

    async def test_pause_flag_restores_old_behaviour(self) -> None:
        self.assertEqual(await self._run_monitor_loop(True), 0)


if __name__ == "__main__":
    unittest.main()
