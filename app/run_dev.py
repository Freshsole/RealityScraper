"""Local reload supervisor so Ctrl+C always stops make dev."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

from app import config


def main() -> None:
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
        "--reload-exclude",
        "*.sqlite",
        "--reload-exclude",
        "*.sqlite-*",
        "--reload-delay",
        "0.4",
        "--timeout-graceful-shutdown",
        "4",
    ]
    proc = subprocess.Popen(cmd, start_new_session=True)

    def shutdown(_signum: int, _frame: object) -> None:
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except ProcessLookupError:
            pass
        deadline = time.time() + 1.5
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise SystemExit(130)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    raise SystemExit(proc.wait() or 0)


if __name__ == "__main__":
    main()
