"""Hysteresis thresholds for /trh SEO pages."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.market_pages import (
    MIN_ACTIVE_CREATE,
    MIN_ACTIVE_KEEP,
    decide_market_page_active,
    evaluate_market_page,
)
from app.store import Store


class MarketPageHysteresisTests(unittest.TestCase):
    def test_constants(self) -> None:
        self.assertEqual(MIN_ACTIVE_CREATE, 20)
        self.assertEqual(MIN_ACTIVE_KEEP, 12)
        self.assertLess(MIN_ACTIVE_KEEP, MIN_ACTIVE_CREATE)

    def test_decide_hysteresis(self) -> None:
        self.assertFalse(decide_market_page_active(False, 19))
        self.assertTrue(decide_market_page_active(False, 20))
        self.assertTrue(decide_market_page_active(True, 12))
        self.assertFalse(decide_market_page_active(True, 11))
        self.assertTrue(decide_market_page_active(True, 15))

    def test_persists_active_state(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        store = Store(Path(tmp.name))

        # Simulate create: force counts via set after evaluate with empty catalog → inactive
        active, count = evaluate_market_page(store, "Praha", "pronajem")
        self.assertFalse(active)
        self.assertEqual(count, 0)
        row = store.get_market_seo_page("Praha", "pronajem")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertFalse(row["active"])

        # Mark as previously active with mid count → stays active (≥ KEEP)
        store.set_market_seo_page("Smíchov", "pronajem", active=True, quality_count=50)
        store.set_market_seo_page("Smíchov", "pronajem", active=decide_market_page_active(True, 15), quality_count=15)
        row = store.get_market_seo_page("Smíchov", "pronajem")
        assert row is not None
        self.assertTrue(row["active"])
        self.assertEqual(row["quality_count"], 15)

        # Drop below KEEP → inactive
        store.set_market_seo_page(
            "Smíchov",
            "pronajem",
            active=decide_market_page_active(True, 11),
            quality_count=11,
        )
        row = store.get_market_seo_page("Smíchov", "pronajem")
        assert row is not None
        self.assertFalse(row["active"])


if __name__ == "__main__":
    unittest.main()
