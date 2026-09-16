import json
import time
from pathlib import Path

import pytest

from app.games import (
    higher_lower_pair,
    leaderboard,
    refresh_pool_now,
    rent_round,
    reset_pool_cache,
    save_rent_round,
    score_guess,
    score_round,
    wait_refresh,
)
from app.sreality import Listing
from app.store import Store


@pytest.fixture(autouse=True)
def _reset_game_pool_cache():
    reset_pool_cache()
    yield
    wait_refresh(0.6)
    reset_pool_cache()


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


def test_higher_lower_seed_pair(tmp_path: Path):
    store = Store(tmp_path / "empty.sqlite")
    pair = higher_lower_pair(store)
    assert pair["left"]["id"] != pair["right"]["id"]
    assert pair["cheaper"] in {"left", "right"}
    cheaper = pair["left"] if pair["cheaper"] == "left" else pair["right"]
    other = pair["right"] if pair["cheaper"] == "left" else pair["left"]
    assert cheaper["price_czk"] <= other["price_czk"]
    assert pair["seeded"] is True
    assert {"cheaper", "copy", "left", "right", "seeded", "vanish_hours"} <= set(pair)


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
    assert len(round_payload["items"]) == 5


def test_refresh_uses_live_catalog_rentals(tmp_path: Path):
    store = Store(tmp_path / "live.sqlite")
    listings = [
        Listing(
            id=2000 + i,
            name=f"Pronájem bytu {i}",
            price_czk=12000 + i * 1500,
            price_label=f"{12000 + i * 1500} Kč/měsíc",
            disposition="2+kk",
            area_m2=48 + i,
            locality=f"Praha {i + 1}",
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
