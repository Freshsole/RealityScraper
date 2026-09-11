from __future__ import annotations

import asyncio
import json
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from app import config
from app import push as web_push
from app import email_notify as mail_notify
from app.discord_notify import send_digest, send_listing, send_sold, send_text
from app.sreality import Listing, ListingGone, format_price, is_recently_created, listing_from_dict
from app.sources import client_for, source_name
from app.catalog_sync import daily_shards, listing_is_new_for_monitor
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
        self._discord_task: asyncio.Task[None] | None = None
        self.catalog_running = False
        self.last_error: str | None = None
        self._portal_gate = asyncio.Semaphore(2)
        self._status_cache: dict[str, Any] | None = None
        self._status_cache_at = 0.0

    def client_for(self, search_url: str):
        client = self.clients.get(search_url)
        if client is None:
            client = client_for(search_url)
            self.clients[search_url] = client
        return client

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.last_error = None
        self._task = asyncio.create_task(self._loop(), name="sreality-hub")
        self._sold_task = asyncio.create_task(self._sold_loop(), name="sreality-sold")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="sreality-pings")
        self._catalog_task = asyncio.create_task(self._catalog_loop(), name="sreality-catalog")
        if config.DISCORD_BOT_TOKEN and config.DISCORD_GUILD_ID:
            from app.discord_bot import run_discord_bot

            self._discord_task = asyncio.create_task(run_discord_bot(self.store), name="discord-bot")
        asyncio.create_task(self.backfill_missing_coords(), name="sreality-coords")

    async def stop(self) -> None:
        self.running = False
        for task in (self._task, self._sold_task, self._ping_task, self._catalog_task, self._discord_task):
            if not task:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._sold_task = None
        self._ping_task = None
        self._catalog_task = None
        self._discord_task = None

    async def close(self) -> None:
        await self.stop()
        for client in self.clients.values():
            await client.aclose()
        self.clients.clear()

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.check_due()
                await self.maybe_send_digest()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{exc}"
            try:
                await asyncio.sleep(10)
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
            for monitor in self.store.list_monitors():
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
                if not any(self._monitor_due(item) for item in bucket["monitors"]):
                    continue
                results.append(await self.check_live_job(bucket["job"], bucket["monitors"]))
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
            async with self._portal_gate:
                if (job.get("kind") or "") == "monitor_live":
                    listings, total = await client.fetch_all(newest=True, max_pages=40)
                    notify_limit = max(config.POLL_PAGES * 20, 30)
                else:
                    listings, total = await client.fetch_pages(config.POLL_PAGES, newest=True)
                    notify_limit = len(listings)
            for index, listing in enumerate(listings):
                try:
                    result = self.store.upsert_catalog_listing(listing, kind="refresh")
                    targets = self.store.matching_monitors(listing, job["id"])
                    if not targets:
                        targets = monitors
                    for monitor in targets:
                        self.store.add_monitor_hit(monitor["id"], result["listing_key"])
                    if index >= notify_limit:
                        continue
                    alert = await self._classify(client, listing, result.get("prev"))
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
                except ListingGone:
                    for monitor in monitors:
                        await self.notify_sold(monitor, listing.id)
                except Exception as exc:
                    errors.append(f"{listing.id}: {exc}")
            for monitor in monitors:
                await self._probe_sold(monitor, client, self.store.stale_listings(monitor["id"], days=1, limit=5))
                current = self.store.get_monitor(monitor["id"]) or monitor
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
                self.store.update_monitor_stats(
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
                self.store.update_monitor_stats(
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
                item = self.store.claim_next_ping()
                if not item:
                    await asyncio.sleep(0.25)
                    continue
                try:
                    await self._dispatch_ping(item)
                    self.store.mark_ping_sent(item)
                except asyncio.CancelledError:
                    self.store.mark_ping_failed(int(item["id"]), "cancelled", int(item.get("attempts") or 1))
                    raise
                except Exception as exc:
                    self.last_error = f"Discord queue: {exc}"
                    self.store.mark_ping_failed(int(item["id"]), str(exc), int(item.get("attempts") or 1))
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

    async def _catalog_loop(self) -> None:
        await asyncio.sleep(8)
        while self.running:
            try:
                await self.maybe_run_catalog_sync()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set_meta("catalog_sync_error", str(exc))
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

    def _catalog_due_today(self) -> bool:
        last = self.store.get_meta("catalog_sync_last") or ""
        today = datetime.now().astimezone().date().isoformat()
        if str(last).startswith(today) and self.store.get_meta("catalog_sync_status") == "done":
            return False
        return datetime.now().astimezone().hour >= config.CATALOG_SYNC_HOUR

    async def maybe_run_catalog_sync(self, force: bool = False) -> dict[str, Any]:
        if self.catalog_running:
            return {"ok": False, "reason": "already-running"}
        if not force and not self._catalog_due_today():
            return {"ok": False, "reason": "not-due"}
        return await self.run_catalog_sync()

    def start_catalog_sync(self) -> dict[str, Any]:
        if self.catalog_running:
            return {"ok": False, "reason": "already-running", "status": self.store.catalog_sync_status()}
        asyncio.create_task(self.run_catalog_sync(), name="sreality-catalog-run")
        return {"ok": True, "started": True, "status": self.store.catalog_sync_status()}

    async def run_catalog_sync(self) -> dict[str, Any]:
        if self.catalog_running:
            return {"ok": False, "reason": "already-running"}
        self.catalog_running = True
        started = utc_now()
        today = started[:10]
        self.store.set_meta("catalog_sync_status", "running")
        self.store.set_meta("catalog_sync_error", None)
        shards = daily_shards()
        shard_ok: dict[str, bool] = {}
        try:
            for shard in shards:
                job = self.store.ensure_scrape_job(
                    kind=shard["kind"],
                    portal=shard["portal"],
                    shard_key=shard["shard_key"],
                    search_url=shard["search_url"],
                )
                finished = str(job.get("finished_at") or "")
                if job.get("status") == "done" and finished[:10] == today:
                    shard_ok[shard["shard_key"]] = True
                    continue
                if finished[:10] != today:
                    self.store.update_scrape_job(
                        job["id"],
                        page=1,
                        upserts=0,
                        status="pending",
                        finished_at=None,
                        last_error=None,
                    )
                    job = {**job, "page": 1, "upserts": 0, "started_at": None, "status": "pending"}
                shard_ok[shard["shard_key"]] = await self._run_catalog_job(job)
            complete_portals = []
            for portal in {item["portal"] for item in shards}:
                keys = [item["shard_key"] for item in shards if item["portal"] == portal]
                if keys and all(shard_ok.get(key) for key in keys):
                    complete_portals.append(portal)
            if complete_portals:
                self.store.mark_catalog_stale_gone(started, complete_portals)
            all_ok = bool(shards) and all(shard_ok.get(item["shard_key"]) for item in shards)
            self.store.set_meta("catalog_sync_status", "done" if all_ok else "partial")
            self.store.set_meta("catalog_sync_last", utc_now())
            return {"ok": all_ok, "status": self.store.catalog_sync_status()}
        except Exception as exc:
            self.store.set_meta("catalog_sync_status", "error")
            self.store.set_meta("catalog_sync_error", str(exc))
            raise
        finally:
            self.catalog_running = False

    async def _run_catalog_job(self, job: dict[str, Any]) -> bool:
        client = self.client_for(job["search_url"])
        page = max(int(job.get("page") or 1), 1)
        upserts = int(job.get("upserts") or 0)
        self.store.update_scrape_job(
            job["id"],
            status="running",
            started_at=utc_now(),
            last_error=None,
        )
        total = int(job.get("last_total") or 0)
        try:
            while True:
                try:
                    async with self._portal_gate:
                        batch, page_total = await client.fetch_page(page, newest=True)
                except Exception as exc:
                    if "403" in str(exc) or "429" in str(exc):
                        await asyncio.sleep(20)
                        async with self._portal_gate:
                            batch, page_total = await client.fetch_page(page, newest=True)
                    else:
                        raise
                total = page_total
                if not batch:
                    break
                for listing in batch:
                    self.store.upsert_catalog_listing(listing, kind="seeded")
                    upserts += 1
                self.store.update_scrape_job(job["id"], page=page + 1, upserts=upserts, last_total=total)
                if total:
                    needed = (int(total) + max(len(batch), 1) - 1) // max(len(batch), 1)
                    if page >= needed:
                        break
                page += 1
                await asyncio.sleep(0.15)
            self.store.update_scrape_job(
                job["id"],
                status="done",
                finished_at=utc_now(),
                last_total=total,
                upserts=upserts,
                page=page,
            )
            return True
        except Exception as exc:
            self.store.update_scrape_job(
                job["id"],
                status="error",
                last_error=str(exc),
                upserts=upserts,
                page=page,
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
        started = time.perf_counter()
        now = time.monotonic()
        if not fresh and self._status_cache is not None and now - self._status_cache_at < 1.5:
            # #region agent log
            try:
                with open("/Users/jirka/Desktop/Folders/RealityScraper/.cursor/debug-c31723.log", "a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "sessionId": "c31723",
                                "hypothesisId": "A",
                                "location": "monitor.py:status",
                                "message": "status cache hit",
                                "data": {"age_ms": round((now - self._status_cache_at) * 1000, 1)},
                                "timestamp": int(time.time() * 1000),
                                "runId": "post-fix",
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except Exception:
                pass
            # #endregion
            return self._status_cache
        parts: dict[str, float] = {}

        def _mark(name: str, begin: float) -> None:
            parts[name] = round((time.perf_counter() - begin) * 1000, 1)

        tick = time.perf_counter()
        monitors = self.store.list_monitors()
        _mark("list_monitors", tick)
        errors = [item.get("last_error") for item in monitors if item.get("last_error")]
        last_checks = [item.get("last_check") for item in monitors if item.get("last_check")]
        tick = time.perf_counter()
        catalog = self.store.catalog_sync_status()
        _mark("catalog_sync", tick)
        tick = time.perf_counter()
        tracked = self.store.count()
        _mark("count", tick)
        tick = time.perf_counter()
        new_today = self.store.new_today_count()
        _mark("new_today", tick)
        tick = time.perf_counter()
        recent = self.store.recent_notified(limit=36, twins=False)
        _mark("recent", tick)
        tick = time.perf_counter()
        recent_today = self.store.recent_notified(limit=24, since=local_day_start(), twins=False)
        _mark("recent_today", tick)
        tick = time.perf_counter()
        templates = self.store.list_templates()
        settings = self.store.app_settings()
        _mark("templates_settings", tick)
        settle_pending_if_due(self.store)
        payload = {
            "running": self.running,
            "checking": self.checking,
            "seeded": all(item.get("seeded") for item in monitors) if monitors else False,
            "last_check": max(last_checks) if last_checks else None,
            "last_error": self.last_error or (errors[0] if errors else None),
            "interval_sec": config.POLL_INTERVAL_SEC,
            "poll_pages": config.POLL_PAGES,
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
        # #region agent log
        try:
            with open("/Users/jirka/Desktop/Folders/RealityScraper/.cursor/debug-c31723.log", "a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "sessionId": "c31723",
                            "hypothesisId": "A",
                            "location": "monitor.py:status",
                            "message": "status parts",
                            "data": {
                                "ms": round((time.perf_counter() - started) * 1000, 1),
                                "parts": parts,
                                "checking": self.checking,
                                "n_monitors": len(monitors),
                                "n_recent": len(recent),
                                "tracked": tracked,
                            },
                            "timestamp": int(time.time() * 1000),
                            "runId": "post-fix",
                        },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception:
            pass
        # #endregion
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
