import asyncio
import time
import unittest

import httpx

from app import portal_pacing
from app.scrape_http import request_with_log


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class PortalPacingTests(unittest.TestCase):
    def setUp(self):
        portal_pacing.reset()

    def test_idnes_is_spaced_by_default(self):
        self.assertEqual(portal_pacing.min_interval("idnes"), 1.0)
        self.assertEqual(portal_pacing.min_interval("bazos"), 0.0)

    def test_requests_are_spaced_and_shared_between_tasks(self):
        stamps: list[float] = []

        def handler(request):
            stamps.append(time.monotonic())
            return httpx.Response(200, text="ok")

        async def run():
            old = portal_pacing.DEFAULT_MIN_INTERVAL["idnes"]
            portal_pacing.DEFAULT_MIN_INTERVAL["idnes"] = 0.08
            try:
                async with _client(handler) as client:
                    await asyncio.gather(
                        *(request_with_log(client, "GET", "https://reality.idnes.cz/x", portal="idnes") for _ in range(4))
                    )
            finally:
                portal_pacing.DEFAULT_MIN_INTERVAL["idnes"] = old

        asyncio.run(run())
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(stamps), 4)
        self.assertTrue(all(gap >= 0.08 * 0.7 for gap in gaps), gaps)

    def test_unpaced_portal_is_not_delayed(self):
        async def run():
            async with _client(lambda r: httpx.Response(200)) as client:
                t0 = time.monotonic()
                for _ in range(5):
                    await request_with_log(client, "GET", "https://reality.bazos.cz/", portal="bazos")
                return time.monotonic() - t0

        self.assertLess(asyncio.run(run()), 0.5)

    def test_429_starts_cooldown_and_blocks_further_requests_locally(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "3600"}, text="Too many")

        async def run():
            async with _client(handler) as client:
                first = await request_with_log(client, "GET", "https://reality.idnes.cz/a", portal="idnes")
                self.assertEqual(first.status_code, 429)
                with self.assertRaises(httpx.HTTPStatusError) as ctx:
                    await request_with_log(client, "GET", "https://reality.idnes.cz/b", portal="idnes")
                self.assertEqual(ctx.exception.response.status_code, 429)

        asyncio.run(run())
        self.assertEqual(calls["n"], 1)  # second call never hit the network
        self.assertGreater(portal_pacing.cooldown_remaining("idnes"), 3000)

    def test_success_resets_strikes_and_cooldown_expires(self):
        resp = httpx.Response(429, request=httpx.Request("GET", "https://x/"))  # no Retry-After
        portal_pacing.note_response("idnes", resp)
        self.assertGreaterEqual(portal_pacing.cooldown_remaining("idnes"), 200)
        portal_pacing._cooldown_until["idnes"] = time.monotonic() - 1
        portal_pacing.check_cooldown("idnes")  # expired -> no raise
        portal_pacing.note_response("idnes", httpx.Response(200, request=httpx.Request("GET", "https://x/")))
        self.assertNotIn("idnes", portal_pacing._strikes)

    def test_other_portals_unaffected_by_idnes_cooldown(self):
        portal_pacing.note_response("idnes", httpx.Response(429, headers={"Retry-After": "600"}, request=httpx.Request("GET", "https://x/")))
        portal_pacing.check_cooldown("bazos")
        portal_pacing.check_cooldown("sreality")


if __name__ == "__main__":
    unittest.main()
