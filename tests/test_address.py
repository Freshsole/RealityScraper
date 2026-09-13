import unittest

from app.places import format_reverse_address, has_house_number
from app.sreality import format_locality


class AddressTests(unittest.TestCase):
    def test_detects_house_number(self):
        self.assertTrue(has_house_number("Kamenická 655/54, Praha 7 – Holešovice"))
        self.assertFalse(has_house_number("Kamenická, Praha - Holešovice"))

    def test_formats_nominatim_address(self):
        label = format_reverse_address(
            {
                "house_number": "655/54",
                "road": "Kamenická",
                "suburb": "Holešovice",
                "district": "obvod Praha 7",
                "city": "Praha",
            }
        )
        self.assertEqual(label, "Kamenická 655/54, Praha 7 – Holešovice")

    def test_sreality_keeps_street_number(self):
        label = format_locality(
            {
                "street": "Kamenická",
                "streetNumber": "54",
                "cityPart": "Holešovice",
                "city": "Praha",
            }
        )
        self.assertEqual(label, "Kamenická 54, Praha – Holešovice")


if __name__ == "__main__":
    unittest.main()
