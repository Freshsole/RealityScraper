"""Batch upsert smoke: many listings in one transaction path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.sreality import Listing
from app.store import Store


class BatchUpsertTests(unittest.TestCase):
    def test_upsert_catalog_listings_batch_fast(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "batch.sqlite")
            listings = [
                Listing(
                    id=1000 + i,
                    name=f"Byt {i} 2+kk",
                    price_czk=15000 + i,
                    price_label=f"{15000 + i} Kč/měsíc",
                    disposition="2+kk",
                    area_m2=50,
                    locality="Praha 1",
                    url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{1000 + i}",
                    image_url=None,
                )
                for i in range(25)
            ]
            n = store.upsert_catalog_listings_batch(listings, kind="seeded", commit_every=10, fast=True)
            self.assertEqual(n["n"], 25)
            self.assertEqual(n["new"], 25)
            n2 = store.upsert_catalog_listings_batch(listings, kind="refresh", commit_every=10, fast=True)
            self.assertEqual(n2["n"], 25)
            self.assertEqual(n2["new"], 0)


if __name__ == "__main__":
    unittest.main()


def test_store_connections_do_not_fsync_every_commit(tmp_path):
    """synchronous defaults to FULL per connection; on a slow volume that made every commit cost an fsync."""
    from app.store import Store

    store = Store(tmp_path / "m.sqlite")
    for kwargs in ({}, {"readonly": True}, {"quick": True}):
        conn = store.connect(**kwargs)
        try:
            assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        finally:
            conn.close()
