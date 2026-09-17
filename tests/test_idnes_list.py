import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.idnes import IdnesClient, oid_to_int
from app.places import approx_point_from_locality

FIXTURES = Path(__file__).parent / "fixtures"


class IdnesListTests(unittest.TestCase):
    def test_list_fixture_parses_cards_and_total(self):
        html = (FIXTURES / "idnes_cards.html").read_text()
        client = IdnesClient("https://reality.idnes.cz/s/pronajem/byty/")
        items = client._parse_list(client._results_html(html))
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0].id, oid_to_int("6aab2c79f0d2c87c830c6141"))
        self.assertTrue(items[0].url.endswith("/6aab2c79f0d2c87c830c6141/"))
        self.assertEqual(items[0].price_czk, 16000)
        self.assertIn("Ostrava", items[0].locality)
        self.assertEqual(items[1].price_czk, 19500)
        house = items[2]
        self.assertTrue(house.url.endswith("/detail/pronajem/dum/praha-8-sedlecka/6aab1889b1a909eb5300b367/"))
        self.assertEqual(house.price_czk, 48000)
        self.assertEqual(house.extras.get("estate"), "Dům")
        self.assertIn("Kobylisy", house.locality)
        croatia = items[3]
        self.assertTrue(croatia.url.endswith("/detail/pronajem/byt/rijeka/6aab5287292ee166be09a05b/"))
        self.assertIn("Chorvatsko", croatia.locality)
        self.assertEqual(client._parse_total(html), 8519)

    def test_fetch_page_uses_local_pins_not_network_geocode(self):
        html = (FIXTURES / "idnes_cards.html").read_text()
        geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("/s/pronajem/byty/", str(request.url))
            return httpx.Response(200, text=html)

        async def _run() -> None:
            client = IdnesClient("https://reality.idnes.cz/s/pronajem/byty/")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            with patch("app.places.geocode_locality", geocode):
                listings, total = await client.fetch_page(1, newest=True)
            await client.aclose()
            self.assertEqual(len(listings), 4)
            self.assertEqual(total, 8519)
            geocode.assert_not_called()
            praha = approx_point_from_locality("Kolbenova, Praha 9 - Vysočany")
            ostrava = approx_point_from_locality("Lechowiczova, Ostrava - Moravská Ostrava")
            self.assertEqual((listings[1].lat, listings[1].lon), praha)
            self.assertEqual((listings[0].lat, listings[0].lon), ostrava)
            rijeka = approx_point_from_locality("Rijeka, Primorsko-goranska županija, Chorvatsko")
            self.assertEqual((listings[3].lat, listings[3].lon), rijeka)
            self.assertIsNotNone(rijeka)
            self.assertNotEqual(rijeka, approx_point_from_locality("Chorvatsko"))
            unknown = approx_point_from_locality("Gornja Fužina, Chorvatsko")
            self.assertEqual(unknown, approx_point_from_locality("Chorvatsko"))

        asyncio.run(_run())

    def test_page_url_canonicalizes_singular_house_paths(self):
        client = IdnesClient("https://reality.idnes.cz/s/pronajem/dum/")
        page1 = client._page_url(1, newest=True)
        self.assertIn("/s/pronajem/domy/", page1)
        self.assertNotIn("/dum/", page1)
        self.assertNotIn("/praha/", page1)
        page2 = client._page_url(2, newest=True)
        self.assertIn("/s/pronajem/domy/", page2)
        self.assertIn("page=1", page2)
        sale = IdnesClient("https://reality.idnes.cz/s/prodej/byt/")
        self.assertIn("/s/prodej/byty/", sale._page_url(1, newest=True))

    def test_fetch_page_rewrites_dum_404_path(self):
        html = (FIXTURES / "idnes_cards.html").read_text()
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            self.assertIn("/s/pronajem/domy/", str(request.url))
            self.assertNotIn("/dum/", str(request.url))
            return httpx.Response(200, text=html)

        async def _run() -> None:
            client = IdnesClient("https://reality.idnes.cz/s/pronajem/dum/")
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            listings, total = await client.fetch_page(1, newest=True)
            await client.aclose()
            self.assertEqual(total, 8519)
            self.assertEqual(len(listings), 4)
            self.assertTrue(any("/s/pronajem/domy/" in url for url in hits))
            house = next(item for item in listings if "/dum/" in item.url)
            self.assertEqual(house.extras.get("estate"), "Dům")
            self.assertEqual(house.price_czk, 48000)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
