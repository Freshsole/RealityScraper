import json
import random
import time
from pathlib import Path

import pytest

from app.games import (
    TEACHING_RATIO,
    disposition_rank,
    higher_lower_pair,
    is_teaching_pair,
    leaderboard,
    live_pairable_pool,
    locality_key,
    pick_same_locality_pair,
    preferred_game_pool,
    public_card,
    public_higher_lower,
    public_rent_round,
    public_rent_score,
    refresh_pool_now,
    rent_round,
    reset_pool_cache,
    save_rent_round,
    score_guess,
    score_round,
    wait_refresh,
)
from app.sreality import Listing
from app.site_pages import site_body, site_html, site_page
from app.store import Store


@pytest.fixture(autouse=True)
def _reset_game_pool_cache():
    reset_pool_cache()
    yield
    wait_refresh(0.6)
    reset_pool_cache()


def _flat(key, locality, disposition, area, price, **extra):
    row = {
        "id": key,
        "name": f"{disposition} {locality}",
        "locality": locality,
        "disposition": disposition,
        "area_m2": area,
        "price_czk": price,
        "image_url": "/static/site/assets/sold-1.webp",
        "portal": "sreality",
        "vanish_hours": 2.0,
        "locality_key": locality_key(locality),
    }
    row.update(extra)
    return row


def test_score_guess_perfect_and_far():
    perfect = score_guess(20000, 20000)
    assert perfect["points"] == 1000
    assert perfect["error_pct"] == 0
    far = score_guess(20000, 40000)
    assert far["points"] == 0
    mid = score_guess(20000, 25000)
    assert 0 < mid["points"] < 1000


def test_score_round_and_leaderboard(tmp_path: Path):
    store = Store(tmp_path / "games.sqlite")
    items = [
        {"id": "a", "name": "A", "locality": "Praha", "price_czk": 10000, "portal": "sreality"},
        {"id": "b", "name": "B", "locality": "Brno", "price_czk": 20000, "portal": "bezrealitky"},
    ]
    scored = score_round(items, [{"id": "a", "guess": 10000}, {"id": "b", "guess": 20000}])
    assert scored["score"] == 2000
    assert scored["accuracy"] == 100.0
    save_rent_round(store, player_name="Eva", scored=scored)
    miss = score_round(items, [{"id": "a", "guess": 15000}, {"id": "b", "guess": 10000}])
    save_rent_round(store, player_name="Jan", scored=miss)
    board = leaderboard(store)
    assert board["stats"]["n"] == 2
    assert board["top"][0]["player_name"] == "Eva"
    assert board["top"][0]["score"] == 2000
    assert len(board["recent"]) == 2
    assert board["recent"][0]["items"]


def test_locality_key_canonicalizes_neighborhood():
    assert locality_key("Praha 2 – Vinohrady") == "praha-2-vinohrady"
    assert locality_key("Vinohrady, Praha 2") == "praha-2-vinohrady"
    assert locality_key("Praha 2, Vinohrady") == "praha-2-vinohrady"
    assert locality_key("Praha 3 – Žižkov") == "praha-3-zizkov"
    assert locality_key("Bedihošť") == "bedihost"
    assert locality_key("Brno – střed") == "brno-stred"
    assert locality_key("Praha 2 – Vinohrady") != locality_key("Bedihošť")
    assert locality_key("Praha 2 – Vinohrady") != locality_key("Praha 3 – Žižkov")


def test_disposition_rank_orders_layouts():
    assert disposition_rank("1+kk") < disposition_rank("1+1") < disposition_rank("2+kk")
    assert disposition_rank("2+kk") < disposition_rank("3+kk") < disposition_rank("4+kk")


def test_teaching_pair_better_and_cheaper():
    better = _flat("good", "Praha 2 – Vinohrady", "3+kk", 82, 21900)
    worse = _flat("bad", "Praha 2 – Vinohrady", "2+kk", 46, 26800)
    assert is_teaching_pair(better, worse) is True
    normal_cheap = _flat("small", "Praha 2 – Vinohrady", "1+kk", 32, 14200)
    normal_big = _flat("big", "Praha 2 – Vinohrady", "3+kk", 82, 21900)
    assert is_teaching_pair(normal_cheap, normal_big) is False


