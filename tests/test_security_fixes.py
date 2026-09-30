"""Testy bezpečnostních a provozních oprav (2026-09-30)."""
import os
import sys
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-sec-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import rate_limit
from app import users as user_registry
from app.main import app


@pytest.fixture()
def client():
    rate_limit.reset()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _register(client, email="sec-test@example.com"):
    return client.post(
        "/api/auth/register",
        json={"name": "Test Uživatel", "email": email, "password": "testpassword123"},
    )


# --- admin gating scrape-control endpointů -----------------------------------

@pytest.mark.parametrize("path", ["/api/monitor/start", "/api/monitor/stop", "/api/monitor/check", "/api/catalog/sync"])
def test_scrape_controls_require_admin(client, path):
    r = client.post(path, json={})
    assert r.status_code == 401, f"{path} není chráněný: {r.status_code}"


def test_monitor_check_requires_login(client):
    r = client.post("/api/monitor/check", json={"monitor_id": "x"})
    assert r.status_code == 401


# --- push endpointy ----------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/push/subscribe", "/api/push/unsubscribe", "/api/push/test"])
def test_push_endpoints_require_login(client, path):
    r = client.post(path, json={})
    assert r.status_code == 401, f"{path} není chráněný: {r.status_code}"


def test_push_vapid_requires_login(client):
    r = client.get("/api/push/vapid")
    assert r.status_code == 401


# --- /api/update, /api/perf/diag --------------------------------------------

def test_update_requires_admin(client):
    r = client.post("/api/update")
    assert r.status_code == 401


def test_perf_diag_requires_admin(client):
    r = client.get("/api/perf/diag")
    assert r.status_code == 401


# --- rate limiting -----------------------------------------------------------

def test_login_rate_limited(client):
    # 30/min z IP; překročíme limit
    for _ in range(31):
        r = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "x" * 12})
    assert r.status_code == 429, f"očekáváno 429, je {r.status_code}"


def test_register_rate_limited(client):
    for i in range(6):
        r = client.post(
            "/api/auth/register",
            json={"name": "Test", "email": f"rl{i}@example.com", "password": "testpassword123"},
        )
    assert r.status_code == 429, f"očekáváno 429, je {r.status_code}"


# --- sjednocené login chyby ---------------------------------------------------

def test_login_same_error_unknown_vs_wrong(client):
    rate_limit.reset()
    r1 = client.post("/api/auth/login", json={"email": "neexistuje-xyz@example.com", "password": "testpassword123"})
    _register(client, "login-diff@example.com")
    rate_limit.reset()
    r2 = client.post("/api/auth/login", json={"email": "login-diff@example.com", "password": "spatneheslo999"})
    assert r1.status_code == 400 and r2.status_code == 400
    assert r1.json()["detail"] == r2.json()["detail"], "login prozrazuje existenci účtu"


# --- Stripe webhook fail-closed ----------------------------------------------

def test_webhook_rejects_without_secret(client):
    r = client.post("/api/billing/webhook", content=b'{"type":"x"}', headers={"stripe-signature": "sig"})
    # Bez STRIPE_WEBHOOK_SECRET (test env) musí webhook odmítnout, ne přijmout unsigned JSON
    assert r.status_code in (400, 401, 422), f"webhook není fail-closed: {r.status_code}"


# --- email verification ------------------------------------------------------

def test_register_creates_unverified_user(client):
    r = _register(client, "verify-me@example.com")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("email_verified") is False, "nový uživatel by neměl být ověřený"


def test_verify_email_flow():
    from app.main import hub

    user = user_registry.create_user(hub.store, email="verify-flow@example.com", password="testpassword123", first="V", last="F")
    assert user_registry.public_user(user)["email_verified"] is False
    token = user_registry.create_email_verification(hub.store, user["id"])
    assert user_registry.verify_email_with_token(hub.store, "spatny-token") is None
    verified = user_registry.verify_email_with_token(hub.store, token)
    assert verified is not None
    assert user_registry.public_user(verified)["email_verified"] is True
    # Token je single-use
    assert user_registry.verify_email_with_token(hub.store, token) is None


# --- rate_limit unit ----------------------------------------------------------

def test_rate_limit_unit():
    rate_limit.reset()
    assert rate_limit.check("s", "k", 2, 60)
    assert rate_limit.check("s", "k", 2, 60)
    assert not rate_limit.check("s", "k", 2, 60)
    assert rate_limit.check("s", "jiny-klic", 2, 60)
