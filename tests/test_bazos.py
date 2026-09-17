import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import unittest

from app.bazos import BazosClient, parse_disposition, parse_photos, parse_price, upgrade_photo
from app.places import approx_point_from_locality
from app.sreality import Listing

FIXTURES = Path(__file__).parent / "fixtures"
CARDS = (FIXTURES / "bazos_cards.html").read_text()

INLINE_CARD = """
<div class="inzeraty inzeratyflex">
<div class="inzeratynadpis"><a href="/inzerat/223238855/pronajem-hezkeho-bytu-2kk-praha-7.php"><img src="https://www.bazos.cz/img/1t/855/223238855.jpg?t=1" class="obrazek" alt="alt"></a>
<h2 class=nadpis><a href="/inzerat/223238855/pronajem-hezkeho-bytu-2kk-praha-7.php">Pronájem hezkého bytu 2+kk, Praha 7-Holešovice</a></h2><span> - [14.9. 2026]</span>
<div class=popis>Ke dlouhodobému pronájmu je Vám k dispozici tento hezký byt <b>2+kk</b>, 44 m2, Praha 7, dům s výtahem.</div>
</div>
<div class="inzeratycena"><b><span translate="no">  24 000 Kč</span></b></div>
<div class="inzeratylok">Praha 7<br>170 00</div>
<div class="inzeratyview">505 x</div>
<div class="inzeratyakce"></div>
</div>
"""

DETAIL = """
<h1 class=nadpisdetail>Pronájem hezkého bytu 2+kk, Praha 7</h1>
<div class="inzeratylok">Praha 7<br>170 00</div>
<div class="inzeratycena"><b>24 000 Kč</b></div>
<div class="popisdetail">Byt 2+kk, 44 m2 ve 4. patře s výtahem a balkonem. https://www.google.com/maps/place/50.1023,14.4391</div>
<img src="https://www.bazos.cz/img/1t/855/223238855.jpg">
<img src="https://www.bazos.cz/img/2/855/223238855.jpg">
"""


