import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.places import approx_point_from_locality
from app.sreality import SrealityClient, listing_from_raw, parse_search_payload

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_PROPS = json.loads((FIXTURES / "sreality_estates_search.json").read_text())


def test_list_cards_fill_sale_house_urls_price_gps():
    listings, total = parse_search_payload(PAGE_PROPS)
    assert total == 11307
    assert [item.id for item in listings] == [4115275852, 3273384012, 2864177228, 1000000001]

    rent = listings[0]
    assert rent.url == "https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-josefov-maiselova/4115275852"
    assert rent.price_czk == 40000
    assert rent.image_url
    assert "Josefov" in rent.locality
    assert rent.lat == 50.0892
    assert rent.lon == 14.4184
    assert rent.extras.get("offer") == "Pronájem"
    assert rent.extras.get("estate") == "Byty"

    sale = listings[1]
    assert sale.url == "https://www.sreality.cz/detail/prodej/byt/2+1/kladno-krocehlavy-otevrena/3273384012"
    assert sale.price_czk is None
    assert sale.price_label == "Cena neuvedena"
    assert "Kročehlavy" in sale.locality
    assert sale.extras.get("offer") == "Prodej"
    assert sale.lat is not None

    house = listings[2]
    assert house.url == (
        "https://www.sreality.cz/detail/pronajem/dum/rodinny/pruhonice-pruhonice-pod-valem-ii/2864177228"
    )
    assert "Rodinný" not in house.url
    assert "/byt/" not in house.url
    assert house.price_czk == 65000
    assert house.extras.get("estate") == "Domy"
    assert house.disposition == "Rodinný"
    assert house.lat == 49.996766

    pin_card = listings[3]
    assert pin_card.lat is None
    assert pin_card.lon is None
    assert pin_card.locality == "Praha 7"


def test_listing_from_raw_keeps_flat_plus_slug():
    item = listing_from_raw(
        {
            "id": 1,
            "name": "Pronájem bytu 3+kk 70 m²",
            "priceCzk": 22000,
            "categoryTypeCb": {"name": "Pronájem", "value": 2},
            "categoryMainCb": {"name": "Byty", "value": 1},
            "categorySubCb": {"name": "3+kk", "value": 6},
            "locality": {"citySeoName": "brno", "streetSeoName": "vesla"},
        }
    )
    assert item is not None
    assert item.url.endswith("/detail/pronajem/byt/3+kk/brno-vesla/1")


def test_fetch_page_uses_json_gps_not_photon():
    html = (
        '<html><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"buildId": "test-build", "props": {"pageProps": PAGE_PROPS}})
        + "</script></html>"
    )
    geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        hits.append(url)
        if "/_next/data/" in url:
            return httpx.Response(200, json={"pageProps": PAGE_PROPS})
        return httpx.Response(200, text=html)

    async def _run() -> None:
        client = SrealityClient("https://www.sreality.cz/hledani/pronajem/byty?razeni=nejnovejsi")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with patch("app.places.geocode_locality", geocode), patch(
                "app.places.geocode_locality_sync",
                side_effect=AssertionError("list fetch must not geocode"),
            ):
                listings, total = await client.fetch_page(1, newest=True)
        finally:
            await client.aclose()
        assert total == 11307
        assert len(listings) == 4
        geocode.assert_not_called()
        assert any("razeni=nejnovejsi" in url for url in hits)
        by_id = {item.id: item for item in listings}
        assert by_id[4115275852].lat == 50.0892
        pin = approx_point_from_locality("Praha 7")
        assert pin is not None
        assert (by_id[1000000001].lat, by_id[1000000001].lon) == pin
        assert "/prodej/byt/2+1/" in by_id[3273384012].url
        assert "/dum/rodinny/" in by_id[2864177228].url

    asyncio.run(_run())
