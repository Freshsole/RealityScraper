import json
import random
import time
from pathlib import Path

import pytest

from app.games import (
    DEFAULT_VANISH_HOURS,
    TEACHING_RATIO,
    TYPICAL_VANISH_LABEL,
    disposition_rank,
    filter_game_estate,
    game_vanish_hours,
    higher_lower_pair,
    is_teaching_pair,
    item_estate_kind,
    leaderboard,
    live_pairable_pool,
    locality_key,
    pair_locality_key,
    pick_rent_round,
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
    teaching_contrast,
    wait_refresh,
)
from app.sreality import Listing
from app.site_pages import site_body, site_html, site_page, web_body, web_page
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
        "name": extra.pop("name", None) or f"{disposition} {locality}",
        "locality": locality,
        "disposition": disposition,
        "area_m2": area,
        "price_czk": price,
        "image_url": "/static/site/assets/sold-1.webp",
        "portal": "sreality",
        "vanish_hours": 2.0,
        "locality_key": locality_key(locality),
        "url": extra.pop("url", None) or f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{key}",
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
    assert teaching_contrast(better, worse) > 0
    normal_cheap = _flat("small", "Praha 2 – Vinohrady", "1+kk", 32, 14200)
    normal_big = _flat("big", "Praha 2 – Vinohrady", "3+kk", 82, 21900)
    assert is_teaching_pair(normal_cheap, normal_big) is False
    missing_area = _flat("gap", "Praha 2 – Vinohrady", "2+kk", None, 31000)
    same_disp = _flat("ok", "Praha 2 – Vinohrady", "2+kk", 48, 19600)
    assert is_teaching_pair(missing_area, same_disp) is False
    tiny = _flat("tiny-a", "Praha 2 – Vinohrady", "2+kk", 48, 19600)
    tiny_dear = _flat("tiny-b", "Praha 2 – Vinohrady", "2+kk", 49, 19700)
    assert is_teaching_pair(tiny, tiny_dear) is False
    # Tiny 3+kk cheaper than a larger 2+kk is not a better Kč/m² deal.
    fake_better = _flat("tiny-3kk", "Praha 2 – Vinohrady", "3+kk", 38, 18000)
    larger_dear = _flat("big-2kk", "Praha 2 – Vinohrady", "2+kk", 70, 22000)
    assert is_teaching_pair(fake_better, larger_dear) is False
    assert teaching_contrast(fake_better, larger_dear) == 0


def test_teaching_contrast_ranks_price_m2_area_and_disposition():
    clear_good = _flat("clear-good", "Praha 3 – Žižkov", "4+kk", 92, 17500)
    clear_bad = _flat("clear-bad", "Praha 3 – Žižkov", "1+kk", 28, 24800)
    weak_a = _flat("weak-a", "Praha 3 – Žižkov", "2+kk", 50, 19600)
    weak_b = _flat("weak-b", "Praha 3 – Žižkov", "2+kk", 42, 20800)
    clear = teaching_contrast(clear_good, clear_bad)
    weak = teaching_contrast(weak_a, weak_b)
    assert is_teaching_pair(clear_good, clear_bad)
    assert is_teaching_pair(weak_a, weak_b)
    assert clear > weak * 2
    assert clear > 2.0


def test_pair_locality_key_aliases_prague_district():
    assert pair_locality_key("Praha 3 – Žižkov") == "praha-3"
    assert pair_locality_key("Praha 3") == "praha-3"
    assert pair_locality_key("Žižkov, Praha 3") == "praha-3"
    assert pair_locality_key("Praha 2 – Vinohrady") == "praha-2"
    assert pair_locality_key("Vinohrady, Praha 2") == "praha-2"
    assert pair_locality_key("Praha 2 – Vinohrady") != pair_locality_key("Praha 3 – Žižkov")
    assert pair_locality_key("Brno – střed") == "brno-stred"
    assert pair_locality_key("Bedihošť") == "bedihost"


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


