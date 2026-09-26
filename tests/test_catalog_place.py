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
        "app.places.street_index_sync",
        return_value={"dělnická": (50.1027, 14.4502)},
    )
    def test_idnes_praha7_without_coords_matches_place_filter(self, _streets):
        self.store.upsert_catalog_listing(listing(), kind="seeded")
        self.store.upsert_catalog_listing(
            listing(
                id=2,
                locality="Sokolovská, Praha 8 - Karlín",
                url="https://reality.idnes.cz/detail/pronajem/byt/praha-8-sokolovska/bbbbbbbbbbbbbbbbbbbbbbbb/",
            ),
            kind="seeded",
        )
        data = self.store.catalog(
            {
                "portal": "idnes",
                "district": "Praha 7",
                "place_geoms": [PRAHA7],
                "limit": 20,
                "offset": 0,
                "status": "all",
                "include_pins": "1",
            }
        )
        self.assertEqual(data["total"], 1)
        self.assertIn("Praha 7", data["items"][0]["locality"])
        self.assertEqual(len(data["pins"]), 1)
        self.assertTrue(data["pins"][0].get("approx"))
        self.assertAlmostEqual(data["pins"][0]["lat"], 50.1027, places=3)
        self.assertAlmostEqual(data["pins"][0]["lon"], 14.4502, places=3)

    @patch(
        "app.places.street_index_sync",
        return_value={"dělnická": (50.1027, 14.4502), "komunardů": (50.1062, 14.4499)},
    )
    def test_street_localities_get_distinct_pins(self, _streets):
        self.store.upsert_catalog_listing(listing(), kind="seeded")
        self.store.upsert_catalog_listing(
            listing(
                id=4,
                locality="Komunardů, Praha 7 - Holešovice",
                url="https://reality.idnes.cz/detail/pronajem/byt/praha-7-komunardu/cccccccccccccccccccccccc/",
            ),
            kind="seeded",
        )
        data = self.store.catalog({"place_geoms": [PRAHA7], "limit": 20, "offset": 0, "status": "all", "include_pins": "1"})
        pts = {(round(pin["lat"], 4), round(pin["lon"], 4)) for pin in data["pins"]}
        self.assertEqual(len(data["pins"]), 2)
        self.assertEqual(len(pts), 2)

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
