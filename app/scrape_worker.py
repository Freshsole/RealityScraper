from __future__ import annotations

import asyncio
import json
import os
import traceback

from app import config
from app import users as user_registry
from app.monitor import Hub


class ScrapeWorker:
    """One worker process, one Hub per user (SCRAPE_ROLE=worker).

    The primary hub owns the global store (users table) plus the shared
    rate-limiter registry and thread pools.  Every user gets a child hub
    (shared=primary) running the full scrape pipeline on their private
    store: monitor priority loop, new-discovery, rolling deep catalog,
    sold, coords, dedupe, stale sweep and the per-user tick.
    """

    RESCAN_INTERVAL_S = 60

    def __init__(self) -> None:
        self.hub = Hub()
        self.running = False
        self.user_hubs: dict[str, Hub] = {}
        self._user_tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._rescan_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        self.running = True
        print(
            f"scrape_worker start concurrency={config.SCRAPE_CONCURRENCY} "
            f"overrides={config.SCRAPE_CONCURRENCY_OVERRIDES or '{}'} "
            f"recent_pages={config.SCRAPE_RECENT_PAGES}",
            flush=True,
        )
        await self._rescan_users()
        self._rescan_task = asyncio.create_task(self._rescan_loop(), name="worker-rescan-users")
        self._watch_task = asyncio.create_task(self._watchdog_loop(), name="worker-watchdog")
        # Ping/discord stay on web process so notifications dequeue once.
        try:
            while self.running:
                try:
                    for user_id, user_hub in list(self.user_hubs.items()):
                        try:
                            await self._user_tick(user_hub)
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            user_hub.last_error = f"scrape_worker: {exc}"
                            print(
                                f"scrape_worker tick error user={user_id}: {exc}\n{traceback.format_exc()}",
                                flush=True,
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.hub.last_error = f"scrape_worker: {exc}"
                    print(f"scrape_worker tick error: {exc}\n{traceback.format_exc()}", flush=True)
                await asyncio.sleep(10)
        finally:
            self.running = False
            for task in (self._rescan_task, self._watch_task):
                if task is not None:
                    task.cancel()
            for tasks in self._user_tasks.values():
                for task in tasks:
                    task.cancel()
            try:
                for user_hub in list(self.user_hubs.values()):
                    try:
                        await user_hub._flush_scrape_metrics()
                    except Exception:
                        pass
            finally:
                for user_hub in list(self.user_hubs.values()):
                    try:
                        await user_hub.close()
                    except Exception:
                        pass
                try:
                    await self.hub.close()
                except Exception:
                    pass

    def _spawn_user(self, user_id: str) -> None:
        if user_id in self.user_hubs:
            return
        user_hub = Hub(store_path=user_registry.user_store_path(user_id), shared=self.hub)
        user_hub.running = True
        tasks = [
            asyncio.create_task(user_hub._loop(), name=f"worker-monitor-priority:{user_id}"),
            asyncio.create_task(user_hub._recent_catalog_loop(), name=f"worker-new-discovery:{user_id}"),
            asyncio.create_task(user_hub._deep_catalog_loop(), name=f"worker-rolling-deep:{user_id}"),
            # Own sold/coords/dedupe — web process does not.
            asyncio.create_task(user_hub._sold_loop(), name=f"worker-sold:{user_id}"),
            asyncio.create_task(user_hub.backfill_missing_coords(), name=f"worker-coords:{user_id}"),
            asyncio.create_task(user_hub._dedupe_loop(), name=f"worker-dedupe:{user_id}"),
            asyncio.create_task(self._stale_sweep_loop(user_hub), name=f"worker-stale-sweep:{user_id}"),
        ]
        self.user_hubs[user_id] = user_hub
        self._user_tasks[user_id] = tasks
        print(f"scrape_worker: spawned pipelines for user {user_id}", flush=True)

    async def _rescan_users(self) -> None:
        try:
            users = await asyncio.to_thread(user_registry.list_users, self.hub.store)
        except Exception as exc:
            print(f"scrape_worker: user rescan failed: {exc}", flush=True)
            return
        for user in users:
            user_id = str(user.get("id") or "")
            if user_id:
                try:
                    self._spawn_user(user_id)
                except Exception as exc:
                    print(f"scrape_worker: spawn failed for user {user_id}: {exc}", flush=True)

    async def _rescan_loop(self) -> None:
        while self.running:
            try:
                await asyncio.sleep(self.RESCAN_INTERVAL_S)
                if not self.running:
                    break
                await self._rescan_users()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"scrape_worker: rescan loop error: {exc}", flush=True)

    async def _user_tick(self, user_hub: Hub) -> None:
        await self._maybe_scrape_url_request(user_hub)
        await self._maybe_forced_catalog(user_hub)
        await user_hub.maybe_run_catalog_sync(force=False)
        await user_hub._flush_scrape_metrics()

    async def _stale_sweep_loop(self, user_hub: Hub) -> None:
        """Hourly independent staleness sweep — does not wait for portal sync completion."""
        await asyncio.sleep(45)
        while self.running:
            try:
                hours = int(os.getenv("CATALOG_STALE_HOURS", "72") or 72)
                n = await user_hub._job_db(user_hub.store.sweep_catalog_stale_gone, hours)
                if n:
                    print(f"catalog stale sweep gone={n} hours={hours}", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"catalog stale sweep error: {exc}", flush=True)
            await asyncio.sleep(3600)

    async def _watchdog_loop(self) -> None:
        from app.scrape_timing import watchdog_sample, watchdog_snapshot

        while self.running:
            try:
                lag = await watchdog_sample()
                if lag >= 0.5:
                    from app.scrape_console import emit

                    emit(f"scrape watchdog lag_ms={lag * 1000:.0f}")
                snap = watchdog_snapshot()
                if snap.get("n") and snap["n"] % 100 == 0:
                    await self.hub._job_db(
                        self.hub.store.set_meta, "scrape_watchdog", json.dumps(snap)
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(1)


    async def _maybe_forced_catalog(self, user_hub: Hub | None = None) -> None:
        user_hub = user_hub if user_hub is not None else self.hub
        raw = await user_hub._job_db(user_hub.store.get_meta, "catalog_sync_request")
        if not raw:
            return
        portals = None
        try:
            payload = json.loads(str(raw))
            if isinstance(payload, list):
                portals = [str(item) for item in payload if str(item).strip()]
        except json.JSONDecodeError:
            portals = None
        wanted = {str(item) for item in (portals or config.CATALOG_SYNC_HOURS) if item}
        to_start = wanted - user_hub.catalog_running_portals
        if not to_start:
            return
        leftover = sorted(wanted - to_start)
        await user_hub._job_db(
            user_hub.store.set_meta,
            "catalog_sync_request",
            json.dumps(leftover) if leftover else None,
        )
        user_hub.catalog_running_portals |= to_start
        user_hub.catalog_running = True
        asyncio.create_task(
            user_hub.run_catalog_sync(portals=sorted(to_start), rerun=True, claimed=True),
            name="forced-catalog",
        )

    async def _maybe_scrape_url_request(self, user_hub: Hub | None = None) -> None:
        user_hub = user_hub if user_hub is not None else self.hub
        raw = await user_hub._job_db(user_hub.store.get_meta, "scrape_url_request")
        if not raw:
            return
        await user_hub._job_db(user_hub.store.set_meta, "scrape_url_request", None)
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
        await user_hub._run_scrape_search_url(url, pages)


async def _amain() -> None:
    worker = ScrapeWorker()
    await worker.run()


def main() -> None:
    from app.scrape_console import install_stdout_tee

    install_stdout_tee()
    asyncio.run(_amain())


if __name__ == "__main__":
    main()