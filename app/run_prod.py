"""Production process supervisor (no pkg_resources / supervisord).

Mirrors supervisord.conf: uvicorn SCRAPE_ROLE=web + delayed scrape_worker.
Use when supervisord is unavailable: `python -m app.run_prod`
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app import config

ROOT = Path(config.ROOT)
WORKER_DELAY_S = 20


def _env(role: str) -> dict[str, str]:
    env = os.environ.copy()
    env["SCRAPE_ROLE"] = role
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _kill(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 15
    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def main() -> None:
    port = os.environ.get("PORT") or str(config.PORT)
    web_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    worker_cmd = [sys.executable, "-m", "app.scrape_worker"]

    web = subprocess.Popen(web_cmd, cwd=str(ROOT), env=_env("web"))
    time.sleep(WORKER_DELAY_S)
    worker = subprocess.Popen(worker_cmd, cwd=str(ROOT), env=_env("worker"))

    def shutdown(_signum: int = 0, _frame: object = None) -> None:
        _kill(worker)
        _kill(web)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while True:
            if web.poll() is not None:
                _kill(worker)
                raise SystemExit(web.returncode or 1)
            if worker.poll() is not None:
                print("scrape_worker exited, restarting", flush=True)
                worker = subprocess.Popen(worker_cmd, cwd=str(ROOT), env=_env("worker"))
            time.sleep(0.5)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
