from __future__ import annotations

import unittest
from unittest.mock import patch

from app import billing


class PromoResolveTests(unittest.TestCase):
    def test_empty_code(self):
        self.assertEqual(billing.resolve_promo_code("")["code"], "")

    def test_bad_format(self):
        with self.assertRaises(ValueError):
            billing.resolve_promo_code("!!")

    def test_deferred_without_stripe(self):
        with patch.object(billing.config, "STRIPE_SECRET_KEY", ""):
            got = billing.resolve_promo_code("TEST20")
        self.assertEqual(got["code"], "TEST20")
        self.assertTrue(got.get("deferred"))
        self.assertEqual(got["percent"], 0)
        self.assertEqual(billing.pending_promo_for_signup("test20"), "TEST20")


if __name__ == "__main__":
    unittest.main()
