"""Admin users table: same numbers as before, but unchanged users cost two stat() calls, and the page is cached."""
from __future__ import annotations

import time

import pytest

from app import admin, billing, users
from app.store import Store


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(users, "user_data_dir", lambda: tmp_path / "users")
    gstore = Store(tmp_path / "g.sqlite", seed_webhook_url="")
    stores: dict[str, Store] = {}
    calls: list[str] = []

    def store_for(uid: str) -> Store:
        calls.append(uid)
        if uid not in stores:
            stores[uid] = Store(users.user_store_path(uid), seed_webhook_url="")
        return stores[uid]

    ids = [users.create_user(gstore, email=f"u{i}@ex.cz", password="Heslo12345!", first="U", last=str(i))["id"] for i in range(8)]
    for uid in ids:
        store_for(uid)
    calls.clear()
    admin._USER_STAT_CACHE.clear()
    billing.invalidate_cache()
    yield gstore, store_for, ids, calls
    admin._USER_STAT_CACHE.clear()
    billing.invalidate_cache()


def test_unchanged_users_do_not_touch_their_stores(world):
    gstore, store_for, ids, calls = world
    first = admin.users_payload(gstore, store_for)
    assert first["total"] == 8 and len(calls) == 8
    calls.clear()
    second = admin.users_payload(gstore, store_for)
    assert calls == [], "a second refresh must not rebuild/open any user store"
    assert second["users"] == first["users"]


def test_changed_user_is_recomputed_alone(world):
    gstore, store_for, ids, calls = world
    admin.users_payload(gstore, store_for)
    calls.clear()
    store_for(ids[3]).save_billing_record({"plan": "start", "subscription_id": "sub_1", "status": "active"})
    calls.clear()
    out = admin.users_payload(gstore, store_for)
    assert calls == [ids[3]]
    row = next(r for r in out["users"] if r["id"] == ids[3])
    assert row["plan"] == "start"


def test_expired_time_limited_grant_is_not_served_from_cache(world):
    gstore, store_for, ids, calls = world
    store_for(ids[0]).save_billing_record({"comp_plan": "pro", "comp_until": "2000-01-01T00:00:00+00:00"})
    admin._USER_STAT_CACHE.clear()
    out = admin.users_payload(gstore, store_for)
    assert next(r for r in out["users"] if r["id"] == ids[0])["plan"] == "free"


def test_last_activity_uses_newest_session_or_presence(world):
    gstore, store_for, ids, calls = world
    with gstore.connect() as conn:
        conn.execute("UPDATE users SET created_at = '2026-01-01T00:00:00+00:00'")
        conn.execute("INSERT OR REPLACE INTO sessions (token, user_id, created_at, expires_at) VALUES ('t1', ?, '2026-03-01T00:00:00+00:00', '2099-01-01')", (ids[1],))
    out = admin.users_payload(gstore, store_for)
    row = next(r for r in out["users"] if r["id"] == ids[1])
    assert row["activity_at"] == "2026-03-01T00:00:00+00:00"
    other = next(r for r in out["users"] if r["id"] == ids[2])
    assert other["activity_at"] == "2026-01-01T00:00:00+00:00"


def test_cached_payload_serves_second_call_without_loading(world):
    hits = []

    def loader():
        hits.append(1)
        return {"n": len(hits)}

    assert admin.cached_payload("users_test", loader) == {"n": 1}
    t = time.perf_counter()
    assert admin.cached_payload("users_test", loader) == {"n": 1}
    assert time.perf_counter() - t < 0.05 and len(hits) == 1
    admin.invalidate_payloads()
    billing.invalidate_cache("admin_users_test")
    assert admin.cached_payload("users_test", loader) == {"n": 2}


def test_warm_refresh_is_fast_for_many_users(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "user_data_dir", lambda: tmp_path / "users")
    gstore = Store(tmp_path / "g.sqlite", seed_webhook_url="")
    stores: dict[str, Store] = {}

    def store_for(uid):
        if uid not in stores:
            stores[uid] = Store(users.user_store_path(uid), seed_webhook_url="")
        return stores[uid]

    for i in range(150):
        store_for(users.create_user(gstore, email=f"m{i}@ex.cz", password="Heslo12345!", first="M", last=str(i))["id"])
    admin._USER_STAT_CACHE.clear()
    admin.users_payload(gstore, store_for)  # cold
    admin.users_payload(gstore, store_for)  # SQLite settles WAL files after the first reads
    t = time.perf_counter()
    admin.users_payload(gstore, store_for)
    warm = time.perf_counter() - t
    assert warm < 0.5, f"warm refresh of 150 users took {warm:.2f}s"


def test_admin_handlers_never_authenticate_on_the_event_loop():
    """Admin auth reads the contended global DB and may build the big catalog Store: it must run in a pool."""
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8"))
    offenders: list[int] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append("sync")
            self.generic_visit(node)
            self.stack.pop()

        visit_Lambda = visit_FunctionDef

        def visit_AsyncFunctionDef(self, node):
            self.stack.append("async")
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id in {"_admin_user", "_admin_store"} and self.stack[-1:] == ["async"]:
                offenders.append(node.lineno)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert offenders == [], f"sync admin auth inside async handlers at lines {offenders}"
