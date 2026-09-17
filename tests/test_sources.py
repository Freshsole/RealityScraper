from app.block_page import PortalCooldown
from app.catalog_sync import (
    HEALTHY_DISCOVERY_PORTALS,
    extra_portal_recent_shards,
    order_recent_shards,
    prepare_discovery_shards,
    recent_shards,
)
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
    annonce = [item for item in extra if item["portal"] == "annonce"]
    assert {item["shard_key"] for item in annonce} >= {
        "annonce:recent:pronajem:byty",
        "annonce:recent:prodej:byty",
        "annonce:recent:pronajem:domy",
        "annonce:recent:prodej:domy",
    }
    assert all("nabidkovy=1" in item["search_url"] for item in annonce)
    realitycz = [item for item in extra if item["portal"] == "realitycz"]
    assert {item["shard_key"] for item in realitycz} >= {
        "realitycz:recent:pronajem:byty",
        "realitycz:recent:prodej:byty",
        "realitycz:recent:pronajem:domy",
        "realitycz:recent:prodej:domy",
    }
    assert all("s=2" in item["search_url"] for item in realitycz)
    remax = [item for item in extra if item["portal"] == "remax"]
    assert {item["shard_key"] for item in remax} >= {
        "remax:recent:pronajem:byty",
        "remax:recent:prodej:byty",
        "remax:recent:pronajem:domy",
        "remax:recent:prodej:domy",
    }
    assert all("order_by_published_date=0" in item["search_url"] for item in remax)
    assert any("/reality/domy-a-vily/pronajem/" in item["search_url"] for item in remax)


def test_prepare_discovery_shards_extras_first_and_skips_cooldown():
    shards = recent_shards()
    ordered = order_recent_shards(shards)
    assert ordered[0]["portal"] != "sreality"
    assert ordered[-1]["portal"] == "sreality"
    assert {item["portal"] for item in ordered} == set(PORTAL_IDS)
    assert HEALTHY_DISCOVERY_PORTALS == set(PORTAL_IDS) - {"mmreality"}

    ready = prepare_discovery_shards(None)
    assert any(item["portal"] == "mmreality" for item in ready)
    assert ready[0]["portal"] != "sreality"

    cooldown = PortalCooldown()
    cooldown.note("mmreality", "cloudflare")
    filtered = prepare_discovery_shards(cooldown)
    assert all(item["portal"] != "mmreality" for item in filtered)
    assert {item["portal"] for item in filtered} == HEALTHY_DISCOVERY_PORTALS


def test_search_urls_for_all_portals():
    urls = search_urls_for_portals("https://www.sreality.cz/hledani/pronajem/byty?razeni=nejnovejsi")
    assert set(urls) >= set(PORTAL_IDS)
