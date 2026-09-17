from app.catalog_sync import daily_shards, extra_portal_recent_shards, sreality_recent_shards


def test_sreality_shards_split_praha():
    shards = [item for item in daily_shards() if item["portal"] == "sreality"]
    keys = {item["shard_key"] for item in shards}
    assert not any(key.endswith(":praha:all") or ":praha:" in key and key.count(":") == 3 for key in keys)
    assert any("praha-1" in key for key in keys)
    assert any("praha-7:2+kk" in key for key in keys)
    assert len(shards) > 100


def test_annonce_house_shards_are_offer_only():
    daily = [item for item in daily_shards() if item["portal"] == "annonce"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "annonce"]
    assert {item["shard_key"] for item in recent} >= {
        "annonce:recent:pronajem:byty",
        "annonce:recent:prodej:byty",
        "annonce:recent:pronajem:domy",
        "annonce:recent:prodej:domy",
        "annonce:recent:pronajem:pozemky",
        "annonce:recent:prodej:pozemky",
    }
    assert {item["shard_key"] for item in daily} >= {
        "annonce:byty:pronajem:cz",
        "annonce:byty:prodej:cz",
        "annonce:domy:pronajem:cz",
        "annonce:domy:prodej:cz",
        "annonce:pozemky:pronajem:cz",
        "annonce:pozemky:prodej:cz",
    }
    assert any("domy-k-pronajmu.html" in item["search_url"] for item in daily)
    assert any("domy-na-prodej.html" in item["search_url"] for item in daily + recent)
    assert any("byty-na-prodej.html" in item["search_url"] for item in recent)
    assert any("/pozemky.html" in item["search_url"] and "business_type=131" in item["search_url"] for item in recent)
    assert any("/pozemky.html" in item["search_url"] and "business_type=130" in item["search_url"] for item in recent)
    assert all("nabidkovy=1" in item["search_url"] for item in daily + recent)
    assert all("sort=ageasc" in item["search_url"] for item in daily + recent)
    assert all("/rodinne-domy.html" not in item["search_url"] for item in daily + recent)


def test_remax_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "remax"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "remax"]
    assert any(item["shard_key"] == "remax:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "remax:recent:pronajem:domy" for item in recent)
    assert all("order_by_published_date=0" in item["search_url"] for item in daily + recent)
    assert any("/reality/domy-a-vily/pronajem/" in item["search_url"] for item in recent)
    assert any("/reality/byty/pronajem/" in item["search_url"] for item in recent)
    assert any("/reality/pozemky/prodej/" in item["search_url"] for item in recent)


def test_realitycz_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "realitycz"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "realitycz"]
    assert any(item["shard_key"] == "realitycz:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "realitycz:recent:pronajem:domy" for item in recent)
    assert all("s=2" in item["search_url"] for item in daily + recent)
    assert any("/pronajem/domy/Ceska-republika/" in item["search_url"] for item in recent)
    assert any("/pronajem/byty/Ceska-republika/" in item["search_url"] for item in recent)
    assert any("/prodej/pozemky/Ceska-republika/" in item["search_url"] for item in recent)


def test_sreality_recent_shards_cover_sizes():
    recent = sreality_recent_shards()
    assert len(recent) == 30
    assert recent[0]["kind"] == "catalog_recent"
    assert "velikost=" in recent[0]["search_url"]
    assert "razeni=nejnovejsi" in recent[0]["search_url"]
    houses = [item for item in recent if item["shard_key"].endswith(":domy")]
    assert {item["shard_key"] for item in houses} == {
        "sreality:recent:pronajem:domy",
        "sreality:recent:prodej:domy",
    }
    assert all("/domy" in item["search_url"] for item in houses)
    assert all("razeni=nejnovejsi" in item["search_url"] for item in houses)
    assert all("velikost=" not in item["search_url"] for item in houses)
    land = [item for item in recent if item["shard_key"].endswith(":pozemky")]
    assert {item["shard_key"] for item in land} == {
        "sreality:recent:pronajem:pozemky",
        "sreality:recent:prodej:pozemky",
    }
    assert all("/pozemky" in item["search_url"] for item in land)
    assert all("velikost=" not in item["search_url"] for item in land)
    assert all("/praha" not in item["search_url"] for item in land)


def test_idnes_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "idnes"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "idnes"]
    assert any(item["shard_key"].startswith("idnes:domy:pronajem:") for item in daily)
    assert {item["shard_key"] for item in recent} >= {
        "idnes:recent:pronajem:byty",
        "idnes:recent:prodej:byty",
        "idnes:recent:pronajem:domy",
        "idnes:recent:prodej:domy",
        "idnes:recent:pronajem:pozemky",
        "idnes:recent:prodej:pozemky",
    }
    assert any("/s/pronajem/domy/" in item["search_url"] for item in recent)
    assert any("/s/prodej/domy/" in item["search_url"] for item in recent)
    assert any("/s/prodej/pozemky/" in item["search_url"] for item in recent)
    assert any("/s/pronajem/byty/" in item["search_url"] for item in recent)
    assert all("/s/pronajem/dum/" not in item["search_url"] for item in daily + recent)
    assert all("/s/pronajem/byt/" not in item["search_url"] for item in daily + recent)
    houses = [item for item in recent if item["shard_key"].endswith(":domy")]
    assert all("/praha/" not in item["search_url"] for item in houses)


