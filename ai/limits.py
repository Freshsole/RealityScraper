"""Daily chat message limits per plan. The only place where the numbers live."""

from __future__ import annotations

from datetime import datetime
from typing import Any

DAILY_CHAT_LIMITS: dict[str, int] = {
    "free": 10,
    "start": 100,
    "pro": 1000,
    "individual": 1000,
}
UPGRADE_URL = "/#cenik"

# Chat actions (WRITE tools). Confirmed actions do not count as extra chat messages.
MAX_PROPOSALS_PER_MESSAGE = 5
MAX_CONFIRMS_PER_HOUR = 30
PENDING_TTL_SECONDS = 10 * 60
_NO_UPGRADE = {"pro", "individual"}


def limit_for_plan(plan: str) -> int:
    return DAILY_CHAT_LIMITS.get(plan, DAILY_CHAT_LIMITS["free"])


def today(now: datetime | None = None) -> str:
    """Counter day in Czech time (limit resets at local midnight)."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Prague")
    moment = now.astimezone(tz) if now is not None and now.tzinfo else (now or datetime.now(tz))
    return moment.date().isoformat()


def limit_payload(plan: str) -> dict[str, Any]:
    limit = limit_for_plan(plan)
    if plan in _NO_UPGRADE:
        return {"error": "limit", "message": f"Dnes jste vyčerpali denní limit {limit} zpráv. Zítra můžete pokračovat."}
    return {
        "error": "limit",
        "message": f"Dnes jste vyčerpali {limit} zpráv. S vyšším tarifem máte víc.",
        "upgrade_url": UPGRADE_URL,
    }


def consume(store: Any, user_id: str, plan: str, now: datetime | None = None, day: str | None = None) -> dict[str, Any] | None:
    """Count one message (atomic, before the LLM call). None = allowed, else the 429 payload."""
    allowed, _count = store.ai_usage_try_increment(user_id, day or today(now), limit_for_plan(plan))
    return None if allowed else limit_payload(plan)


def refund(store: Any, user_id: str, day: str) -> None:
    """Return the message counted for `day` when the LLM call failed."""
    store.ai_usage_refund(user_id, day)
