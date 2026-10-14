import asyncio
import unittest

from app.bazos import PAGE_SIZE, BazosClient
from app.sreality import Listing


def _listing(i: int) -> Listing:
    return Listing(
        id=i, name=f"Byt {i}", price_czk=1, price_label="1 Kč", disposition="2+kk", area_m2=40,
        locality="Praha", url=f"https://reality.bazos.cz/inzerat/{i}/x.php", image_url=None,
    )


class BazosCatalogTests(unittest.TestCase):
    def _client(self, fail_pages=(), flaky=()):
        client = BazosClient("https://reality.bazos.cz/prodam/byt/?kitx=ano")
        attempts: dict[int, int] = {}

        async def fake_fetch_page(page=1, newest=True):
            attempts[page] = attempts.get(page, 0) + 1
            if page in fail_pages or (page in flaky and attempts[page] == 1):
                raise RuntimeError("boom")
            return [_listing(page * 100 + k) for k in range(PAGE_SIZE)], 5 * PAGE_SIZE

        client.fetch_page = fake_fetch_page  # type: ignore[method-assign]
        return client, attempts

    def test_page_url_uses_offset_query(self):
        client = BazosClient("https://reality.bazos.cz/prodam/byt/?kitx=ano")
        self.assertIn("crp=40", client._page_url(3))
        self.assertNotIn("crp", client._page_url(1))

    def test_catalog_retries_flaky_page_and_reports_nothing_failed(self):
        client, attempts = self._client(flaky=(3,))
        listings, total = asyncio.run(client.fetch_catalog())
        self.assertEqual(total, 100)
        self.assertEqual(len(listings), 5 * PAGE_SIZE)
        self.assertEqual(client.failed_pages, [])
        self.assertEqual(attempts[3], 2)

    def test_catalog_reports_permanently_failed_pages(self):
        client, _ = self._client(fail_pages=(4,))
        listings, _ = asyncio.run(client.fetch_catalog())
        self.assertEqual(client.failed_pages, [4])
        self.assertEqual(len(listings), 4 * PAGE_SIZE)


if __name__ == "__main__":
    unittest.main()
