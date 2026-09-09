from __future__ import annotations

from app import config
from app.bezrealitky import BezrealitkyClient
from app.sreality import SrealityClient


def is_bezrealitky(url: str) -> bool:
    return "bezrealitky.cz" in (url or "").lower()


def client_for(search_url: str) -> SrealityClient | BezrealitkyClient:
    if is_bezrealitky(search_url):
        return BezrealitkyClient(search_url)
    return SrealityClient(search_url)


def source_name(url: str) -> str:
    return "Bezrealitky" if is_bezrealitky(url) else "Sreality"


def webhook_for(search_url: str, monitor_webhook: str | None = None) -> str:
    if (monitor_webhook or "").strip():
        return monitor_webhook.strip()
    if is_bezrealitky(search_url):
        return config.BEZREALITKY_WEBHOOK_URL
    return config.DISCORD_WEBHOOK_URL
