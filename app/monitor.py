from __future__ import annotations

import asyncio
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from app import config
from app import push as web_push
from app import email_notify as mail_notify
from app.discord_notify import send_digest, send_listing, send_sold, send_text
from app.sreality import Listing, ListingGone, format_price, is_recently_created, listing_from_dict
from app.sources import PORTAL_LABELS, client_for, source_name
from app.catalog_sync import (
    daily_shards,
    listing_is_new_for_monitor,
    prepare_discovery_shards,
    recent_shards,
)
from app.store import Store, _listing_from_catalog_dict, local_day_start, utc_now
from app.version import current_version
from app.billing import billing_state, settle_pending_if_due


class Hub:
    def __init__(self) -> None:
        self.store = Store(config.DB_PATH)
        self.clients: dict[str, object] = {}
        self.running = False
        self.checking = False
        self._task: asyncio.Task[None] | None = None
        self._sold_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._catalog_task: asyncio.Task[None] | None = None
        self._recent_catalog_task: asyncio.Task[None] | None = None
        self._deep_catalog_task: asyncio.Task[None] | None = None
        self._discord_task: asyncio.Task[None] | None = None
        self._coords_task: asyncio.Task[None] | None = None
        self._dedupe_task: asyncio.Task[None] | None = None
        self._ulov_hydrate_task: asyncio.Task[None] | None = None
        self.catalog_running = False
        self.catalog_running_portals: set[str] = set()
        self._recent_shard_idx = 0
        self._deep_shard_idx = 0
        self.dedupe_running = False
        self.dedupe_scanning = False
        self.last_error: str | None = None
        self._recover_stuck_catalog_meta()
        self._portal_gate = asyncio.Semaphore(2)
        self._bazos_gate = asyncio.Semaphore(24)
        self._catalog_write = asyncio.Lock()
        from app.scrape_engine import AdaptiveLimiter, ScrapeEngine

        self._scrape_limiter = AdaptiveLimiter()
        self._monitor_engine = ScrapeEngine(self._scrape_limiter, priority=0)
        self._discovery_engine = ScrapeEngine(self._scrape_limiter, priority=1)
        self._deep_engine = ScrapeEngine(self._scrape_limiter, priority=2)
        self._discovery_inflight = False
        self._pending_discovery: list[Listing] = []
        self._status_cache: dict[str, Any] | None = None
        self._status_cache_at = 0.0
        self.catalog_gen = 0
        self.ui_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rf-ui")
        self.auth_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rf-auth")
        self.job_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rf-job")

    def client_for(self, search_url: str):
        client = self.clients.get(search_url)
        if client is None:
            client = client_for(search_url)
            self.clients[search_url] = client
        return client

    def _recover_stuck_catalog_meta(self) -> None:
        """Clear leftover 'running' meta from a crashed process so sync/live can resume."""
        try:
            status = str(self.store.get_meta("catalog_sync_status") or "")
            if status == "running":
                self.store.set_meta("catalog_sync_status", "partial")
                self.store.set_meta(
                    "catalog_sync_error",
                    "Obnoveno po restartu — předchozí sync zůstal viset ve stavu running.",
                )
        except Exception:
            pass
        try:
            settings = self.store.dedupe_settings()
            status = str(settings.get("status") or "")
            error = str(settings.get("error") or "")
            interrupted = error.startswith("Přerušeno restartem procesu")
            if status in {"running", "scanning"} or interrupted:
                self.store.patch_dedupe_meta(
                    {
                        # A reload interruption is operational cleanup, not a
                        # persistent dedupe failure shown as a red admin error.
                        "status": "idle",
                        "error": "",
                        "last_attempt": utc_now(),
                    }
                )
        except Exception:
            pass
        try:
            # Stuck catalog_daily jobs (e.g. Bazos „Běží“ for days) block admin status.
            now = utc_now()
            for job in self.store.list_scrape_jobs():
                if str(job.get("status") or "") != "running":
                    continue
                portal = str(job.get("portal") or "")
                self.store.update_scrape_job(
                    job["id"],
                    status="partial",
                    last_error="Obnoveno po restartu — job zůstal ve stavu running.",
                    finished_at=now,
                )
                if portal:
                    # Mark portal attempted today so minute ticks are not starved by catch-up.
                    self.store.set_meta(f"catalog_sync_{portal}_status", "partial")
                    if not str(self.store.get_meta(f"catalog_sync_{portal}_last") or "").startswith(now[:10]):
                        self.store.set_meta(f"catalog_sync_{portal}_last", now)
        except Exception:
            pass

    async def _job_db(self, fn, *args, **kwargs):
        def _call():
            last_error: Exception | None = None
            for attempt in range(4):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    if "database is locked" not in str(exc).lower() or attempt == 3:
                        raise
                    time.sleep(0.15 * (attempt + 1))
            if last_error:
                raise last_error

        return await asyncio.get_running_loop().run_in_executor(self.job_pool, _call)

    async def _record_tick(self, tick: dict[str, Any]) -> None:
        """Serialize tick metadata off the event loop and retry transient writers."""
        def _call() -> None:
            for attempt in range(5):
                try:
                    self.store.record_scrape_tick(tick)
                    return
                except Exception as exc:
                    if "database is locked" not in str(exc).lower() or attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))

        # Tick metadata is a writer — keep it off ui_pool so catalog JSON is not queued
        # behind scrape commits. job_pool already serializes with other catalog writes.
        async with self._catalog_write:
            await asyncio.get_running_loop().run_in_executor(self.job_pool, _call)

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.last_error = None
        role = config.SCRAPE_ROLE
        # Heavy scrape lives in scrape_worker when role=web; role=all keeps legacy single-process.
        if role == "worker":
            return
        if role == "all":
            self._task = asyncio.create_task(self._loop(), name="sreality-hub")
            self._sold_task = asyncio.create_task(self._sold_loop(), name="sreality-sold")
            self._catalog_task = asyncio.create_task(self._catalog_loop(), name="sreality-catalog")
            self._recent_catalog_task = asyncio.create_task(
                self._recent_catalog_loop(), name="sreality-catalog-recent"
            )
            self._deep_catalog_task = asyncio.create_task(
                self._deep_catalog_loop(), name="sreality-catalog-deep"
            )
            self._coords_task = asyncio.create_task(self.backfill_missing_coords(), name="sreality-coords")
            self._dedupe_task = asyncio.create_task(self._dedupe_loop(), name="sreality-dedupe")
            self._ulov_hydrate_task = asyncio.create_task(self._ulov_hydrate_loop(), name="sreality-ulov-hydrate")
        elif role == "web":
            # Digests only — listing polls / catalog / sold run in scrape_worker.
            self._task = asyncio.create_task(self._web_loop(), name="sreality-hub-web")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="sreality-pings")
        if config.DISCORD_BOT_TOKEN and config.DISCORD_GUILD_ID:
            from app.discord_bot import run_discord_bot

            self._discord_task = asyncio.create_task(run_discord_bot(self.store), name="discord-bot")

    async def _web_loop(self) -> None:
        await asyncio.sleep(20)
        while self.running:
            try:
                await self.maybe_send_digest()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{exc}"
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                raise

    async def stop(self) -> None:
        self.running = False
        tasks = [
            task
            for task in (
                self._task,
                self._sold_task,
                self._ping_task,
                self._catalog_task,
                self._recent_catalog_task,
                self._deep_catalog_task,
                self._discord_task,
                self._coords_task,
                self._dedupe_task,
                self._ulov_hydrate_task,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=1.5)
        self._task = None
        self._sold_task = None
        self._ping_task = None
        self._catalog_task = None
        self._recent_catalog_task = None
        self._deep_catalog_task = None
        self._discord_task = None
        self._coords_task = None
        self._dedupe_task = None
        self._ulov_hydrate_task = None

    async def close(self) -> None:
        await self.stop()
        for client in list(self.clients.values()):
            closer = getattr(client, "aclose", None)
            if closer is None:
                continue
            try:
                await asyncio.wait_for(closer(), timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                pass
        self.clients.clear()

    async def _loop(self) -> None:
        await asyncio.sleep(1)
        while self.running:
            started = time.monotonic()
            try:
                monitor_run = await self.check_due()
                results = monitor_run.get("results") if isinstance(monitor_run, dict) else []
                if results:
                    good = [item for item in results if isinstance(item, dict)]
                    tick = {
                        "at": utc_now(),
                        "kind": "monitor_priority",
                        "role": config.SCRAPE_ROLE,
                        "ms": int((time.monotonic() - started) * 1000),
                        "discovery": {},
                        "refresh": {
                            "shards": len(good),
                            "listings": sum(int(item.get("scanned") or 0) for item in good),
                            "notified": sum(len(item.get("new") or []) for item in good),
                            "errors": sum(len(item.get("errors") or []) for item in good),
                        },
                    }
                    await self._record_tick(tick)
                if config.SCRAPE_ROLE != "worker":
                    await self.maybe_send_digest()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"monitor-priority: {exc}"
            try:
                await asyncio.sleep(
                    max(0.25, float(config.SCRAPE_MONITOR_LOOP_SEC) - (time.monotonic() - started))
                )
            except asyncio.CancelledError:
                raise

    def _monitor_due(self, monitor: dict[str, Any]) -> bool:
        interval = monitor.get("interval_sec") or config.POLL_INTERVAL_SEC
        try:
            interval = max(20, int(interval))
        except (TypeError, ValueError):
            interval = config.POLL_INTERVAL_SEC
        last = monitor.get("last_check")
        if not last:
            return True
        try:
            seen = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - seen).total_seconds() >= interval

    async def check_due(self) -> dict[str, Any]:
        if self.checking:
            return {"ok": False, "reason": "already-checking"}
        self.checking = True
        try:
            groups: dict[str, dict[str, Any]] = {}
            monitors = await self._job_db(self.store.list_monitors_light)
            for monitor in monitors:
                if not monitor.get("enabled"):
                    continue
                if not monitor.get("seeded"):
                    await self._job_db(self.store.seed_monitor_from_catalog, monitor)
                    await self._job_db(self.store.set_monitor_seeded, monitor["id"], True)
                jobs = await self._job_db(self.store.attach_monitor_live_jobs, monitor)
                for job in jobs:
                    bucket = groups.setdefault(job["id"], {"job": job, "monitors": []})
                    bucket["monitors"].append(monitor)
            pending = []
            for bucket in groups.values():
                if not any(self._monitor_due(item) for item in bucket["monitors"]):
                    continue
                async def bounded_check(current=bucket):
                    try:
                        return await asyncio.wait_for(
                            self.check_live_job(current["job"], current["monitors"]),
                            timeout=float(config.SCRAPE_MONITOR_DEADLINE_SEC) + 30.0,
                        )
                    except asyncio.TimeoutError:
                        message = "Monitor search timeout; další cyklus ho zkusí znovu."
                        for item in current["monitors"]:
                            await self._job_db(
                                self.store.update_monitor_stats,
                                item["id"],
                                last_check=utc_now(),
                                last_error=message,
                            )
                        return {
                            "job_id": current["job"]["id"],
                            "scanned": 0,
                            "new": [],
                            "errors": [message],
                        }

                pending.append(bounded_check())
            raw_results = await asyncio.gather(*pending, return_exceptions=True)
            results = []
            for result in raw_results:
                if isinstance(result, BaseException):
                    self.last_error = f"monitor-priority: {result}"
                    results.append({"ok": False, "error": str(result)})
                else:
                    results.append(result)
            return {"ok": True, "results": results}
        finally:
            self.checking = False

    async def check_once(self, monitor_id: str | None = None) -> dict[str, Any]:
        if self.checking:
            return {"ok": False, "reason": "already-checking"}
        self.checking = True
        try:
            monitors = self.store.list_monitors()
            if monitor_id:
                monitors = [item for item in monitors if item["id"] == monitor_id]
            groups: dict[str, dict[str, Any]] = {}
            for monitor in monitors:
                if not monitor.get("enabled"):
                    continue
                if not monitor.get("seeded"):
                    self.store.seed_monitor_from_catalog(monitor)
                    self.store.set_monitor_seeded(monitor["id"], True)
                for job in self.store.attach_monitor_live_jobs(monitor):
                    bucket = groups.setdefault(job["id"], {"job": job, "monitors": []})
                    bucket["monitors"].append(monitor)
            results = []
            for bucket in groups.values():
                results.append(await self.check_live_job(bucket["job"], bucket["monitors"]))
            return {"ok": True, "results": results}
        finally:
            self.checking = False

    async def check_monitor(self, monitor: dict[str, Any]) -> dict[str, Any]:
        if not monitor.get("seeded"):
            self.store.seed_monitor_from_catalog(monitor)
            self.store.set_monitor_seeded(monitor["id"], True)
        jobs = self.store.attach_monitor_live_jobs(monitor)
        results = [await self.check_live_job(job, [monitor]) for job in jobs]
        return results[0] if results else {"ok": True, "scanned": 0}

    async def check_live_job(self, job: dict[str, Any], monitors: list[dict[str, Any]]) -> dict[str, Any]:
        client = self.client_for(job["search_url"])
        notified: list[Listing] = []
        errors: list[str] = []
        try:
            fetched = await self._monitor_engine.fetch_shards(
                [
                    {
                        "shard_key": f"priority:{job['id']}",
                        "search_url": job["search_url"],
                    }
                ],
                client_factory=self.client_for,
                max_pages=config.POLL_PAGES,
                deadline_sec=float(config.SCRAPE_MONITOR_DEADLINE_SEC),
            )
            result = fetched[0]
            listings, total = result.listings, result.total
            notify_limit = len(listings)
            if result.error:
                errors.append(result.error)
            # All catalog writers share one lock. Monitor batches queue first at the
            # network limiter and then serialize here instead of fighting SQLite.
            async with self._catalog_write:
                upserts = await self._job_db(
                    self.store.upsert_catalog_listings_results,
                    listings,
                    kind="refresh",
                    fast=True,
                )
            for index, (listing, upsert) in enumerate(zip(listings, upserts)):
                try:
                    # The common unchanged case only needs last_seen from the batch write.
                    # Avoid monitor matching, hit writes and detail calls for every item.
                    prev = upsert.get("prev")
                    if prev is not None and not snapshot_price_change(prev, listing):
                        continue
                    targets = await self._job_db(self.store.matching_monitors, listing, job["id"])
                    if not targets:
                        targets = monitors
                    for monitor in targets:
                        await self._job_db(self.store.add_monitor_hit, monitor["id"], upsert["listing_key"])
                    if index >= notify_limit:
                        continue
                    alert = await self._classify(client, listing, prev)
                    for monitor in targets:
                        if alert is None:
                            continue
                        if alert.kind == "refresh" and not config.NOTIFY_REFRESHES:
                            continue
                        if alert.kind == "new" and not listing_is_new_for_monitor(listing, monitor, None):
                            self.store.upsert_seen(monitor["id"], listing, notified=False, kind="seeded")
                            continue
                        prefs = self.store.notify_prefs()
                        kind = alert.kind or "new"
                        if kind == "new" and not prefs.get("ntNew", True):
                            self.store.upsert_seen(monitor["id"], alert, notified=False)
                            continue
                        if kind == "changed" and not prefs.get("ntPrice", True):
                            self.store.upsert_seen(monitor["id"], alert, notified=False)
                            continue
                        webhook = self.store.notify_webhook(monitor.get("search_url") or "", monitor.get("webhook_url"))
                        template = self.store.get_template(monitor.get("template_id") or "default")
                        template_config = (template or {}).get("config")
                        queued = False
                        if prefs.get("discord") is not False:
                            if not webhook:
                                errors.append(f"{listing.id}: chybí Discord webhook")
                            else:
                                queued = self.store.enqueue_listing_ping(
                                    alert,
                                    webhook,
                                    monitor_id=monitor["id"],
                                    template_config=template_config,
                                    monitor_name=monitor.get("name") or "",
                                )
                        pushed = await self._notify_push(alert, kind, monitor.get("name") or "")
                        mailed = await self._notify_email(alert, kind, monitor.get("name") or "")
                        self.store.upsert_seen(monitor["id"], alert, notified=bool(queued or pushed or mailed))
                        if queued or pushed or mailed:
                            notified.append(alert)
                            try:
                                from app import analytics as site_stats

                                site_stats.track(self.store, site_stats.KIND_NOTIFY, path="/zprava")
                            except Exception:
                                pass
                        elif prefs.get("discord") is not False and not webhook:
                            try:
                                from app import analytics as site_stats

                                site_stats.track(self.store, site_stats.KIND_NOTIFY_FAIL, path="/zprava")
                            except Exception:
                                pass
                except ListingGone:
                    for monitor in monitors:
                        await self.notify_sold(monitor, listing.id)
                except Exception as exc:
                    errors.append(f"{listing.id}: {exc}")
            # Sold/removal probing has its own continuous _sold_loop. Doing it once
            # per search URL multiplied detail requests and starved monitor refresh.
            for monitor in monitors:
                current = await self._job_db(self.store.get_monitor, monitor["id"]) or monitor
                try:
                    inventory = json.loads(current.get("last_inventory") or "{}")
                except json.JSONDecodeError:
                    inventory = {}
                if not isinstance(inventory, dict):
                    inventory = {}
                portal = str(job.get("portal") or "sreality")
                inventory[portal] = {"total": int(total or 0), "found": len(listings)}
                last_total = sum(int((row or {}).get("total") or 0) for row in inventory.values() if isinstance(row, dict))
                last_found = sum(int((row or {}).get("found") or 0) for row in inventory.values() if isinstance(row, dict))
                await self._job_db(
                    self.store.update_monitor_stats,
                    monitor["id"],
                    last_check=utc_now(),
                    last_error="; ".join(errors) if errors else None,
                    last_total=last_total,
                    last_found=last_found,
                    last_inventory=json.dumps(inventory, ensure_ascii=False),
                )
            return {
                "job_id": job["id"],
                "scanned": len(listings),
                "total": total,
                "new": [item.to_dict() for item in notified],
                "errors": errors,
            }
        except Exception as exc:
            for monitor in monitors:
                await self._job_db(
                    self.store.update_monitor_stats,
                    monitor["id"],
                    last_check=utc_now(),
                    last_error=f"{exc}\n{traceback.format_exc(limit=2)}",
                )
            raise

    async def _classify(
        self,
        client,
        listing: Listing,
        prev: dict[str, Any] | None,
    ) -> Listing | None:
        if prev is None:
            listing = await client.fetch_detail(listing)
            if is_recently_created(listing.created_on, config.NEW_MAX_AGE_DAYS):
                listing.kind = "new"
                listing.changes = detail_price_change(listing)
                return listing
            listing.changes = detail_price_change(listing)
            if listing.changes:
                listing.kind = "changed"
                return listing
            listing.kind = "refresh"
            return listing if config.NOTIFY_REFRESHES else None

        price_changes = snapshot_price_change(prev, listing)
        if price_changes:
            listing = await client.fetch_detail(listing)
            listing.kind = "changed"
            listing.changes = merge_price_changes(price_changes, detail_price_change(listing))
            return listing
        listing.kind = "seen"
        return None

    async def _notify_push(self, listing: Any, kind: str, monitor_name: str = "", *, ignore_quiet: bool = False) -> int:
        try:
            return await asyncio.to_thread(
                web_push.notify_listing,
                self.store,
                listing,
                kind,
                monitor_name,
                ignore_quiet=ignore_quiet,
            )
        except Exception:
            return 0

    async def _notify_email(self, listing: Any, kind: str, monitor_name: str = "", *, ignore_quiet: bool = False, prefix: str = "") -> bool:
        try:
            return await mail_notify.notify_listing(
                self.store,
                listing,
                kind,
                monitor_name,
                ignore_quiet=ignore_quiet,
                prefix=prefix,
            )
        except Exception:
            return False

    async def notify_sold(self, monitor: dict[str, Any] | None, listing_id: int, monitor_id: str | None = None) -> None:
        mid = (monitor or {}).get("id") or monitor_id
        if not mid:
            return
        row = self.store.mark_gone(mid, listing_id)
        if not row:
            return
        await self._send_sold_ping(row, (monitor or {}).get("name") or row.get("monitor_name") or "")

    async def _send_sold_ping(self, row: dict[str, Any], monitor_name: str = "") -> None:
        if row.get("sold_notified"):
            return
        if not self._should_ping_sold(row):
            self.store.mark_sold_notified(row["monitor_id"], int(row["id"]))
            return
        webhook = self.store.notify_webhook(sold=True)
        prefs = self.store.notify_prefs()
        pushed = 0
        if prefs.get("ntExpire", True):
            pushed = await self._notify_push(row, "sold", monitor_name)
        queued = False
        if webhook and prefs.get("discord") is not False:
            queued = bool(self.store.enqueue_sold_ping(row, webhook, monitor_name=monitor_name))
        wa = False
        mailed = await self._notify_email(row, "sold", monitor_name)
        if queued or pushed or mailed:
            self.store.mark_sold_notified(row["monitor_id"], int(row["id"]))

    def _should_ping_sold(self, row: dict[str, Any]) -> bool:
        if row.get("notified"):
            return True
        first = row.get("first_seen")
        try:
            seen = datetime.fromisoformat(str(first).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return (datetime.now(timezone.utc) - seen).days <= 14

    def _listing_from_row(self, row: dict[str, Any]) -> Listing:
        return Listing(
            id=int(row["id"]),
            name=row.get("name") or "",
            price_czk=row.get("price_czk"),
            price_label=row.get("price_label") or "",
            disposition=row.get("disposition") or "",
            area_m2=row.get("area_m2"),
            locality=row.get("locality") or "",
            url=row.get("url") or "",
            image_url=row.get("image_url"),
        )

    def _inventory_due(self, monitor: dict[str, Any]) -> bool:
        last = monitor.get("last_inventory")
        if not last:
            return True
        try:
            seen = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - seen).total_seconds() >= config.SOLD_INVENTORY_SEC

    async def _ping_loop(self) -> None:
        while self.running:
            try:
                item = await asyncio.to_thread(self.store.claim_next_ping)
                if not item:
                    await asyncio.sleep(0.25)
                    continue
                try:
                    await self._dispatch_ping(item)
                    await asyncio.to_thread(self.store.mark_ping_sent, item)
                except asyncio.CancelledError:
                    await asyncio.to_thread(
                        self.store.mark_ping_failed, int(item["id"]), "cancelled", int(item.get("attempts") or 1)
                    )
                    raise
                except Exception as exc:
                    self.last_error = f"Discord queue: {exc}"
                    await asyncio.to_thread(
                        self.store.mark_ping_failed, int(item["id"]), str(exc), int(item.get("attempts") or 1)
                    )
                await asyncio.sleep(0.45)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{exc}"
                await asyncio.sleep(1)

    async def _dispatch_ping(self, item: dict[str, Any]) -> None:
        payload = json.loads(item.get("payload") or "{}")
        webhook = item.get("webhook_url") or ""
        if item.get("ping_type") == "sold":
            await send_sold(webhook, payload.get("row") or {}, monitor_name=payload.get("monitor_name") or "")
            return
        listing = listing_from_dict(payload.get("listing") or {})
        await send_listing(
            webhook,
            listing,
            template_config=payload.get("template_config"),
            monitor_name=payload.get("monitor_name") or "",
        )

    async def _recent_catalog_loop(self) -> None:
        """Continuously discover new listings, independent of monitors and deep crawl."""
        await asyncio.sleep(2)
        while self.running:
            tick_started = time.monotonic()
            try:
                await self.run_due_scrape_schedules()
                await self._recent_catalog_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"recent-catalog: {exc}"
                try:
                    await self._record_tick(
                        {
                            "at": utc_now(),
                            "kind": "minute_discovery",
                            "role": config.SCRAPE_ROLE,
                            "error": str(exc)[:240],
                            "discovery": {"shards": 0, "listings": 0, "new": 0, "updated": 0},
                            "refresh": {},
                        }
                    )
                except Exception:
                    pass
            elapsed = time.monotonic() - tick_started
            sleep_for = max(0.5, float(config.SCRAPE_DISCOVERY_LOOP_SEC) - elapsed)
            try:
                await asyncio.sleep(sleep_for)
            except asyncio.CancelledError:
                raise

    async def _deep_catalog_loop(self) -> None:
        """Continuously consume low-priority full-market shards."""
        from app.scrape_engine import should_yield_deep

        await asyncio.sleep(4)
        while self.running:
            try:
                if should_yield_deep(
                    discovery_inflight=self._discovery_inflight,
                    waiting_below=self._scrape_limiter.waiting_below(2),
                    enabled=bool(config.SCRAPE_DEEP_YIELD_TO_DISCOVERY),
                ):
                    await asyncio.sleep(0.25)
                    continue
                await self._deep_catalog_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"rolling-deep: {exc}"
            try:
                await asyncio.sleep(float(config.SCRAPE_DEEP_LOOP_SEC))
            except asyncio.CancelledError:
                raise

    def start_scrape_search_url(self, url: str, *, max_pages: int = 40) -> dict[str, Any]:
        """Queue manual URL scrape in background so admin UI does not hang on DB locks."""
        from app.catalog_sync import normalize_search_url

        cleaned = normalize_search_url(url)
        if not cleaned:
            return {"ok": False, "error": "Chybí URL hledání"}
        pages = max(1, min(200, int(max_pages or 40)))
        try:
            self.store.record_scrape_tick(
                {
                    "at": utc_now(),
                    "kind": "manual_queued",
                    "url": cleaned,
                    "ms": 0,
                    "discovery": {"shards": 1, "listings": 0, "new": 0, "updated": 0, "pages": pages},
                    "refresh": {},
                }
            )
        except Exception as exc:
            return {"ok": False, "error": f"Nelze zapsat frontu (DB locked?): {exc}"}
        if config.SCRAPE_ROLE == "web":
            self.store.set_meta(
                "scrape_url_request",
                json.dumps({"url": cleaned, "max_pages": pages}, ensure_ascii=False),
            )
            return {"ok": True, "queued": True, "url": cleaned, "max_pages": pages}
        asyncio.create_task(self._run_scrape_search_url(cleaned, pages), name="admin-scrape-url")
        return {"ok": True, "queued": True, "url": cleaned, "max_pages": pages}

    def schedule_manual_scrape(self, job: dict[str, Any]) -> dict[str, Any]:
        """Persist a one-shot scrape (URL or whole portal) for later."""
        from app.catalog_sync import normalize_search_url

        scope = str(job.get("scope") or "url").strip().lower()
        run_at_raw = str(job.get("run_at") or "").strip()
        if not run_at_raw:
            return {"ok": False, "error": "Chybí čas naplánování"}
        try:
            parsed = datetime.fromisoformat(run_at_raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
            run_at = parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return {"ok": False, "error": "Neplatný čas naplánování"}
        payload: dict[str, Any] = {
            "scope": scope,
            "run_at": run_at,
            "created_at": utc_now(),
        }
        if scope == "portal":
            portal = str(job.get("portal") or "").strip().lower()
            if portal not in config.CATALOG_SYNC_HOURS:
                return {"ok": False, "error": "Neznámý portál"}
            payload["portal"] = portal
            payload["label"] = PORTAL_LABELS.get(portal, portal)
        else:
            cleaned = normalize_search_url(str(job.get("url") or ""))
            if not cleaned:
                return {"ok": False, "error": "Chybí URL hledání"}
            pages = max(1, min(200, int(job.get("max_pages") or 40)))
            payload["url"] = cleaned
            payload["max_pages"] = pages
            payload["label"] = cleaned[:72]
        saved = self.store.add_scrape_schedule(payload)
        return {"ok": True, "scheduled": True, "job": saved}

    async def run_due_scrape_schedules(self) -> int:
        due = await self._job_db(self.store.claim_due_scrape_schedules)
        for job in due:
            scope = str(job.get("scope") or "url")
            if scope == "portal":
                portal = str(job.get("portal") or "").strip()
                if portal:
                    self.start_catalog_sync(portals=[portal])
            else:
                url = str(job.get("url") or "")
                pages = int(job.get("max_pages") or 40)
                if url:
                    self.start_scrape_search_url(url, max_pages=pages)
        return len(due)

    async def _run_scrape_search_url(self, cleaned: str, pages: int) -> None:
        try:
            await self.scrape_search_url(cleaned, max_pages=pages)
        except Exception as exc:
            self.last_error = f"manual-scrape: {exc}"
            try:
                await self._job_db(
                    self.store.record_scrape_tick,
                    {
                        "at": utc_now(),
                        "kind": "manual_url",
                        "url": cleaned,
                        "error": str(exc)[:240],
                        "discovery": {"listings": 0, "new": 0, "updated": 0},
                        "refresh": {},
                    },
                )
            except Exception:
                pass

    async def _catalog_upsert(
        self,
        listings: list[Listing],
        *,
        kind: str = "seeded",
        write_deadline_sec: float | None = None,
    ) -> dict[str, int]:
        """Upsert in lock-scoped chunks so minute discovery can interleave."""
        if not listings:
            return {"n": 0, "new": 0, "updated": 0, "same": 0, "deferred_write": 0}
        # Keep individual SQLite writer holds short; large 500-row transactions
        # blocked admin/auth/tick writes for tens of seconds on a 1GB database.
        chunk_size = max(50, min(100, int(config.SCRAPE_BATCH_COMMIT)))
        totals = {"n": 0, "new": 0, "updated": 0, "same": 0, "deferred_write": 0}
        deadline = (
            time.monotonic() + max(5.0, write_deadline_sec)
            if write_deadline_sec is not None
            else None
        )
        for offset in range(0, len(listings), chunk_size):
            if deadline is not None and time.monotonic() >= deadline:
                totals["deferred_write"] = len(listings) - offset
                break
            chunk = listings[offset : offset + chunk_size]
            async with self._catalog_write:
                part = await self._job_db(
                    self.store.upsert_catalog_listings_batch,
                    chunk,
                    kind=kind,
                    commit_every=chunk_size,
                    fast=True,
                )
            for key in ("n", "new", "updated", "same"):
                totals[key] += int(part.get(key) or 0)
            # Let pending minute ticks acquire the write lock.
            await asyncio.sleep(0)
        return totals

    async def _try_catalog_upsert(
        self,
        listings: list[Listing],
        *,
        kind: str = "refresh",
        timeout_sec: float = 4.0,
        write_deadline_sec: float | None = 20.0,
    ) -> tuple[dict[str, int] | None, str | None]:
        """Prefer minute cadence: skip write if catalog sync holds the lock too long."""
        if not listings:
            return {"n": 0, "new": 0, "updated": 0, "same": 0, "deferred_write": 0}, None
        try:
            await asyncio.wait_for(self._catalog_write.acquire(), timeout=max(0.5, timeout_sec))
        except asyncio.TimeoutError:
            return None, "write-deferred:catalog-busy"
        # Probe only — chunked upsert re-acquires so catalog sync can interleave.
        self._catalog_write.release()
        return await self._catalog_upsert(
            listings, kind=kind, write_deadline_sec=write_deadline_sec
        ), None

    def next_deep_shards(self, take: int | None = None) -> list[dict[str, str]]:
        """Rotate through full-market shards (all portals) without synchronous DB writes."""
        deep = daily_shards()
        if not deep:
            return []
        n = max(1, min(len(deep), int(take or config.SCRAPE_DEEP_SHARDS_PER_TICK)))
        cooldown = getattr(self._scrape_limiter, "cooldown", None)
        picked: list[dict[str, str]] = []
        scanned = 0
        while len(picked) < n and scanned < len(deep):
            shard = deep[self._deep_shard_idx % len(deep)]
            self._deep_shard_idx = (self._deep_shard_idx + 1) % len(deep)
            scanned += 1
            portal = (shard.get("portal") or "").strip().lower()
            if cooldown is not None and portal and cooldown.active(portal):
                continue
            picked.append(shard)
        return picked

    def _merge_pending_discovery(self, listings: list[Listing]) -> list[Listing]:
        pending = list(getattr(self, "_pending_discovery", None) or [])
        self._pending_discovery = []
        if not pending:
            return listings
        seen = {item.id for item in listings}
        merged = list(listings)
        for item in pending:
            if item.id in seen:
                continue
            seen.add(item.id)
            merged.append(item)
        return merged[:2500]

    async def _warmup_ulov_sitemap(self, shards: list[dict[str, str]]) -> None:
        """One sitemap GET per discovery tick so rent+sale shards share the 8 min cache."""
        from app.ulovdomov import sitemap_cache_fresh

        if sitemap_cache_fresh():
            return
        url = next((item.get("search_url") or "" for item in shards if (item.get("portal") or "") == "ulovdomov"), "")
        if not url:
            return
        client = self.client_for(url)
        loader = getattr(client, "_load_sitemap_rows", None)
        if loader is None:
            return
        await loader()

    async def _recent_catalog_tick(self) -> None:
        """High-priority NewDiscovery; never waits for rolling deep."""
        recent_all = recent_shards()
        recent = prepare_discovery_shards(self._scrape_limiter.cooldown)
        if not recent:
            return

        started = time.monotonic()
        engine = self._discovery_engine
        disc_stats = {"n": 0, "new": 0, "updated": 0, "same": 0, "deferred_write": 0}
        disc_listings: list[Listing] = []
        disc_pages = 0
        disc_deferred = 0
        error: str | None = None
        write_note: str | None = None
        self._discovery_inflight = True
        try:
            await self._warmup_ulov_sitemap(recent)
            results = await engine.fetch_shards(
                recent,
                client_factory=self.client_for,
                max_pages=config.SCRAPE_RECENT_PAGES,
                deadline_sec=float(config.SCRAPE_DISCOVERY_DEADLINE_SEC),
            )
            seen: set[int] = set()
            for item in results:
                disc_pages += item.pages_ok
                disc_deferred += len(item.deferred_pages)
                for listing in item.listings:
                    if listing.id in seen:
                        continue
                    seen.add(listing.id)
                    disc_listings.append(listing)
            disc_listings = self._merge_pending_discovery(disc_listings)
            if disc_listings:
                written, write_note = await self._try_catalog_upsert(
                    disc_listings,
                    kind="refresh",
                    timeout_sec=3.0,
                    write_deadline_sec=12.0,
                )
                if written is not None:
                    disc_stats = written
                    disc_deferred += int(written.get("deferred_write") or 0)
                    self._pending_discovery = []
                else:
                    self._pending_discovery = disc_listings[:2500]
                    disc_deferred += len(disc_listings)
        except Exception as exc:
            error = str(exc)[:240]
            self.last_error = f"recent-catalog: {error}"
        finally:
            self._discovery_inflight = False

        tick = {
            "at": utc_now(),
            "kind": "new_discovery",
            "role": config.SCRAPE_ROLE,
            "ms": int((time.monotonic() - started) * 1000),
            "error": error,
            "note": write_note,
            "discovery": {
                "shards": len(recent),
                "shards_all": len(recent_all),
                "shards_cooling": max(0, len(recent_all) - len(recent)),
                "listings": len(disc_listings),
                "pages_ok": disc_pages,
                "deferred": disc_deferred,
                "deferred_engine": len(engine.deferred),
                "new": int(disc_stats.get("new") or 0),
                "updated": int(disc_stats.get("updated") or 0),
                "same": int(disc_stats.get("same") or 0),
                "write_skipped": bool(write_note) and not disc_listings,
            },
            "refresh": {},
            "metrics": {**engine.metrics.snapshot(), "limit": engine.limiter.limit},
        }
        try:
            await self._record_tick(tick)
            print(
                f"new_discovery listings={len(disc_listings)} new={disc_stats.get('new')} "
                f"shards={len(recent)}/{len(recent_all)} ms={tick['ms']}"
                + (f" note={write_note}" if write_note else ""),
                flush=True,
            )
        except Exception as exc:
            self.last_error = f"scrape-tick: {exc}"
            print(f"record_scrape_tick failed: {exc}", flush=True)

    async def _deep_catalog_tick(self) -> None:
        """Low-priority rolling crawl; monitor and discovery requests preempt its queue."""
        from app.scrape_engine import should_yield_deep

        if should_yield_deep(
            discovery_inflight=self._discovery_inflight,
            waiting_below=self._scrape_limiter.waiting_below(2),
            enabled=bool(config.SCRAPE_DEEP_YIELD_TO_DISCOVERY),
        ):
            return
        deep = self.next_deep_shards()
        if not deep:
            return
        started = time.monotonic()
        engine = self._deep_engine
        listings: list[Listing] = []
        pages_ok = 0
        deferred = 0
        stats = {"n": 0, "new": 0, "updated": 0, "same": 0, "deferred_write": 0}
        error: str | None = None
        try:
            results = await engine.fetch_shards(
                deep,
                client_factory=self.client_for,
                max_pages=config.SCRAPE_DEEP_PAGES,
                deadline_sec=float(config.SCRAPE_DEEP_DEADLINE_SEC),
            )
            seen: set[int] = set()
            for item in results:
                pages_ok += item.pages_ok
                deferred += len(item.deferred_pages)
                for listing in item.listings:
                    if listing.id in seen:
                        continue
                    seen.add(listing.id)
                    listings.append(listing)
            if listings:
                written, note = await self._try_catalog_upsert(
                    listings,
                    kind="refresh",
                    timeout_sec=1.0,
                    write_deadline_sec=12.0,
                )
                if written is not None:
                    stats = written
                    deferred += int(written.get("deferred_write") or 0)
                else:
                    deferred += len(listings)
                    error = note
        except Exception as exc:
            error = str(exc)[:240]
            self.last_error = f"rolling-deep: {error}"

        deep_total = len([item for item in daily_shards() if item.get("portal") == "sreality"]) or 1
        coverage_pct = round(100.0 * self._deep_shard_idx / deep_total, 1)
        tick = {
            "at": utc_now(),
            "kind": "rolling_deep",
            "role": config.SCRAPE_ROLE,
            "ms": int((time.monotonic() - started) * 1000),
            "error": error if error and not error.startswith("write-deferred:") else None,
            "note": error if error and error.startswith("write-deferred:") else None,
            "discovery": {},
            "refresh": {
                "shards": len(deep),
                "listings": len(listings),
                "pages_ok": pages_ok,
                "deferred": deferred,
                "new": int(stats.get("new") or 0),
                "updated": int(stats.get("updated") or 0),
                "same": int(stats.get("same") or 0),
                "deep_idx": self._deep_shard_idx,
                "deep_total": deep_total,
                "coverage_pct": coverage_pct,
                "notified": 0,
            },
            "metrics": {**engine.metrics.snapshot(), "limit": engine.limiter.limit},
        }
        await self._record_tick(tick)
        print(
            f"rolling_deep listings={len(listings)} shards={len(deep)} "
            f"cover={coverage_pct}% ms={tick['ms']}",
            flush=True,
        )

    async def scrape_search_url(self, url: str, *, max_pages: int = 40) -> dict[str, Any]:
        """Manually crawl a portal search URL into the catalog (admin/provoz)."""
        from app.catalog_sync import normalize_search_url
        from app.scrape_engine import ScrapeEngine
        from app.sources import portal_of

        cleaned = normalize_search_url(url)
        if not cleaned:
            return {"ok": False, "error": "Chybí URL hledání"}
        pages = max(1, min(200, int(max_pages or 40)))
        client = self.client_for(cleaned)
        engine = ScrapeEngine()
        started = time.monotonic()
        result = await engine.fetch_pages_parallel(
            shard_key=f"manual:{portal_of(cleaned)}:{cleaned[:80]}",
            fetch_page=lambda p: client.fetch_page(p, newest=True),
            max_pages=pages,
            deadline_monotonic=time.monotonic() + max(30.0, float(config.SCRAPE_FULL_MARKET_DEADLINE_SEC)),
        )
        listings = list(result.listings)
        stats = {"n": 0, "new": 0, "updated": 0, "same": 0}
        if listings:
            stats = await self._catalog_upsert(listings, kind="seeded")
        tick = {
            "at": utc_now(),
            "kind": "manual_url",
            "url": cleaned,
            "ms": int((time.monotonic() - started) * 1000),
            "discovery": {
                "shards": 1,
                "listings": len(listings),
                "pages_ok": result.pages_ok,
                "deferred": len(result.deferred_pages),
                "new": int(stats.get("new") or 0),
                "updated": int(stats.get("updated") or 0),
                "same": int(stats.get("same") or 0),
                "total": result.total,
            },
            "refresh": {"listings": 0, "shards": 0, "notified": 0},
            "metrics": {**engine.metrics.snapshot(), "limit": engine.limiter.limit},
        }
        await self._job_db(self.store.record_scrape_tick, tick)
        return {
            "ok": True,
            "url": cleaned,
            "portal": portal_of(cleaned),
            "listings": len(listings),
            "pages_ok": result.pages_ok,
            "deferred": result.deferred_pages,
            "total": result.total,
            "new": stats.get("new") or 0,
            "updated": stats.get("updated") or 0,
            "same": stats.get("same") or 0,
            "ms": tick["ms"],
            "error": result.error,
        }

    async def _catalog_loop(self) -> None:
        await asyncio.sleep(8)
        while self.running:
            try:
                await self.maybe_run_catalog_sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._job_db(self.store.set_meta, "catalog_sync_error", str(exc))
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

    async def _dedupe_loop(self) -> None:
        await asyncio.sleep(90)
        while self.running:
            try:
                await self.maybe_run_dedupe()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.patch_dedupe_meta(
                    {"status": "error", "error": str(exc)[:300], "last_attempt": utc_now()}
                )
            try:
                await asyncio.sleep(300)
            except asyncio.CancelledError:
                raise

    def _dedupe_due_today(self) -> bool:
        settings = self.store.dedupe_settings()
        if not settings.get("enabled"):
            return False
        now = datetime.now().astimezone()
        hour = int(settings.get("hour") or 3)
        # Keep auto-dedupe in a short night window — all-day retries lock SQLite for minutes.
        if now.hour < hour or now.hour > hour + 2:
            return False
        today = now.date().isoformat()
        last = str(settings.get("last_run") or "")
        if last.startswith(today):
            return False
        attempt = str(settings.get("last_attempt") or "")
        if attempt.startswith(today):
            return False
        return True

    async def maybe_run_dedupe(self) -> dict[str, Any]:
        if self.dedupe_running or self.dedupe_scanning:
            return {"ok": False, "reason": "already-running"}
        # Dedupe holds long write locks — never overlap with catalog sync / recent scrape.
        if self.catalog_running or self.catalog_running_portals:
            return {"ok": False, "reason": "catalog-running"}
        if not self._dedupe_due_today():
            return {"ok": False, "reason": "not-due"}
        return await self.run_dedupe()

    def start_dedupe(self) -> dict[str, Any]:
        if self.dedupe_running or self.dedupe_scanning:
            return {"ok": False, "reason": "already-running"}
        if self.catalog_running or self.catalog_running_portals:
            return {"ok": False, "reason": "catalog-running"}
        asyncio.create_task(self.run_dedupe(), name="dedupe-run")
        return {"ok": True, "started": True}

    def start_dedupe_scan(self) -> dict[str, Any]:
        if self.dedupe_running or self.dedupe_scanning:
            return {"ok": False, "reason": "already-running"}
        asyncio.create_task(self.run_dedupe_scan(), name="dedupe-scan")
        return {"ok": True, "started": True}

    async def run_dedupe(self) -> dict[str, Any]:
        if self.dedupe_running or self.dedupe_scanning:
            return {"ok": False, "reason": "already-running"}
        self.dedupe_running = True
        self.store.patch_dedupe_meta({"status": "running", "error": "", "last_attempt": utc_now()})
        try:
            stats = await asyncio.to_thread(lambda: self.store.merge_duplicate_listings(force=True))
            return {"ok": True, "stats": stats}
        except Exception as exc:
            self.store.patch_dedupe_meta(
                {"status": "error", "error": str(exc)[:300], "last_attempt": utc_now()}
            )
            print(f"Sloučení duplicit selhalo: {exc}", flush=True)
            return {"ok": False, "error": str(exc)}
        finally:
            self.dedupe_running = False

    async def run_dedupe_scan(self) -> dict[str, Any]:
        if self.dedupe_running or self.dedupe_scanning:
            return {"ok": False, "reason": "already-running"}
        self.dedupe_scanning = True
        self.store.patch_dedupe_meta({"status": "scanning", "error": ""})
        try:
            result = await asyncio.to_thread(self.store.preview_duplicate_listings)
            return {"ok": True, **result}
        except Exception as exc:
            self.store.patch_dedupe_meta({"status": "error", "error": str(exc)[:300]})
            print(f"Kontrola duplicit selhala: {exc}", flush=True)
            return {"ok": False, "error": str(exc)}
        finally:
            self.dedupe_scanning = False

    def _due_catalog_portals(self, force: bool = False) -> list[str]:
        now = datetime.now().astimezone()
        today = now.date().isoformat()
        due: list[str] = []
        for portal, hour in config.CATALOG_SYNC_HOURS.items():
            if not force:
                if now.hour < hour:
                    continue
                # No all-day catch-up: missed window must wait until tomorrow (or manual run).
                # Catch-up of Bazos/iDNES was holding SQLite locks for hours and killing minute ticks.
                if now.hour > hour + 2:
                    continue
            last = self.store.get_meta(f"catalog_sync_{portal}_last") or ""
            status = str(self.store.get_meta(f"catalog_sync_{portal}_status") or "")
            if not force and str(last).startswith(today):
                continue
            if not force and status in {"done", "partial", "error", "running"} and str(last).startswith(today):
                continue
            due.append(portal)
        return due

    def _catalog_due_today(self) -> bool:
        return bool(self._due_catalog_portals())

    async def maybe_run_catalog_sync(self, force: bool = False) -> dict[str, Any]:
        portals = None if force else await self._job_db(self._due_catalog_portals)
        if not force and not portals:
            return {"ok": False, "reason": "not-due"}
        wanted = {str(item) for item in (portals or config.CATALOG_SYNC_HOURS) if item}
        to_start = wanted - self.catalog_running_portals
        if not to_start:
            return {"ok": False, "reason": "already-running"}
        if not force and self.catalog_running_portals:
            return {"ok": False, "reason": "already-running"}
        if not force:
            pick = "bazos" if "bazos" in to_start else sorted(to_start)[0]
            to_start = {pick}
        return await self.run_catalog_sync(portals=sorted(to_start))

    def start_catalog_sync(self, portals: list[str] | None = None) -> dict[str, Any]:
        wanted = {str(item) for item in (portals or config.CATALOG_SYNC_HOURS) if item}
        if config.SCRAPE_ROLE == "web":
            self.store.set_meta("catalog_sync_request", json.dumps(sorted(wanted)))
            return {
                "ok": True,
                "queued": True,
                "portals": sorted(wanted),
                "status": self.store.catalog_sync_status(),
            }
        to_start = wanted - self.catalog_running_portals
        if not to_start:
            return {"ok": False, "reason": "already-running", "status": self.store.catalog_sync_status()}
        self.catalog_running_portals |= to_start
        self.catalog_running = True
        asyncio.create_task(
            self.run_catalog_sync(portals=sorted(to_start), rerun=bool(portals), claimed=True),
            name="sreality-catalog-run",
        )
        return {"ok": True, "started": True, "portals": sorted(to_start), "status": self.store.catalog_sync_status()}

    async def run_catalog_sync(
        self, portals: list[str] | None = None, rerun: bool = False, claimed: bool = False
    ) -> dict[str, Any]:
        wanted = {str(item) for item in (portals or config.CATALOG_SYNC_HOURS) if item}
        if not claimed:
            to_start = wanted - self.catalog_running_portals
            if not to_start:
                return {"ok": False, "reason": "already-running"}
            wanted = to_start
            self.catalog_running_portals |= wanted
            self.catalog_running = True
        started = utc_now()
        today = started[:10]
        shards = [item for item in daily_shards() if item["portal"] in wanted]
        await self._job_db(self.store.set_meta, "catalog_sync_status", "running")
        await self._job_db(self.store.set_meta, "catalog_sync_error", None)
        shard_ok: dict[str, bool] = {}
        try:
            async def run_portal(portal: str) -> None:
                portal_shards = [item for item in shards if item["portal"] == portal]
                async def run_shard(shard: dict[str, str]) -> None:
                    job = await self._job_db(
                        self.store.ensure_scrape_job,
                        kind=shard["kind"],
                        portal=shard["portal"],
                        shard_key=shard["shard_key"],
                        search_url=shard["search_url"],
                    )
                    finished = str(job.get("finished_at") or "")
                    job_status = str(job.get("status") or "")
                    if (
                        not rerun
                        and job_status == "done"
                        and finished[:10] == today
                    ):
                        shard_ok[shard["shard_key"]] = True
                        return
                    if rerun or finished[:10] != today or job_status in {"partial", "error", "pending", "running"}:
                        await self._job_db(
                            self.store.update_scrape_job,
                            job["id"],
                            page=1,
                            upserts=0,
                            status="pending",
                            finished_at=None,
                            last_error=None,
                        )
                        job = {**job, "page": 1, "upserts": 0, "started_at": None, "status": "pending"}
                    shard_ok[shard["shard_key"]] = await self._run_catalog_job(job)

                if portal == "bazos":
                    # One Bazos shard at a time — parallel full-catalog upserts lock SQLite for minutes.
                    shard_gate = asyncio.Semaphore(1)

                    async def run_bazos_shard(shard: dict[str, str]) -> None:
                        async with shard_gate:
                            await run_shard(shard)

                    await asyncio.gather(*(run_bazos_shard(shard) for shard in portal_shards))
                else:
                    for shard in portal_shards:
                        await run_shard(shard)

            await asyncio.gather(*(run_portal(portal) for portal in sorted(wanted)))
            complete_portals = []
            for portal in wanted:
                keys = [item["shard_key"] for item in shards if item["portal"] == portal]
                ok = bool(keys) and all(shard_ok.get(key) for key in keys)
                await self._job_db(
                    self.store.set_meta, f"catalog_sync_{portal}_status", "done" if ok else "partial"
                )
                await self._job_db(self.store.set_meta, f"catalog_sync_{portal}_last", utc_now())
                if ok:
                    complete_portals.append(portal)
            if complete_portals:
                await self._job_db(self.store.mark_catalog_stale_gone, started, complete_portals)
            all_today = True
            for portal in config.CATALOG_SYNC_HOURS:
                last = await self._job_db(self.store.get_meta, f"catalog_sync_{portal}_last") or ""
                status = await self._job_db(self.store.get_meta, f"catalog_sync_{portal}_status") or ""
                if not (str(last).startswith(today) and status == "done"):
                    all_today = False
                    break
            await self._job_db(self.store.set_meta, "catalog_sync_status", "done" if all_today else "partial")
            await self._job_db(self.store.set_meta, "catalog_sync_last", utc_now())
            status = await self._job_db(self.store.catalog_sync_status)
            return {"ok": all_today, "status": status}
        except Exception as exc:
            await self._job_db(self.store.set_meta, "catalog_sync_status", "error")
            await self._job_db(self.store.set_meta, "catalog_sync_error", str(exc))
            raise
        finally:
            self.catalog_running_portals -= wanted
            self.catalog_running = bool(self.catalog_running_portals)

    async def _run_catalog_job(self, job: dict[str, Any]) -> bool:
        client = self.client_for(job["search_url"])
        if str(job.get("portal") or "") == "bazos" and hasattr(client, "fetch_catalog"):
            return await self._run_bazos_catalog_job(job, client)
        page = max(int(job.get("page") or 1), 1)
        upserts = int(job.get("upserts") or 0)
        self.store.update_scrape_job(
            job["id"],
            status="running",
            started_at=utc_now(),
            last_error=None,
        )
        total = int(job.get("last_total") or 0)
        seen_ids: set[int] = set()
        try:
            from app.scrape_engine import ScrapeEngine

            engine = ScrapeEngine()
            needed: int | None = None
            while page <= 400:
                window = max(1, engine.limiter.limit)
                pages = list(range(page, min(page + window, (needed or 400) + 1)))
                if not pages:
                    break
                results = await asyncio.gather(
                    *(engine.fetch_one_page(lambda p=p: client.fetch_page(p, newest=True), p) for p in pages)
                )
                empty_streak = 0
                chunk: list[Listing] = []
                for result in results:
                    if result.error:
                        if any(code in (result.error or "") for code in ("403", "429")):
                            await asyncio.sleep(20)
                        raise RuntimeError(result.error)
                    total = result.total or total
                    if not result.listings:
                        empty_streak += 1
                        continue
                    fresh = [item for item in result.listings if item.id not in seen_ids]
                    if not fresh:
                        empty_streak += 1
                        continue
                    for listing in fresh:
                        seen_ids.add(listing.id)
                        chunk.append(listing)
                    page_size = max(len(result.listings), 1)
                    if total:
                        needed = (int(total) + page_size - 1) // page_size
                if chunk:
                    await self._catalog_upsert(chunk, kind="seeded")
                    upserts += len(chunk)
                page = pages[-1] + 1
                # Persist progress once per window (not per page).
                self.store.update_scrape_job(job["id"], page=page, upserts=upserts, last_total=total)
                if needed and pages[-1] >= needed:
                    break
                if empty_streak >= len(pages):
                    break
            complete = True
            if total and upserts < max(1, int(total * 0.92)):
                complete = False
            self.store.update_scrape_job(
                job["id"],
                status="done" if complete else "partial",
                finished_at=utc_now(),
                last_total=total,
                upserts=upserts,
                page=page,
                last_error=None
                if complete
                else f"Incomplete crawl: upserts={upserts} total={total}",
            )
            return complete
        except Exception as exc:
            self.store.update_scrape_job(
                job["id"],
                status="error",
                last_error=str(exc),
                upserts=upserts,
                page=page,
            )
            return False

    async def _run_bazos_catalog_job(self, job: dict[str, Any], client: Any) -> bool:
        started = time.monotonic()
        await self._job_db(
            self.store.update_scrape_job,
            job["id"],
            status="running",
            started_at=utc_now(),
            last_error=None,
        )
        try:
            listings, total = await client.fetch_catalog(self._bazos_gate)
            # Chunked writes — a single 30k+ lock was starving minute_discovery ticks.
            await self._catalog_upsert(listings, kind="seeded")
            pages = max(1, (int(total) + 19) // 20) if total else 1
            await self._job_db(
                self.store.update_scrape_job,
                job["id"],
                status="done",
                finished_at=utc_now(),
                last_total=total,
                upserts=len(listings),
                page=pages,
            )
            return True
        except Exception as exc:
            await self._job_db(
                self.store.update_scrape_job,
                job["id"],
                status="error",
                last_error=str(exc)[:300],
            )
            return False

    async def _sold_loop(self) -> None:
        await asyncio.sleep(15)
        while self.running:
            try:
                await self._sold_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{exc}"
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise

    async def _sold_tick(self) -> None:
        for pending in self.store.pending_sold_pings(limit=10):
            await self._send_sold_ping(pending, pending.get("monitor_name") or "")
        due = [item for item in self.store.list_monitors() if item.get("enabled") and self._inventory_due(item)]
        if not due:
            return
        for row in self.store.stale_catalog_listings(days=1, limit=8):
            listing = _listing_from_catalog_dict(row)
            try:
                listing = await self.client_for(row.get("url") or "").fetch_detail(listing)
                self.store.upsert_catalog_listing(listing, kind="refresh")
            except ListingGone:
                self.store.mark_catalog_listing_gone(row.get("listing_key") or "")
            except Exception:
                continue
            await asyncio.sleep(0.2)
        self.store.update_monitor_stats(due[0]["id"], last_inventory=utc_now(), last_error=None)
        for pending in self.store.pending_sold_pings(limit=10):
            await self._send_sold_ping(pending, pending.get("monitor_name") or "")

    async def _probe_sold(self, monitor: dict[str, Any], client, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            listing = self._listing_from_row(row)
            try:
                listing = await client.fetch_detail(listing)
                self.store.upsert_seen(monitor["id"], listing, notified=False, kind="refresh")
            except ListingGone:
                await self.notify_sold(monitor, int(row["id"]))
            except Exception:
                continue
            await asyncio.sleep(0.2)

    async def maybe_send_digest(self) -> None:
        settings = self.store.app_settings()
        hour = int(settings.get("digest_hour") or 8)
        now = datetime.now().astimezone()
        if now.hour != hour:
            return
        last = settings.get("digest_last") or ""
        today = now.date().isoformat()
        if str(last).startswith(today):
            return
        since = last or now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        items = self.store.digest_items(since)
        webhook = self.store.digest_webhook()
        prefs = self.store.notify_prefs()
        if webhook and items and prefs.get("discord") is not False:
            await send_digest(webhook, items)
        if items:
            await asyncio.to_thread(web_push.notify_digest, self.store, items)
            try:
                await mail_notify.notify_digest(self.store, items)
            except Exception:
                pass
        self.store.set_meta("digest_last", utc_now())

    async def send_digest_test(self, webhook_url: str | None = None) -> dict[str, Any]:
        if webhook_url and not self.store.discord_webhook_url():
            self.store.save_app_settings({"digest_webhook": str(webhook_url).strip()})
        settings = self.store.app_settings()
        last = settings.get("digest_last") or ""
        since = last or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        items = self.store.digest_items(since)
        webhook = self.store.digest_webhook()
        prefs = self.store.notify_prefs()
        sent_discord = False
        if webhook and prefs.get("discord") is not False:
            await send_digest(webhook, items, test=True)
            sent_discord = True
        sent_push = await asyncio.to_thread(web_push.notify_test, self.store)
        sent_mail = False
        try:
            sent_mail = await mail_notify.notify_digest(self.store, items, test=True)
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        if not sent_discord and not sent_push and not sent_mail:
            raise RuntimeError("Zapněte Discord, Push nebo e-mailové notifikace")
        return {"ok": True, "count": len(items), "push": bool(sent_push), "discord": sent_discord, "email": sent_mail}

    async def send_test(self, monitor_id: str | None = None) -> dict[str, Any]:
        monitors = self.store.list_monitors()
        monitor = next((item for item in monitors if item["id"] == monitor_id), None) if monitor_id else None
        monitor = monitor or next((item for item in monitors if item.get("enabled")), None) or (monitors[0] if monitors else None)
        if not monitor:
            raise RuntimeError("Žádný monitor")
        client = self.client_for(monitor["search_url"])
        listings, _ = await client.fetch_pages(1, newest=True)
        webhook = self.store.notify_webhook(monitor.get("search_url") or "", monitor.get("webhook_url"))
        template = self.store.get_template(monitor.get("template_id") or "default")
        prefs = self.store.notify_prefs()
        sent_discord = False
        sent_push = 0
        if not listings:
            if webhook and prefs.get("discord") is not False:
                await send_text(webhook, f"Test monitoru: {source_name(monitor.get('search_url') or '')} teď nevrátilo žádný listing.")
                sent_discord = True
            sent_push = await asyncio.to_thread(web_push.notify_test, self.store)
            sent_mail = False
            try:
                sent_mail = await self._notify_email({}, "test", monitor.get("name") or "", ignore_quiet=True, prefix="Test monitoru")
            except Exception:
                sent_mail = False
            if not sent_discord and not sent_push and not sent_mail:
                raise RuntimeError("Zapněte Discord, Push nebo e-mailové notifikace")
            return {"ok": True, "listing": None, "discord": sent_discord, "push": bool(sent_push), "email": sent_mail}
        listing = listings[0]
        if webhook and prefs.get("discord") is not False:
            await send_listing(
                webhook,
                listing,
                template_config=(template or {}).get("config"),
                monitor_name=monitor.get("name") or "",
                prefix="**TEST monitoru** — ukázka formátu, toto není nový zásah.\n\n",
            )
            sent_discord = True
        sent_push = await self._notify_push(listing, "test", monitor.get("name") or "", ignore_quiet=True)
        if not sent_push and prefs.get("push"):
            sent_push = await asyncio.to_thread(web_push.notify_test, self.store)
        sent_mail = False
        try:
            sent_mail = await mail_notify.notify_listing(
                self.store,
                listing,
                "test",
                monitor.get("name") or "",
                ignore_quiet=True,
                prefix="TEST — ukázka formátu, toto není nový zásah.",
            )
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
        if not sent_discord and not sent_push and not sent_mail:
            raise RuntimeError("Zapněte Discord, Push nebo e-mailové notifikace")
        return {"ok": True, "listing": listing.to_dict(), "monitor_id": monitor["id"], "discord": sent_discord, "push": bool(sent_push), "email": sent_mail}

    async def _ulov_hydrate_loop(self) -> None:
        # Lazy import so InstantSiteASGI / web request modules stay off this path.
        from app import ulov_hydrate

        await ulov_hydrate.loop(self)

    async def backfill_missing_coords(self) -> None:
        rows = self.store.missing_coords(notified_only=True)
        for row in rows:
            listing = Listing(
                id=int(row["id"]),
                name=row.get("name") or "",
                price_czk=row.get("price_czk"),
                price_label=row.get("price_label") or "",
                disposition=row.get("disposition") or "",
                area_m2=row.get("area_m2"),
                locality=row.get("locality") or "",
                url=row.get("url") or "",
                image_url=row.get("image_url"),
            )
            monitor = self.store.get_monitor(row.get("monitor_id") or "default")
            search_url = (monitor or {}).get("search_url") or config.SEARCH_URL
            try:
                listing = await self.client_for(search_url).fetch_detail(listing)
                self.store.update_location(listing, row.get("monitor_id"))
            except Exception:
                continue
            await asyncio.sleep(0.2)

    def status(self, *, fresh: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        if not fresh and self._status_cache is not None and now - self._status_cache_at < 1.5:
            return self._status_cache
        monitors = self.store.list_monitors()
        errors = [item.get("last_error") for item in monitors if item.get("last_error")]
        last_checks = [item.get("last_check") for item in monitors if item.get("last_check")]
        catalog = self.store.catalog_sync_status()
        tracked = self.store.count()
        new_today = self.store.new_today_count()
        recent = self.store.recent_notified(limit=36, twins=False)
        recent_today = self.store.recent_notified(limit=24, since=local_day_start(), twins=False)
        templates = self.store.list_templates()
        settings = self.store.app_settings()
        settle_pending_if_due(self.store)
        scrape_tick = None
        raw_tick = self.store.get_meta("scrape_worker_tick")
        if raw_tick:
            try:
                scrape_tick = json.loads(str(raw_tick))
            except json.JSONDecodeError:
                scrape_tick = None
        payload = {
            "running": self.running,
            "checking": self.checking,
            "seeded": all(item.get("seeded") for item in monitors) if monitors else False,
            "last_check": max(last_checks) if last_checks else None,
            "last_error": self.last_error or (errors[0] if errors else None),
            "interval_sec": config.POLL_INTERVAL_SEC,
            "poll_pages": config.POLL_PAGES,
            "scrape_role": config.SCRAPE_ROLE,
            "scrape_worker": scrape_tick,
            "tracked": tracked,
            "new_today": new_today,
            "search_total": catalog.get("listings") or 0,
            "webhook_ready": bool(self.store.discord_webhook_url())
            or any(self.store.notify_webhook(item.get("search_url") or "", item.get("webhook_url")) for item in monitors)
            or bool(config.DISCORD_WEBHOOK_URL or config.BEZREALITKY_WEBHOOK_URL),
            "recent": recent,
            "recent_today": recent_today,
            "monitors": monitors,
            "templates": templates,
            "settings": settings,
            "version": current_version(),
            "catalog_sync": catalog,
            "catalog_running": self.catalog_running,
            "billing": billing_state(self.store),
            "storage": {
                "path": str(config.DB_PATH),
                "persistent": config.PERSISTENT_STORAGE,
            },
        }
        self._status_cache = payload
        self._status_cache_at = time.monotonic()
        return payload


Monitor = Hub


def snapshot_price_change(prev: dict[str, Any], listing: Listing) -> list[tuple[str, str, str]]:
    prev_price = prev.get("price_czk")
    if prev_price is None or listing.price_czk is None:
        return []
    try:
        if int(prev_price) == int(listing.price_czk):
            return []
    except (TypeError, ValueError):
        return []
    before = prev.get("price_label") or format_price(prev_price, "měsíc")
    return [("Cena", str(before), listing.price_label)]


def merge_price_changes(*groups: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    merged: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for group in groups:
        for change in group:
            if change not in seen:
                seen.add(change)
                merged.append(change)
    return merged


def detail_price_change(listing: Listing) -> list[tuple[str, str, str]]:
    if listing.old_price_czk and listing.price_czk and listing.old_price_czk != listing.price_czk:
        return [("Cena", format_price(listing.old_price_czk, "měsíc"), listing.price_label)]
    return []
