"""Shared fake transport for the portal pagination tests (no network)."""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}
        self.content = json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def patch_requests(monkeypatch, handler, calls: list):
    """Replace app.scrape_http.request_with_log; handler(method, url, kwargs) -> FakeResponse."""
    import app.scrape_http as scrape_http

    async def fake(client, method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return handler(method, url, kwargs)

    monkeypatch.setattr(scrape_http, "request_with_log", fake)
