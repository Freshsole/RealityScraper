"""Scraper resilience: the event loop must never be blocked, a stalled worker must restart itself and
discovery must be independent per portal (one slow portal may not delay fresh listings of the others)."""

import ast
import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config, container
from app.monitor import Hub
from app.scrape_worker import ScrapeWorker
from app.store import Store


class LoopGuardTests(unittest.TestCase):
    def test_exits_after_stall_and_not_before(self):
        exits: list[int] = []
        guard = container.LoopGuard(0.2, exit_fn=exits.append)
        guard.beat()
        self.assertFalse(guard.check())
        time.sleep(0.25)
        self.assertTrue(guard.check())
        self.assertEqual(exits, [container.LoopGuard.EXIT_CODE])

    def test_beat_resets_the_clock(self):
        exits: list[int] = []
        guard = container.LoopGuard(0.3, exit_fn=exits.append)
        time.sleep(0.2)
        guard.beat()
        time.sleep(0.2)
        self.assertFalse(guard.check())
        self.assertEqual(exits, [])

    def test_thread_fires_on_a_blocked_loop(self):
        exits: list[int] = []
        guard = container.LoopGuard(0.4, exit_fn=exits.append)
        guard.start()
        try:
            time.sleep(1.5)  # nobody beats: this is what a loop blocked in a sync SQLite call looks like
        finally:
            guard.stop()
        self.assertEqual(exits[:1], [container.LoopGuard.EXIT_CODE])

    def test_zero_disables(self):
        guard = container.LoopGuard(0)
        guard.start()
        self.assertIsNone(guard._thread)

    def test_beat_loop_keeps_a_healthy_loop_alive(self):
        exits: list[int] = []
        guard = container.LoopGuard(0.5, exit_fn=exits.append)

        async def scenario() -> None:
            task = asyncio.ensure_future(guard.beat_loop())
            await asyncio.sleep(1.3)
            self.assertFalse(guard.check())
            task.cancel()

        asyncio.run(scenario())
        self.assertEqual(exits, [])


class NoBlockingStoreCallsTests(unittest.TestCase):
    """A sync `self.store.x()` inside `async def` runs SQLite on the event loop. One slow query froze every
    portal fetch of the worker for 7 minutes (all of them timed out together). Go through `_job_db`."""

    def test_monitor_has_no_sync_store_calls_in_coroutines(self):
        source = (Path(config.__file__).parent / "monitor.py").read_text(encoding="utf-8")
        offenders: list[str] = []

        def visit(node: ast.AST, in_async: bool, fn: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.AsyncFunctionDef):
                    visit(child, True, child.name)
                elif isinstance(child, (ast.FunctionDef, ast.Lambda)):
                    visit(child, False, getattr(child, "name", "<lambda>"))
                else:
                    func = getattr(child, "func", None)
                    if (
                        in_async
                        and isinstance(child, ast.Call)
                        and isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "store"
                        and isinstance(func.value.value, ast.Name)
                        and func.value.value.id == "self"
                    ):
                        offenders.append(f"{fn}: self.store.{func.attr} (line {child.lineno})")
                    visit(child, in_async, fn)

        visit(ast.parse(source), False, "<module>")
        self.assertEqual(offenders, [])


class PendingSoldIndexTests(unittest.TestCase):
    def test_background_index_exists_and_query_still_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite")
            store.ensure_background_indexes()
            conn = sqlite3.connect(store.path)
            try:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            finally:
                conn.close()
            self.assertIn("idx_listings_sold_pending", names)
            self.assertEqual(store.pending_sold_pings(limit=10), [])

    def test_query_plan_uses_the_partial_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite")
            store.ensure_background_indexes()
            seen: list[str] = []
            real_connect = store.connect

            class Spy:
                def __init__(self, conn):
                    self._conn = conn

                def __enter__(self):
                    self._conn.__enter__()
                    return self

                def __exit__(self, *exc):
                    return self._conn.__exit__(*exc)

                def execute(self, sql, params=()):
                    if "sold_notified" in sql:
                        plan = self._conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
                        seen.extend(str(tuple(row)) for row in plan)
                    return self._conn.execute(sql, params)

            with patch.object(store, "connect", lambda *a, **k: Spy(real_connect(*a, **k))):
                store.pending_sold_pings(limit=10)
            self.assertTrue(any("idx_listings_sold_pending" in line for line in seen), seen)


class PerPortalDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def _shards(self):
        return [
            {"shard_key": "a", "search_url": "https://slow.example/a", "kind": "recent", "portal": "slow"},
            {"shard_key": "b", "search_url": "https://fast.example/b", "kind": "recent", "portal": "fast"},
        ]

    async def test_slow_portal_does_not_delay_a_fast_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            hub.running = True
            calls = {"slow": 0, "fast": 0}

            async def fake_tick(portal=None):
                calls[portal] += 1
                if portal == "slow":
                    await asyncio.sleep(3600)  # stuck portal (iDNES ReadTimeout in production)
                return True

            with patch("app.monitor.recent_shards", return_value=self._shards()), \
                 patch.object(config, "SCRAPE_DISCOVERY_PORTAL_LOOP_SEC", 0.05), \
                 patch.object(config, "SCRAPE_DISCOVERY_SEC_PER_SHARD", 0.0), \
                 patch.object(hub, "_recent_catalog_tick", new=fake_tick), \
                 patch.object(hub, "run_due_scrape_schedules", new=lambda: asyncio.sleep(0)):
                task = asyncio.ensure_future(hub._recent_catalog_loop())
                await asyncio.sleep(2.6)  # 2 s start-up delay in the loop + polling time
                hub.running = False
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(calls["slow"], 1)
            self.assertGreaterEqual(calls["fast"], 3)

    async def test_failing_portal_backs_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            hub.running = True
            stamps: list[float] = []

            async def failing_tick(portal=None):
                stamps.append(time.monotonic())
                return False

            with patch.object(config, "SCRAPE_DISCOVERY_PORTAL_LOOP_SEC", 0.1), \
                 patch.object(hub, "_recent_catalog_tick", new=failing_tick):
                task = asyncio.ensure_future(hub._portal_discovery_loop("fast"))
                await asyncio.sleep(2.0)
                hub.running = False
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            gaps = [b - a for a, b in zip(stamps, stamps[1:])]
            self.assertGreaterEqual(len(gaps), 3)
            self.assertGreater(gaps[2], gaps[0] * 1.5)

    async def test_discovery_portals_respects_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            with patch("app.monitor.recent_shards", return_value=self._shards()):
                self.assertEqual(hub._discovery_portals(), ["slow", "fast"])
                with patch.object(config, "DISCOVERY_PORTALS", ("fast",)):
                    self.assertEqual(hub._discovery_portals(), ["fast"])

    async def test_interval_stretches_with_shard_count_and_honours_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            many = [{"shard_key": str(i), "search_url": f"https://big.example/{i}", "kind": "recent", "portal": "big"}
                    for i in range(26)] + self._shards()
            with patch("app.monitor.recent_shards", return_value=many), \
                 patch.object(config, "SCRAPE_DISCOVERY_PORTAL_LOOP_SEC", 3.0), \
                 patch.object(config, "SCRAPE_DISCOVERY_SEC_PER_SHARD", 0.4):
                self.assertAlmostEqual(hub._discovery_interval("big"), 10.4)
                self.assertEqual(hub._discovery_interval("fast"), 3.0)
                with patch.object(config, "SCRAPE_DISCOVERY_PORTAL_INTERVALS", {"big": 7.0}):
                    self.assertEqual(hub._discovery_interval("big"), 7.0)

    async def test_tick_records_freshness_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            from app.scrape_engine import ShardFetchResult

            async def fake_fetch(self_engine, shards, **_kw):  # noqa: ANN001
                return [ShardFetchResult(shard_key="b", search_url="https://fast.example/b", listings=[], total=0,
                                         pages_ok=2, deferred_pages=[], error=None, portal="fast")]

            with patch("app.monitor.recent_shards", return_value=self._shards()), \
                 patch("app.scrape_engine.ScrapeEngine.fetch_shards", new=fake_fetch), \
                 patch.object(hub, "_record_tick", new=lambda tick: asyncio.sleep(0)):
                ok = await hub._recent_catalog_tick("fast")
            self.assertTrue(ok)
            state = hub.discovery_state["fast"]
            self.assertEqual(state["fails"], 0)
            self.assertIn("last_ok_at", state)
            self.assertNotIn("slow", hub.discovery_state)


class PipelineSplitTests(unittest.TestCase):
    def test_default_runs_everything(self):
        with patch.object(config, "WORKER_PIPELINES", frozenset({"all"})):
            for name in ("discovery", "deep", "sold", "monitors", "maint"):
                self.assertTrue(ScrapeWorker.runs(name))

    def test_discovery_only_process(self):
        with patch.object(config, "WORKER_PIPELINES", frozenset({"discovery"})):
            self.assertTrue(ScrapeWorker.runs("discovery"))
            for name in ("deep", "sold", "monitors", "maint", "dedupe"):
                self.assertFalse(ScrapeWorker.runs(name))

    def test_main_worker_without_discovery(self):
        with patch.object(config, "WORKER_PIPELINES", frozenset({"monitors", "deep", "sold", "coords", "dedupe", "maint"})):
            self.assertFalse(ScrapeWorker.runs("discovery"))
            self.assertTrue(ScrapeWorker.runs("deep"))


if __name__ == "__main__":
    unittest.main()
