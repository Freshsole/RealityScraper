import asyncio
from pathlib import Path

import httpx

from app.block_page import PortalBlocked
from app.mmreality import MmrealityClient
from app.ulovdomov import UlovdomovClient, reset_ulov_caches

FIXTURES = Path(__file__).parent / "fixtures"


def test_html_client_raises_on_cloudflare_403():
    html = (FIXTURES / "mm_cloudflare.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=html, headers={"server": "cloudflare", "cf-ray": "test"})

    async def _run() -> None:
        client = MmrealityClient("https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
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


def test_ulov_api_500_then_empty_html_blocks_portal():
    reset_ulov_caches()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(500, json={"error": "udBe.internalServerError", "success": False, "data": None})
        return httpx.Response(
            200,
            text='<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"count":0}}}</script></html>',
        )

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            try:
                await client.fetch_page(1)
            except PortalBlocked as exc:
                assert exc.kind == "server_error"
                assert exc.status_code == 500
            else:
                raise AssertionError("expected PortalBlocked after empty fallbacks")
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()


def test_ulov_api_500_uses_sitemap_and_skips_html():
    reset_ulov_caches()
    xml = (FIXTURES / "ulov_sitemap_offers.xml").read_text()
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            return httpx.Response(500, json={"error": "udBe.internalServerError", "success": False, "data": None})
        if "sitemap-offers" in str(request.url):
            return httpx.Response(200, text=xml, headers={"content-type": "application/xml"})
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    async def _run() -> None:
        client = UlovdomovClient("https://www.ulovdomov.cz/pronajem/byty")
        await client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            listings, total = await client.fetch_page(1)
            assert total == 2
            assert [item.id for item in listings] == [3496443, 2037015]
            assert listings[0].disposition == "2+kk"
            assert "Brno" in (listings[0].locality or listings[0].name)
            assert all("/inzerat/" in item.url for item in listings)
            sale_client = UlovdomovClient("https://www.ulovdomov.cz/prodej/byty")
            await sale_client.aclose()
            sale_client._client = client._client
            sales, sale_total = await sale_client.fetch_page(1)
            assert sale_total == 1
            assert sales[0].id == 5669330
            assert not any(path.endswith("/pronajem/byty") or path.endswith("/prodej/byty") for path in hits)
        finally:
            await client.aclose()

    asyncio.run(_run())
    reset_ulov_caches()
