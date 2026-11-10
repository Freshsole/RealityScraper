"""AI chat actions: READ tools, two-phase WRITE confirmation, navigate whitelist, audit. LLM and network are fakes."""
import asyncio
import json
import os
import sys
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-ai-actions-test-"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _ai_helpers import FakeLLM, as_user, chat_app, make_ctx, make_services, tool_turn

from ai import limits
from ai.tools import READ_TOOLS, TOOL_NAMES, UI_TOOLS, WRITE_TOOLS, ChatTools, safe_path
from ai.context import ToolError
from app import localities, rate_limit
from app import bezrealitky_url
from app.store import Store

PRAHA7 = localities.locality_item(*[d for d in bezrealitky_url.DISTRICTS if d[1] == "Praha 7"][0])


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "g.sqlite", seed_webhook_url="")


@pytest.fixture()
def ustore(tmp_path):
    return Store(tmp_path / "u.sqlite", seed_webhook_url="")


async def _resolve(q=""):
    return {"items": [PRAHA7]}


async def _geocode(q=""):
    return {"items": [{"label": "Václavské náměstí 1, Praha", "lat": 50.08, "lon": 14.43}]}


def _tools(store, **over):
    over.setdefault("resolve_locality", _resolve)
    over.setdefault("geocode", _geocode)
    svc = make_services(store, **over)
    return ChatTools(svc), svc


def _call(tools, ctx_, tool_, /, **args):
    raw, events = _run(tools.call(tool_, json.dumps(args), ctx_))
    return json.loads(raw), events


def _monitors(ustore):
    return [m["name"] for m in ustore.list_monitors()]


# ---- WRITE needs confirmation ---------------------------------------------------------------------------------------

def test_write_tool_changes_nothing_until_confirmed(store, ustore):
    tools, svc = _tools(store)
    ctx = make_ctx(ustore, plan="pro", chat_id=None)
    before = _monitors(ustore)
    result, events = _call(tools, ctx, "create_monitor", locality="Praha 7", offer_type="pronajem", disposition=["2+kk"], max_price=25000)
    assert result.get("status") == "pending_confirmation", result
    assert _monitors(ustore) == before  # nothing created
    confirm = [e for e in events if e["type"] == "confirm"]
    assert len(confirm) == 1 and "Praha 7" in confirm[0]["summary"] and "25\u00a0000\u00a0Kč" in confirm[0]["summary"]
    pending = store.ai_action_get(confirm[0]["action_id"])
    assert pending["status"] == "pending" and pending["user_id"] == "user-1" and pending["tool"] == "create_monitor"


