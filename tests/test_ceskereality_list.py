import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.ceskereality import CeskerealityClient, canonical_list_url
from app.places import approx_point_from_locality
from app.portal_urls import ceskereality_url

FIXTURES = Path(__file__).parent / "fixtures"


class CeskerealityListTests(unittest.TestCase):
    def test_house_urls_use_rodinne_domy_not_agency_domy(self):
        houses = ceskereality_url.build_url({"offers": ["pronajem"], "category": "domy"})
        self.assertIn("/pronajem/rodinne-domy/nejnovejsi/", houses)
        self.assertNotIn("/pronajem/domy/", houses)
        self.assertEqual(
            ceskereality_url.parse_url("https://www.ceskereality.cz/prodej/rodinne-domy/nejnovejsi/")["category"],
            "domy",
        )
        self.assertEqual(
            ceskereality_url.parse_url("https://www.ceskereality.cz/prodej/pozemky/nejnovejsi/")["category"],
            "pozemky",
        )
        self.assertEqual(
            ceskereality_url.parse_url("https://www.ceskereality.cz/pronajem/domy/")["category"],
            "domy",
        )
        self.assertEqual(
            canonical_list_url("https://www.ceskereality.cz/pronajem/domy/nejnovejsi/"),
            "https://www.ceskereality.cz/pronajem/rodinne-domy/nejnovejsi/",
        )
        self.assertEqual(
            canonical_list_url("https://www.ceskereality.cz/pronajem/rodinne-domy/nejnovejsi/"),
            "https://www.ceskereality.cz/pronajem/rodinne-domy/nejnovejsi/",
        )

    def test_house_fixture_parses_ids_urls_total_and_estate(self):
        html = (FIXTURES / "ceskereality_houses.html").read_text()
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/rodinne-domy/nejnovejsi/")
        items = client._parse_list(html)
        self.assertEqual([item.id for item in items], [3895175, 3895109, 3895212])
        self.assertTrue(items[0].url.endswith("3895175.html"))
        self.assertIn("/rodinne-domy/", items[0].url)
        self.assertNotIn("muj-profil", items[0].url)
        self.assertEqual(items[0].price_czk, 26000)
        self.assertEqual(items[0].extras.get("estate"), "Dům")
        self.assertIn("Lipence", items[0].locality)
        self.assertEqual(items[1].locality, "Spálené Poříčí, Štítovská")
        self.assertEqual(items[2].locality, "marianske lazne")
        self.assertEqual(items[2].extras.get("estate"), "Byt")
        self.assertEqual(client._parse_total(html), 331)
        self.assertIn("/rodinne-domy/nejnovejsi/", client._page_url(1, newest=True))
        self.assertNotIn("/domy/", client._page_url(1, newest=True))

    def test_agency_domy_page_url_rewrites_to_rodinne_domy(self):
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/domy/nejnovejsi/")
        page1 = client._page_url(1, newest=True)
        self.assertIn("/pronajem/rodinne-domy/nejnovejsi/", page1)
        self.assertNotIn("/pronajem/domy/", page1)
        page2 = client._page_url(2, newest=True)
        self.assertIn("/pronajem/rodinne-domy/nejnovejsi/", page2)
        self.assertIn("strana=2", page2)

    def test_empty_house_nejnovejsi_falls_back_to_house_list_not_byty(self):
        empty = "<html><body>Hledáte nové domy k pronájmu? Máme tady 331 rodinných domů.</body></html>"
        cards = (FIXTURES / "ceskereality_houses.html").read_text()
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url.path))
            if "/nejnovejsi/" in request.url.path:
                return httpx.Response(200, text=empty)
            return httpx.Response(200, text=cards)

        async def _run() -> None:
            client = CeskerealityClient("https://www.ceskereality.cz/pronajem/rodinne-domy/nejnovejsi/")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                listings, total = await client.fetch_page(1, newest=True)
            finally:
                await client.aclose()
            self.assertEqual([item.id for item in listings[:2]], [3895175, 3895109])
            self.assertEqual(total, 331)
            self.assertEqual(hits, ["/pronajem/rodinne-domy/nejnovejsi/", "/pronajem/rodinne-domy/"])
            self.assertTrue(all("/byty/" not in path for path in hits))

        asyncio.run(_run())

    def test_fetch_page_uses_local_pins_not_network_geocode(self):
        html = (FIXTURES / "ceskereality_houses.html").read_text()
        geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("/rodinne-domy/", str(request.url))
            self.assertNotIn("/pronajem/domy/", str(request.url))
            return httpx.Response(200, text=html)

        async def _run() -> None:
            client = CeskerealityClient("https://www.ceskereality.cz/pronajem/domy/nejnovejsi/")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            with patch("app.places.geocode_locality", geocode):
                listings, total = await client.fetch_page(1, newest=True)
            await client.aclose()
            self.assertEqual(len(listings), 3)
            self.assertEqual(total, 331)
            geocode.assert_not_called()
            praha = approx_point_from_locality("Praha Lipence, Jílovišťská")
            self.assertEqual((listings[0].lat, listings[0].lon), praha)
            porici = approx_point_from_locality("Spálené Poříčí, Štítovská")
            self.assertEqual((listings[1].lat, listings[1].lon), porici)
            self.assertIsNotNone(porici)
            lazne = approx_point_from_locality("marianske lazne")
            self.assertEqual((listings[2].lat, listings[2].lon), lazne)
            self.assertEqual(lazne, approx_point_from_locality("Mariánské Lázně"))
            krnov = approx_point_from_locality("Krnov Pod Bezručovým vrchem, Partyzánů")
            self.assertEqual(krnov, approx_point_from_locality("Krnov"))
            self.assertIsNotNone(krnov)
            self.assertEqual(
                approx_point_from_locality("Šternberk, Příčná"),
                approx_point_from_locality("Šternberk"),
            )

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
