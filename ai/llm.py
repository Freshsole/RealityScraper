"""The only module that talks to the LLM (DeepSeek, OpenAI-compatible API).

Imported only when AI_ENABLED is on. Missing LLM_API_KEY / LLM_MODEL fail at import (= at app start).
No fallback model and no retries: a failed call raises LLMError and the caller shows a clear error.
Request content is never logged, only call metadata. No user data is ever passed in here.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

logger = logging.getLogger("realitify.ai")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "").strip()
LLM_MODEL = os.environ.get("LLM_MODEL", "").strip()

_missing = [name for name, value in (("LLM_API_KEY", LLM_API_KEY), ("LLM_MODEL", LLM_MODEL)) if not value]
if _missing:
    raise RuntimeError(f"AI_ENABLED=true, ale chybí proměnné prostředí: {', '.join(_missing)}")

CHAT_MAX_TOKENS = 400
SUMMARY_MAX_TOKENS = 200
TEMPERATURE = 0.3

# max_retries=0: the SDK retries by default, we want one explicit attempt.
client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY, timeout=60, max_retries=0)


class LLMError(Exception):
    """The LLM call failed. The message is safe to show to the user."""


def _log_call(kind: str, usage: Any, started: float) -> None:
    logger.info(
        "llm_call kind=%s model=%s prompt_tokens=%s completion_tokens=%s prompt_cache_hit_tokens=%s duration_ms=%d",
        kind,
        LLM_MODEL,
        getattr(usage, "prompt_tokens", None),
        getattr(usage, "completion_tokens", None),
        getattr(usage, "prompt_cache_hit_tokens", None),
        int((time.monotonic() - started) * 1000),
    )


def _fail(kind: str, exc: Exception, started: float) -> LLMError:
    logger.error(
        "llm_call_failed kind=%s model=%s error=%s status=%s detail=%s duration_ms=%d",
        kind,
        LLM_MODEL,
        type(exc).__name__,
        getattr(exc, "status_code", None),
        str(getattr(exc, "message", "") or exc)[:200],
        int((time.monotonic() - started) * 1000),
    )
    return LLMError("AI asistent je momentálně nedostupný. Zkuste to prosím za chvíli.")


async def chat_stream(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> AsyncIterator[dict[str, Any]]:
    """Stream one model turn. Yields {"type": "text", "text": str} and at most one {"type": "tool_calls", "calls": [...]}."""
    started = time.monotonic()
    kwargs: dict[str, Any] = {}
    if tools:
        kwargs["tools"] = tools
    usage = None
    calls: dict[int, dict[str, str]] = {}
    try:
        stream = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=CHAT_MAX_TOKENS,
            extra_body={"thinking": {"type": "disabled"}},
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield {"type": "text", "text": delta.content}
            for part in delta.tool_calls or []:
                slot = calls.setdefault(part.index, {"id": "", "name": "", "arguments": ""})
                if part.id:
                    slot["id"] = part.id
                if part.function and part.function.name:
                    slot["name"] += part.function.name
                if part.function and part.function.arguments:
                    slot["arguments"] += part.function.arguments
    except Exception as exc:
        raise _fail("chat", exc, started) from exc
    _log_call("chat", usage, started)
    if calls:
        yield {"type": "tool_calls", "calls": [calls[i] for i in sorted(calls)]}


async def complete(messages: list[dict[str, Any]], max_tokens: int = SUMMARY_MAX_TOKENS) -> str:
    """One non-streaming completion (listing summaries)."""
    started = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:
        raise _fail("summary", exc, started) from exc
    _log_call("summary", response.usage, started)
    text = (response.choices[0].message.content or "").strip()
    if not text:
        raise LLMError("AI vrátila prázdnou odpověď.")
    return text
