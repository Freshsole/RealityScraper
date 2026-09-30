"""Multi-user auth + data isolation tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import account as user_account  # noqa: E402
from app import users as user_registry  # noqa: E402
from app.store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "central.sqlite")


def _register(store, email, name="Test User", password="password123"):
    return user_account.register(store, name, email, password)


def test_register_two_users(store):
    a, _ = _register(store, "a@example.com", "Alice A")
    b, _ = _register(store, "b@example.com", "Bob B")
    assert a["id"] != b["id"]
    assert a["email"] == "a@example.com"
    assert b["email"] == "b@example.com"


def test_duplicate_email_rejected(store):
    _register(store, "dup@example.com")
    with pytest.raises(ValueError, match="registrov"):
        _register(store, "DUP@example.com")  # case-insensitive


def test_login_ok_and_wrong_password(store):
    _register(store, "login@example.com", password="secret1234")
    user, token = user_account.login(store, "login@example.com", "secret1234")
    assert user["email"] == "login@example.com"
    assert token
    with pytest.raises(ValueError):
        user_account.login(store, "login@example.com", "wrongpass")


def test_session_roundtrip_and_logout(store):
    user, _ = _register(store, "sess@example.com")
    _, token = user_account.login(store, "sess@example.com", "password123")
    seen = user_account.user_from_session(store, token)
    assert seen and seen["id"] == user["id"]
    user_account.clear_session(store, token)
    assert user_account.user_from_session(store, token) is None


def test_sessions_are_isolated_between_users(store):
    a, _ = _register(store, "iso-a@example.com")
    b, _ = _register(store, "iso-b@example.com")
    _, tok_a = user_account.login(store, "iso-a@example.com", "password123")
    _, tok_b = user_account.login(store, "iso-b@example.com", "password123")
    assert user_account.user_from_session(store, tok_a)["id"] == a["id"]
    assert user_account.user_from_session(store, tok_b)["id"] == b["id"]
    # logging out A does not affect B
    user_account.clear_session(store, tok_a)
    assert user_account.user_from_session(store, tok_a) is None
    assert user_account.user_from_session(store, tok_b)["id"] == b["id"]


def test_password_reset_flow(store):
    _register(store, "reset@example.com")
    token = user_account.create_password_reset(store, "reset@example.com")
    assert token
    assert user_account.password_reset_ok(store, token)
    user, session = user_account.reset_password_with_token(store, token, "newpassword1")
    assert user["email"] == "reset@example.com"
    assert session
    # old password no longer works, new one does
    with pytest.raises(ValueError):
        user_account.login(store, "reset@example.com", "password123")
    user2, _ = user_account.login(store, "reset@example.com", "newpassword1")
    assert user2["id"] == user["id"]
    # token is single-use
    assert not user_account.password_reset_ok(store, token)


def test_change_password_invalidates_sessions(store):
    user, _ = _register(store, "chpw@example.com")
    _, tok1 = user_account.login(store, "chpw@example.com", "password123")
    _, tok2 = user_account.login(store, "chpw@example.com", "password123")
    assert user_account.user_from_session(store, tok1)
    user_account.change_password(store, user["id"], "password123", "brandnewpw1")
    assert user_account.user_from_session(store, tok1) is None
    assert user_account.user_from_session(store, tok2) is None
    user_account.login(store, "chpw@example.com", "brandnewpw1")


def test_admin_role_assignment(store):
    u, _ = _register(store, "role@example.com")
    assert u["role"] == "user"
    user_registry.set_role(store, u["id"], "admin")
    assert user_registry.get_user_by_id(store, u["id"])["role"] == "admin"


def test_per_user_db_paths_are_distinct(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    p1 = user_registry.user_store_path("aaa")
    p2 = user_registry.user_store_path("bbb")
    assert p1 != p2
    assert "aaa" in str(p1) and "bbb" in str(p2)


def test_private_stores_are_isolated(tmp_path):
    s1 = Store(tmp_path / "u1.sqlite")
    s2 = Store(tmp_path / "u2.sqlite")
    s1.set_meta("account", '{"email": "a@example.com"}')
    s2.set_meta("account", '{"email": "b@example.com"}')
    assert "a@example.com" in (s1.get_meta("account") or "")
    assert "b@example.com" in (s2.get_meta("account") or "")
    assert "b@example.com" not in (s1.get_meta("account") or "")
