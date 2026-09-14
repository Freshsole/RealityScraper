import tempfile
import unittest
from pathlib import Path

from app.identity import listing_key
from app.sreality import Listing
from app.store import Store


def listing(*, listing_id: int, url: str, disposition: str = "2+kk", area: int = 45, price: int = 25000, floor: str = "3/5") -> Listing:
    return Listing(
        id=listing_id,
        name="Byt Praha",
        price_czk=price,
        price_label=f"{price} Kč/měsíc",
        disposition=disposition,
        area_m2=area,
        locality="Praha 3",
        url=url,
        image_url=None,
        lat=50.08812,
        lon=14.43221,
        extras={"offer": "Pronájem", "specs": [{"label": "Podlaží", "value": floor}]},
    )


class ListingDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def test_two_portals_become_one_catalog_row(self):
        sreality = listing(listing_id=111, url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/111")
        bezrealitky = listing(listing_id=222, url="https://www.bezrealitky.cz/nemovitosti-byty-domy/222", price=25200)
        first = self.store.upsert_catalog_listing(sreality, kind="new")
        second = self.store.upsert_catalog_listing(bezrealitky, kind="new")
        self.assertEqual(first["canonical_key"], second["canonical_key"])
        with self.store.connect() as conn:
            catalog_n = conn.execute("SELECT COUNT(*) FROM catalog_listings").fetchone()[0]
            listing_n = conn.execute("SELECT COUNT(*) FROM listings WHERE monitor_id = '__catalog__'").fetchone()[0]
            links_n = conn.execute("SELECT COUNT(*) FROM listing_links").fetchone()[0]
        self.assertEqual(catalog_n, 1)
        self.assertEqual(listing_n, 1)
        self.assertEqual(links_n, 2)
        data = self.store.catalog({"limit": 20, "offset": 0, "status": "all"})
        self.assertEqual(data["total"], 1)
        item = data["items"][0]
        urls = {link["url"] for link in item["links"]}
        self.assertIn(sreality.url, urls)
        self.assertIn(bezrealitky.url, urls)

    def test_neighbor_units_do_not_merge(self):
        left = listing(listing_id=1, url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1")
        right = listing(
            listing_id=2,
            url="https://www.bezrealitky.cz/nemovitosti-byty-domy/2",
            disposition="3+kk",
            area=70,
            price=32000,
            floor="4/5",
        )
        self.store.upsert_catalog_listing(left, kind="new")
        self.store.upsert_catalog_listing(right, kind="new")
        with self.store.connect() as conn:
            catalog_n = conn.execute("SELECT COUNT(*) FROM catalog_listings").fetchone()[0]
        self.assertEqual(catalog_n, 2)

    def test_one_portal_gone_keeps_other_link(self):
        sreality = listing(listing_id=111, url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/111")
        bezrealitky = listing(listing_id=222, url="https://www.bezrealitky.cz/nemovitosti-byty-domy/222")
        self.store.upsert_catalog_listing(sreality, kind="new")
        result = self.store.upsert_catalog_listing(bezrealitky, kind="new")
        self.store.mark_catalog_listing_gone(listing_key(sreality.url))
        with self.store.connect() as conn:
            gone = conn.execute("SELECT gone FROM catalog_listings WHERE canonical_key = ?", (result["canonical_key"],)).fetchone()
            live = conn.execute(
                "SELECT COUNT(*) FROM listing_links WHERE canonical_key = ? AND gone = 0",
                (result["canonical_key"],),
            ).fetchone()[0]
        self.assertEqual(int(gone["gone"]), 0)
        self.assertEqual(live, 1)

    def test_duplicate_counts_and_schedule(self):
        self.store.save_dedupe_schedule(enabled=True, hour=4)
        settings = self.store.dedupe_settings()
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["hour"], 4)
        counts = self.store.duplicate_row_counts()
        self.assertEqual(counts["listing_dup_groups"], 0)
        self.assertEqual(counts["catalog_dup_groups"], 0)
        preview = self.store.preview_duplicate_listings()
        self.assertEqual(preview["stats"]["listings_relinkable"], 0)


if __name__ == "__main__":
    unittest.main()
