"""Automatic e-mails: rendering, queue and delivery, dedupe, unsubscribe, click safety, campaigns, triggers, admin API."""
from __future__ import annotations

import json
import smtplib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import config, users
from app.emailing import admin_api, campaigns, catalog, render, service, tracking, triggers
from app.emailing import store as estore
from app.store import Store


@pytest.fixture()
def env(tmp_path, monkeypatch):
    gstore = Store(tmp_path / "monitor.sqlite", seed_webhook_url="")
    estore.bind(gstore)
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(config, "SMTP_FROM", "Realitify <noreply@realitify.cz>")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://realitify.test")
    monkeypatch.setattr(config, "EMAIL_FROM_TRANSACTIONAL", "")
    monkeypatch.setattr(config, "EMAIL_FROM_ALERTS", "")
    monkeypatch.setattr(config, "EMAIL_FROM_LIFECYCLE", "")
    sent: list[Any] = []
    monkeypatch.setattr(service, "deliver", lambda msg: sent.append(msg))
    stores: dict[str, Store] = {}

    def store_for(uid: str) -> Store:
        if uid not in stores:
            stores[uid] = Store(tmp_path / "users" / uid / "monitor.sqlite", seed_webhook_url="")
        return stores[uid]

    class Env:
        pass

    e = Env()
    e.gstore, e.sent, e.store_for, e.tmp = gstore, sent, store_for, tmp_path
    triggers._STATE["store_for"] = store_for
    triggers._STATE["last"] = {}
    yield e
    estore._bound = None
    estore._schema_ready.clear()


def make_user(env, email="jiri@example.cz", first="Jiří", verified=True):
    user = users.create_user(env.gstore, email=email, password="Heslo12345!", first=first, last="Kolb")
    if verified:
        users.mark_email_verified(env.gstore, user["id"])
        user = users.get_user_by_id(env.gstore, user["id"])
    return user


# ---------------------------------------------------------------- render --

def test_render_escapes_plain_vars_and_keeps_raw_blocks():
    out = render.render(
        "<html><body><p>{{jmeno}}</p>{{{radky_html}}}<p>{{neznama}}</p></body></html>",
        "Ahoj {{jmeno}}",
        "Pre {{jmeno}}",
        {"jmeno": "<b>Jiří</b>", "radky_html": "<tr><td>x</td></tr>"},
    )
    assert "&lt;b&gt;Jiří&lt;/b&gt;" in out.html
    assert "<tr><td>x</td></tr>" in out.html
    assert "{{" not in out.html
    assert out.subject == "Ahoj <b>Jiří</b>"  # subjects are plain text, never HTML-escaped
    assert out.html.index("Pre ") < out.html.index("Jiří")  # preheader span right after <body>


def test_text_part_turns_links_into_urls():
    text = render.html_to_text('<body><p>Ahoj</p><a href="https://x.cz/a">Otevřít</a><ul><li>jedna</li></ul></body>')
    assert "Otevřít: https://x.cz/a" in text and "- jedna" in text and "<" not in text


def test_every_template_renders_with_sample_data():
    for tdef in catalog.TEMPLATES:
        out = render.render(catalog.read_default_html(tdef.key), tdef.subject, tdef.preheader, catalog.sample_vars(tdef.key))
        assert "{{" not in out.html and "{{" not in out.subject, tdef.key
        assert out.subject.strip() and out.text.strip(), tdef.key


# ----------------------------------------------------------- send / queue --

def test_transactional_is_sent_at_once_from_its_own_sender(env):
    user = make_user(env)
    log_id = service.send_template("verify_email", user=user, variables={"odkaz": "https://realitify.test/overeni?token=abc"})
    row = estore.log_get(log_id)
    assert row["status"] == "sent" and row["stream"] == "transactional"
    msg = env.sent[0]
    assert "noreply@realitify.cz" in msg["From"] and msg["To"] == user["email"]
    assert "https://realitify.test/overeni?token=abc" in msg.get_body(("html",)).get_content()
    assert msg["List-Unsubscribe"] is None


def test_streams_use_their_senders_and_alerts_are_not_unsubscribable(env):
    assert "alerty@realitify.cz" in service.sender_for("alerts")
    assert "ahoj@realitify.cz" in service.sender_for("lifecycle")
    assert "noreply@realitify.cz" in service.sender_for("transactional")


def test_lifecycle_is_queued_and_carries_unsubscribe_headers(env):
    user = make_user(env)
    log_id = service.send_template("welcome", user=user, variables={"odkaz": "https://realitify.test/monitory"})
    assert estore.log_get(log_id)["status"] == "queued" and not env.sent
    service.process_queue()
    msg = env.sent[0]
    assert "ahoj@realitify.cz" in msg["From"]
    assert tracking.unsubscribe_url(user["id"]) in msg["List-Unsubscribe"]
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    html = msg.get_body(("html",)).get_content()
    assert "/e/o/" in html  # open pixel
    assert estore.log_get(log_id)["status"] == "sent"


def test_dedupe_key_blocks_a_second_send(env):
    user = make_user(env)
    first = service.send_template("password_changed", user=user, dedupe_key="pw:1", variables={"datum_cas": "dnes", "odkaz": "https://x.cz"})
    second = service.send_template("password_changed", user=user, dedupe_key="pw:1", variables={"datum_cas": "dnes", "odkaz": "https://x.cz"})
    assert first and second is None and len(env.sent) == 1