def test_confirm_executes_via_service_logs_and_notes_in_chat(store, monkeypatch):
    client = chat_app(store, "pro", FakeLLM(), monkeypatch, services=make_services(store, resolve_locality=_resolve, geocode=_geocode))
    ustore = client.hub.store_for("user-1")
    tools, svc = _tools(store)
    chat_id = store.ai_chat_create("user-1", "t")
    ctx = make_ctx(ustore, plan="pro", chat_id=chat_id)
    _, events = _call(tools, ctx, "create_monitor", locality="Praha 7", offer_type="pronajem", disposition=["2+kk"], max_price=25000)
    action_id = events[0]["action_id"]
    n_before = len(ustore.list_monitors())
    r = client.post(f"/api/chat/actions/{action_id}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "executed"
    assert len(ustore.list_monitors()) == n_before + 1
    assert store.ai_action_get(action_id)["status"] == "executed"
    chat = store.ai_chat_get("user-1", chat_id)
    assert chat["messages"][-1]["role"] == "assistant" and "Provedeno" in chat["messages"][-1]["content"]
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT result FROM ai_actions_log WHERE user_id='user-1'").fetchone()[0] == "executed"
    # a second click cannot run it again
    assert client.post(f"/api/chat/actions/{action_id}/confirm").status_code == 409
    assert len(ustore.list_monitors()) == n_before + 1


def test_confirming_someone_elses_action_is_403(store, monkeypatch):
    client = chat_app(store, "pro", FakeLLM(), monkeypatch, users={"other": "user-2"})
    action_id = store.ai_action_create("user-1", None, "unlink_discord", {}, "Odpojit Discord od účtu", 600)
    intruder = as_user(client, "other")
    assert intruder.post(f"/api/chat/actions/{action_id}/confirm").status_code == 403
    assert intruder.post(f"/api/chat/actions/{action_id}/cancel").status_code == 403
    assert intruder.post("/api/chat/actions/does-not-exist/confirm").status_code == 403
    assert store.ai_action_get(action_id)["status"] == "pending"  # untouched
    anon = as_user(client, "nobody")
    assert anon.post(f"/api/chat/actions/{action_id}/confirm").status_code == 401


def test_expired_action_cannot_be_confirmed_and_is_logged(store, monkeypatch):
    client = chat_app(store, "pro", FakeLLM(), monkeypatch)
    action_id = store.ai_action_create("user-1", None, "unlink_discord", {}, "Odpojit Discord od účtu", -5)
    r = client.post(f"/api/chat/actions/{action_id}/confirm")
    assert r.status_code == 410
    assert store.ai_action_get(action_id)["status"] == "expired"
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT result FROM ai_actions_log WHERE user_id='user-1'").fetchone()[0] == "expired"


def test_cancel_marks_cancelled_and_logs(store, monkeypatch):
    client = chat_app(store, "pro", FakeLLM(), monkeypatch)
    action_id = store.ai_action_create("user-1", None, "unlink_discord", {}, "Odpojit Discord od účtu", 600)
    assert client.post(f"/api/chat/actions/{action_id}/cancel").json()["status"] == "cancelled"
    assert store.ai_action_get(action_id)["status"] == "cancelled"
    assert client.post(f"/api/chat/actions/{action_id}/confirm").status_code == 409


def test_confirm_rate_limit_per_hour(store, monkeypatch):
    rate_limit.reset()
    monkeypatch.setattr(limits, "MAX_CONFIRMS_PER_HOUR", 2)
    client = chat_app(store, "pro", FakeLLM(), monkeypatch)
    codes = []
    for _ in range(3):
        aid = store.ai_action_create("user-1", None, "unlink_discord", {}, "Odpojit Discord od účtu", 600)
        codes.append(client.post(f"/api/chat/actions/{aid}/cancel").status_code)
    assert codes == [200, 200, 200]  # cancel is not limited
    codes = []
    for _ in range(3):
        aid = store.ai_action_create("user-1", None, "unlink_discord", {}, "Odpojit Discord od účtu", 600)
        codes.append(client.post(f"/api/chat/actions/{aid}/confirm").status_code)
    assert codes == [200, 200, 429]
    rate_limit.reset()


def test_max_five_proposals_per_message(store, ustore):
    tools, svc = _tools(store)
    ctx = make_ctx(ustore, plan="pro")
    outcomes = [_call(tools, ctx, "unlink_discord")[0] for _ in range(6)]
    assert [("status" in o) for o in outcomes] == [True] * 5 + [False]
    assert "error" in outcomes[5]


# ---- plan limits ---------------------------------------------------------------------------------------------------

def test_pro_only_action_for_free_user_returns_plan_error(store, ustore):
    tools, svc = _tools(store)
    ctx = make_ctx(ustore, plan="free")
    result, events = _call(tools, ctx, "send_test_notification", channel="whatsapp")
    assert "dostupná v PRO" in result["error"]
    assert events == []
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_pending_actions").fetchone()[0] == 0


def test_whatsapp_confirm_rechecks_plan(store, monkeypatch):
    client = chat_app(store, "free", FakeLLM(), monkeypatch)
    aid = store.ai_action_create("user-1", None, "send_test_notification", {"channel": "whatsapp"}, "Poslat testovací zprávu: WhatsApp", 600)
    r = client.post(f"/api/chat/actions/{aid}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "error" and "PRO" in r.json()["message"]
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT result FROM ai_actions_log").fetchone()[0] == "error"


def test_monitor_limit_of_free_plan_is_enforced_like_in_the_ui(store, ustore):
    tools, svc = _tools(store)
    ctx = make_ctx(ustore, plan="free")
    for name in [m["id"] for m in ustore.list_monitors()]:
        ustore.delete_monitor(name)
    ustore.save_monitor({"name": "první", "search_url": "https://www.sreality.cz/hledani/pronajem/byty/praha", "enabled": True})
    result, _ = _call(tools, ctx, "create_monitor", locality="Praha 7", offer_type="pronajem")
    assert "Limit tarifu" in result["error"]


# ---- navigate + tool list -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["https://evil.example/x", "//evil.example", "/admin", "/api/auth/password", "javascript:alert(1)",
                                  "/nabidka/../admin", "/\\evil", "nabidka", "/", "/#evil", "http://realitify.cz/monitory", ""])
