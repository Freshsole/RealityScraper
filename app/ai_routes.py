"""AI routes (chat + listing summary). Imported by main.py only when AI_ENABLED is on."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Cookie, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from ai import actions, limits, llm, summary
from ai import chat as ai_chat
from ai.context import Services, ToolContext
from ai.tools import ChatTools
from app import billing, rate_limit
from app.store import CATALOG_MONITOR_ID

logger = logging.getLogger("realitify.ai")

# Loaded once at startup and sent unchanged as the first message (DeepSeek caches the prefix).
SYSTEM_PROMPT = (Path(__file__).resolve().parent.parent / "ai" / "prompts" / "chat_system.md").read_text(encoding="utf-8").strip()


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def register(
    app: FastAPI,
    *,
    hub: Any,
    current_user: Callable[[str | None], dict[str, Any]],
    catalog_store: Callable[[str | None, str | None], tuple[Any, Any]],
    public_store: Callable[[], Any],
    services: Services,
) -> None:
    tools = ChatTools(services)

    async def make_ctx(user: dict[str, Any], chat_id: str | None = None) -> ToolContext:
        user_id = str(user.get("id"))
        ustore = hub.store_for(user_id)
        plan = str((await asyncio.to_thread(billing.billing_state, ustore)).get("plan") or "free")
        return ToolContext(user_id=user_id, ustore=ustore, plan=plan, chat_id=chat_id)

    @app.get("/api/ai/enabled")
    async def api_ai_enabled(realitify_session: str | None = Cookie(default=None, alias="realitify_session")):
        """Registered only with AI_ENABLED: the frontend widget treats a 404 here as "AI is off"."""
        try:
            current_user(realitify_session)
            logged_in = True
        except HTTPException:
            logged_in = False
        return {"enabled": True, "logged_in": logged_in, "upgrade_url": limits.UPGRADE_URL}

    @app.post("/api/chat")
    async def api_chat(
        payload: dict[str, Any] = Body(...),
        realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    ):
        """Body: {"message": str, "chat_id": str | null}. History is read from the DB, never trusted from the client."""
        user = current_user(realitify_session)  # 401 when not logged in
        user_id = str(user.get("id"))
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(400, "message must be a non-empty string")
        message = message.strip()[: ai_chat.MAX_MESSAGE_CHARS]
        chat_id = payload.get("chat_id")
        if chat_id is not None and not (isinstance(chat_id, str) and await asyncio.to_thread(hub.store.ai_chat_owned, user_id, chat_id)):
            raise HTTPException(404, "Konverzace neexistuje")
        ctx = await make_ctx(user)
        plan = ctx.plan
        await asyncio.to_thread(hub.store.ai_actions_expire, user_id)
        day = limits.today()
        blocked = await asyncio.to_thread(limits.consume, hub.store, user_id, plan, None, day)
        if blocked:
            return JSONResponse(blocked, status_code=429)

        new_chat = chat_id is None
        if new_chat:
            chat_id = await asyncio.to_thread(hub.store.ai_chat_create, user_id, message)
        ctx.chat_id = chat_id
        user_msg_id = await asyncio.to_thread(hub.store.ai_chat_add_message, chat_id, "user", message)
        stored = await asyncio.to_thread(hub.store.ai_chat_recent_messages, chat_id, ai_chat.HISTORY_LIMIT)
        history = ai_chat.sanitize_history(stored)

        async def undo() -> None:
            """The LLM failed: give the message back and drop what was saved for it."""
            await asyncio.to_thread(limits.refund, hub.store, user_id, day)
            if new_chat:
                await asyncio.to_thread(hub.store.ai_chat_delete, user_id, chat_id)
            else:
                await asyncio.to_thread(hub.store.ai_chat_delete_message, user_msg_id)

        async def stream():
            text = ""
            cards: list[dict[str, Any]] = []
            yield _sse({"type": "chat", "id": chat_id})
            try:
                async for event in ai_chat.run_chat(llm, tools, SYSTEM_PROMPT, history, ctx):
                    if event["type"] == "delta":
                        text += event["text"]
                    elif event["type"] == "cards":
                        cards.extend(event["items"])
                    yield _sse(event)
                if text or cards:
                    await asyncio.to_thread(hub.store.ai_chat_add_message, chat_id, "assistant", text, cards or None)
                yield _sse({"type": "done"})
            except llm.LLMError as exc:
                await undo()
                yield _sse({"type": "error", "message": str(exc), "chat_deleted": new_chat})
            except Exception as exc:
                logger.error("chat_failed error=%s", type(exc).__name__, exc_info=True)
                yield _sse({"type": "error", "message": "Něco se nepovedlo. Zkuste to prosím znovu."})

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
        )

    @app.get("/api/chats")
    async def api_chats(realitify_session: str | None = Cookie(default=None, alias="realitify_session")):
        user = current_user(realitify_session)
        return {"items": await asyncio.to_thread(hub.store.ai_chat_list, str(user.get("id")))}

    @app.get("/api/chats/{chat_id}")
    async def api_chat_get(chat_id: str, realitify_session: str | None = Cookie(default=None, alias="realitify_session")):
        user = current_user(realitify_session)
        chat = await asyncio.to_thread(hub.store.ai_chat_get, str(user.get("id")), chat_id)
        if not chat:
            raise HTTPException(404, "Konverzace neexistuje")
        chat["pending"] = await asyncio.to_thread(hub.store.ai_actions_pending_for_chat, str(user.get("id")), chat_id)
        return chat

    @app.delete("/api/chats/{chat_id}")
    async def api_chat_delete(chat_id: str, realitify_session: str | None = Cookie(default=None, alias="realitify_session")):
        user = current_user(realitify_session)
        if not await asyncio.to_thread(hub.store.ai_chat_delete, str(user.get("id")), chat_id):
            raise HTTPException(404, "Konverzace neexistuje")
        return {"ok": True}

    @app.post("/api/chat/actions/{action_id}/confirm")
    async def api_action_confirm(
        action_id: str, realitify_session: str | None = Cookie(default=None, alias="realitify_session")
    ):
        user = current_user(realitify_session)
        if not rate_limit.check("ai_confirm", str(user.get("id")), limits.MAX_CONFIRMS_PER_HOUR, 3600.0):
            raise HTTPException(429, "Příliš mnoho potvrzených akcí za hodinu. Zkuste to později.")
        try:
            return await actions.confirm(services, await make_ctx(user), action_id)
        except actions.ActionRefused as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.post("/api/chat/actions/{action_id}/cancel")
    async def api_action_cancel(
        action_id: str, realitify_session: str | None = Cookie(default=None, alias="realitify_session")
    ):
        user = current_user(realitify_session)
        try:
            return await actions.cancel(services, await make_ctx(user), action_id)
        except actions.ActionRefused as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @app.get("/api/listings/{listing_id}/summary")
    async def api_listing_summary(
        listing_id: str,
        realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
        realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
    ):
        catalog_store(realitify_session, realitify_guest)  # 401 unless logged in or e-mail guest
        store = public_store() or hub.store
        item = await asyncio.to_thread(store.catalog_item, CATALOG_MONITOR_ID, listing_id)
        if not item:
            raise HTTPException(404, "Nabídka se nenašla")
        try:
            text = await summary.get_or_create_summary(store, str(item.get("id")), item, llm)
        except llm.LLMError as exc:
            return JSONResponse({"error": "llm", "message": str(exc)}, status_code=502)
        return {"summary": text}