def test_disabled_template_is_skipped_but_forced_test_goes_out(env):
    user = make_user(env)
    tdef = catalog.get_template("welcome")
    estore.save_override("welcome", {"subject": tdef.subject, "html": catalog.read_default_html("welcome"), "enabled": False})
    log_id = service.send_template("welcome", user=user, variables={"odkaz": "https://x.cz"})
    assert estore.log_get(log_id)["status"] == "skipped" and estore.log_get(log_id)["error"] == "disabled"
    forced = service.send_template("welcome", user=user, force=True, variables={"odkaz": "https://x.cz"})
    service.process_queue()
    assert estore.log_get(forced)["status"] == "sent"


def test_override_replaces_subject_and_html(env):
    user = make_user(env)
    estore.save_override("password_changed", {"subject": "Nové heslo pro {{jmeno}}", "html": "<html><body>Změna {{datum_cas}}</body></html>"})
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "dnes"})
    row = estore.log_get(log_id)
    assert row["subject"] == "Nové heslo pro Jiří" and "Změna dnes" in row["html"]
    estore.delete_override("password_changed")
    assert service.effective("password_changed")["customized"] is False


def test_unsubscribed_user_gets_no_lifecycle_but_still_alerts_and_transactional(env):
    user = make_user(env)
    users.set_marketing_unsub(env.gstore, user["id"], True)
    welcome = service.send_template("welcome", user=user, variables={"odkaz": "https://x.cz"})
    assert estore.log_get(welcome)["error"] == "marketing_unsub"
    ok = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    assert estore.log_get(ok)["status"] == "sent"


def test_unsubscribe_after_queueing_still_stops_the_mail(env):
    user = make_user(env)
    log_id = service.send_template("welcome", user=user, variables={"odkaz": "https://x.cz"})
    users.set_marketing_unsub(env.gstore, user["id"], True)
    service.process_queue()
    assert not env.sent and estore.log_get(log_id)["status"] == "skipped"


def test_failed_delivery_backs_off_then_fails(env, monkeypatch):
    user = make_user(env)

    def boom(msg):
        raise OSError("smtp down")

    monkeypatch.setattr(service, "deliver", boom)
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    row = estore.log_get(log_id)
    assert row["status"] == "queued" and row["attempts"] == 1 and row["scheduled_at"] > estore.now_iso()
    for _ in range(2):
        estore.log_update(log_id, scheduled_at=estore.now_iso())
        service.process_now(log_id)
    assert estore.log_get(log_id)["status"] == "failed" and estore.log_get(log_id)["attempts"] == 3
    assert service.retry(log_id) and estore.log_get(log_id)["status"] == "queued"


def test_refused_recipient_fails_immediately(env, monkeypatch):
    user = make_user(env)

    def refuse(msg):
        raise smtplib.SMTPRecipientsRefused({"x": (550, b"no such user")})

    monkeypatch.setattr(service, "deliver", refuse)
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    assert estore.log_get(log_id)["status"] == "failed"


def test_claim_is_atomic(env):
    user = make_user(env)
    log_id = service.send_template("welcome", user=user, variables={"odkaz": "https://x.cz"})
    assert estore.claim(log_id) is True and estore.claim(log_id) is False


def test_smtp_not_configured_is_logged_as_skipped(env, monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "")
    user = make_user(env)
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    assert estore.log_get(log_id)["error"] == "smtp_not_configured"


# --------------------------------------------------------------- tracking --

def test_tokens_are_signed(env):
    token = tracking.log_token(7)
    assert tracking.parse_log_token(token) == 7
    assert tracking.parse_log_token("7.deadbeef") is None
    assert tracking.parse_log_token("8." + token.split(".")[1]) is None
    assert tracking.parse_unsubscribe_token(tracking.unsubscribe_token("u1")) == "u1"
    assert tracking.parse_unsubscribe_token("u1.bad") is None
    assert tracking.parse_action(tracking.action_url("u1", "found").split("/e/a/")[1].split("?")[0], "found") == "u1"
    assert tracking.parse_action(tracking.action_url("u1", "found").split("/e/a/")[1].split("?")[0], "pauza") is None


def test_click_redirect_only_to_urls_inside_the_mail(env):
    user = make_user(env)
    log_id = service.send_template("welcome", user=user, variables={"odkaz": "https://realitify.test/monitory"})
    service.process_queue()
    html = env.sent[0].get_body(("html",)).get_content()
    assert "/e/c/" in html
    token = tracking.log_token(log_id)
    assert tracking.resolve_click(token, "https://realitify.test/monitory") == "https://realitify.test/monitory"
    assert estore.log_get(log_id)["click_count"] == 1 and estore.log_get(log_id)["opened_at"]
    # open redirect attempts
    assert tracking.resolve_click(token, "https://evil.example/phish") is None
    assert tracking.resolve_click(token, "javascript:alert(1)") is None
    assert tracking.resolve_click("1.forged", "https://realitify.test/monitory") is None


def test_unsubscribe_and_resubscribe_links_are_not_rewritten(env):
    out = tracking.instrument(
        '<body><a href="https://realitify.test/odhlasit?token=x">u</a><a href="https://a.cz">a</a></body>', 5, pixel=True, clicks=True
    )
    assert 'href="https://realitify.test/odhlasit?token=x"' in out and "/e/c/" in out and "/e/o/" in out


def test_open_pixel_counts(env):
    user = make_user(env)
    log_id = service.send_template("alerts_unopened", user=user, variables={"odkaz": "https://x.cz"})
    tracking.record_open(tracking.log_token(log_id))
    tracking.record_open(tracking.log_token(log_id))
    row = estore.log_get(log_id)
    assert row["open_count"] == 2 and row["opened_at"]


