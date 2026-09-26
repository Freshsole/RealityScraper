from app.catalog_sync import extra_portal_recent_shards, recent_shards
from app.filter_bridge import search_urls_for_portals
from app.sources import PORTAL_IDS, PORTAL_LABELS, PORTAL_ORDER, portal_of, registered_portals, source_name


def test_ten_portals_registered():
    assert len(PORTAL_IDS) == 10
    assert PORTAL_ORDER == PORTAL_IDS
    rows = registered_portals()
    assert [item["id"] for item in rows] == list(PORTAL_ORDER)
    assert [PORTAL_LABELS[item] for item in PORTAL_ORDER] == [
        "Sreality",
        "Reality.iDNES",
        "Bazoš",
        "ČeskéReality",
        "Bezrealitky",
        "Annonce",
        "M&M Reality",
        "UlovDomov",
        "RE/MAX",
        "Reality.cz",
    ]


def test_portal_of_host_safety():
    assert portal_of("https://www.sreality.cz/hledani/pronajem/byty") == "sreality"
    assert portal_of("https://www.reality.cz/pronajem/byty/") == "realitycz"
    assert portal_of("https://reality.idnes.cz/s/pronajem/byty/") == "idnes"
    assert portal_of("https://www.remax-czech.cz/reality/byty/?sale=2") == "remax"
    assert portal_of("https://www.ulovdomov.cz/pronajem/byty") == "ulovdomov"
    assert source_name("https://www.ceskereality.cz/pronajem/byty/") == "ČeskéReality"


def test_recent_shards_cover_all_portals():
    shards = recent_shards()
    portals = {item["portal"] for item in shards}
    assert portals == set(PORTAL_IDS)
    extra = extra_portal_recent_shards()
    assert {item["portal"] for item in extra} == set(PORTAL_IDS) - {"sreality"}
    assert all(item["kind"] == "catalog_recent" for item in shards)


def test_search_urls_for_all_portals():
    urls = search_urls_for_portals("https://www.sreality.cz/hledani/pronajem/byty?razeni=nejnovejsi")
    assert set(urls) >= set(PORTAL_IDS)
