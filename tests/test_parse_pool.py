"""Parse offload: same results as in-process, never worse than the old thread path."""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest import mock

from app import config, parse_pool
from app.annonce import AnnonceClient
from app.bazos import BazosClient
from app.ceskereality import CeskerealityClient
from app.idnes import IdnesClient
from app.mmreality import MmrealityClient
from app.remax import RemaxClient
from app.sreality import ListingGone, parse_next_data_blob

import test_extra_portals as portals  # noqa: E402  (fixtures with sample HTML)
import test_bazos as bazos_fixtures  # noqa: E402


def run(coro):
    return asyncio.run(coro)


class PoolCase(unittest.TestCase):
    procs = 2

    def setUp(self) -> None:
        parse_pool.reset_pool(kill=True)
        parse_pool._disabled_until = 0.0
        for key in parse_pool.stats:
            parse_pool.stats[key] = 0
        patcher = mock.patch.object(config, "SCRAPE_PARSE_PROCS", self.procs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(parse_pool.reset_pool, kill=True)


class ProcessExecutionTests(PoolCase):
    def test_runs_in_another_process(self) -> None:
        pid = run(parse_pool.call(parse_pool._debug_pid))
        self.assertNotEqual(pid, os.getpid())
        self.assertEqual(parse_pool.stats["process"], 1)

    def test_disabled_pool_uses_threads(self) -> None:
        with mock.patch.object(config, "SCRAPE_PARSE_PROCS", 0):
            pid = run(parse_pool.call(parse_pool._debug_pid))
        self.assertEqual(pid, os.getpid())
        self.assertEqual(parse_pool.stats["thread"], 1)

    def test_unpicklable_arguments_fall_back_to_a_thread(self) -> None:
        result = run(parse_pool.call(lambda: 41 + 1))  # a lambda cannot be shipped to a process
        self.assertEqual(result, 42)
        self.assertEqual(parse_pool.stats["fallback"], 1)

    def test_broken_pool_falls_back_and_cools_down(self) -> None:
        with mock.patch.object(parse_pool, "_get_pool", side_effect=OSError("no fork")):
            first = run(parse_pool.call(parse_pool._debug_pid))
        self.assertEqual(first, os.getpid())
        self.assertFalse(parse_pool.enabled(), "pool must stay off during the cooldown")
        second = run(parse_pool.call(parse_pool._debug_pid))
        self.assertEqual(second, os.getpid())
        parse_pool._disabled_until = 0.0
        self.assertTrue(parse_pool.enabled())

    def test_timeout_raises_and_kills_the_pool(self) -> None:
        with mock.patch.object(parse_pool, "TIMEOUT_S", 0.5):
            with self.assertRaises(TimeoutError):
                run(parse_pool.call(parse_pool._debug_sleep, 30))
        self.assertEqual(parse_pool.stats["timeout"], 1)
        # A fresh pool is created for the next call.
        self.assertNotEqual(run(parse_pool.call(parse_pool._debug_pid)), os.getpid())


class ParserEquivalenceTests(PoolCase):
    """Parsing through the pool gives exactly the in-process result for every portal."""

    def _same(self, client, html: str) -> None:
        blob = html.encode("utf-8")
        direct = client._parse_blob(blob, 1)
        pooled = run(parse_pool.call(client._parse_blob, blob, 1))
        self.assertEqual(pooled, direct)
        self.assertTrue(direct[0], "fixture should yield at least one listing")

    def test_html_portals(self) -> None:
        self._same(CeskerealityClient("https://www.ceskereality.cz/pronajem/byty/"), portals.CESKE)
        self._same(AnnonceClient("https://www.annonce.cz/byty-k-pronajmu.html"), portals.ANNONCE)
        self._same(RemaxClient("https://www.remax-czech.cz/reality/byty/?sale=2"), portals.REMAX)
        self._same(MmrealityClient("https://www.mmreality.cz/nemovitosti/pronajem/"), portals.MM)

    def test_bazos(self) -> None:
        self._same(BazosClient("https://reality.bazos.cz/pronajmu/byt/?hledat=2%2Bkk&kitx=ano"), bazos_fixtures.CARDS)

    def test_sreality_json_blob(self) -> None:
        blob = b'{"pageProps": {"dehydratedState": {"queries": []}}}'
        self.assertEqual(run(parse_pool.call(parse_next_data_blob, blob, "")), parse_next_data_blob(blob, ""))

    def test_parser_exceptions_propagate_unchanged(self) -> None:
        client = IdnesClient("https://reality.idnes.cz/s/pronajem/byty/praha/")
        gone = b"<html>Str\xc3\xa1nka nenalezena</html>"
        from app.sreality import Listing

        listing = Listing(id=1, name="x", price_czk=1, price_label="", disposition="", area_m2=None, locality="", url="https://reality.idnes.cz/detail/1/", image_url=None)
        with self.assertRaises(ListingGone):
            run(parse_pool.call(client._parse_detail_blob, gone, listing))
        # ...and it was not silently re-run in a thread.
        self.assertEqual(parse_pool.stats["thread"], 0)


class EventLoopResponsivenessTests(PoolCase):
    """The reason for the pool: a pathological page must not freeze the event loop."""

    @staticmethod
    async def _max_loop_lag(task_factory) -> float:
        lag = 0.0
        stop = False

        async def ticker() -> None:
            nonlocal lag
            while not stop:
                started = time.monotonic()
                await asyncio.sleep(0.02)
                lag = max(lag, time.monotonic() - started - 0.02)

        tick = asyncio.create_task(ticker())
        await asyncio.sleep(0.1)
        await task_factory()
        stop = True
        await tick
        return lag

    def test_regex_burn_does_not_stall_the_loop_in_a_process(self) -> None:
        async def go() -> float:
            # Warm the pool first so process start-up is not counted as a stall.
            await parse_pool.call(parse_pool._debug_pid)
            return await self._max_loop_lag(lambda: parse_pool.call(parse_pool._debug_regex_burn, 25))

        self.assertLess(run(go()), 0.25)

    def test_same_burn_in_a_thread_stalls_the_loop(self) -> None:
        # Documents the problem being fixed (GIL held inside one regex call).
        async def go() -> float:
            with mock.patch.object(config, "SCRAPE_PARSE_PROCS", 0):
                return await self._max_loop_lag(lambda: parse_pool.call(parse_pool._debug_regex_burn, 25))

        self.assertGreater(run(go()), 0.25)

    def test_many_pages_in_parallel(self) -> None:
        async def go() -> list[int]:
            return await asyncio.gather(*(parse_pool.call(parse_pool._debug_pid) for _ in range(12)))

        pids = run(go())
        self.assertEqual(len(pids), 12)
        self.assertNotIn(os.getpid(), pids)
        self.assertLessEqual(len(set(pids)), 2)


if __name__ == "__main__":
    unittest.main()
