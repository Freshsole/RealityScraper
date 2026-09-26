import unittest
from unittest.mock import patch

from app import places
from app.sreality import Listing


def _listing(**kwargs) -> Listing:
    defaults = dict(
        id=1,
        name="x",
        price_czk=10000,
        price_label="10 000 Kč",
        disposition="2+kk",
        area_m2=40,
        locality="Holešovice",
        url="https://example.test/1",
        image_url=None,
        lat=50.103,
        lon=14.436,
    )
    defaults.update(kwargs)
    return Listing(**defaults)


class GeocodePoolTests(unittest.TestCase):
    def setUp(self):
        places._GEOCODE_CACHE.clear()

    def test_executor_is_dedicated(self):
        self.assertEqual(places._geocode_executor._max_workers, 4)
        self.assertEqual(places._geocode_executor._thread_name_prefix, "geocode")

    def test_forward_cache_hit_skips_network(self):
        places._cache_put("praha 7", (50.1, 14.4))
        with patch.object(places, "_photon_locality_sync", side_effect=AssertionError("photon")):
            with patch.object(places, "_nominatim_locality_sync", side_effect=AssertionError("nominatim")):
                self.assertEqual(places.geocode_locality_sync("Praha 7"), (50.1, 14.4))

    def test_reverse_cache_round_trip(self):
        key = places._reverse_cache_key(50.103, 14.436)
        places._cache_put(key, ("Heřmanova 1, Praha 7", 50.103, 14.436))
        self.assertIn("Heřmanova", places.reverse_address_cached(50.103, 14.436))

    def test_refine_without_network_does_not_sleep(self):
        listing = _listing()
        with patch.object(places.time, "sleep", side_effect=AssertionError("sleep")):
            places.refine_listing_location(listing, network=False)
        self.assertEqual(listing.locality, "Holešovice")

    def test_refine_uses_cache_without_http(self):
        key = places._reverse_cache_key(50.103, 14.436)
        places._cache_put(key, ("Heřmanova 9, Praha 7", 50.103, 14.436))
        listing = _listing()
        places.refine_listing_location(listing, network=False)
        self.assertEqual(listing.locality, "Heřmanova 9, Praha 7")

    def test_async_geocode_cache_hit_skips_executor(self):
        places._cache_put("praha 3", (50.08, 14.45))

        async def _run():
            return await places.geocode_locality("Praha 3")

        import asyncio

        with patch.object(places, "_to_geocode_thread", side_effect=AssertionError("executor")):
            self.assertEqual(asyncio.run(_run()), (50.08, 14.45))


if __name__ == "__main__":
    unittest.main()
