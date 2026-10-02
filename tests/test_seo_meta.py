"""SEO title/description hygiene and index archive rules."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import market_pages, market_seo
from app.store import Store

FORBIDDEN = ("…", "...", "None", "null", "NaN", "—")


def _assert_clean_meta(title: str, description: str) -> None:
    assert title and title.strip(), "empty title"
    assert description and description.strip(), "empty description"
    blob = f"{title}\n{description}"
    for bad in FORBIDDEN:
        assert bad not in blob, f"forbidden {bad!r} in {blob!r}"
    assert re.search(r"\bNone\b", blob) is None
    assert re.search(r"\bnull\b", blob, re.I) is None
    assert re.search(r"\bNaN\b", blob) is None


class SeoMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))
        now = datetime.now(timezone.utc).isoformat()
        extras = json.dumps({"offer": "Pronájem", "estate": "Byt"}, ensure_ascii=False)
        prev = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        with self.store.connect() as conn:
            for i in range(25):
                conn.execute(
                    """
                    INSERT INTO listings(
                      id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                      locality, url, first_seen, last_seen, gone, extras
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)
                    """,
                    (
                        2000 + i,
                        "t",
                        f"Byt {i}",
                        20000 + i * 100,
                        f"{20000 + i * 100} Kč/měsíc",
                        "2+kk",
                        40 + (i % 10),
                        "Praha 5 - Smíchov",
                        f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-5/{2000 + i}",
                        now,
                        now,
                        extras,
                    ),
                )
            for i in range(25):
                conn.execute(
                    """
                    INSERT INTO listings(
                      id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                      locality, url, first_seen, last_seen, gone, extras
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)
                    """,
                    (
                        3000 + i,
                        "t",
                        f"Byt archiv {i}",
                        21000 + i * 100,
                        f"{21000 + i * 100} Kč/měsíc",
                        "2+kk",
                        42 + (i % 8),
                        "Praha 1",
                        f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{3000 + i}",
                        f"{prev}-15T12:00:00+00:00",
                        now,
                        extras,
                    ),
                )

    def test_trh_title_description_clean(self) -> None:
        html = market_seo.render_trh_page(self.store, "Smíchov", "pronajem")
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        desc = re.search(r'name="description" content="(.*?)"', html).group(1)
        _assert_clean_meta(title, desc)
        self.assertIn("–", title)
        self.assertIn("medián", title)

    def test_index_month_title_czech_and_clean(self) -> None:
        months = self.store.list_index_months(completed_only=True)
        self.assertTrue(months)
        month = months[0]
        html = market_seo.render_index_month(self.store, month)
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        desc = re.search(r'name="description" content="(.*?)"', html).group(1)
        _assert_clean_meta(title, desc)
        self.assertTrue(title.startswith("Realitify index nájmů – "))
        self.assertNotIn(month, title)
        self.assertRegex(desc, r"\d")

    def test_current_month_not_in_archive_list(self) -> None:
        current = date.today().strftime("%Y-%m")
        self.assertNotIn(current, self.store.list_index_months(completed_only=True))
        with self.assertRaises(ValueError):
            self.store.index_month_city_stats(current)

    def test_index_live_meta_clean(self) -> None:
        html = market_pages.render_index(self.store)
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        desc = re.search(r'name="description" content="(.*?)"', html).group(1)
        _assert_clean_meta(title, desc)
        self.assertIn("průběžně k", title)


if __name__ == "__main__":
    unittest.main()
