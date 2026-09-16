import asyncio
from pathlib import Path

import httpx

from app.block_page import PortalBlocked
from app.mmreality import MmrealityClient
from app.ulovdomov import UlovdomovClient

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
