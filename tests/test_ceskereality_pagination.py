"""ČeskéReality: ?strana=N paging, real total from the header, region/disposition shard URLs."""

import asyncio
import re
import unittest
from pathlib import Path

import httpx

from app.ceskereality import CeskerealityClient
from app.portal_urls import ceskereality_url

FIXTURE = (Path(__file__).parent / "fixtures" / "ceskereality_list_p2.html").read_text(encoding="utf-8")


def _client(url: str, seen: list[str], last_page: int = 3) -> CeskerealityClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        page = int(request.url.params.get("strana", "1"))
        if page > last_page:
            return httpx.Response(404, text="not found")
        # Distinct ids per page so fetch_all's de-dup does not hide paging bugs.
        body = re.sub(r"(\d{7})(\.html)", lambda m: str(int(m.group(1)) + page * 1000) + m.group(2), FIXTURE)
        return httpx.Response(200, text=body)

    client = CeskerealityClient(url)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return client


class CeskerealityParseTests(unittest.TestCase):
    def test_total_is_header_count_not_page_size(self):
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/")
        listings, total = client._parse_blob(FIXTURE.encode(), 2)
        self.assertEqual(total, 4929)
        self.assertEqual(len(listings), 3)
        first = listings[0]
        self.assertEqual(first.price_czk, 12000)
        self.assertEqual(first.disposition, "2+1")
        self.assertEqual(first.area_m2, 64)
        self.assertIn("pronajem", first.url)
        asyncio.run(client.aclose())

    def test_total_on_sale_wording(self):
        client = CeskerealityClient("https://www.ceskereality.cz/prodej/byty/")
        html = "<p>Právě teď můžete vybírat z 9 857 bytů na prodej</p>"
        self.assertEqual(client._parse_total(html), 9857)
        asyncio.run(client.aclose())


class CeskerealityPagingTests(unittest.TestCase):
    def test_page_url_newest_first_and_strana_param(self):
        client = CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/jihomoravsky-kraj/")
        self.assertEqual(
            client._page_url(1), "https://www.ceskereality.cz/pronajem/byty/jihomoravsky-kraj/nejnovejsi/"
        )
        self.assertEqual(
            client._page_url(3), "https://www.ceskereality.cz/pronajem/byty/jihomoravsky-kraj/nejnovejsi/?strana=3"
        )
        # An explicit sort in the stored url is kept, not doubled.
        client.search_url = "https://www.ceskereality.cz/pronajem/byty/nejlevnejsi/"
        self.assertEqual(client._page_url(2), "https://www.ceskereality.cz/pronajem/byty/nejlevnejsi/?strana=2")
        asyncio.run(client.aclose())

    def test_fetch_all_walks_pages_until_404(self):
        seen: list[str] = []
        client = _client("https://www.ceskereality.cz/pronajem/byty/", seen, last_page=3)

        async def run():
            try:
                return await client.fetch_all()
            finally:
                await client.aclose()

        listings, total = asyncio.run(run())
        self.assertEqual(total, 4929)
        self.assertEqual(len(listings), 9)
        self.assertEqual(len({item.id for item in listings}), 9)
        self.assertEqual(len(seen), 4)  # pages 1..3 + the 404 that ends the walk
        self.assertTrue(seen[1].endswith("?strana=2"))
        self.assertTrue(seen[3].endswith("?strana=4"))


class CeskerealityShardUrlTests(unittest.TestCase):
    def test_nationwide_region_and_disposition_urls(self):
        build = ceskereality_url.build_url
        self.assertEqual(build({"offers": ["prodej"]}), "https://www.ceskereality.cz/prodej/byty/")
        self.assertEqual(
            build({"offers": ["pronajem"], "districts": ["vysocina-kraj"]}),
            "https://www.ceskereality.cz/pronajem/byty/kraj-vysocina/",
        )
        self.assertEqual(
            build({"offers": ["prodej"], "districts": ["praha"], "sizes": ["2+kk"]}),
            "https://www.ceskereality.cz/prodej/byty/byty-2-kk/hlavni-mesto-praha/",
        )
        self.assertEqual(
            build({"offers": ["prodej"], "sizes": ["5+1"]}),
            "https://www.ceskereality.cz/prodej/byty/byty-5-1-vetsi/",
        )


if __name__ == "__main__":
    unittest.main()
