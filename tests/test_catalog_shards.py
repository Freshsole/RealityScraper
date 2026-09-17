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
    assert any(item["shard_key"] == "annonce:domy:pronajem:cz" for item in daily)
    assert any("domy-k-pronajmu.html" in item["search_url"] for item in daily)
    assert any(item["shard_key"] == "annonce:recent:pronajem:domy" for item in recent)
    assert all("nabidkovy=1" in item["search_url"] for item in daily + recent)


def test_remax_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "remax"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "remax"]
    assert any(item["shard_key"] == "remax:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "remax:recent:pronajem:domy" for item in recent)
    assert all("order_by_published_date=0" in item["search_url"] for item in daily + recent)
    assert any("/reality/domy-a-vily/pronajem/" in item["search_url"] for item in recent)
    assert any("/reality/byty/pronajem/" in item["search_url"] for item in recent)


def test_realitycz_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "realitycz"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "realitycz"]
    assert any(item["shard_key"] == "realitycz:domy:pronajem:cz" for item in daily)
    assert any(item["shard_key"] == "realitycz:recent:pronajem:domy" for item in recent)
    assert all("s=2" in item["search_url"] for item in daily + recent)
    assert any("/pronajem/domy/Ceska-republika/" in item["search_url"] for item in recent)
    assert any("/pronajem/byty/Ceska-republika/" in item["search_url"] for item in recent)


def test_sreality_recent_shards_cover_sizes():
    recent = sreality_recent_shards()
    assert len(recent) == 28
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


def test_idnes_newest_and_house_shards():
    daily = [item for item in daily_shards() if item["portal"] == "idnes"]
    recent = [item for item in extra_portal_recent_shards() if item["portal"] == "idnes"]
    assert any(item["shard_key"].startswith("idnes:domy:pronajem:") for item in daily)
    assert {item["shard_key"] for item in recent} >= {
        "idnes:recent:pronajem:byty",
        "idnes:recent:prodej:byty",
        "idnes:recent:pronajem:domy",
        "idnes:recent:prodej:domy",
    }
    assert any("/s/pronajem/domy/" in item["search_url"] for item in recent)
    assert any("/s/prodej/domy/" in item["search_url"] for item in recent)
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
    }
    assert any("/pronajem/rodinne-domy/nejnovejsi/" in item["search_url"] for item in recent)
    assert any("/prodej/rodinne-domy/nejnovejsi/" in item["search_url"] for item in recent)
    assert any("/pronajem/byty/nejnovejsi/" in item["search_url"] for item in recent)
    assert all("/pronajem/domy/" not in item["search_url"] for item in daily + recent)
    houses = [item for item in recent if item["shard_key"].endswith(":domy")]
    assert all("rodinne-domy" in item["search_url"] for item in houses)


def test_sreality_house_daily_shards():
    daily = [item for item in daily_shards() if item["portal"] == "sreality"]
    keys = {item["shard_key"] for item in daily}
    assert "sreality:domy:pronajem:cz" in keys
    assert "sreality:domy:prodej:cz" in keys
    houses = [item for item in daily if item["shard_key"].startswith("sreality:domy:")]
    assert all("razeni=nejnovejsi" in item["search_url"] for item in houses)
    assert any("/pronajem/domy" in item["search_url"] for item in houses)
    assert all("velikost=" not in item["search_url"] for item in houses)
