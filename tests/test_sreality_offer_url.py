from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.sreality import (
    build_detail_url,
    listing_from_raw,
    rewrite_sreality_detail_offer,
    sreality_row_is_sale,
)
from app.store import Store


SALE_RAW = {
    "id": 3689492556,
    "name": "Prodej bytu 2+kk 67 m²",
    "priceCzk": 9999000,
    "priceUnitCb": {"name": "za nemovitost", "value": 1},
    "categoryTypeCb": {"name": "Prodej", "value": 1},
    "categoryMainCb": {"name": "Byty", "value": 1},
    "categorySubCb": {"name": "2+kk", "value": 4},
    "locality": {
        "citySeoName": "praha",
        "cityPartSeoName": "holesovice",
        "streetSeoName": "",
    },
}

RENT_RAW = {
    "id": 42,
    "name": "Pronájem bytu 2+kk 50 m²",
    "priceCzk": 22000,
    "priceUnitCb": {"name": "za měsíc", "value": 3},
    "categoryTypeCb": {"name": "Pronájem", "value": 2},
    "categoryMainCb": {"name": "Byty", "value": 1},
    "categorySubCb": {"name": "2+kk", "value": 4},
    "locality": {"citySeoName": "praha", "cityPartSeoName": "vinohrady"},
}


class SrealityOfferUrlTests(unittest.TestCase):
    def test_sale_list_url_uses_prodej(self):
        listing = listing_from_raw(SALE_RAW)
        self.assertIsNotNone(listing)
        assert listing is not None
        self.assertIn("/detail/prodej/", listing.url)
        self.assertNotIn("/detail/pronajem/", listing.url)
        self.assertEqual(listing.extras.get("offer"), "Prodej")
        self.assertTrue(listing.url.endswith("/3689492556"))

    def test_rent_list_url_stays_pronajem(self):
        listing = listing_from_raw(RENT_RAW)
        self.assertIsNotNone(listing)
        assert listing is not None
        self.assertIn("/detail/pronajem/", listing.url)

    def test_search_url_fallback_when_type_missing(self):
        raw = {**SALE_RAW, "categoryTypeCb": {}}
        url = build_detail_url(
            raw,
            search_url="https://www.sreality.cz/hledani/prodej/byty/praha-7?velikost=2%2Bkk",
        )
        self.assertIn("/detail/prodej/", url)

    def test_rewrite_helper(self):
        src = "https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-holesovice/1"
        self.assertIn("/detail/prodej/", rewrite_sreality_detail_offer(src, "prodej"))

    def test_sale_heuristic_ignores_wrong_url(self):
        self.assertTrue(sreality_row_is_sale({}, "9 999 000 Kč/nemovitost", 9999000))
        self.assertFalse(sreality_row_is_sale({}, "22 000 Kč/měsíc", 22000))

    def test_store_rewrites_existing_sale_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "monitor.sqlite")
            listing = listing_from_raw(SALE_RAW)
            assert listing is not None
            listing.url = listing.url.replace("/prodej/", "/pronajem/")
            store.upsert_catalog_listing(listing, kind="seeded")
            with store.connect() as conn:
                conn.execute("DELETE FROM meta WHERE key = 'sreality_offer_urls_v1'")
                store._repair_sreality_offer_urls(conn)
                row = conn.execute(
                    "SELECT url FROM catalog_listings WHERE CAST(id AS TEXT) = ?",
                    ("3689492556",),
                ).fetchone()
            self.assertIn("/detail/prodej/", row["url"])


if __name__ == "__main__":
    unittest.main()
