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
    assert estore.verification_required() is True
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