def test_higher_lower_seed_pair(tmp_path: Path):
    store = Store(tmp_path / "empty.sqlite")
    pair = higher_lower_pair(store)
    assert pair["left"]["id"] != pair["right"]["id"]
    assert pair["cheaper"] in {"left", "right"}
    cheaper = pair["left"] if pair["cheaper"] == "left" else pair["right"]
    other = pair["right"] if pair["cheaper"] == "left" else pair["left"]
    assert cheaper["price_czk"] <= other["price_czk"]
    assert pair["seeded"] is True
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"]
    assert pair["locality_key"]
    assert pair["pair_kind"] in {"teaching", "random"}
    assert {"cheaper", "copy", "copy_ok", "copy_miss", "left", "right", "seeded", "vanish_hours", "vanish_label"} <= set(pair)


def test_pairs_never_mix_cities():
    pool = [
        _flat("v1", "Praha 2 – Vinohrady", "3+kk", 80, 20000),
        _flat("v2", "Praha 2 – Vinohrady", "1+kk", 30, 25000),
        _flat("b1", "Bedihošť", "2+kk", 50, 12000),
        _flat("b2", "Bedihošť", "1+kk", 28, 15000),
    ]
    for seed in range(250):
        pair = pick_same_locality_pair(pool, rng=random.Random(seed))
        left_key = pair["left"]["locality_key"]
        right_key = pair["right"]["locality_key"]
        assert left_key == right_key == pair["locality_key"]
        cities = {pair["left"]["id"][0], pair["right"]["id"][0]}
        assert len(cities) == 1


def test_teaching_distribution_is_about_80_percent():
    pool = [
        _flat("v-good", "Praha 2 – Vinohrady", "3+kk", 80, 20000),
        _flat("v-bad", "Praha 2 – Vinohrady", "1+kk", 30, 26000),
        _flat("b-small", "Bedihošť", "1+kk", 28, 10000),
        _flat("b-big", "Bedihošť", "3+kk", 70, 18000),
    ]
    rng = random.Random(7)
    n = 400
    kinds = [
        pick_same_locality_pair(pool, rng=rng, teaching_ratio=TEACHING_RATIO)["pair_kind"]
        for _ in range(n)
    ]
    rate = kinds.count("teaching") / n
    assert 0.72 <= rate <= 0.88, f"teaching rate {rate:.3f}"
    forced = [
        pick_same_locality_pair(pool, rng=random.Random(i), teaching_ratio=1.0)
        for i in range(80)
    ]
    assert all(row["pair_kind"] == "teaching" for row in forced)
    assert all(is_teaching_pair(row["left"], row["right"]) for row in forced)


def test_cold_path_does_not_block_on_slow_catalog(tmp_path: Path):
    class Slow(Store):
        def game_listing_pool(self, **kwargs):
            time.sleep(0.3)
            return []

    store = Slow(tmp_path / "slow.sqlite")
    t0 = time.perf_counter()
    pair = higher_lower_pair(store)
    hl_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    round_payload = rent_round(store)
    rent_ms = (time.perf_counter() - t0) * 1000
    assert hl_ms < 50, f"higher-lower cold path {hl_ms:.1f}ms"
    assert rent_ms < 50, f"rent-round cold path {rent_ms:.1f}ms"
    assert pair["seeded"] is True
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"]
    assert len(round_payload["items"]) == 5


