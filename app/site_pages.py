"""In-memory marketing HTML served without FastAPI, SQLite, or the thread pool."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Awaitable, Callable

from fastapi.responses import HTMLResponse

from app import config

_CACHE_HEADERS = {"Cache-Control": "public, max-age=120"}
_CACHE_CONTROL = b"public, max-age=120"
INSTANT_ROUTES: dict[str, str] = {
    "/": "index.html",
    "/hry": "hry.html",
    "/hry/": "hry.html",
    "/hry/vyssi-nizsi": "hry-vyssi-nizsi.html",
    "/hry/najem": "hry-najem.html",
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


def preload_site_pages() -> None:
    for name in dict.fromkeys(INSTANT_ROUTES.values()):
        site_body(name)


def site_page(name: str) -> HTMLResponse:
    return HTMLResponse(site_html(name), headers=_CACHE_HEADERS)


def instant_page_name(path: str) -> str | None:
    return INSTANT_ROUTES.get(path)


class InstantSiteASGI:
    """Outer ASGI app: GET/HEAD / and /hry* never enter Hub, middleware, or SQLite."""

    def __init__(self, app: App) -> None:
        self.app = app
        preload_site_pages()

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        if scope.get("type") == "http" and scope.get("method") in {"GET", "HEAD"}:
            name = INSTANT_ROUTES.get(str(scope.get("path") or ""))
            if name:
                body = site_body(name)
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"text/html; charset=utf-8"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"cache-control", _CACHE_CONTROL),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"" if scope.get("method") == "HEAD" else body,
                    }
                )
                return
        await self.app(scope, receive, send)
