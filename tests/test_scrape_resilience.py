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


class PendingSoldQueryTests(unittest.TestCase):
    """pending_sold_pings must walk the primary key of real monitors, never scan the millions of catalog rows
    (no extra index: building one on the production DB holds the write lock for minutes)."""

    def test_query_plan_searches_by_primary_key_without_full_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite")
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
                self.assertEqual(store.pending_sold_pings(limit=10), [])
            plan = " ".join(seen)
            self.assertIn("SEARCH listings USING", plan)
            self.assertNotIn("SCAN listings", plan)

    def test_returns_only_real_monitor_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite")
            with store.connect() as conn:
                cols = [row[1] for row in conn.execute("PRAGMA table_info(listings)")]
                monitor = conn.execute("SELECT id FROM monitors LIMIT 1").fetchone()
                self.assertIsNotNone(monitor)
                base = {"name": "x", "url": "u", "first_seen": "2026-01-01", "last_seen": "2026-01-02", "gone": 1, "sold_notified": 0}
                for mid, lid in ((monitor[0], 1), ("__catalog__", 2)):
                    row = {**base, "monitor_id": mid, "id": lid, "url": f"u{lid}"}
                    keys = [k for k in row if k in cols]
                    conn.execute(
                        f"INSERT INTO listings({','.join(keys)}) VALUES ({','.join('?' * len(keys))})", [row[k] for k in keys]
                    )
            ids = {row["id"] for row in store.pending_sold_pings(limit=10)}
            self.assertEqual(ids, {1})


class DiscoveryNeverFreezesTests(unittest.IsolatedAsyncioTestCase):
    async def test_locked_database_defers_the_write_instead_of_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")

            async def hung_job(fn, *args, **kwargs):
                await asyncio.sleep(3600)  # another process holds the SQLite write lock

            from app.monitor import Listing

            listings = [
                Listing(id=i, name="n", url=f"https://x/{i}", price_czk=1, price_label="1 Kč", disposition="2+kk",
                        area_m2=40, locality="Praha", image_url="")
                for i in range(3)
            ]
            with patch.object(hub, "_job_db", new=hung_job):
                started = time.monotonic()
                totals = await hub._catalog_upsert(listings, kind="refresh", chunk_timeout_sec=0.2)
                elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.0)
            self.assertEqual(totals["deferred_write"], len(listings))
            # the write gate is free again, so the next portal is not stuck behind the hung write
            self.assertFalse(hub._catalog_write.locked())

    async def test_tick_recording_does_not_block_the_poll_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            release = asyncio.Event()
            calls: list[int] = []

            async def slow_record(tick):
                calls.append(1)
                await release.wait()

            with patch.object(hub, "_record_tick", new=slow_record):
                started = time.monotonic()
                hub._record_tick_background({"at": "now"})
                hub._record_tick_background({"at": "dropped while one is in flight"})
                self.assertLess(time.monotonic() - started, 0.1)
                await asyncio.sleep(0.05)
                self.assertEqual(len(calls), 1)  # single flight
                release.set()
                await asyncio.sleep(0.05)
                hub._record_tick_background({"at": "next"})
                await asyncio.sleep(0.05)
            self.assertEqual(len(calls), 2)


class ChunkedStaleSweepTests(unittest.TestCase):
    def test_sweep_marks_only_stale_rows_in_small_commits(self):
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite")
            old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
            fresh = datetime.now(timezone.utc).isoformat()
            with store.connect() as conn:
                for i in range(25):
                    conn.execute(
                        "INSERT INTO catalog_listings(listing_key, id, name, url, first_seen, last_seen, gone)"
                        " VALUES (?, ?, 'n', 'u', '2026-01-01', ?, 0)",
                        (f"k{i}", i, old if i < 20 else fresh),
                    )
                conn.execute(
                    "INSERT INTO catalog_listings(listing_key, id, name, url, first_seen, last_seen, gone)"
                    " VALUES ('knull', 99, 'n', 'u', '2026-01-01', NULL, 0)"
                )
            commits: list[int] = []
            real_connect = store.connect

            def counting_connect(*a, **k):
                commits.append(1)
                return real_connect(*a, **k)

            with patch.object(store, "connect", counting_connect):
                n = store.sweep_catalog_stale_gone(72, chunk=100, pause_sec=0)
            self.assertEqual(n, 21)  # 20 old + the NULL last_seen row
            with real_connect() as conn:
                live = {row[0] for row in conn.execute("SELECT listing_key FROM catalog_listings WHERE gone = 0")}
            self.assertEqual(live, {f"k{i}" for i in range(20, 25)})
            self.assertGreaterEqual(len(commits), 3)  # several separate transactions, not one giant UPDATE

    def test_chunking_splits_a_large_backlog(self):
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "m.sqlite")
            old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
            with store.connect() as conn:
                conn.executemany(
                    "INSERT INTO catalog_listings(listing_key, id, name, url, first_seen, last_seen, gone)"
                    " VALUES (?, ?, 'n', 'u', '2026-01-01', ?, 0)",
                    [(f"k{i}", i, old) for i in range(350)],
                )
            statements: list[str] = []
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
                    if sql.startswith("UPDATE catalog_listings"):
                        statements.append(sql)
                    return self._conn.execute(sql, params)

            with patch.object(store, "connect", lambda *a, **k: Spy(real_connect(*a, **k))):
                self.assertEqual(store.sweep_catalog_stale_gone(72, chunk=100, pause_sec=0), 350)
            self.assertGreaterEqual(len(statements), 4)  # 100+100+100+50 (+ NULL pass)


