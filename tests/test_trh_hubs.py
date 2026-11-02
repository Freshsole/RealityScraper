"""/trh hub pages: plain <a href> links, unique title/h1 per page."""

from __future__ import annotations

import re
import unittest

from app import market_seo


ROWS = [
    {"locality": "Brno", "offer": "prodej", "active_count": 500, "kind": "city"},
    {"locality": "Brno", "offer": "pronajem", "active_count": 300, "kind": "city"},
    {"locality": "Praha", "offer": "pronajem", "active_count": 900, "kind": "city"},
]
SUM = {
    ("Brno", "prodej", ""): {"median_price_per_m2": 130000, "median_price": 6000000, "active_count": 500},
    ("Brno", "pronajem", ""): {"median_price_per_m2": 330, "median_price": 20000, "active_count": 300},
    ("Praha", "pronajem", ""): {"median_price_per_m2": 400, "median_price": 25000, "active_count": 900},
}


class HubTests(unittest.TestCase):
    def test_hub_links_all_localities(self) -> None:
        html = market_seo.render_trh_hub(ROWS, SUM, month="listopad 2026")
        for href in ("/trh/brno", "/trh/brno/prodej", "/trh/brno/pronajem", "/trh/praha", "/trh/praha/pronajem"):
            self.assertIn(f'href="{href}"', html)
        self.assertIn('rel="canonical" href="https://realitify.cz/trh"', html)
        self.assertIn("listopad 2026", html)

    def test_locality_hub_links_dispositions_both_offers(self) -> None:
        offers = {r["offer"]: r for r in ROWS if r["locality"] == "Brno"}
        html = market_seo.render_trh_locality_hub(
            "Brno", offers, {"prodej": ["2+1"], "pronajem": ["2+1", "3+kk"]}, SUM, [], month="listopad 2026"
        )
        for href in ("/trh/brno/prodej/2-1", "/trh/brno/pronajem/2-1", "/trh/brno/pronajem/3kk", "/trh"):
            self.assertIn(f'href="{href}"', html)

    def test_title_unique_and_dated(self) -> None:
        rep = {"active_count": 50, "median_price": 20000, "median_price_per_m2": 330, "updated_at": "2026-11-02"}
        a, _ = market_seo.build_title_description("Brno", "pronajem", rep, "2+1")
        b, _ = market_seo.build_title_description("Brno", "pronajem", rep, "3+1")
        self.assertEqual(a, "Pronájem bytů 2+1 Brno – ceny a nabídky (listopad 2026)")
        self.assertNotEqual(a, b)
        self.assertIsNone(re.search(r"\|\s*Realitify", a))


if __name__ == "__main__":
    unittest.main()
