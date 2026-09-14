from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from app import config
from app.main import app


def open_browser() -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://{config.HOST}:{config.PORT}/")


if __name__ == "__main__":
    print(f"Sreality monitor: http://{config.HOST}:{config.PORT}")
    print("Zavři toto okno, až chceš hlídání ukončit.")
    print(f"Data a .env: {config.ROOT}")
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info", timeout_graceful_shutdown=2)
