from __future__ import annotations

import asyncio
import traceback
from typing import Any

from app import config
from app.discord_notify import send_listing, send_text
from app.sreality import Listing, SrealityClient, format_price, is_recently_created
from app.store import Store, utc_now
from app.version import current_version


class Hub:
    def __init__(self) -> None:
        self.store = Store(config.DB_PATH)
        self.clients: dict[str, SrealityClient] = {}
        self.running = False
        self.checking = False
        self._task: asyncio.Task[None] | None = None
        self.last_error: str | None = None

    def client_for(self, search_url: str) -> SrealityClient:
        client = self.clients.get(search_url)
        if client is None:
            client = SrealityClient(search_url)
            self.clients[search_url] = client
        return client

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.last_error = None
        self._task = asyncio.create_task(self._loop(), name="sreality-hub")
        asyncio.create_task(self.backfill_missing_coords(), name="sreality-coords")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def close(self) -> None:
        await self.stop()
        for client in self.clients.values():
            await client.aclose()
        self.clients.clear()

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{exc}"
            try:
                await asyncio.sleep(config.POLL_INTERVAL_SEC)
            except asyncio.CancelledError:
                raise

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
        webhook = (monitor.get("webhook_url") or config.DISCORD_WEBHOOK_URL).strip()
        try:
            if not monitor.get("seeded"):
                listings, total = await client.fetch_all(newest=True)
                for listing in listings:
                    self.store.upsert_seen(monitor_id, listing, notified=False)
                self.store.set_monitor_seeded(monitor_id, True)
                self.store.update_monitor_stats(
                    monitor_id,
                    last_check=utc_now(),
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
                        if listing.kind == "refresh":
                            self.store.upsert_seen(monitor_id, listing, notified=False, kind="refresh")
                        elif listing.lat is not None:
                            self.store.update_location(listing, monitor_id)
                        continue
                    await send_listing(
                        webhook,
                        alert,
                        template_config=template_config,
                        monitor_name=monitor.get("name") or "",
                    )
                    self.store.upsert_seen(monitor_id, alert, notified=True)
                    notified.append(alert)
                except Exception as exc:
                    errors.append(f"{listing.id}: {exc}")
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
        client: SrealityClient,
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

    async def send_test(self, monitor_id: str | None = None) -> dict[str, Any]:
        monitors = self.store.list_monitors()
        monitor = next((item for item in monitors if item["id"] == monitor_id), None) if monitor_id else None
        monitor = monitor or next((item for item in monitors if item.get("enabled")), None) or (monitors[0] if monitors else None)
        if not monitor:
            raise RuntimeError("Žádný monitor")
        client = self.client_for(monitor["search_url"])
        listings, _ = await client.fetch_pages(1, newest=True)
        webhook = (monitor.get("webhook_url") or config.DISCORD_WEBHOOK_URL).strip()
        template = self.store.get_template(monitor.get("template_id") or "default")
        if not listings:
            await send_text(webhook, "Test monitoru: Sreality teď nevrátilo žádný listing.")
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
            "webhook_ready": bool(config.DISCORD_WEBHOOK_URL),
            "recent": self.store.recent_notified(),
            "monitors": monitors,
            "templates": self.store.list_templates(),
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
