from __future__ import annotations

import asyncio
import json
import time
import traceback
from typing import Any

from app import config
from app.catalog_sync import (
    daily_shards,
    listing_is_new_for_monitor,
    monitor_search_targets,
    sreality_recent_shards,
)
from app.monitor import Hub
from app.monitor_index import MonitorIndex
from app.scrape_engine import ScrapeEngine, ScrapeMetrics
from app.sreality import Listing, ListingGone
from app.store import utc_now


class ScrapeWorker:
    """Minute NewDiscovery + MonitorRefresh + rolling deep catalog (SCRAPE_ROLE=worker)."""

    def __init__(self) -> None:
        self.hub = Hub()
        self.engine = ScrapeEngine()
        self.monitor_index = MonitorIndex()
        self.running = False
        self.last_tick: dict[str, Any] = {}

    async def run(self) -> None:
        self.running = True
        self.hub.running = True
        print(
            f"scrape_worker start concurrency={config.SCRAPE_CONCURRENCY} "
            f"recent_pages={config.SCRAPE_RECENT_PAGES}",
            flush=True,
        )
        # Three independent continuous pipelines. Monitor traffic has priority 0,
        # NewDiscovery priority 1 and rolling deep priority 2 on the shared limiter.
        self.hub._task = asyncio.create_task(self.hub._loop(), name="worker-monitor-priority")
        self.hub._recent_catalog_task = asyncio.create_task(
            self.hub._recent_catalog_loop(), name="worker-new-discovery"
        )
        self.hub._deep_catalog_task = asyncio.create_task(
            self.hub._deep_catalog_loop(), name="worker-rolling-deep"
        )
        # Own sold/coords/dedupe — web process does not.
        self.hub._sold_task = asyncio.create_task(self.hub._sold_loop(), name="worker-sold")
        self.hub._coords_task = asyncio.create_task(self.hub.backfill_missing_coords(), name="worker-coords")
        self.hub._dedupe_task = asyncio.create_task(self.hub._dedupe_loop(), name="worker-dedupe")
        # Ping/discord stay on web process so notifications dequeue once.
        try:
            while self.running:
                try:
                    await self._maybe_scrape_url_request()
                    await self._maybe_forced_catalog()
                    await self.hub.maybe_run_catalog_sync(force=False)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.hub.last_error = f"scrape_worker: {exc}"
                    print(f"scrape_worker tick error: {exc}\n{traceback.format_exc()}", flush=True)
                await asyncio.sleep(10)
        finally:
            self.running = False
            await self.hub.close()

    async def minute_tick(self) -> dict[str, Any]:
        started = time.monotonic()
        discovery = await self.run_new_discovery()
        refresh = await self.run_monitor_refresh()
        snap = self.engine.metrics.snapshot()
        snap["limit"] = self.engine.limiter.limit
        self.last_tick = {
            "at": utc_now(),
            "kind": "minute",
            "role": "worker",
            "ms": int((time.monotonic() - started) * 1000),
            "discovery": discovery,
            "refresh": refresh,
            "metrics": snap,
        }
        await self.hub._job_db(self.hub.store.record_scrape_tick, self.last_tick)
        if snap.get("error_rate_5m", 0) >= config.SCRAPE_ERROR_RATE_ALERT:
            self.hub.last_error = (
                f"scrape throttle: error_rate_5m={snap['error_rate_5m']} "
                f"403={snap['http_403']} 429={snap['http_429']}"
            )
        print(
            f"scrape_worker tick discovery={discovery.get('listings')} "
            f"new={discovery.get('new')} upd={discovery.get('updated')} "
            f"refresh={refresh.get('listings')} deferred={snap.get('tick_deferred_pages')} "
            f"ms={self.last_tick.get('ms')} limit={snap.get('limit')}",
            flush=True,
        )
        return self.last_tick

    async def run_new_discovery(self) -> dict[str, Any]:
        shards = sreality_recent_shards()
        results = await self.engine.fetch_shards(
            shards,
            client_factory=self.hub.client_for,
            max_pages=config.SCRAPE_RECENT_PAGES,
            deadline_sec=float(config.SCRAPE_DISCOVERY_DEADLINE_SEC),
        )
        listings: list[Listing] = []
        seen: set[int] = set()
        pages_ok = 0
        deferred = 0
        for item in results:
            pages_ok += item.pages_ok
            deferred += len(item.deferred_pages)
            for listing in item.listings:
                if listing.id in seen:
                    continue
                seen.add(listing.id)
                listings.append(listing)
        stats = {"n": 0, "new": 0, "updated": 0, "same": 0}
        write_skipped = False
        if listings:
            written, note = await self.hub._try_catalog_upsert(
                listings,
                kind="refresh",
                timeout_sec=3.0,
                write_deadline_sec=18.0,
            )
            if written is not None:
                stats = written
                deferred += int(written.get("deferred_write") or 0)
            else:
                write_skipped = True
                deferred += len(listings)
        return {
            "shards": len(shards),
            "listings": len(listings),
            "pages_ok": pages_ok,
            "deferred": deferred,
            "new": int(stats.get("new") or 0),
            "updated": int(stats.get("updated") or 0),
            "same": int(stats.get("same") or 0),
            "write_skipped": write_skipped,
            "note": "write-deferred:catalog-busy" if write_skipped else None,
        }

    def _unique_monitor_shards(self) -> list[dict[str, str]]:
        shards: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for monitor in self.hub.store.list_monitors():
            if not monitor.get("enabled"):
                continue
            if not monitor.get("seeded"):
                self.hub.store.seed_monitor_from_catalog(monitor)
                self.hub.store.set_monitor_seeded(monitor["id"], True)
            for target in monitor_search_targets(monitor):
                url = target.get("search_url") or ""
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                shards.append(
                    {
                        "kind": "monitor_live",
                        "portal": target.get("portal") or "sreality",
                        "shard_key": f"monitor:{target.get('portal')}:{len(seen_urls)}",
                        "search_url": url,
                    }
                )
        return shards

    def _full_market_shards(self) -> list[dict[str, str]]:
        """Deep rolling coverage for full catalog — not gated on monitor URL count."""
        return self.hub.next_deep_shards()

    async def run_monitor_refresh(self) -> dict[str, Any]:
        monitors = [item for item in self.hub.store.list_monitors() if item.get("enabled")]
        self.monitor_index.rebuild(monitors)
        monitor_shards = self._unique_monitor_shards()
        # Always rotate deep shards so ~50k catalog coverage advances every minute.
        # Few monitor URLs still get polled for alerts; deep path keeps the long tail fresh.
        deep = self._full_market_shards()
        full_market = len(monitor_shards) >= config.SCRAPE_FULL_MARKET_URLS
        if full_market:
            shards = deep
            deadline = float(config.SCRAPE_FULL_MARKET_DEADLINE_SEC)
            max_pages = config.SCRAPE_DEEP_PAGES
        else:
            # Poll concrete monitor searches + a deep slice each tick.
            shards = monitor_shards + deep
            deadline = float(config.SCRAPE_MONITOR_DEADLINE_SEC) + float(config.SCRAPE_DEEP_DEADLINE_SEC)
            max_pages = max(config.POLL_PAGES, config.SCRAPE_DEEP_PAGES)
        if not shards:
            return {"listings": 0, "shards": 0, "full_market": full_market, "notified": 0}

        results = await self.engine.fetch_shards(
            shards,
            client_factory=self.hub.client_for,
            max_pages=max_pages,
            deadline_sec=min(deadline, float(config.SCRAPE_FULL_MARKET_DEADLINE_SEC) + 20),
        )
        listings: list[Listing] = []
        seen: set[int] = set()
        for item in results:
            for listing in item.listings:
                if listing.id in seen:
                    continue
                seen.add(listing.id)
                listings.append(listing)

        notified = 0
        if listings:
            notified = await self._notify_matches(listings)

        deep_total = len([item for item in daily_shards() if item.get("portal") == "sreality"]) or 1
        return {
            "listings": len(listings),
            "shards": len(shards),
            "deep_shards": len(deep),
            "deep_idx": self.hub._deep_shard_idx,
            "deep_total": deep_total,
            "coverage_pct": round(100.0 * (self.hub._deep_shard_idx % deep_total) / deep_total, 1),
            "full_market": full_market,
            "notified": notified,
            "monitors": len(monitors),
            "index_buckets": self.monitor_index.bucket_count,
        }

    async def _notify_matches(self, listings: list[Listing]) -> int:
        notified = 0
        prefs = self.hub.store.notify_prefs()
        self.monitor_index.rebuild([item for item in self.hub.store.list_monitors() if item.get("enabled")])
        for listing in listings:
            try:
                result = await self.hub._job_db(
                    self.hub.store.upsert_catalog_listing, listing, kind="refresh", fast=True
                )
                targets = self.monitor_index.matching_monitors(listing)
                if not targets:
                    continue
                for monitor in targets:
                    await self.hub._job_db(self.hub.store.add_monitor_hit, monitor["id"], result["listing_key"])
                alert = await self.hub._classify(self.hub.client_for(listing.url), listing, result.get("prev"))
                if alert is None:
                    continue
                if alert.kind == "refresh" and not config.NOTIFY_REFRESHES:
                    continue
                for monitor in targets:
                    if alert.kind == "new" and not listing_is_new_for_monitor(listing, monitor, None):
                        self.hub.store.upsert_seen(monitor["id"], listing, notified=False, kind="seeded")
                        continue
                    kind = alert.kind or "new"
                    if kind == "new" and not prefs.get("ntNew", True):
                        self.hub.store.upsert_seen(monitor["id"], alert, notified=False)
                        continue
                    if kind == "changed" and not prefs.get("ntPrice", True):
                        self.hub.store.upsert_seen(monitor["id"], alert, notified=False)
                        continue
                    webhook = self.hub.store.notify_webhook(monitor.get("search_url") or "", monitor.get("webhook_url"))
                    template = self.hub.store.get_template(monitor.get("template_id") or "default")
                    template_config = (template or {}).get("config")
                    queued = False
                    if prefs.get("discord") is not False and webhook:
                        queued = self.hub.store.enqueue_listing_ping(
                            alert,
                            webhook,
                            monitor_id=monitor["id"],
                            template_config=template_config,
                            monitor_name=monitor.get("name") or "",
                        )
                    pushed = await self.hub._notify_push(alert, kind, monitor.get("name") or "")
                    mailed = await self.hub._notify_email(alert, kind, monitor.get("name") or "")
                    self.hub.store.upsert_seen(monitor["id"], alert, notified=bool(queued or pushed or mailed))
                    if queued or pushed or mailed:
                        notified += 1
            except ListingGone:
                continue
            except Exception as exc:
                self.hub.last_error = f"notify {getattr(listing, 'id', '?')}: {exc}"
        return notified

    async def _maybe_forced_catalog(self) -> None:
        raw = await self.hub._job_db(self.hub.store.get_meta, "catalog_sync_request")
        if not raw:
            return
        await self.hub._job_db(self.hub.store.set_meta, "catalog_sync_request", None)
        portals = None
        try:
            payload = json.loads(str(raw))
            if isinstance(payload, list):
                portals = [str(item) for item in payload]
        except json.JSONDecodeError:
            portals = None
        await self.hub.run_catalog_sync(portals=portals, rerun=True)

    async def _maybe_scrape_url_request(self) -> None:
        raw = await self.hub._job_db(self.hub.store.get_meta, "scrape_url_request")
        if not raw:
            return
        await self.hub._job_db(self.hub.store.set_meta, "scrape_url_request", None)
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        url = str(payload.get("url") or "").strip()
        if not url:
            return
        try:
            pages = int(payload.get("max_pages") or 40)
        except (TypeError, ValueError):
            pages = 40
        await self.hub._run_scrape_search_url(url, pages)


async def _amain() -> None:
    worker = ScrapeWorker()
    await worker.run()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
