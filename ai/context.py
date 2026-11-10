"""Shared types for the chat tools: per-request context (user taken from the session) and injected services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class ToolError(Exception):
    """The tool cannot run. The message is returned to the model as the tool result (and shown to the user)."""


@dataclass
class ToolContext:
    """Who is chatting. Filled by the server from the session, never from a model argument."""

    user_id: str
    ustore: Any  # the user's private Store
    plan: str
    chat_id: str | None = None
    proposals: int = 0  # WRITE actions proposed during the current user message


@dataclass
class Services:
    """Everything the tools need from the app, injected so ai/ does not import main.py."""

    global_store: Any  # hub.store: pending actions and the audit log live here
    public_base_url: str
    catalog: dict[str, Callable[..., Awaitable[Any]]]  # search, item, stats, price_check, compare
    user_catalog: Callable[[Any], Any]  # user's store -> shared catalog scoped to that user
    public_store: Callable[[], Any]
    geocode: Callable[..., Awaitable[Any]]
    resolve_locality: Callable[..., Awaitable[Any]]
    route_times: Callable[..., Awaitable[Any]]
    filter_mod: Callable[..., Any]
    check_once: Callable[[str, str | None], Awaitable[Any]]
    discord_test: Callable[[str, str | None], Awaitable[Any]]
    on_event: Callable[[str, dict[str, Any]], None]
    after_listing_user: Callable[[Any], None]
    track_monitor_created: Callable[[], None]
