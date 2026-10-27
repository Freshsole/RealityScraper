"""OAuth login for /mcp-admin: connector added by URL + admin sign-in, isolated from the customer OAuth."""
import os
import secrets
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-aoauth-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import admin_oauth, mcp_oauth, rate_limit
from app import users as user_registry
from app.main import app, hub

PASSWORD = "Heslo12345!"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture()
def client():
    rate_limit.reset()
    yield TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def _admin(email="aoauth-admin@example.cz"):
    admin = user_registry.get_user_by_email(hub.store, email) or user_registry.create_user(
        hub.store, email=email, password=PASSWORD, first="A", last="D"
    )
    user_registry.set_role(hub.store, admin["id"], "admin")
    return admin


def _register(client, redirect=REDIRECT):
    return client.post("/oauth/admin/register", json={"redirect_uris": [redirect], "client_name": "Claude"})


def _authorize_params(client_id, challenge, redirect=REDIRECT, **extra):
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-1",
        "resource": admin_oauth.resource_url(),
        **extra,
    }


def _login_and_get_tokens(client, email="aoauth-admin@example.cz", password=PASSWORD):
    verifier = secrets.token_urlsafe(48)
    reg = _register(client)
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    form = _authorize_params(client_id, mcp_oauth.pkce_s256(verifier)) | {"email": email, "password": password}
    resp = client.post("/oauth/admin/authorize", data=form)
    assert resp.status_code == 303, resp.text
    query = parse_qs(urlparse(resp.headers["location"]).query)
    assert query["state"] == ["st-1"]
    token = client.post(
        "/oauth/admin/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    return client_id, verifier, query["code"][0], token


def _rpc(client, token, method, params=None):
    return client.post(
        "/mcp-admin",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_unauthenticated_request_points_to_admin_oauth_metadata(client):
    resp = client.post("/mcp-admin", json={})
    assert resp.status_code == 401
    assert admin_oauth.metadata_url() in resp.headers["www-authenticate"]
    meta = client.get("/.well-known/oauth-protected-resource/mcp-admin").json()
    assert meta["resource"] == admin_oauth.resource_url()
    assert meta["authorization_servers"] == [admin_oauth.issuer()]
    for path in ("/.well-known/oauth-authorization-server/admin-oauth", "/.well-known/openid-configuration/admin-oauth"):
        server = client.get(path).json()
        assert server["issuer"] == admin_oauth.issuer()
        assert server["authorization_endpoint"].endswith("/oauth/admin/authorize")
        assert server["token_endpoint"].endswith("/oauth/admin/token")
        assert server["code_challenge_methods_supported"] == ["S256"]
        assert "mcp-admin" not in server["token_endpoint"]


def test_registration_only_accepts_trusted_redirects(client):
    assert _register(client).status_code == 201
    assert _register(client, "https://evil.example/callback").status_code == 400
    assert client.post("/oauth/admin/register", json={}).status_code == 400


def test_full_flow_tokens_work_and_are_hashed(client):
    _admin()
    _, _, _, token = _login_and_get_tokens(client)
    assert token.status_code == 200, token.text
    data = token.json()
    assert data["access_token"].startswith(admin_oauth.ACCESS_PREFIX)
    assert data["expires_in"] == admin_oauth.ACCESS_TTL_SEC
    assert token.headers["cache-control"] == "no-store"
    stored = str(hub.store.get_meta(admin_oauth.TOKENS_META)) + str(hub.store.get_meta(admin_oauth.CODES_META))
    assert data["access_token"] not in stored and data["refresh_token"] not in stored

    tools = _rpc(client, data["access_token"], "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"list_endpoints", "describe_endpoint", "call_api"}
    out = _rpc(
        client, data["access_token"], "tools/call", {"name": "call_api", "arguments": {"method": "GET", "path": "/api/admin/me"}}
    ).json()["result"]
    assert out["isError"] is False


def test_consent_page_is_not_frameable_and_hides_form_on_bad_request(client):
    reg = _register(client).json()
    ok = client.get("/oauth/admin/authorize", params=_authorize_params(reg["client_id"], "abc"))
    assert ok.status_code == 200
    assert 'type="password"' in ok.text and "Plná admin práva" in ok.text
    assert ok.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in ok.headers["content-security-policy"]
    bad = client.get("/oauth/admin/authorize", params=_authorize_params("unknown-client", "abc"))
    assert bad.status_code == 400 and 'type="password"' not in bad.text
    wrong_resource = client.get(
        "/oauth/admin/authorize", params=_authorize_params(reg["client_id"], "abc", resource="https://realitify.cz/mcp")
    )
    assert wrong_resource.status_code == 400 and 'type="password"' not in wrong_resource.text
    other_redirect = client.get(
        "/oauth/admin/authorize",
        params=_authorize_params(reg["client_id"], "abc", redirect="https://claude.ai/other"),
    )
    assert other_redirect.status_code == 400


def test_wrong_password_and_non_admin_get_no_code(client):
    _admin()
    if not user_registry.get_user_by_email(hub.store, "aoauth-plain@example.cz"):
        user_registry.create_user(hub.store, email="aoauth-plain@example.cz", password=PASSWORD, first="P", last="U")
    reg = _register(client).json()
    base = _authorize_params(reg["client_id"], mcp_oauth.pkce_s256("v" * 50))
    messages = set()
    for email, password in (("aoauth-admin@example.cz", "wrong"), ("aoauth-plain@example.cz", PASSWORD), ("nobody@example.cz", "x")):
        resp = client.post("/oauth/admin/authorize", data=base | {"email": email, "password": password})
        assert resp.status_code == 403, (email, resp.status_code)
        assert "location" not in resp.headers
        messages.add("E-mail nebo heslo nesedí" in resp.text)
    assert messages == {True}  # one message for all failures: no account enumeration


def test_env_admin_account_can_sign_in(client):
    from unittest.mock import patch

    from app import config

    with patch.object(config, "ADMIN_EMAIL", "aoauth-env@example.cz"), patch.object(config, "ADMIN_PASSWORD", "env-secret-pw"):
        _, _, _, token = _login_and_get_tokens(client, email="aoauth-env@example.cz", password="env-secret-pw")
    assert token.status_code == 200, token.text
    assert _rpc(client, token.json()["access_token"], "tools/list").status_code == 200


def test_password_guessing_is_rate_limited(client):
    _admin()
    reg = _register(client).json()
    form = _authorize_params(reg["client_id"], "c") | {"email": "aoauth-admin@example.cz", "password": "nope"}
    codes = [client.post("/oauth/admin/authorize", data=form).status_code for _ in range(8)]
    assert 429 in codes


def test_code_is_single_use_and_pkce_is_enforced(client):
    _admin()
    client_id, verifier, code, token = _login_and_get_tokens(client)
    assert token.status_code == 200
    replay = client.post(
        "/oauth/admin/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier},
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"

    # wrong verifier / wrong redirect on a fresh code
    verifier2 = secrets.token_urlsafe(48)
    form = _authorize_params(client_id, mcp_oauth.pkce_s256(verifier2)) | {"email": "aoauth-admin@example.cz", "password": PASSWORD}
    code2 = parse_qs(urlparse(client.post("/oauth/admin/authorize", data=form).headers["location"]).query)["code"][0]
    bad = client.post(
        "/oauth/admin/token",
        data={"grant_type": "authorization_code", "code": code2, "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": "x" * 50},
    )
    assert bad.status_code == 400
    # the failed attempt burned the code
    again = client.post(
        "/oauth/admin/token",
        data={"grant_type": "authorization_code", "code": code2, "redirect_uri": REDIRECT, "client_id": client_id, "code_verifier": verifier2},
    )
    assert again.status_code == 400


def test_refresh_rotates_and_reuse_revokes_the_grant(client):
    _admin()
    client_id, _, _, token = _login_and_get_tokens(client)
    first = token.json()
    rotated = client.post("/oauth/admin/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client_id})
    assert rotated.status_code == 200, rotated.text
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert _rpc(client, second["access_token"], "tools/list").status_code == 200
    # an already-rotated refresh token shows up again: treat as stolen, kill the whole grant
    replay = client.post("/oauth/admin/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client_id})
    assert replay.status_code == 400
    assert _rpc(client, second["access_token"], "tools/list").status_code == 401


def test_grants_can_be_listed_and_revoked_by_the_admin(client):
    admin = _admin()
    _, _, _, token = _login_and_get_tokens(client)
    access = token.json()["access_token"]
    client.cookies.set("realitify_admin", user_registry.create_session(hub.store, admin["id"]))
    grants = client.get("/api/admin/mcp-oauth").json()["grants"]
    assert grants and grants[-1]["client_name"] == "Claude"
    assert client.delete(f"/api/admin/mcp-oauth/{grants[-1]['id']}").status_code == 200
    assert client.delete(f"/api/admin/mcp-oauth/{grants[-1]['id']}").status_code == 404
    client.cookies.clear()
    assert _rpc(client, access, "tools/list").status_code == 401


def test_revoke_endpoint_kills_the_token(client):
    _admin()
    _, _, _, token = _login_and_get_tokens(client)
    access = token.json()["access_token"]
    assert _rpc(client, access, "tools/list").status_code == 200
    assert client.post("/oauth/admin/revoke", data={"token": access}).status_code == 200
    assert _rpc(client, access, "tools/list").status_code == 401


def test_demoted_admin_loses_access_immediately(client):
    admin = _admin("aoauth-demote@example.cz")
    _, _, _, token = _login_and_get_tokens(client, email="aoauth-demote@example.cz")
    access = token.json()["access_token"]
    assert _rpc(client, access, "tools/list").status_code == 200
    user_registry.set_role(hub.store, admin["id"], "user")
    assert _rpc(client, access, "tools/list").status_code == 401
    user_registry.set_role(hub.store, admin["id"], "admin")


def test_expired_access_token_is_rejected(client):
    _admin()
    _, _, _, token = _login_and_get_tokens(client)
    access = token.json()["access_token"]
    import json as _json

    rows = _json.loads(hub.store.get_meta(admin_oauth.TOKENS_META))
    for row in rows:
        row["access_exp"] = int(time.time()) - 1
    hub.store.set_meta(admin_oauth.TOKENS_META, _json.dumps(rows))
    assert _rpc(client, access, "tools/list").status_code == 401


def test_customer_oauth_tokens_cannot_open_admin_mcp_and_vice_versa(client):
    _admin()
    _, _, _, token = _login_and_get_tokens(client)
    admin_access = token.json()["access_token"]
    # an access token of the customer OAuth (any user) is not an admin credential
    fake_customer = mcp_oauth.TOKEN_PREFIX + secrets.token_urlsafe(32)
    assert _rpc(client, fake_customer, "tools/list").status_code == 401
    # and an admin token is no key for the customer MCP / REST API
    resp = client.post("/mcp", headers={"Authorization": f"Bearer {admin_access}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert resp.status_code == 401


def test_ai_cannot_manage_oauth_access_through_call_api(client):
    _admin()
    _, _, _, token = _login_and_get_tokens(client)
    access = token.json()["access_token"]
    for path, method in (("/api/admin/mcp-oauth", "GET"), ("/oauth/admin/register", "POST"), ("/api/admin/mcp-key", "POST")):
        out = _rpc(client, access, "tools/call", {"name": "call_api", "arguments": {"method": method, "path": path, "body": {}}}).json()["result"]
        assert out["isError"] is True, path
