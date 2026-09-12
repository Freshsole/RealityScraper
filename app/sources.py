from __future__ import annotations

from urllib.parse import urlparse

from app import config
from app.bezrealitky import BezrealitkyClient
from app.idnes import IdnesClient
from app.sreality import SrealityClient


def is_bezrealitky(url: str) -> bool:
    return "bezrealitky.cz" in (url or "").lower()


def is_idnes(url: str) -> bool:
    raw = (url or "").lower()
    return "reality.idnes.cz" in raw or "idnes.cz/s/" in raw or "idnes.cz/detail/" in raw


def portal_of(url: str) -> str:
    if is_idnes(url):
        return "idnes"
    if is_bezrealitky(url):
        return "bezrealitky"
    return "sreality"


def client_for(search_url: str) -> SrealityClient | BezrealitkyClient | IdnesClient:
    if is_idnes(search_url):
        return IdnesClient(search_url)
    if is_bezrealitky(search_url):
        return BezrealitkyClient(search_url)
    return SrealityClient(search_url)


def source_name(url: str) -> str:
    portal = portal_of(url)
    return {"idnes": "Reality.iDNES", "bezrealitky": "Bezrealitky"}.get(portal, "Sreality")


def is_discord_webhook(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and "/api/webhooks/" in (parsed.path or "")
        and (host == "discord.com" or host == "discordapp.com" or host.endswith(".discord.com"))
    )


def usable_discord_webhook(url: str) -> str:
    raw = (url or "").strip()
    if not raw or not is_discord_webhook(raw):
        return ""
    lowered = raw.lower()
    if "webhooks/id/token" in lowered or "/webhooks/id/" in lowered:
        return ""
    parts = [part for part in urlparse(raw).path.split("/") if part]
    try:
        index = parts.index("webhooks")
        hook_id = parts[index + 1]
        token = parts[index + 2]
    except (ValueError, IndexError):
        return ""
    if not hook_id.isdigit() or token.lower() in {"token", "id"} or len(token) < 20:
        return ""
    return raw


def webhook_for(search_url: str, monitor_webhook: str | None = None) -> str:
    if (monitor_webhook or "").strip():
        return usable_discord_webhook(monitor_webhook)
    if is_idnes(search_url):
        return usable_discord_webhook(getattr(config, "IDNES_WEBHOOK_URL", "") or config.DISCORD_WEBHOOK_URL)
    if is_bezrealitky(search_url):
        return usable_discord_webhook(config.BEZREALITKY_WEBHOOK_URL)
    return usable_discord_webhook(config.DISCORD_WEBHOOK_URL)
