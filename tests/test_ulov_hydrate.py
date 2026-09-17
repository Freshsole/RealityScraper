import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.store import CATALOG_MONITOR_ID, Store
from app.ulovdomov import (
    DETAIL_API,
    UlovdomovClient,
    estate_from_inzerat_slug,
    hydrate_bucket,
    interleave_hydrate,
    listing_from_detail_payload,
    listing_hydrate_bucket,
    merge_detail,
    needs_hydrate,
    reset_ulov_caches,
)
from app import ulov_hydrate

FIXTURES = Path(__file__).parent / "fixtures"
DETAIL = json.loads((FIXTURES / "ulov_offer_detail.json").read_text())
HOUSE_DETAIL = json.loads((FIXTURES / "ulov_offer_house.json").read_text())
SALE_DETAIL = json.loads((FIXTURES / "ulov_offer_sale.json").read_text())
SITEMAP = (FIXTURES / "ulov_sitemap_offers.xml").read_text()


def test_detail_payload_has_price_and_photo():
    listing = listing_from_detail_payload(DETAIL)
    assert listing is not None
    assert listing.id == 3496443
    assert listing.price_czk == 6000
    assert "měsíc" in listing.price_label
    assert listing.disposition == "1+kk"
    assert "Ústí nad Labem" in listing.locality
    assert listing.image_url and listing.image_url.startswith("https://storage.livendo.eu/")
    assert len(listing.photos) == 2
    assert listing.lat == pytest.approx(50.65715)
    assert listing.area_m2 == 18
    assert listing.url.endswith("/3496443")
    assert "/inzerat/" in listing.url
    assert listing.extras.get("estate") == "Byt"


def test_house_and_sale_detail_payloads():
    house = listing_from_detail_payload(HOUSE_DETAIL)
    assert house is not None
    assert house.id == 5222881
    assert house.price_czk == 49000
    assert house.extras.get("estate") == "Dům"
    assert house.disposition == "dům"
    assert house.image_url
    sale = listing_from_detail_payload(SALE_DETAIL)
    assert sale is not None
    assert sale.id == 5679032
    assert sale.price_czk == 4350000
    assert sale.extras.get("offer") == "Prodej"
    assert sale.extras.get("estate") == "Byt"


def test_detail_keeps_catalog_url():
    keep = "https://www.ulovdomov.cz/inzerat/pronajem-brno-veveri-bayerova-2-kk/3496443"
    listing = listing_from_detail_payload(DETAIL, keep_url=keep)
    assert listing is not None
    assert listing.url == keep
    stub = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty").listing_from_sitemap_url(
        keep, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443
    )
    assert needs_hydrate(stub) is True
    merge_detail(stub, listing)
    assert stub.url == keep
    assert stub.price_czk == 6000
    assert stub.image_url
    assert stub.extras.get("sitemap_card") is None
    assert needs_hydrate(stub) is False


def test_hydrate_allowed_never_on_web(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_ULOV_HYDRATE", True)
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    assert ulov_hydrate.allowed() is False
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    assert ulov_hydrate.allowed() is True
    monkeypatch.setattr("app.config.SCRAPE_ULOV_HYDRATE", False)
    assert ulov_hydrate.allowed() is False


def test_instant_site_stays_off_ulov_hydrate_path():
    src = Path("app/site_pages.py").read_text()
    assert "ulov_hydrate" not in src
    assert "offer/detail" not in src
    assert "ulovdomov" not in src


def test_fetch_page_does_not_call_detail():
    reset_ulov_caches()
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(500, json={"error": "udBe.internalServerError", "success": False})
        if "sitemap-offers" in str(request.url):
            return httpx.Response(200, text=SITEMAP, headers={"content-type": "application/xml"})
        raise AssertionError(f"list path must not hit {request.url}")

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listings, total = await client.fetch_page(1)
            assert total == 2
            assert len(listings) == 2
            assert all(needs_hydrate(item) for item in listings)
            house_client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/domy")
            await house_client.aclose()
            house_client._client = client._client
            houses, house_total = await house_client.fetch_page(1)
            assert house_total == 1
            assert houses[0].id == 5222881
            assert houses[0].extras.get("estate") == "Dům"
            sale_houses = UlovdomovClient("https://www.ulovdomov.cz/prodej/domy")
            await sale_houses.aclose()
            sale_houses._client = client._client
            villas, villa_total = await sale_houses.fetch_page(1)
            assert villa_total == 1
            assert villas[0].id == 5653004
            land_client = UlovdomovClient("https://www.ulovdomov.cz/prodej/pozemky")
            await land_client.aclose()
            land_client._client = client._client
            plots, plot_total = await land_client.fetch_page(1)
            assert plot_total == 1
            assert plots[0].id == 5669330
            assert plots[0].extras.get("estate") == "Pozemek"
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()
    assert any("sitemap-offers" in path for path in hits)
    assert not any("offer/detail" in path for path in hits)


def test_sitemap_single_flight_and_cache_fresh():
    from app.ulovdomov import sitemap_cache_fresh

    reset_ulov_caches()
    assert sitemap_cache_fresh() is False
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url.path))
        return httpx.Response(200, text=SITEMAP, headers={"content-type": "application/xml"})

    async def _run() -> None:
        rent = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        sale = UlovdomovClient("https://www.ulovdomov.cz/prodej/byty")
        await rent.aclose()
        await sale.aclose()
        transport = httpx.MockTransport(handler)
        rent._client = httpx.AsyncClient(transport=transport)
        sale._client = httpx.AsyncClient(transport=transport)
        try:
            rows_a, rows_b = await asyncio.gather(rent._load_sitemap_rows(), sale._load_sitemap_rows())
            assert rows_a and rows_b
            assert sitemap_cache_fresh() is True
            await rent._load_sitemap_rows()
        finally:
            await rent.aclose()
            await sale.aclose()

    asyncio.run(_run())
    reset_ulov_caches()
    assert hits.count("/sitemap-offers.xml") == 1


