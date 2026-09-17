"""Local reload supervisor so Ctrl+C always stops make dev.

Default: two processes — uvicorn (SCRAPE_ROLE=web) + scrape_worker.
Scrape CPU/GIL must not sit on the HTTP event loop or document TTFB
jumps to tens of seconds. Escape hatch: SCRAPE_ROLE=all make dev.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app import config

PID_DIR = Path(config.ROOT) / ".run"
WORKER_PID = PID_DIR / "scrape_worker.pid"


def _env(role: str) -> dict[str, str]:
    env = os.environ.copy()
    env["SCRAPE_ROLE"] = role
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _killpg(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        return
    deadline = time.time() + 1.5
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def _spawn_web() -> subprocess.Popen[bytes]:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        config.HOST,
        "--port",
        str(config.PORT),
        "--reload",
        "--reload-exclude",
        "tests/*",
        "--reload-exclude",
        ".cursor/*",
        "--reload-exclude",
        "data/*",
        "--reload-exclude",
        "extension/*",
        "--timeout-graceful-shutdown",
        "15",
    ]
    role = "all" if (os.environ.get("SCRAPE_ROLE") or "").strip().lower() == "all" else "web"
    return subprocess.Popen(cmd, start_new_session=True, env=_env(role))


def _spawn_worker() -> subprocess.Popen[bytes]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.scrape_worker"],
        start_new_session=True,
        env=_env("worker"),
    )
    _write_pid(WORKER_PID, proc.pid)
    print(f"scrape_worker pid={proc.pid} (SCRAPE_ROLE=worker)", flush=True)
    return proc


def main() -> None:
    split = (os.environ.get("SCRAPE_ROLE") or "web").strip().lower() != "all"
    web = _spawn_web()
    worker = _spawn_worker() if split else None

    def shutdown(_signum: int = 0, _frame: object = None) -> None:
        _killpg(web)
        _killpg(worker)
        try:
            WORKER_PID.unlink(missing_ok=True)
        except OSError:
            pass
        raise SystemExit(130)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        while True:
            code = web.poll()
            if code is not None:
                _killpg(worker)
                try:
                    WORKER_PID.unlink(missing_ok=True)
                except OSError:
                    pass
                raise SystemExit(code or 0)
            if worker is not None and worker.poll() is not None:
                print("scrape_worker exited, restarting", flush=True)
                worker = _spawn_worker()
            time.sleep(0.4)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
