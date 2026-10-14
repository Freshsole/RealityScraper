"""Fake transport for the HTML/XHR portal pagination tests (no network, no parse processes)."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "portal_paging"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class HtmlResponse:
    def __init__(self, body: str | bytes, status_code: int = 200, url: str = ""):
        self.content = body if isinstance(body, bytes) else body.encode("utf-8")
        self.text = self.content.decode("utf-8")
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def patch_http(monkeypatch, handler, calls: list):
    """handler(method, url, kwargs) -> HtmlResponse. Also parses inline (no process pool)."""
    import app.parse_pool as parse_pool
    import app.scrape_http as scrape_http

    async def fake(client, method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return handler(method, url, kwargs)

    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(scrape_http, "request_with_log", fake)
    monkeypatch.setattr(parse_pool, "call", inline)