def test_refresh_uses_live_catalog_rentals(tmp_path: Path):
    store = Store(tmp_path / "live.sqlite")
    listings = [
        Listing(
            id=2000 + i,
            name=f"Pronájem bytu {i}",
            price_czk=12000 + i * 1500,
            price_label=f"{12000 + i * 1500} Kč/měsíc",
            disposition="2+kk" if i % 2 == 0 else "1+kk",
            area_m2=70 - i * 4,
            locality="Praha 3 – Žižkov",
            url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{2000 + i}",
            image_url=f"https://img.example/{i}.jpg",
        )
        for i in range(8)
    ]
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    pool = refresh_pool_now(store)
    live = [item for item in pool if not str(item["id"]).startswith("seed-")]
    assert len(live) >= 6
    assert live_pairable_pool(pool) is not None
    pair = higher_lower_pair(store)
    assert pair["seeded"] is False
    assert pair["left"]["id"] != pair["right"]["id"]
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"] == "praha-3-zizkov"
    assert not str(pair["left"]["id"]).startswith("seed-")
    assert not str(pair["right"]["id"]).startswith("seed-")
    round_payload = rent_round(store)
    assert round_payload["seeded"] is False
    assert all(not str(item["id"]).startswith("seed-") for item in round_payload["items"])


def test_live_pool_does_not_pair_vinohrady_with_bedihost(tmp_path: Path):
    store = Store(tmp_path / "mixed.sqlite")
    listings = []
    for i, loc in enumerate(["Praha 2 – Vinohrady"] * 4 + ["Bedihošť"] * 4):
        listings.append(
            Listing(
                id=3000 + i,
                name=f"Pronájem bytu {i}",
                price_czk=11000 + i * 1800,
                price_label=f"{11000 + i * 1800} Kč/měsíc",
                disposition="3+kk" if i % 2 == 0 else "1+kk",
                area_m2=40 + i * 6,
                locality=loc,
                url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{3000 + i}",
                image_url=f"https://img.example/mix-{i}.jpg",
            )
        )
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    refresh_pool_now(store)
    for i in range(40):
        pair = higher_lower_pair(store, rng=random.Random(i))
        assert pair["left"]["locality_key"] == pair["right"]["locality_key"]
        assert pair["locality_key"] in {"praha-2-vinohrady", "bedihost"}