def test_teaching_pick_prefers_clear_price_m2_contrast():
    pool = [
        _flat("clear-good", "Praha 3 – Žižkov", "4+kk", 92, 17500),
        _flat("clear-bad", "Praha 3 – Žižkov", "1+kk", 28, 24800),
        _flat("weak-a", "Praha 3 – Žižkov", "2+kk", 50, 19600),
        _flat("weak-b", "Praha 3 – Žižkov", "2+kk", 42, 20800),
        _flat("noise", "Praha 3 – Žižkov", "2+kk", 48, 19700),
    ]
    weak = teaching_contrast(
        next(item for item in pool if item["id"] == "weak-a"),
        next(item for item in pool if item["id"] == "weak-b"),
    )
    clear_ids = frozenset({"clear-good", "clear-bad"})
    picked = []
    for seed in range(80):
        pair = pick_same_locality_pair(pool, rng=random.Random(seed), teaching_ratio=1.0)
        ids = frozenset({pair["left"]["id"], pair["right"]["id"]})
        picked.append(ids)
        assert pair["pair_kind"] == "teaching"
        assert is_teaching_pair(pair["left"], pair["right"])
        assert teaching_contrast(pair["left"], pair["right"]) >= weak
        assert "v řádu minut" not in (pair.get("copy") or "")
        assert "v řádu minut" not in (pair.get("copy_ok") or "")
    assert picked.count(clear_ids) >= 50


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
    assert len({item.get("pair_key") for item in round_payload["items"]}) == 1


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
    assert len({item.get("pair_key") for item in round_payload["items"]}) == 1
    assert round_payload["pair_key"] == "praha-3"


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
    assert "Takové nabídky mizí" in html
    assert "STEJNÁ LOKALITA" in rent
    assert "v řádu hodin" in rent
    assert "v řádu hodin" in hub
    assert "v řádu minut" not in rent
    assert "v řádu minut" not in hub
    assert "v řádu minut" not in html
    assert "V ŘÁDU HODIN" in hub
    assert "games.css?v=15" in hub
    assert "board-tease" in rent
    assert "jen admin" in rent
    assert "Ve hře skóre uvidíte jen vy" in rent
    assert "rent-hint" in rent
    assert "mezery doplníme" in rent
    assert "18 000" in rent
    assert "games.js?v=13" in rent
    assert "games.css?v=15" in rent
    assert "games.js?v=13" in html
    assert "games.css?v=15" in html
    assert "vidíte v administraci" not in hub
    assert "Výhodné kousky" in hub
    css = (Path(__file__).resolve().parents[1] / "web" / "site" / "games.css").read_text(encoding="utf-8")
    js = (Path(__file__).resolve().parents[1] / "web" / "site" / "games.js").read_text(encoding="utf-8")
    assert "#163300" in css
    assert "#9fe870" in css
    assert "text-transform: uppercase" in css
    assert "font-weight: 900" in css
    assert ".converter" in css
    assert ".ccy-pill" in css
    assert ".info-row" in css
    assert ".board-tease" in css
    assert ".board-tease-head" in css
    assert "@font-face" in css
    assert "font-display: optional" in css
    assert "fonts.googleapis" not in css
    assert "system-ui" in css
    assert ".game-hub" in css
    assert "background: var(--surface)" in css
    assert ".game-card .pill" in css
    assert "ccy-pill" in js
    assert "info-rows" in js
    assert "pill-ghost" in js
    assert "rent-unit" in js
    assert "rent-hint" in js
    assert "replace(/[^\\d]/g, \"\")" in js
    assert "groupDigits" in js
    assert "formatGuessInput" in js
    assert "caretAfterDigits" in js
    assert "mezery doplníme" in js
    assert "Kolik stojí měsíc v Kč?" in js
    assert "v řádu minut" not in js
    assert "Takové nabídky mizí" in js
    assert "vanishText(item.vanish_hours, item.vanish_label" in js
    assert "TYPICAL_VANISH" in js
    assert "v řádu hodin" in js
    assert "boardTease" in js
    assert "board-tease" in js
    assert "jen admin" in js
    assert "roundMeta.copy" in js
    assert "prettyGuess" in js
    assert "TEACHING_RATIO" not in js
    assert 'width="390"' in js
    assert 'width="504"' not in js
    assert 'size="8"' in js
    assert ".rent-hint" in css
    assert ".converter-actions .pill" in css
    assert ".converter-actions .pill-ghost" in css
    assert "@media (max-width: 480px)" in css
    phone = css.split("@media (max-width: 480px)", 1)[1]
    assert "--pad: 16px" in phone
    assert "grid-column: 1 / -1" in phone
    assert "font-size: 40px" in phone
    assert "min-height: 48px" in phone
    assert ".info-row" in phone
    assert "flex-wrap: wrap" in phone
    assert ".board-tease-head" in phone
    assert ".name-field" in phone
    assert "fonts.googleapis" not in phone
    tease = css.split(".board-tease {", 1)[1].split(".loc-chip {", 1)[0]
    assert "font-weight: 900" not in tease
    assert "fonts.googleapis" not in tease
    ccy = css.split(".ccy-pill {", 1)[1].split("}", 1)[0]
    assert "flex-shrink: 0" in ccy
    assert "max-width: 100%" in css.split(".converter {", 1)[1].split("}", 1)[0]
    assert "max-width: 100%" in css.split(".rent-hero-img {", 1)[1].split("}", 1)[0]
    assert "width: 0" in css.split(".rent-amount input {", 1)[1].split("}", 1)[0]
    assert "padding: 24px 16px" in phone
    assert "white-space: normal" in phone
    assert ".game-card" in phone
    assert "max-width: 100%" in phone
    assert "overflow-wrap: anywhere" in css
    assert "overflow-wrap: anywhere" in phone
    assert "padding: 16px var(--gutter) 40px" in phone
    assert "justify-content: space-between" in css.split(".game-card {", 1)[1].split("}", 1)[0]
    assert "max-width: 36rem" in css.split(".game-hero .lead {", 1)[1].split("}", 1)[0]


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
    assert "fonts.googleapis" not in html
    assert "fonts.gstatic" not in html
    assert "archivo-black-latin.woff2" in html
    assert "ccy-pill" in html
    assert "mint-banner" in html
    assert "Otevřít nabídku" in html
    assert "ceny v Kč" in html
    assert "dobré ceny mizí" in html
    assert "admin žebříčku" not in html
    assert "data-node-id" not in html
    css = (Path(__file__).resolve().parents[1] / "web" / "site" / "site.css").read_text(encoding="utf-8")
    assert "@font-face" in css
    assert "font-display: optional" in css
    assert "#163300" in css
    assert "#9fe870" in css
    assert "font-weight: 900" in css
    assert "text-transform: uppercase" in css
    assert ".ccy-pill" in css
    assert ".mint-banner" in css
    assert "fonts.googleapis" not in css
    assert "--font-ui: system-ui" in css
    assert "site.css?v=12" in html
    ccy = css.split(".ccy-pill {", 1)[1].split("}", 1)[0]
    assert "flex-shrink: 0" in ccy
    assert "@media (max-width: 480px)" in css
    phone = css.split("@media (max-width: 480px)", 1)[1]
    assert "--pad: 16px" in phone
    assert "min-height: 48px" in phone
    assert "max-width: 100%" in phone
    assert ".alert-card" in phone
    assert ".fomo" in phone
    assert "white-space: normal" in phone
    assert "fonts.googleapis" not in phone
    assert cold_ms < 80, f"cold homepage HTML {cold_ms:.1f}ms"
    assert warm_ms < 5, f"cached homepage HTML {warm_ms:.1f}ms"
    response = site_page("index.html")
    assert response.headers["cache-control"].startswith("public")


