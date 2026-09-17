import asyncio
import json
from unittest.mock import AsyncMock, patch

import httpx
import unittest

from app.bezrealitky import (
    LIST_FIELDS,
    PAGE_SIZE,
    BezrealitkyClient,
    extras_from_bezrealitky,
    format_disposition,
    listing_url,
)
from app.catalog_sync import extra_portal_recent_shards


HOUSE = {
    "id": "1067752",
    "uri": "1067752-nabidka-pronajem-domu-menin",
    "estateType": "DUM",
    "offerType": "PRONAJEM",
    "disposition": "OSTATNI",
    "surface": 65,
    "surfaceLand": 223,
    "price": 12900,
    "charges": 0,
    "currency": "CZK",
    "imageAltText": "Pronájem domu 65 m², pozemek 223 m², Měnín",
    "address": "Měnín - Měnín, Jihomoravský kraj",
    "gps": {"lat": 49.0749655, "lng": 16.6935841},
    "mainImage": {"url": "https://img.example/menin.jpg"},
    "publicImages": [],
    "condition": "VERY_GOOD",
    "ownership": "UNDEFINED",
    "equipped": "CASTECNE",
    "floor": "UNDEFINED",
    "visitCount": 12,
}


SALE_FULL_URL = {
    "id": "1066310",
    "uri": "https://www.bezrealitky.cz/nemovitosti-byty-domy/1066310-nabidka-prodej-domu-trinec?utm=1",
    "estateType": "DUM",
    "offerType": "PRODEJ",
    "disposition": "UNDEFINED",
    "surface": 120,
    "surfaceLand": 5000,
    "price": 10000000,
    "imageAltText": "",
    "address": "Třinec - Guty, Moravskoslezský kraj",
    "gps": {"lat": 49.65314, "lng": 18.6012186},
    "mainImage": {"url": "https://img.example/trinec.jpg"},
}


