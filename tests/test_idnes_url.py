import unittest

from app import idnes_url
from app.catalog_sync import normalize_portals, normalize_search_url
from app.filter_bridge import search_urls_for_portals, sr_to_idnes
from app.idnes import JS_SAFE_ID, oid_to_int


class IdnesUrlTests(unittest.TestCase):
    def test_build_and_parse_roundtrip(self):
        url = idnes_url.build_url(
            {
                "offers": ["pronajem"],
                "category": "byty",
                "districts": ["praha"],
                "sizes": ["2-kk", "3-kk"],
                "ownership": ["osobni"],
                "conditions": ["novostavba"],
                "buildings": ["cihlova"],
                "extras": ["balkon", "internet"],
                "flags": ["video", "sale"],
                "article_age": "7",
                "price_from": 100,
                "price_to": 50001,
                "area_from": 20,
                "area_to": 2000,
            }
        )
        self.assertIn("reality.idnes.cz/s/pronajem/byty/praha/", url)
        self.assertIn("dispozice=2-kk|3-kk", url.replace("%7C", "|"))
        parsed = idnes_url.parse_url(url)
        self.assertEqual(parsed["offers"], ["pronajem"])
        self.assertEqual(parsed["category"], "byty")
        self.assertEqual(parsed["districts"], ["praha"])
        self.assertEqual(parsed["sizes"], ["2-kk", "3-kk"])
        self.assertEqual(parsed["price_from"], 100)
        self.assertIn("video", parsed["flags"])
        self.assertEqual(parsed["article_age"], "7")

    def test_category_aliases_build_real_paths(self):
        url = idnes_url.build_url({"offers": ["pronajem"], "category": "komercni", "districts": ["praha"]})
        self.assertIn("/pronajem/komercni-nemovitosti/praha/", url)
        parsed = idnes_url.parse_url("https://reality.idnes.cz/s/prodej/male-objekty-garaze/brno/")
        self.assertEqual(parsed["category"], "male-objekty-garaze")
        self.assertEqual(idnes_url.canonical_category("dum"), "domy")
        self.assertEqual(idnes_url.canonical_category("byt"), "byty")
        self.assertEqual(idnes_url.canonical_category("pozemek"), "pozemky")
        self.assertIn("/pronajem/domy/", idnes_url.build_url({"offers": ["pronajem"], "category": "dum"}))
        self.assertNotIn("/byty/", idnes_url.build_url({"offers": ["pronajem"], "category": "dum"}))

    def test_nationwide_parse_does_not_inject_praha(self):
        parsed = idnes_url.parse_url("https://reality.idnes.cz/s/pronajem/byty/")
        self.assertEqual(parsed["districts"], [])
        self.assertEqual(parsed["category"], "byty")
        self.assertEqual(idnes_url.build_url(parsed), "https://reality.idnes.cz/s/pronajem/byty/")
        self.assertEqual(
            normalize_search_url("https://reality.idnes.cz/s/pronajem/dum/"),
            "https://reality.idnes.cz/s/pronajem/domy/",
        )
        self.assertEqual(
            normalize_search_url("https://reality.idnes.cz/s/prodej/domy/"),
            "https://reality.idnes.cz/s/prodej/domy/",
        )
        self.assertEqual(
            idnes_url.page_url("https://reality.idnes.cz/s/pronajem/byt/?s-l-rq=1", 1),
            "https://reality.idnes.cz/s/pronajem/byty/",
        )

    def test_search_urls_include_idnes(self):
        urls = search_urls_for_portals(
            "https://www.sreality.cz/hledani/pronajem/byty/praha?velikost=2%2Bkk"
        )
        self.assertIn("sreality", urls)
        self.assertIn("bezrealitky", urls)
        self.assertIn("idnes", urls)
        self.assertIn("reality.idnes.cz/s/pronajem/byty/praha/", urls["idnes"])

    def test_normalize_portals(self):
        self.assertEqual(normalize_portals("idnes"), "idnes")
        self.assertEqual(normalize_search_url("https://reality.idnes.cz/s/prodej/domy/"), "https://reality.idnes.cz/s/prodej/domy/")

    def test_daily_shards_use_real_idnes_paths(self):
        from app.catalog_sync import daily_shards

        shards = [item for item in daily_shards() if item["portal"] == "idnes"]
        urls = {item["search_url"] for item in shards}
        self.assertTrue(any("/komercni-nemovitosti/" in url for url in urls))
        self.assertTrue(any("/male-objekty-garaze/" in url for url in urls))
        self.assertFalse(any("/s/pronajem/komercni/" in url for url in urls))
        self.assertGreater(len(shards), 200)
        value = oid_to_int("6a5a0f6686ba1a60050d59cb")
        self.assertLessEqual(value, JS_SAFE_ID)
        self.assertEqual(value, oid_to_int("6a5a0f6686ba1a60050d59cb"))

    def test_sr_to_idnes_sizes(self):
        dst, skipped, _notes = sr_to_idnes({"offers": ["pronajem"], "sizes": ["2+kk", "6-a-vice"], "districts": ["praha"]})
        self.assertEqual(dst["sizes"], ["2-kk", "6-kk-a-vetsi"])
        self.assertFalse(any("2+kk" in item for item in skipped))


if __name__ == "__main__":
    unittest.main()
