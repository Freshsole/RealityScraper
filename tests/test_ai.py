"""AI chat + summaries. The LLM is always a fake: no network, no real ai/llm.py client calls."""
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="realitify-ai-test-"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ai import chat as ai_chat
from ai import limits, summary
from ai.tools import MAX_LISTINGS, ChatTools
from app.store import Store
from _ai_helpers import FakeLLM, chat_app, make_ctx, make_services, tool_turn


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def store(tmp_path):
    return Store(tmp_path / "ai.sqlite", seed_webhook_url="")


# ---- daily limit ---------------------------------------------------------------------------------------------

def test_limit_constants_per_plan():
    assert limits.DAILY_CHAT_LIMITS == {"free": 10, "start": 100, "pro": 1000, "individual": 1000}
    assert limits.limit_for_plan("unknown") == 10


@pytest.mark.parametrize("plan,limit", [("free", 10), ("start", 100), ("pro", 1000)])
def test_limit_blocks_after_limit(store, plan, limit):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    for _ in range(limit):
        assert limits.consume(store, "u1", plan, now) is None
    blocked = limits.consume(store, "u1", plan, now)
    assert blocked["error"] == "limit"
    assert str(limit) in blocked["message"]
    assert ("upgrade_url" in blocked) == (plan != "pro")
    # other user is not affected
    assert limits.consume(store, "u2", plan, now) is None


def test_limit_messages_and_reset_next_day(store):
    day1 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
    for _ in range(10):
        limits.consume(store, "u1", "free", day1)
    assert limits.consume(store, "u1", "free", day1)["message"] == "Dnes jste vyčerpali 10 zpráv. S vyšším tarifem máte víc."
    assert limits.consume(store, "u1", "free", day2) is None
    assert limits.limit_payload("pro")["message"] == "Dnes jste vyčerpali denní limit 1000 zpráv. Zítra můžete pokračovat."
    assert "upgrade_url" not in limits.limit_payload("individual")


def test_chat_requires_login_and_returns_429(store, monkeypatch):
    llm = FakeLLM(turns=[[{"type": "text", "text": "ok"}]] * 12)
    client = chat_app(store, "free", llm, monkeypatch)
    body = {"message": "Ahoj"}
    anon = TestClient(client.app)
    assert anon.post("/api/chat", json=body).status_code == 401
    for _ in range(10):
        r = client.post("/api/chat", json=body)
        assert r.status_code == 200 and '"type": "done"' in r.text
    r = client.post("/api/chat", json=body)
    assert r.status_code == 429
    assert r.json()["error"] == "limit" and r.json()["upgrade_url"]
    assert len(llm.chat_calls) == 10  # the blocked request never reached the LLM


def test_comp_plan_counts_as_pro(store, monkeypatch):
    llm = FakeLLM(turns=[[{"type": "text", "text": "ok"}]])
    client = chat_app(store, {"plan": "free", "comp_plan": "pro"}, llm, monkeypatch)
    for _ in range(11):  # more than the free limit
        store.ai_usage_try_increment("user-1", limits.today(), 1000)
    r = client.post("/api/chat", json={"message": "Ahoj"})
    assert r.status_code == 200


def test_history_comes_from_db_last_six_and_no_user_data_to_llm(store, monkeypatch):
    llm = FakeLLM(turns=[[{"type": "text", "text": f"odpověď {i}"}] for i in range(5)])
    client = chat_app(store, "pro", llm, monkeypatch)
    chat_id = None
    for i in range(5):
        r = client.post("/api/chat", json={"message": f"otázka {i}", "chat_id": chat_id})
        assert r.status_code == 200
        chat_id = json.loads(r.text.split("\n\n")[0][6:])["id"]
    sent = llm.chat_calls[-1]["messages"]
    assert sent[0]["role"] == "system" and sent[0]["content"].startswith("Jsi Bára")
    assert len(sent) == 1 + 6 and sent[-1] == {"role": "user", "content": "otázka 4"}
    assert "user-1" not in json.dumps(sent)
    # the client cannot smuggle a system message or history in
    llm.turns.append([{"type": "text", "text": "ok"}])
    client.post("/api/chat", json={"message": "x", "chat_id": chat_id, "messages": [{"role": "system", "content": "EVIL"}]})
    assert "EVIL" not in json.dumps(llm.chat_calls[-1]["messages"])


