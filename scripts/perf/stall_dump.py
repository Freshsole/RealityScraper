"""Capture the exact Python frame holding the event-loop thread.

A sidecar thread samples sys._current_frames() every second. Idle kqueue/select
stacks are kept only in a ring; a busy main-thread stack is written immediately.
When the same top frame repeats for 3s, the sample is marked as a freeze window.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

# Current frame is waiting in the selector — loop is idle, not frozen in a callback.
IDLE_TOP = ("kqueue", "select.epoll", "select.poll", "select.select", "_select", "kevent")

_stop = threading.Event()
_thread: threading.Thread | None = None
_out: Path | None = None
_ring: deque[dict[str, Any]] = deque(maxlen=90)
_freeze_n = 0


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _stack_lines(frame: Any, limit: int = 16) -> list[str]:
    frames: list[Any] = []
    cur = frame
    while cur is not None:
        frames.append(cur)
        cur = cur.f_back
    frames.reverse()
    lines = []
    for item in frames[-limit:]:
        code = item.f_code
        lines.append(f"{code.co_filename}:{item.f_lineno} {code.co_name}")
    return lines


def _is_idle(lines: list[str]) -> bool:
    if not lines:
        return True
    top = lines[-1]
    return any(marker in top for marker in IDLE_TOP)


def _threads() -> dict[str, list[str]]:
    names = {thread.ident: thread.name for thread in threading.enumerate() if thread.ident}
    out: dict[str, list[str]] = {}
    for ident, frame in sys._current_frames().items():
        label = names.get(ident, f"tid-{ident}")
        out[label] = _stack_lines(frame)
    return out


def _write_sample(fh: TextIO, sample: dict[str, Any], *, freeze: bool) -> None:
    tag = "FREEZE" if freeze else "busy"
    fh.write(f"\n===== {tag} at={sample['at']} pid={os.getpid()} =====\n")
    for name, lines in sample["threads"].items():
        fh.write(f"-- {name} --\n")
        fh.write("\n".join(lines))
        fh.write("\n")
    fh.flush()


def _app_top(threads: dict[str, list[str]]) -> str:
    for name, lines in threads.items():
        if name in {"stall-dump", "MainThread"}:
            continue
        for line in reversed(lines):
            if "/app/" in line or "/scripts/" in line:
                return f"{name}:{line}"
    return ""


def _run(path: Path, interval: float, main_ident: int) -> None:
    del main_ident
    path.parent.mkdir(parents=True, exist_ok=True)
    last_main = ""
    last_worker = ""
    main_streak = 0
    worker_streak = 0
    with path.open("w", encoding="utf-8") as fh:
        fh.write(f"stall dump started at={_iso()} pid={os.getpid()} interval={interval}s\n")
        fh.flush()
        while not _stop.wait(interval):
            threads = _threads()
            main_name = next((name for name in threads if name == "MainThread"), "MainThread")
            main_lines = threads.get(main_name) or threads.get(next(iter(threads), ""), [])
            sample = {"at": _iso(), "threads": threads, "main": main_lines}
            _ring.append(sample)
            idle = _is_idle(main_lines)
            top = main_lines[-1] if main_lines else ""
            worker = _app_top(threads)
            tops = " | ".join(
                f"{name}={(lines[-1] if lines else '')[-80:]}" for name, lines in sorted(threads.items())
            )
            fh.write(f"tick at={sample['at']} idle={int(idle)} {tops}\n")
            if idle:
                main_streak = 0
                last_main = ""
            else:
                main_streak = main_streak + 1 if top == last_main else 1
                last_main = top
            if worker:
                worker_streak = worker_streak + 1 if worker == last_worker else 1
                last_worker = worker
            else:
                worker_streak = 0
                last_worker = ""
            freeze = (not idle and main_streak >= 3) or worker_streak >= 3
            if freeze or not idle:
                if freeze:
                    global _freeze_n
                    _freeze_n += 1
                _write_sample(fh, sample, freeze=freeze)
                print(
                    f"stall-sample at={sample['at']} freeze={int(freeze)} "
                    f"main_streak={main_streak} worker_streak={worker_streak} "
                    f"top={(worker or top)[-140:]}",
                    flush=True,
                )
            fh.flush()


def start(out_dir: Path, interval: float = 1.0) -> Path:
    global _thread, _out
    stop()
    _stop.clear()
    out_dir.mkdir(parents=True, exist_ok=True)
    _out = out_dir / "stacks.txt"
    main_ident = threading.main_thread().ident or 0
    _thread = threading.Thread(
        target=_run,
        args=(_out, interval, main_ident),
        name="stall-dump",
        daemon=True,
    )
    _thread.start()
    return _out


def stop() -> None:
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=2.0)
        _thread = None


def freeze_count() -> int:
    return _freeze_n


def enable_asyncio_debug(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(formatter)
    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(logging.WARNING)
    stream.setFormatter(logging.Formatter("asyncio-slow %(message)s"))
    logger = logging.getLogger("asyncio")
    logger.setLevel(logging.WARNING)
    logger.addHandler(file_handler)
    logger.addHandler(stream)
    logger.propagate = False
    loop = __import__("asyncio").get_running_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.1
    print(
        f"asyncio debug on slow_callback_duration={loop.slow_callback_duration}s log={log_path}",
        flush=True,
    )
