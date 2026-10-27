"""A locked catalog DB must make one monitor poll give up, not hold the write gate for every other poll."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import config
from app.monitor import Hub


class MonitorWriteTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_write_returns_error_and_frees_gate(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "m.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"), patch.object(config, "SCRAPE_MONITOR_WRITE_TIMEOUT_SEC", 0.3):
            hub = Hub()
            release = threading.Event()
            listing = SimpleNamespace(id=1, url="https://www.sreality.cz/detail/1")
            fetched = SimpleNamespace(listings=[listing], total=1, error=None)

            def blocked_write(*args, **kwargs):
                release.wait(5)
                return []

            async def fake_live(key, ttl, fn, cacheable=None):
                return (fetched,)

            job = {"id": "j1", "search_url": "https://www.sreality.cz/hledani/pronajem/byty"}
            monitor = {"id": "m1", "enabled": True}
            try:
                with patch("app.monitor.shared_live", SimpleNamespace(get=fake_live)), \
                        patch.object(hub.store, "upsert_catalog_listings_results", blocked_write):
                    result = await hub.check_live_job(job, [monitor])
                self.assertEqual(result["scanned"], 0)
                self.assertTrue(result["errors"][0].startswith("Catalog write busy"))
                self.assertFalse(hub._catalog_write.locked())  # gate released for the other polls
            finally:
                release.set()
                await hub.stop()

    async def test_abandoned_writes_do_not_pile_up_in_the_pool(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "m.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"), patch.object(config, "SCRAPE_MONITOR_WRITE_TIMEOUT_SEC", 0.2), \
                patch.object(config, "SCRAPE_MONITOR_WRITES_MAX_INFLIGHT", 1):
            hub = Hub()
            release = threading.Event()
            started = []
            listing = SimpleNamespace(id=1, url="https://www.sreality.cz/detail/1")
            fetched = SimpleNamespace(listings=[listing], total=1, error=None)

            def blocked_write(*args, **kwargs):
                started.append(1)
                release.wait(5)
                return []

            async def fake_live(key, ttl, fn, cacheable=None):
                return (fetched,)

            job = {"id": "j1", "search_url": "https://www.sreality.cz/hledani/pronajem/byty"}
            try:
                with patch("app.monitor.shared_live", SimpleNamespace(get=fake_live)), \
                        patch.object(hub.store, "upsert_catalog_listings_results", blocked_write):
                    for _ in range(4):
                        result = await hub.check_live_job(job, [{"id": "m1", "enabled": True}])
                        self.assertTrue(result["errors"][0].startswith("Catalog write busy"))
                self.assertEqual(len(started), 1)  # the later polls never queued another pool thread
            finally:
                release.set()
                await hub.stop()


if __name__ == "__main__":
    unittest.main()
