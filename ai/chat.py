"""Chat turn logic: history sanitising and the bounded tool-calling loop. No LLM import here (the module is injected)."""

from __future__ import annotations

from typing import Any, AsyncIterator

HISTORY_LIMIT = 6
MAX_TOOL_ROUNDS = 3
MAX_MESSAGE_CHARS = 2000


def sanitize_history(raw: Any) -> list[dict[str, str]]:
    """Last 6 user/assistant messages. Anything else (system, tool, junk) from the client is dropped."""
    if not isinstance(raw, list):
        raise ValueError("messages must be a list")
    clean = []
    for msg in raw:
        if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            clean.append({"role": msg["role"], "content": content.strip()[:MAX_MESSAGE_CHARS]})
    clean = clean[-HISTORY_LIMIT:]
    if not clean or clean[-1]["role"] != "user":
        raise ValueError("last message must be from the user")
    return clean


async def run_chat(
    llm: Any, tools: Any, system_prompt: str, history: list[dict[str, str]], ctx: Any
) -> AsyncIterator[dict[str, Any]]:
    """Yield {"type": "delta", "text"} events. Up to 3 rounds may call tools; the 4th request has no tools."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}, *history]
    for round_no in range(MAX_TOOL_ROUNDS + 1):
        offered = tools.schemas() if round_no < MAX_TOOL_ROUNDS else None
        calls: list[dict[str, str]] = []
        text = ""
        async for event in llm.chat_stream(messages, offered):
            if event["type"] == "text":
                text += event["text"]
                yield {"type": "delta", "text": event["text"]}
            elif event["type"] == "tool_calls":
                calls = event["calls"]
        if not calls or offered is None:
            return
        messages.append(
            {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls
                ],
            }
        )
        for c in calls:
            content, events = await tools.call(c["name"], c["arguments"], ctx)
            for event in events:
                yield event
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": content})
