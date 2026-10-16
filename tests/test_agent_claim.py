"""auth.md user-claimed agent registration (claim ceremony)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import mcp_oauth  # noqa: E402
from app.store import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "central.sqlite")


def test_claim_pending_then_approved_consumed(store):
    created = mcp_oauth.create_claim(store, email="A@Example.com", agent_name="bot")
    token = created["claim_token"]
    code = created["claim"]["user_code"]
    assert mcp_oauth.poll_claim(store, token)[0] == "pending"
    mcp_oauth.approve_claim(store, code, {"id": "u1", "email": "a@example.com"})
    state, row = mcp_oauth.poll_claim(store, token)
    assert state == "approved" and row["user_id"] == "u1"
    assert mcp_oauth.poll_claim(store, token)[0] == "expired"  # consumed


def test_claim_rejects_other_email_and_bad_code(store):
    created = mcp_oauth.create_claim(store, email="a@example.com")
    code = created["claim"]["user_code"]
    with pytest.raises(ValueError):
        mcp_oauth.approve_claim(store, code, {"id": "u2", "email": "b@example.com"})
    with pytest.raises(ValueError):
        mcp_oauth.approve_claim(store, "ZZZZ-ZZZZ", {"id": "u2", "email": "a@example.com"})


def test_claim_refresh_issues_new_code_and_expiry(store):
    created = mcp_oauth.create_claim(store, email="")
    token, old = created["claim_token"], created["claim"]["user_code"]
    fresh = mcp_oauth.refresh_claim(store, token)
    assert fresh["claim"]["user_code"] != old
    assert mcp_oauth.find_claim_by_code(store, old) is None
    with pytest.raises(ValueError):
        mcp_oauth.refresh_claim(store, "nope")


def test_metadata_advertises_agent_flow():
    meta = mcp_oauth.authorization_server_metadata()
    assert mcp_oauth.CLAIM_GRANT in meta["grant_types_supported"]
    aa = meta["agent_auth"]
    assert aa["skill"].endswith("/auth.md") and aa["identity_endpoint"].endswith("/agent/identity")
    assert aa["claim_uri"] and aa["identity_assertion"]["assertion_types_supported"] == ["verified_email"]
