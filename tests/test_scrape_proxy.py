import asyncio
from pathlib import Path

import httpx

from app.annonce import AnnonceClient
from app.block_page import PortalBlocked
from app.browser_fetch import BrowserFetchResult, fetch_html
from app.mmreality import MmrealityClient
from app.scrape_proxy import (
    allowed,
    attached_proxy_url,
    httpx_kwargs,
    playwright_proxy,
    redacted,
    url_for,
)

FIXTURES = Path(__file__).parent / "fixtures"
MM_LIST = """
<a href="/nemovitosti/pronajem-bytu-2kk-praha-778899">
  <h3>Pronájem bytu 2+kk, Praha 4</h3>
  <span>19 800 Kč</span>
  <img src="https://cdn.mmreality.cz/foto.jpg" />
</a>
"""
PROXY = "http://user:pass@proxy.test:8080"


def _worker_proxy(monkeypatch, url: str = PROXY, portals: tuple[str, ...] = ("mmreality",)) -> None:
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.SCRAPE_HTTP_PROXY", url)
    monkeypatch.setattr("app.config.SCRAPE_HTTPS_PROXY", "")
    monkeypatch.setattr("app.config.SCRAPE_PROXY_PORTALS", frozenset(portals))


def test_proxy_disabled_on_web_role(monkeypatch):
    _worker_proxy(monkeypatch)
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    assert allowed("mmreality") is False
    assert url_for("mmreality") == ""
    assert httpx_kwargs("mmreality") == {}
    client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
    try:
        assert client._proxy_url == ""
        assert attached_proxy_url(client._client) == ""
    finally:
        asyncio.run(client.aclose())


def test_proxy_opt_in_on_worker(monkeypatch):
    _worker_proxy(monkeypatch)
    assert allowed("mmreality") is True
    assert allowed("annonce") is False
    assert url_for("mmreality") == PROXY
    assert httpx_kwargs("mmreality") == {"proxy": PROXY, "trust_env": False}
    assert httpx_kwargs("annonce") == {}
    client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
    try:
        assert client._proxy_url == PROXY
        assert attached_proxy_url(client._client) == "http://proxy.test:8080"
    finally:
        asyncio.run(client.aclose())


def test_generic_http_proxy_env_is_ignored(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://system-proxy:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://system-proxy:8080")
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.SCRAPE_HTTP_PROXY", "")
    monkeypatch.setattr("app.config.SCRAPE_HTTPS_PROXY", "")
    monkeypatch.setattr("app.config.SCRAPE_PROXY_PORTALS", frozenset({"mmreality"}))
    assert url_for("mmreality") == ""
    assert httpx_kwargs("mmreality") == {}


def test_host_port_proxy_gets_http_scheme(monkeypatch):
    _worker_proxy(monkeypatch, url="proxy.test:8080")
    assert url_for("mmreality") == "http://proxy.test:8080"


def test_other_html_portals_stay_direct(monkeypatch):
    _worker_proxy(monkeypatch)
    client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html?nabidkovy=1")
    try:
        assert client._proxy_url == ""
        assert attached_proxy_url(client._client) == ""
    finally:
        asyncio.run(client.aclose())


def test_allowlist_can_include_other_html_portals(monkeypatch):
    _worker_proxy(monkeypatch, portals=("mmreality", "annonce"))
    client = AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html?nabidkovy=1")
    try:
        assert client._proxy_url == PROXY
        assert attached_proxy_url(client._client) == "http://proxy.test:8080"
    finally:
        asyncio.run(client.aclose())


def test_redacted_proxy_strips_credentials():
    assert redacted(PROXY) == "http://***@proxy.test:8080"
    assert playwright_proxy(PROXY) == {
        "server": "http://proxy.test:8080",
        "username": "user",
        "password": "pass",
    }


def test_mmreality_list_via_mocked_proxy_transport(monkeypatch):
    _worker_proxy(monkeypatch)
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, text=MM_LIST)

    async def _run() -> None:
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
        try:
            assert client._proxy_url == PROXY
            assert attached_proxy_url(client._client) == "http://proxy.test:8080"
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            listings, total = await client.fetch_page(1)
            assert len(listings) == 1
            assert listings[0].id == 778899
            assert listings[0].price_czk == 19800
            assert total == 1
            assert hits and "mmreality.cz" in hits[0]
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_mmreality_without_proxy_still_hard_blocks(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.SCRAPE_HTTP_PROXY", "")
    monkeypatch.setattr("app.config.SCRAPE_HTTPS_PROXY", "")
    monkeypatch.setattr("app.config.SCRAPE_PROXY_PORTALS", frozenset({"mmreality"}))
    html = (FIXTURES / "mm_cloudflare.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=html, headers={"server": "cloudflare", "cf-ray": "test"})

    async def _run() -> None:
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
        try:
            assert client._proxy_url == ""
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                await client.fetch_page(1)
            except PortalBlocked as exc:
                assert exc.kind == "cloudflare"
                assert exc.status_code == 403
                assert exc.portal == "mmreality"
            else:
                raise AssertionError("expected PortalBlocked")
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_mocked_proxy_transport_does_not_invent_listings_on_hard_block(monkeypatch):
    _worker_proxy(monkeypatch)
    html = (FIXTURES / "mm_cloudflare.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=html, headers={"server": "cloudflare", "cf-ray": "test"})

    async def _run() -> None:
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
        try:
            await client.aclose()
            client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                await client.fetch_page(1)
            except PortalBlocked as exc:
                assert exc.kind == "cloudflare"
                assert exc.detail == "hard"
            else:
                raise AssertionError("expected PortalBlocked")
        finally:
            await client.aclose()

    asyncio.run(_run())


def test_fetch_html_forwards_proxy_to_backends(monkeypatch):
    _worker_proxy(monkeypatch)
    seen: dict[str, str] = {}

    async def fake_curl(url: str, timeout: float, headers: dict, proxy: str = ""):
        seen["proxy"] = proxy
        return BrowserFetchResult(200, "<html>ok</html>", {}, "curl_cffi")

    monkeypatch.setattr("app.browser_fetch._curl_cffi_fetch", fake_curl)

    async def _run() -> None:
        result = await fetch_html("https://www.mmreality.cz/nemovitosti/", portal="mmreality")
        assert result.backend == "curl_cffi"
        assert seen["proxy"] == PROXY
        seen.clear()
        result = await fetch_html("https://www.mmreality.cz/nemovitosti/")
        assert seen["proxy"] == ""

    asyncio.run(_run())


def test_instant_site_stays_off_scrape_proxy_path():
    src = Path("app/site_pages.py").read_text()
    assert "scrape_proxy" not in src
    assert "SCRAPE_HTTP_PROXY" not in src
    assert "SCRAPE_HTTPS_PROXY" not in src
    assert "browser_fetch" not in src
    assert "html_listing" not in src
    assert "mmreality" not in src
