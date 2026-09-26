from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

from app import config, portal_health
from app.html_listing import HtmlPortalClient
from app.scrape_engine import ScrapeEngine
from app.scrape_http import scrape_timeout


class PortalHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        portal_health.reset()
        self._fails = config.SCRAPE_PORTAL_FAILS_TO_DISABLE
        self._cool = config.SCRAPE_PORTAL_COOLDOWN_SEC
        self._cap = config.SCRAPE_PORTAL_COOLDOWN_CAP_SEC
        config.SCRAPE_PORTAL_FAILS_TO_DISABLE = 3
        config.SCRAPE_PORTAL_COOLDOWN_SEC = 15 * 60
        config.SCRAPE_PORTAL_COOLDOWN_CAP_SEC = 60 * 60

    def tearDown(self) -> None:
        config.SCRAPE_PORTAL_FAILS_TO_DISABLE = self._fails
        config.SCRAPE_PORTAL_COOLDOWN_SEC = self._cool
        config.SCRAPE_PORTAL_COOLDOWN_CAP_SEC = self._cap
        portal_health.reset()

    def test_trips_after_n_hard_fails_and_skips(self) -> None:
        self.assertFalse(portal_health.is_disabled("mmreality"))
        self.assertFalse(portal_health.record_fail("mmreality", "403"))
        self.assertFalse(portal_health.record_fail("mmreality", "403"))
        self.assertTrue(portal_health.record_fail("mmreality", "403"))
        self.assertTrue(portal_health.is_disabled("mmreality"))
        snap = portal_health.snapshot()["mmreality"]
        self.assertEqual(snap["cooldown_sec"], 30 * 60)
        self.assertTrue(snap["disabled"])

    def test_ok_resets_consecutive(self) -> None:
        portal_health.record_fail("idnes", "timeout")
        portal_health.record_ok("idnes")
        portal_health.record_fail("idnes", "timeout")
        self.assertFalse(portal_health.is_disabled("idnes"))

    def test_cooldown_doubles_capped(self) -> None:
        for _ in range(3):
            portal_health.record_fail("ulovdomov", "500")
        first = portal_health.snapshot()["ulovdomov"]["cooldown_sec"]
        self.assertEqual(first, 30 * 60)
        portal_health._state["ulovdomov"].disabled_until = 0  # type: ignore[attr-defined]
        portal_health.record_fail("ulovdomov", "500")
        self.assertEqual(portal_health.snapshot()["ulovdomov"]["cooldown_sec"], 60 * 60)
        portal_health._state["ulovdomov"].disabled_until = 0  # type: ignore[attr-defined]
        portal_health.record_fail("ulovdomov", "500")
        self.assertEqual(portal_health.snapshot()["ulovdomov"]["cooldown_sec"], 60 * 60)

    def test_429_is_not_hard_fail(self) -> None:
        self.assertFalse(portal_health.is_hard_fail(429))
        self.assertTrue(portal_health.is_hard_fail(403))
        self.assertTrue(portal_health.is_timeout(httpx.ReadTimeout("read")))

    def test_inflight_fail_does_not_retrip(self) -> None:
        for _ in range(3):
            portal_health.record_fail("mmreality", "403")
        until = portal_health._state["mmreality"].disabled_until
        count = portal_health.snapshot()["mmreality"]["disable_count"]
        self.assertFalse(portal_health.record_fail("mmreality", "403"))
        self.assertEqual(portal_health.snapshot()["mmreality"]["disable_count"], count)
        self.assertEqual(portal_health._state["mmreality"].disabled_until, until)

    def test_inflight_ok_does_not_reenable(self) -> None:
        for _ in range(3):
            portal_health.record_fail("mmreality", "403")
        portal_health.record_ok("mmreality")
        self.assertTrue(portal_health.is_disabled("mmreality"))

    def test_correlated_timeouts_are_loop_stall(self) -> None:
        self.assertFalse(portal_health.record_fail("sreality", "timeout"))
        self.assertFalse(portal_health.record_fail("mmreality", "timeout"))
        self.assertFalse(portal_health.record_fail("realitycz", "timeout"))
        self.assertFalse(portal_health.is_disabled("sreality"))
        self.assertFalse(portal_health.is_disabled("mmreality"))
        self.assertFalse(portal_health.is_disabled("realitycz"))
        # First two timeouts were rewound once the stall was recognized.
        self.assertEqual(portal_health.snapshot()["sreality"]["consecutive"], 0)

    def test_isolated_timeouts_still_trip(self) -> None:
        self.assertFalse(portal_health.record_fail("mmreality", "timeout"))
        self.assertFalse(portal_health.record_fail("mmreality", "timeout"))
        self.assertTrue(portal_health.record_fail("mmreality", "timeout"))
        self.assertTrue(portal_health.is_disabled("mmreality"))


