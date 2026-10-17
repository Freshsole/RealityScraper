import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.sreality import Listing
from app.store import Store


PRAHA7 = {
    "id": "R19999114",
    "label": "Praha 7",
    "kind": "area",
    "lat": 50.104,
    "lon": 14.438,
    "bbox": [50.09, 50.13, 14.41, 14.46],
    "geojson": {
        "type": "Polygon",
        "coordinates": [
            [
                [14.41, 50.09],
                [14.46, 50.09],
                [14.46, 50.13],
                [14.41, 50.13],
                [14.41, 50.09],
            ]
        ],
    },
}


def listing(**kwargs) -> Listing:
    data = dict(
        id=1,
        name="pronájem bytu 2+kk 60 m²",
        price_czk=25000,
        price_label="25 000 Kč/měsíc",
        disposition="2+kk",
        area_m2=60,
        locality="Dělnická, Praha 7 - Holešovice",
        url="https://reality.idnes.cz/detail/pronajem/byt/praha-7-delnicka/aaaaaaaaaaaaaaaaaaaaaaaa/",
        image_url=None,
        lat=None,
        lon=None,
        extras={"offer": "Pronájem", "estate": "Byt"},
    )
    data.update(kwargs)
    return Listing(**data)


class CatalogPlaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    @patch(
        "app.places.street_index_for_locality",
        return_value={"dělnická": (50.1027, 14.4502)},
    )
    @patch("app.places.street_index_cached", return_value={})
    def test_offers_without_gps_are_located_once_by_backfill_not_while_reading(self, _cached, _streets):
        self.store.upsert_catalog_listing(listing(), kind="seeded")
        self.store.upsert_catalog_listing(
            listing(
                id=2,
                locality="Sokolovská, Brno - Židenice",
                url="https://reality.idnes.cz/detail/pronajem/byt/brno-sokolovska/bbbbbbbbbbbbbbbbbbbbbbbb/",
            ),
            kind="seeded",
        )
        query = {"portal": "idnes", "place_geoms": [PRAHA7], "limit": 20, "offset": 0, "status": "all", "include_pins": "1"}
        before = self.store.catalog(dict(query))
        # Reading never guesses a position: no GPS, not in the place, not on the map.
        self.assertEqual(before["total"], 0)
        self.assertEqual(before["pins"], [])
        stats = self.store.locate_catalog_listings()
        self.assertGreaterEqual(stats["rows"], 1)
        data = self.store.catalog(dict(query))
        # Brno stays out, Praha 7 street lands on the street, list == pins.
        self.assertEqual(data["total"], 1)
        self.assertIn("Praha 7", data["items"][0]["locality"])
        self.assertEqual(len(data["pins"]), 1)
        self.assertEqual(data["pinned"], data["total"])
        self.assertAlmostEqual(data["pins"][0]["lat"], 50.1027, places=3)
        self.assertAlmostEqual(data["pins"][0]["lon"], 14.4502, places=3)

    @patch(
        "app.places.street_index_for_locality",
        # Same street name, but the index point is in another town: must be rejected.
        return_value={"šimáčkova": (49.88, 16.05)},
    )
    @patch("app.places.street_index_cached", return_value={})
    def test_backfill_rejects_street_match_in_another_city(self, _cached, _streets):
        self.store.upsert_catalog_listing(
            listing(id=5, locality="Šimáčkova, Praha 7 - Holešovice",
                    url="https://reality.idnes.cz/detail/pronajem/byt/praha-7-simackova/eeeeeeeeeeeeeeeeeeeeeeee/"),
            kind="seeded",
        )
        self.store.locate_catalog_listings()
        data = self.store.catalog({"portal": "idnes", "place_geoms": [PRAHA7], "limit": 20, "offset": 0, "status": "all", "include_pins": "1"})
        for pin in data["pins"]:
            self.assertLess(abs(pin["lat"] - 50.10), 0.1)
            self.assertLess(abs(pin["lon"] - 14.44), 0.1)

    def test_list_total_and_pins_are_the_same_set(self):
        for i in range(1, 8):
            self.store.upsert_catalog_listing(
                listing(
                    id=100 + i,
                    lat=50.095 + i * 0.003,
                    lon=14.42 + i * 0.002,
                    locality=f"Ulice {i}, Praha 7 - Holešovice",
                    url=f"https://reality.idnes.cz/detail/pronajem/byt/praha-7-ulice-{i}/{i:024d}/",
                ),
                kind="seeded",
            )
        # one in the polygon bbox but outside the polygon, one far away
        self.store.upsert_catalog_listing(listing(id=200, lat=50.0, lon=14.0, locality="Praha 7 - Holešovice",
                                                  url="https://reality.idnes.cz/detail/pronajem/byt/x/" + "9" * 24 + "/"), kind="seeded")
        query = {"place_geoms": [PRAHA7], "limit": 3, "offset": 0, "status": "all", "include_pins": "1"}
        page1 = self.store.catalog(dict(query))
        pins_only = self.store.catalog(dict(query, pins_only=True))
        self.assertEqual(page1["total"], 7)
        self.assertEqual(len(page1["items"]), 3)
        self.assertEqual({p["listing_key"] for p in page1["pins"]}, {p["listing_key"] for p in pins_only["items"]})
        self.assertEqual(sum(p["count"] for p in page1["pins"]), page1["total"])
        # paging walks the whole set, no duplicates
        seen = []
        for off in (0, 3, 6):
            seen += [i["id"] for i in self.store.catalog(dict(query, offset=off, include_pins="0"))["items"]]
        self.assertEqual(len(seen), 7)
        self.assertEqual(len(set(seen)), 7)

    def test_coords_outside_polygon_are_excluded(self):
        self.store.upsert_catalog_listing(
            listing(id=3, lat=50.0, lon=14.0, locality="Praha 7 - Holešovice"),
            kind="seeded",
        )
        data = self.store.catalog(
            {
                "portal": "idnes",
                "place_geoms": [PRAHA7],
                "limit": 20,
                "offset": 0,
                "status": "all",
            }
        )
        self.assertEqual(data["total"], 0)
