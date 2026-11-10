"""Admin switch "Vyžadovat ověření e-mailu": store, admin API, /api/auth/me and the write-blocking middleware."""
import os
import sys
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-vsw-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import rate_limit
from app import users as user_registry
from app.emailing import store as estore
from app.main import app, hub


@pytest.fixture()
def client():
    rate_limit.reset()
    with TestClient(app, raise_server_exceptions=False) as c:
        estore.set_verification_required(True, "test")
        yield c
        estore.set_verification_required(True, "test")


def _admin_cookie():
    admin = user_registry.create_user(hub.store, email="vsw-admin@example.cz", password="Heslo12345!", first="A", last="D") \
        if not user_registry.get_user_by_email(hub.store, "vsw-admin@example.cz") else user_registry.get_user_by_email(hub.store, "vsw-admin@example.cz")
    user_registry.set_role(hub.store, admin["id"], "admin")
    return user_registry.create_session(hub.store, admin["id"])


def _register(client, email):
    r = client.post("/api/auth/register", json={"name": "Test U", "email": email, "password": "testpassword123", "terms": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_store_default_is_on_and_set_persists_with_who_and_when():
    estore.bind(hub.store)
    assert estore.verification_required() is estore.verification_default()
    out = estore.set_verification_required(False, "admin@realitify.cz")
    assert out["required"] is False and out["updated_by"] == "admin@realitify.cz" and out["updated_at"]
    assert estore.verification_required() is False
    estore.set_verification_required(True, "admin@realitify.cz")
    assert estore.verification_required() is True


def test_admin_api_requires_admin_and_validates(client):
    assert client.get("/api/admin/settings/email-verification").status_code == 401
    assert client.post("/api/admin/settings/email-verification", json={"required": False}).status_code == 401
    client.cookies.set("realitify_admin", _admin_cookie())
    assert client.get("/api/admin/settings/email-verification").json()["required"] is True
    assert client.post("/api/admin/settings/email-verification", json={"required": "no"}).status_code == 400
    r = client.post("/api/admin/settings/email-verification", json={"required": False})
    assert r.status_code == 200 and r.json()["required"] is False and r.json()["updated_by"]
    assert client.get("/api/admin/emails/status").json()["verification"]["required"] is False


def test_middleware_blocks_unverified_writes_only_while_required(client):
    created = _register(client, "vsw-user@example.cz")
    assert created["email_verified"] is False and created["email_verification_required"] is True
    assert client.get("/api/auth/me").json()["email_verification_required"] is True
    blocked = client.post("/api/monitors", json={})
    assert blocked.status_code == 403 and blocked.json().get("verification_required") is True

    estore.set_verification_required(False, "test")
    assert client.get("/api/auth/me").json()["email_verification_required"] is False
    assert client.post("/api/monitors", json={}).status_code != 403
    # the account itself stays unverified
    assert client.get("/api/auth/me").json()["email_verified"] is False

    estore.set_verification_required(True, "test")
    assert client.post("/api/monitors", json={}).status_code == 403


def test_register_and_login_report_the_switch(client):
    estore.set_verification_required(False, "test")
    created = _register(client, "vsw-user2@example.cz")
    assert created["email_verified"] is False and created["email_verification_required"] is False
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": "vsw-user2@example.cz", "password": "testpassword123"})
    assert r.status_code == 200 and r.json()["email_verification_required"] is False


APP_PAGES = ["/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"]


def test_unverified_user_cannot_enter_app_pages_or_read_api(client):
    client.follow_redirects = False
    _register(client, "gate-unverified@example.cz")
    for page in APP_PAGES:
        r = client.get(page)
        assert r.status_code == 303 and r.headers["location"] == "/overeni", page
    for api in ("/api/monitors", "/api/billing", "/api/listings"):
        r = client.get(api)
        assert r.status_code == 403 and r.json()["verification_required"] is True, api
    # the verification flow itself stays reachable
    assert client.get("/overeni").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    # extension header is not a way around the gate either
    session = client.cookies.get("realitify_session")
    client.cookies.clear()
    assert client.get("/api/extension/scores", headers={"x-realitify-session": session}).status_code in (403, 405)


def test_verified_user_or_switch_off_gets_in(client):
    client.follow_redirects = False
    _register(client, "gate-verified@example.cz")
    user = user_registry.get_user_by_email(hub.store, "gate-verified@example.cz")
    assert client.get("/prehled").status_code == 303
    estore.set_verification_required(False, "test")
    estore.verification_required.__globals__["_verify_cache"].update(at=0)
    assert client.get("/prehled").status_code == 200
    estore.set_verification_required(True, "test")
    assert client.get("/prehled").status_code == 303
    user_registry.mark_email_verified(hub.store, user["id"])
    assert client.get("/prehled").status_code == 200
    assert client.get("/api/monitors").status_code == 200


def test_exempt_prefix_does_not_leak_to_similar_api_paths(client):
    """'/api/t' used to be a prefix exemption and also let /api/templates through."""
    _register(client, "gate-templates@example.cz")
    for method, path in (("get", "/api/templates"), ("post", "/api/templates"), ("delete", "/api/templates/x")):
        r = getattr(client, method)(path)
        assert r.status_code == 403 and r.json().get("verification_required") is True, (method, path)


def test_unverified_cannot_get_mcp_access_via_oauth_or_claim_pages(client):
    client.follow_redirects = False
    _register(client, "gate-oauth@example.cz")
    for method, path in (("get", "/oauth/authorize"), ("post", "/oauth/authorize"), ("get", "/agent/claim"),
                         ("post", "/agent/claim"), ("get", "/hledat/katalog")):
        r = getattr(client, method)(path)
        assert r.status_code == 303 and r.headers["location"] == "/overeni", (method, path)


def test_nobody_can_self_register_the_admin_address(client):
    from app import config
    r = client.post("/api/auth/register", json={"name": "Evil Admin", "email": config.ADMIN_EMAIL or "admin@realitify.cz",
                                                "password": "testpassword123", "terms": True})
    assert r.status_code == 400
    assert user_registry.get_user_by_email(hub.store, (config.ADMIN_EMAIL or "admin@realitify.cz")) is None \
        or user_registry.get_user_by_email(hub.store, config.ADMIN_EMAIL)["role"] == "admin"


def test_dev_verify_code_needs_explicit_localhost_base_url():
    from app.config import dev_verify_code
    assert dev_verify_code("", "http://127.0.0.1:8080") == ""          # variable missing -> default localhost
    assert dev_verify_code("https://realitify.cz", "https://realitify.cz") == ""
    assert dev_verify_code("http://127.0.0.1:8080", "http://127.0.0.1:8080") == "000000"


def test_unverified_email_can_be_registered_again_but_verified_cannot(client):
    estore.set_verification_required(True, "test")
    first = _register(client, "squat-owner@example.cz")
    assert first["email_verified"] is False
    client.cookies.clear()
    second = _register(client, "squat-owner@example.cz")  # the real owner: no "already registered"
    assert second["id"] != first["id"]
    user_registry.mark_email_verified(hub.store, second["id"])
    client.cookies.clear()
    r = client.post("/api/auth/register", json={"name": "Test U", "email": "squat-owner@example.cz", "password": "testpassword123", "terms": True})
    assert r.status_code == 400 and "už je registrovaný" in r.text


def test_avatar_preset_custom_and_clear(client):
    import base64, io
    from PIL import Image
    estore.set_verification_required(False, "test")
    me = _register(client, "avatar-user@example.cz")
    assert me["avatar"].endswith("/bara/001.webp")
    assert client.post("/api/auth/avatar", json={"preset": "999"}).status_code == 400
    r = client.post("/api/auth/avatar", json={"preset": 12})
    assert r.status_code == 200 and r.json()["avatar"].endswith("/bara/012.webp")
    assert client.get("/api/auth/me").json()["avatar"].endswith("/bara/012.webp")
    buf = io.BytesIO(); Image.new("RGB", (300, 200), (200, 50, 50)).save(buf, "PNG")
    url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    r = client.post("/api/auth/avatar", json={"image": url})
    assert r.status_code == 200 and r.json()["avatar"].startswith("/api/auth/avatar?v=")
    got = client.get("/api/auth/avatar")
    assert got.status_code == 200 and Image.open(io.BytesIO(got.content)).size == (256, 256)
    assert client.post("/api/auth/avatar", json={"image": "data:image/png;base64,AAAA"}).status_code == 400
    assert client.post("/api/auth/avatar", json={"clear": True}).json()["avatar"].endswith("/bara/001.webp")
