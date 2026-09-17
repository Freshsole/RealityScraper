from __future__ import annotations

import asyncio

from app.block_page import PortalBlocked
from app.monitor_index import MonitorIndex
from app.scrape_engine import AdaptiveLimiter, ScrapeEngine, ScrapeMetrics
from app.sreality import Listing


def test_scrape_metrics_error_rate():
    metrics = ScrapeMetrics()
    for _ in range(9):
        metrics.record("ok")
    metrics.record("429")
    assert 0.05 <= metrics.error_rate_5m() <= 0.2
    snap = metrics.snapshot()
    assert snap["fetch_ok"] == 9
    assert snap["http_429"] == 1


def test_adaptive_limiter_shrinks_on_429():
    async def _run() -> int:
        limiter = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
        await limiter.acquire()
        await limiter.release(status_code=429, ok=False)
        return limiter.limit

    assert asyncio.run(_run()) == 8


def test_adaptive_limiter_serves_monitor_before_deep_waiter():
    async def _run() -> list[str]:
        limiter = AdaptiveLimiter(initial=1, floor=1, ceiling=1)
        order: list[str] = []
        await limiter.acquire()

        async def waiter(name: str, priority: int) -> None:
            await limiter.acquire(priority)
            order.append(name)
            await limiter.release(ok=True)

        deep = asyncio.create_task(waiter("deep", 2))
        await asyncio.sleep(0)
        monitor = asyncio.create_task(waiter("monitor", 0))
        await asyncio.sleep(0)
        await limiter.release(ok=True)
        await asyncio.gather(deep, monitor)
        return order

    assert asyncio.run(_run()) == ["monitor", "deep"]


def test_fetch_pages_parallel_defers_after_deadline():
    async def _run() -> None:
        engine = ScrapeEngine(AdaptiveLimiter(initial=4, floor=2, ceiling=4))
        calls: list[int] = []

        async def fetch_page(page: int):
            calls.append(page)
            await asyncio.sleep(0.01)
            listings = [
                Listing(
                    id=page * 100 + i,
                    name=f"Byt {page}-{i} 2+kk",
                    price_czk=10000,
                    price_label="10 000 Kč/měsíc",
                    disposition="2+kk",
                    area_m2=50,
                    locality="Praha 1",
                    url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{page * 100 + i}",
                    image_url=None,
                )
                for i in range(3)
            ]
            return listings, 30

        # Deadline already passed → all pages deferred, no HTTP work.
        expired = await engine.fetch_pages_parallel(
            shard_key="test:shard-expired",
            fetch_page=fetch_page,
            max_pages=5,
            deadline_monotonic=asyncio.get_running_loop().time() - 1.0,
        )
        assert expired.pages_ok == 0
        assert expired.deferred_pages == [1, 2, 3, 4, 5]
        assert calls == []

        # Fresh deadline completes page 1 and records deferred from prior tick.
        ok = await engine.fetch_pages_parallel(
            shard_key="test:shard-expired",
            fetch_page=fetch_page,
            max_pages=1,
            deadline_monotonic=asyncio.get_running_loop().time() + 2.0,
        )
        assert ok.pages_ok >= 1
        assert 1 in calls

    asyncio.run(_run())


def test_monitor_index_buckets_not_all_monitors():
    monitors = [
        {
            "id": "m-praha-rent",
            "enabled": True,
            "portals": "sreality",
            "search_url": (
                "https://www.sreality.cz/hledani/pronajem/byty/praha"
                "?velikost=2%2Bkk&razeni=nejnovejsi"
            ),
            "created_at": "2020-01-01T00:00:00+00:00",
        },
        {
            "id": "m-brno-sale",
            "enabled": True,
            "portals": "sreality",
            "search_url": (
                "https://www.sreality.cz/hledani/prodej/byty/brno"
                "?velikost=3%2Bkk&razeni=nejnovejsi"
            ),
            "created_at": "2020-01-01T00:00:00+00:00",
        },
    ]
    index = MonitorIndex(monitors)
    listing = Listing(
        id=1,
        name="Pronájem bytu 2+kk 55 m²",
        price_czk=20000,
        price_label="20 000 Kč/měsíc",
        disposition="2+kk",
        area_m2=55,
        locality="Praha 3 - Žižkov",
        url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1",
        image_url=None,
    )
    candidates = index.candidate_monitors(listing)
    ids = {item["id"] for item in candidates}
    assert "m-praha-rent" in ids
    # Brno sale should not be in the tight candidate set for Praha rent 2+kk.
    assert "m-brno-sale" not in ids or len(candidates) < len(monitors)


