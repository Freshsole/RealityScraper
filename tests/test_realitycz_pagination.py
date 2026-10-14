"""Reality.cz: ?strana=N is ignored by the site; real paging is ?g=N-1 ("Dalších 25 nabídek")."""

import asyncio

from app.portal_urls import REALITYCZ_REGION_SLUGS, realitycz_url
from app.realitycz import RealityczClient
from tests._html_paging_helpers import HtmlResponse, patch_http, read


def test_build_url_nationwide_or_region():
    assert realitycz_url.build_url({"offers": ["pronajem"]}) == "https://www.reality.cz/pronajem/byty/Ceska-republika/"
    one = realitycz_url.build_url({"offers": ["prodej"], "districts": ["praha"]})
    assert one == "https://www.reality.cz/prodej/byty/hlavni-mesto-Praha/"
    assert len(REALITYCZ_REGION_SLUGS) == 14


def test_parse_cards_alphanumeric_prefix_total_and_gps():
    client = RealityczClient("https://www.reality.cz/pronajem/byty/hlavni-mesto-Praha/")
    html = read("realitycz_page1.html")
    items = client._parse_list(html)
    assert len(items) == 3 and len({i.id for i in items}) == 3
    assert items[0].url == "https://www.reality.cz/BNB-SCB16L/"
    assert items[0].price_czk == 24000 and items[0].disposition == "2+kk" and items[0].area_m2 == 55
    assert items[0].lat == 50.060772 and items[0].lon == 14.390681
    assert items[1].lat is None  # gpsx0 gpsy0 = unknown
    assert client._parse_total(html) == 293


def test_ids_do_not_collide_across_agencies():
    client = RealityczClient("https://www.reality.cz/pronajem/byty/hlavni-mesto-Praha/")
    a = client._parse_card('<a href="DMQ-003736/?c=1">x</a>', "prodej")
    b = client._parse_card('<a href="BNB-003736/?c=1">x</a>', "prodej")
    assert a.id != b.id


def test_page_n_uses_g_cursor_not_strana(monkeypatch):
    calls: list = []
    patch_http(monkeypatch, lambda m, u, k: HtmlResponse(read("realitycz_page2.html"), url=u), calls)
    client = RealityczClient("https://www.reality.cz/pronajem/byty/Ceska-republika/?strana=9")
    asyncio.run(client.fetch_page(1))
    asyncio.run(client.fetch_page(2))
    asyncio.run(client.fetch_page(12))
    assert [c["url"] for c in calls] == [
        "https://www.reality.cz/pronajem/byty/Ceska-republika/",
        "https://www.reality.cz/pronajem/byty/Ceska-republika/?g=1",
        "https://www.reality.cz/pronajem/byty/Ceska-republika/?g=11",
    ]
