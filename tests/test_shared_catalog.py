"""Shared catalog: scraped once (owner store), read by every user with their own state.

Covers the scoped read (`Store.scoped_for`), the indexed fast path of `Store.catalog`,
the sold sync for consumers and the scrape worker's owner / consumer role split.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.sreality import Listing
from app.store import CATALOG_MONITOR_ID, Store, listing_key


def _listing(i: int, *, locality: str = "Brno", rent: bool = True, price: int | None = None, name: str | None = None) -> Listing:
    price = price if price is not None else (15000 + i if rent else 5_000_000 + i)
    kind = "pronajem" if rent else "prodej"
    return Listing(
        id=7000 + i,
        name=name or f"{'Pronájem' if rent else 'Prodej'} bytu 2+kk {40 + i} m²",
        price_czk=price,
        price_label=f"{price} Kč/měsíc" if rent else f"{price} Kč",
        disposition="2+kk",
        area_m2=40 + i,
        locality=f"Ulice {i}, {locality}",
        url=f"https://www.sreality.cz/detail/{kind}/byt/2+kk/{locality.lower()}/{7000 + i}",
        image_url=f"https://img.example/{i}.jpg",
    )


def _catalog_store(tmp: str, name: str, rows: list[Listing]) -> Store:
    store = Store(Path(tmp) / name)
    if rows:
        store.upsert_catalog_listings_batch(rows, kind="seeded", commit_every=10, fast=True)
    return store


def _ids(result: dict) -> list[int]:
    return sorted(int(item["id"]) for item in result["items"])


FILTERS = {"district": "Brno", "offer": "pronajem", "limit": 50, "offset": 0, "facets": "1", "sort": "price_asc"}


class ScopedCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        tmp = self._tmp.name
        self.rows = [_listing(i) for i in range(6)]
        self.owner = _catalog_store(tmp, "owner.sqlite", self.rows)
        self.user1 = _catalog_store(tmp, "user1.sqlite", [])
        self.user2 = _catalog_store(tmp, "user2.sqlite", [])
        self.url = {r.id: r.url for r in self.rows}

    def test_user_reads_owner_catalog_even_when_own_catalog_is_empty(self) -> None:
        own = self.user1.catalog(dict(FILTERS))
        self.assertEqual(own["total"], 0)
        shared = self.owner.scoped_for(self.user1).catalog(dict(FILTERS))
        self.assertEqual(shared["total"], 6)
        self.assertEqual(_ids(shared), sorted(r.id for r in self.rows))

    def test_hidden_listing_is_per_user(self) -> None:
        self.user1.set_listing_user(self.url[7001], status="hidden")
        u1 = self.owner.scoped_for(self.user1).catalog(dict(FILTERS))
        u2 = self.owner.scoped_for(self.user2).catalog(dict(FILTERS))
        self.assertNotIn(7001, _ids(u1))
        self.assertEqual(u1["total"], 5)
        self.assertIn(7001, _ids(u2))
        self.assertEqual(u2["total"], 6)

    def test_owner_state_does_not_leak_to_users(self) -> None:
        # The owner (admin) hides one listing and notes another in *their* app.
        self.owner.set_listing_user(self.url[7002], status="hidden")
        self.owner.set_listing_user(self.url[7003], note="soukromá poznámka admina")
        shared = self.owner.scoped_for(self.user2).catalog(dict(FILTERS))
        self.assertIn(7002, _ids(shared))
        notes = {int(i["id"]): i["note"] for i in shared["items"]}
        self.assertEqual(notes[7003], "")

    def test_saved_status_and_notes_come_from_the_user(self) -> None:
        self.user1.set_listing_user(self.url[7004], status="saved", note="zavolat")
        scoped = self.owner.scoped_for(self.user1)
        result = scoped.catalog(dict(FILTERS))
        item = next(i for i in result["items"] if int(i["id"]) == 7004)
        self.assertEqual(item["status"], "saved")
        self.assertEqual(item["note"], "zavolat")
        saved = scoped.catalog({**FILTERS, "status": "saved"})
        self.assertEqual(_ids(saved), [7004])
        other = self.owner.scoped_for(self.user2).catalog({**FILTERS, "status": "saved"})
        self.assertEqual(other["total"], 0)

    def test_monitors_and_notified_overlay_are_the_users(self) -> None:
        owner_monitor = self.owner.save_monitor({"id": "default", "name": "Admin hlídač", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/brno"})
        user_monitor = self.user1.save_monitor({"id": "default", "name": "Můj hlídač", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/brno"})
        listing_a, listing_b = self.rows[0], self.rows[1]
        # owner's monitor notified about A; user1's monitor notified about B.
        self.owner.upsert_seen(owner_monitor["id"], listing_a, notified=True)
        self.user1.upsert_seen(user_monitor["id"], listing_b, notified=True)
        self.user1.add_monitor_hit(user_monitor["id"], listing_key(listing_b.url))
        scoped = self.owner.scoped_for(self.user1).catalog(dict(FILTERS))
        by_id = {int(i["id"]): i for i in scoped["items"]}
        self.assertEqual(by_id[listing_a.id]["notified"], 0, "owner's notified flag must not show for a user")
        self.assertEqual(by_id[listing_b.id]["notified"], 1)
        names = {m.get("name") for m in by_id[listing_a.id]["monitors"]}
        self.assertNotIn("Admin hlídač", names)
        facet_monitors = {m["name"] for m in scoped["facets"]["monitors"]}
        self.assertEqual(facet_monitors, {"Můj hlídač"})

    def test_owner_monitor_rows_are_not_served_to_users(self) -> None:
        # The owner's monitor saw a listing (their own monitor row, monitor_id "default").
        owner_monitor = self.owner.save_monitor({"id": "default", "name": "Admin hlídač", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/brno"})
        extra = _listing(50)
        self.owner.upsert_seen(owner_monitor["id"], extra, notified=True)
        self.user1.save_monitor({"id": "default", "name": "Můj hlídač", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/brno"})
        result = self.owner.scoped_for(self.user1).catalog(dict(FILTERS))
        self.assertTrue(all(i["monitor_id"] == CATALOG_MONITOR_ID for i in result["items"]))
        self.assertNotIn(extra.id, _ids(result))

    def test_scoped_clone_is_read_only(self) -> None:
        scoped = self.owner.scoped_for(self.user1)
        with self.assertRaises(Exception):
            with scoped.connect() as conn:
                conn.execute("DELETE FROM listings")
        self.assertEqual(self.owner.catalog(dict(FILTERS))["total"], 6)

    def test_scoped_item_lookup_returns_shared_row(self) -> None:
        item = self.owner.scoped_for(self.user1).catalog_item(CATALOG_MONITOR_ID, 7000)
        self.assertIsNotNone(item)
        self.assertEqual(int(item["id"]), 7000)


class FastPathTests(unittest.TestCase):
    """The indexed columns must give exactly the same answers as the original LIKE/JSON predicates."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        rows: list[Listing] = []
        for i in range(12):
            rows.append(_listing(i, locality="Brno", rent=True))
        for i in range(12, 20):
            rows.append(_listing(i, locality="Praha", rent=True, price=18000 + i))
        for i in range(20, 28):
            rows.append(_listing(i, locality="Brno", rent=False))
        rows.append(_listing(40, locality="Brno", rent=True, price=900))  # below the quality floor
        rows.append(_listing(41, locality="Brno", rent=True, name="Krátkodobý pronájem airbnb"))
        self.store = _catalog_store(self._tmp.name, "fast.sqlite", rows)
        self.store.ensure_fast_indexes()

    def _both(self, filters: dict) -> tuple[dict, dict]:
        self.assertTrue(self.store._fast_ok)
        fast = self.store.catalog(dict(filters))
        self.store._fast_ok = False
        try:
            slow = self.store.catalog(dict(filters))
        finally:
            self.store._fast_ok = True
        return fast, slow

    def test_same_results_as_legacy_predicates(self) -> None:
        cases = [
            {"district": "Brno", "offer": "pronajem"},
            {"district": "Brno", "offer": "pronajem", "price_to": 15005},
            {"district": "Praha", "offer": "pronajem", "listing_quality": "apartment"},
            {"district": "Brno", "offer": "prodej", "listing_quality": "apartment"},
            {"offer": "prodej"},
            {"offer": "pronajem,prodej", "district": "Brno"},
            {"q": "Brno", "offer": "pronajem", "listing_quality": "apartment"},
        ]
        for case in cases:
            fast, slow = self._both({**case, "limit": 50, "offset": 0, "facets": "0", "sort": "price_asc"})
            self.assertEqual(fast["total"], slow["total"], case)
            self.assertEqual(_ids(fast), _ids(slow), case)

    def test_quality_filter_excludes_short_term_and_implausible_prices(self) -> None:
        result = self.store.catalog({"district": "Brno", "offer": "pronajem", "listing_quality": "apartment", "limit": 50, "offset": 0, "facets": "0"})
        ids = _ids(result)
        self.assertNotIn(7040, ids)
        self.assertNotIn(7041, ids)
        self.assertEqual(len(ids), 12)

    def test_internal_columns_are_not_exposed(self) -> None:
        item = self.store.catalog({"district": "Brno", "offer": "pronajem", "limit": 1, "offset": 0, "facets": "0"})["items"][0]
        self.assertFalse([k for k in item if k.startswith("x_")])


