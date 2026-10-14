"""Run CPU-bound HTML/JSON parsing in worker *processes*.

The parsers are pure-Python regex/JSON code. In a thread they still hold the GIL (a single regex
call cannot be interrupted), so a big page froze the scrape worker's event loop for seconds
(`scrape watchdog lag_ms=2000+`, mass "Monitor search timeout"). A process has its own GIL, so the
event loop keeps running while pages are parsed on other cores.

Usage: ``listings, total = await parse_pool.call(client._parse_blob, raw, page)``

`call` takes a module-level function or a *bound method of a client object*; for the latter the
object's plain attributes are shipped along and the method runs on a bare copy. It never makes a
page fail that would have parsed in-process: if the pool is disabled / broken / the call cannot be
pickled, it falls back to ``asyncio.to_thread``. The one exception is a timeout, which raises
``TimeoutError`` (re-parsing a pathological page in-process would freeze the loop again).

Enabled by ``SCRAPE_PARSE_PROCS`` (default 3 in the worker, 0 elsewhere).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import logging
import multiprocessing
import pickle
import threading
import time
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable

from app import config

log = logging.getLogger("realitify.parse_pool")

TIMEOUT_S = float(getattr(config, "SCRAPE_PARSE_TIMEOUT_SEC", 60.0))
COOLDOWN_S = 60.0
MAX_STATE_BYTES = 20_000

_lock = threading.Lock()
_pool: concurrent.futures.ProcessPoolExecutor | None = None
_disabled_until = 0.0
_ENVIRONMENT_ERRORS = (ImportError, AttributeError, TypeError, pickle.PickleError, multiprocessing.ProcessError)
stats = {"process": 0, "thread": 0, "fallback": 0, "timeout": 0, "broken": 0}


def procs() -> int:
    return max(0, int(getattr(config, "SCRAPE_PARSE_PROCS", 0) or 0))


def enabled() -> bool:
    return procs() > 0 and time.monotonic() >= _disabled_until


def _worker_init() -> None:
    # Import every parser once per process so the first page does not pay for it.
    for name in (
        "app.sreality",
        "app.html_listing",
        "app.idnes",
        "app.bazos",
        "app.ceskereality",
        "app.annonce",
        "app.mmreality",
        "app.remax",
        "app.realitycz",
        "app.realitymix",
        "app.ulovdomov",
    ):
        try:
            importlib.import_module(name)
        except Exception:
            pass


def _get_pool() -> concurrent.futures.ProcessPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            ctx = multiprocessing.get_context("spawn")  # fork inside a threaded asyncio process is unsafe
            _pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=procs(), mp_context=ctx, initializer=_worker_init, max_tasks_per_child=400
            )
        return _pool


def reset_pool(*, kill: bool = False) -> None:
    """Drop the pool (and optionally kill its processes, e.g. one stuck in a pathological regex)."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is None:
        return
    if kill:
        for proc in list(getattr(pool, "_processes", {}).values()):
            try:
                proc.terminate()
            except Exception:
                pass
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


def close() -> None:
    reset_pool(kill=True)


def _plain_state(obj: Any) -> dict[str, Any]:
    """The picklable, small attributes of a client object (never its HTTP client or caches)."""
    state: dict[str, Any] = {}
    for key, value in vars(obj).items():
        try:
            if len(pickle.dumps(value)) <= MAX_STATE_BYTES:
                state[key] = value
        except Exception:
            continue
    return state


def _spec(fn: Callable[..., Any]) -> tuple:
    if inspect.ismethod(fn):
        owner = fn.__self__
        cls = type(owner)
        return ("method", cls.__module__, cls.__qualname__, _plain_state(owner), fn.__name__)
    return ("func", fn.__module__, fn.__qualname__)


def _resolve(module: str, qualname: str) -> Any:
    target: Any = importlib.import_module(module)
    for part in qualname.split("."):
        target = getattr(target, part)
    return target


def _run(spec: tuple, args: tuple, kwargs: dict[str, Any]) -> Any:
    """Executes inside a pool process."""
    if spec[0] == "method":
        _, module, qualname, state, name = spec
        cls = _resolve(module, qualname)
        obj = cls.__new__(cls)
        obj.__dict__.update(state)
        return getattr(obj, name)(*args, **kwargs)
    _, module, qualname = spec
    return _resolve(module, qualname)(*args, **kwargs)


async def _in_thread(fn: Callable[..., Any], args: tuple, kwargs: dict[str, Any]) -> Any:
    stats["thread"] += 1
    return await asyncio.to_thread(fn, *args, **kwargs)


async def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run `fn(*args, **kwargs)` in a parse process (or a thread when the pool is unavailable)."""
    global _disabled_until
    if not enabled():
        return await _in_thread(fn, args, kwargs)
    try:
        spec = _spec(fn)
        pickle.dumps((spec, args, kwargs))
    except Exception:
        stats["fallback"] += 1
        return await _in_thread(fn, args, kwargs)
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(loop.run_in_executor(_get_pool(), _run, spec, args, kwargs), TIMEOUT_S)
        stats["process"] += 1
        return result
    except asyncio.TimeoutError:
        stats["timeout"] += 1
        log.warning("parse pool: %s took over %.0fs, killing the pool", getattr(fn, "__qualname__", fn), TIMEOUT_S)
        reset_pool(kill=True)
        raise TimeoutError(f"parsing took over {TIMEOUT_S:.0f}s") from None
    except (BrokenProcessPool, concurrent.futures.BrokenExecutor, OSError, pickle.PicklingError, EOFError) as exc:
        stats["broken"] += 1
        _disabled_until = time.monotonic() + COOLDOWN_S
        log.warning("parse pool unavailable (%s); parsing in-process for %.0fs", exc, COOLDOWN_S)
        reset_pool(kill=True)
        stats["fallback"] += 1
        return await _in_thread(fn, args, kwargs)
    except Exception as exc:
        if isinstance(exc, RuntimeError) and "shutdown" in str(exc):
            # The pool was reset by a concurrent timeout: this call just runs in-process.
            stats["fallback"] += 1
            return await _in_thread(fn, args, kwargs)
        if isinstance(exc, _ENVIRONMENT_ERRORS):
            # Looks like the child process could not run `fn` (missing module, unpicklable result...),
            # not a parse failure: redo it in-process, which then yields the real answer or error.
            stats["fallback"] += 1
            return await _in_thread(fn, args, kwargs)
        # The parser itself raised (e.g. ListingGone): the same exception the in-process path gives.
        raise


# --- helpers used only by tests (they must live in an importable module to run in a pool process) ---
def _debug_pid() -> int:
    import os

    return os.getpid()


def _debug_regex_burn(n: int = 27) -> int:
    """Catastrophic backtracking: holds the GIL in C for a long time (exponential in `n`)."""
    import re

    re.match(r"(a+)+$", "a" * n + "b")
    return n


def _debug_sleep(seconds: float) -> float:
    time.sleep(seconds)
    return seconds