def test_unsubscribe_page_flow(env):
    from app.main import app as main_app, hub

    with TestClient(main_app) as client:
        user = users.create_user(hub.store, email="unsub@example.cz", password="Heslo12345!", first="Jiří", last="Kolb")
        token = tracking.unsubscribe_token(user["id"])
        assert client.get(f"/odhlasit?token={quote(token)}").status_code == 200
        assert client.get("/odhlasit?token=forged").status_code == 400
        assert client.post(f"/odhlasit?token={quote(token)}").status_code == 200
        assert users.get_user_by_id(hub.store, user["id"])["marketing_unsub"] is True
        client.post(f"/odhlasit?token={quote(token)}", data={"action": "resubscribe"})
        assert users.get_user_by_id(hub.store, user["id"])["marketing_unsub"] is False


# -------------------------------------------------------------- campaigns --

def test_campaign_segment_and_send(env):
    a = make_user(env, "a@example.cz", "Anna")
    b = make_user(env, "b@example.cz", "Boris")
    c = make_user(env, "c@example.cz", "Cyril")
    make_user(env, "d@example.cz", "Dita", verified=False)
    users.set_marketing_unsub(env.gstore, c["id"], True)
    preview = campaigns.audience_preview({"verified": "yes"}, env.store_for)
    assert preview["count"] == 2  # unsubscribed and unverified are left out
    cid = estore.campaign_save({
        "name": "Test", "mode": "text", "subject": "Novinky pro {{jmeno}}", "body": "Ahoj {{jmeno}}!",
        "segment": {"verified": "yes"},
    })
    started = campaigns.start(cid, env.store_for)
    assert started["total"] == 2
    service.process_queue()
    assert len(env.sent) == 2
    msg = next(m for m in env.sent if m["To"] == a["email"])
    assert msg["Subject"] == "Novinky pro Anna"
    assert msg.get_body(("html",)) is None  # text mode = plain text only
    assert "Odhlásit z tipů a novinek" in msg.get_content()
    done = campaigns.run_due(env.store_for) or estore.campaign_get(cid)
    assert estore.campaign_get(cid)["status"] == "sent" and estore.campaign_get(cid)["stats"]["sent"] == 2
    with pytest.raises(ValueError):
        campaigns.start(cid, env.store_for)  # cannot be started twice
    assert b["email"] in [m["To"] for m in env.sent]


