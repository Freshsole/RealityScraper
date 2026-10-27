"""Helpers for the split web / worker containers.

- wait_for_db: worker blocks (retry + backoff) until the web container has
  finished Store._init on the shared volume.
- heartbeat_loop / healthcheck: worker liveness via a file touched from the
  asyncio loop, so a blocked or wedged loop stops the heartbeat.

CLI: python -m app.container healthcheck web|worker (Docker HEALTHCHECK)
     python -m app.container wait-web (single-container fallback)
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

HEARTBEAT_PATH = Path(os.getenv("WORKER_HEARTBEAT_FILE", "/tmp/scrape_worker.heartbeat"))
HEARTBEAT_INTERVAL_S = 15
HEARTBEAT_MAX_AGE_S = 90

# Same file Store._init holds LOCK_EX on while it creates / migrates the schema.
STORE_INIT_LOCK = ".store_init.lock"


def db_ready(path: Path) -> bool:
    """True once the schema exists and no Store._init is running on this volume."""
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            conn.execute("SELECT 1 FROM meta LIMIT 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    try:
        import fcntl

        with open(path.parent / STORE_INIT_LOCK, "a+", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                return False
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    return True


def wait_for_db(
    path: Path,
    *,
    timeout_s: float = 600.0,
    first_delay_s: float = 0.5,
    max_delay_s: float = 10.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda msg: print(msg, flush=True),
) -> None:
    """Retry with exponential backoff until the DB is ready; raise on timeout.

    Raising makes the container exit so the restart policy retries it.
    """
    start = time.monotonic()
    delay = first_delay_s
    attempt = 0
    while True:
        attempt += 1
        if db_ready(path):
            if attempt > 1:
                log(f"db ready after {attempt} checks ({time.monotonic() - start:.1f}s): {path}")
            return
        waited = time.monotonic() - start
        if waited >= timeout_s:
            raise RuntimeError(f"database not ready after {waited:.0f}s: {path}")
        log(f"waiting for database {path} (attempt {attempt}, retry in {delay:.1f}s)")
        sleep(delay)
        delay = min(delay * 2, max_delay_s)


def touch_heartbeat() -> None:
    HEARTBEAT_PATH.write_text(str(time.time()), encoding="utf-8")


async def heartbeat_loop() -> None:
    while True:
        try:
            touch_heartbeat()
        except OSError:
            pass
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)


class LoopGuard:
    """Hard stall watchdog for the scrape worker.

    Docker never restarts an *unhealthy* container, and a blocked asyncio loop cannot fix itself (a single
    sync SQLite call froze the worker for 7 minutes while every portal looked "down"). A daemon thread
    watches a beat the loop writes every second; when it goes stale it dumps every thread's stack to
    stderr and exits, so the restart policy brings up a clean worker within seconds.
    """

    EXIT_CODE = 75

    def __init__(self, max_stall_s: float, *, exit_fn: Callable[[int], None] = os._exit) -> None:
        self.max_stall_s = float(max_stall_s)
        self._exit_fn = exit_fn
        self._beat = time.monotonic()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        self._beat = time.monotonic()

    def stalled_for(self) -> float:
        return time.monotonic() - self._beat

    def check(self) -> bool:
        """True (after dumping stacks and calling exit_fn) when the loop has been silent too long."""
        stalled = self.stalled_for()
        if stalled < self.max_stall_s:
            return False
        print(f"loop_guard: event loop stalled {stalled:.0f}s (limit {self.max_stall_s:.0f}s) - dumping stacks and exiting", flush=True)
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            pass
        self._exit_fn(self.EXIT_CODE)
        return True

    def start(self) -> None:
        if self.max_stall_s <= 0 or self._thread is not None:
            return
        self.beat()

        def _run() -> None:
            interval = min(5.0, max(0.5, self.max_stall_s / 4))
            while not self._stop.wait(interval):
                if self.check():
                    return

        self._thread = threading.Thread(target=_run, name="loop-guard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    async def beat_loop(self) -> None:
        while True:
            self.beat()
            await asyncio.sleep(1.0)


def worker_healthy() -> bool:
    try:
        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
    except OSError:
        return False
    return age <= HEARTBEAT_MAX_AGE_S


def web_healthy() -> bool:
    port = os.getenv("PORT") or "8000"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def wait_for_web(
    *,
    timeout_s: float = 300.0,
    first_delay_s: float = 0.5,
    max_delay_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Single-container fallback: hold the worker back until the web answers /health."""
    start = time.monotonic()
    delay = first_delay_s
    while not web_healthy():
        if time.monotonic() - start >= timeout_s:
            return False
        sleep(delay)
        delay = min(delay * 2, max_delay_s)
    return True


def main(argv: list[str]) -> int:
    if argv == ["wait-web"]:
        return 0 if wait_for_web() else 1
    if len(argv) == 2 and argv[0] == "healthcheck" and argv[1] in {"web", "worker"}:
        ok = web_healthy() if argv[1] == "web" else worker_healthy()
        return 0 if ok else 1
    print("usage: python -m app.container healthcheck web|worker", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
