"""Annonce: ?page=N paging (``strana`` is ignored by the site), total from the pager comment, out-of-range guard."""

import asyncio
import re
import unittest
from pathlib import Path

import httpx

from app.annonce import AnnonceClient
from app.portal_urls import annonce_url

FIXTURE = (Path(__file__).parent / "fixtures" / "annonce_list_p2.html").read_text(encoding="utf-8")
LAST_PAGE = 3


def _page_html(page: int, shown: int) -> str:
    body = FIXTURE.replace("actualPage: 2,", f"actualPage: {shown},").replace("pageCount: 75", f"pageCount: {LAST_PAGE}")
    return re.sub(r"(\d{8})(-[a-z0-9]+\.html)", lambda m: str(int(m.group(1)) + shown * 100) + m.group(2), body)


def _client(seen: list[str]) -> AnnonceClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/":
            return httpx.Response(200, text="home")
        page = int(request.url.params.get("page", "1"))
        # Real behaviour: pages past the end silently render page 1 again.
        shown = page if page <= LAST_PAGE else 1
        return httpx.Response(200, text=_page_html(page, shown))

    client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    client._warmed_up = True  # no homepage warmup / sleep in tests
    return client


class AnnonceParseTests(unittest.TestCase):
    def test_every_card_is_parsed_and_total_is_real(self):
        client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
        listings, total = client._parse_blob(FIXTURE.encode(), 2)
        asyncio.run(client.aclose())
        self.assertEqual(total, 1485)
        # Regression: the lazy CARD_RE used to swallow every second card (10 of 20 per page).
        self.assertEqual([item.id for item in listings], [89182965, 89225517, 89225473])
        first = listings[0]
        self.assertEqual(first.price_czk, 21500)
        self.assertEqual(first.locality, "Kladno")
        self.assertEqual(first.disposition, "2+1")
        self.assertEqual(first.area_m2, 77)
        self.assertTrue(first.created_on.startswith("2026-10-14"))

    def test_out_of_range_page_is_empty(self):
        client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
        page_one = FIXTURE.replace("actualPage: 2,", "actualPage: 1,")
        listings, total = client._parse_blob(page_one.encode(), 9)
        asyncio.run(client.aclose())
        self.assertEqual(listings, [])
        self.assertEqual(total, 1485)


class AnnoncePagingTests(unittest.TestCase):
    def test_page_url_uses_page_param(self):
        client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html")
        self.assertEqual(client._page_url(1), "https://www.annonce.cz/byty-k-pronajmu.html")
        self.assertEqual(client._page_url(4), "https://www.annonce.cz/byty-k-pronajmu.html?page=4")
        asyncio.run(client.aclose())

    def test_fetch_all_stops_at_page_count(self):
        seen: list[str] = []
        client = _client(seen)

        async def run():
            try:
                return await client.fetch_all()
            finally:
                await client.aclose()

        listings, total = asyncio.run(run())
        self.assertEqual(total, 1485)
        self.assertEqual(len(listings), 3 * LAST_PAGE)
        self.assertTrue(all("strana" not in url for url in seen))

    def test_url_builder(self):
        self.assertEqual(annonce_url.build_url({"offers": ["prodej"]}), "https://www.annonce.cz/byty-na-prodej.html")
        self.assertEqual(annonce_url.build_url({"offers": ["pronajem"]}), "https://www.annonce.cz/byty-k-pronajmu.html")


if __name__ == "__main__":
    unittest.main()
