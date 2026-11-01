from app import portal_urls
from app.catalog_sync import normalize_search_url

FILTERS = {
    "offers": ["pronajem"], "category": "byty", "sizes": ["2+kk", "2+1"],
    "districts": ["praha-1", "praha-2"], "price_to": 25759, "area_from": 45,
}


def test_filters_roundtrip_where_portal_supports_them():
    for pid in ("remax", "mmreality", "ulovdomov", "realitycz", "realitymix", "realingo"):
        mod = portal_urls.MODULES[pid]
        url = mod.build_url(FILTERS)
        parsed = mod.parse_url(url)
        assert parsed["price_to"] == 25759, pid
        assert set(parsed["sizes"]) == {"2+kk", "2+1"}, (pid, parsed)
        assert normalize_search_url(url) == url, pid


def test_plain_urls_unchanged_without_filters():
    assert portal_urls.remax_url.build_url({"offers": ["pronajem"]}) == "https://www.remax-czech.cz/reality/byty/pronajem/"
    assert portal_urls.realingo_url.build_url({"offers": ["pronajem"]}) == "https://www.realingo.cz/pronajem_byty/cr/"
    assert portal_urls.mmreality_url.build_url({"offers": ["prodej"]}) == "https://www.mmreality.cz/nemovitosti/prodej/byty/"
