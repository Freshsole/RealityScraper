"""Regression tests for catalog data-quality fixes (stale gone, flats-only, id merge, price, area)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.catalog_sync import daily_shards, is_apartment_listing
from app.html_listing import parse_area, parse_price
from app.sreality import Listing, format_price, listing_from_raw
from app.store import Store


def _listing(**kwargs) -> Listing:
    base = dict(
        id=1001,
        name="Byt Praha",
        price_czk=20000,
        price_label="20 000 Kč/měsíc",
        disposition="2+kk",
        area_m2=48,
        locality="Praha 3",
        url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-3/1001",
        image_url=None,
        lat=50.08,
        lon=14.43,
        extras={"offer": "Pronájem", "estate": "Byt"},
    )
    base.update(kwargs)
    return Listing(**base)


class StaleGoneSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def test_sweep_marks_ten_day_old_live_row_gone(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.store.upsert_catalog_listing(_listing(), kind="new")
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE catalog_listings SET last_seen = ?, gone = 0",
                (old,),
            )
        n = self.store.sweep_catalog_stale_gone(72)
        self.assertGreaterEqual(n, 1)
        with self.store.connect() as conn:
            row = conn.execute("SELECT gone, last_seen FROM catalog_listings").fetchone()
            self.assertEqual(row["gone"], 1)
            self.assertEqual(row["last_seen"], old)


class ApartmentFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        daily_shards.cache_clear()
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def test_daily_shards_are_apartments_only(self) -> None:
        shards = daily_shards()
        idnes = [s for s in shards if s["portal"] == "idnes"]
        bazos = [s for s in shards if s["portal"] == "bazos"]
        self.assertTrue(idnes)
        self.assertTrue(bazos)
        self.assertTrue(all("/byty/" in s["search_url"] for s in idnes))
        self.assertTrue(all("/byt/" in s["search_url"] for s in bazos))
        self.assertFalse(any("/domy/" in s["search_url"] or "/pozemky/" in s["search_url"] for s in idnes))
        self.assertFalse(any("/dum/" in s["search_url"] or "/pozemek/" in s["search_url"] for s in bazos))

    def test_mixed_shard_upsert_keeps_only_flats(self) -> None:
        flat = _listing(
            id=1,
            url="https://reality.idnes.cz/detail/pronajem/byt/praha/abc/",
            extras={"offer": "Pronájem", "estate": "Byt"},
        )
        house = _listing(
            id=2,
            name="Dům 1+1",
            url="https://reality.idnes.cz/detail/pronajem/dum/praha/def/",
            extras={"offer": "Pronájem", "estate": "Dům"},
            disposition="1+1",
        )
        land = _listing(
            id=3,
            name="Pozemek",
            url="https://reality.bazos.cz/inzerat/99/pozemek.php",
            extras={"offer": "Prodej", "estate": "Pozemek"},
        )
        self.store.upsert_catalog_listing(flat, kind="new")
        self.store.upsert_catalog_listing(house, kind="new")
        self.store.upsert_catalog_listing(land, kind="new")
        with self.store.connect() as conn:
            live = conn.execute(
                "SELECT url FROM catalog_listings WHERE IFNULL(gone, 0) = 0"
            ).fetchall()
        self.assertEqual(len(live), 1)
        self.assertIn("/byt/", live[0]["url"])

    def test_is_apartment_helpers(self) -> None:
        self.assertTrue(is_apartment_listing("https://reality.bazos.cz/inzerat/1/x.php", {"estate": "Byt"}))
        self.assertFalse(is_apartment_listing("https://reality.idnes.cz/detail/prodej/pozemky/x/", {}))
        self.assertFalse(is_apartment_listing("https://reality.bazos.cz/inzerat/1/x.php", {"estate": "Dům"}))


class PortalNativeIdMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def test_slug_drift_shares_canonical(self) -> None:
        first = _listing(
            id=555,
            url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-3/555",
        )
        second = _listing(
            id=555,
            url="https://www.sreality.cz/detail/pronajem/byt/3+kk/praha-4/555",
            disposition="3+kk",
            area_m2=55,
        )
        a = self.store.upsert_catalog_listing(first, kind="new")
        b = self.store.upsert_catalog_listing(second, kind="new")
        self.assertEqual(a["canonical_key"], b["canonical_key"])
        with self.store.connect() as conn:
            live = conn.execute(
                "SELECT COUNT(*) FROM catalog_listings WHERE IFNULL(gone, 0) = 0 AND id = 555"
            ).fetchone()[0]
            # One live canonical identity; second slug may retarget the same row.
            rows = conn.execute(
                "SELECT listing_key, canonical_key, url, gone FROM catalog_listings WHERE id = 555"
            ).fetchall()
        self.assertEqual({row["canonical_key"] for row in rows}, {a["canonical_key"]})
        self.assertEqual(live, 1)


class PriceZeroTests(unittest.TestCase):
    def test_parse_price_dotaz(self) -> None:
        amount, label = parse_price("Cena na dotaz")
        self.assertIsNone(amount)
        self.assertTrue(label)

    def test_sreality_zero_price_becomes_none(self) -> None:
        listing = listing_from_raw(
            {
                "id": 42,
                "name": "Pronájem bytu atypický 90 m²",
                "priceCzk": 0,
                "priceUnitCb": {"name": "za měsíc"},
                "categorySubCb": {"name": "atypický"},
                "locality": {"city": "Praha"},
            }
        )
        self.assertIsNotNone(listing)
        assert listing is not None
        self.assertIsNone(listing.price_czk)
        self.assertEqual(format_price(0, "měsíc"), "Cena neuvedena")

    def test_upsert_stores_null_not_zero(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        store = Store(Path(tmp.name))
        store.upsert_catalog_listing(_listing(price_czk=0, price_label="0 Kč/měsíc"), kind="new")
        with store.connect() as conn:
            row = conn.execute("SELECT price_czk, price_label FROM catalog_listings").fetchone()
        self.assertIsNone(row["price_czk"])


class AreaParsingTests(unittest.TestCase):
    def test_phone_mail_does_not_become_area(self) -> None:
        blob = (
            "Skromný pracující důchodce... Nabidněte prosim na tel.: 721990100 "
            "Mail: skipisek@gmail.com"
        )
        self.assertIsNone(parse_area(blob))

    def test_normal_flat_area(self) -> None:
        self.assertEqual(parse_area("Pronájem bytu 2+kk 51 m²"), 51)

    def test_hectares_for_land(self) -> None:
        self.assertEqual(parse_area("Orná půda 85,9 ha", estate="pozemek"), 859000)

    def test_cap_rejects_phone_sized_number_as_flat(self) -> None:
        from app.html_listing import sanitize_area_m2

        self.assertIsNone(sanitize_area_m2(990100, estate="Byt"))


class CatalogQualityOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def _insert_catalog(
        self,
        *,
        listing_key: str,
        portal: str,
        native_id: str,
        url: str,
        name: str,
        extras: str,
        last_seen: str,
        price_czk: int | None = None,
        area_m2: int | None = None,
    ) -> None:
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog_listings(
                    listing_key, canonical_key, portal, id, url, name, gone,
                    first_seen, last_seen, extras, price_czk, area_m2
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    listing_key,
                    f"url:{listing_key}",
                    portal,
                    native_id,
                    url,
                    name,
                    last_seen,
                    last_seen,
                    extras,
                    price_czk,
                    area_m2,
                ),
            )

    def test_dry_run_does_not_mutate(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._insert_catalog(
            listing_key="reality.idnes.cz/detail/prodej/dum/brno/x/",
            portal="idnes",
            native_id="9",
            url="https://reality.idnes.cz/detail/prodej/dum/brno/x/",
            name="Dům Brno",
            extras='{"offer":"Prodej","estate":"Dům"}',
            last_seen=now,
        )
        before = self.store.catalog_quality_dry_run(72)
        out = self.store.run_catalog_quality_cleanup(dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["before"]["non_apartment_live"], before["non_apartment_live"])
        with self.store.connect() as conn:
            live = conn.execute(
                "SELECT COUNT(*) FROM catalog_listings WHERE IFNULL(gone, 0) = 0"
            ).fetchone()[0]
        self.assertGreaterEqual(live, 1)

    def test_fresh_house_marked_before_sweep_would_miss_it(self) -> None:
        """Houses scraped just before deploy have last_seen < 72h; mark must precede sweep."""
        fresh = datetime.now(timezone.utc).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self._insert_catalog(
            listing_key="reality.idnes.cz/detail/prodej/dum/ostrava/y/",
            portal="idnes",
            native_id="77",
            url="https://reality.idnes.cz/detail/prodej/dum/ostrava/y/",
            name="Rodinný dům",
            extras='{"offer":"Prodej","estate":"Dům"}',
            last_seen=fresh,
        )
        self._insert_catalog(
            listing_key="www.sreality.cz/detail/pronajem/byt/2+kk/praha/88",
            portal="sreality",
            native_id="88",
            url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/88",
            name="Byt",
            extras='{"offer":"Pronájem","estate":"Byt"}',
            last_seen=old,
            price_czk=0,
            area_m2=990100,
        )
        # Wrong order leaves the fresh house live:
        swept_only = self.store.sweep_catalog_stale_gone(72)
        with self.store.connect() as conn:
            house = conn.execute(
                "SELECT gone FROM catalog_listings WHERE id = '77'"
            ).fetchone()
        self.assertEqual(house["gone"], 0)
        self.assertGreaterEqual(swept_only, 1)

        result = self.store.run_catalog_quality_cleanup(max_age_hours=72, chunk_size=20)
        self.assertFalse(result["dry_run"])
        self.assertGreaterEqual(result["applied"]["non_apartment_gone"], 1)
        with self.store.connect() as conn:
            house2 = conn.execute(
                "SELECT gone FROM catalog_listings WHERE id = '77'"
            ).fetchone()
            flat = conn.execute(
                "SELECT gone, price_czk, area_m2 FROM catalog_listings WHERE id = '88'"
            ).fetchone()
        self.assertEqual(house2["gone"], 1)
        self.assertEqual(flat["gone"], 1)
        self.assertIsNone(flat["price_czk"])
        self.assertIsNone(flat["area_m2"])


if __name__ == "__main__":
    unittest.main()