def test_game_listing_pool_stays_fast_on_fat_catalog(tmp_path: Path):
    store = Store(tmp_path / "fat.sqlite")
    blob = json.dumps({"offer": "Prodej", "blob": "x" * 4000})
    now = "2026-09-16T12:00:00+00:00"
    n = 8000
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO catalog_listings(
                listing_key, id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, extras, last_seen, gone, portal
            ) VALUES (?, ?, ?, ?, ?, '2+kk', 50, 'Praha', ?, ?, ?, ?, ?, 0, 'sreality')
            """,
            [
                (
                    f"sale-{i}",
                    i,
                    f"Prodej bytu {i}",
                    25000 + (i % 80),
                    f"{25000 + (i % 80)} Kč",
                    f"https://www.sreality.cz/detail/prodej/byt/2+kk/praha/{i}",
                    "" if i % 17 else f"https://img/{i}.jpg",
                    now,
                    blob,
                    now,
                )
                for i in range(n)
            ],
        )
        conn.commit()
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(catalog_listings)")}
    assert "idx_catalog_gone_last_seen" in indexes

    t0 = time.perf_counter()
    rows = store.game_listing_pool(limit=240, budget_sec=0.2)
    pool_ms = (time.perf_counter() - t0) * 1000
    assert pool_ms < 200, f"game_listing_pool {pool_ms:.1f}ms on {n} rows"
    assert rows == []

    t0 = time.perf_counter()
    pair = higher_lower_pair(store)
    assert (time.perf_counter() - t0) * 1000 < 50
    assert pair["seeded"] is True
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"]


def test_hry_html_is_memory_fast_and_nonblocking():
    site_html.cache_clear()
    site_body.cache_clear()
    t0 = time.perf_counter()
    html = site_html("hry-vyssi-nizsi.html")
    cold_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    again = site_html("hry-vyssi-nizsi.html")
    warm_ms = (time.perf_counter() - t0) * 1000
    assert "KTERÝ BYT JE LEVNĚJŠÍ" in html
    assert html == again
    assert "games.css" in html
    assert "site.css" not in html
    assert "fonts.googleapis" not in html
    assert "fonts.gstatic" not in html
    assert "archivo-black-latin.woff2" in html
    assert 'rel="preload"' in html
    assert ".png" not in html
    assert cold_ms < 80, f"cold HTML read {cold_ms:.1f}ms"
    assert warm_ms < 5, f"cached HTML {warm_ms:.1f}ms"
    response = site_page("hry.html")
    assert response.headers["cache-control"].startswith("public")
    hub = site_html("hry.html")
    rent = site_html("hry-najem.html")
    assert "HIGHER / LOWER" in hub
    assert "KOLIK STOJÍ MĚSÍC" in rent
    assert "games.css" in hub and "games.css" in rent
    assert "rent-board" in rent
    assert 'id="rent-board"' in rent
    assert "site.css" not in rent
    assert "fonts.googleapis" not in hub and "fonts.googleapis" not in rent
    assert "archivo-black-latin.woff2" in hub
    assert "is-skeleton" in rent
    assert "ccy-pill" in rent
    assert "Takové nabídky mizí" in rent
    assert "vidíte v administraci" not in hub
    assert "dobré ceny mizí" in hub
    css = (Path(__file__).resolve().parents[1] / "web" / "site" / "games.css").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "web" / "site" / "games.js").read_text(encoding="utf-8")
    assert "#163300" in css
    assert "#9fe870" in css
    assert "text-transform: uppercase" in css
    assert "font-weight: 900" in css
    assert ".converter" in css
    assert ".ccy-pill" in css
    assert ".info-row" in css
    assert "@font-face" in css
    assert "font-display: optional" in css
    assert "fonts.googleapis" not in css
    assert "system-ui" in css
    assert "ccy-pill" in js
    assert "info-rows" in js
    assert "pill-ghost" in js
    assert "rent-unit" in js
    assert "replace(/[^\\d]/g, \"\")" in js
    assert "Takové nabídky mizí" in js
    assert "vanishText(item.vanish_hours)" in js
    assert "prettyGuess" in js
    assert "TEACHING_RATIO" not in js
    assert ".converter-actions .pill" in css


def test_admin_games_leaderboard_uses_five_column_wise_grid():
    root = Path(__file__).resolve().parents[1]
    css = (root / "web" / "admin" / "admin.css").read_text(encoding="utf-8")
    js = (root / "web" / "admin" / "admin.js").read_text(encoding="utf-8")
    assert ".ad-game-tbl" in css
    assert "grid-template-columns: 40px minmax(0, 1fr) max-content" in css
    assert "ad-ccy-pill" in css
    assert "#163300" in css
    assert "#9fe870" in css
    assert "ad-game-tbl" in js
    assert "ad-ccy-pill" in js
    assert "Hráči žebříček nevidí" in js


def test_homepage_html_is_memory_fast_and_uses_webp():
    site_html.cache_clear()
    site_body.cache_clear()
    t0 = time.perf_counter()
    html = site_html("index.html")
    cold_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    again = site_html("index.html")
    warm_ms = (time.perf_counter() - t0) * 1000
    assert "NEJLEPŠÍ BYTY ZMIZÍ" in html
    assert html == again
    assert ".png" not in html
    assert "hero-apart.webp" in html
    assert "sold-1.webp" in html
    assert 'media="print"' in html
    assert "ceny v Kč" in html
    assert "dobré ceny mizí" in html
    assert "admin žebříčku" not in html
    assert cold_ms < 80, f"cold homepage HTML {cold_ms:.1f}ms"
    assert warm_ms < 5, f"cached homepage HTML {warm_ms:.1f}ms"
    response = site_page("index.html")
    assert response.headers["cache-control"].startswith("public")


def test_public_game_helpers_are_memory_only():
    class Boom:
        def game_listing_pool(self, **kwargs):
            raise AssertionError("game JSON must not scan catalog on the request path")

        def connect(self, *args, **kwargs):
            raise AssertionError("game JSON must not open SQLite on the request path")

    store = Boom()
    t0 = time.perf_counter()
    pair = public_higher_lower(store)
    round_payload = public_rent_round(store)
    scored = public_rent_score(
        store,
        {"name": "Eva", "guesses": [{"id": "seed-zizkov-2kk", "guess": 16500}]},
        allow_db=False,
    )
    ms = (time.perf_counter() - t0) * 1000
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"]
    assert len(round_payload["items"]) == 5
    assert all(item.get("vanish_hours") is not None for item in round_payload["items"])
    assert scored["ok"] is True
    assert scored["score"] == 1000
    assert ms < 40, f"public game helpers {ms:.1f}ms"
    remote = public_card(
        {
            "id": "remote-1",
            "locality": "Praha 3 – Žižkov",
            "disposition": "2+kk",
            "area_m2": 50,
            "image_url": "https://img.sreality.cz/huge.jpg",
            "portal": "sreality",
        }
    )
    assert remote["image_url"].startswith("/static/site/assets/")
    assert remote["image_url"].endswith(".webp")


def test_live_same_locality_beats_mixed_seed():
    pool = [
        _flat("live-zizkov-a", "Praha 3 – Žižkov", "3+kk", 72, 17800),
        _flat("live-zizkov-b", "Praha 3 – Žižkov", "1+kk", 31, 21400),
        _flat("seed-zizkov-2kk", "Praha 3 – Žižkov", "2+kk", 54, 16500),
        _flat("seed-zizkov-1kk", "Praha 3 – Žižkov", "1+kk", 38, 18900),
    ]
    preferred = preferred_game_pool(pool)
    assert {item["id"] for item in preferred} == {"live-zizkov-a", "live-zizkov-b"}
    for seed in range(80):
        pair = pick_same_locality_pair(pool, rng=random.Random(seed))
        assert pair["seeded"] is False
        assert pair["left"]["id"].startswith("live-")
        assert pair["right"]["id"].startswith("live-")
        assert pair["left"]["locality_key"] == pair["right"]["locality_key"] == "praha-3-zizkov"


def test_two_live_listings_are_enough_to_skip_seed(tmp_path: Path):
    store = Store(tmp_path / "two-live.sqlite")
    listings = [
        Listing(
            id=4100 + i,
            name=f"Pronájem bytu {i}",
            price_czk=15000 + i * 2500,
            price_label=f"{15000 + i * 2500} Kč/měsíc",
            disposition="2+kk" if i == 0 else "1+kk",
            area_m2=58 - i * 16,
            locality="Praha 3 – Žižkov",
            url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{4100 + i}",
            image_url=f"https://img.example/two-{i}.jpg",
        )
        for i in range(2)
    ]
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    pool = refresh_pool_now(store)
    assert live_pairable_pool(pool) is not None
    pair = higher_lower_pair(store)
    assert pair["seeded"] is False
    assert not str(pair["left"]["id"]).startswith("seed-")
    assert not str(pair["right"]["id"]).startswith("seed-")
    round_payload = rent_round(store)
    live_ids = [item["id"] for item in round_payload["items"] if not str(item["id"]).startswith("seed-")]
    assert len(live_ids) >= 2


def test_unpaired_live_catalog_falls_back_to_seed(tmp_path: Path):
    store = Store(tmp_path / "unpaired.sqlite")
    listings = [
        Listing(
            id=4200 + i,
            name=f"Pronájem bytu {i}",
            price_czk=14000 + i * 3000,
            price_label=f"{14000 + i * 3000} Kč/měsíc",
            disposition="2+kk",
            area_m2=50,
            locality=loc,
            url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{4200 + i}",
            image_url=f"https://img.example/city-{i}.jpg",
        )
        for i, loc in enumerate(["Praha 1", "Brno – střed", "Ostrava – Poruba"])
    ]
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    pool = refresh_pool_now(store)
    assert live_pairable_pool(pool) is None
    assert all(str(item["id"]).startswith("seed-") for item in pool)
    pair = higher_lower_pair(store)
    assert pair["seeded"] is True
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"]
    round_payload = rent_round(store)
    assert round_payload["seeded"] is True


def test_preferred_game_pool_is_memory_fast():
    live = [
        _flat(f"live-{i}", "Praha 3 – Žižkov", "2+kk" if i % 2 == 0 else "1+kk", 40 + i, 16000 + i * 400)
        for i in range(48)
    ]
    t0 = time.perf_counter()
    for _ in range(200):
        preferred_game_pool(live)
        preferred_game_pool([])
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 40, f"preferred_game_pool loop {ms:.1f}ms"
