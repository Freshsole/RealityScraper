from pathlib import Path

from app.annonce import AnnonceClient
from app.html_listing import listing_from_card
from app.places import approx_point_from_locality
from app.store import Store


def _annonce(listing_id: int, locality: str, *, lat=None, lon=None):
    return listing_from_card(
        listing_id=listing_id,
        name=f"Pronájem bytu 2+kk, {locality}",
        url=f"https://www.annonce.cz/inzerat/pronajem-bytu-{listing_id}-abc123.html",
        price_czk=18000,
        price_label="18 000 Kč",
        locality=locality,
        disposition="2+kk",
        area_m2=45,
        offer="pronajem",
        lat=lat,
        lon=lon,
    )


def test_html_portal_pins_local_city_without_network():
    client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
    listing = _annonce(771122, "Praha 7")
    assert listing.lat is None
    client._attach_local_coords([listing])
    point = approx_point_from_locality("Praha 7")
    assert point is not None
    assert listing.lat == point[0]
    assert listing.lon == point[1]


def test_catalog_write_invalidates_pin_and_preview_caches(tmp_path: Path):
    store = Store(tmp_path / "fresh.sqlite")
    first = _annonce(1001, "Praha 7", lat=50.1, lon=14.43)
    store.upsert_catalog_listing(first, kind="new", fast=True)
    store._city_pin_cache[("wide",)] = (0.0, [], 0, [])
    store._new_today_cache = (0.0, 99)
    store._landing_preview_cache = (0.0, [{"id": 1}])
    store._hot_json_cache["catalog|keep"] = (0.0, {"items": []})

    second = _annonce(1002, "Brno", lat=49.2, lon=16.6)
    store.upsert_catalog_listing(second, kind="new", fast=True)

    assert store._city_pin_cache == {}
    assert store._new_today_cache is None
    assert store._landing_preview_cache is None
    assert "catalog|keep" in store._hot_json_cache

    page = store.catalog({"sort": "newest", "limit": 12, "include_pins": "0"})
    ids = [item["id"] for item in page["items"]]
    assert 1002 in ids
    assert 1001 in ids
