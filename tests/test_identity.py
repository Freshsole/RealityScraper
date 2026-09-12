import unittest

from app.identity import fingerprint, same_listing


def row(**kwargs):
    base = {
        "url": "https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1",
        "disposition": "2+kk",
        "area_m2": 45,
        "lat": 50.08812,
        "lon": 14.43221,
        "price_czk": 25000,
        "price_label": "25 000 Kč/měsíc",
        "extras": {"offer": "Pronájem", "specs": [{"label": "Podlaží", "value": "3/5"}]},
    }
    base.update(kwargs)
    return base


class IdentityTests(unittest.TestCase):
    def test_same_listing_across_portals(self):
        sreality = row()
        bezrealitky = row(
            url="https://www.bezrealitky.cz/nemovitosti-byty-domy/123",
            price_czk=25500,
        )
        self.assertTrue(same_listing(sreality, bezrealitky))
        self.assertEqual(fingerprint(sreality), fingerprint(bezrealitky))

    def test_different_disposition_stays_separate(self):
        left = row()
        right = row(
            url="https://www.bezrealitky.cz/nemovitosti-byty-domy/9",
            disposition="3+kk",
            area_m2=68,
        )
        self.assertFalse(same_listing(left, right))

    def test_different_floor_stays_separate(self):
        left = row()
        right = row(
            url="https://www.bezrealitky.cz/nemovitosti-byty-domy/8",
            extras={"offer": "Pronájem", "specs": [{"label": "Podlaží", "value": "4/5"}]},
        )
        self.assertFalse(same_listing(left, right))

    def test_same_portal_neighbors_stay_separate(self):
        left = row(url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1")
        right = row(url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/2")
        self.assertFalse(same_listing(left, right))

    def test_sale_and_rent_stay_separate(self):
        rent = row()
        sale = row(
            url="https://www.sreality.cz/detail/prodej/byt/2+kk/praha/2",
            price_czk=6_500_000,
            extras={"offer": "Prodej", "specs": [{"label": "Podlaží", "value": "3/5"}]},
        )
        self.assertFalse(same_listing(rent, sale))


if __name__ == "__main__":
    unittest.main()
