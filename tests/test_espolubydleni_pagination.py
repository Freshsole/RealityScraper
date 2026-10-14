"""eSpolubydleni: /podnajem-spolubydlici/N, 8 rows/page (rows, not title-anchors), exact total via last-page probe."""

import asyncio

from app.espolubydleni import EspolubydleniClient
from tests._html_paging_helpers import HtmlResponse, patch_http, read


def test_rows_on_page_3_are_parsed():
    # Pages >= 2 carry no title="..." anchors at all; the old parser returned 0 there.
    client = EspolubydleniClient("https://www.espolubydleni.cz/podnajem-spolubydlici/")
    items = client._parse_list(read("espolubydleni_page3.html"))
    assert len(items) == 2
    first = items[0]
    assert first.id == 245153 and first.price_czk == 3750
    assert first.locality == "Ústí nad Labem, Pařížská"
    assert first.name == "Nabízím 6 míst 1L pokoj, Cihlový byt 3+1(kk)"
    assert first.created_on == "2026-10-14" and first.extras["offer"] == "Pronájem"


def test_page_urls():
    client = EspolubydleniClient("https://www.espolubydleni.cz/podnajem-spolubydlici/")
    assert client._page_url(1) == "https://www.espolubydleni.cz/podnajem-spolubydlici/"
    assert client._page_url(7) == "https://www.espolubydleni.cz/podnajem-spolubydlici/7"


def test_total_is_not_the_8000_constant(monkeypatch):
    EspolubydleniClient._total_cache = (0.0, 0)
    calls: list = []

    def handler(method, url, kwargs):
        if url.endswith("/900"):
            return HtmlResponse(read("espolubydleni_beyond.html"), url=url)  # "Další" -> 252
        if url.endswith("/252"):
            return HtmlResponse(read("espolubydleni_last.html"), url=url)  # 2 rows in the fixture
        return HtmlResponse(read("espolubydleni_page3.html"), url=url)

    patch_http(monkeypatch, handler, calls)
    client = EspolubydleniClient("https://www.espolubydleni.cz/podnajem-spolubydlici/")
    listings, total = asyncio.run(client.fetch_page(1))
    assert len(listings) == 2
    assert total == 251 * 8 + 2
    # cached: a second page-1 fetch needs no probe requests
    n = len(calls)
    asyncio.run(client.fetch_page(1))
    assert len(calls) == n + 1
    EspolubydleniClient._total_cache = (0.0, 0)