def test_hydrate_batch_fills_price_and_is_fail_fast():
    reset_ulov_caches()
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        if "offer/detail" in str(request.url):
            offer_id = request.url.params.get("offerId")
            if offer_id == "3496443":
                return httpx.Response(200, json=DETAIL)
            if offer_id == "2037015":
                return httpx.Response(429, json={"error": "rate"}, headers={"Retry-After": "3"})
            return httpx.Response(404, json={"error": "Offer not found", "success": False, "data": None})
        raise AssertionError(f"unexpected {request.url}")

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            keep = "https://www.ulovdomov.cz/inzerat/pronajem-brno-veveri-bayerova-2-kk/3496443"
            first = client.listing_from_sitemap_url(keep, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443)
            second = client.listing_from_sitemap_url(
                "https://www.ulovdomov.cz/inzerat/pronajem-praha-liben-na-korabe-1-kk/2037015",
                "pronajem",
                "pronajem-praha-liben-na-korabe-1-kk",
                2037015,
            )
            result = await client.hydrate_listings(
                [first, second],
                concurrency=1,
                delay_sec=0,
                deadline_sec=5,
                fail_fast=True,
            )
            assert result.priced == 1
            assert result.imaged == 1
            assert first.price_czk == 6000
            assert first.url == keep
            assert result.blocked is not None
            assert result.blocked.status_code == 429
            assert result.aborted
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()


def test_hydrate_gone_is_not_a_block():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Offer not found", "success": False, "data": None})

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listing = client.listing_from_sitemap_url(
                "https://www.ulovdomov.cz/inzerat/pronajem-x/1", "pronajem", "pronajem-x", 1
            )
            result = await client.hydrate_listings([listing], concurrency=1, delay_sec=0, deadline_sec=3)
            assert result.gone == 1
            assert result.priced == 0
            assert result.blocked is None
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_sitemap_refresh_does_not_wipe_hydrated_price(tmp_path: Path):
    store = Store(tmp_path / "ulov-hydrate.sqlite")
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    url = "https://www.ulovdomov.cz/inzerat/pronajem-brno-veveri-bayerova-2-kk/3496443"
    stub = client.listing_from_sitemap_url(url, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443)
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    detailed = listing_from_detail_payload(DETAIL, keep_url=url)
    merge_detail(stub, detailed)
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    again = client.listing_from_sitemap_url(url, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443)
    store.upsert_catalog_listing(again, kind="refresh", fast=True)
    rows = store.unpriced_ulov_listings(limit=10)
    assert rows == []
    with store.connect() as conn:
        row = dict(conn.execute("SELECT price_czk, image_url, name FROM catalog_listings").fetchone())
        seen = dict(
            conn.execute(
                "SELECT price_czk, image_url, lat FROM listings WHERE monitor_id = ?",
                (CATALOG_MONITOR_ID,),
            ).fetchone()
        )
    assert row["price_czk"] == 6000
    assert row["image_url"]
    assert "1+kk" in row["name"]
    assert seen["price_czk"] == 6000
    assert seen["image_url"]
    assert seen["lat"] == pytest.approx(50.65715)


