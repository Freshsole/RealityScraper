from pathlib import Path

from app.games import higher_lower_pair, leaderboard, save_rent_round, score_guess, score_round
from app.store import Store


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
