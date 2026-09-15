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
