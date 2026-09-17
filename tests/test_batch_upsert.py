"""Batch upsert smoke: many listings in one transaction path."""

from __future__ import annotations

from pathlib import Path

from app.sreality import Listing
from app.store import Store


def test_upsert_catalog_listings_batch_fast(tmp_path: Path):
    store = Store(tmp_path / "batch.sqlite")
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
    assert n["n"] == 25
    assert n["new"] == 25
    # Second pass hits fast canonical path (existing keys).
    n2 = store.upsert_catalog_listings_batch(listings, kind="refresh", commit_every=10, fast=True)
    assert n2["n"] == 25
    assert n2["new"] == 0


def test_upsert_catalog_listings_batch_default_commit_stays_500(tmp_path: Path):
    from app import config

    store = Store(tmp_path / "batch-default.sqlite")
    listings = [
        Listing(
            id=2000 + i,
            name=f"Byt {i} 2+kk",
            price_czk=16000 + i,
            price_label=f"{16000 + i} Kč/měsíc",
            disposition="2+kk",
            area_m2=48,
            locality="Praha 2",
            url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{2000 + i}",
            image_url=None,
        )
        for i in range(60)
    ]
    n = store.upsert_catalog_listings_batch(listings, kind="seeded", fast=True)
    assert n["n"] == 60
    assert n["new"] == 60
    assert config.SCRAPE_BATCH_COMMIT >= 50
    with store.read() as conn:
        fts = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listings_fts'"
        ).fetchone()
        cover = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_listings_pin_cover'"
        ).fetchone()
        first_seen = [row[2] for row in conn.execute("PRAGMA index_info('idx_listings_first_seen')")]
        n_listings = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    assert fts
    assert cover
    assert first_seen[:1] == ["first_seen"]
    assert n_listings == 60
