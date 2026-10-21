"""Greeting in e-mails uses the hand-entered 5th case ("Adélo"); campaign preview renders like a recipient sees it."""
from __future__ import annotations

import pytest

from app import config, users
from app.emailing import campaigns, service
from app.emailing import store as estore
from app.store import Store


@pytest.fixture()
def gstore(tmp_path, monkeypatch):
    store = Store(tmp_path / "g.sqlite", seed_webhook_url="")
    estore.bind(store)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://realitify.test")
    yield store
    estore._bound = None
    estore._schema_ready.clear()


def test_greeting_uses_vocative_and_never_the_nominative(gstore):
    user = users.create_user(gstore, email="a@ex.cz", password="Heslo12345!", first="Adéla", last="Nováková")
    assert service.common_vars(users.get_user_by_id(gstore, user["id"]), "a@ex.cz")["osloveni"] == "Dobrý den,"
    users.set_vocatives(gstore, {user["id"]: "  Adélo "})
    loaded = users.get_user_by_id(gstore, user["id"])
    assert loaded["first_vocative"] == "Adélo"
    vars_ = service.common_vars(loaded, "a@ex.cz")
    assert vars_["osloveni"] == "Dobrý den, Adélo,"
    assert vars_["jmeno"] == "Adéla"  # {{jmeno}} stays the name as written


def test_campaign_audience_carries_the_vocative(gstore):
    user = users.create_user(gstore, email="a@ex.cz", password="Heslo12345!", first="Adéla", last="N")
    users.set_vocatives(gstore, {user["id"]: "Adélo"})
    person = campaigns.audience({"verified": "any"}, None)[0]
    camp = {"mode": "html", "subject": "S", "preheader": "", "body": "<p>{{osloveni}}</p>"}
    rendered, _ = campaigns.render_campaign(camp, person, "a@ex.cz")
    assert "Dobrý den, Adélo," in rendered.html


def test_suggestions():
    s = users.suggest_vocative
    assert [s(n) for n in ("Adéla", "Jan", "Petr", "Marek", "Zdeněk", "Jiří", "Tomáš", "Ondřej", "Marie")] == [
        "Adélo", "Jane", "Petře", "Marku", "Zdeňku", "Jiří", "Tomáši", "Ondřeji", "Marie"]
    assert s("") == ""


def test_preview_resolves_variables_logo_and_images(gstore):
    camp = {"mode": "html", "subject": "Předmět {{jmeno}}", "preheader": "Pre",
            "body": "<html><body><img src=\"{{base_url}}/static/site/assets/email-pro-free.png\"><p>{{osloveni}}</p></body></html>"}
    out = campaigns.preview(camp)
    assert "{{" not in out["html"]
    assert 'src="https://realitify.test/static/site/assets/email-pro-free.png"' in out["html"]
    assert "Dobrý den, Jiří," in out["html"]
    assert out["subject"] == "Předmět Jiří"
