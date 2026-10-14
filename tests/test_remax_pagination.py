"""RE/MAX: canonical /reality/byty/{pronajem|prodej}/ list, ?stranka=N, 21 cards, total from "z celkem"."""

import asyncio

from app.portal_urls import remax_url
from app.remax import RemaxClient
from tests._html_paging_helpers import HtmlResponse, patch_http, read


def test_build_url_is_canonical_and_not_price_sorted():
    # ?sale=2&order_by_price=0 redirected and meant "most expensive first".
    assert remax_url.build_url({"offers": ["pronajem"]}) == "https://www.remax-czech.cz/reality/byty/pronajem/"
    assert remax_url.build_url({"offers": ["prodej"]}) == "https://www.remax-czech.cz/reality/byty/prodej/"


def test_parse_keeps_every_card_including_the_last_and_total():
    client = RemaxClient("https://www.remax-czech.cz/reality/byty/prodej/")
    html = read("remax_page.html")
    items = client._parse_list(html)
    assert len(items) == 4  # 3 + trailing card the old lookahead regex dropped
    assert len({i.url for i in items}) == 4
    assert all("/reality/detail/" in i.url for i in items)
    assert items[0].price_czk and items[0].extras["offer"] == "Prodej"
    assert client._parse_total(html) == 2052


def test_page_n_requests_stranka_n_and_legacy_url_is_rewritten(monkeypatch):
    calls: list = []
    patch_http(monkeypatch, lambda m, u, k: HtmlResponse(read("remax_page.html"), url=u), calls)
    client = RemaxClient("https://www.remax-czech.cz/reality/byty/?sale=2&order_by_price=0")
    listings, total = asyncio.run(client.fetch_page(3))
    assert calls[0]["url"] == "https://www.remax-czech.cz/reality/byty/pronajem/?stranka=3"
    assert len(listings) == 4 and total == 2052
    asyncio.run(client.fetch_page(1))
    assert calls[1]["url"] == "https://www.remax-czech.cz/reality/byty/pronajem/"
