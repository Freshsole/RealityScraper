import asyncio
import unittest

from app import sreality
from app.sreality import MAX_PAGE, SrealityClient


class SrealityPagingTests(unittest.TestCase):
    def test_result_window_constants(self):
        self.assertEqual(sreality.PAGE_SIZE, 22)
        self.assertEqual(MAX_PAGE, 454)

    def test_page_beyond_window_makes_no_request(self):
        client = SrealityClient("https://www.sreality.cz/hledani/prodej/byty")

        async def boom(*a, **k):
            raise AssertionError("network must not be touched")

        client._fetch_next_data = boom  # type: ignore[method-assign]
        client._fetch_html = boom  # type: ignore[method-assign]
        self.assertEqual(asyncio.run(client.fetch_page(MAX_PAGE + 1)), ([], 0))
        asyncio.run(client.aclose())

    def test_page_inside_window_goes_to_json_api(self):
        client = SrealityClient("https://www.sreality.cz/hledani/prodej/byty")
        seen = []

        async def fake(page, newest):
            seen.append(page)
            return [], 7

        client._fetch_next_data = fake  # type: ignore[method-assign]
        self.assertEqual(asyncio.run(client.fetch_page(MAX_PAGE)), ([], 7))
        self.assertEqual(seen, [MAX_PAGE])
        asyncio.run(client.aclose())


if __name__ == "__main__":
    unittest.main()
