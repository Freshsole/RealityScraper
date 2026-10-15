"""Admin 'run catalog / scrape URL' requests are written by the web process into the GLOBAL store,
but the catalog pipelines run on the owner's per-user store: the worker must pick them up from both."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import config
from app.monitor import Hub
from app.scrape_worker import ScrapeWorker


class WorkerRequestTests(unittest.IsolatedAsyncioTestCase):
    async def _setup(self, tmp: str):
        worker = ScrapeWorker()
        owner = Hub(store_path=Path(tmp) / "users" / "owner" / "monitor.sqlite", shared=worker.hub)
        owner.catalog_owner = True
        owner.run_catalog_sync = AsyncMock(return_value={"ok": True})
        owner._run_scrape_search_url = AsyncMock(return_value=None)
        return worker, owner

    async def test_catalog_request_in_global_store_is_picked_up(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "g.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"):
            worker, owner = await self._setup(tmp)
            worker.hub.store.set_meta("catalog_sync_request", json.dumps(["idnes", "bazos"]))
            await worker._maybe_forced_catalog(owner)
            for _ in range(5):
                import asyncio

                await asyncio.sleep(0)
            owner.run_catalog_sync.assert_called_once()
            self.assertEqual(owner.run_catalog_sync.call_args.kwargs["portals"], ["bazos", "idnes"])
            self.assertFalse(worker.hub.store.get_meta("catalog_sync_request"))

    async def test_catalog_request_in_owner_store_still_works(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "g.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"):
            worker, owner = await self._setup(tmp)
            owner.store.set_meta("catalog_sync_request", json.dumps(["remax"]))
            await worker._maybe_forced_catalog(owner)
            import asyncio

            await asyncio.sleep(0)
            owner.run_catalog_sync.assert_called_once()
            self.assertFalse(owner.store.get_meta("catalog_sync_request"))

    async def test_scrape_url_request_in_global_store_runs_on_owner(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "g.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"):
            worker, owner = await self._setup(tmp)
            worker.hub.store.set_meta(
                "scrape_url_request", json.dumps({"url": "https://www.sreality.cz/hledani/pronajem/byty", "max_pages": 7})
            )
            await worker._maybe_scrape_url_request(owner)
            owner._run_scrape_search_url.assert_awaited_once_with("https://www.sreality.cz/hledani/pronajem/byty", 7)
            self.assertFalse(worker.hub.store.get_meta("scrape_url_request"))


if __name__ == "__main__":
    unittest.main()