class SoldSyncTests(unittest.TestCase):
    def test_consumer_follows_owner_gone_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            listing = _listing(1)
            owner = _catalog_store(tmp, "owner.sqlite", [listing])
            user = _catalog_store(tmp, "user.sqlite", [])
            monitor = user.save_monitor({"id": "default", "name": "Hlídač", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/brno"})
            user.upsert_seen(monitor["id"], listing, notified=True)
            user.add_monitor_hit(monitor["id"], listing_key(listing.url))
            self.assertEqual(user.sync_gone_from_catalog(owner), 0, "nothing is gone yet")
            self.assertEqual(user.pending_sold_pings(), [])

            owner.mark_catalog_listing_gone(listing_key(listing.url))
            self.assertEqual(user.pending_sold_pings(), [], "the user's own store is untouched until the sync runs")
            self.assertEqual(user.sync_gone_from_catalog(owner), 1)
            pending = user.pending_sold_pings()
            self.assertEqual([int(p["id"]) for p in pending], [listing.id])
            # Idempotent: the row is gone for good, a second pass finds nothing.
            self.assertEqual(user.sync_gone_from_catalog(owner), 0)

    def test_sync_without_gone_listings_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            owner = _catalog_store(tmp, "owner.sqlite", [_listing(1)])
            user = _catalog_store(tmp, "user.sqlite", [])
            self.assertEqual(user.sync_gone_from_catalog(owner), 0)


class ConsumerSoldTickTests(unittest.TestCase):
    """A consumer hub's sold tick follows the owner and never re-crawls its own stale catalog copy."""

    def test_sold_tick_for_consumer_uses_owner_state(self) -> None:
        from app.monitor import Hub

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            listing = _listing(1)
            owner = _catalog_store(tmp, "owner.sqlite", [listing])
            hub = Hub(store_path=Path(tmp) / "user.sqlite")
            monitor = hub.store.save_monitor({"id": "default", "name": "Hlídač", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/brno"})
            hub.store.upsert_seen(monitor["id"], listing, notified=True)
            hub.store.add_monitor_hit(monitor["id"], listing_key(listing.url))
            hub.catalog_owner = False
            hub.catalog_owner_store_fn = lambda: owner
            sent: list[int] = []

            async def fake_ping(pending, monitor_name=""):
                sent.append(int(pending["id"]))

            hub._send_sold_ping = fake_ping  # type: ignore[assignment]
            stale_probe = mock.Mock(side_effect=AssertionError("a consumer must not crawl stale catalog rows"))
            hub.store.stale_catalog_listings = stale_probe  # type: ignore[assignment]

            async def run() -> None:
                await hub._sold_tick()
                self.assertEqual(sent, [])
                owner.mark_catalog_listing_gone(listing_key(listing.url))
                hub._owner_sync_at = 0.0
                await hub._sold_tick()

            asyncio.run(run())
            self.assertEqual(sent, [listing.id])
            stale_probe.assert_not_called()

    def test_owner_hub_keeps_the_stale_recheck(self) -> None:
        from app.monitor import Hub

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            hub = Hub(store_path=Path(tmp) / "owner.sqlite")
            self.assertTrue(hub.catalog_owner)


class _FakeHub:
    """Stands in for monitor.Hub: records which pipelines the worker starts."""

    instances: list["_FakeHub"] = []

    def __init__(self, store_path=None, shared=None) -> None:
        self.store_path = store_path
        self.store = object()
        self.running = False
        self.catalog_owner = True
        self.catalog_owner_store_fn = None
        self.calls: list[str] = []
        self.last_error = None
        _FakeHub.instances.append(self)

    async def _loop(self):
        self.calls.append("monitor")

    async def _sold_loop(self):
        self.calls.append("sold")

    async def backfill_missing_coords(self):
        self.calls.append("coords")

    async def catalog_coords_loop(self):
        self.calls.append("catalog_coords")

    async def _recent_catalog_loop(self):
        self.calls.append("discovery")

    async def _deep_catalog_loop(self):
        self.calls.append("deep")

    async def _dedupe_loop(self):
        self.calls.append("dedupe")

    async def _stale_sweep_noop(self):
        self.calls.append("stale")

    async def close(self):
        pass


class WorkerRolesTests(unittest.TestCase):
    def _worker(self):
        from app import scrape_worker

        with mock.patch.object(scrape_worker, "Hub", _FakeHub):
            _FakeHub.instances.clear()
            worker = scrape_worker.ScrapeWorker()
            worker._stale_sweep_loop = lambda hub: hub._stale_sweep_noop()  # type: ignore[assignment]
            return scrape_worker, worker

    def _spawn(self, worker, user_ids: list[str], owner_id: str | None) -> dict[str, list[str]]:
        async def run() -> dict[str, list[str]]:
            worker.owner_id = owner_id
            for uid in user_ids:
                await worker._spawn_user(uid)
            await asyncio.sleep(0.05)
            return {uid: sorted(hub.calls) for uid, hub in worker.user_hubs.items()}

        return asyncio.run(run())

    def test_only_the_owner_runs_catalog_pipelines(self) -> None:
        from app import config

        scrape_worker, worker = self._worker()
        with mock.patch.object(scrape_worker, "Hub", _FakeHub), mock.patch.object(config, "SHARED_CATALOG", True):
            calls = self._spawn(worker, ["admin", "u1", "u2"], "admin")
        self.assertEqual(calls["admin"], sorted(["monitor", "sold", "coords", "catalog_coords", "discovery", "deep", "dedupe", "stale"]))
        for uid in ("u1", "u2"):
            self.assertEqual(calls[uid], sorted(["monitor", "sold", "coords"]), uid)
            self.assertFalse(worker.user_hubs[uid].catalog_owner)
        self.assertTrue(worker.user_hubs["admin"].catalog_owner)

    def test_flag_off_restores_per_user_scraping(self) -> None:
        from app import config

        scrape_worker, worker = self._worker()
        with mock.patch.object(scrape_worker, "Hub", _FakeHub), mock.patch.object(config, "SHARED_CATALOG", False):
            calls = self._spawn(worker, ["admin", "u1"], "admin")
        full = sorted(["monitor", "sold", "coords", "catalog_coords", "discovery", "deep", "dedupe", "stale"])
        self.assertEqual(calls["admin"], full)
        self.assertEqual(calls["u1"], full)

    def test_no_owner_means_everybody_scrapes(self) -> None:
        from app import config

        scrape_worker, worker = self._worker()
        with mock.patch.object(scrape_worker, "Hub", _FakeHub), mock.patch.object(config, "SHARED_CATALOG", True):
            calls = self._spawn(worker, ["u1", "u2"], None)
        self.assertEqual(calls["u1"], calls["u2"])
        self.assertIn("discovery", calls["u1"])

    def test_owner_store_resolves_for_consumers(self) -> None:
        scrape_worker, worker = self._worker()
        with mock.patch.object(scrape_worker, "Hub", _FakeHub):
            calls = self._spawn(worker, ["admin", "u1"], "admin")
        self.assertIs(worker._owner_store(), worker.user_hubs["admin"].store)
        self.assertIs(worker.user_hubs["u1"].catalog_owner_store_fn(), worker.user_hubs["admin"].store)
        del calls


class CatalogOwnerIdTests(unittest.TestCase):
    def test_admin_role_wins_then_oldest_user(self) -> None:
        from app import users as user_registry

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = Store(Path(tmp) / "global.sqlite")
            self.assertIsNone(user_registry.catalog_owner_id(store))
            first = user_registry.create_user(store, email="a@example.com", password="x" * 10, first="A")
            second = user_registry.create_user(store, email="b@example.com", password="x" * 10, first="B", role="admin")
            self.assertEqual(user_registry.catalog_owner_id(store), str(second["id"]))
            user_registry.set_role(store, str(second["id"]), "user")
            self.assertEqual(user_registry.catalog_owner_id(store), str(first["id"]))


if __name__ == "__main__":
    unittest.main()


class SharedCatalogApiTests(unittest.TestCase):
    """End to end through the FastAPI app: a user's own store is empty, the catalog is the owner's."""

    @classmethod
    def setUpClass(cls) -> None:
        import os

        os.environ.setdefault("SCRAPE_ROLE", "web")
        from fastapi.testclient import TestClient

        from app import main
        from app import users as user_registry

        cls.main = main
        cls.registry = user_registry
        cls.client = TestClient(main.app)

    def setUp(self) -> None:
        main, registry = self.main, self.registry
        suffix = f"{id(self):x}"
        self.admin = registry.create_user(main.hub.store, email=f"admin-{suffix}@example.com", password="x" * 10, first="Admin", role="admin")
        self.user = registry.create_user(main.hub.store, email=f"user-{suffix}@example.com", password="x" * 10, first="Uživatel")
        registry.mark_email_verified(main.hub.store, self.user["id"])
        registry.mark_email_verified(main.hub.store, self.admin["id"])
        self.token = registry.create_session(main.hub.store, self.user["id"])
        self.admin_token = registry.create_session(main.hub.store, self.admin["id"])
        main._admin_id_cache.clear()
        self.rows = [_listing(i) for i in range(5)]
        owner = main.hub.store_for(str(self.admin["id"]))
        owner.upsert_catalog_listings_batch(self.rows, kind="seeded", commit_every=10, fast=True)
        main._CATALOG_CACHE.clear()

    def tearDown(self) -> None:
        self.main._admin_id_cache.clear()

    def _get(self, path: str, token: str | None = None):
        self.client.cookies.clear()
        if token:
            self.client.cookies.set("realitify_session", token)
        return self.client.get(path)

    def test_user_sees_owner_catalog_and_own_hidden_state(self) -> None:
        query = "/api/catalog?district=Brno&offer=pronajem&limit=50&facets=0"
        first = self._get(query, self.token)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["total"], 5)
        hide_url = self.rows[2].url
        self.client.cookies.clear()
        self.client.cookies.set("realitify_session", self.token)
        res = self.client.post("/api/catalog/user", json={"url": hide_url, "status": "hidden"})
        self.assertEqual(res.status_code, 200, res.text)
        after = self._get(query, self.token).json()
        self.assertEqual(after["total"], 4)
        self.assertNotIn(7002, [int(i["id"]) for i in after["items"]])
        # The owner's own view and another user's view are unaffected by this user's hidden listing.
        self.assertEqual(self._get(query, self.admin_token).json()["total"], 5)

    def test_item_detail_reads_the_shared_row(self) -> None:
        res = self._get(f"/api/catalog/item?monitor_id={CATALOG_MONITOR_ID}&id=7001&enrich=0", self.token)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(int(res.json()["id"]), 7001)

    def test_pins_and_facets_use_the_shared_catalog(self) -> None:
        pins = self._get("/api/catalog/pins?district=Brno&offer=pronajem&limit=50", self.token)
        self.assertEqual(pins.status_code, 200, pins.text)
        facets = self._get("/api/catalog?district=Brno&offer=pronajem&limit=5&facets=1", self.token).json()["facets"]
        self.assertIn("2+kk", facets["dispositions"])

    def test_flag_off_falls_back_to_the_users_own_store(self) -> None:
        from app import config

        query = "/api/catalog?district=Brno&offer=pronajem&limit=50&facets=0"
        with mock.patch.object(config, "SHARED_CATALOG", False):
            res = self._get(query, self.token)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["total"], 0)

    def test_status_reports_the_shared_catalog_size(self) -> None:
        res = self._get("/api/status", self.token)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertGreaterEqual(int(res.json().get("search_total") or 0), 5)

    def test_agent_tools_search_the_shared_catalog(self) -> None:
        from app import agents

        ustore = self.main.hub.store_for(str(self.user["id"]))
        reader = self.main._shared_catalog_for(ustore)
        self.assertIsNotNone(reader)
        found = agents.run_tool(ustore, "search_listings", {"district": "Brno", "offer": "pronajem", "limit": 25}, reader)
        self.assertEqual(found["total"], 5)
        own_only = agents.run_tool(ustore, "search_listings", {"district": "Brno", "offer": "pronajem", "limit": 25})
        self.assertEqual(own_only["total"], 0)

    def test_monitor_filtered_queries_stay_on_the_users_store(self) -> None:
        res = self._get("/api/catalog?monitor_id=default&limit=10&facets=0", self.token)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["total"], 0)
