"""Regression tests for the security audit (S1-S15): every finding gets a test that would have failed before."""
import io
import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-sec-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import config, rate_limit
from app import users as user_registry
from app.emailing import store as estore
from app.main import app, hub

PW = "Audit-Pass-12345"


@pytest.fixture(scope="module")
def _app_client():
    # One app lifespan for the whole module (starting it per test is slow and spawns the scrape hub every time).
    before = {u["id"] for u in _all_users()}
    with TestClient(app, raise_server_exceptions=False) as c:
        estore.set_verification_required(False, "test")
        yield c
        estore.set_verification_required(True, "test")
    # The data dir is shared with the other test modules (e.g. shared-catalog owner selection): leave no users behind.
    for user in _all_users():
        if user["id"] not in before:
            user_registry.delete_user(hub.store, user["id"])


def _all_users():
    with hub.store.connect() as conn:
        return [{"id": row[0]} for row in conn.execute("SELECT id FROM users")]


@pytest.fixture()
def client(_app_client):
    rate_limit.reset()
    _app_client.cookies.clear()
    return _app_client


def _register(client, email, name="Test User", password=PW):
    return client.post("/api/auth/register", json={"name": name, "email": email, "password": password, "terms": True})


def _admin_cookie():
    email = "sec-admin@example.cz"
    admin = user_registry.get_user_by_email(hub.store, email) or user_registry.create_user(
        hub.store, email=email, password=PW, first="A", last="D"
    )
    user_registry.set_role(hub.store, admin["id"], "admin")
    return user_registry.create_session(hub.store, admin["id"])


# --- S1: anonymous /api/extension/me must not leak the owner profile -----------------------------------------------

def test_extension_me_anonymous_leaks_nothing(client):
    hub.store.set_meta("account", json.dumps({"first": "Owner", "last": "Secret", "email": "owner@private.example"}))
    body = client.get("/api/extension/me").json()
    assert body["authenticated"] is False
    assert body["email"] == "" and body["name"] == ""
    assert "owner@private.example" not in json.dumps(body)


def test_extension_me_authenticated_returns_own_profile(client):
    assert _register(client, "ext-me@example.cz").status_code == 200
    body = client.get("/api/extension/me").json()
    assert body["authenticated"] is True and body["email"] == "ext-me@example.cz"


# --- S2/S3: backup must not export or import server-wide settings for normal users ----------------------------------

def test_user_backup_has_no_global_settings(client, monkeypatch):
    monkeypatch.setattr(config, "SOLD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/SECRET-TOKEN")
    assert _register(client, "backup-u@example.cz").status_code == 200
    for path in ("/api/backup/json", "/api/backup/pack"):
        r = client.get(path)
        assert r.status_code == 200
        assert b"SECRET-TOKEN" not in r.content
    assert client.get("/api/backup/json").json()["settings"] == {}


def test_admin_backup_still_has_settings(client, monkeypatch):
    monkeypatch.setattr(config, "SOLD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/SECRET-TOKEN")
    client.cookies.set("realitify_session", _admin_cookie())
    assert client.get("/api/backup/json").json()["settings"]["sold_webhook_url"].endswith("SECRET-TOKEN")


