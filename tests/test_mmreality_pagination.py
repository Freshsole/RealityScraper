"""M&M Reality: /nemovitosti/<offer>/byty/?page=N, total from the SSR state, city extraction."""

import asyncio
import re
import unittest
from pathlib import Path

import httpx

from app.mmreality import MmrealityClient
from app.portal_urls import mmreality_url

FIXTURE = (Path(__file__).parent / "fixtures" / "mmreality_list_p1.html").read_text(encoding="utf-8")


def _client(seen: list[str], pages: int = 3) -> MmrealityClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        page = int(request.url.params.get("page", "1"))
        if page > pages:  # past the end the site renders an empty list
            body = re.sub(r"<a href=.*?</a>", "", FIXTURE, flags=re.S)
        else:
            body = re.sub(r"/nemovitosti/(\d+)/", lambda m: f"/nemovitosti/{int(m.group(1)) + page * 1000}/", FIXTURE)
        return httpx.Response(200, text=body)

    client = MmrealityClient("https://www.mmreality.cz/nemovitosti/pronajem/byty/")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    client._warmed_up = True
    return client


class MmrealityParseTests(unittest.TestCase):
    def test_cards_and_real_total(self):
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/pronajem/byty/")
        listings, total = client._parse_blob(FIXTURE.encode(), 1)
        asyncio.run(client.aclose())
        self.assertEqual(total, 593)
        self.assertEqual([item.id for item in listings], [958036, 958020, 957966])
        self.assertEqual(listings[0].price_czk, 25000)
        self.assertEqual(listings[0].disposition, "2+1")
        self.assertEqual(listings[0].area_m2, 54)
        self.assertEqual(listings[0].locality, "Praha")
        # Name without a street: city is the last part, not the "60 m²" part.
        self.assertEqual(listings[2].locality, "Janov nad Nisou")

    def test_page_size_matches_site(self):
        self.assertEqual(MmrealityClient.PAGE_SIZE, 11)


class MmrealityPagingTests(unittest.TestCase):
    def test_page_url(self):
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/prodej/byty/")
        self.assertEqual(client._page_url(1), "https://www.mmreality.cz/nemovitosti/prodej/byty/")
        self.assertEqual(client._page_url(7), "https://www.mmreality.cz/nemovitosti/prodej/byty/?page=7")
        asyncio.run(client.aclose())

    def test_fetch_all_walks_until_empty_page(self):
        seen: list[str] = []
        client = _client(seen, pages=3)

        async def run():
            try:
                return await client.fetch_all()
            finally:
                await client.aclose()

        listings, total = asyncio.run(run())
        self.assertEqual(total, 593)
        self.assertEqual(len(listings), 9)
        self.assertEqual(len(seen), 4)
        self.assertTrue(seen[1].endswith("?page=2"))

    def test_builder_restricts_to_flats(self):
        self.assertEqual(
            mmreality_url.build_url({"offers": ["pronajem"]}), "https://www.mmreality.cz/nemovitosti/pronajem/byty/"
        )
        self.assertEqual(
            mmreality_url.build_url({"offers": ["prodej"]}), "https://www.mmreality.cz/nemovitosti/prodej/byty/"
        )


if __name__ == "__main__":
    unittest.main()
