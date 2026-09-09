from __future__ import annotations

import asyncio
import traceback
from datetime import datetime, timezone
from typing import Any

from app import config
from app.discord_notify import send_digest, send_listing, send_sold, send_text
from app.sreality import Listing, ListingGone, format_price, is_recently_created
from app.sources import client_for, source_name, webhook_for
from app.store import Store, utc_now
from app.version import current_version


class Hub:
    def __init__(self) -> None:
        self.store = Store(config.DB_PATH)
        self.clients: dict[str, object] = {}
        self.running = False
        self.checking = False
        self._task: asyncio.Task[None] | None = None
        self._sold_task: asyncio.Task[None] | None = None
        self.last_error: str | None = None

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
        asyncio.create_task(self.backfill_missing_coords(), name="sreality-coords")

    async def stop(self) -> None:
        self.running = False
        for task in (self._task, self._sold_task):
            if not task:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._sold_task = None

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
            results = []
            for monitor in self.store.list_monitors():
                if not monitor.get("enabled") or not self._monitor_due(monitor):
                    continue
                results.append(await self.check_monitor(monitor))
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
            results = []
            for monitor in monitors:
                if not monitor.get("enabled"):
                    continue
                results.append(await self.check_monitor(monitor))
            return {"ok": True, "results": results}
        finally:
            self.checking = False

    async def check_monitor(self, monitor: dict[str, Any]) -> dict[str, Any]:
        monitor_id = monitor["id"]
        client = self.client_for(monitor["search_url"])
        template = self.store.get_template(monitor.get("template_id") or "default")
        template_config = (template or {}).get("config")
        webhook = webhook_for(monitor.get("search_url") or "", monitor.get("webhook_url"))
        try:
            if not monitor.get("seeded"):
                listings, total = await client.fetch_all(newest=True)
                for listing in listings:
                    self.store.upsert_seen(monitor_id, listing, notified=False)
                self.store.set_monitor_seeded(monitor_id, True)
                self.store.update_monitor_stats(
                    monitor_id,
                    last_check=utc_now(),
                    last_inventory=utc_now(),
                    last_error=None,
                    last_total=total,
                    last_found=len(listings),
                )
                return {"id": monitor_id, "seeded": True, "tracked": len(listings), "total": total, "new": []}

            listings, total = await client.fetch_pages(config.POLL_PAGES, newest=True)
            known = self.store.get_many(monitor_id, [item.id for item in listings])
            notified: list[Listing] = []
            errors: list[str] = []
            for listing in listings:
                try:
                    alert = await self._classify(client, listing, known.get(listing.id))
                    if alert is None:
                        self.store.snapshot_scrape_price(monitor_id, listing)
                        if listing.kind == "refresh":
                            self.store.upsert_seen(monitor_id, listing, notified=False, kind="refresh")
                        elif listing.lat is not None:
                            self.store.update_location(listing, monitor_id)
                        continue
                    already = self.store.url_already_notified(alert.url, exclude_monitor_id=monitor_id)
                    if already:
                        self.store.upsert_seen(monitor_id, alert, notified=True, kind=alert.kind or "new")
                    else:
                        await send_listing(
                            webhook,
                            alert,
                            template_config=template_config,
                            monitor_name=monitor.get("name") or "",
                        )
                        self.store.upsert_seen(monitor_id, alert, notified=True)
                        notified.append(alert)
                except ListingGone:
                    await self.notify_sold(monitor, listing.id)
                except Exception as exc:
                    errors.append(f"{listing.id}: {exc}")
            await self._probe_sold(monitor, client, self.store.stale_listings(monitor_id, days=1, limit=5))
            self.store.update_monitor_stats(
                monitor_id,
                last_check=utc_now(),
                last_error="; ".join(errors) if errors else None,
                last_total=total,
                last_found=len(listings),
            )
            return {
                "id": monitor_id,
                "seeded": False,
                "scanned": len(listings),
                "total": total,
                "new": [item.to_dict() for item in notified],
                "errors": errors,
            }
        except Exception as exc:
            self.store.update_monitor_stats(
                monitor_id,
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
        webhook = config.SOLD_WEBHOOK_URL
        if not webhook:
            return
        if self.store.url_sold_notified(row.get("url") or "", exclude_monitor_id=row.get("monitor_id")):
            self.store.mark_sold_notified(row["monitor_id"], int(row["id"]))
            return
        try:
            await send_sold(webhook, row, monitor_name=monitor_name)
            self.store.mark_sold_notified(row["monitor_id"], int(row["id"]))
        except Exception as exc:
            self.last_error = f"Sold Discord: {exc}"

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
        for monitor in self.store.list_monitors():
            if not monitor.get("enabled") or not self._inventory_due(monitor):
                continue
            client = self.client_for(monitor["search_url"])
            try:
                found, total = await client.fetch_all(newest=True)
            except Exception as exc:
                self.store.update_monitor_stats(monitor["id"], last_inventory=utc_now(), last_error=f"{exc}")
                return
            present = {item.id for item in found}
            self.store.touch_last_seen(monitor["id"], [item.id for item in found])
            self.store.update_monitor_stats(monitor["id"], last_inventory=utc_now(), last_error=None)
            complete = bool(total) and len(found) >= total
            rows = (
                self.store.active_missing(monitor["id"], present, limit=20)
                if complete
                else self.store.stale_listings(monitor["id"], days=1, limit=8)
            )
            await self._probe_sold(monitor, client, rows)
            return

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
        if webhook and items:
            await send_digest(webhook, items)
        self.store.set_meta("digest_last", utc_now())

    async def send_digest_test(self, webhook_url: str | None = None) -> dict[str, Any]:
        if webhook_url is not None:
            self.store.save_app_settings({"digest_webhook": str(webhook_url).strip()})
        settings = self.store.app_settings()
        last = settings.get("digest_last") or ""
        since = last or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        items = self.store.digest_items(since)
        webhook = (settings.get("digest_webhook") or "").strip() or self.store.digest_webhook()
        if not webhook:
            raise RuntimeError("Chybí webhook pro ranní digest")
        await send_digest(webhook, items, test=True)
        return {"ok": True, "count": len(items)}

    async def send_test(self, monitor_id: str | None = None) -> dict[str, Any]:
        monitors = self.store.list_monitors()
        monitor = next((item for item in monitors if item["id"] == monitor_id), None) if monitor_id else None
        monitor = monitor or next((item for item in monitors if item.get("enabled")), None) or (monitors[0] if monitors else None)
        if not monitor:
            raise RuntimeError("Žádný monitor")
        client = self.client_for(monitor["search_url"])
        listings, _ = await client.fetch_pages(1, newest=True)
        webhook = webhook_for(monitor.get("search_url") or "", monitor.get("webhook_url"))
        template = self.store.get_template(monitor.get("template_id") or "default")
        if not listings:
            await send_text(webhook, f"Test monitoru: {source_name(monitor.get('search_url') or '')} teď nevrátilo žádný listing.")
            return {"ok": True, "listing": None}
        listing = listings[0]
        await send_listing(
            webhook,
            listing,
            template_config=(template or {}).get("config"),
            monitor_name=monitor.get("name") or "",
            prefix="**TEST monitoru** — ukázka formátu, toto není nový zásah.\n\n",
        )
        return {"ok": True, "listing": listing.to_dict(), "monitor_id": monitor["id"]}

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

    def status(self) -> dict[str, Any]:
        monitors = self.store.list_monitors()
        errors = [item.get("last_error") for item in monitors if item.get("last_error")]
        last_checks = [item.get("last_check") for item in monitors if item.get("last_check")]
        return {
            "running": self.running,
            "checking": self.checking,
            "seeded": all(item.get("seeded") for item in monitors) if monitors else False,
            "last_check": max(last_checks) if last_checks else None,
            "last_error": self.last_error or (errors[0] if errors else None),
            "interval_sec": config.POLL_INTERVAL_SEC,
            "poll_pages": config.POLL_PAGES,
            "tracked": self.store.count(),
            "new_today": self.store.new_today_count(),
            "search_total": sum(item.get("last_total") or 0 for item in monitors),
            "webhook_ready": any(webhook_for(item.get("search_url") or "", item.get("webhook_url")) for item in monitors)
            or bool(config.DISCORD_WEBHOOK_URL or config.BEZREALITKY_WEBHOOK_URL),
            "recent": self.store.recent_notified(),
            "monitors": monitors,
            "templates": self.store.list_templates(),
            "settings": self.store.app_settings(),
            "version": current_version(),
        }


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
