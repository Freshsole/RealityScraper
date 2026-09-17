"""In-memory marketing HTML + game JSON + /hry* assets served without FastAPI/SQLite."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi.responses import HTMLResponse

from app import config
from app import games as marketing_games

_CACHE_HEADERS = {"Cache-Control": "public, max-age=120"}
_CACHE_CONTROL = b"public, max-age=120"
_ASSET_CACHE_CONTROL = b"public, max-age=2592000"
_JSON_NO_STORE = b"no-store"
_ACAO = b"*"
INSTANT_ROUTES: dict[str, str] = {
    "/": "index.html",
    "/hry": "hry.html",
    "/hry/": "hry.html",
    "/hry/vyssi-nizsi": "hry-vyssi-nizsi.html",
    "/hry/vyssi-nizsi/": "hry-vyssi-nizsi.html",
    "/hry/najem": "hry-najem.html",
    "/hry/najem/": "hry-najem.html",
}
_ASSET_TYPES = {
    ".css": b"text/css; charset=utf-8",
    ".js": b"text/javascript; charset=utf-8",
    ".woff2": b"font/woff2",
    ".svg": b"image/svg+xml",
    ".webp": b"image/webp",
}
INSTANT_ASSET_FILES = (
    "site/site.css",
    "site/games.css",
    "site/games.js",
    "site/assets/logo.svg",
    "site/fonts/archivo-black-latin.woff2",
    "site/fonts/archivo-black-latin-ext.woff2",
    "site/assets/hero-apart.webp",
    "site/assets/sold-1.webp",
    "site/assets/sold-2.webp",
    "site/assets/sold-3.webp",
    "site/assets/sold-4.webp",
    "site/assets/db-1.webp",
    "site/assets/db-2.webp",
    "site/assets/db-3.webp",
)
INSTANT_ASSETS: dict[str, str] = {f"/static/{rel}": rel for rel in INSTANT_ASSET_FILES}
INSTANT_GAME_GET = {
    "/api/public/games/higher-lower",
    "/api/public/games/higher-lower/",
    "/api/public/games/rent-round",
    "/api/public/games/rent-round/",
}
INSTANT_GAME_POST = {
    "/api/public/games/rent-score",
    "/api/public/games/rent-score/",
}

Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]
App = Callable[[dict[str, Any], Receive, Send], Awaitable[None]]


@lru_cache(maxsize=32)
def site_html(name: str) -> str:
    path = config.WEB_DIR / "site" / name
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=32)
def site_body(name: str) -> bytes:
    return site_html(name).encode("utf-8")


@lru_cache(maxsize=64)
def site_asset(rel: str) -> tuple[bytes, bytes]:
    path = Path(config.WEB_DIR) / rel
    suffix = path.suffix.lower()
    content_type = _ASSET_TYPES.get(suffix)
    if content_type is None:
        raise KeyError(rel)
    return path.read_bytes(), content_type


def instant_asset_rel(path: str) -> str | None:
    return INSTANT_ASSETS.get(path)


def preload_site_pages() -> None:
    for name in dict.fromkeys(INSTANT_ROUTES.values()):
        site_body(name)
    for rel in INSTANT_ASSET_FILES:
        site_asset(rel)


def site_page(name: str) -> HTMLResponse:
    return HTMLResponse(site_html(name), headers=_CACHE_HEADERS)


def instant_page_name(path: str) -> str | None:
    name = INSTANT_ROUTES.get(path)
    if name:
        return name
    if path != "/" and path.endswith("/"):
        return INSTANT_ROUTES.get(path[:-1])
    return None


def instant_game_kind(path: str) -> str | None:
    normalized = path[:-1] if path.endswith("/") and path != "/" else path
    if normalized == "/api/public/games/higher-lower":
        return "higher-lower"
    if normalized == "/api/public/games/rent-round":
        return "rent-round"
    if normalized == "/api/public/games/rent-score":
        return "rent-score"
    return None


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    more = True
    while more:
        message = await receive()
        if message.get("type") != "http.request":
            continue
        chunks.append(message.get("body") or b"")
        more = bool(message.get("more_body"))
    return b"".join(chunks)


async def _send_bytes(
    send: Send,
    *,
    status: int,
    body: bytes,
    content_type: bytes,
    cache_control: bytes,
    method: str,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", cache_control),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"" if method == "HEAD" else body,
        }
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class InstantSiteASGI:
    """Outer ASGI app: GET/HEAD `/`, `/hry*`, game JSON, and game assets skip Hub/SQLite."""

    def __init__(self, app: App, store: Any | None = None) -> None:
        self.app = app
        self.store = store
        preload_site_pages()

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            method = str(scope.get("method") or "")
            path = str(scope.get("path") or "")
            if method in {"GET", "HEAD"}:
                rel = instant_asset_rel(path)
                if rel:
                    body, content_type = site_asset(rel)
                    await _send_bytes(
                        send,
                        status=200,
                        body=body,
                        content_type=content_type,
                        cache_control=_ASSET_CACHE_CONTROL,
                        method=method,
                        extra_headers=[(b"access-control-allow-origin", _ACAO)],
                    )
                    return
                name = instant_page_name(path)
                if name:
                    await _send_bytes(
                        send,
                        status=200,
                        body=site_body(name),
                        content_type=b"text/html; charset=utf-8",
                        cache_control=_CACHE_CONTROL,
                        method=method,
                    )
                    return
                kind = instant_game_kind(path)
                if kind in {"higher-lower", "rent-round"}:
                    if kind == "higher-lower":
                        payload = marketing_games.public_higher_lower(self.store)
                    else:
                        payload = marketing_games.public_rent_round(self.store)
                    await _send_bytes(
                        send,
                        status=200,
                        body=_json_bytes(payload),
                        content_type=b"application/json; charset=utf-8",
                        cache_control=_JSON_NO_STORE,
                        method=method,
                    )
                    return
            if method == "POST" and instant_game_kind(path) == "rent-score":
                raw = await _read_body(receive)
                try:
                    body = json.loads(raw.decode("utf-8") or "{}") if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                payload = marketing_games.public_rent_score(self.store, body, allow_db=False)
                status = int(payload.pop("status", 200))
                await _send_bytes(
                    send,
                    status=status,
                    body=_json_bytes(payload),
                    content_type=b"application/json; charset=utf-8",
                    cache_control=_JSON_NO_STORE,
                    method=method,
                )
                return
        await self.app(scope, receive, send)
