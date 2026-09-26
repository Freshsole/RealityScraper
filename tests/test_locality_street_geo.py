import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import places
from app.sreality import Listing
from app.store import Store


class LocalityStreetGeoTests(unittest.TestCase):
    def test_street_detection(self):
        self.assertTrue(places.locality_has_street("Václavské náměstí 53, Praha – Nové Město"))
        self.assertTrue(places.locality_has_street("Rubešova, Praha 2 – Vyšehrad"))
        self.assertTrue(places.locality_has_street("Průmyslová, Brandýs nad Labem-Stará Boleslav"))
        self.assertFalse(places.locality_has_street("Praha 4 140 00"))
        self.assertFalse(places.locality_has_street("Praha 1"))
        self.assertFalse(places.locality_has_street("Karlovy Vary"))

    def test_dump_detection(self):
        approx = places.approx_point_from_locality("Václavské náměstí 53, Praha – Nové Město")
        self.assertIsNotNone(approx)
        self.assertTrue(places.coords_are_approx_dump("Václavské náměstí 53, Praha – Nové Město", approx[0], approx[1]))
        self.assertFalse(
            places.coords_are_approx_dump("Václavské náměstí 53, Praha – Nové Město", 50.0815, 14.4265)
        )

    def test_resolve_street_index_not_approx(self):
        streets = {"dělnická": (50.1027, 14.4502)}
        point, approx = places.resolve_locality_coords(
            "Dělnická, Praha 7 - Holešovice",
            streets,
            allow_network=False,
        )
        self.assertEqual(point, (50.1027, 14.4502))
        self.assertFalse(approx)

    def test_resolve_city_only_is_approx(self):
        point, approx = places.resolve_locality_coords("Praha 4 140 00", allow_network=False)
        self.assertIsNotNone(point)
        self.assertTrue(approx)

    def test_osm_ids_prefer_district(self):
        ids = places.osm_ids_for_locality("Dlouhá 737/21, Praha 1 – Josefov")
        self.assertTrue(ids)
        self.assertEqual(ids[0], "R15107966")  # Praha 1 before Praha

    def test_praha_zapad_not_city_center(self):
        places._MATCH_CACHE.clear()
        places._ANCHORS = None
        for sample in (
            "Praha - západ 252 64",
            "Praha-západ",
            "Slapy, okres Praha-západ",
            "Jesenice, okres Praha-západ",
        ):
            match = places.locality_anchor_match(sample)
            self.assertIsNotNone(match, sample)
            self.assertEqual(match[0], "Praha-západ", sample)
            approx = places.approx_point_from_locality(sample)
            self.assertAlmostEqual(approx[0], 49.9603, places=3)
            self.assertAlmostEqual(approx[1], 14.3208, places=3)
            praha = places.approx_point_from_locality("Praha")
            self.assertGreater(
                abs(approx[0] - praha[0]) + abs(approx[1] - praha[1]),
                0.05,
                sample,
            )

    def test_wrong_city_anchor_relocates_praha_zapad(self):
        places._MATCH_CACHE.clear()
        places._ANCHORS = None
        praha = places.approx_point_from_locality("Praha")
        fixed = places.wrong_city_anchor_point("Praha - západ 252 64", praha[0], praha[1])
        self.assertIsNotNone(fixed)
        self.assertAlmostEqual(fixed[0], 49.9603, places=3)
        p1 = places.approx_point_from_locality("Praha 1 110 00")
        self.assertIsNone(places.wrong_city_anchor_point("Praha 1 110 00", p1[0], p1[1]))


class UpgradeCoarseCoordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def test_street_dump_upgraded_via_street_index(self):
        dump = places.approx_point_from_locality("Dělnická 12, Praha 7 - Holešovice")
        self.assertIsNotNone(dump)
        listing = Listing(
            id=101,
            name="byt",
            price_czk=20000,
            price_label="20 000 Kč",
            disposition="2+kk",
            area_m2=50,
            locality="Dělnická 12, Praha 7 - Holešovice",
            url="https://reality.bazos.cz/inzerat/101/",
            image_url=None,
            lat=dump[0],
            lon=dump[1],
            extras={"offer": "Pronájem", "estate": "Byt", "portal": "bazos"},
        )
        self.store.upsert_catalog_listing(listing, kind="seeded")

        def boom(_text: str):
            raise AssertionError("catalog must not network-geocode")

        with patch("app.places.geocode_locality_sync", side_effect=boom), patch(
            "app.places.street_index_for_locality",
            return_value={"dělnická 12": (50.1027, 14.4502), "dělnická": (50.1027, 14.4502)},
        ), patch("app.places.street_index_cached", return_value={}):
            data = self.store.catalog(
                {
                    "limit": 20,
                    "offset": 0,
                    "status": "all",
                    "include_pins": "1",
                    "place_geoms": [
                        {
                            "id": "R19999114",
                            "label": "Praha 7",
                            "kind": "area",
                            "lat": 50.104,
                            "lon": 14.438,
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
                    ],
                }
            )
        self.assertEqual(len(data["pins"]), 1)
        self.assertFalse(data["pins"][0].get("approx"))
        self.assertAlmostEqual(data["pins"][0]["lat"], 50.1027, places=3)

    def test_catalog_marks_dump_approx_without_network(self):
        dump = places.approx_point_from_locality("Praha 1 110 00")
        self.assertIsNotNone(dump)
        listing = Listing(
            id=202,
            name="byt",
            price_czk=59900,
            price_label="59 900 Kč",
            disposition="5+1",
            area_m2=156,
            locality="Praha 1 110 00",
            url="https://www.sreality.cz/detail/202",
            image_url=None,
            lat=None,
            lon=None,
            extras={"offer": "Pronájem", "estate": "Byt", "portal": "sreality"},
        )
        self.store.upsert_catalog_listing(listing, kind="seeded")

        def boom(_text: str):
            raise AssertionError("catalog must not network-geocode")

        with patch("app.places.geocode_locality_sync", side_effect=boom), patch(
            "app.places.street_index_for_locality", return_value={}
        ), patch("app.places.street_index_cached", return_value={}):
            data = self.store.catalog(
                {
                    "limit": 20,
                    "offset": 0,
                    "status": "all",
                    "include_pins": "1",
                    "place_geoms": [
                        {
                            "id": "R439840",
                            "label": "Praha",
                            "kind": "area",
                            "lat": 50.087,
                            "lon": 14.421,
                            "geojson": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [14.2, 49.9],
                                        [14.7, 49.9],
                                        [14.7, 50.2],
                                        [14.2, 50.2],
                                        [14.2, 49.9],
                                    ]
                                ],
                            },
                        }
                    ],
                }
            )
        self.assertEqual(len(data["pins"]), 1)
        self.assertTrue(data["pins"][0].get("approx"))

    def test_peer_street_moves_idnes_dump_off_district(self):
        """Street listing stuck on Praha 3 dump should snap to peer GPS on same street."""
        dump = places.approx_point_from_locality("Jana Želivského, Praha 3 - Žižkov")
        self.assertIsNotNone(dump)
        peer = Listing(
            id=1,
            name="peer",
            price_czk=20000,
            price_label="20 000 Kč",
            disposition="2+kk",
            area_m2=50,
            locality="Jana Želivského 18, Praha – Žižkov",
            url="https://www.sreality.cz/detail/1",
            image_url=None,
            lat=50.089057,
            lon=14.468953,
            extras={"offer": "Pronájem", "estate": "Byt", "portal": "sreality"},
        )
        stuck = Listing(
            id=2354964141489982,
            name="idnes",
            price_czk=15990,
            price_label="15 990 Kč",
            disposition="1+kk",
            area_m2=30,
            locality="Jana Želivského, Praha 3 - Žižkov",
            url="https://reality.idnes.cz/detail/pronajem/byt/praha-3-jana-zelivskeho/6aa92fc70d7d41c9b40fae50/",
            image_url=None,
            lat=dump[0],
            lon=dump[1],
            extras={"offer": "Pronájem", "estate": "Byt", "portal": "idnes"},
        )
        self.store.upsert_catalog_listing(peer, kind="seeded")
        self.store.upsert_catalog_listing(stuck, kind="seeded")
        with patch("app.places.geocode_locality_sync", side_effect=AssertionError("no network")), patch(
            "app.places.street_index_for_locality", return_value={}
        ), patch("app.places.street_index_cached", return_value={}):
            data = self.store.catalog(
                {
                    "limit": 20,
                    "offset": 0,
                    "status": "all",
                    "include_pins": "1",
                }
            )
        pins = [p for p in data["pins"] if p["id"] == stuck.id]
        self.assertEqual(len(pins), 1)
        self.assertFalse(pins[0].get("approx"))
        self.assertGreater(abs(pins[0]["lat"] - dump[0]) + abs(pins[0]["lon"] - dump[1]), 0.001)
        self.assertAlmostEqual(pins[0]["lat"], 50.089057, places=3)

    def test_street_without_gps_is_not_unknown_approx(self):
        """RE/MAX-style street address without GPS must not claim 'portal neuvedl lokalitu'."""
        loc = "Korunní 1279 / 1279, Hlavní město Praha , Praha 2, Hlavní město Praha"
        point, approx = places.resolve_locality_coords(loc, {}, allow_network=False)
        self.assertIsNotNone(point)
        self.assertFalse(approx)
        self.assertTrue(places.locality_has_street(loc))
        listing = Listing(
            id=448117,
            name="Pronájem bytu 4+1",
            price_czk=42000,
            price_label="42 000 Kč",
            disposition="4+1",
            area_m2=121,
            locality=loc,
            url="https://www.remax-czech.cz/reality/detail/448117/x",
            image_url=None,
            lat=None,
            lon=None,
            extras={"offer": "Pronájem", "estate": "Byt", "portal": "remax"},
        )
        self.store.upsert_catalog_listing(listing, kind="seeded")
        with patch("app.places.geocode_locality_sync", side_effect=AssertionError("no network")), patch(
            "app.places.street_index_for_locality", return_value={}
        ), patch("app.places.street_index_cached", return_value={}):
            data = self.store.catalog(
                {
                    "limit": 20,
                    "offset": 0,
                    "status": "all",
                    "include_pins": "1",
                    "place_geoms": [
                        {
                            "id": "R19999122",
                            "label": "Praha 2",
                            "kind": "area",
                            "lat": 50.074,
                            "lon": 14.431,
                            "geojson": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [14.40, 50.06],
                                        [14.46, 50.06],
                                        [14.46, 50.09],
                                        [14.40, 50.09],
                                        [14.40, 50.06],
                                    ]
                                ],
                            },
                        }
                    ],
                }
            )
        self.assertEqual(len(data["pins"]), 1)
        self.assertFalse(data["pins"][0].get("approx"))