class DiscoveryWritesOnlyChangesTests(unittest.IsolatedAsyncioTestCase):
    def _listing(self, i, price=1000):
        from app.monitor import Listing

        return Listing(id=i, name="n", url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{i}", price_czk=price,
                       price_label=f"{price} Kč", disposition="2+kk", area_m2=40, locality="Praha", image_url="")

    async def test_known_unchanged_listings_are_not_written_but_new_and_changed_are(self):
        from app import scrape_fast
        from app.identity import listing_key

        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            path = str(hub.store.path)
            known, changed, new = self._listing(1), self._listing(2), self._listing(3)
            cache = scrape_fast.cache_for(path, _EmptyConn())
            cache[listing_key(known.url)] = scrape_fast.listing_fingerprint(known)
            cache[listing_key(changed.url)] = scrape_fast.listing_fingerprint(self._listing(2, price=900))  # price differs
            to_write, skipped = hub._discovery_changes_only([known, changed, new])
            self.assertEqual([item.id for item in to_write], [2, 3])
            self.assertEqual(skipped, 1)

    async def test_known_listing_is_touched_again_after_the_ttl(self):
        from app import scrape_fast
        from app.identity import listing_key

        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            known = self._listing(1)
            cache = scrape_fast.cache_for(str(hub.store.path), _EmptyConn())
            cache[listing_key(known.url)] = scrape_fast.listing_fingerprint(known)
            self.assertEqual(hub._discovery_changes_only([known])[1], 1)
            hub._discovery_touched[listing_key(known.url)] = time.monotonic() - config.SCRAPE_DISCOVERY_TOUCH_SEC - 1
            self.assertEqual([i.id for i in hub._discovery_changes_only([known])[0]], [1])
            hub._discovery_mark_touched([known])
            self.assertEqual(hub._discovery_changes_only([known])[1], 1)

    async def test_without_a_cache_everything_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            items = [self._listing(1), self._listing(2)]
            self.assertEqual(hub._discovery_changes_only(items), (items, 0))

    async def test_partially_written_batch_keeps_its_tail_in_the_backlog(self):
        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            batch = [self._listing(i) for i in range(1, 6)]

            async def fake_fetch(self_engine, shards, **_kw):  # noqa: ANN001
                from app.scrape_engine import ShardFetchResult

                return [ShardFetchResult(shard_key="s", search_url="https://www.sreality.cz/x", listings=batch, total=5,
                                         pages_ok=1, deferred_pages=[], error=None, portal="sreality")]

            async def fake_upsert(listings, **_kw):
                return {"n": 3, "new": 3, "updated": 0, "same": 0, "deferred_write": 2}, None

            shard = [{"shard_key": "s", "search_url": "https://www.sreality.cz/x", "kind": "recent", "portal": "sreality"}]
            with patch("app.monitor.recent_shards", return_value=shard), \
                 patch("app.scrape_engine.ScrapeEngine.fetch_shards", new=fake_fetch), \
                 patch.object(hub, "_try_catalog_upsert", new=fake_upsert), \
                 patch.object(hub, "_record_tick_background", new=lambda tick: None):
                await hub._recent_catalog_tick("sreality")
            self.assertEqual(sorted(hub._discovery_backlog), [4, 5])  # the two that were not written


class _EmptyConn:
    def execute(self, *_a, **_k):
        return []


class BoundedFingerprintCacheTests(unittest.TestCase):
    def test_limit_loads_only_the_newest_rows(self):
        from app import scrape_fast

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE catalog_listings(listing_key, price_czk, name, disposition, area_m2, locality, edited_on,"
            " old_price_czk, lat, image_url, gone, last_seen)"
        )
        for i in range(10):
            conn.execute(
                "INSERT INTO catalog_listings VALUES (?,?,?,?,?,?,?,?,?,?,0,?)",
                (f"k{i}", 1, "n", "2+kk", 1, "l", None, None, None, "", f"2026-01-{i + 1:02d}"),
            )
        cache = scrape_fast.cache_for("bounded-test", conn, limit=3)
        self.assertEqual(set(cache), {"k9", "k8", "k7"})


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

    async def test_discovery_warms_the_fingerprint_cache_before_polling(self):
        from app import scrape_fast

        with tempfile.TemporaryDirectory() as tmp:
            hub = Hub(Path(tmp) / "m.sqlite")
            path = str(hub.store.path)
            self.assertIsNone(scrape_fast.peek(path))
            order: list[str] = []
            real_warm = hub._warm_fast_cache

            async def warm_then_note():
                await real_warm()
                order.append("warm")

            async def fake_tick(portal=None):
                order.append("tick")
                hub.running = False
                return True

            hub.running = True
            with patch("app.monitor.recent_shards", return_value=self._shards()), \
                 patch.object(hub, "_warm_fast_cache", new=warm_then_note), \
                 patch.object(hub, "_recent_catalog_tick", new=fake_tick), \
                 patch.object(hub, "run_due_scrape_schedules", new=lambda: asyncio.sleep(0)):
                task = asyncio.ensure_future(hub._recent_catalog_loop())
                await asyncio.sleep(2.6)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(order[0], "warm")
            self.assertIsNotNone(scrape_fast.peek(path))

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
