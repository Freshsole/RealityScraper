from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if _frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    if _frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


ROOT = app_root()
load_dotenv(ROOT / ".env")
load_dotenv(Path.cwd() / ".env", override=False)

DEFAULT_SEARCH_URL = (
    "https://www.sreality.cz/hledani/pronajem/byty/"
    "praha-1,praha-2,praha-3,praha-4,praha-6,praha-7,praha-8,praha-9"
    "?velikost=2%2B1%2C2%2Bkk%2C3%2B1%2C3%2Bkk%2C4%2B1%2C4%2Bkk%2C5%2B1%2C5%2Bkk"
    "&razeni=nejlevnejsi&cena-do=25759&plocha-od=45"
)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
BEZREALITKY_WEBHOOK_URL = os.getenv("BEZREALITKY_WEBHOOK_URL", "").strip()
SEARCH_URL = os.getenv("SEARCH_URL", DEFAULT_SEARCH_URL).strip()
POLL_INTERVAL_SEC = max(20, int(os.getenv("POLL_INTERVAL_SEC", "60")))
POLL_PAGES = max(1, int(os.getenv("POLL_PAGES", "2")))
NEW_MAX_AGE_DAYS = max(1, int(os.getenv("NEW_MAX_AGE_DAYS", "2")))
NOTIFY_REFRESHES = os.getenv("NOTIFY_REFRESHES", "0").strip() in {"1", "true", "yes"}
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
UPDATE_FEED = os.getenv("UPDATE_FEED", "").strip()
UPDATE_TOKEN = os.getenv("UPDATE_TOKEN", "").strip()
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "1").strip() in {"1", "true", "yes"}
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "monitor.sqlite"
WEB_DIR = resource_root() / "web"