def test_auth_and_marketing_html_is_self_hosted_wise():
    site_html.cache_clear()
    site_body.cache_clear()
    pages = {
        "prihlaseni.html": "PŘIHLÁŠENÍ",
        "registrace.html": "VYTVOŘTE SI ÚČET",
        "heslo.html": "OBNOVTE SI HESLO",
        "kontakt.html": "OZVĚTE SE NÁM",
        "uspechy.html": "NAŠLI SI VYSNĚNÉ BYDLENÍ",
        "clanek.html": "Vyzkoušet zdarma",
        "obchodni-podminky.html": "OBCHODNÍ PODMÍNKY",
        "ochrana-soukromi.html": "OCHRANA SOUKROMÍ",
        "nastaveni-cookies.html": "NASTAVENÍ COOKIES",
        "byt.html": "Začít hlídat zdarma",
    }
    for name, needle in pages.items():
        html = site_html(name)
        assert needle in html, name
        assert "fonts.googleapis" not in html, name
        assert "fonts.gstatic" not in html, name
        assert "archivo-black-latin.woff2" in html, name
        assert 'rel="preload"' in html, name
        assert "/static/site/site.css" in html, name
    login = site_html("prihlaseni.html")
    register = site_html("registrace.html")
    forgot = site_html("heslo.html")
    assert "/static/site/auth.css" in login and "/static/site/auth.js" in login
    assert "/api/auth/login" not in login
    assert 'id="login-form"' in login
    assert 'id="register-flow"' in register
    assert 'id="forgot-form"' in forgot
    css = (Path(__file__).resolve().parents[1] / "web" / "site" / "auth.css").read_text(encoding="utf-8")
    assert "fonts.googleapis" not in css
    assert "text-transform: uppercase" in css
    assert "var(--forest)" in css
    assert "var(--green)" in css
    assert "var(--green-hover)" in css
    assert "font-weight: 900" in css
    assert "var(--pale)" in css
    assert "var(--radius-pill)" in css
    assert ".auth-card[hidden]" in css
    assert "auth.css?v=14" in login
    assert "@media (max-width: 480px)" in css
    phone = css.split("@media (max-width: 480px)", 1)[1]
    assert "padding: 24px 16px 40px" in phone
    assert "min-height: 48px" in phone
    assert "max-width: 100%" in phone


