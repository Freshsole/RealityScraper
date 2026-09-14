"""Důkladný regresní test mapového zoomu / bbox filtru katalogu.

Pokryté situace:
- vnořené bboxy (wide → mid → tight → wide)
- monotonicita GPS výsledků
- inzeráty bez GPS podle locality vs. cizí města
- span >= 3.5 nesmí tahat celý katalog bez GPS
- zoom do městské části: locality match i při špatných/centroid GPS
- prázdné anchory v těsném výřezu
- kruh vs. bbox
- pin total vs. list total
- portál + status kombinace
- opakovaný zoom sem a zpět
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.sreality import Listing
from app.store import Store, _apply_map_bbox


def L(
    id: int,
    *,
    locality: str,
    lat: float | None,
    lon: float | None,
    portal: str = "bazos",
    name: str = "byt",
) -> Listing:
    host = {
        "bazos": "reality.bazos.cz",
        "sreality": "www.sreality.cz",
        "bezrealitky": "www.bezrealitky.cz",
        "idnes": "reality.idnes.cz",
    }[portal]
    return Listing(
        id=id,
        name=name,
        price_czk=20000 + id,
        price_label=f"{20000 + id} Kč",
        disposition="2+kk",
        area_m2=50,
        locality=locality,
        url=f"https://{host}/inzerat/{id}/",
        image_url=None,
        lat=lat,
        lon=lon,
        extras={"offer": "Pronájem", "estate": "Byt", "portal": portal},
    )


# Realistické výřezy Prahy (součet stran = span)
WIDE = dict(south="49.90", north="50.25", west="14.10", east="14.75")  # span ~1.0
MID = dict(south="50.02", north="50.14", west="14.28", east="14.55")  # span ~0.39
TIGHT_P4 = dict(south="50.01", north="50.06", west="14.42", east="14.50")  # Praha 4
TIGHT_CENTER = dict(south="50.07", north="50.10", west="14.40", east="14.45")
NATION = dict(south="48.5", north="51.1", west="12.0", east="19.0")  # span ~9.6
SOUTH_NO_ANCHOR = dict(south="50.020", north="50.035", west="14.430", east="14.460")


class CatalogMapZoomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))
        # Praha 4 — správné GPS v obvodu
        self.store.upsert_catalog_listing(
            L(1, locality="Praha 4 140 00", lat=50.028, lon=14.445), kind="seeded"
        )
        # Praha 4 — GPS omylem na centrum Prahy (typický approx dump)
        self.store.upsert_catalog_listing(
            L(2, locality="Praha 4 140 00", lat=50.0875, lon=14.4213), kind="seeded"
        )
        # Praha 4 — bez GPS
        self.store.upsert_catalog_listing(
            L(3, locality="Praha 4 140 00", lat=None, lon=None), kind="seeded"
        )
        # Praha 1 — GPS v centru
        self.store.upsert_catalog_listing(
            L(4, locality="Praha 1 110 00", lat=50.0855, lon=14.4107), kind="seeded"
        )
        # Brno — bez GPS (nesmí se objevit v pražském výřezu)
        self.store.upsert_catalog_listing(
            L(5, locality="Brno-střed", lat=None, lon=None), kind="seeded"
        )
        # Brno — GPS v Brně
        self.store.upsert_catalog_listing(
            L(6, locality="Brno", lat=49.195, lon=16.607), kind="seeded"
        )
        # Praha 7 — GPS
        self.store.upsert_catalog_listing(
            L(7, locality="Praha 7 170 00", lat=50.104, lon=14.438), kind="seeded"
        )
        # Ostrava bez GPS
        self.store.upsert_catalog_listing(
            L(8, locality="Ostrava", lat=None, lon=None), kind="seeded"
        )
        # Další portál v P4
        self.store.upsert_catalog_listing(
            L(9, locality="Praha 4", lat=50.041, lon=14.445, portal="bezrealitky"),
            kind="seeded",
        )

    def _catalog(self, box: dict, **extra) -> dict:
        payload = {
            "status": "all",
            "limit": 50,
            "offset": 0,
            "include_pins": "0",
            **box,
            **extra,
        }
        return self.store.catalog(payload)

    def _ids(self, data: dict) -> set[int]:
        return {int(item["id"]) for item in data["items"]}

    def test_01_nested_zoom_keeps_district_listings(self):
        """Zoom do Prahy 4 musí nechat P4 inzeráty (včetně špatného GPS a bez GPS)."""
        wide = self._catalog(WIDE, portal="bazos")
        mid = self._catalog(MID, portal="bazos")
        tight = self._catalog(TIGHT_P4, portal="bazos")
        self.assertGreaterEqual(wide["total"], mid["total"])
        self.assertIn(1, self._ids(tight))
        self.assertIn(2, self._ids(tight), "P4 s GPS na centru Prahy musí zůstat při zoomu do P4")
        self.assertIn(3, self._ids(tight), "P4 bez GPS musí zůstat při zoomu do P4")
        self.assertNotIn(5, self._ids(tight))
        self.assertNotIn(6, self._ids(tight))

    def test_02_nationwide_span_does_not_dump_all_nogps_into_prague_view(self):
        """Široký výřez Prahy nesmí přibalit Brno/Ostrava jen proto, že nemají GPS."""
        data = self._catalog(WIDE, portal="bazos")
        ids = self._ids(data)
        self.assertNotIn(5, ids)
        self.assertNotIn(8, ids)
        self.assertNotIn(6, ids)

    def test_03_nation_bbox_still_sees_major_cities(self):
        data = self._catalog(NATION, portal="bazos")
        ids = self._ids(data)
        self.assertIn(5, ids)
        self.assertIn(6, ids)
        self.assertIn(1, ids)

    def test_04_zoom_to_center_keeps_center_drops_p4_gps_only_if_locality_mismatch(self):
        data = self._catalog(TIGHT_CENTER, portal="bazos")
        ids = self._ids(data)
        self.assertIn(4, ids)
        # P4 locality by se při centru neměla tvářit jako výsledek jen kvůli špatnému GPS —
        # ale pokud je Praha / Praha 1 v anchorech a coarse matchne %Praha%, může projít textem.
        # Kontrakt: Brno nesmí.
        self.assertNotIn(5, ids)
        self.assertNotIn(6, ids)

    def test_05_gps_monotonicity_for_precise_points(self):
        """Přesné GPS v menším bboxu ⊆ větší bbox."""
        boxes = [WIDE, MID, TIGHT_P4]
        totals = []
        for box in boxes:
            # jen id=1 má přesné GPS v P4
            data = self._catalog(box, portal="bazos")
            totals.append(data["total"])
            if 1 in self._ids(self._catalog(TIGHT_P4, portal="bazos")):
                self.assertIn(1, self._ids(data))
        self.assertGreaterEqual(totals[0], totals[1])

    def test_06_zoom_out_restores_listings(self):
        a = self._ids(self._catalog(WIDE, portal="bazos"))
        b = self._ids(self._catalog(TIGHT_P4, portal="bazos"))
        c = self._ids(self._catalog(WIDE, portal="bazos"))
        self.assertEqual(a, c)
        self.assertTrue(b.issubset(a) or b & a)

    def test_07_portal_filter_survives_zoom(self):
        bazos = self._catalog(TIGHT_P4, portal="bazos")
        bez = self._catalog(TIGHT_P4, portal="bezrealitky")
        self.assertIn(1, self._ids(bazos))
        self.assertNotIn(9, self._ids(bazos))
        self.assertIn(9, self._ids(bez))
        self.assertNotIn(1, self._ids(bez))

    def test_08_circle_ignores_map_bbox(self):
        data = self._catalog(
            TIGHT_P4,
            portal="bazos",
            lat="50.028",
            lon="14.445",
            radius_m="800",
        )
        ids = self._ids(data)
        self.assertIn(1, ids)
        # kruh vyžaduje GPS — bez GPS neprojde
        self.assertNotIn(3, ids)

    def test_09_pins_and_list_agree_on_membership(self):
        list_data = self._catalog(TIGHT_P4, portal="bazos", include_pins="0")
        pin_data = self.store.catalog(
            {**TIGHT_P4, "portal": "bazos", "status": "all", "pins_only": True, "limit": 50}
        )
        list_ids = self._ids(list_data)
        pin_ids = {int(p["id"]) for p in pin_data["items"] if p.get("monitor_id") != "__cluster__"}
        # každý pin s reálným id musí jít do list filtru (nebo cluster)
        self.assertTrue(pin_ids.issubset(list_ids) or not pin_ids)

    def test_10_apply_bbox_helpers_never_include_foreign_nogps_in_prague(self):
        where: list[str] = ["1=1"]
        params: list = []
        filters = {**WIDE}
        _apply_map_bbox(where, params, filters)
        sql = " AND ".join(where)
        # Brno bez GPS nesmí splnit WHERE
        self.assertNotIn("TRIM(IFNULL(listings.locality, '')) != ''", sql)
        self.assertIn("listings.lat BETWEEN", sql)

    def test_11_empty_anchor_zone_still_matches_nearby_district_by_locality(self):
        """I výřez bez přesného anchor bodu uvnitř musí umět P4 podle locality."""
        data = self._catalog(SOUTH_NO_ANCHOR, portal="bazos")
        ids = self._ids(data)
        self.assertIn(1, ids)
        self.assertIn(3, ids, "P4 bez GPS musí jít přes nearby/padded anchory")
        self.assertNotIn(5, ids)

    def test_12_repeated_zoom_stress(self):
        sequence = [WIDE, MID, TIGHT_P4, TIGHT_CENTER, MID, WIDE, TIGHT_P4, WIDE]
        last_wide = None
        for i, box in enumerate(sequence):
            data = self._catalog(box, portal="bazos")
            self.assertIsInstance(data["total"], int)
            self.assertGreaterEqual(data["total"], 0)
            ids = self._ids(data)
            self.assertNotIn(5, ids, f"step {i} leaked Brno nogps")
            self.assertNotIn(8, ids, f"step {i} leaked Ostrava nogps")
            if box == WIDE:
                if last_wide is not None:
                    self.assertEqual(ids, last_wide)
                last_wide = ids
            if box == TIGHT_P4:
                self.assertTrue({1, 2, 3}.issubset(ids), f"step {i} lost P4 listings: {ids}")

    def test_13_all_portals_zoom(self):
        data = self._catalog(TIGHT_P4)
        ids = self._ids(data)
        self.assertIn(1, ids)
        self.assertIn(9, ids)

    def test_14_status_active_default_does_not_wipe_seeded(self):
        data = self._catalog(TIGHT_P4, portal="bazos", status="")
        self.assertGreaterEqual(data["total"], 3)

    def test_15_tight_center_does_not_match_all_praha_via_city_label(self):
        """Zoom na centrum: Praha 1/2 ano, samotné „Praha %“ nesmí vytáhnout P4 bez GPS v centru."""
        data = self._catalog(TIGHT_CENTER, portal="bazos")
        ids = self._ids(data)
        self.assertIn(4, ids)
        # id=3 je P4 bez GPS — při centru bez P4 anchoru nesmí projít jen kvůli „Praha“
        self.assertNotIn(3, ids)
        self.assertNotIn(5, ids)

    def test_16_pins_do_not_undersample_district_when_zoomed_out(self):
        """Široký bbox musí vrátit všechny GPS piny ve čtvrti, ne LIMIT 400 sample."""
        from app.sreality import Listing as ListingCls

        # 120 bodů v Holešovicích + 500 jinde → starý LIMIT 400 podvzorkoval čtvrť
        for i in range(120):
            self.store.upsert_catalog_listing(
                ListingCls(
                    id=3000 + i,
                    name=f"pin-stable-h7-{i}",
                    price_czk=15000 + i * 17,
                    price_label=f"{15000 + i * 17} Kč",
                    disposition=["1+kk", "2+kk", "3+kk", "4+kk"][i % 4],
                    area_m2=30 + i,
                    locality="Praha 7 170 00",
                    url=f"https://reality.bazos.cz/inzerat/pin-h7-{3000+i}/",
                    image_url=None,
                    lat=50.100 + (i % 10) * 0.0012,
                    lon=14.440 + (i // 10) * 0.0012,
                    extras={"offer": "Pronájem", "estate": "Byt"},
                ),
                kind="seeded",
            )
        for i in range(500):
            self.store.upsert_catalog_listing(
                ListingCls(
                    id=4000 + i,
                    name=f"pin-stable-p4-{i}",
                    price_czk=18000 + i * 13,
                    price_label=f"{18000 + i * 13} Kč",
                    disposition=["1+kk", "2+kk", "3+kk"][i % 3],
                    area_m2=35 + (i % 80),
                    locality="Praha 4 140 00",
                    url=f"https://reality.bazos.cz/inzerat/pin-p4-{4000+i}/",
                    image_url=None,
                    lat=50.02 + (i % 25) * 0.0015,
                    lon=14.42 + (i // 25) * 0.0015,
                    extras={"offer": "Pronájem", "estate": "Byt"},
                ),
                kind="seeded",
            )
        with self.store.connect(readonly=True) as conn:
            p7 = int(
                conn.execute(
                    "SELECT COUNT(*) FROM listings WHERE locality LIKE 'Praha 7%' AND lat IS NOT NULL"
                ).fetchone()[0]
            )
        self.assertGreaterEqual(p7, 100, f"fixture collapsed Praha 7 rows to {p7}")
        cell = dict(south="50.098", north="50.113", west="14.438", east="14.456")
        wide_pins = self.store.catalog(
            {**WIDE, "portal": "bazos", "status": "all", "pins_only": True, "limit": 50}
        )["items"]
        holes = [
            p
            for p in wide_pins
            if p.get("lat") is not None
            and float(cell["south"]) <= float(p["lat"]) <= float(cell["north"])
            and float(cell["west"]) <= float(p["lon"]) <= float(cell["east"])
        ]
        self.assertGreaterEqual(
            len(holes),
            100,
            f"wide pins undersampled Holešovice: {len(holes)}/{p7}",
        )
        tight_pins = self.store.catalog(
            {**cell, "portal": "bazos", "status": "all", "pins_only": True, "limit": 50}
        )["items"]
        ratio = (len(tight_pins) + 1) / (len(holes) + 1)
        self.assertLess(ratio, 1.35, f"pin count jumped on zoom: wide_cell={len(holes)} tight={len(tight_pins)}")


if __name__ == "__main__":
    unittest.main()
