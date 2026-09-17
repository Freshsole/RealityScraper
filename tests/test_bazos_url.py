import unittest

from app import bazos_url
from app.catalog_sync import daily_shards, listing_matches_filters, normalize_portals, normalize_search_url
from app.filter_bridge import search_urls_for_portals, sr_to_bazos


class BazosUrlTests(unittest.TestCase):
    def test_build_and_parse_roundtrip(self):
        url = bazos_url.build_url(
            {
                "offers": ["pronajem"],
                "category": "byt",
                "districts": ["praha"],
                "sizes": ["2+kk"],
                "price_from": 10000,
                "price_to": 35000,
                "radius": 20,
            }
        )
        self.assertIn("reality.bazos.cz/pronajmu/byt/", url)
        self.assertIn("hledat=2%2Bkk", url)
        self.assertIn("hlokalita=Praha", url)
        self.assertIn("kitx=ano", url)
        parsed = bazos_url.parse_url(url)
        self.assertEqual(parsed["offers"], ["pronajem"])
        self.assertEqual(parsed["category"], "byt")
        self.assertEqual(parsed["districts"], ["praha"])
        self.assertEqual(parsed["sizes"], ["2+kk"])
        self.assertEqual(parsed["price_from"], 10000)
        self.assertEqual(parsed["price_to"], 35000)

    def test_multiple_sizes_stay_out_of_query(self):
        url = bazos_url.build_url({"offers": ["pronajem"], "category": "byt", "sizes": ["2+kk", "3+kk"], "districts": ["praha"]})
        self.assertNotIn("hledat=", url)
        self.assertIn("velikost=2%2Bkk%2C3%2Bkk", url)
        parsed = bazos_url.parse_url(url)
        self.assertEqual(parsed["sizes"], ["2+kk", "3+kk"])

    def test_search_urls_include_bazos(self):
        urls = search_urls_for_portals("https://www.sreality.cz/hledani/pronajem/byty/praha?velikost=2%2Bkk")
        self.assertIn("bazos", urls)
        self.assertIn("reality.bazos.cz/pronajmu/byt/", urls["bazos"])
        self.assertIn("hledat=2%2Bkk", urls["bazos"])

    def test_normalize_portals(self):
        self.assertEqual(normalize_portals("bazos"), "bazos")
        self.assertTrue(normalize_search_url("https://reality.bazos.cz/prodam/dum/").startswith("https://reality.bazos.cz/"))
        self.assertIn("/pronajmu/byt/", normalize_search_url("https://reality.bazos.cz/pronajem/byty/"))
        self.assertIn("/prodam/dum/", normalize_search_url("https://reality.bazos.cz/prodej/domy/"))

    def test_parse_plural_and_sreality_offer_aliases(self):
        parsed = bazos_url.parse_url("https://reality.bazos.cz/pronajem/byty/")
        self.assertEqual(parsed["offers"], ["pronajem"])
        self.assertEqual(parsed["category"], "byt")
        parsed_house = bazos_url.parse_url("https://reality.bazos.cz/prodam/domy/")
        self.assertEqual(parsed_house["offers"], ["prodej"])
        self.assertEqual(parsed_house["category"], "dum")
        self.assertEqual(bazos_url.page_url("https://reality.bazos.cz/pronajem/byty/?order=1", 2), "https://reality.bazos.cz/pronajmu/byt/20/?kitx=ano")

    def test_daily_shards_cover_flats_and_houses(self):
        shards = [item for item in daily_shards() if item["portal"] == "bazos"]
        urls = {item["search_url"] for item in shards}
        self.assertTrue(any("/prodam/dum/" in url for url in urls))
        self.assertTrue(any("/pronajmu/byt/" in url and "hlokalita=" not in url for url in urls))
        self.assertTrue(any("/prodam/pozemek/" in url and "hlokalita=" not in url for url in urls))
        self.assertEqual(len(shards), 26)

    def test_sr_to_bazos_sizes(self):
        dst, skipped, notes = sr_to_bazos({"offers": ["pronajem"], "sizes": ["2+kk", "6-a-vice"], "districts": ["praha"]})
        self.assertEqual(dst["sizes"], ["2+kk", "6-a-vice"])
        self.assertFalse(skipped)
        self.assertTrue(any("dispozici" in item for item in notes))

    def test_listing_matches_filters_after_broad_search(self):
        listing = {
            "url": "https://reality.bazos.cz/inzerat/1/x.php",
            "price_czk": 22000,
            "area_m2": 44,
            "disposition": "2+kk",
            "locality": "Praha 7",
            "extras": {"offer": "Pronájem"},
        }
        filters = bazos_url.parse_url(
            bazos_url.build_url({"offers": ["pronajem"], "category": "byt", "sizes": ["2+kk", "3+kk"], "districts": ["praha"], "price_to": 35000})
        )
        self.assertTrue(listing_matches_filters(listing, filters, ignore_source=True))
        listing["disposition"] = "1+kk"
        self.assertFalse(listing_matches_filters(listing, filters, ignore_source=True))


if __name__ == "__main__":
    unittest.main()
