"""Regresní testy pro UI opravy (2026-09-30): výchozí hodnoty filtrů bez podivných čísel."""
from __future__ import annotations

import unittest

from app.url_builder import build_url, default_filters


class DefaultFiltersUiTest(unittest.TestCase):
    def test_no_weird_numeric_defaults(self):
        defaults = default_filters()
        # Audit našel předvyplněné "25759" (cena do) a "45" (plocha od) — pole musí být prázdná.
        self.assertIsNone(defaults["price_to"])
        self.assertIsNone(defaults["area_from"])
        self.assertIsNone(defaults["price_from"])
        self.assertIsNone(defaults["area_to"])

    def test_empty_area_not_in_url(self):
        defaults = default_filters()
        url = build_url(defaults)
        self.assertNotIn("uzitna-plocha-od", url)
        self.assertNotIn("cena-do", url)

    def test_poi_distance_internal_fallback(self):
        # Prázdné pole v UI (None) se interně chová jako 2 km, když je POI aktivní.
        filters = default_filters()
        filters["pois"] = ["13"]  # Metro
        filters["poi_distance"] = None
        url = build_url(filters)
        self.assertIn("pois_in_place_distance=2", url)


if __name__ == "__main__":
    unittest.main()