def test_portal_blocked_does_not_shrink_limiter_or_fetch_more_pages():
    async def _run() -> None:
        limiter = AdaptiveLimiter(initial=16, floor=4, ceiling=16)
        engine = ScrapeEngine(limiter)
        calls: list[int] = []

        async def fetch_page(page: int):
            calls.append(page)
            raise PortalBlocked("cloudflare", 403, portal="mmreality")

        result = await engine.fetch_pages_parallel(
            shard_key="mmreality:recent:pronajem:byty",
            fetch_page=fetch_page,
            max_pages=4,
            deadline_monotonic=asyncio.get_running_loop().time() + 2.0,
            portal="mmreality",
        )
        assert limiter.limit == 16
        assert calls == [1]
        assert result.pages_ok == 0
        assert result.deferred_pages == []
        assert result.error and result.error.startswith("blocked:cloudflare")
        assert limiter.cooldown.active("mmreality")
        assert not limiter.cooldown.active("sreality")

        skipped = await engine.fetch_pages_parallel(
            shard_key="mmreality:recent:prodej:byty",
            fetch_page=fetch_page,
            max_pages=4,
            deadline_monotonic=asyncio.get_running_loop().time() + 2.0,
            portal="mmreality",
        )
        assert skipped.error and skipped.error.startswith("cooling:")
        assert calls == [1]

        healthy_calls: list[int] = []

        async def healthy(page: int):
            healthy_calls.append(page)
            return (
                [
                    Listing(
                        id=page,
                        name="Byt",
                        price_czk=10000,
                        price_label="10 000 Kč",
                        disposition="2+kk",
                        area_m2=40,
                        locality="Praha",
                        url=f"https://www.sreality.cz/detail/{page}",
                        image_url=None,
                    )
                ],
                1,
            )

        ok = await engine.fetch_pages_parallel(
            shard_key="sreality:recent",
            fetch_page=healthy,
            max_pages=1,
            deadline_monotonic=asyncio.get_running_loop().time() + 2.0,
            portal="sreality",
        )
        assert ok.pages_ok == 1
        assert healthy_calls == [1]
        assert limiter.limit == 16

    asyncio.run(_run())


def test_batch_commit_constant_from_config():
    from app import config

    assert config.SCRAPE_BATCH_COMMIT >= 50
    assert config.SCRAPE_CONCURRENCY >= config.SCRAPE_CONCURRENCY_FLOOR
    assert 0.4 <= config.SCRAPE_PAGE1_BUDGET_FRAC <= 0.95
    assert config.SCRAPE_PAGE1_ACROSS_SHARDS is True
    assert config.SCRAPE_DEEP_YIELD_TO_DISCOVERY is True


def test_should_yield_deep_while_discovery_or_higher_priority_waiters():
    from app.scrape_engine import should_yield_deep

    assert should_yield_deep(discovery_inflight=True) is True
    assert should_yield_deep(discovery_inflight=False, waiting_below=2) is True
    assert should_yield_deep(discovery_inflight=False, waiting_below=0) is False
    assert should_yield_deep(discovery_inflight=True, enabled=False) is False


def _listing(n: int) -> Listing:
    return Listing(
        id=n,
        name=f"Byt {n}",
        price_czk=10000,
        price_label="10 000 Kč",
        disposition="2+kk",
        area_m2=40,
        locality="Praha",
        url=f"https://www.sreality.cz/detail/{n}",
        image_url=None,
    )


def test_fetch_shards_skips_cooled_portal_without_http():
    async def _run() -> None:
        limiter = AdaptiveLimiter(initial=4, floor=2, ceiling=4)
        limiter.cooldown.note("mmreality", "cloudflare")
        engine = ScrapeEngine(limiter)
        calls: list[str] = []

        class Client:
            def __init__(self, url: str) -> None:
                self.url = url

            async def fetch_page(self, page: int, newest: bool = True):
                calls.append(self.url)
                return [_listing(page)], 1

        shards = [
            {
                "portal": "mmreality",
                "shard_key": "mmreality:recent:pronajem:byty",
                "search_url": "https://www.mmreality.cz/nemovitosti/?typ-nabidky=pronajem",
            },
            {
                "portal": "sreality",
                "shard_key": "sreality:recent",
                "search_url": "https://www.sreality.cz/hledani/pronajem/byty",
            },
        ]
        results = await engine.fetch_shards(
            shards,
            client_factory=lambda url: Client(url),
            max_pages=2,
            deadline_sec=1.0,
            page1_across=False,
            min_shard_sec=0,
        )
        assert calls == ["https://www.sreality.cz/hledani/pronajem/byty"]
        by_key = {item.shard_key: item for item in results}
        assert by_key["mmreality:recent:pronajem:byty"].error.startswith("cooling:")
        assert by_key["sreality:recent"].pages_ok >= 1
        assert limiter.limit == 4

    asyncio.run(_run())