def test_bazos_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "bazos"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "bazos"]
    assert any(item["shard_key"] == "bazos:dum:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "bazos:recent:pronajem:domy" for item in recent)
    assert any("/pronajmu/dum/" in item["search_url"] for item in recent)
    assert any("/prodam/dum/" in item["search_url"] for item in recent)
    assert any("/prodam/pozemek/" in item["search_url"] for item in recent)
    assert any("/pronajmu/pozemek/" in item["search_url"] for item in recent)
    assert any("/pronajmu/byt/" in item["search_url"] for item in recent)
    assert all("/byty/" not in item["search_url"] and "/domy/" not in item["search_url"] for item in daily + recent)
    assert all("kitx=ano" in item["search_url"] for item in recent)


def test_ceskereality_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "ceskereality"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "ceskereality"]
    assert any(item["shard_key"] == "ceskereality:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "ceskereality:recent:pronajem:domy" for item in recent)
    assert {item["shard_key"] for item in recent} >= {
        "ceskereality:recent:pronajem:byty",
        "ceskereality:recent:prodej:byty",
        "ceskereality:recent:pronajem:domy",
        "ceskereality:recent:prodej:domy",
        "ceskereality:recent:pronajem:pozemky",
        "ceskereality:recent:prodej:pozemky",
    }
    assert any("/pronajem/rodinne-domy/nejnovejsi/" in item["search_url"] for item in recent)
    assert any("/prodej/rodinne-domy/nejnovejsi/" in item["search_url"] for item in recent)
    assert any("/prodej/pozemky/nejnovejsi/" in item["search_url"] for item in recent)
    assert any("/pronajem/byty/nejnovejsi/" in item["search_url"] for item in recent)
    assert all("/pronajem/domy/" not in item["search_url"] for item in daily + recent)
    houses = [item for item in recent if item["shard_key"].endswith(":domy")]
    assert all("rodinne-domy" in item["search_url"] for item in houses)


def test_bezrealitky_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "bezrealitky"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "bezrealitky"]
    assert any(item["shard_key"] == "bezrealitky:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "bezrealitky:recent:pronajem:domy" for item in recent)
    assert {item["shard_key"] for item in recent} >= {
        "bezrealitky:recent:pronajem:byty",
        "bezrealitky:recent:prodej:byty",
        "bezrealitky:recent:pronajem:domy",
        "bezrealitky:recent:prodej:domy",
        "bezrealitky:recent:pronajem:pozemky",
        "bezrealitky:recent:prodej:pozemky",
    }
    assert any("estateType=DUM" in item["search_url"] for item in recent)
    assert any("estateType=BYT" in item["search_url"] for item in recent)
    assert any("estateType=POZEMEK" in item["search_url"] for item in recent)
    houses = [item for item in recent if item["shard_key"].endswith(":domy")]
    assert all("estateType=DUM" in item["search_url"] for item in houses)
    assert all("estateType=BYT" not in item["search_url"] for item in houses)
    assert all("order=TIMEORDER_DESC" in item["search_url"] for item in houses)
    daily_houses = [item for item in daily if item["shard_key"].startswith("bezrealitky:domy:")]
    assert {item["shard_key"] for item in daily_houses} == {
        "bezrealitky:domy:pronajem:cz",
        "bezrealitky:domy:prodej:cz",
    }
    assert all("estateType=DUM" in item["search_url"] for item in daily_houses)


def test_ulovdomov_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "ulovdomov"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "ulovdomov"]
    assert any(item["shard_key"] == "ulovdomov:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "ulovdomov:recent:pronajem:domy" for item in recent)
    assert {item["shard_key"] for item in recent} >= {
        "ulovdomov:recent:pronajem:byty",
        "ulovdomov:recent:prodej:byty",
        "ulovdomov:recent:pronajem:domy",
        "ulovdomov:recent:prodej:domy",
        "ulovdomov:recent:pronajem:pozemky",
        "ulovdomov:recent:prodej:pozemky",
    }
    assert any(item["search_url"].endswith("/pronajem/domy") for item in recent)
    assert any(item["search_url"].endswith("/prodej/domy") for item in recent)
    assert any(item["search_url"].endswith("/prodej/pozemky") for item in recent)
    assert any(item["search_url"].endswith("/pronajem/byty") for item in recent)
    houses = [item for item in recent if item["shard_key"].endswith(":domy")]
    assert all("/domy" in item["search_url"] for item in houses)


def test_sreality_house_daily_shards():
    daily = [item for item in daily_shards() if item["portal"] == "sreality"]
    keys = {item["shard_key"] for item in daily}
    assert "sreality:domy:pronajem:cz" in keys
    assert "sreality:domy:prodej:cz" in keys
    houses = [item for item in daily if item["shard_key"].startswith("sreality:domy:")]
    assert all("razeni=nejnovejsi" in item["search_url"] for item in houses)
    assert any("/pronajem/domy" in item["search_url"] for item in houses)
    assert all("velikost=" not in item["search_url"] for item in houses)
    assert "sreality:pozemky:pronajem:cz" in keys
    assert "sreality:pozemky:prodej:cz" in keys
    land = [item for item in daily if item["shard_key"].startswith("sreality:pozemky:")]
    assert all("/pozemky" in item["search_url"] for item in land)
    assert all("velikost=" not in item["search_url"] for item in land)
    assert all("/praha" not in item["search_url"] for item in land)


def test_newest_and_daily_pozemky_shards():
    daily = daily_shards()
    recent = extra_portal_recent_shards() + sreality_recent_shards()
    want_recent = {
        f"{portal}:recent:{offer}:pozemky"
        for portal in (
            "sreality",
            "annonce",
            "realitycz",
            "remax",
            "ceskereality",
            "ulovdomov",
            "bazos",
            "idnes",
            "bezrealitky",
        )
        for offer in ("pronajem", "prodej")
    }
    assert {item["shard_key"] for item in recent} >= want_recent
    land = [item for item in recent if item["shard_key"].endswith(":pozemky")]
    assert all("pozem" in item["search_url"].casefold() for item in land)
    assert any("/pozemky.html" in item["search_url"] and "business_type=130" in item["search_url"] for item in land)
    assert any("/reality/pozemky/prodej/" in item["search_url"] for item in land)
    assert any("/prodej/pozemky/Ceska-republika/" in item["search_url"] for item in land)
    assert any("/prodej/pozemky/nejnovejsi/" in item["search_url"] for item in land)
    assert any(item["search_url"].endswith("/prodej/pozemky") for item in land)
    assert any("/prodam/pozemek/" in item["search_url"] for item in land)
    assert any("/s/prodej/pozemky/" in item["search_url"] for item in land)
    assert any("estateType=POZEMEK" in item["search_url"] for item in land)
    assert any("/hledani/prodej/pozemky" in item["search_url"] for item in land)
    assert any(item["shard_key"] == "annonce:pozemky:prodej:cz" for item in daily)
    assert any(item["shard_key"] == "bezrealitky:pozemky:pronajem:cz" for item in daily)
    bazos_land = [item for item in land if item["portal"] == "bazos"]
    assert all("/pozemek/" in item["search_url"] for item in bazos_land)
    assert all("/pozemky/" not in item["search_url"] for item in bazos_land)
