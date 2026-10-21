"""Admin MCP: one key per admin, hashed at rest, full admin API access via call_api, no key self-management."""
import os
import sys
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-amcp-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import rate_limit
from app import users as user_registry
from app.main import app, hub


@pytest.fixture()
def client():
    rate_limit.reset()
    # No `with`: skip the lifespan so no scrape/warm threads start and skew timing-sensitive tests that run later.
    yield TestClient(app, raise_server_exceptions=False)


def _admin(email="amcp-admin@example.cz"):
    admin = user_registry.get_user_by_email(hub.store, email) or user_registry.create_user(
        hub.store, email=email, password="Heslo12345!", first="A", last="D"
    )
    user_registry.set_role(hub.store, admin["id"], "admin")
    return admin


def _rpc(client, token, method, params=None, mid=1):
    return client.post(
        "/mcp-admin",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}},
    )


def test_key_lifecycle_and_calls(client):
    admin = _admin()
    cookie = user_registry.create_session(hub.store, admin["id"])
    client.cookies.set("realitify_admin", cookie)
    client.delete("/api/admin/mcp-key")
    created = client.post("/api/admin/mcp-key", json={"name": "t"})
    assert created.status_code == 200, created.text
    token = created.json()["key"]["token"]
    assert client.post("/api/admin/mcp-key", json={}).status_code == 409  # one per admin
    assert token not in str(hub.store.get_meta("admin_mcp_keys"))  # hashed at rest

    client.cookies.clear()
    assert client.post("/mcp-admin", json={}).status_code == 401
    assert _rpc(client, "rf_admin_nope", "tools/list").status_code == 401

    tools = _rpc(client, token, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_endpoints", "describe_endpoint", "call_api"}

    out = _rpc(client, token, "tools/call", {"name": "call_api", "arguments": {"method": "GET", "path": "/api/admin/me"}}).json()
    res = out["result"]
    assert res["isError"] is False and '"status": 200' in res["content"][0]["text"]

    blocked = _rpc(client, token, "tools/call", {"name": "call_api", "arguments": {"method": "GET", "path": "/api/admin/mcp-key"}}).json()
    assert blocked["result"]["isError"] is True

    listed = _rpc(client, token, "tools/call", {"name": "list_endpoints", "arguments": {"contains": "admin/users"}}).json()
    assert "/api/admin/users" in listed["result"]["content"][0]["text"]

    # demoted owner loses access immediately
    user_registry.set_role(hub.store, admin["id"], "user")
    assert _rpc(client, token, "tools/list").status_code == 401
    user_registry.set_role(hub.store, admin["id"], "admin")
    cookie = user_registry.create_session(hub.store, admin["id"])
    client.cookies.set("realitify_admin", cookie)
    assert client.delete("/api/admin/mcp-key").status_code == 200
    client.cookies.clear()
    assert _rpc(client, token, "tools/list").status_code == 401