def test_catalog_list_overlays_hydrated_fields(tmp_path: Path):
    store = Store(tmp_path / "ulov-overlay.sqlite")
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    url = "https://www.ulovdomov.cz/inzerat/pronajem-brno-veveri-bayerova-2-kk/3496443"
    stub = client.listing_from_sitemap_url(url, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443)
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    detailed = listing_from_detail_payload(DETAIL, keep_url=url)
    merge_detail(stub, detailed)
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    with store.connect() as conn:
        conn.execute(
            "UPDATE listings SET price_czk = NULL, price_label = '', image_url = '', lat = NULL, lon = NULL"
        )
        conn.commit()
    page = store.catalog({"limit": 12, "include_pins": "0"})
    assert page["items"]
    item = page["items"][0]
    assert item["price_czk"] == 6000
    assert item["image_url"]
    detail = store.catalog_item(CATALOG_MONITOR_ID, 3496443, "", url)
    assert detail is not None
    assert detail["price_czk"] == 6000
    assert detail["lat"] == pytest.approx(50.65715)


def test_hydrate_write_invalidates_city_pin_cache(tmp_path: Path):
    store = Store(tmp_path / "ulov-pins.sqlite")
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    url = "https://www.ulovdomov.cz/inzerat/pronajem-brno-veveri-bayerova-2-kk/3496443"
    stub = client.listing_from_sitemap_url(url, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443)
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    detailed = listing_from_detail_payload(DETAIL, keep_url=url)
    merge_detail(stub, detailed)
    store._city_pin_cache[("wide",)] = (0.0, [], 0, [])
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    assert store._city_pin_cache == {}
    store._city_pin_cache[("wide",)] = (0.0, [], 0, [])
    again = client.listing_from_sitemap_url(url, "pronajem", "pronajem-brno-veveri-bayerova-2-kk", 3496443)
    store.upsert_catalog_listing(again, kind="refresh", fast=True)
    assert ("wide",) in store._city_pin_cache


def test_unpriced_ulov_reader_prefers_missing_price(tmp_path: Path):
    store = Store(tmp_path / "ulov-unpriced.sqlite")
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    stub = client.listing_from_sitemap_url(
        "https://www.ulovdomov.cz/inzerat/pronajem-praha-liben-na-korabe-1-kk/2037015",
        "pronajem",
        "pronajem-praha-liben-na-korabe-1-kk",
        2037015,
    )
    store.upsert_catalog_listing(stub, kind="refresh", fast=True)
    rows = store.unpriced_ulov_listings(limit=5)
    assert len(rows) == 1
    assert rows[0]["id"] == 2037015
    assert DETAIL_API.endswith("/v2/offer/detail")


def test_estate_and_hydrate_buckets():
    assert estate_from_inzerat_slug("pronajem-troubsko-troubsko-troubsko-dum") == "dum"
    assert estate_from_inzerat_slug("-senohraby-senohraby-ve-vilach-fiveplusrooms") == "dum"
    assert estate_from_inzerat_slug("-hluboka-nad-vltavou-zahradni-housing") == "pozemek"
    assert hydrate_bucket("prodej", "-hluboka-nad-vltavou-zahradni-housing") == "sale"
    assert hydrate_bucket("pronajem", "pronajem-olomouc-2-kk") == "rent"
    assert hydrate_bucket("prodej", "-praha-kbely-1-kk") == "sale"
    assert hydrate_bucket("pronajem", "pronajem-troubsko-troubsko-troubsko-dum") == "rent_house"
    mixed = interleave_hydrate(
        {
            "rent": ["r1", "r2"],
            "sale": ["s1"],
            "rent_house": ["h1"],
            "sale_house": ["v1"],
            "other": [],
        },
        5,
    )
    assert mixed == ["r1", "s1", "h1", "v1", "r2"]


def test_collect_candidates_interleaves_sale_and_houses():
    reset_ulov_caches()

    def handler(request: httpx.Request) -> httpx.Response:
        if "sitemap-offers" in str(request.url):
            return httpx.Response(200, text=SITEMAP, headers={"content-type": "application/xml"})
        raise AssertionError(f"unexpected {request.url}")

    class EmptyStore:
        def unpriced_ulov_listings(self, limit: int = 20):
            return []

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listings = await ulov_hydrate.collect_candidates(EmptyStore(), client, limit=4)
            kinds = [listing_hydrate_bucket(item) for item in listings]
            assert "rent" in kinds
            assert "sale" in kinds
            assert "rent_house" in kinds
            assert "sale_house" in kinds
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()


