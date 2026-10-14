"""Daily catalog job: completeness with duplicate rows, transient errors, empty shards."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.monitor import Hub
from app.sreality import Listing


def _mk(i: int) -> Listing:
    return Listing(
        id=i, name=f"Byt {i}", price_czk=10000 + i, price_label="x", disposition="2+kk",
        area_m2=30 + i % 90, locality=f"Ulice {i}", url=f"https://www.sreality.cz/detail/prodej/byt/2+kk/x/{i}",
        image_url="https://i/x", lat=49 + (i % 700) * 0.005, lon=13 + (i // 700) * 0.01,
    )


_real_sleep = asyncio.sleep


async def _fast_sleep(*_a, **_k):
    await _real_sleep(0)


class FakeClient:
    """Pages of 10 rows where each page repeats 2 rows of the previous one (like Sreality)."""

    def __init__(self, pages: int, fail_once: set[int] | None = None, empty: bool = False):
        self.pages, self.fail_once, self.empty, self.calls = pages, set(fail_once or ()), empty, []
        self._blocked_status = None

    async def fetch_page(self, page: int, newest: bool = True):
        self.calls.append(page)
        if page in self.fail_once:
            self.fail_once.discard(page)
            raise RuntimeError("wall-clock 15.0s")
        if self.empty or page > self.pages:
            return [], 0
        base = (page - 1) * 8
        return [_mk(base + j) for j in range(10)], self.pages * 10


class CatalogJobTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, client: FakeClient):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "DB_PATH", Path(tmp) / "j.sqlite"), \
                patch.object(config, "SCRAPE_ROLE", "worker"):
            hub = Hub()
            job = hub.store.ensure_scrape_job(
                kind="catalog_daily", portal="sreality", shard_key="sreality:t",
                search_url="https://www.sreality.cz/hledani/prodej/byty/jihocesky-kraj?razeni=nejnovejsi",
            )
            with patch.object(Hub, "client_for", lambda self, url: client), \
                    patch("asyncio.sleep", new=_fast_sleep):
                ok = await hub._run_catalog_job(job)
            status = hub.store.ensure_scrape_job(
                kind="catalog_daily", portal="sreality", shard_key="sreality:t", search_url=job["search_url"]
            )["status"]
            return ok, status

    async def test_duplicate_rows_still_complete(self):
        ok, status = await self._run(FakeClient(pages=12))
        self.assertTrue(ok)
        self.assertEqual(status, "done")

    async def test_transient_error_is_retried(self):
        client = FakeClient(pages=6, fail_once={3})
        ok, _ = await self._run(client)
        self.assertTrue(ok)

    async def test_empty_shard_is_not_an_error(self):
        ok, status = await self._run(FakeClient(pages=0, empty=True))
        self.assertTrue(ok)
        self.assertEqual(status, "done")


if __name__ == "__main__":
    unittest.main()
