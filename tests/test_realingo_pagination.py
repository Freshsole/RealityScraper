"""Realingo: GraphQL (/graphql) paging, offer/property filter, locked teasers, CZK price."""

import asyncio

from app.portal_urls import realingo_url
from app.realingo import GRAPHQL_MAX_PAGE_SIZE, RealingoClient
from tests._pagination_helpers import FakeResponse, load, patch_requests


def test_build_url_is_flats_only():
    # "_reality" meant every property type (39k rent / 100k sale); flats are "_byty".
    assert realingo_url.build_url({"offers": ["pronajem"]}) == "https://www.realingo.cz/pronajem_byty/cr/"
    assert realingo_url.build_url({"offers": ["prodej"]}) == "https://www.realingo.cz/prodej_byty/cr/"


def test_filter_from_search_url():
    assert RealingoClient("https://www.realingo.cz/pronajem_byty/cr/")._filter() == {"purpose": "RENT", "property": "FLAT"}
    assert RealingoClient("https://www.realingo.cz/prodej_byty/cr/")._filter() == {"purpose": "SELL", "property": "FLAT"}
    # Legacy monitor URLs: all property types -> no property filter.
    assert RealingoClient("https://www.realingo.cz/prodej_reality/cr/")._filter() == {"purpose": "SELL"}


def test_fetch_page_maps_rows_and_skips_locked(monkeypatch):
    payload = load("realingo_graphql_rent.json")
    calls: list = []
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse(payload), calls)
    client = RealingoClient("https://www.realingo.cz/pronajem_byty/cr/")
    listings, total = asyncio.run(client.fetch_page(1))
    assert total == 14859
    # 8 fixture rows, 1 locked teaser (no url/price) must be dropped.
    assert len(listings) == 7
    assert all(item.url.startswith("https://www.realingo.cz/pronajem/") for item in listings)
    first = listings[0]
    assert first.id == 24746547 and first.price_czk == 13500 and first.disposition == "2+1"
    assert first.area_m2 == 50 and first.extras["offer"] == "Pronájem"
    assert first.created_on.startswith("2026-10-12")
    # EUR rows use the CZK-converted `canonical` price, not the raw EUR amount.
    eur = next(item for item in listings if item.id == 24746279)
    assert eur.price_czk and eur.price_czk > 10_000


def test_request_uses_skip_first_and_newest_sort(monkeypatch):
    calls: list = []
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse(load("realingo_graphql_sale.json")), calls)
    client = RealingoClient("https://www.realingo.cz/prodej_byty/cr/")
    asyncio.run(client.fetch_page(3))
    call = calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/graphql")
    v = call["json"]["variables"]
    assert v["filter"] == {"purpose": "SELL", "property": "FLAT"}
    assert v["sort"] == "NEWEST"
    assert v["first"] == client.PAGE_SIZE <= GRAPHQL_MAX_PAGE_SIZE
    assert v["skip"] == 2 * client.PAGE_SIZE


def test_blocked_returns_empty_and_records_status(monkeypatch):
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse({}, 429), [])
    client = RealingoClient("https://www.realingo.cz/pronajem_byty/cr/")
    assert asyncio.run(client.fetch_page(1)) == ([], 0)
    assert client._blocked_status == 429


def test_graphql_errors_raise(monkeypatch):
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse({"errors": [{"message": "boom"}]}), [])
    client = RealingoClient("https://www.realingo.cz/pronajem_byty/cr/")
    try:
        asyncio.run(client.fetch_page(1))
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