class ScrapeTimeoutTests(unittest.TestCase):
    def test_wall_clock_timeout_cancels_hanging_request(self) -> None:
        async def _run() -> float:
            previous = config.SCRAPE_HTTP_TIMEOUT
            config.SCRAPE_HTTP_TIMEOUT = 0.2
            try:
                class Slow:
                    async def request(self, *args, **kwargs):
                        await asyncio.sleep(5)
                        raise AssertionError("should have been cancelled")

                started = time.monotonic()
                with self.assertRaises(httpx.ReadTimeout):
                    from app.scrape_http import bounded_request

                    await bounded_request(Slow(), "GET", "https://example.com/")
                return time.monotonic() - started
            finally:
                config.SCRAPE_HTTP_TIMEOUT = previous

        elapsed = asyncio.run(_run())
        self.assertLess(elapsed, 1.0)

    def test_connect_and_read_are_split(self) -> None:
        timeout = scrape_timeout()
        self.assertEqual(timeout.connect, config.SCRAPE_HTTP_CONNECT_TIMEOUT)
        self.assertEqual(timeout.read, config.SCRAPE_HTTP_TIMEOUT)
        self.assertLess(timeout.connect + timeout.read, 50.0)

    def test_html_client_uses_split_timeout(self) -> None:
        client = HtmlPortalClient("https://www.mmreality.cz/nemovitosti/")
        self.assertEqual(client._client.timeout.read, config.SCRAPE_HTTP_TIMEOUT)
        self.assertEqual(client._client.timeout.connect, config.SCRAPE_HTTP_CONNECT_TIMEOUT)

    def test_list_crawl_does_not_sleep_on_403(self) -> None:
        async def _run() -> float:
            engine = ScrapeEngine()
            slept: list[float] = []

            async def boom(_page: int):
                raise httpx.HTTPStatusError(
                    "403",
                    request=httpx.Request("GET", "https://x"),
                    response=httpx.Response(403),
                )

            real_sleep = asyncio.sleep

            async def fake_sleep(delay: float):
                slept.append(delay)
                await real_sleep(0)

            with patch("app.scrape_engine.asyncio.sleep", new=fake_sleep):
                await engine.fetch_one_page(boom, 1, portal="mmreality")
            return sum(slept)

        self.assertEqual(asyncio.run(_run()), 0.0)

    def test_disabled_portal_skips_http(self) -> None:
        portal_health.reset()
        previous = config.SCRAPE_PORTAL_FAILS_TO_DISABLE
        config.SCRAPE_PORTAL_FAILS_TO_DISABLE = 1
        try:
            portal_health.record_fail("realitycz", "403")

            async def _run() -> tuple[str | None, int]:
                calls = 0

                async def fetch_page(_page: int):
                    nonlocal calls
                    calls += 1
                    return [], 1

                engine = ScrapeEngine()
                result = await engine.fetch_one_page(fetch_page, 1, portal="realitycz")
                return result.error, calls

            error, calls = asyncio.run(_run())
            self.assertEqual(error, "portal-disabled")
            self.assertEqual(calls, 0)
        finally:
            config.SCRAPE_PORTAL_FAILS_TO_DISABLE = previous
            portal_health.reset()
