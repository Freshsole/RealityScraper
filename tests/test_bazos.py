import unittest

from app.bazos import BazosClient, parse_disposition, parse_photos, upgrade_photo
from app.sreality import Listing

CARDS = """
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
        items = client._parse_list(CARDS)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.id, 223238855)
        self.assertEqual(item.disposition, "2+kk")
        self.assertEqual(item.area_m2, 44)
        self.assertEqual(item.price_czk, 24000)
        self.assertIn("Praha 7", item.locality)
        self.assertEqual(item.extras["offer"], "Pronájem")
        self.assertTrue(item.image_url.endswith("/img/1/855/223238855.jpg"))

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


if __name__ == "__main__":
    unittest.main()