def test_navigate_rejects_outside_whitelist(path):
    with pytest.raises(ToolError):
        safe_path(path)


@pytest.mark.parametrize("path", ["/monitory", "/nastaveni", "/nastaveni/ucet?x=1", "/nabidka?listing_key=a%3Ab", "/prehled", "/#cenik", "/trh/praha/pronajem"])
def test_navigate_accepts_relative_whitelisted_paths(path):
    assert safe_path(path) == path


def test_navigate_tool_emits_event_without_confirmation(store, ustore):
    tools, _ = _tools(store)
    ctx = make_ctx(ustore)
    result, events = _call(tools, ctx, "navigate", path="/nastaveni")
    assert events == [{"type": "navigate", "path": "/nastaveni"}]
    result, events = _call(tools, ctx, "navigate", path="https://evil.example")
    assert "error" in result and events == []
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_pending_actions").fetchone()[0] == 0


def test_forbidden_actions_are_not_tools(store):
    tools, _ = _tools(store)
    names = {t["function"]["name"] for t in tools.schemas()}
    assert names == set(TOOL_NAMES) == set(READ_TOOLS) | set(WRITE_TOOLS) | set(UI_TOOLS)
    banned = ("billing", "checkout", "subscription", "stripe", "cancel_plan", "account", "delete_account", "email_change", "change_email",
              "password", "api_key", "token", "mcp", "oauth", "admin", "backup", "webhook", "push", "profile", "phone", "avatar", "inquir")
    for name in names:
        assert not any(b in name for b in banned), name
    # nothing identifies the user or can be pointed at somebody else
    for t in tools.schemas():
        props = t["function"]["parameters"]["properties"]
        assert not ({"user_id", "ctx", "phone", "password", "token", "email_address", "webhook_url"} & set(props)), t["function"]["name"]


def test_model_cannot_inject_context_or_user_id(store, ustore):
    tools, _ = _tools(store)
    ctx = make_ctx(ustore)
    assert "error" in _call(tools, ctx, "list_my_monitors", user_id="user-2")[0]
    assert "error" in _call(tools, ctx, "list_my_monitors", ctx={"user_id": "user-2"})[0]


# ---- READ tools ---------------------------------------------------------------------------------------------------------

def test_settings_never_expose_commute_addresses_or_webhooks(store, ustore):
    ustore.save_app_settings({"commute_points": [{"id": "p1", "name": "Práce", "address": "Tajná 1, Praha", "lat": 50.1, "lon": 14.4}],
                              "digest_webhook": "https://discord.com/api/webhooks/1/secret"})
    tools, _ = _tools(store)
    raw, _ = _run(tools.call("get_settings", "{}", make_ctx(ustore)))
    assert "Tajná" not in raw and "50.1" not in raw and "secret" not in raw and "webhook" not in raw
    assert json.loads(raw)["commute_points"] == [{"id": "p1", "label": "Práce"}]


def test_get_listing_includes_note_of_that_listing_only(store, ustore):
    ustore.set_listing_user("https://portal/x", "saved", "Zavolat v pondělí")
    ustore.set_listing_user("https://portal/y", None, "Jiná poznámka")

    async def item(**kw):
        return {"id": 1, "listing_key": "k1", "name": "Byt", "url": "https://portal/x", "price_czk": 1}

    tools, _ = _tools(store, catalog={"item": item})
    out = _call(tools, make_ctx(ustore), "get_listing", listing_url="https://realitify.cz/nabidka?listing_key=k1")[0]
    assert out["my_note"] == "Zavolat v pondělí" and out["my_status"] == "saved"
    assert "Jiná" not in json.dumps(out, ensure_ascii=False)


