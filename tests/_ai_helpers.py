"""Shared fakes for the AI tests: a scripted LLM, injectable services and a bare FastAPI app with the AI routes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ai.context import Services, ToolContext
from app.sources import url_mod_for
from app.store import Store


class FakeLLM:
    LLM_MODEL = "fake-model"

    class LLMError(Exception):
        pass

    def __init__(self, turns=None, summary_text="Shrnutí."):
        self.turns = list(turns or [])
        self.chat_calls = []
        self.complete_calls = 0
        self.summary_text = summary_text

    async def chat_stream(self, messages, tools):
        self.chat_calls.append({"messages": messages, "tools": tools})
        for event in self.turns.pop(0):
            yield event

    async def complete(self, messages, max_tokens=200):
        self.complete_calls += 1
        return self.summary_text


def tool_turn(name="locality_stats", arguments='{"locality":"Brno"}', call_id="c1"):
    return [{"type": "tool_calls", "calls": [{"id": call_id, "name": name, "arguments": arguments}]}]


async def _nothing(**kw):
    return {}


def _never(**kw):
    raise AssertionError("network service called in a test")


def make_services(store, catalog=None, **over):
    cat = {k: _nothing for k in ("search", "item", "stats", "price_check", "compare")}
    cat.update(catalog or {})
    values = dict(
        global_store=store,
        public_base_url="https://realitify.cz",
        catalog=cat,
        user_catalog=lambda s: None,
        public_store=lambda: None,
        geocode=_never,
        resolve_locality=_never,
        route_times=_never,
        filter_mod=lambda source=None, url="": url_mod_for(source, url or ""),
        check_once=_never,
        discord_test=_never,
        on_event=lambda name, props: None,
        after_listing_user=lambda s: None,
        track_monitor_created=lambda: None,
    )
    values.update(over)
    return Services(**values)


def make_ctx(ustore, plan="free", user_id="user-1", chat_id=None):
    ustore.save_billing_record({"plan": plan})  # the plan lives in the user's store, like in the app
    return ToolContext(user_id=user_id, ustore=ustore, plan=plan, chat_id=chat_id)


def chat_app(store, plan, llm, monkeypatch, services=None, users=None):
    """Bare app with the AI routes. Cookie "ok" is user-1; `users` maps other cookie values to user ids."""
    monkeypatch.setenv("LLM_API_KEY", "test")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    from app import ai_routes

    monkeypatch.setattr(ai_routes, "llm", llm)
    user_stores: dict[str, Store] = {}

    def store_for(uid):
        if uid not in user_stores:
            s = Store(store.path.parent / f"user-{uid}.sqlite", seed_webhook_url="")
            s.save_billing_record(plan if isinstance(plan, dict) else {"plan": plan})
            user_stores[uid] = s
        return user_stores[uid]

    class Hub:
        pass

    hub = Hub()
    hub.store = store
    hub.store_for = store_for
    cookies = {"ok": "user-1", **(users or {})}

    def current_user(session):
        if session not in cookies:
            raise HTTPException(401, "Nejste přihlášeni")
        return {"id": cookies[session]}

    app = FastAPI()
    ai_routes.register(
        app,
        hub=hub,
        current_user=current_user,
        catalog_store=lambda s, g: (None, None),
        public_store=lambda: store,
        services=services or make_services(store),
    )
    client = TestClient(app)
    client.cookies.set("realitify_session", "ok")
    client.hub = hub
    return client


def as_user(client, cookie):
    other = TestClient(client.app)
    other.cookies.set("realitify_session", cookie)
    return other
