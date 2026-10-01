"""Admin login via ADMIN_PASSWORD (env) — first login must auto-create the admin user."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import admin as admin_panel  # noqa: E402
from app import config  # noqa: E402
from app import users as user_registry  # noqa: E402
from app.store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "central.sqlite")


@pytest.fixture()
def admin_pw(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_PASSWORD", "test-admin-pw-123")
    return "test-admin-pw-123"


def test_first_login_creates_admin_user(store, admin_pw):
    """Correct ADMIN_PASSWORD with no admin user yet must create it, not 500."""
    token = admin_panel.login_admin(store, "admin@realitify.cz", admin_pw)
    assert token, "login must return a session token"
    user = user_registry.get_user_by_email(store, "admin@realitify.cz")
    assert user is not None
    assert user.get("role") == "admin"
    # Token must resolve back to the admin user.
    me = admin_panel.admin_from_cookie(store, token)
    assert me and me.get("email") == "admin@realitify.cz"


def test_second_login_reuses_admin_user(store, admin_pw):
    first = admin_panel.login_admin(store, "admin@realitify.cz", admin_pw)
    second = admin_panel.login_admin(store, "admin@realitify.cz", admin_pw)
    assert first and second
    users = [
        u for u in user_registry.list_users(store)
        if (u.get("email") or "").lower() == "admin@realitify.cz"
    ]
    assert len(users) == 1, "must not create duplicate admin users"


def test_wrong_password_rejected(store, admin_pw):
    with pytest.raises(ValueError, match="nesedí"):
        admin_panel.login_admin(store, "admin@realitify.cz", "wrong-password")