def test_page1_across_shards_beats_held_gate_under_tight_deadline():
    """Holding the shard gate through pages 2..N starves later healthy shards' page 1."""

    async def _once(*, page1_across: bool) -> tuple[int, int]:
        limiter = AdaptiveLimiter(initial=4, floor=4, ceiling=4)
        engine = ScrapeEngine(limiter)
        calls: list[tuple[int, int]] = []

        class Client:
            def __init__(self, shard_id: int) -> None:
                self.shard_id = shard_id

            async def fetch_page(self, page: int, newest: bool = True):
                calls.append((self.shard_id, page))
                await asyncio.sleep(0.03)
                return [_listing(self.shard_id * 100 + page)], 80

        shards = [
            {
                "portal": "idnes" if i < 6 else "sreality",
                "shard_key": f"p{i}",
                "search_url": f"https://example.test/{i}",
            }
            for i in range(12)
        ]
        clients = {f"https://example.test/{i}": Client(i) for i in range(12)}
        results = await engine.fetch_shards(
            shards,
            client_factory=lambda url: clients[url],
            max_pages=8,
            deadline_sec=0.12,
            page1_across=page1_across,
            min_shard_sec=0,
        )
        page1_ok = sum(1 for _shard, page in calls if page == 1)
        listings = sum(len(item.listings) for item in results)
        return page1_ok, listings

    async def _run() -> None:
        held_page1, held_listings = await _once(page1_across=False)
        across_page1, across_listings = await _once(page1_across=True)
        assert across_page1 > held_page1
        assert across_listings >= held_listings
        assert across_page1 >= 10
        assert held_page1 <= 8

    asyncio.run(_run())


def test_persisted_engine_retries_deferred_pages_next_tick():
    async def _run() -> None:
        limiter = AdaptiveLimiter(initial=4, floor=2, ceiling=4)
        engine = ScrapeEngine(limiter)
        calls: list[int] = []

        async def fetch_page(page: int):
            calls.append(page)
            await asyncio.sleep(0.08)
            return [_listing(page)], 60

        first = await engine.fetch_pages_parallel(
            shard_key="idnes:recent:pronajem:byty",
            fetch_page=fetch_page,
            max_pages=3,
            deadline_monotonic=asyncio.get_running_loop().time() + 0.05,
            portal="idnes",
        )
        assert first.pages_ok == 1
        assert 2 in first.deferred_pages
        assert engine.deferred["idnes:recent:pronajem:byty"]

        second = await engine.fetch_pages_parallel(
            shard_key="idnes:recent:pronajem:byty",
            fetch_page=fetch_page,
            max_pages=3,
            deadline_monotonic=asyncio.get_running_loop().time() + 1.0,
            portal="idnes",
        )
        assert second.pages_ok >= 2
        assert 2 in calls and 3 in calls

    asyncio.run(_run())


def test_pending_discovery_listings_carry_across_busy_write():
    from app.monitor import Hub

    class Fake:
        pass

    fake = Fake()
    fake._pending_discovery = [_listing(1), _listing(2)]
    merged = Hub._merge_pending_discovery(fake, [_listing(2), _listing(3)])
    assert [item.id for item in merged] == [2, 3, 1]
    assert fake._pending_discovery == []


def test_next_deep_shards_skips_cooled_portal():
    from app.catalog_sync import daily_shards
    from app.monitor import Hub

    deep = daily_shards()
    idx = next(i for i, item in enumerate(deep) if item["portal"] == "mmreality")
    hub = object.__new__(Hub)
    hub._deep_shard_idx = idx
    hub._scrape_limiter = AdaptiveLimiter(initial=4, floor=2, ceiling=4)
    hub._scrape_limiter.cooldown.note("mmreality", "cloudflare")
    picked = Hub.next_deep_shards(hub, take=4)
    assert picked
    assert all(item["portal"] != "mmreality" for item in picked)


def test_deep_tick_does_not_pick_shards_while_discovery_inflight():
    from app.monitor import Hub

    hub = object.__new__(Hub)
    hub._discovery_inflight = True
    hub._scrape_limiter = AdaptiveLimiter(initial=4, floor=2, ceiling=4)
    picked: list[int] = []

    def boom(take=None):
        picked.append(1)
        raise AssertionError("deep must yield to NewDiscovery")

    hub.next_deep_shards = boom  # type: ignore[method-assign]
    asyncio.run(hub._deep_catalog_tick())
    assert picked == []


def test_throughput_helpers_stay_off_instant_site():
    from pathlib import Path

    src = Path("app/site_pages.py").read_text()
    assert "prepare_discovery_shards" not in src
    assert "ulov_hydrate" not in src
    assert "offer/detail" not in src
