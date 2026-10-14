"""RealityMIX: nationwide /reality/byty/{offer}, ?stranka=N, 20 cards (nested <li>), total from "z celkem"."""

import asyncio

from app.portal_urls import realitymix_url
from app.realitymix import RealitymixClient
from tests._html_paging_helpers import HtmlResponse, patch_http, read


def test_build_url_is_nationwide():
    assert realitymix_url.build_url({"offers": ["pronajem"]}) == "https://realitymix.cz/reality/byty/pronajem"
    assert realitymix_url.build_url({"offers": ["prodej"]}) == "https://realitymix.cz/reality/byty/prodej"


def test_cards_with_nested_li_are_all_parsed_without_duplicates():
    client = RealitymixClient("https://realitymix.cz/reality/byty/pronajem")
    html = read("realitymix_page.html")
    items = client._parse_list(html)
    assert len(items) == 3
    assert len({i.url for i in items}) == 3 and len({i.id for i in items}) == 3
    first = items[0]
    assert first.price_czk == 17900 and first.extras["offer"] == "Pronájem"
    assert first.disposition == "2+kk" and "Komořsku" in first.locality
    assert client._parse_total(html) == 5165


def test_listing_id_is_stable_not_python_hash():
    client = RealitymixClient("https://realitymix.cz/reality/byty/pronajem")
    assert [i.id for i in client._parse_list(read("realitymix_page.html"))][0] == 8698633


def test_page_n_requests_stranka_n(monkeypatch):
    calls: list = []
    patch_http(monkeypatch, lambda m, u, k: HtmlResponse(read("realitymix_page.html"), url=u), calls)
    client = RealitymixClient("https://realitymix.cz/reality/byty/pronajem")
    listings, total = asyncio.run(client.fetch_page(3))
    assert calls[0]["url"] == "https://realitymix.cz/reality/byty/pronajem?stranka=3"
    assert len(listings) == 3 and total == 5165