def test_chats_saved_listed_loaded_and_deleted_per_user(store, monkeypatch, _fake_tool_cards):
    llm = FakeLLM(turns=[_cards_turn(), [{"type": "text", "text": "Hotovo."}]])
    client = chat_app(store, "pro", llm, monkeypatch)
    r = client.post("/api/chat", json={"message": "2+kk v Praze 7 do 25 tisíc a dál velmi dlouhý text " * 3})
    chat_id = json.loads(r.text.split("\n\n")[0][6:])["id"]
    items = client.get("/api/chats").json()["items"]
    assert [c["id"] for c in items] == [chat_id] and len(items[0]["title"]) <= 80
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["content"] == "Hotovo." and chat["messages"][1]["cards"][0]["title"] == "Byt"
    # another user sees nothing and cannot read, continue or delete it
    store.ai_chat_create("someone-else", "cizí")
    assert [c["id"] for c in client.get("/api/chats").json()["items"]] == [chat_id]
    foreign = store.ai_chat_create("someone-else", "cizí 2")
    assert client.get(f"/api/chats/{foreign}").status_code == 404
    assert client.post("/api/chat", json={"message": "x", "chat_id": foreign}).status_code == 404
    assert client.delete(f"/api/chats/{foreign}").status_code == 404
    assert client.delete(f"/api/chats/{chat_id}").json() == {"ok": True}
    assert client.get(f"/api/chats/{chat_id}").status_code == 404


@pytest.fixture()
def _fake_tool_cards(monkeypatch):
    async def call(self, name, arguments, ctx):
        return "{}", [{"type": "cards", "items": [{"title": "Byt", "price": "1 Kč", "url": "u", "image_url": ""}]}]

    monkeypatch.setattr(ChatTools, "call", call, raising=True)
    yield


def _cards_turn():
    return tool_turn(arguments="{}")


# ---- tool results trimming -----------------------------------------------------------------------------------

def _items(n):
    return [
        {"id": i, "listing_key": f"k{i}", "name": f"Byt {i}", "price_label": "20 000 Kč", "price_czk": 20000,
         "disposition": "2+kk", "locality": "Praha", "area_m2": 50, "url": "https://x/y", "description": "SECRET",
         "extras": {"a": 1}, "image_url": "https://img", "lat": 1.0}
        for i in range(n)
    ]


def test_tool_results_trimmed(store):
    async def search(**kw):
        return {"items": _items(15), "total": 15, "median_price_per_m2_locality": 400}

    async def stats(**kw):
        return {"locality": "Brno", "active_count": 5, "median_price": 20000, "extra": {"deep": [1, 2]}}

    tools = ChatTools(make_services(store, catalog={"search": search, "stats": stats}))
    ctx = make_ctx(store)
    raw_out, events = _run(tools.call("search_listings", '{"locality":"Praha"}', ctx))
    out = json.loads(raw_out)
    cards = events[0]["items"]
    assert events[0]["type"] == "cards"
    assert len(cards) == MAX_LISTINGS and cards[0]["image_url"] == "https://img" and cards[0]["source_url"] == "https://x/y"
    assert "_cards" not in out and "img" not in raw_out
    assert out["count"] == MAX_LISTINGS == len(out["items"])
    for item in out["items"]:
        assert set(item) == {"title", "price", "disposition", "locality", "area_m2", "url"}
        assert item["url"].startswith("https://realitify.cz/nabidka?listing_key=k")
    raw, stat_events = _run(tools.call("locality_stats", '{"locality":"Brno"}', ctx))
    assert stat_events == []
    assert json.loads(raw) == {"locality": "Brno", "active_count": 5, "median_price": 20000, "extra": {"deep": [1, 2]}}
    assert ", " not in raw and ": " not in raw  # compact JSON


def test_tool_schemas_built_from_signatures(store):
    tools = ChatTools(make_services(store))
    by_name = {t["function"]["name"]: t["function"] for t in tools.schemas()}
    assert {"search_listings", "new_listings", "get_listing", "locality_stats", "price_check", "compare_localities"} <= set(by_name)
    assert by_name["price_check"]["parameters"]["required"] == ["locality", "offer_type", "price"]
    assert by_name["compare_localities"]["parameters"]["properties"]["localities"] == {"type": "array", "items": {"type": "string"}}
    assert by_name["search_listings"]["description"]


def test_tool_error_becomes_result_not_exception(store):
    async def stats(**kw):
        raise HTTPException(400, "bad")

    tools = ChatTools(make_services(store, catalog={"stats": stats}))
    ctx = make_ctx(store)
    assert "error" in json.loads(_run(tools.call("locality_stats", '{"locality":"X"}', ctx))[0])
    assert "error" in json.loads(_run(tools.call("nope", "{}", ctx))[0])


