"""UlovDomov: 2026 find-API body (offerType/propertyType), total from extraData, row mapping."""

import asyncio

from app.portal_urls import ulovdomov_url
from app.ulovdomov import FIND_PAGE_SIZE, UlovdomovClient, offers_from_payload
from tests._pagination_helpers import FakeResponse, load, patch_requests


def test_find_body_has_real_offer_filter():
    rent = UlovdomovClient(ulovdomov_url.build_url({"offers": ["pronajem"]}))._find_body(1)
    sale = UlovdomovClient(ulovdomov_url.build_url({"offers": ["prodej"]}))._find_body(1)
    assert (rent["offerType"], rent["propertyType"]) == ("rent", "flat")
    assert sale["offerType"] == "sale"  # sale exists and has its own total (was identical to rent before)


def test_total_comes_from_extra_data():
    rows, total = offers_from_payload(load("ulovdomov_find.json")["rent"])
    assert len(rows) == 5 and total == 3132


def test_fetch_page_rent_and_sale(monkeypatch):
    fx = load("ulovdomov_find.json")
    calls: list = []

    def handler(method, url, kwargs):
        return FakeResponse(fx["sale"] if kwargs["json"]["offerType"] == "sale" else fx["rent"])

    patch_requests(monkeypatch, handler, calls)
    rent_client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    sale_client = UlovdomovClient("https://www.ulovdomov.cz/prodej/byty")
    rent, rent_total = asyncio.run(rent_client.fetch_page(2))
    sale, sale_total = asyncio.run(sale_client.fetch_page(1))
    assert rent_total == 3132 and sale_total == 2361 and rent_total != sale_total
    assert calls[0]["params"] == {"page": 2, "perPage": FIND_PAGE_SIZE, "sorting": "latest"}
    assert len({item.url for item in rent}) == len(rent) == 5
    assert all(item.extras["offer"] == "Pronájem" for item in rent)
    assert all(item.extras["offer"] == "Prodej" for item in sale)
    first = sale[0]
    assert first.price_czk == 1159900 and first.disposition == "2+1"
    assert first.locality == "M. Švabinského, Bílina" and first.created_on.startswith("2026-10-14")
    assert first.url.startswith("https://www.ulovdomov.cz/inzerat/prodej-")


def test_http_500_still_falls_back_to_sitemap(monkeypatch):
    # The old (broken) body returned 500; the sitemap fallback must still engage on a real outage.
    patch_requests(monkeypatch, lambda m, u, k: FakeResponse({"success": False}, 500), [])
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")

    async def boom(page):
        raise ValueError("sitemap down")

    monkeypatch.setattr(client, "_fetch_page_sitemap_fallback", boom)
    try:
        asyncio.run(client.fetch_page(1))
    except RuntimeError as exc:
        assert "500" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
