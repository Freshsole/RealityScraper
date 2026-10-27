"""Unchanged listings only get last_seen touched; changed ones take the full path."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app import scrape_fast
from app.identity import listing_key
from app.sreality import Listing
from app.store import Store


def _mk(i: int, price: int = 15000) -> Listing:
    return Listing(
        id=2000 + i,
        name=f"Byt {i} 2+kk",
        price_czk=price,
        price_label=f"{price} Kč/měsíc",
        disposition="2+kk",
        area_m2=50,
        locality="Praha 1",
        url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{2000 + i}",
        image_url="https://img.example/x.jpg",
        lat=50.1,
        lon=14.4,
    )


class FastTouchTests(unittest.TestCase):
    def test_touch_and_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "t.sqlite")
            items = [_mk(i) for i in range(10)]
            store.upsert_catalog_listings_batch(items, kind="seeded")
            r1 = store.upsert_catalog_listings_batch(items, kind="refresh")  # loads cache, full path
            r2 = store.upsert_catalog_listings_batch(items, kind="refresh")  # all touched
            self.assertEqual((r2["new"], r2["updated"], r2["same"]), (0, 0, 10))
            self.assertTrue(scrape_fast.is_known(str(store.path), listing_key(items[0].url), items[0]))

            conn = sqlite3.connect(store.path)
            conn.execute("UPDATE catalog_listings SET gone = 1, last_seen = '2000-01-01'")
            conn.commit()
            store.upsert_catalog_listings_batch(items, kind="refresh")
            row = conn.execute("SELECT min(gone), min(last_seen) FROM catalog_listings").fetchone()
            self.assertEqual(row[0], 0)
            self.assertGreater(row[1], "2020")

            items[3] = replace(items[3], price_czk=12000)
            r3 = store.upsert_catalog_listings_batch(items, kind="refresh")
            self.assertEqual((r3["updated"], r3["same"]), (1, 9))
            self.assertFalse(scrape_fast.is_known(str(store.path), listing_key(_mk(3).url), _mk(3)))
            price = conn.execute(
                "SELECT price_czk FROM catalog_listings WHERE listing_key = ?", (listing_key(items[3].url),)
            ).fetchone()[0]
            self.assertEqual(price, 12000)
            conn.close()


class MonitorResultsTests(unittest.TestCase):
    def test_results_path_skips_unchanged_but_reports_price_change(self):
        from app.monitor import snapshot_price_change

        def varied(i: int, price: int = 15000) -> Listing:
            base = _mk(i, price)
            return replace(base, area_m2=30 + i, lat=49 + i * 0.01, lon=14 + i * 0.01, locality=f"Ulice {i}")

        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "r.sqlite")
            items = [varied(i) for i in range(20)]
            first = store.upsert_catalog_listings_results(items, kind="refresh")
            self.assertEqual(len(first), 20)
            second = store.upsert_catalog_listings_results(items, kind="refresh")
            self.assertEqual(len(second), 20)
            third = store.upsert_catalog_listings_results(items, kind="refresh")
            # unchanged rows come back with a price-equal prev, so the monitor loop skips them
            for item, listing in zip(third, items):
                self.assertIsNotNone(item["prev"])
                self.assertEqual(snapshot_price_change(item["prev"], listing), [])
            changed = list(items)
            changed[5] = varied(5, 12000)
            fourth = store.upsert_catalog_listings_results(changed, kind="refresh")
            self.assertTrue(snapshot_price_change(fourth[5]["prev"], changed[5]))  # 15000 -> 12000 detected
            self.assertEqual(snapshot_price_change(fourth[4]["prev"], changed[4]), [])
            # a brand new listing is still reported as new (prev is None)
            fresh = store.upsert_catalog_listings_results([varied(99)], kind="refresh")
            self.assertIsNone(fresh[0]["prev"])


class EarlyStopTests(unittest.IsolatedAsyncioTestCase):
    async def _crawl(self, known):
        import time

        from app.scrape_engine import ScrapeEngine

        calls: list[int] = []

        async def fetch_page(page: int):
            calls.append(page)
            return [_mk(page * 10 + j) for j in range(5)], 100

        engine = ScrapeEngine(pipeline="discovery")
        await engine.fetch_pages_parallel(
            shard_key="s",
            fetch_page=fetch_page,
            max_pages=4,
            deadline_monotonic=time.monotonic() + 30,
            portal="sreality",
            known_fn=known,
        )
        return calls

    async def test_stops_when_page_one_is_known(self):
        self.assertEqual(await self._crawl(lambda item: True), [1])

    async def test_goes_deeper_when_something_is_new(self):
        self.assertEqual(sorted(await self._crawl(lambda item: False)), [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()


class RollingWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_window_walks_whole_shard_and_wraps(self):
        import time

        from app.scrape_engine import ScrapeEngine

        total_pages = 12
        calls: list[int] = []

        async def fetch_page(page: int):
            calls.append(page)
            return [_mk(page * 10 + j) for j in range(5)], total_pages * 5

        engine = ScrapeEngine(pipeline="deep")
        windows: dict[str, int] = {}
        covered: set[int] = set()
        for _ in range(4):
            calls.clear()
            res = await engine.fetch_pages_parallel(
                shard_key="s",
                fetch_page=fetch_page,
                max_pages=5,
                deadline_monotonic=time.monotonic() + 30,
                portal="sreality",
                window_start=windows.get("s", 2),
            )
            windows["s"] = res.next_window
            self.assertIn(1, calls)
            self.assertLessEqual(max(calls), total_pages)
            covered.update(calls)
        self.assertEqual(covered, set(range(1, total_pages + 1)))


class PriceAndCoordsTests(unittest.TestCase):
    def test_one_crown_placeholder_becomes_price_on_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "p.sqlite")
            item = replace(_mk(1, price=1), name="Prodej bytu 5+1 240 m²")
            store.upsert_catalog_listings_batch([item], kind="seeded")
            with store.connect() as conn:
                row = conn.execute("SELECT price_czk, price_label FROM catalog_listings").fetchone()
            self.assertIsNone(row["price_czk"])
            self.assertEqual(row["price_label"], "Cena na dotaz")

    def test_cleanup_and_catalog_coordinates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "c.sqlite")
            item = replace(_mk(2, price=15000), lat=None, lon=None, url="https://reality.idnes.cz/detail/prodej/byt/x/abc123/")
            store.upsert_catalog_listings_batch([item], kind="seeded")
            with store.connect() as conn:
                conn.execute("UPDATE catalog_listings SET price_czk = 1, lat = NULL, portal = 'idnes'")
            self.assertEqual(store.clean_price_placeholders() >= 1, True)
            rows = store.catalog_missing_coords(("idnes",), 10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(store.catalog_missing_coords(("idnes",), 10, {rows[0]["listing_key"]}), [])
            store.set_catalog_location(rows[0]["listing_key"], rows[0]["url"], 50.1, 14.4)
            self.assertEqual(store.catalog_missing_coords(("idnes",), 10), [])


def test_monitor_path_uses_catalog_fingerprints_when_its_own_cache_is_cold(tmp_path):
    """After a restart / TTL expiry unchanged listings must be touched, not rewritten row by row."""
    from unittest.mock import patch

    from app import scrape_fast
    from app.monitor import Listing
    from app.store import Store

    store = Store(tmp_path / "m.sqlite")
    listings = [
        Listing(id=i, name=f"Byt {i}", url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{i}", price_czk=20000,
                price_label="20 000 Kč", disposition="2+kk", area_m2=40, locality="Praha", image_url="")
        for i in range(1, 6)
    ]
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    with store.connect(readonly=True) as conn:
        scrape_fast.cache_for(str(store.path), conn)  # what discovery's warm-up / the first batch write does
    scrape_fast._RESULTS.clear()  # cold monitor cache

    with patch.object(store, "upsert_catalog_listing", side_effect=AssertionError("row-by-row rewrite")):
        results = store.upsert_catalog_listings_results(listings, kind="refresh")
    assert len(results) == 5
    assert all(item["changed"] is False and item["new"] is False for item in results)
    assert all(item["prev"]["price_czk"] == 20000 for item in results)
