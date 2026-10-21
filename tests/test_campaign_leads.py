"""Kampaně: příjemci = registrovaní / leady / oba, odhlášení leadu."""
from __future__ import annotations

import pytest

from app import config, guest, users
from app.emailing import campaigns, service, tracking
from app.emailing import store as estore
from app.store import Store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    gstore = Store(tmp_path / "monitor.sqlite", seed_webhook_url="")
    estore.bind(gstore)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://realitify.test")
    leads = [
        {"email": "Lead@Example.cz", "ts": 3},
        {"email": "lead@example.cz", "ts": 2},
        {"email": "user@example.cz", "ts": 1},
        {"email": "blocked@example.cz", "ts": 0},
        {"email": "bad", "ts": 0},
    ]
    monkeypatch.setattr(guest, "list_leads", lambda limit=1000: leads)
    users.create_user(gstore, email="user@example.cz", password="Heslo12345!", first="Jan", last="Novák")
    estore.suppress("blocked@example.cz", "unsubscribed")
    yield gstore
    estore._bound = None
    estore._schema_ready.clear()


def emails(seg):
    return sorted(p["email"] for p in campaigns.audience(seg, None))


def test_audience_modes(env):
    assert campaigns.normalize_segment({})["audience"] == "users"
    assert emails({"verified": "any"}) == ["user@example.cz"]
    assert emails({"audience": "leads"}) == ["lead@example.cz"]  # deduped, registered and suppressed ones dropped
    assert emails({"audience": "both", "verified": "any"}) == ["lead@example.cz", "user@example.cz"]


def test_lead_id_roundtrip_and_unsubscribe_token(env):
    lid = tracking.lead_id("Lead@Example.cz")
    assert tracking.lead_email(lid) == "lead@example.cz"
    assert tracking.lead_email("abc123") is None
    assert tracking.parse_unsubscribe_token(tracking.unsubscribe_token(lid)) == lid


def test_lead_mail_has_lead_footer_and_unsubscribe(env):
    person = campaigns.audience({"audience": "leads"}, None)[0]
    camp = {"mode": "html", "subject": "S", "preheader": "", "body": "<p>Ahoj</p>"}
    rendered, _ = campaigns.render_campaign(camp, person, person["email"])
    assert "zadali svůj e-mail" in rendered.html and "máte účet" not in rendered.html
    assert "/odhlasit?token=" + tracking.lead_id("lead@example.cz") in rendered.html.replace("%2E", ".")


def test_unsubscribe_suppresses_lead(env):
    estore.suppress("lead@example.cz", "unsubscribed")
    assert emails({"audience": "leads"}) == []
    estore.unsuppress("lead@example.cz")
    assert emails({"audience": "leads"}) == ["lead@example.cz"]


def test_alerts_pause_blocks_only_alerts(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", False)
    monkeypatch.setattr(config, "EMAIL_ALERTS_PAUSED", True)
    assert service.paused_stream("alerts") is True
    assert service.paused_stream("alerts", {"test": True}) is False
    for stream in ("lifecycle", "transactional"):
        assert service.paused_stream(stream) is False
    monkeypatch.setattr(config, "EMAIL_ALERTS_PAUSED", False)
    assert service.paused_stream("alerts") is False


def test_excluded_addresses_are_dropped_everywhere(env):
    seg = {"audience": "both", "verified": "any", "exclude_emails": [" LEAD@example.cz ", "user@example.cz", "bad"]}
    assert campaigns.normalize_segment(seg)["exclude_emails"] == ["lead@example.cz", "user@example.cz"]
    assert emails(seg) == []
    assert emails({"audience": "both", "verified": "any", "exclude_emails": "user@example.cz; x@y.cz"}) == ["lead@example.cz"]
    assert campaigns.audience_preview(seg, None)["count"] == 0