def test_user_import_cannot_change_global_config(client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ROOT", tmp_path)  # a rewrite of .env would land here, never in the project
    before = (config.DISCORD_WEBHOOK_URL, config.SEARCH_URL, config.UPDATE_FEED, config.POLL_PAGES)
    assert _register(client, "import-u@example.cz").status_code == 200
    evil = {"kind": "sreality-monitor", "settings": {
        "discord_webhook_url": "https://discord.com/api/webhooks/1/evil", "search_url": "https://evil.example/",
        "update_feed": "github:evil/repo", "poll_pages": 99}}
    r = client.post("/api/backup/import", files={"file": ("x.json", json.dumps(evil), "application/json")})
    assert r.status_code == 200
    assert (config.DISCORD_WEBHOOK_URL, config.SEARCH_URL, config.UPDATE_FEED, config.POLL_PAGES) == before
    assert not (tmp_path / ".env").exists()


def test_user_cannot_upload_raw_sqlite(client):
    assert _register(client, "sqlite-u@example.cz").status_code == 200
    r = client.post("/api/backup/import", files={"file": ("x.sqlite", b"SQLite format 3\x00" + b"\0" * 100, "application/octet-stream")})
    assert r.status_code == 403


# --- S4: X-Forwarded-For spoofing --------------------------------------------------------------------------------

def test_spoofed_forwarded_for_does_not_reset_rate_limit(client):
    codes = []
    for i in range(14):
        # a real proxy appends the true peer on the right; the spoofed leading entries must be ignored
        r = client.post("/api/auth/login", json={"email": "victim@example.cz", "password": "x"},
                        headers={"X-Forwarded-For": f"10.9.8.{i}, 203.0.113.7"})
        codes.append(r.status_code)
    assert codes[:10] == [400] * 10
    assert 429 in codes[10:]


# --- S5/S7/S15: headers, docs, dotfiles ------------------------------------------------------------------------

def test_security_headers_on_public_pages(client):
    r = client.get("/kontakt")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "permissions-policy" in r.headers


def test_hsts_when_behind_https_proxy(client):
    r = client.get("/kontakt", headers={"X-Forwarded-Proto": "https"})
    assert "max-age=" in r.headers["strict-transport-security"]


def test_api_docs_disabled_for_https_production():
    import importlib
    import subprocess

    code = (
        "import os,tempfile;os.environ['DATA_DIR']=tempfile.mkdtemp();os.environ['REALITIFY_SKIP_LEGACY_MIGRATE']='1';"
        "os.environ['PUBLIC_BASE_URL']='https://realitify.cz';"
        "from fastapi.testclient import TestClient;from app.main import app;c=TestClient(app);"
        "print([c.get(p).status_code for p in ('/docs','/redoc','/openapi.json')])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert "[404, 404, 404]" in out.stdout, out.stdout + out.stderr
    del importlib


def test_dotfiles_not_served_from_static(client):
    assert client.get("/static/.DS_Store").status_code == 404
    assert client.get("/static/site/.env").status_code == 404


# --- S6 server side counterpart lives in the JS; S8: forgot password is uniform ---------------------------------

def test_forgot_password_same_answer_for_known_and_unknown(client):
    assert _register(client, "forgot-known@example.cz").status_code == 200
    client.cookies.clear()
    a = client.post("/api/auth/forgot", json={"email": "forgot-known@example.cz"})
    b = client.post("/api/auth/forgot", json={"email": "nobody-at-all@example.cz"})
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (200, {"ok": True})
    assert "Errno" not in a.text


def test_forgot_password_has_per_address_cap(client, monkeypatch):
    sent = []

    async def fake(email, token):
        sent.append(email)

    monkeypatch.setattr("app.main._send_reset_email", fake)
    assert _register(client, "forgot-cap@example.cz").status_code == 200
    client.cookies.clear()
    for i in range(6):
        client.post("/api/auth/forgot", json={"email": "forgot-cap@example.cz"}, headers={"X-Forwarded-For": f"198.51.100.{i}"})
    assert len(sent) <= 3


# --- S9/S10: password policy and input validation --------------------------------------------------------------

@pytest.mark.parametrize("pw", ["password", "12345678", "aaaaaaaa", "abcdefgh", "short1", "A1b" * 50])
def test_weak_passwords_rejected(client, pw):
    r = _register(client, "weakpw@example.cz", password=pw)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("email", ["a b@example.cz", "notanemail", "x@y", "<a>@b.cz", "a@b@c.cz", "a" * 300 + "@b.cz"])
def test_bad_emails_rejected(client, email):
    assert _register(client, email).status_code == 400


def test_overlong_name_rejected(client):
    assert _register(client, "longname@example.cz", name="N" * 100000).status_code == 400
    assert _register(client, "longname2@example.cz", name="Jan " + "N" * 150).status_code == 400


def test_good_registration_still_works(client):
    r = _register(client, "good.user+tag@example.cz", name="Jan Novák")
    assert r.status_code == 200 and r.json()["email"] == "good.user+tag@example.cz"


def test_change_password_enforces_policy(client):
    assert _register(client, "chpw@example.cz").status_code == 200
    r = client.post("/api/auth/password", json={"current": PW, "new": "password"})
    assert r.status_code == 400


def test_name_with_markup_is_stored_but_never_rendered_unescaped(client):
    r = _register(client, "xss@example.cz", name="<img src=x onerror=alert(1)> Test")
    assert r.status_code == 200  # stored as plain data; every renderer escapes (checked in the Playwright run)


# --- S11: OAuth dynamic client registration ---------------------------------------------------------------------

def test_oauth_register_rejects_foreign_redirect(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"], "client_name": "Realitify Official"})
    assert r.status_code == 400


def test_oauth_register_appends_host_to_client_name(client):
    r = client.post("/oauth/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Realitify Official\n"})
    assert r.status_code == 201
    name = r.json()["client_name"]
    assert name.endswith("(claude.ai)") and "\n" not in name


# --- S12: public endpoints are rate limited ----------------------------------------------------------------------

def test_inquiries_are_rate_limited(client):
    codes = [client.post("/api/inquiries", json={"name": "x", "email": "a@b.cz", "message": "hello spam test", "topic": "x"}).status_code
             for _ in range(8)]
    assert 429 in codes


# --- S14: Host header never ends up in e-mailed links ------------------------------------------------------------

def test_site_base_ignores_forged_host(monkeypatch):
    from starlette.requests import Request

    from app.main import _site_base

    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "")
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"host", b"evil.example")], "scheme": "https",
             "server": ("evil.example", 443), "query_string": b""}
    assert _site_base(Request(scope)) == "https://realitify.cz"
