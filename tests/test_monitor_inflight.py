"""A slow portal's live poll must not hold back the other portals' monitor ticks."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.monitor import Hub


class InflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_job_is_not_restarted_and_does_not_block_fast_ones(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "m.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"), patch.object(config, "SCRAPE_MONITOR_TICK_WAIT_SEC", 0.3):
            hub = Hub()
            calls: dict[str, int] = {}
            gate = asyncio.Event()

            async def fake_check(job, monitors):
                portal_key = job["search_url"].split("/")[2]
                calls[portal_key] = calls.get(portal_key, 0) + 1
                if "ulovdomov" in portal_key:
                    await gate.wait()  # slow portal
                return {"job_id": job["id"], "scanned": 1, "new": [], "errors": []}

            jobs = [
                {"id": "fast", "search_url": "https://www.sreality.cz/x"},
                {"id": "slow", "search_url": "https://www.ulovdomov.cz/x"},
            ]
            monitor = {"id": "m1", "enabled": True, "seeded": True, "last_check": None}
            with patch.object(hub, "check_live_job", fake_check), \
                    patch.object(hub.store, "list_monitors_light", lambda: [monitor]), \
                    patch.object(hub.store, "attach_monitor_live_jobs", lambda m: jobs):
                first = await hub.check_due()
                second = await hub.check_due()
            self.assertEqual(len(first["results"]), 1)  # only the fast one finished in time
            self.assertEqual(calls.get("www.ulovdomov.cz"), 1)  # slow poll not duplicated
            self.assertEqual(calls.get("www.sreality.cz"), 2)  # fast one polled again
            gate.set()
            await hub.stop()


if __name__ == "__main__":
    unittest.main()


class ClientCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_cache_is_bounded_and_detail_clients_are_shared(self):
        import tempfile as _tf

        closed: list[object] = []

        class C:
            async def aclose(self):
                closed.append(self)

        with _tf.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "c.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"), patch.object(config, "CLIENT_CACHE_MAX", 8), \
                patch("app.monitor.client_for", lambda url: C()):
            hub = Hub()
            for i in range(30):
                hub.client_for(f"https://www.sreality.cz/hledani/prodej/byty/x{i}")
            self.assertLessEqual(len(hub.clients), 8)
            hub.clients.clear()
            for i in range(50):
                hub.detail_client_for(f"https://www.sreality.cz/detail/prodej/byt/2+kk/praha/{i}")
            self.assertEqual(len(hub.clients), 1)  # one shared detail client, not one per listing