def test_commute_filter_hides_address_and_filters_by_minutes(store, ustore):
    ustore.save_app_settings({"commute_points": [{"id": "p1", "name": "Práce", "address": "Tajná 1, Praha", "lat": 50.1, "lon": 14.4}]})
    seen = []

    async def search(**kw):
        return {"items": [
            {"id": 1, "listing_key": "a", "name": "Blízko", "lat": 50.0, "lon": 14.0, "url": "u1"},
            {"id": 2, "listing_key": "b", "name": "Daleko", "lat": 51.0, "lon": 15.0, "url": "u2"},
            {"id": 3, "listing_key": "c", "name": "Bez souřadnic", "url": "u3"},
        ], "total": 3}

    async def route_times(lat, lon, to_lat, to_lon, **kw):
        seen.append((to_lat, to_lon))
        return {"transit": 600 if lat == 50.0 else 5400, "walk": None, "drive": None}

    tools, _ = _tools(store, catalog={"search": search}, route_times=route_times)
    raw, events = _run(tools.call("search_listings", json.dumps({"locality": "Praha", "commute_point_id": "p1", "max_commute_minutes": 30}), make_ctx(ustore)))
    out = json.loads(raw)
    assert [i["title"] for i in out["items"]] == ["Blízko"] and out["items"][0]["commute_min"] == 10
    assert "Tajná" not in raw and "50.1" not in raw and out["commute_to"] == "Práce"
    assert seen and set(seen) == {(50.1, 14.4)}  # the server filled in the coordinates
    assert "error" in _call(tools, make_ctx(ustore), "search_listings", locality="Praha", commute_point_id="neexistuje")[0]


def test_commute_point_write_shows_label_and_address_and_saves_on_confirm(store, monkeypatch):
    client = chat_app(store, "pro", FakeLLM(), monkeypatch, services=make_services(store, geocode=_geocode))
    ustore = client.hub.store_for("user-1")
    tools = ChatTools(make_services(store, geocode=_geocode))
    ctx = make_ctx(ustore, plan="pro")
    result, events = _call(tools, ctx, "set_commute_point", name="Práce", address="Václavské náměstí 1")
    assert "Práce" in events[0]["summary"] and "Václavské náměstí 1" in events[0]["summary"]
    assert ustore.app_settings()["commute_points"] == []
    assert client.post(f"/api/chat/actions/{events[0]['action_id']}/confirm").json()["status"] == "executed"
    points = ustore.app_settings()["commute_points"]
    assert len(points) == 1 and points[0]["name"] == "Práce" and points[0]["lat"] == 50.08


def test_notify_settings_proposal_summary_is_written_by_the_server(store, ustore):
    tools, _ = _tools(store)
    ctx = make_ctx(ustore, plan="pro")
    result, events = _call(tools, ctx, "update_notify_settings", email=False, quiet=True, quiet_from="21:00", digest_hour=9)
    s = events[0]["summary"]
    assert "e-mail vypnout" in s and "tiché hodiny zapnout" in s and "od 21:00" in s and "9:00" in s
    assert "error" in _call(tools, ctx, "update_notify_settings", quiet_from="25:99")[0]
    assert ustore.app_settings()["notify"]["email"] is True  # still unchanged


def test_note_write_is_confirmed_and_saved(store, monkeypatch):
    client = chat_app(store, "pro", FakeLLM(), monkeypatch)
    ustore = client.hub.store_for("user-1")
    tools, _ = _tools(store)
    ctx = make_ctx(ustore, plan="pro")
    _, events = _call(tools, ctx, "set_listing_note", listing_url="https://portal.example/byt/1", note="Ptát se na kauci")
    assert ustore.get_listing_user("https://portal.example/byt/1")["note"] == ""
    client.post(f"/api/chat/actions/{events[0]['action_id']}/confirm")
    assert ustore.get_listing_user("https://portal.example/byt/1")["note"] == "Ptát se na kauci"


def test_chat_stream_emits_confirm_and_get_chat_lists_pending(store, monkeypatch):
    llm = FakeLLM(turns=[tool_turn("unlink_discord", "{}"), [{"type": "text", "text": "Navrhla jsem odpojení."}]])
    client = chat_app(store, "pro", llm, monkeypatch)
    r = client.post("/api/chat", json={"message": "odpoj mi discord"})
    events = [json.loads(b[6:]) for b in r.text.split("\n\n") if b.startswith("data: ")]
    confirm = [e for e in events if e["type"] == "confirm"]
    assert len(confirm) == 1 and confirm[0]["summary"] == "Odpojit Discord od účtu"
    chat_id = next(e["id"] for e in events if e["type"] == "chat")
    assert client.get(f"/api/chats/{chat_id}").json()["pending"] == [{"action_id": confirm[0]["action_id"], "summary": "Odpojit Discord od účtu"}]
    # the model's tool result says the action was NOT executed
    tool_msg = [m for m in llm.chat_calls[1]["messages"] if m["role"] == "tool"][0]["content"]
    assert "NEBYLA provedena" in tool_msg
