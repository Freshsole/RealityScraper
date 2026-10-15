from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import traceback

from app import config
from app import container
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
        self._heartbeat_task: asyncio.Task[None] | None = None
        self.owner_id: str | None = None
        self._checkpoint_task: asyncio.Task[None] | None = None
        self._ssr_task: asyncio.Task[None] | None = None
        self._stall_task: asyncio.Task[None] | None = None

    # Grace period after (re)deploy: keep the worker idle while Coolify
    # healthchecks the new container (60s start + 3x30s attempts). Scraping
    # is CPU-heavy and used to saturate the container during the healthcheck
    # window, which got deployments rolled back. Separate containers do not
    # share CPU, so docker-compose.yml shortens this via WORKER_BOOT_GRACE_SEC.
    BOOT_GRACE_SEC = max(0, int(os.getenv("WORKER_BOOT_GRACE_SEC", "180")))

    async def run(self) -> None:
        self.running = True
        self._heartbeat_task = asyncio.create_task(container.heartbeat_loop(), name="worker-heartbeat")
        print(
            f"scrape_worker start concurrency={config.SCRAPE_CONCURRENCY} "
            f"overrides={config.SCRAPE_CONCURRENCY_OVERRIDES or '{}'} "
            f"recent_pages={config.SCRAPE_RECENT_PAGES}",
            flush=True,
        )
        print(
            f"scrape_worker boot grace {self.BOOT_GRACE_SEC}s "
            "(deploy healthcheck window)",
            flush=True,
        )
        await asyncio.sleep(self.BOOT_GRACE_SEC)
        await self._rescan_users()
        self._rescan_task = asyncio.create_task(self._rescan_loop(), name="worker-rescan-users")
        self._watch_task = asyncio.create_task(self._watchdog_loop(), name="worker-watchdog")
        self._checkpoint_task = asyncio.create_task(self._checkpoint_loop(), name="worker-wal-checkpoint")
        self._ssr_task = asyncio.create_task(self._ssr_rebuild_loop(), name="worker-ssr-rebuild")
        self._stall_task = asyncio.create_task(self._stall_dump_loop(), name="worker-stall-dump")
        # Ping/discord stay on web process so notifications dequeue once.
        try:
            while self.running:
                try:
                    pass_ok = pass_fd_errors = 0
                    for user_id, user_hub in list(self.user_hubs.items()):
                        try:
                            await self._user_tick(user_hub)
                            pass_ok += 1
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            if "unable to open database file" in str(exc) or "Too many open files" in str(exc):
                                pass_fd_errors += 1
                            user_hub.last_error = f"scrape_worker: {exc}"
                            print(
                                f"scrape_worker tick error user={user_id}: {exc}\n{traceback.format_exc()}",
                                flush=True,
                            )
                    self._check_fd_exhaustion(pass_ok, pass_fd_errors)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.hub.last_error = f"scrape_worker: {exc}"
                    print(f"scrape_worker tick error: {exc}\n{traceback.format_exc()}", flush=True)
                await asyncio.sleep(10)
        finally:
            self.running = False
            try:
                from app import parse_pool

                parse_pool.close()
            except Exception:
                pass
            for task in (self._rescan_task, self._watch_task, self._heartbeat_task, self._checkpoint_task, self._ssr_task, self._stall_task):
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

    def _is_catalog_owner(self, user_id: str) -> bool:
        """True when this user's hub scrapes + maintains the catalog (everyone, unless SHARED_CATALOG)."""
        if not config.SHARED_CATALOG:
            return True
        return self.owner_id is None or user_id == self.owner_id

    def _owner_store(self) -> Any:
        hub = self.user_hubs.get(self.owner_id or "")
        return hub.store if hub is not None else None

    async def _spawn_user(self, user_id: str) -> None:
        if user_id in self.user_hubs:
            return
        # Building a Hub opens + migrates the user's SQLite store (seconds for a big one): never on the loop.
        user_hub = await asyncio.to_thread(Hub, store_path=user_registry.user_store_path(user_id), shared=self.hub)
        if user_id in self.user_hubs:  # spawned concurrently meanwhile
            await user_hub.close()
            return
        user_hub.running = True
        owner = self._is_catalog_owner(user_id)
        user_hub.catalog_owner = owner
        user_hub.catalog_owner_store_fn = self._owner_store
        tasks = [
            # Every user: their own monitors (search URLs, matching, notifications), sold pings, coords.
            asyncio.create_task(user_hub._loop(), name=f"worker-monitor-priority:{user_id}"),
            asyncio.create_task(user_hub._sold_loop(), name=f"worker-sold:{user_id}"),
            asyncio.create_task(user_hub.backfill_missing_coords(), name=f"worker-coords:{user_id}"),
        ]
        if owner:
            # Catalog maintenance runs once, on the owner's store (shared by everybody).
            tasks += [
                asyncio.create_task(user_hub._recent_catalog_loop(), name=f"worker-new-discovery:{user_id}"),
                asyncio.create_task(user_hub._deep_catalog_loop(), name=f"worker-rolling-deep:{user_id}"),
                asyncio.create_task(user_hub._dedupe_loop(), name=f"worker-dedupe:{user_id}"),
                asyncio.create_task(self._stale_sweep_loop(user_hub), name=f"worker-stale-sweep:{user_id}"),
            ]
        self.user_hubs[user_id] = user_hub
        self._user_tasks[user_id] = tasks
        print(
            f"scrape_worker: spawned {'catalog-owner' if owner else 'monitor-only'} pipelines for user {user_id}",
            flush=True,
        )

    async def _stop_user(self, user_id: str) -> None:
        for task in self._user_tasks.pop(user_id, []):
            task.cancel()
        user_hub = self.user_hubs.pop(user_id, None)
        if user_hub is not None:
            try:
                await user_hub.close()
            except Exception:
                pass

    async def _rescan_users(self) -> None:
        try:
            users = await asyncio.to_thread(user_registry.list_users, self.hub.store)
        except Exception as exc:
            print(f"scrape_worker: user rescan failed: {exc}", flush=True)
            return
        try:
            self.owner_id = await asyncio.to_thread(user_registry.catalog_owner_id, self.hub.store)
        except Exception:
            pass
        gate = asyncio.Semaphore(3)  # a few stores open in parallel; each open can take seconds

        async def ensure(user_id: str) -> None:
            existing = self.user_hubs.get(user_id)
            if existing is not None and existing.catalog_owner != self._is_catalog_owner(user_id):
                # The owner changed (e.g. a different admin): rebuild this user's pipelines.
                await self._stop_user(user_id)
            try:
                async with gate:
                    await self._spawn_user(user_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"scrape_worker: spawn failed for user {user_id}: {exc}", flush=True)

        ids = [str(user.get("id") or "") for user in users]
        # The owner first: it carries the catalog pipelines every consumer depends on.
        ids.sort(key=lambda uid: uid != self.owner_id)
        await asyncio.gather(*(ensure(uid) for uid in ids if uid))

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
        if user_hub.catalog_owner:
            await self._maybe_forced_catalog(user_hub)
            await user_hub.maybe_run_catalog_sync(force=False)
        await user_hub._flush_scrape_metrics()

    FD_EXHAUSTION_PASSES = 6

    def _check_fd_exhaustion(self, ok: int, fd_errors: int) -> None:
        """Out of file descriptors makes every DB open fail while the heartbeat stays green.
        After several fully failed passes exit so the container restart policy brings a clean worker."""
        if ok > 0 or fd_errors == 0:
            self._fd_failed_passes = 0
            return
        self._fd_failed_passes = getattr(self, "_fd_failed_passes", 0) + 1
        if self._fd_failed_passes >= self.FD_EXHAUSTION_PASSES:
            print(
                f"scrape_worker: {self._fd_failed_passes} passes without a usable DB handle "
                "(file descriptors exhausted?) - exiting for restart",
                flush=True,
            )
            os._exit(70)

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

    async def _ssr_rebuild_loop(self) -> None:
        """Rebuild the public SSR cache in a separate low-priority process (the web only reads it)."""
        from app import ssr_cache

        await asyncio.sleep(90)
        while self.running:
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "app.ssr_rebuild", "--if-stale", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
                )
                out, _ = await proc.communicate()
                tail = (out or b"").decode("utf-8", "replace").strip().splitlines()[-1:]
                print(f"scrape_worker: ssr rebuild exit={proc.returncode} {tail[0][:160] if tail else ''}", flush=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"scrape_worker: ssr rebuild error: {exc}", flush=True)
            await asyncio.sleep(ssr_cache.REBUILD_INTERVAL_SEC)

    async def _stall_dump_loop(self) -> None:
        """If the event loop is blocked for WORKER_STALL_DUMP_SEC, dump every thread's Python stack.

        faulthandler's watchdog is a C thread that needs no GIL, so it works exactly when a regex or
        JSON call is hogging the interpreter. The loop re-arms it every 0.5 s; a stall lets it fire.
        Output goes to stderr (the container log).
        """
        import faulthandler

        timeout = float(os.getenv("WORKER_STALL_DUMP_SEC", "2.0") or 0)
        if timeout <= 0:
            return
        from app import parse_pool

        ticks = 0
        try:
            while self.running:
                faulthandler.dump_traceback_later(timeout, repeat=False, file=sys.stderr)
                await asyncio.sleep(0.5)
                ticks += 1
                if ticks % 120 == 0:
                    from app import perf_diag

                    print(
                        f"scrape_worker: pools in_flight={perf_diag.snapshot().get('pool_in_flight')} "
                        f"hubs={len(self.user_hubs)} parse_pool procs={parse_pool.procs()} {parse_pool.stats}",
                        flush=True,
                    )
        finally:
            faulthandler.cancel_dump_traceback_later()

    async def _checkpoint_loop(self) -> None:
        """Keep the WAL small: with writers and readers always overlapping SQLite rarely resets it,
        which makes every read slower. Best effort, never blocks the scrape loops."""
        await asyncio.sleep(120)
        while self.running:
            try:
                stores = [self.hub.store] + [h.store for h in list(self.user_hubs.values())]

                def _run() -> None:
                    for store in stores:
                        try:
                            with store.connect() as conn:
                                conn.execute("PRAGMA busy_timeout=2000")
                                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                        except Exception:
                            pass

                await asyncio.to_thread(_run)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"scrape_worker: wal checkpoint error: {exc}", flush=True)
            await asyncio.sleep(600)

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


    def _request_stores(self, user_hub: Hub) -> list[Any]:
        """Stores an admin request may sit in: the owner's own store and the global one.

        The web process writes `catalog_sync_request` / `scrape_url_request` through its global hub
        (the global store), while the catalog pipelines run on the owner's per-user store, so the
        worker has to look in both or the admin buttons silently do nothing."""
        stores = [user_hub.store]
        global_store = self.hub.store
        if str(global_store.path) != str(user_hub.store.path):
            stores.append(global_store)
        return stores

    async def _maybe_forced_catalog(self, user_hub: Hub | None = None) -> None:
        user_hub = user_hub if user_hub is not None else self.hub
        wanted: set[str] = set()
        found_in: list[Any] = []
        for store in self._request_stores(user_hub):
            raw = await user_hub._job_db(store.get_meta, "catalog_sync_request")
            if not raw:
                continue
            found_in.append(store)
            try:
                payload = json.loads(str(raw))
            except json.JSONDecodeError:
                payload = None
            portals = [str(item) for item in payload if str(item).strip()] if isinstance(payload, list) else []
            wanted |= {str(item) for item in (portals or config.CATALOG_SYNC_HOURS) if item}
        if not found_in:
            return
        to_start = wanted - user_hub.catalog_running_portals
        leftover = sorted(wanted - to_start)
        if not to_start:
            print(
                f"catalog_sync request waiting: portals={sorted(wanted)} already running={sorted(user_hub.catalog_running_portals)}",
                flush=True,
            )
            return
        for store in found_in:
            await user_hub._job_db(
                store.set_meta,
                "catalog_sync_request",
                json.dumps(leftover) if leftover and store is user_hub.store else None,
            )
        user_hub.catalog_running_portals |= to_start
        user_hub.catalog_running = True
        print(
            f"catalog_sync request picked up portals={sorted(to_start)} "
            f"from={[str(s.path) for s in found_in]}",
            flush=True,
        )
        asyncio.create_task(
            user_hub.run_catalog_sync(portals=sorted(to_start), rerun=True, claimed=True),
            name="forced-catalog",
        )

    async def _maybe_scrape_url_request(self, user_hub: Hub | None = None) -> None:
        user_hub = user_hub if user_hub is not None else self.hub
        stores = self._request_stores(user_hub) if user_hub.catalog_owner else [user_hub.store]
        for store in stores:
            raw = await user_hub._job_db(store.get_meta, "scrape_url_request")
            if not raw:
                continue
            await user_hub._job_db(store.set_meta, "scrape_url_request", None)
            try:
                payload = json.loads(str(raw))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            url = str(payload.get("url") or "").strip()
            if not url:
                continue
            try:
                pages = int(payload.get("max_pages") or 40)
            except (TypeError, ValueError):
                pages = 40
            print(f"scrape_url request picked up url={url[:100]} pages={pages} from={store.path}", flush=True)
            await user_hub._run_scrape_search_url(url, pages)


async def _amain() -> None:
    worker = ScrapeWorker()
    run_task = asyncio.ensure_future(worker.run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            # Cancel run() so its finally block flushes metrics and closes hubs.
            loop.add_signal_handler(sig, run_task.cancel)
        except NotImplementedError:
            pass
    try:
        await run_task
    except asyncio.CancelledError:
        print("scrape_worker: shutdown complete", flush=True)


def main() -> None:
    from app.scrape_console import install_stdout_tee

    install_stdout_tee()
    # Web owns schema init on a cold volume; do not open the store until it is ready.
    container.wait_for_db(config.DB_PATH)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()