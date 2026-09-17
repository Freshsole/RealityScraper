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
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id, oid_to_int("6aab2c79f0d2c87c830c6141"))
        self.assertTrue(items[0].url.endswith("/6aab2c79f0d2c87c830c6141/"))
        self.assertEqual(items[0].price_czk, 16000)
        self.assertIn("Ostrava", items[0].locality)
        self.assertEqual(items[1].price_czk, 19500)
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
            self.assertEqual(len(listings), 2)
            self.assertEqual(total, 8519)
            geocode.assert_not_called()
            praha = approx_point_from_locality("Kolbenova, Praha 9 - Vysočany")
            ostrava = approx_point_from_locality("Lechowiczova, Ostrava - Moravská Ostrava")
            self.assertEqual((listings[1].lat, listings[1].lon), praha)
            self.assertEqual((listings[0].lat, listings[0].lon), ostrava)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
