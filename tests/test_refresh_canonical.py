"""Refresh upsert must not change identity when geo/URL stay the same."""

from __future__ import annotations

from pathlib import Path

from app.sreality import Listing
from app.store import Store


def _listing(**kwargs) -> Listing:
    defaults = dict(
        id=4242,
        name="Byt 2+kk",
        price_czk=18000,
        price_label="18 000 Kč/měsíc",
        disposition="2+kk",
        area_m2=48,
        locality="Praha 7",
        url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/4242",
        image_url=None,
        lat=50.1034,
        lon=14.4356,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


def test_refresh_keeps_canonical_key(tmp_path: Path):
    store = Store(tmp_path / "refresh.sqlite")
    first = store.upsert_catalog_listing(_listing(), kind="seeded")
    key = first["canonical_key"]
    assert key

    again = store.upsert_catalog_listing(_listing(price_czk=18200, price_label="18 200 Kč/měsíc"), kind="refresh", fast=True)
    assert again["canonical_key"] == key
    assert again["new"] is False

    with store.connect() as conn:
        row = conn.execute("SELECT canonical_key, url FROM catalog_listings").fetchone()
        assert row["canonical_key"] == key
        events = int(conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'refresh'").fetchone()[0])
    assert events == 0


def test_unchanged_refresh_skips_events(tmp_path: Path):
    store = Store(tmp_path / "events.sqlite")
    listing = _listing()
    store.upsert_catalog_listing(listing, kind="seeded")
    store.upsert_seen("__catalog__", listing, notified=False, kind="refresh", skip_nearby=True, fast=True)
    with store.connect() as conn:
        n = int(conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'refresh'").fetchone()[0])
    assert n == 0


def test_refresh_logs_price_change_event(tmp_path: Path):
    store = Store(tmp_path / "price.sqlite")
    store.upsert_catalog_listing(_listing(), kind="seeded")
    changed = _listing(price_czk=15000, price_label="15 000 Kč/měsíc")
    store.upsert_seen("__catalog__", changed, notified=False, kind="refresh", skip_nearby=True, fast=True)
    with store.connect() as conn:
        n = int(conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'refresh'").fetchone()[0])
    assert n == 1