def test_html_campaign_gets_footer_and_scheduled_one_waits(env):
    make_user(env)
    cid = estore.campaign_save({"name": "H", "mode": "html", "subject": "S", "body": "<h1>Ahoj</h1>", "segment": {},
                                "scheduled_at": campaigns.normalize_when((datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"))})
    result = campaigns.schedule_or_send(cid, env.store_for)
    assert result["status"] == "scheduled" and not env.sent
    estore.campaign_update(cid, scheduled_at=estore.iso_in(-5))
    campaigns.run_due(env.store_for)
    service.process_queue()
    html = env.sent[0].get_body(("html",)).get_content()
    assert "<h1>Ahoj</h1>" in html and "/odhlasit?token=" in html


def test_campaign_cancel_stops_queued_mails(env):
    make_user(env)
    cid = estore.campaign_save({"name": "C", "mode": "text", "subject": "S", "body": "B", "segment": {}})
    campaigns.start(cid, env.store_for)
    campaigns.cancel(cid)
    service.process_queue()
    assert not env.sent and estore.campaign_get(cid)["status"] == "canceled"


def test_segment_by_plan_needs_user_stores(env):
    make_user(env)
    with pytest.raises(ValueError):
        campaigns.audience({"plans": ["pro"]}, None)
    assert campaigns.audience({"plans": ["free"]}, env.store_for)
    assert campaigns.audience({"plans": ["pro"]}, env.store_for) == []


# ---------------------------------------------------------------- triggers --

def test_on_verified_sends_welcome_once(env):
    user = make_user(env)
    triggers.on_verified(user)
    triggers.on_verified(user)
    service.process_queue()
    assert [m["Subject"] for m in env.sent].count(catalog.get_template("welcome").subject.replace("{{", "").replace("}}", "")) <= 1
    assert len(env.sent) == 1 and estore.get_meta(f"verified:{user['id']}")


def test_onboarding_nudge_only_without_dog_and_after_a_day(env, monkeypatch):
    user = make_user(env)
    estore.set_meta(f"verified:{user['id']}", (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat())
    ctx = triggers._Ctx(env.store_for)
    monkeypatch.setattr(ctx, "rows", lambda days: [{"locality": "Praha 2", "url": "https://x"}] * 5)
    triggers._rule_onboarding(ctx)
    keys = [r["template_key"] for r in estore.log_query()["items"]]
    assert keys == ["nudge_no_dog"]
    # with a dog the nudge is not sent again and the confirmation goes out instead
    env.store_for(user["id"]).save_monitor({"id": "default", "name": "Praha 2, 2+kk", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/praha-2"})
    triggers._rule_onboarding(ctx)
    keys = [r["template_key"] for r in estore.log_query()["items"]]
    assert "first_dog_confirm" in keys and keys.count("nudge_no_dog") == 1


def test_trial_started_schedules_reminder_and_guard_cancels_it(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    end = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    before = {"plan": "free", "status": "active"}
    ustore.save_billing_record({"plan": "start", "status": "trialing", "subscription_id": "sub_1", "current_period_end": end})
    triggers.on_billing_event(ustore, "sync", None, before, ustore.billing_record())
    items = {r["template_key"]: r for r in estore.log_query()["items"]}
    assert items["trial_started"]["status"] == "sent"
    assert items["trial_ending"]["status"] == "queued" and items["trial_ending"]["scheduled_at"] > estore.now_iso()
    # duplicate webhook + sync do not mail twice
    triggers.on_billing_event(ustore, "sync", None, before, ustore.billing_record())
    assert len([r for r in estore.log_query()["items"] if r["template_key"] == "trial_started"]) == 1
    # the user cancels the trial before the reminder: it must be skipped
    ustore.save_billing_record({"plan": "start", "status": "trialing", "subscription_id": "sub_1", "current_period_end": end, "cancel_at_period_end": True})
    estore.log_update(items["trial_ending"]["id"], scheduled_at=estore.now_iso())
    service.process_queue()
    assert estore.log_get(items["trial_ending"]["id"])["error"] == "canceled"


def test_cancellation_and_end_of_subscription_mails(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    end = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    paid = {"plan": "pro", "status": "active", "subscription_id": "sub_2", "current_period_end": end}
    ustore.save_billing_record({**paid, "cancel_at_period_end": True})
    triggers.on_billing_event(ustore, "sync", None, paid, ustore.billing_record())
    ustore.save_billing_record({"plan": "free", "status": "canceled", "subscription_id": ""})
    triggers.on_billing_event(ustore, "sync", None, {**paid, "cancel_at_period_end": True}, ustore.billing_record())
    keys = [r["template_key"] for r in estore.log_query()["items"]]
    assert "subscription_canceled" in keys and "subscription_ended" in keys
    assert estore.get_meta(f"paid_ended:{user['id']}")


def test_invoice_mails_skip_the_free_trial_invoice_and_escalate_failures(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    ustore.save_billing_record({"plan": "start", "status": "active", "subscription_id": "sub_3", "payment": {"brand": "VISA", "last4": "4242"}})
    rec = ustore.billing_record()
    triggers.on_billing_event(ustore, "invoice.paid", {"id": "in_0", "amount_paid": 0}, rec, rec)
    assert not estore.log_query()["items"]
    inv = {"id": "in_1", "amount_paid": 14900, "number": "2026-001", "lines": {"data": [{"period": {"start": 1760000000, "end": 1762600000}}]}}
    triggers.on_billing_event(ustore, "invoice.paid", inv, rec, rec)
    assert estore.log_query()["items"][0]["template_key"] == "payment_ok"
    for attempt in (1, 2, 3):
        triggers.on_billing_event(ustore, "invoice.payment_failed", {"id": "in_2", "attempt_count": attempt, "amount_due": 14900}, rec, rec)
    keys = [r["template_key"] for r in estore.log_query()["items"]]
    assert keys.count("payment_failed") == 2 and keys.count("payment_failed_last") == 1


def test_dog_limit_upsell_waits_an_hour_and_respects_the_gap(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    triggers.on_dog_limit_hit(ustore)
    row = estore.log_query()["items"][0]
    assert row["template_key"] == "upsell_dog_limit" and row["status"] == "queued" and row["scheduled_at"] > estore.now_iso()
    triggers.on_dog_limit_hit(ustore)  # within 7 days: no second upsell
    assert len(estore.log_query()["items"]) == 1
    ustore.save_billing_record({"plan": "start", "status": "active", "subscription_id": "s"})
    estore.log_update(row["id"], scheduled_at=estore.now_iso())
    service.process_queue()
    assert estore.log_get(row["id"])["error"] == "upgraded"


def test_alert_mail_dedupes_the_same_offer_from_another_portal(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    item = {"id": 1, "name": "Pronájem bytu 2+kk", "price_czk": 24500, "disposition": "2+kk", "area_m2": 54,
            "locality": "Praha 2 - Vinohrady", "url": "https://www.sreality.cz/detail/1"}
    assert triggers.send_alert(ustore, item, "Můj pes") is True
    assert triggers.send_alert(ustore, {**item, "id": 99, "url": "https://www.bezrealitky.cz/nemovitosti-byty-domy/99"}, "Můj pes") is False
    assert len(env.sent) == 1
    assert "alerty@realitify.cz" in env.sent[0]["From"]
    assert "2+kk" in env.sent[0]["Subject"] and "24 500" in env.sent[0]["Subject"]


def test_unopened_alerts_notice_only_when_ten_are_unread_and_none_bounced(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    for i in range(10):
        estore.log_insert(user_id=user["id"], to_email=user["email"], template_key="alert_new_listing", stream="alerts",
                          status="sent", sent_at=estore.now_iso(), subject="x")
    triggers._rule_alerts_unopened(triggers._Ctx(env.store_for))
    assert [r["template_key"] for r in estore.log_query(template="alerts_unopened")["items"]] == ["alerts_unopened"]
    # a bounce means the address is bad: nothing more is sent
    other = make_user(env, "other@example.cz")
    for i in range(10):
        estore.log_insert(user_id=other["id"], to_email=other["email"], template_key="alert_new_listing", stream="alerts",
                          status="failed" if i == 3 else "sent", subject="x")
    triggers._rule_alerts_unopened(triggers._Ctx(env.store_for))
    assert len(estore.log_query(template="alerts_unopened")["items"]) == 1


def test_verification_reminder_after_24h_only_once(env):
    user = make_user(env, "late@example.cz", verified=False)
    with env.gstore.connect() as conn:
        conn.execute("UPDATE users SET created_at = ? WHERE id = ?", ((datetime.now(timezone.utc) - timedelta(hours=26)).isoformat(), user["id"]))
    ctx = triggers._Ctx(env.store_for)
    triggers._rule_verify_retry(ctx)
    triggers._rule_verify_retry(ctx)
    assert len([r for r in estore.log_query()["items"] if r["template_key"] == "verify_email"]) == 1


def test_found_home_action_pauses_dogs_and_sends_followup(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    ustore.save_monitor({"id": "default", "name": "Pes", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/praha"})
    message = triggers.handle_action(user["id"], "found", env.store_for)
    assert "Gratulujeme" in message
    assert not any(m["enabled"] for m in ustore.list_monitors_light())
    assert estore.get_meta(f"found_exit:{user['id']}") == "found"
    assert [r["template_key"] for r in estore.log_query()["items"]] == ["found_home_followup"]


def test_pause_action_pauses_only_that_dog_and_is_signed_per_dog(env, monkeypatch):
    from app import billing

    monkeypatch.setattr(billing, "watch_limit_for", lambda store: None)
    user = make_user(env)
    ustore = env.store_for(user["id"])
    for mid in ("a", "b"):
        ustore.save_monitor({"id": mid, "name": f"Pes {mid}", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/praha"})
    url = tracking.action_url(user["id"], "pauza", "a")
    token = url.split("/e/a/")[1].split("?")[0]
    assert tracking.parse_action(token, "pauza", "a") == user["id"]
    assert tracking.parse_action(token, "pauza", "b") is None  # the signature covers the dog id
    assert tracking.parse_action(token, "pauza") is None
    triggers.handle_action(user["id"], "pauza", env.store_for, "a")
    state = {m["id"]: m["enabled"] for m in ustore.list_monitors_light()}
    assert not state["a"] and state["b"]


def test_auto_pause_sends_dog_auto_paused_and_zapnout_resumes(env):
    user = make_user(env)
    ustore = env.store_for(user["id"])
    ustore.save_monitor({"id": "default", "name": "Pes a", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/praha"})
    for i in range(10):
        estore.log_insert(user_id=user["id"], to_email=user["email"], template_key="alert_new_listing", stream="alerts",
                          status="sent", sent_at=estore.now_iso(), subject="x")
    ctx = triggers._Ctx(env.store_for)
    triggers._rule_alerts_unopened(ctx)
    estore.set_meta(f"unopened_notice:{user['id']}", (datetime.now(timezone.utc) - timedelta(days=8)).isoformat())
    triggers._rule_alerts_unopened(ctx)
    assert not any(m["enabled"] for m in ustore.list_monitors_light())
    assert len(estore.log_query(template="dog_auto_paused")["items"]) == 1
    triggers.handle_action(user["id"], "zapnout", env.store_for)
    assert all(m["enabled"] for m in ustore.list_monitors_light())


# -------------------------------------------------------------- admin API --

@pytest.fixture()
def api(env):
    def admin_user(token):
        if token != "ok":
            raise HTTPException(401, "no")
        return {"email": "admin@realitify.cz"}

    app = FastAPI()
    app.include_router(admin_api.build_router(admin_user, env.store_for))
    client = TestClient(app)
    client.cookies.set("realitify_admin", "ok")
    return client


def test_admin_api_requires_login(env):
    app = FastAPI()
    app.include_router(admin_api.build_router(lambda t: (_ for _ in ()).throw(HTTPException(401, "no")), env.store_for))
    assert TestClient(app).get("/api/admin/emails/templates").status_code == 401


def test_admin_api_template_crud_preview_and_test(api, env):
    listing = api.get("/api/admin/emails/templates").json()
    assert len(listing["templates"]) == len(catalog.TEMPLATES) and "1" in listing["groups"]
    detail = api.get("/api/admin/emails/templates/welcome").json()
    assert detail["html"] == detail["default_html"] and detail["variables"] and detail["common_variables"]
    save = api.post("/api/admin/emails/templates/welcome", json={"subject": "Ahoj {{jmeno}}", "preheader": "p", "html": "<html><body>X {{jmeno}}</body></html>", "enabled": True})
    assert save.status_code == 200
    assert api.get("/api/admin/emails/templates/welcome").json()["customized"] is True
    preview = api.post("/api/admin/emails/templates/welcome/preview", json={}).json()
    assert preview["subject"].startswith("Ahoj ") and "X " in preview["html"]
    assert api.post("/api/admin/emails/templates/welcome", json={"subject": "", "html": "x"}).status_code == 400
    sent = api.post("/api/admin/emails/templates/welcome/test", json={"to": "me@example.cz"}).json()
    assert sent["ok"] and env.sent[-1]["Subject"].startswith("[TEST] ")
    assert api.post("/api/admin/emails/templates/welcome/reset").status_code == 200
    assert api.get("/api/admin/emails/templates/welcome").json()["customized"] is False
    assert api.get("/api/admin/emails/templates/nope").status_code == 404


def test_admin_api_log_retry_and_status(api, env, monkeypatch):
    user = make_user(env)
    monkeypatch.setattr(service, "deliver", lambda m: (_ for _ in ()).throw(smtplib.SMTPRecipientsRefused({"x": (550, b"x")})))
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    data = api.get("/api/admin/emails/log?status=failed").json()
    assert data["total"] == 1 and data["items"][0]["template_name"] and data["counts"]["failed"] == 1
    assert api.get(f"/api/admin/emails/log/{log_id}").json()["html"]
    monkeypatch.setattr(service, "deliver", lambda m: env.sent.append(m))
    assert api.post(f"/api/admin/emails/log/{log_id}/retry").json()["status"] == "sent"
    assert api.post(f"/api/admin/emails/log/{log_id}/retry").status_code == 400
    status = api.get("/api/admin/emails/status").json()
    assert status["smtp_configured"] and set(status["senders"]) == {"transactional", "alerts", "lifecycle"}


def test_admin_api_campaign_flow(api, env):
    make_user(env, "x@example.cz", "Xena")
    segment = {"verified": "yes"}
    assert api.post("/api/admin/emails/campaigns/audience", json={"segment": segment}).json()["count"] == 1
    created = api.post("/api/admin/emails/campaigns", json={"name": "K", "mode": "text", "subject": "Ahoj", "body": "Text", "segment": segment}).json()
    assert created["status"] == "draft"
    assert api.post(f"/api/admin/emails/campaigns/{created['id']}/test", json={"to": "me@example.cz"}).json()["ok"]
    sent = api.post(f"/api/admin/emails/campaigns/{created['id']}/send").json()
    assert sent["status"] in ("sending", "sent") and sent["total"] == 1
    service.process_queue()
    detail = api.get(f"/api/admin/emails/campaigns/{created['id']}").json()
    assert detail["stats"]["sent"] == 1
    assert api.get("/api/admin/emails/campaigns").json()["campaigns"][0]["id"] == created["id"]
    assert api.post(f"/api/admin/emails/campaigns/{created['id']}/send").status_code == 400


# ------------------------------------------------------------------- SES --

def _alert(env, user, **kw):
    return service.send_template(
        "alert_new_listing", user=user, immediate=False,
        variables={"dispozice": "2+kk", "plocha": "50", "lokalita_detail": "Praha", "cena": "20 000", "odkaz": "https://x.cz", **kw},
    )


@pytest.fixture()
def ses_state(monkeypatch):
    monkeypatch.setattr(service, "_pause_until", 0.0)
    monkeypatch.setattr(service, "_last_send", 0.0)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_SEC", 0.0)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_HOUR", 0)
    monkeypatch.setattr(config, "SES_CONFIGURATION_SET", "")
    monkeypatch.setattr(config, "SES_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(config, "SES_SNS_TOPIC_ARN", "")


def test_provider_detects_ses(monkeypatch):
    from app import email_notify

    monkeypatch.setattr(config, "SMTP_HOST", "email-smtp.eu-central-1.amazonaws.com")
    assert email_notify.provider() == "ses"
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.websupport.cz")
    assert email_notify.provider() == "smtp"


def test_configuration_set_adds_ses_headers(env, ses_state, monkeypatch):
    monkeypatch.setattr(config, "SES_CONFIGURATION_SET", "realitify")
    user = make_user(env)
    log_id = _alert(env, user)
    service.process_now(log_id)
    msg = env.sent[0]
    assert msg["X-SES-CONFIGURATION-SET"] == "realitify"
    tags = msg["X-SES-MESSAGE-TAGS"]
    assert "template_key=alert_new_listing" in tags and "stream=alerts" in tags and f"log_id={log_id}" in tags


def test_no_ses_headers_without_configuration_set(env, ses_state):
    user = make_user(env)
    service.process_now(_alert(env, user))
    assert env.sent[0]["X-SES-CONFIGURATION-SET"] is None


def test_hourly_limit_defers_instead_of_failing(env, ses_state, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_MAX_PER_HOUR", 1)
    user = make_user(env)
    first = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    second = service.send_template("password_changed", user=user, variables={"datum_cas": "y", "odkaz": "https://x.cz"})
    assert estore.log_get(first)["status"] == "sent"
    row = estore.log_get(second)
    assert row["status"] == "queued" and row["attempts"] == 0 and row["scheduled_at"] > estore.now_iso()
    assert len(env.sent) == 1


def test_per_second_limit_spaces_sends(env, ses_state, monkeypatch):
    import time

    monkeypatch.setattr(config, "EMAIL_MAX_PER_SEC", 20.0)
    user = make_user(env)
    started = time.monotonic()
    for i in range(3):
        service.send_template("password_changed", user=user, variables={"datum_cas": str(i), "odkaz": "https://x.cz"})
    assert time.monotonic() - started >= 0.09 and len(env.sent) == 3


def test_auth_failure_pauses_sending_and_keeps_messages_queued(env, ses_state, monkeypatch):
    user = make_user(env)

    def deny(msg):
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 authentication failed")

    monkeypatch.setattr(service, "deliver", deny)
    first = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    row = estore.log_get(first)
    assert row["status"] == "queued" and row["attempts"] == 0 and row["error"].startswith("auth:")
    assert service.paused_for() > 200
    calls: list[Any] = []
    monkeypatch.setattr(service, "deliver", lambda m: calls.append(m))
    second = service.send_template("password_changed", user=user, variables={"datum_cas": "y", "odkaz": "https://x.cz"})
    assert estore.log_get(second)["status"] == "queued" and not calls
    estore.log_update(second, scheduled_at=estore.now_iso())
    assert service.process_queue() == 0  # paused: queue is not even touched
    monkeypatch.setattr(service, "_pause_until", 0.0)
    estore.log_update(first, scheduled_at=estore.now_iso())
    estore.log_update(second, scheduled_at=estore.now_iso())
    service.process_queue()
    assert len(calls) == 2 and estore.log_get(first)["status"] == "sent"


def test_old_alerts_are_skipped_as_stale_but_other_mails_are_not(env, ses_state):
    user = make_user(env)
    old = estore.iso_in(-7 * 3600)
    alert = _alert(env, user)
    estore.log_update(alert, created_at=old)
    other = service.send_template("welcome", user=user, variables={"odkaz": "https://x.cz"})
    estore.log_update(other, created_at=old)
    service.process_queue()
    assert estore.log_get(alert)["status"] == "skipped" and estore.log_get(alert)["error"] == "stale"
    assert estore.log_get(other)["status"] == "sent"


def test_suppressed_address_is_skipped_everywhere(env, ses_state):
    user = make_user(env, email="bounced@example.cz")
    estore.suppress("Bounced@Example.cz", "bounce:General")
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    row = estore.log_get(log_id)
    assert row["status"] == "skipped" and row["error"] == "suppressed" and not env.sent
    # suppressed after queueing
    other = make_user(env, email="later@example.cz")
    queued = service.send_template("welcome", user=other, variables={"odkaz": "https://x.cz"})
    estore.suppress("later@example.cz", "complaint")
    service.process_queue()
    assert estore.log_get(queued)["status"] == "skipped" and not env.sent


def test_legacy_send_mime_refuses_suppressed(env, ses_state):
    from email.message import EmailMessage
    from app import email_notify

    estore.suppress("x@example.cz", "complaint")
    msg = EmailMessage()
    msg["To"] = "Jiří <x@example.cz>"
    with pytest.raises(smtplib.SMTPRecipientsRefused):
        email_notify.send_mime(msg)


# ------------------------------------------------------------ SNS webhook --

def _sns_signed(topic="arn:aws:sns:eu-central-1:1:t", inner=None, kind="Notification", version="2"):
    """Build an SNS message signed with a throwaway key; returns (message, certificate pem)."""
    import base64
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    from cryptography.x509.oid import NameOID

    from app.emailing import ses

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sns.amazonaws.com")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256())
    )
    msg = {
        "Type": kind,
        "MessageId": "m1",
        "TopicArn": topic,
        "Message": json.dumps(inner or {}),
        "Timestamp": "2026-10-20T10:00:00.000Z",
        "SignatureVersion": version,
        "SigningCertURL": "https://sns.eu-central-1.amazonaws.com/SimpleNotificationService-x.pem",
    }
    if kind == "SubscriptionConfirmation":
        msg.update({"Token": "tok", "SubscribeURL": "https://sns.eu-central-1.amazonaws.com/?Action=ConfirmSubscription&Token=tok"})
    digest = hashes.SHA256() if version == "2" else hashes.SHA1()
    sig = key.sign(ses.string_to_sign(msg), padding.PKCS1v15(), digest)
    msg["Signature"] = base64.b64encode(sig).decode()
    return msg, cert.public_bytes(serialization.Encoding.PEM)


@pytest.fixture()
def sns(env, ses_state, monkeypatch):
    from app.emailing import ses

    fetched: list[str] = []
    state = {"pem": b""}

    def fake_fetch(url):
        fetched.append(url)
        return state["pem"]

    monkeypatch.setattr(ses, "fetch", fake_fetch)
    monkeypatch.setattr(ses, "_CERTS", {})
    monkeypatch.setattr(config, "SES_WEBHOOK_TOKEN", "secret-token")
    return type("S", (), {"fetched": fetched, "state": state, "ses": ses})


def _post(sns, env, msg, pem, token="secret-token"):
    sns.state["pem"] = pem
    sns.ses._CERTS.clear()  # every test message is signed with a fresh key
    body = json.dumps(msg).encode()
    return sns.ses.process_request(body, token)


BOUNCE = {
    "notificationType": "Bounce",
    "bounce": {"bounceType": "Permanent", "bounceSubType": "General", "bouncedRecipients": [{"emailAddress": "Gone@Example.cz"}]},
    "mail": {"tags": {"log_id": ["999"]}},
}


def test_webhook_suppresses_hard_bounce_and_flags_user(env, sns):
    user = make_user(env, email="gone@example.cz")
    msg, pem = _sns_signed(inner=BOUNCE)
    status, body = _post(sns, env, msg, pem)
    assert status == 200 and body["suppressed"] == 1
    assert estore.is_suppressed("gone@example.cz")
    with estore.db() as conn:
        assert conn.execute("SELECT email_suppressed FROM users WHERE id = ?", (user["id"],)).fetchone()[0] == 1


def test_webhook_works_with_signature_version_1(env, sns):
    msg, pem = _sns_signed(inner=BOUNCE, version="1")
    assert _post(sns, env, msg, pem)[0] == 200 and estore.is_suppressed("gone@example.cz")


def test_webhook_ignores_soft_bounce_and_delivery_but_stores_complaints(env, sns):
    soft = {"notificationType": "Bounce", "bounce": {"bounceType": "Transient", "bouncedRecipients": [{"emailAddress": "soft@example.cz"}]}}
    msg, pem = _sns_signed(inner=soft)
    assert _post(sns, env, msg, pem)[0] == 200 and not estore.is_suppressed("soft@example.cz")
    msg, pem = _sns_signed(inner={"notificationType": "Delivery"})
    assert _post(sns, env, msg, pem)[0] == 200
    complaint = {"notificationType": "Complaint", "complaint": {"complainedRecipients": [{"emailAddress": "angry@example.cz"}]}}
    msg, pem = _sns_signed(inner=complaint)
    assert _post(sns, env, msg, pem)[0] == 200 and estore.is_suppressed("angry@example.cz")


def test_webhook_marks_the_sent_log_row_failed(env, sns):
    user = make_user(env, email="gone@example.cz")
    log_id = service.send_template("password_changed", user=user, variables={"datum_cas": "x", "odkaz": "https://x.cz"})
    inner = {**BOUNCE, "mail": {"tags": {"log_id": [str(log_id)]}}}
    msg, pem = _sns_signed(inner=inner)
    _post(sns, env, msg, pem)
    assert estore.log_get(log_id)["status"] == "failed"


def test_webhook_rejects_forged_signature_wrong_token_and_missing_config(env, sns, monkeypatch):
    msg, pem = _sns_signed(inner=BOUNCE)
    forged = {**msg, "Message": json.dumps({**BOUNCE, "bounce": {**BOUNCE["bounce"], "bouncedRecipients": [{"emailAddress": "victim@example.cz"}]}})}
    assert _post(sns, env, forged, pem)[0] == 403
    assert _post(sns, env, msg, pem, token="nope")[0] == 403
    assert not estore.is_suppressed("victim@example.cz") and not estore.is_suppressed("gone@example.cz")
    monkeypatch.setattr(config, "SES_WEBHOOK_TOKEN", "")
    assert _post(sns, env, msg, pem)[0] == 503
    assert sns.ses.process_request(b"not json", "x")[0] == 400


def test_webhook_topic_allow_list(env, sns, monkeypatch):
    monkeypatch.setattr(config, "SES_WEBHOOK_TOKEN", "")
    monkeypatch.setattr(config, "SES_SNS_TOPIC_ARN", "arn:aws:sns:eu-central-1:1:mine")
    msg, pem = _sns_signed(topic="arn:aws:sns:eu-central-1:2:other", inner=BOUNCE)
    assert _post(sns, env, msg, pem)[0] == 403
    msg, pem = _sns_signed(topic="arn:aws:sns:eu-central-1:1:mine", inner=BOUNCE)
    assert _post(sns, env, msg, pem)[0] == 200


def test_signing_cert_url_must_be_sns_https(env, sns):
    ses = sns.ses
    assert ses.cert_url_ok("https://sns.eu-central-1.amazonaws.com/a.pem")
    for bad in ("http://sns.eu-central-1.amazonaws.com/a.pem", "https://evil.example.com/a.pem",
                "https://sns.eu-central-1.amazonaws.com.evil.com/a.pem", "https://sns.eu-central-1.amazonaws.com/a.txt"):
        assert not ses.cert_url_ok(bad)
    msg, pem = _sns_signed(inner=BOUNCE)
    msg["SigningCertURL"] = "https://evil.example.com/a.pem"
    assert _post(sns, env, msg, pem)[0] == 403 and not sns.fetched


def test_subscription_confirmation_only_to_amazonaws(env, sns):
    msg, pem = _sns_signed(kind="SubscriptionConfirmation")
    assert _post(sns, env, msg, pem)[0] == 200
    assert sns.fetched[-1].startswith("https://sns.eu-central-1.amazonaws.com/?Action=ConfirmSubscription")
    sns.fetched.clear()
    assert not sns.ses.subscribe_url_ok("https://evil.example.com/confirm")
    assert not sns.ses.subscribe_url_ok("http://sns.eu-central-1.amazonaws.com/x")


def test_admin_status_reports_provider_and_suppressed(api, env, monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "email-smtp.eu-central-1.amazonaws.com")
    estore.suppress("a@example.cz", "complaint")
    body = api.get("/api/admin/emails/status").json()
    assert body["provider"] == "ses" and body["suppressed"] == 1


# ------------------------------------------------- only transactional --

def test_only_transactional_sends_account_mail_and_skips_the_rest(env, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", True)
    user = make_user(env)
    tid = service.send_template("verify_email", user=user, variables={"odkaz": "https://realitify.test/x"})
    assert estore.log_get(tid)["status"] == "sent"
    wid = service.send_template("welcome", user=user, variables={"odkaz": "https://realitify.test/x"})
    row = estore.log_get(wid)
    assert row["status"] == "skipped" and row["error"] == "paused"
    assert service.send_template("alert_new_listing", user=user, variables={}) is None  # alerts are not even logged
    assert [m["To"] for m in env.sent] == [user["email"]] and len(env.sent) == 1


def test_only_transactional_holds_back_rows_queued_earlier_but_lets_admin_tests_out(env, monkeypatch):
    user = make_user(env)
    qid = service.send_template("welcome", user=user, variables={"odkaz": "https://realitify.test/x"})
    assert estore.log_get(qid)["status"] == "queued"
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", True)
    service.process_queue()
    assert estore.log_get(qid)["error"] == "paused" and not env.sent
    rendered = render.Rendered("[TEST] s", "", "<p>x</p>", "x")
    tid = service._enqueue(template_key="welcome", stream="lifecycle", to="me@example.cz", user_id="", rendered=rendered, meta={"test": True, "force": True})
    assert service.process_now(tid) == "sent"


def test_only_transactional_blocks_campaigns_and_legacy_notifications(env, monkeypatch):
    import asyncio
    from app import email_notify

    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", True)
    cid = estore.campaign_save({"name": "x", "mode": "text", "subject": "s", "body": "b", "segment": {}})
    with pytest.raises(ValueError):
        campaigns.start(cid, env.store_for)
    assert asyncio.run(email_notify.notify_listing(env.gstore, {"name": "x"}, "new")) is False
    assert asyncio.run(email_notify.notify_digest(env.gstore, [{"name": "x"}])) is False


def test_pause_does_not_backfill_lifecycle_mail_afterwards(env, monkeypatch):
    user = make_user(env)
    verified = datetime.now(timezone.utc) - timedelta(hours=30)
    estore.set_meta(f"verified:{user['id']}", verified.isoformat())
    ctx = triggers._Ctx(env.store_for)
    monkeypatch.setattr(ctx, "rows", lambda days: [{"locality": "Praha 2", "url": "https://x"}] * 5)
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", True)
    triggers.run_scheduler(env.store_for)  # paused tick: only resume/verify_retry run, the barrier is recorded
    assert estore.log_query()["items"] == []
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", False)
    estore.set_meta(triggers.BARRIER_KEY, (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())  # the pause ended 2 h ago
    triggers._rule_onboarding(ctx)  # the 1-day nudge fell into the pause: it is not sent late
    assert estore.log_query()["items"] == []
    # a user who becomes due after the pause is served normally
    fresh = make_user(env, email="new@example.cz")
    estore.set_meta(f"verified:{fresh['id']}", (datetime.now(timezone.utc) + timedelta(hours=-25)).isoformat())
    triggers._rule_onboarding(ctx)
    assert [r["template_key"] for r in estore.log_query()["items"]] == ["nudge_no_dog"]


def test_status_reports_only_transactional(env, api, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", True)
    assert api.get("/api/admin/emails/status").json()["only_transactional"] is True
    monkeypatch.setattr(config, "EMAIL_ONLY_TRANSACTIONAL", False)
    assert api.get("/api/admin/emails/status").json()["only_transactional"] is False
