"""Consistency checks for content/facts.yaml vs public AI-facing copy."""

from __future__ import annotations

import unittest

from app import billing, facts, market_pages


class FactsConsistencyTests(unittest.TestCase):
    def test_facts_load(self) -> None:
        data = facts.get_facts()
        self.assertEqual(data["product"]["name"], "Realitify")
        self.assertEqual(data["operator"]["ico"], "21527059")
        self.assertEqual(len(facts.portal_labels()), 9)

    def test_portal_labels_match_market_pages(self) -> None:
        self.assertEqual(tuple(market_pages.PUBLIC_PORTAL_LABELS), facts.portal_labels())

    def test_billing_features_no_inflated_portal_counts(self) -> None:
        blob = " ".join(
            " ".join(plan.get("features") or []) for plan in billing.PLANS.values()
        )
        self.assertNotIn("12+", blob)
        self.assertNotIn("20+", blob)

    def test_llms_txt_no_duplicate_rent_index(self) -> None:
        txt = market_pages.llms_txt()
        self.assertEqual(txt.count("[Rent index]"), 1)
        self.assertIn("Last-Updated:", txt)
        self.assertIn("llms-full.txt", txt)

    def test_dataset_helpers(self) -> None:
        creator = facts.dataset_creator()
        self.assertEqual(creator["@type"], "Organization")
        self.assertTrue(facts.dataset_license().startswith("http"))
        dist = facts.dataset_distribution("https://realitify.cz/index.csv")
        self.assertEqual(dist["@type"], "DataDownload")
        self.assertEqual(dist["encodingFormat"], "text/csv")

    def test_methodology_thresholds(self) -> None:
        self.assertEqual(market_pages.MIN_ACTIVE_CREATE, 20)
        self.assertEqual(market_pages.MIN_ACTIVE_KEEP, 12)


if __name__ == "__main__":
    unittest.main()
