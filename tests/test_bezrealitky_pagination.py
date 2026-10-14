"""Bezrealitky: limit/offset paging (100 rows), Czech region default, parsing."""

import asyncio

from app import bezrealitky
from app.bezrealitky import CZ_REGION_OSM, PAGE_SIZE, BezrealitkyClient
from tests._pagination_helpers import FakeResponse, load, patch_requests

URL = "https://www.bezrealitky.cz/vyhledat?offerType=PRONAJEM&estateType=BYT&order=TIMEORDER_DESC"


def test_page_size_is_api_max():
    assert PAGE_SIZE == 100  # API clamps limit to 100 (15 was 7x more requests)


def test_args_offset_and_default_czech_region():
    args = BezrealitkyClient(URL)._args(3)
    assert f"limit: {PAGE_SIZE}" in args and f"offset: {2 * PAGE_SIZE}" in args
    assert f'regionOsmIds: ["{CZ_REGION_OSM}"]' in args  # otherwise ~half the rows are German EUR offers
    assert "order: TIMEORDER_DESC" in args


def test_explicit_region_or_polygon_not_overridden():
    own = BezrealitkyClient(URL + "&regionOsmIds=R435514")._args(1)
    assert 'regionOsmIds: ["R435514"]' in own and CZ_REGION_OSM not in own
    poly = BezrealitkyClient(URL + '&boundaryPoints=[{"lat":50,"lng":14},{"lat":50,"lng":15},{"lat":49,"lng":15}]')._args(1)
    assert "regionOsmIds" not in poly


def test_list_query_trims_photos_but_detail_keeps_50():
    assert "limit: 12" in bezrealitky.LIST_FIELDS and "limit: 50" not in bezrealitky.LIST_FIELDS
    assert "limit: 50" in bezrealitky.DETAIL_FIELDS


def test_fetch_page_parses_rows(monkeypatch):
    fx = load("bezrealitky_list.json")
    calls: list = []
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse(fx["PRONAJEM"]), calls)
    client = BezrealitkyClient(URL)
    listings, total = asyncio.run(client.fetch_page(1))
    assert total == fx["PRONAJEM"]["data"]["listAdverts"]["totalCount"] > 1000
    assert len(listings) == 5 and len({item.id for item in listings}) == 5
    assert all(item.url.startswith("https://www.bezrealitky.cz/nemovitosti-byty-domy/") for item in listings)
    assert all(item.extras["offer"] == "Pronájem" for item in listings)
    assert "listAdverts(" in calls[0]["json"]["query"]


def test_sale_rows_are_sale(monkeypatch):
    fx = load("bezrealitky_list.json")
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse(fx["PRODEJ"]), [])
    client = BezrealitkyClient(URL.replace("PRONAJEM", "PRODEJ"))
    listings, total = asyncio.run(client.fetch_page(1))
    assert listings and all(item.extras["offer"] == "Prodej" for item in listings)