def test_collect_candidates_prefers_store_unpriced_over_sitemap(tmp_path: Path):
    reset_ulov_caches()
    store = Store(tmp_path / "ulov-mix.sqlite")
    client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
    unpriced = client.listing_from_sitemap_url(
        "https://www.ulovdomov.cz/inzerat/-pardubice-studanka-bartonova-2-1/5679032",
        "prodej",
        "-pardubice-studanka-bartonova-2-1",
        5679032,
    )
    priced = client.listing_from_sitemap_url(
        "https://www.ulovdomov.cz/inzerat/pronajem-brno-veveri-bayerova-2-kk/3496443",
        "pronajem",
        "pronajem-brno-veveri-bayerova-2-kk",
        3496443,
    )
    merge_detail(priced, listing_from_detail_payload(DETAIL, keep_url=priced.url))
    store.upsert_catalog_listing(unpriced, kind="refresh", fast=True)
    store.upsert_catalog_listing(priced, kind="refresh", fast=True)
    rows = store.unpriced_ulov_listings(limit=10)
    assert [row["id"] for row in rows] == [5679032]

    def handler(request: httpx.Request) -> httpx.Response:
        if "sitemap-offers" in str(request.url):
            return httpx.Response(200, text=SITEMAP, headers={"content-type": "application/xml"})
        raise AssertionError(f"unexpected {request.url}")

    async def _run() -> None:
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listings = await ulov_hydrate.collect_candidates(store, client, limit=3)
            ids = [item.id for item in listings]
            assert 5679032 in ids
            assert 3496443 not in ids
            sale = next(item for item in listings if item.id == 5679032)
            assert listing_hydrate_bucket(sale) == "sale"
            assert all(needs_hydrate(item) for item in listings)
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()


def test_hydrate_410_is_gone_not_a_block():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410,
            json={"error": "Offer is not available", "success": False, "data": {"status": "DELETED"}},
        )

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/prodej/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listing = client.listing_from_sitemap_url(
                "https://www.ulovdomov.cz/inzerat/-praha-kbely-herlikovicka-1-kk/5679034",
                "prodej",
                "-praha-kbely-herlikovicka-1-kk",
                5679034,
            )
            result = await client.hydrate_listings([listing], concurrency=1, delay_sec=0, deadline_sec=3)
            assert result.gone == 1
            assert result.blocked is None
            assert result.priced == 0
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_hydrate_mixed_sale_house_batch_is_fail_fast():
    reset_ulov_caches()
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        offer_id = request.url.params.get("offerId")
        if offer_id == "5679032":
            return httpx.Response(200, json=SALE_DETAIL)
        if offer_id == "5222881":
            return httpx.Response(200, json=HOUSE_DETAIL)
        if offer_id == "2037015":
            return httpx.Response(429, json={"error": "rate"}, headers={"Retry-After": "3"})
        raise AssertionError(f"unexpected {request.url}")

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            sale = client.listing_from_sitemap_url(
                "https://www.ulovdomov.cz/inzerat/-pardubice-studanka-bartonova-2-1/5679032",
                "prodej",
                "-pardubice-studanka-bartonova-2-1",
                5679032,
            )
            house = client.listing_from_sitemap_url(
                "https://www.ulovdomov.cz/inzerat/pronajem-troubsko-troubsko-troubsko-dum/5222881",
                "pronajem",
                "pronajem-troubsko-troubsko-troubsko-dum",
                5222881,
            )
            later = client.listing_from_sitemap_url(
                "https://www.ulovdomov.cz/inzerat/pronajem-praha-liben-na-korabe-1-kk/2037015",
                "pronajem",
                "pronajem-praha-liben-na-korabe-1-kk",
                2037015,
            )
            result = await client.hydrate_listings(
                [sale, house, later],
                concurrency=1,
                delay_sec=0,
                deadline_sec=5,
                fail_fast=True,
            )
            assert sale.price_czk == 4350000
            assert house.price_czk == 49000
            assert house.extras.get("estate") == "Dům"
            assert result.priced == 2
            assert result.blocked is not None
            assert result.blocked.status_code == 429
            assert result.aborted
            assert result.kinds.get("sale", {}).get("priced") == 1
            assert result.kinds.get("rent_house", {}).get("priced") == 1
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()
