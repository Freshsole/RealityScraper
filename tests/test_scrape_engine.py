from __future__ import annotations

import asyncio

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


def test_batch_commit_constant_from_config():
    from app import config

    assert config.SCRAPE_BATCH_COMMIT >= 50
    assert config.SCRAPE_CONCURRENCY >= config.SCRAPE_CONCURRENCY_FLOOR
