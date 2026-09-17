"""Worker-only HTTP(S) proxy for hard-CF list fetches (M&M).

Never imported by InstantSiteASGI. Off unless SCRAPE_HTTP_PROXY / SCRAPE_HTTPS_PROXY
is set, and always disabled when SCRAPE_ROLE=web so /hry* and dashboard shells
cannot pay for residential egress. Generic HTTP_PROXY is not read on purpose.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from app import config


def allowed(portal: str) -> bool:
    return config.scrape_proxy_allowed(portal)


def url_for(portal: str) -> str:
    if not allowed(portal):
        return ""
    return config.scrape_proxy_url()


def httpx_kwargs(portal: str) -> dict[str, Any]:
    """Extra AsyncClient kwargs. Empty when the portal must stay on datacenter egress."""
    proxy = url_for(portal)
    if not proxy:
        return {}
    return {"proxy": proxy, "trust_env": False}


def redacted(url: str) -> str:
    """Host:port form safe to log — credentials stripped."""
    parts = urlsplit((url or "").strip())
    if not parts.hostname:
        return ""
    netloc = parts.hostname
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username or parts.password:
        netloc = f"***@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def playwright_proxy(url: str) -> dict[str, str] | None:
    parts = urlsplit((url or "").strip())
    if not parts.hostname:
        return None
    server = f"{parts.scheme or 'http'}://{parts.hostname}"
    if parts.port:
        server = f"{server}:{parts.port}"
    cfg: dict[str, str] = {"server": server}
    if parts.username:
        cfg["username"] = unquote(parts.username)
    if parts.password:
        cfg["password"] = unquote(parts.password)
    return cfg


def chrome_proxy_server(url: str) -> str:
    cfg = playwright_proxy(url)
    return cfg["server"] if cfg else ""


def attached_proxy_url(client: Any) -> str:
    """Best-effort proxy URL from an httpx client (tests / diagnostics)."""
    mounts = getattr(client, "_mounts", None) or {}
    for transport in mounts.values():
        pool = getattr(transport, "_pool", None)
        raw = getattr(pool, "_proxy_url", None)
        if raw is None:
            continue
        scheme = getattr(raw, "scheme", None)
        host = getattr(raw, "host", None)
        port = getattr(raw, "port", None)
        if isinstance(scheme, bytes):
            scheme = scheme.decode()
        if isinstance(host, bytes):
            host = host.decode()
        if host:
            netloc = f"{host}:{port}" if port else str(host)
            return f"{scheme or 'http'}://{netloc}"
        text = str(raw)
        if text.startswith("http"):
            return text.rstrip("/")
    return ""
