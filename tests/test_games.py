import json
import random
import tempfile
import time
import unittest
from pathlib import Path

from app.games import (
    TEACHING_RATIO,
    disposition_rank,
    higher_lower_pair,
    is_teaching_pair,
    leaderboard,
    locality_key,
    pick_same_locality_pair,
    refresh_pool_now,
    rent_round,
    reset_pool_cache,
    save_rent_round,
    score_guess,
    score_round,
    wait_refresh,
)
from app.sreality import Listing
from app.site_pages import site_html, site_page
from app.store import Store


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


class GamesTests(unittest.TestCase):
    def setUp(self):
        reset_pool_cache()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        wait_refresh(0.6)
        reset_pool_cache()
        self._tmpdir.cleanup()

    def test_score_guess_perfect_and_far(self):
        perfect = score_guess(20000, 20000)
        self.assertEqual(perfect["points"], 1000)
        self.assertEqual(perfect["error_pct"], 0)
        far = score_guess(20000, 40000)
        self.assertEqual(far["points"], 0)
        mid = score_guess(20000, 25000)
        self.assertGreater(mid["points"], 0)
        self.assertLess(mid["points"], 1000)

    def test_score_round_and_leaderboard(self):
        store = Store(self.tmp / "games.sqlite")
        items = [
            {"id": "a", "name": "A", "locality": "Praha", "price_czk": 10000, "portal": "sreality"},
            {"id": "b", "name": "B", "locality": "Brno", "price_czk": 20000, "portal": "bezrealitky"},
        ]
        scored = score_round(items, [{"id": "a", "guess": 10000}, {"id": "b", "guess": 20000}])
        self.assertEqual(scored["score"], 2000)
        self.assertEqual(scored["accuracy"], 100.0)
        save_rent_round(store, player_name="Eva", scored=scored)
        miss = score_round(items, [{"id": "a", "guess": 15000}, {"id": "b", "guess": 10000}])
        save_rent_round(store, player_name="Jan", scored=miss)
        board = leaderboard(store)
        self.assertEqual(board["stats"]["n"], 2)
        self.assertEqual(board["top"][0]["player_name"], "Eva")
        self.assertEqual(board["top"][0]["score"], 2000)
        self.assertEqual(len(board["recent"]), 2)
        self.assertTrue(board["recent"][0]["items"])

    def test_locality_key_canonicalizes_neighborhood(self):
        self.assertEqual(locality_key("Praha 2 – Vinohrady"), "praha-2-vinohrady")
        self.assertEqual(locality_key("Vinohrady, Praha 2"), "praha-2-vinohrady")
        self.assertEqual(locality_key("Praha 2, Vinohrady"), "praha-2-vinohrady")
        self.assertEqual(locality_key("Praha 3 – Žižkov"), "praha-3-zizkov")
        self.assertEqual(locality_key("Bedihošť"), "bedihost")
        self.assertEqual(locality_key("Brno – střed"), "brno-stred")
        self.assertNotEqual(locality_key("Praha 2 – Vinohrady"), locality_key("Bedihošť"))
        self.assertNotEqual(locality_key("Praha 2 – Vinohrady"), locality_key("Praha 3 – Žižkov"))

    def test_disposition_rank_orders_layouts(self):
        self.assertLess(disposition_rank("1+kk"), disposition_rank("1+1"))
        self.assertLess(disposition_rank("1+1"), disposition_rank("2+kk"))
        self.assertLess(disposition_rank("2+kk"), disposition_rank("3+kk"))
        self.assertLess(disposition_rank("3+kk"), disposition_rank("4+kk"))

    def test_teaching_pair_better_and_cheaper(self):
        better = _flat("good", "Praha 2 – Vinohrady", "3+kk", 82, 21900)
        worse = _flat("bad", "Praha 2 – Vinohrady", "2+kk", 46, 26800)
        self.assertTrue(is_teaching_pair(better, worse))
        normal_cheap = _flat("small", "Praha 2 – Vinohrady", "1+kk", 32, 14200)
        normal_big = _flat("big", "Praha 2 – Vinohrady", "3+kk", 82, 21900)
        self.assertFalse(is_teaching_pair(normal_cheap, normal_big))

    def test_higher_lower_seed_pair(self):
        store = Store(self.tmp / "empty.sqlite")
        pair = higher_lower_pair(store)
        self.assertNotEqual(pair["left"]["id"], pair["right"]["id"])
        self.assertIn(pair["cheaper"], {"left", "right"})
        cheaper = pair["left"] if pair["cheaper"] == "left" else pair["right"]
        other = pair["right"] if pair["cheaper"] == "left" else pair["left"]
        self.assertLessEqual(cheaper["price_czk"], other["price_czk"])
        self.assertTrue(pair["seeded"])
        self.assertEqual(pair["left"]["locality_key"], pair["right"]["locality_key"])
        self.assertTrue(pair["locality_key"])
        self.assertIn(pair["pair_kind"], {"teaching", "random"})
        self.assertTrue(
            {"cheaper", "copy", "copy_ok", "copy_miss", "left", "right", "seeded", "vanish_hours"}
            <= set(pair)
        )

    def test_pairs_never_mix_cities(self):
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
            self.assertEqual(left_key, right_key)
            self.assertEqual(left_key, pair["locality_key"])
            cities = {pair["left"]["id"][0], pair["right"]["id"][0]}
            self.assertEqual(len(cities), 1)

    def test_teaching_distribution_is_about_80_percent(self):
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
        self.assertGreaterEqual(rate, 0.72, f"teaching rate {rate:.3f}")
        self.assertLessEqual(rate, 0.88, f"teaching rate {rate:.3f}")
        forced = [
            pick_same_locality_pair(pool, rng=random.Random(i), teaching_ratio=1.0)
            for i in range(80)
        ]
        self.assertTrue(all(row["pair_kind"] == "teaching" for row in forced))
        self.assertTrue(all(is_teaching_pair(row["left"], row["right"]) for row in forced))

    def test_cold_path_does_not_block_on_slow_catalog(self):
        class Slow(Store):
            def game_listing_pool(self, **kwargs):
                time.sleep(0.3)
                return []

        store = Slow(self.tmp / "slow.sqlite")
        t0 = time.perf_counter()
        pair = higher_lower_pair(store)
        hl_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        round_payload = rent_round(store)
        rent_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(hl_ms, 50, f"higher-lower cold path {hl_ms:.1f}ms")
        self.assertLess(rent_ms, 50, f"rent-round cold path {rent_ms:.1f}ms")
        self.assertTrue(pair["seeded"])
        self.assertEqual(pair["left"]["locality_key"], pair["right"]["locality_key"])
        self.assertEqual(len(round_payload["items"]), 5)

    def test_refresh_uses_live_catalog_rentals(self):
        store = Store(self.tmp / "live.sqlite")
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
        self.assertGreaterEqual(len(live), 6)
        pair = higher_lower_pair(store)
        self.assertFalse(pair["seeded"])
        self.assertNotEqual(pair["left"]["id"], pair["right"]["id"])
        self.assertEqual(pair["left"]["locality_key"], "praha-3-zizkov")
        self.assertEqual(pair["right"]["locality_key"], "praha-3-zizkov")

    def test_live_pool_does_not_pair_vinohrady_with_bedihost(self):
        store = Store(self.tmp / "mixed.sqlite")
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
            self.assertEqual(pair["left"]["locality_key"], pair["right"]["locality_key"])
            self.assertIn(pair["locality_key"], {"praha-2-vinohrady", "bedihost"})

    def test_game_listing_pool_stays_fast_on_fat_catalog(self):
        store = Store(self.tmp / "fat.sqlite")
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
        self.assertIn("idx_catalog_gone_last_seen", indexes)

        t0 = time.perf_counter()
        rows = store.game_listing_pool(limit=240, budget_sec=0.2)
        pool_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(pool_ms, 200, f"game_listing_pool {pool_ms:.1f}ms on {n} rows")
        self.assertEqual(rows, [])

        t0 = time.perf_counter()
        pair = higher_lower_pair(store)
        self.assertLess((time.perf_counter() - t0) * 1000, 50)
        self.assertTrue(pair["seeded"])
        self.assertEqual(pair["left"]["locality_key"], pair["right"]["locality_key"])

    def test_hry_html_is_memory_fast_and_nonblocking(self):
        site_html.cache_clear()
        t0 = time.perf_counter()
        html = site_html("hry-vyssi-nizsi.html")
        cold_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        again = site_html("hry-vyssi-nizsi.html")
        warm_ms = (time.perf_counter() - t0) * 1000
        self.assertIn("KTERÝ BYT JE LEVNĚJŠÍ", html)
        self.assertEqual(html, again)
        self.assertIn("games.css", html)
        self.assertNotIn("site.css", html)
        self.assertIn('media="print"', html)
        self.assertNotIn(".png", html)
        self.assertLess(cold_ms, 80, f"cold HTML read {cold_ms:.1f}ms")
        self.assertLess(warm_ms, 5, f"cached HTML {warm_ms:.1f}ms")
        response = site_page("hry.html")
        self.assertTrue(response.headers["cache-control"].startswith("public"))
        hub = site_html("hry.html")
        rent = site_html("hry-najem.html")
        self.assertIn("HIGHER / LOWER", hub)
        self.assertIn("KOLIK STOJÍ MĚSÍC", rent)
        self.assertIn("games.css", hub)
        self.assertIn("games.css", rent)


if __name__ == "__main__":
    unittest.main()
