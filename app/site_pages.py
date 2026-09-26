"""In-memory marketing HTML so /hry* never waits on disk or the default thread pool."""

from __future__ import annotations

from functools import lru_cache

from fastapi.responses import HTMLResponse

from app import config

_CACHE_HEADERS = {"Cache-Control": "public, max-age=120"}


@lru_cache(maxsize=32)
def site_html(name: str) -> str:
    path = config.WEB_DIR / "site" / name
    return path.read_text(encoding="utf-8")


def site_page(name: str) -> HTMLResponse:
    return HTMLResponse(site_html(name), headers=_CACHE_HEADERS)
