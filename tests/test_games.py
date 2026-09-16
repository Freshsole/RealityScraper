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
    locality_key,
    pick_same_locality_pair,
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
    pair = higher_lower_pair(store)
    assert pair["seeded"] is False
    assert pair["left"]["id"] != pair["right"]["id"]
    assert pair["left"]["locality_key"] == pair["right"]["locality_key"] == "praha-3-zizkov"


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
    assert "TEACHING_RATIO" not in js


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