def test_dashboard_and_admin_shells_are_self_hosted_wise():
    web_body.cache_clear()
    root = Path(__file__).resolve().parents[1]
    shell = web_body("index.html").decode("utf-8")
    admin = web_body("admin/index.html").decode("utf-8")
    leftover = (root / "byt.html").read_text(encoding="utf-8")
    styles = (root / "web" / "styles.css").read_text(encoding="utf-8")
    admin_css = (root / "web" / "admin" / "admin.css").read_text(encoding="utf-8")
    for name, html in (
        ("index.html", shell),
        ("admin/index.html", admin),
        ("byt.html", leftover),
    ):
        assert "fonts.googleapis" not in html, name
        assert "fonts.gstatic" not in html, name
        assert "archivo-black-latin.woff2" in html, name
        assert 'rel="preload"' in html, name
    assert 'id="view-overview"' in shell
    assert "ADMIN PŘIHLÁŠENÍ" in admin
    assert "styles.css?v=1.2.0-n81" in shell
    assert "admin.css?v=44" in admin
    for name, css in (("styles.css", styles), ("admin.css", admin_css)):
        assert "@font-face" in css, name
        assert "font-display: optional" in css, name
        assert "fonts.googleapis" not in css, name
        assert "--font-ui: system-ui" in css, name
        assert "archivo-black-latin.woff2" in css, name
        assert "Inter" not in css, name
    response = web_page("index.html")
    assert response.body == web_body("index.html")
    assert response.headers["cache-control"] == "no-store, max-age=0"


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
    assert all(item.get("vanish_label") for item in round_payload["items"])
    assert "v řádu minut" not in (round_payload.get("vanish_label") or "")
    assert len({item.get("pair_key") for item in round_payload["items"]}) == 1
    assert round_payload.get("pair_key")
    assert "v řádu minut" not in (round_payload.get("copy") or "")
    assert "mezery doplníme" in (round_payload.get("copy") or "")
    assert "18 000" in (round_payload.get("copy_hint") or "")
    assert "v řádu minut" not in (pair.get("copy") or "")
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
    assert len({item.get("pair_key") for item in round_payload["items"]}) == 1
    assert round_payload["pair_key"] == "praha-3"


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
    assert len({item.get("pair_key") for item in round_payload["items"]}) == 1
    assert round_payload["pair_key"] in {"praha-3", "praha-2", "brno-stred"}


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


def _noisy_live_pool():
    return [
        _flat("live-z1", "Praha 3 – Žižkov", "3+kk", 76, 17800, portal="ulovdomov"),
        _flat("live-z2", "Praha 3", "1+kk", 30, 22900, portal="annonce"),
        _flat("live-z3", "Žižkov, Praha 3", "2+kk", 48, 19600, portal="idnes"),
        _flat("live-z4", "Praha 3 – Žižkov", "2+kk", 49, 19700, portal="sreality"),
        _flat("live-z-miss", "Praha 3 – Žižkov", "2+kk", None, 31000, portal="bezrealitky"),
        _flat("live-v1", "Praha 2 – Vinohrady", "3+kk", 80, 21900, portal="ulovdomov"),
        _flat("live-v2", "Vinohrady, Praha 2", "1+kk", 31, 26800, portal="idnes"),
        _flat("live-b1", "Bedihošť", "1+kk", 28, 9000, portal="annonce"),
        _flat("live-b2", "Bedihošť", "3+kk", 70, 16000, portal="annonce"),
        _flat("live-praha", "Praha", "2+kk", 50, 20000, portal="sreality"),
        _flat("live-brno", "Brno – střed", "2+kk", 50, 15000, portal="ulovdomov"),
    ]


def test_live_noisy_pool_keeps_teaching_rate_and_same_place():
    pool = _noisy_live_pool()
    n = 400
    rng = random.Random(11)
    kinds = []
    for _ in range(n):
        pair = pick_same_locality_pair(pool, rng=rng, teaching_ratio=TEACHING_RATIO)
        left_pair = pair["left"].get("pair_key") or pair.get("pair_key")
        right_pair = pair["right"].get("pair_key")
        assert left_pair == right_pair == pair.get("pair_key")
        ids = {pair["left"]["id"], pair["right"]["id"]}
        zizkov = {item for item in ids if item.startswith("live-z")}
        vinohrady = {item for item in ids if item.startswith("live-v")}
        assert not (zizkov and vinohrady)
        assert "live-praha" not in ids or "live-brno" not in ids
        assert pair["seeded"] is False
        if pair["pair_kind"] == "teaching":
            assert is_teaching_pair(pair["left"], pair["right"])
        kinds.append(pair["pair_kind"])
    rate = kinds.count("teaching") / n
    assert 0.72 <= rate <= 0.88, f"live-pool teaching rate {rate:.3f}"
    forced = [
        pick_same_locality_pair(pool, rng=random.Random(i), teaching_ratio=1.0)
        for i in range(80)
    ]
    assert all(row["pair_kind"] == "teaching" for row in forced)
    assert all(is_teaching_pair(row["left"], row["right"]) for row in forced)