class BezrealitkyTests(unittest.TestCase):
    def test_listing_url_canonicalizes_slug_path_and_absolute(self):
        slug = "1067752-nabidka-pronajem-domu-menin"
        want = "https://www.bezrealitky.cz/nemovitosti-byty-domy/1067752-nabidka-pronajem-domu-menin"
        self.assertEqual(listing_url(slug, 1067752), want)
        self.assertEqual(listing_url("/nemovitosti-byty-domy/" + slug, 1), want)
        self.assertEqual(listing_url("nemovitosti-byty-domy/" + slug, 1), want)
        self.assertEqual(listing_url(want + "?utm=1", 1), want)
        self.assertEqual(
            listing_url(None, 1067752),
            "https://www.bezrealitky.cz/nemovitosti-byty-domy/1067752",
        )

    def test_format_disposition_drops_undefined(self):
        self.assertEqual(format_disposition("UNDEFINED"), "")
        self.assertEqual(format_disposition("DISP_UNDEFINED"), "")
        self.assertEqual(format_disposition("OSTATNI"), "ostatní")
        self.assertEqual(format_disposition("DISP_3_KK"), "3+kk")

    def test_parse_house_fills_land_url_extras_without_undefined(self):
        client = BezrealitkyClient(
            "https://www.bezrealitky.cz/vyhledat?offerType=PRONAJEM&estateType=DUM&order=TIMEORDER_DESC"
        )
        try:
            house = client._parse(HOUSE)
            sale = client._parse(SALE_FULL_URL)
        finally:
            asyncio.run(client.aclose())
        self.assertEqual(house.id, 1067752)
        self.assertEqual(
            house.url,
            "https://www.bezrealitky.cz/nemovitosti-byty-domy/1067752-nabidka-pronajem-domu-menin",
        )
        self.assertEqual(house.price_czk, 12900)
        self.assertEqual(house.area_m2, 65)
        self.assertEqual(house.disposition, "ostatní")
        self.assertEqual(house.locality, "Měnín - Měnín, Jihomoravský kraj")
        self.assertAlmostEqual(house.lat, 49.0749655)
        self.assertAlmostEqual(house.lon, 16.6935841)
        self.assertEqual(house.extras.get("estate"), "Dům")
        self.assertEqual(house.extras.get("offer"), "Pronájem")
        self.assertEqual(house.extras.get("land_m2"), 223)
        self.assertIn("land", house.extras.get("flags") or [])
        self.assertTrue(any(item.get("label") == "Pozemek" for item in house.extras.get("specs") or []))
        self.assertTrue(any(item.get("label") == "Stav" for item in house.extras.get("specs") or []))
        self.assertFalse(any(item.get("label") == "Vlastnictví" for item in house.extras.get("specs") or []))
        self.assertFalse(any("UNDEFINED" in str(item.get("value")) for item in house.extras.get("specs") or []))

        self.assertEqual(
            sale.url,
            "https://www.bezrealitky.cz/nemovitosti-byty-domy/1066310-nabidka-prodej-domu-trinec",
        )
        self.assertEqual(sale.disposition, "")
        self.assertIn("domu", sale.name)
        self.assertIn("pozemek 5000 m²", sale.name)
        self.assertEqual(sale.extras.get("estate"), "Dům")
        self.assertEqual(sale.extras.get("land_m2"), 5000)

    def test_list_fields_include_land_not_denied_house_type(self):
        self.assertIn("surfaceLand", LIST_FIELDS)
        self.assertIn("visitCount", LIST_FIELDS)
        self.assertIn("condition", LIST_FIELDS)
        self.assertNotIn("houseType", LIST_FIELDS)
        extras = extras_from_bezrealitky({"estateType": "DUM", "offerType": "PRODEJ", "surfaceLand": 799})
        self.assertEqual(extras["land_m2"], 799)
        self.assertEqual(extras["estate"], "Dům")

    def test_house_search_args_and_newest_shards(self):
        self.assertEqual(PAGE_SIZE, 20)
        url = "https://www.bezrealitky.cz/vyhledat?offerType=PRODEJ&estateType=DUM&order=TIMEORDER_DESC"
        client = BezrealitkyClient(url)
        try:
            args = client._args(1)
        finally:
            asyncio.run(client.aclose())
        self.assertIn("limit: 20", args)
        self.assertIn("estateType: [DUM]", args)
        self.assertIn("offerType: [PRODEJ]", args)
        self.assertIn("order: TIMEORDER_DESC", args)
        recent = extra_portal_recent_shards()
        houses = [item for item in recent if item["shard_key"] == "bezrealitky:recent:pronajem:domy"]
        self.assertEqual(len(houses), 1)
        self.assertIn("estateType=DUM", houses[0]["search_url"])
        self.assertIn("offerType=PRONAJEM", houses[0]["search_url"])
        plots = [item for item in recent if item["shard_key"] == "bezrealitky:recent:prodej:pozemky"]
        self.assertEqual(len(plots), 1)
        self.assertIn("estateType=POZEMEK", plots[0]["search_url"])
        self.assertIn("offerType=PRODEJ", plots[0]["search_url"])

    def test_fetch_page_uses_graphql_gps_not_photon(self):
        payload = {"data": {"listAdverts": {"totalCount": 50, "list": [HOUSE, SALE_FULL_URL]}}}
        geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            self.assertEqual(str(request.url), "https://api.bezrealitky.cz/graphql/")
            body = json.loads(request.content.decode())
            query = body.get("query") or ""
            self.assertIn("surfaceLand", query)
            self.assertNotIn("houseType", query)
            self.assertIn("estateType: [DUM]", query)
            return httpx.Response(200, json=payload)

        async def _run() -> None:
            client = BezrealitkyClient(
                "https://www.bezrealitky.cz/vyhledat?offerType=PRONAJEM&estateType=DUM&order=TIMEORDER_DESC"
            )
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"content-type": "application/json"})
            try:
                with patch("app.places.geocode_locality", geocode), patch(
                    "app.places.geocode_locality_sync",
                    side_effect=AssertionError("list fetch must not geocode"),
                ), patch(
                    "app.places.refine_listing_location",
                    side_effect=AssertionError("list fetch must not reverse-geocode"),
                ):
                    listings, total = await client.fetch_page(1, newest=True)
            finally:
                await client.aclose()
            self.assertEqual(total, 50)
            self.assertEqual(len(listings), 2)
            geocode.assert_not_called()
            self.assertEqual(listings[0].extras.get("estate"), "Dům")
            self.assertEqual(listings[0].extras.get("land_m2"), 223)
            self.assertTrue(listings[0].image_url)
            self.assertEqual(
                listings[1].url,
                "https://www.bezrealitky.cz/nemovitosti-byty-domy/1066310-nabidka-prodej-domu-trinec",
            )

        asyncio.run(_run())
        self.assertTrue(hits)
