"""Eurobydleni: nationwide /byty/{offer}/, /page-N/ HTML for the first page, XHR (JSON) for later pages."""

import asyncio
import json

from app.eurobydleni import EurobydleniClient
from app.portal_urls import eurobydleni_url
from tests._html_paging_helpers import HtmlResponse, patch_http, read


def test_build_url_is_nationwide():
    assert eurobydleni_url.build_url({"offers": ["pronajem"]}) == "https://www.eurobydleni.cz/byty/pronajem/"
    assert eurobydleni_url.build_url({"offers": ["prodej"]}) == "https://www.eurobydleni.cz/byty/prodej/"


def test_html_parse_and_max_page_bound():
    client = EurobydleniClient("https://www.eurobydleni.cz/byty/pronajem/")
    html = read("eurobydleni_page1.html")
    items = client._parse_list(html)
    assert len(items) == 3 and items[0].price_czk == 19000
    assert client._parse_total(html) == 410 * 12
    assert client._page_url(3) == "https://www.eurobydleni.cz/byty/pronajem/page-3/"


def test_first_page_html_then_xhr_with_page_path(monkeypatch):
    EurobydleniClient._total_cache.clear()
    calls: list = []

    def handler(method, url, kwargs):
        if method == "GET":
            return HtmlResponse(read("eurobydleni_page1.html"), url=url)
        href = kwargs["data"]["href"]
        name = "eurobydleni_ajax_last.json" if href.endswith("/page-410/") else "eurobydleni_ajax_page2.json"
        return HtmlResponse(read(name), url=url)

    patch_http(monkeypatch, handler, calls)
    client = EurobydleniClient("https://www.eurobydleni.cz/byty/pronajem/")
    listings, total = asyncio.run(client.fetch_page(1))
    assert calls[0]["method"] == "GET" and len(listings) == 3
    # exact total = 409 * 12 + rows on the last page (2 in the fixture), via XHR
    assert total == 409 * 12 + 2
    assert calls[1]["method"] == "POST" and calls[1]["data"]["href"] == "/byty/pronajem/page-410/"

    listings2, _ = asyncio.run(client.fetch_page(2))
    assert calls[-1]["method"] == "POST" and calls[-1]["data"]["href"] == "/byty/pronajem/page-2/"
    assert calls[-1]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert len(listings2) == 3


def test_xhr_redirect_falls_back_to_html(monkeypatch):
    EurobydleniClient._total_cache.clear()
    calls: list = []

    def handler(method, url, kwargs):
        if method == "POST":
            return HtmlResponse(json.dumps({"redirect": "/byty/pronajem/page-2/"}))
        return HtmlResponse(read("eurobydleni_page1.html"), url=url)

    patch_http(monkeypatch, handler, calls)
    client = EurobydleniClient("https://www.eurobydleni.cz/byty/pronajem/")
    client._session_warm = True
    listings, _ = asyncio.run(client.fetch_page(2))
    assert [c["method"] for c in calls] == ["POST", "GET"]
    assert calls[1]["url"].endswith("/page-2/") and len(listings) == 3


def test_out_of_range_page_is_empty_not_a_wrapped_page_one(monkeypatch):
    calls: list = []
    patch_http(monkeypatch, lambda m, u, k: HtmlResponse(read("eurobydleni_page1.html"), url=u), calls)
    client = EurobydleniClient("https://www.eurobydleni.cz/byty/pronajem/")
    client._max_page = 410
    assert asyncio.run(client.fetch_page(411)) == ([], 0)
    assert calls == []