def test_live_locality_aliases_pair_same_district():
    pool = [
        _flat("z-full", "Praha 3 – Žižkov", "3+kk", 76, 17800),
        _flat("z-district", "Praha 3", "1+kk", 30, 22900),
        _flat("v-full", "Praha 2 – Vinohrady", "3+kk", 80, 20000),
        _flat("v-alias", "Vinohrady, Praha 2", "1+kk", 32, 25000),
    ]
    for seed in range(80):
        pair = pick_same_locality_pair(pool, rng=random.Random(seed), teaching_ratio=1.0)
        assert pair["pair_key"] in {"praha-3", "praha-2"}
        assert pair["left"]["pair_key"] == pair["right"]["pair_key"]
        ids = {pair["left"]["id"], pair["right"]["id"]}
        if pair["pair_key"] == "praha-3":
            assert ids == {"z-full", "z-district"}
        else:
            assert ids == {"v-full", "v-alias"}


def test_vanish_hours_from_last_seen_is_honest():
    hours, observed = game_vanish_hours("2026-09-17T10:00:00+00:00", "2026-09-17T10:00:00+00:00")
    assert observed is False
    assert hours == DEFAULT_VANISH_HOURS
    hours, observed = game_vanish_hours("2026-09-17T07:00:00+00:00", "2026-09-17T10:00:00+00:00")
    assert observed is True
    assert hours == 3.0
    hours, observed = game_vanish_hours("2026-09-15T10:00:00+00:00", "2026-09-17T10:00:00+00:00")
    assert observed is True
    assert hours == 24.0
    observed_pair = pick_same_locality_pair(
        [
            _flat(
                "a",
                "Praha 3 – Žižkov",
                "3+kk",
                76,
                17800,
                first_seen="2026-09-17T07:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
            _flat(
                "b",
                "Praha 3 – Žižkov",
                "1+kk",
                30,
                22900,
                first_seen="2026-09-17T10:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
        ],
        rng=random.Random(1),
        teaching_ratio=1.0,
    )
    assert observed_pair["vanish_hours"] == 3.0
    assert observed_pair["vanish_label"] == "za 3,0 h"
    assert "za 3,0 h" in observed_pair["copy_ok"]
    fresh = pick_same_locality_pair(
        [
            _flat(
                "a",
                "Praha 3 – Žižkov",
                "3+kk",
                76,
                17800,
                first_seen="2026-09-17T10:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
            _flat(
                "b",
                "Praha 3 – Žižkov",
                "1+kk",
                30,
                22900,
                first_seen="2026-09-17T10:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
        ],
        rng=random.Random(1),
        teaching_ratio=1.0,
    )
    assert fresh["vanish_label"] == TYPICAL_VANISH_LABEL
    assert TYPICAL_VANISH_LABEL in fresh["copy_ok"]
    card = public_card(
        {
            "id": "live-1",
            "locality": "Praha 3 – Žižkov",
            "first_seen": "2026-09-17T10:00:00+00:00",
            "last_seen": "2026-09-17T10:00:00+00:00",
            "portal": "ulovdomov",
        }
    )
    assert card["vanish_hours"] == DEFAULT_VANISH_HOURS
    assert card["vanish_label"] == TYPICAL_VANISH_LABEL


def test_live_catalog_vanish_uses_last_seen(tmp_path: Path):
    store = Store(tmp_path / "vanish-live.sqlite")
    listings = [
        Listing(
            id=5100 + i,
            name=f"Pronájem bytu {i}",
            price_czk=16000 + i * 4000,
            price_label=f"{16000 + i * 4000} Kč/měsíc",
            disposition="3+kk" if i == 0 else "1+kk",
            area_m2=78 - i * 40,
            locality="Praha 3 – Žižkov",
            url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{5100 + i}",
            image_url=f"https://img.example/vanish-{i}.jpg",
        )
        for i in range(2)
    ]
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    first = "2026-09-17T07:00:00+00:00"
    last = "2026-09-17T10:00:00+00:00"
    with store.connect() as conn:
        conn.execute("UPDATE catalog_listings SET first_seen = ?, last_seen = ?", (first, last))
        conn.commit()
    pool = refresh_pool_now(store)
    live = [item for item in pool if not str(item["id"]).startswith("seed-")]
    assert live
    assert all(item.get("last_seen") == last for item in live)
    assert all(item.get("vanish_label") == "za 3,0 h" for item in live)
    pair = higher_lower_pair(store, rng=random.Random(3), teaching_ratio=1.0)
    assert pair["seeded"] is False
    assert pair["vanish_hours"] == 3.0
    assert pair["vanish_label"] == "za 3,0 h"
    assert "za 3,0 h" in pair["copy_ok"]
    rent_payload = rent_round(store, rng=random.Random(3), teaching_ratio=1.0)
    assert rent_payload["seeded"] is False
    assert rent_payload["vanish_hours"] == 3.0
    assert rent_payload["vanish_label"] == "za 3,0 h"
    assert "za 3,0 h" in rent_payload["copy"]
    assert len({item.get("pair_key") for item in rent_payload["items"]}) == 1


def test_rent_round_never_mixes_districts():
    pool = _noisy_live_pool()
    for seed in range(250):
        row = pick_rent_round(pool, rng=random.Random(seed))
        keys = {item.get("pair_key") for item in row["items"]}
        assert len(keys) == 1
        assert keys == {row["pair_key"]}
        ids = {item["id"] for item in row["items"]}
        zizkov = {item for item in ids if str(item).startswith("live-z") or "zizkov" in str(item)}
        vinohrady = {item for item in ids if str(item).startswith("live-v") or "vinohrady" in str(item)}
        assert not (zizkov and vinohrady)
        assert row["seeded"] is False or row["pair_key"] in {"praha-3", "praha-2", "brno-stred"}


def test_rent_teaching_distribution_is_about_80_percent():
    pool = _noisy_live_pool()
    rng = random.Random(7)
    n = 400
    kinds = [
        pick_rent_round(pool, rng=rng, teaching_ratio=TEACHING_RATIO)["round_kind"]
        for _ in range(n)
    ]
    rate = kinds.count("teaching") / n
    assert 0.72 <= rate <= 0.88, f"rent teaching rate {rate:.3f}"
    forced = [
        pick_rent_round(pool, rng=random.Random(i), teaching_ratio=1.0)
        for i in range(80)
    ]
    assert all(row["round_kind"] == "teaching" for row in forced)
    for row in forced:
        assert any(
            is_teaching_pair(left, right)
            for index, left in enumerate(row["pool"])
            for right in row["pool"][index + 1 :]
        )


def test_rent_teaching_round_keeps_clear_price_m2_contrast():
    pool = [
        _flat("clear-good", "Praha 3 – Žižkov", "4+kk", 92, 17500),
        _flat("clear-bad", "Praha 3 – Žižkov", "1+kk", 28, 24800),
        _flat("weak-a", "Praha 3 – Žižkov", "2+kk", 50, 19600),
        _flat("weak-b", "Praha 3 – Žižkov", "2+kk", 42, 20800),
        _flat("noise", "Praha 3 – Žižkov", "2+kk", 48, 19700),
        _flat("extra-a", "Praha 3 – Žižkov", "2+kk", 51, 19800),
        _flat("extra-b", "Praha 3 – Žižkov", "2+1", 44, 22100),
        _flat("extra-c", "Praha 3 – Žižkov", "1+1", 36, 23100),
    ]
    for seed in range(40):
        row = pick_rent_round(pool, rng=random.Random(seed), teaching_ratio=1.0)
        assert row["round_kind"] == "teaching"
        ids = {item["id"] for item in row["items"]}
        assert "clear-good" in ids
        assert "clear-bad" in ids
        assert "v řádu minut" not in (row.get("copy") or "")
        assert "mezery doplníme" in (row.get("copy") or "")
        assert "18 000" in (row.get("copy_hint") or "")
        assert any(
            is_teaching_pair(left, right)
            for index, left in enumerate(row["pool"])
            for right in row["pool"][index + 1 :]
        )


def test_rent_round_live_aliases_same_district():
    pool = [
        _flat("z-full", "Praha 3 – Žižkov", "3+kk", 76, 17800),
        _flat("z-district", "Praha 3", "1+kk", 30, 22900),
        _flat("z-alias", "Žižkov, Praha 3", "2+kk", 52, 19600),
        _flat("z-deal", "Praha 3 – Žižkov", "4+kk", 90, 18800),
        _flat("z-dear", "Praha 3", "2+1", 44, 25100),
        _flat("v-full", "Praha 2 – Vinohrady", "3+kk", 80, 20000),
        _flat("v-alias", "Vinohrady, Praha 2", "1+kk", 32, 25000),
    ]
    for seed in range(80):
        row = pick_rent_round(pool, rng=random.Random(seed), teaching_ratio=1.0)
        assert row["pair_key"] in {"praha-3", "praha-2"}
        assert all(item.get("pair_key") == row["pair_key"] for item in row["items"])
        live_ids = [item["id"] for item in row["items"] if not str(item["id"]).startswith("seed-")]
        assert live_ids
        if row["pair_key"] == "praha-3":
            assert all(str(item).startswith("z-") for item in live_ids)
            assert row["seeded"] is False


def test_rent_round_vanish_hours_from_last_seen_is_honest():
    observed = pick_rent_round(
        [
            _flat(
                "r-a",
                "Praha 3 – Žižkov",
                "3+kk",
                76,
                17800,
                first_seen="2026-09-17T07:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
            _flat(
                "r-b",
                "Praha 3 – Žižkov",
                "1+kk",
                30,
                22900,
                first_seen="2026-09-17T10:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
            _flat(
                "r-c",
                "Praha 3 – Žižkov",
                "2+kk",
                52,
                19600,
                first_seen="2026-09-17T07:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
            _flat(
                "r-d",
                "Praha 3 – Žižkov",
                "4+kk",
                90,
                18800,
                first_seen="2026-09-17T07:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
            _flat(
                "r-e",
                "Praha 3 – Žižkov",
                "2+1",
                44,
                25100,
                first_seen="2026-09-17T10:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            ),
        ],
        rng=random.Random(1),
        teaching_ratio=1.0,
    )
    assert observed["vanish_hours"] == 3.0
    assert observed["vanish_label"] == "za 3,0 h"
    assert "za 3,0 h" in observed["copy"]
    assert "minut" not in observed["copy"]
    assert observed["seeded"] is False
    fresh = pick_rent_round(
        [
            _flat(
                f"f-{name}",
                "Praha 3 – Žižkov",
                disp,
                area,
                price,
                first_seen="2026-09-17T10:00:00+00:00",
                last_seen="2026-09-17T10:00:00+00:00",
            )
            for name, disp, area, price in (
                ("a", "3+kk", 76, 17800),
                ("b", "1+kk", 30, 22900),
                ("c", "2+kk", 52, 19600),
                ("d", "4+kk", 90, 18800),
                ("e", "2+1", 44, 25100),
            )
        ],
        rng=random.Random(1),
        teaching_ratio=1.0,
    )
    assert fresh["vanish_label"] == TYPICAL_VANISH_LABEL
    assert TYPICAL_VANISH_LABEL in fresh["copy"]
    assert "minut" not in fresh["copy"]
    assert all(item.get("vanish_label") == TYPICAL_VANISH_LABEL for item in fresh["items"])


def test_rent_cold_seed_stays_same_district():
    row = pick_rent_round([], rng=random.Random(0))
    assert row["seeded"] is True
    assert len(row["items"]) == 5
    assert len({item.get("pair_key") for item in row["items"]}) == 1
    assert row["pair_key"] in {"praha-3", "praha-2", "brno-stred"}
    assert "v řádu minut" not in (row.get("copy") or "")


def test_pick_rent_round_is_memory_fast():
    live = [
        _flat(f"live-{i}", "Praha 3 – Žižkov", "2+kk" if i % 2 == 0 else "1+kk", 40 + i, 16000 + i * 400)
        for i in range(48)
    ]
    t0 = time.perf_counter()
    for index in range(20):
        pick_rent_round(live, rng=random.Random(index))
        pick_rent_round([], rng=random.Random(index))
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 80, f"pick_rent_round loop {ms:.1f}ms"


def _house(key, locality, disposition, area, price, **extra):
    extra.setdefault("name", f"Pronájem domu {disposition}, {locality}")
    extra.setdefault("url", f"https://www.sreality.cz/detail/pronajem/dum/{disposition}/praha/{key}")
    extra.setdefault("extras", {"estate": "Dům", "offer": "Pronájem"})
    return _flat(key, locality, disposition, area, price, **extra)


def _plot(key, locality, area, price, **extra):
    extra.setdefault("name", f"Pronájem pozemku {area} m², {locality}")
    extra.setdefault("url", f"https://www.sreality.cz/detail/pronajem/pozemek/zahrada/praha/{key}")
    extra.setdefault("extras", {"estate": "Pozemek", "offer": "Pronájem"})
    extra.setdefault("disposition", "")
    return _flat(key, locality, extra.pop("disposition"), area, price, **extra)


def _polluted_live_pool():
    return [
        *_noisy_live_pool(),
        _house("house-z", "Praha 3 – Žižkov", "5+1", 180, 12000),
        _plot("plot-z", "Praha 3 – Žižkov", 820, 8000),
        _house("house-v", "Praha 2 – Vinohrady", "6+1", 210, 14500),
        _plot("plot-v", "Praha 2 – Vinohrady", 640, 9500),
        _house("house-b", "Bedihošť", "4+1", 150, 11000),
        _plot("plot-b", "Bedihošť", 900, 7000),
    ]


def test_games_prefer_flats_when_catalog_has_houses_and_land():
    pool = _polluted_live_pool()
    preferred = preferred_game_pool(pool)
    ids = {item["id"] for item in preferred}
    assert "house-z" not in ids
    assert "plot-z" not in ids
    assert all(item_estate_kind(item) == "byt" for item in preferred)
    filtered = filter_game_estate(pool)
    assert {item["id"] for item in filtered} == {item["id"] for item in _noisy_live_pool()}
    mixed = preferred_game_pool(pool, estate="mixed")
    mixed_ids = {item["id"] for item in mixed}
    assert "house-z" in mixed_ids
    assert "plot-z" not in mixed_ids
    assert all(item_estate_kind(item) != "pozemek" for item in mixed)


def test_live_teaching_rate_holds_when_land_and_houses_pollute_catalog():
    pool = _polluted_live_pool()
    n = 400
    rng = random.Random(11)
    kinds = []
    for _ in range(n):
        pair = pick_same_locality_pair(pool, rng=rng, teaching_ratio=TEACHING_RATIO)
        ids = {pair["left"]["id"], pair["right"]["id"]}
        assert not any(str(item).startswith("house-") or str(item).startswith("plot-") for item in ids)
        assert item_estate_kind(pair["left"]) == item_estate_kind(pair["right"]) == "byt"
        if pair["pair_kind"] == "teaching":
            assert is_teaching_pair(pair["left"], pair["right"])
        kinds.append(pair["pair_kind"])
    rate = kinds.count("teaching") / n
    assert 0.72 <= rate <= 0.88, f"polluted-pool teaching rate {rate:.3f}"
    rent_kinds = []
    rng = random.Random(7)
    for _ in range(n):
        row = pick_rent_round(pool, rng=rng, teaching_ratio=TEACHING_RATIO)
        for item in row["items"]:
            assert not str(item["id"]).startswith("house-")
            assert not str(item["id"]).startswith("plot-")
        rent_kinds.append(row["round_kind"])
    rent_rate = rent_kinds.count("teaching") / n
    assert 0.72 <= rent_rate <= 0.88, f"polluted rent teaching rate {rent_rate:.3f}"


def test_mixed_estate_keeps_houses_but_still_drops_plots():
    pool = _polluted_live_pool()
    seen_house = False
    for seed in range(120):
        pair = pick_same_locality_pair(pool, rng=random.Random(seed), estate="mixed")
        ids = {pair["left"]["id"], pair["right"]["id"]}
        assert not any(str(item).startswith("plot-") for item in ids)
        if any(str(item).startswith("house-") for item in ids):
            seen_house = True
    assert seen_house
    for seed in range(80):
        row = pick_rent_round(pool, rng=random.Random(seed), estate="mixed")
        ids = {item["id"] for item in row["items"]}
        assert not any(str(item).startswith("plot-") for item in ids)


def test_game_listing_pool_skips_houses_and_land(tmp_path: Path):
    store = Store(tmp_path / "estate-pool.sqlite")
    listings = []
    for i in range(4):
        listings.append(
            Listing(
                id=7000 + i,
                name=f"Pronájem bytu {i}",
                price_czk=16000 + i * 1500,
                price_label=f"{16000 + i * 1500} Kč/měsíc",
                disposition="2+kk" if i % 2 == 0 else "1+kk",
                area_m2=58 - i * 4,
                locality="Praha 3 – Žižkov",
                url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{7000 + i}",
                image_url=f"https://img.example/flat-{i}.jpg",
                extras={"estate": "Byt", "offer": "Pronájem"},
            )
        )
        listings.append(
            Listing(
                id=7100 + i,
                name=f"Pronájem domu {i}",
                price_czk=18000 + i * 1200,
                price_label=f"{18000 + i * 1200} Kč/měsíc",
                disposition="5+1",
                area_m2=160 + i * 10,
                locality="Praha 3 – Žižkov",
                url=f"https://www.sreality.cz/detail/pronajem/dum/5+1/praha/{7100 + i}",
                image_url=f"https://img.example/house-{i}.jpg",
                extras={"estate": "Dům", "offer": "Pronájem"},
            )
        )
        listings.append(
            Listing(
                id=7200 + i,
                name=f"Pronájem pozemku {i}",
                price_czk=9000 + i * 500,
                price_label=f"{9000 + i * 500} Kč/měsíc",
                disposition="",
                area_m2=700 + i * 40,
                locality="Praha 3 – Žižkov",
                url=f"https://www.sreality.cz/detail/pronajem/pozemek/zahrada/praha/{7200 + i}",
                image_url=f"https://img.example/plot-{i}.jpg",
                extras={"estate": "Pozemek", "offer": "Pronájem"},
            )
        )
    store.upsert_catalog_listings_batch(listings, kind="seeded")
    rows = store.game_listing_pool(limit=240, budget_sec=0.2)
    assert len(rows) == 4
    assert all("/byt/" in str(item.get("url") or "") for item in rows)
    assert all("pozemek" not in str(item.get("name") or "").casefold() for item in rows)
    assert all("domu" not in str(item.get("name") or "").casefold() for item in rows)
    pool = refresh_pool_now(store)
    live = [item for item in pool if not str(item["id"]).startswith("seed-")]
    assert len(live) == 4
    pair = higher_lower_pair(store, rng=random.Random(3), teaching_ratio=1.0)
    assert pair["seeded"] is False
    assert "/byt/" in str(pair["left"].get("id") or pair["left"].get("url") or "") or "byt" in str(
        pair["left"].get("name") or ""
    ).casefold()
    for side in (pair["left"], pair["right"]):
        assert "pozemek" not in str(side.get("name") or "").casefold()
        assert "domu" not in str(side.get("name") or "").casefold()