class BazosParseTests(unittest.TestCase):
    def test_upgrade_thumb(self):
        self.assertEqual(
            upgrade_photo("https://www.bazos.cz/img/1t/855/223238855.jpg"),
            "https://www.bazos.cz/img/1/855/223238855.jpg",
        )

    def test_parse_disposition(self):
        self.assertEqual(parse_disposition("Pronájem bytu 2+kk 44 m2"), "2+kk")
        self.assertEqual(parse_disposition("garsonka u metra"), "1+kk")

    def test_list_cards(self):
        client = BazosClient("https://reality.bazos.cz/pronajmu/byt/?hledat=2%2Bkk&kitx=ano")
        items = client._parse_list(INLINE_CARD)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.id, 223238855)
        self.assertEqual(item.disposition, "2+kk")
        self.assertEqual(item.area_m2, 44)
        self.assertEqual(item.price_czk, 24000)
        self.assertIn("Praha 7", item.locality)
        self.assertEqual(item.extras["offer"], "Pronájem")
        self.assertTrue(item.image_url.endswith("/img/1/855/223238855.jpg"))

    def test_list_cards_fill_sale_house_urls_price_gps(self):
        rent_client = BazosClient("https://reality.bazos.cz/pronajmu/byt/?kitx=ano")
        rent_items = rent_client._parse_list(CARDS)
        self.assertEqual([item.id for item in rent_items], [223238855, 223393801, 223476429])

        rent = rent_items[0]
        self.assertEqual(rent.url, "https://reality.bazos.cz/inzerat/223238855/pronajem-hezkeho-bytu-2kk-praha-7.php")
        self.assertEqual(rent.price_czk, 24000)
        self.assertTrue(rent.image_url.endswith("/img/1/855/223238855.jpg"))
        self.assertIn("Praha 7", rent.locality)
        self.assertAlmostEqual(rent.lat, 50.1023)
        self.assertAlmostEqual(rent.lon, 14.4391)
        self.assertEqual(rent.extras.get("offer"), "Pronájem")
        self.assertEqual(rent.extras.get("estate"), "Byt")

        house_client = BazosClient("https://reality.bazos.cz/prodam/dum/?kitx=ano")
        house = house_client._parse_list(CARDS)[1]
        self.assertEqual(house.url, "https://reality.bazos.cz/inzerat/223393801/rodinny-dum-se-zahradou.php")
        self.assertEqual(house.price_czk, 5667000)
        self.assertEqual(house.extras.get("offer"), "Prodej")
        self.assertEqual(house.extras.get("estate"), "Dům")
        self.assertIn("Frýdek", house.locality)
        self.assertIsNone(house.lat)

        free = rent_items[2]
        self.assertIsNone(free.price_czk)
        self.assertEqual(free.price_label, "Cena neuvedena")
        self.assertIsNone(free.image_url)
        self.assertEqual(rent_client._parse_total(CARDS), 5632)

    def test_unpriced_labels_are_cena_neuvedena(self):
        self.assertEqual(parse_price("Dohodou")[1], "Cena neuvedena")
        self.assertEqual(parse_price("Nabídněte")[1], "Cena neuvedena")
        self.assertEqual(parse_price("V textu")[1], "Cena neuvedena")
        self.assertIsNone(parse_price("Zdarma")[0])

    def test_page_url_canonicalizes_aliases_and_path_offset(self):
        client = BazosClient("https://reality.bazos.cz/pronajem/byty/?order=1&crp=20")
        page1 = client._page_url(1)
        self.assertIn("/pronajmu/byt/", page1)
        self.assertNotIn("/pronajem/", page1)
        self.assertNotIn("/byty/", page1)
        self.assertNotIn("order=", page1)
        self.assertNotIn("crp=", page1)
        self.assertIn("kitx=ano", page1)
        page2 = client._page_url(2)
        self.assertIn("/pronajmu/byt/20/", page2)
        house = BazosClient("https://reality.bazos.cz/prodej/domy/")
        self.assertIn("/prodam/dum/", house._page_url(1))
        self.assertIn("/prodam/dum/20/", house._page_url(2))

    def test_detail_coords_and_photos(self):
        listing = Listing(
            id=223238855,
            name="x",
            price_czk=1,
            price_label="1",
            disposition="2+kk",
            area_m2=44,
            locality="",
            url="https://reality.bazos.cz/inzerat/223238855/x.php",
            image_url=None,
        )
        client = BazosClient("https://reality.bazos.cz/pronajmu/byt/")
        parsed = client._parse_detail(listing, DETAIL)
        self.assertAlmostEqual(parsed.lat, 50.1023)
        self.assertAlmostEqual(parsed.lon, 14.4391)
        self.assertGreaterEqual(len(parse_photos(DETAIL)), 2)
        self.assertIn("lift", parsed.extras.get("flags") or [])

    def test_fetch_page_uses_html_gps_not_photon(self):
        geocode = AsyncMock(side_effect=AssertionError("list fetch must not hit Nominatim/Photon"))
        hits: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            return httpx.Response(200, text=CARDS)

        async def _run() -> None:
            client = BazosClient("https://reality.bazos.cz/pronajem/byty/?order=1")
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
            self.assertEqual(total, 5632)
            self.assertEqual(len(listings), 3)
            geocode.assert_not_called()
            self.assertTrue(any("/pronajmu/byt/" in url for url in hits))
            self.assertFalse(any("/pronajem/byty" in url for url in hits))
            by_id = {item.id: item for item in listings}
            self.assertAlmostEqual(by_id[223238855].lat, 50.1023)
            pin = approx_point_from_locality("Frýdek - Místek")
            self.assertIsNotNone(pin)
            self.assertEqual((by_id[223393801].lat, by_id[223393801].lon), pin)
            praha1 = approx_point_from_locality("Praha 1")
            self.assertEqual((by_id[223476429].lat, by_id[223476429].lon), praha1)
            self.assertNotEqual((by_id[223238855].lat, by_id[223238855].lon), approx_point_from_locality("Praha 7"))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