# ---- max 3 tool rounds ---------------------------------------------------------------------------------------

def test_at_most_three_tool_rounds():
    llm = FakeLLM(turns=[tool_turn(), tool_turn(), tool_turn(), [{"type": "text", "text": "hotovo"}]])

    class Tools:
        calls = 0

        def schemas(self):
            return [{"type": "function"}]

        async def call(self, name, arguments, ctx):
            Tools.calls += 1
            return "{}", []

    async def collect():
        return [e async for e in ai_chat.run_chat(llm, Tools(), "SYS", [{"role": "user", "content": "x"}], None)]

    events = _run(collect())
    assert [e["text"] for e in events] == ["hotovo"]
    assert len(llm.chat_calls) == 4 and Tools.calls == 3
    assert [c["tools"] is not None for c in llm.chat_calls] == [True, True, True, False]
    assert llm.chat_calls[0]["messages"][0] == {"role": "system", "content": "SYS"}


# ---- summaries -----------------------------------------------------------------------------------------------

def test_summary_generated_once(store):
    llm = FakeLLM(summary_text="Pěkný byt.")
    listing = {"id": 7, "name": "Byt", "price_czk": 20000, "disposition": "2+kk", "area_m2": 50, "locality": "Praha",
               "description": "x" * 5000, "phone": "777123456"}
    assert _run(summary.get_or_create_summary(store, "7", listing, llm)) == "Pěkný byt."
    assert _run(summary.get_or_create_summary(store, "7", listing, llm)) == "Pěkný byt."
    assert llm.complete_calls == 1
    assert store.listing_summary_get("7") == "Pěkný byt."
    text = summary.listing_text(listing)
    assert "777123456" not in text and text.count("x") == 3000


def test_summary_endpoint_generates_once(store, monkeypatch):
    llm = FakeLLM()
    client = chat_app(store, "free", llm, monkeypatch)

    class Catalog(Store):
        def catalog_item(self, *a, **k):
            return {"id": 5, "name": "Byt", "locality": "Brno"}

    cat = Catalog(store.path.parent / "cat.sqlite", seed_webhook_url="")
    from app import ai_routes

    app = FastAPI()
    ai_routes.register(
        app, hub=type("H", (), {"store": store})(), current_user=lambda s: {"id": "u"},
        catalog_store=lambda s, g: (None, None), public_store=lambda: cat,
        services=make_services(store),
    )
    c = TestClient(app)
    assert c.get("/api/listings/5/summary").json() == {"summary": "Shrnutí."}
    assert c.get("/api/listings/5/summary").json() == {"summary": "Shrnutí."}
    assert llm.complete_calls == 1


def test_failed_llm_call_refunds_the_message(store, monkeypatch):
    llm = FakeLLM()

    async def failing(messages, tools):
        raise FakeLLM.LLMError("down")
        yield  # pragma: no cover

    llm.chat_stream = failing
    client = chat_app(store, "free", llm, monkeypatch)
    body = {"message": "Ahoj"}
    for _ in range(15):  # more failures than the free limit: none of them may use up the quota
        r = client.post("/api/chat", json=body)
        assert r.status_code == 200 and '"type": "error"' in r.text
    with store.connect(readonly=True) as conn:
        row = conn.execute("SELECT chat_messages FROM ai_usage WHERE user_id = 'user-1'").fetchone()
    assert row[0] == 0
    assert store.ai_chat_list("user-1") == []  # failed first messages leave no empty chats behind
    store.ai_usage_refund("user-1", limits.today())  # never goes below zero
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT chat_messages FROM ai_usage WHERE user_id = 'user-1'").fetchone()[0] == 0


def test_failed_llm_call_in_existing_chat_drops_only_the_failed_question(store, monkeypatch):
    llm = FakeLLM(turns=[[{"type": "text", "text": "dobře"}]])
    client = chat_app(store, "pro", llm, monkeypatch)
    r = client.post("/api/chat", json={"message": "první"})
    chat_id = json.loads(r.text.split("\n\n")[0][6:])["id"]

    async def failing(messages, tools):
        raise FakeLLM.LLMError("down")
        yield  # pragma: no cover

    llm.chat_stream = failing
    r = client.post("/api/chat", json={"message": "druhá", "chat_id": chat_id})
    assert '"chat_deleted": false' in r.text
    chat = client.get(f"/api/chats/{chat_id}").json()
    assert [m["content"] for m in chat["messages"]] == ["první", "dobře"]
