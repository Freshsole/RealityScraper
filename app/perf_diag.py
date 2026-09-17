"""Temporary live-load diagnostics (Wave 3b). JSONL → perf_audit/live_diag.jsonl."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config

LOG_PATH = Path(config.ROOT) / "perf_audit" / "live_diag.jsonl"
ENABLED = os.getenv("PERF_DIAG", "0").strip().lower() not in {"0", "false", "no"}

_lock = threading.Lock()
_lag: deque[float] = deque(maxlen=200)
_req: deque[dict[str, Any]] = deque(maxlen=80)
_sql_slow: deque[dict[str, Any]] = deque(maxlen=80)
_locks: deque[dict[str, Any]] = deque(maxlen=80)
_pool: dict[str, int] = {}
_lock_n = 0
_lock_wait_ms = 0.0
_watch_max = 0.0
_started = 0.0


def _write(row: dict[str, Any]) -> None:
    if not ENABLED:
        return
    row.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    line = json.dumps(row, ensure_ascii=False, default=str)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    if row.get("kind") in {"lag", "req", "sql", "lock"}:
        print(f"PERF {row.get('kind')} {line}", flush=True)


def note_pool(name: str, delta: int) -> None:
    with _lock:
        _pool[name] = max(0, _pool.get(name, 0) + delta)


class CountedPool(ThreadPoolExecutor):
    def __init__(self, *args, pool_name: str = "default", **kwargs):
        super().__init__(*args, **kwargs)
        self._pool_name = pool_name

    def submit(self, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = self._pool_name
        note_pool(name, 1)

        def wrapped(*a, **k):
            try:
                return fn(*a, **k)
            finally:
                note_pool(name, -1)

        return super().submit(wrapped, *args, **kwargs)


def snapshot() -> dict[str, Any]:
    with _lock:
        lags = list(_lag)
        return {
            "uptime_s": round(time.monotonic() - _started, 1) if _started else 0,
            "watch_max_ms": round(_watch_max * 1000, 1),
            "watch_last_ms": round(lags[-1] * 1000, 1) if lags else 0,
            "watch_p95_ms": round(sorted(lags)[int(len(lags) * 0.95)] * 1000, 1) if lags else 0,
            "lock_n": _lock_n,
            "lock_wait_ms": round(_lock_wait_ms, 1),
            "pool_in_flight": dict(_pool),
            "req_tail": list(_req)[-12:],
            "sql_slow_tail": list(_sql_slow)[-8:],
            "lock_tail": list(_locks)[-8:],
            "threads": [
                {"name": t.name, "alive": t.is_alive(), "daemon": t.daemon}
                for t in threading.enumerate()
            ],
        }


def record_request(path: str, method: str, status: int, ms: float, *, thread: str = "") -> None:
    row = {"kind": "req", "path": path[:180], "method": method, "status": status, "ms": round(ms, 1), "thread": thread}
    with _lock:
        _req.append(row)
    if ms >= 80:
        _write(row)


def record_sql(ms: float, sql: str, err: str | None = None, *, locked: bool = False) -> None:
    global _lock_n, _lock_wait_ms
    snippet = " ".join((sql or "").split())[:220]
    row = {"kind": "lock" if locked else "sql", "ms": round(ms, 1), "sql": snippet, "err": err}
    with _lock:
        if locked:
            _lock_n += 1
            _lock_wait_ms += ms
            _locks.append(row)
        if ms >= 20 or locked:
            _sql_slow.append(row)
    if ms >= 50 or locked:
        _write(row)


def wrap_connection(conn: Any) -> Any:
    if not ENABLED or getattr(conn, "_perf_wrapped", False):
        return conn
    orig_ex = conn.execute
    orig_many = conn.executemany

    def execute(sql, *args, **kwargs):
        t0 = time.monotonic()
        err = None
        locked = False
        try:
            return orig_ex(sql, *args, **kwargs)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:240]
            low = str(exc).lower()
            locked = "locked" in low or "busy" in low
            raise
        finally:
            record_sql((time.monotonic() - t0) * 1000, str(sql), err, locked=locked)

    def executemany(sql, seq):
        t0 = time.monotonic()
        err = None
        locked = False
        try:
            return orig_many(sql, seq)
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:240]
            low = str(exc).lower()
            locked = "locked" in low or "busy" in low
            raise
        finally:
            record_sql((time.monotonic() - t0) * 1000, f"MANY {sql}", err, locked=locked)

    conn.execute = execute
    conn.executemany = executemany
    conn._perf_wrapped = True
    return conn


async def watchdog() -> None:
    global _watch_max, _started
    _started = time.monotonic()
    _write({"kind": "watch_start"})
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(0)
        spin = time.monotonic() - t0
        t1 = time.monotonic()
        await asyncio.sleep(0.1)
        lag = time.monotonic() - t1 - 0.1
        worst = max(spin, lag)
        with _lock:
            _lag.append(worst)
            if worst > _watch_max:
                _watch_max = worst
        if worst >= 0.05:
            _write(
                {
                    "kind": "lag",
                    "spin_ms": round(spin * 1000, 1),
                    "sleep_lag_ms": round(lag * 1000, 1),
                    "pool": dict(_pool),
                    "threads": threading.active_count(),
                }
            )


def install_default_executor() -> None:
    loop = asyncio.get_running_loop()
    workers = min(32, (os.cpu_count() or 4) + 4)
    loop.set_default_executor(CountedPool(max_workers=workers, thread_name_prefix="rf-to-thread", pool_name="to_thread"))
    _write({"kind": "executor", "to_thread_workers": workers, "cpu_count": os.cpu_count()})
