from __future__ import annotations

import os
import shutil
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

def _webhook_env(name: str) -> str:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if "webhooks/id/token" in lowered or "/webhooks/id/" in lowered:
        return ""
    return raw


DISCORD_WEBHOOK_URL = _webhook_env("DISCORD_WEBHOOK_URL")
BEZREALITKY_WEBHOOK_URL = _webhook_env("BEZREALITKY_WEBHOOK_URL")
SOLD_WEBHOOK_URL = _webhook_env("SOLD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_SERVER_INVITE = os.getenv("DISCORD_SERVER_INVITE", "").strip()
SEARCH_URL = os.getenv("SEARCH_URL", DEFAULT_SEARCH_URL).strip()
POLL_INTERVAL_SEC = max(20, int(os.getenv("POLL_INTERVAL_SEC", "60")))
POLL_PAGES = max(1, int(os.getenv("POLL_PAGES", "2")))
SCRAPE_MONITOR_LOOP_SEC = max(2, int(os.getenv("SCRAPE_MONITOR_LOOP_SEC", "5")))
SCRAPE_DISCOVERY_LOOP_SEC = max(10, int(os.getenv("SCRAPE_DISCOVERY_LOOP_SEC", "60")))
SCRAPE_DEEP_LOOP_SEC = max(1, int(os.getenv("SCRAPE_DEEP_LOOP_SEC", "2")))
# all = single-process local; web/worker = supervisord split (Railway).
SCRAPE_ROLE = (os.getenv("SCRAPE_ROLE", "all") or "all").strip().lower()
if SCRAPE_ROLE not in {"all", "web", "worker"}:
    SCRAPE_ROLE = "all"


def scrape_owned_here() -> bool:
    """True when this process may run NewDiscovery / rolling deep / Ulov hydrate.

    InstantSiteASGI / SCRAPE_ROLE=web is API + HTML only. Worker and local `all`
    own the scrape engines. Used at runtime so tests can monkeypatch SCRAPE_ROLE.
    """
    return SCRAPE_ROLE != "web"


def ensure_worker_role() -> None:
    """scrape_worker entry: this process always owns the scrape engines."""
    global SCRAPE_ROLE
    os.environ["SCRAPE_ROLE"] = "worker"
    SCRAPE_ROLE = "worker"


SCRAPE_CONCURRENCY = max(4, min(64, int(os.getenv("SCRAPE_CONCURRENCY", "16"))))
SCRAPE_CONCURRENCY_FLOOR = max(1, min(SCRAPE_CONCURRENCY, int(os.getenv("SCRAPE_CONCURRENCY_FLOOR", "4"))))
SCRAPE_RECENT_PAGES = max(1, min(10, int(os.getenv("SCRAPE_RECENT_PAGES", "4"))))
SCRAPE_DISCOVERY_DEADLINE_SEC = max(15, int(os.getenv("SCRAPE_DISCOVERY_DEADLINE_SEC", "50")))
SCRAPE_MONITOR_DEADLINE_SEC = max(20, int(os.getenv("SCRAPE_MONITOR_DEADLINE_SEC", "55")))
SCRAPE_FULL_MARKET_DEADLINE_SEC = max(30, int(os.getenv("SCRAPE_FULL_MARKET_DEADLINE_SEC", "70")))
SCRAPE_FULL_MARKET_URLS = max(10, int(os.getenv("SCRAPE_FULL_MARKET_URLS", "80")))
# Rolling deep list crawl: cover ~all Sreality shards over time (not just newest 2k).
SCRAPE_DEEP_SHARDS_PER_TICK = max(2, min(40, int(os.getenv("SCRAPE_DEEP_SHARDS_PER_TICK", "10"))))
SCRAPE_DEEP_PAGES = max(1, min(40, int(os.getenv("SCRAPE_DEEP_PAGES", "20"))))
SCRAPE_DEEP_DEADLINE_SEC = max(15, int(os.getenv("SCRAPE_DEEP_DEADLINE_SEC", "40")))
SCRAPE_BATCH_COMMIT = max(50, int(os.getenv("SCRAPE_BATCH_COMMIT", "500")))
SCRAPE_DEFERRED_MAX_PER_SHARD = max(1, int(os.getenv("SCRAPE_DEFERRED_MAX_PER_SHARD", "8")))
SCRAPE_ERROR_RATE_ALERT = max(0.01, min(1.0, float(os.getenv("SCRAPE_ERROR_RATE_ALERT", "0.10"))))
SCRAPE_PAGE1_BUDGET_FRAC = max(0.4, min(0.95, float(os.getenv("SCRAPE_PAGE1_BUDGET_FRAC", "0.72"))))
SCRAPE_MIN_SHARD_SEC = max(0.0, min(20.0, float(os.getenv("SCRAPE_MIN_SHARD_SEC", "8"))))


def _flag_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# Page-1 of every ready shard before pages 2..N. Deep ticks wait while NewDiscovery is in-flight.
SCRAPE_PAGE1_ACROSS_SHARDS = _flag_env("SCRAPE_PAGE1_ACROSS_SHARDS", True)
SCRAPE_DEEP_YIELD_TO_DISCOVERY = _flag_env("SCRAPE_DEEP_YIELD_TO_DISCOVERY", True)


# Worker-only opt-in. InstantSiteASGI / SCRAPE_ROLE=web never launch a browser.
SCRAPE_BROWSER_FETCH = _flag_env("SCRAPE_BROWSER_FETCH")
SCRAPE_BROWSER_ON_HARD_CF = _flag_env("SCRAPE_BROWSER_ON_HARD_CF")
SCRAPE_BROWSER_TIMEOUT_SEC = max(2, min(25, int(os.getenv("SCRAPE_BROWSER_TIMEOUT_SEC", "12"))))
SCRAPE_BROWSER_PORTALS = frozenset(
    part.strip().lower()
    for part in (os.getenv("SCRAPE_BROWSER_PORTALS", "mmreality") or "mmreality").split(",")
    if part.strip()
)

# Worker-only opt-in HTTP(S) proxy for hard-CF list fetches (M&M).
# InstantSiteASGI / SCRAPE_ROLE=web never send traffic through this proxy.
# Generic HTTP_PROXY / HTTPS_PROXY are intentionally ignored here.
SCRAPE_HTTP_PROXY = (os.getenv("SCRAPE_HTTP_PROXY") or "").strip()
SCRAPE_HTTPS_PROXY = (os.getenv("SCRAPE_HTTPS_PROXY") or "").strip()
SCRAPE_PROXY_PORTALS = frozenset(
    part.strip().lower()
    for part in (os.getenv("SCRAPE_PROXY_PORTALS", "mmreality") or "mmreality").split(",")
    if part.strip()
)


# Worker-only UlovDomov detail hydrate (v2/offer/detail). Off on InstantSiteASGI / web.
SCRAPE_ULOV_HYDRATE = _flag_env("SCRAPE_ULOV_HYDRATE", True)
SCRAPE_ULOV_HYDRATE_BATCH = max(4, min(48, int(os.getenv("SCRAPE_ULOV_HYDRATE_BATCH", "32"))))
SCRAPE_ULOV_HYDRATE_CONCURRENCY = max(1, min(8, int(os.getenv("SCRAPE_ULOV_HYDRATE_CONCURRENCY", "6"))))
SCRAPE_ULOV_HYDRATE_DELAY_SEC = max(0.0, min(2.0, float(os.getenv("SCRAPE_ULOV_HYDRATE_DELAY_SEC", "0.12"))))
SCRAPE_ULOV_HYDRATE_DEADLINE_SEC = max(3, min(40, int(os.getenv("SCRAPE_ULOV_HYDRATE_DEADLINE_SEC", "18"))))
SCRAPE_ULOV_HYDRATE_LOOP_SEC = max(10, int(os.getenv("SCRAPE_ULOV_HYDRATE_LOOP_SEC", "45")))


def browser_fetch_allowed(portal: str) -> bool:
    """True only for scrape worker (or local all) when the opt-in flag is on."""
    if not SCRAPE_BROWSER_FETCH:
        return False
    if not scrape_owned_here():
        return False
    return (portal or "").strip().lower() in SCRAPE_BROWSER_PORTALS


def scrape_proxy_url() -> str:
    """Normalized worker proxy URL, or empty. Does not read HTTP_PROXY/HTTPS_PROXY."""
    raw = (SCRAPE_HTTPS_PROXY or SCRAPE_HTTP_PROXY or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    scheme = raw.split("://", 1)[0].lower()
    if scheme not in {"http", "https", "socks5", "socks4", "socks"}:
        return ""
    return raw


def scrape_proxy_allowed(portal: str) -> bool:
    """True only for scrape worker (or local all) when a proxy URL is set."""
    if not scrape_proxy_url():
        return False
    if not scrape_owned_here():
        return False
    return (portal or "").strip().lower() in SCRAPE_PROXY_PORTALS


def _hour_env(name: str, default: int) -> int:
    return max(0, min(23, int(os.getenv(name, str(default)))))


CATALOG_SYNC_HOUR = _hour_env("CATALOG_SYNC_HOUR", 3)
CATALOG_SYNC_HOURS = {
    "sreality": _hour_env("CATALOG_SYNC_HOUR_SREALITY", 1),
    "bezrealitky": _hour_env("CATALOG_SYNC_HOUR_BEZREALITKY", 2),
    "idnes": _hour_env("CATALOG_SYNC_HOUR_IDNES", 3),
    "bazos": _hour_env("CATALOG_SYNC_HOUR_BAZOS", 4),
    "ceskereality": _hour_env("CATALOG_SYNC_HOUR_CESKEREALITY", 5),
    "annonce": _hour_env("CATALOG_SYNC_HOUR_ANNONCE", 6),
    "mmreality": _hour_env("CATALOG_SYNC_HOUR_MMREALITY", 7),
    "ulovdomov": _hour_env("CATALOG_SYNC_HOUR_ULOVDOMOV", 8),
    "remax": _hour_env("CATALOG_SYNC_HOUR_REMAX", 9),
    "realitycz": _hour_env("CATALOG_SYNC_HOUR_REALITYCZ", 10),
}
IDNES_WEBHOOK_URL = _webhook_env("IDNES_WEBHOOK_URL")
BAZOS_WEBHOOK_URL = _webhook_env("BAZOS_WEBHOOK_URL")
NEW_MAX_AGE_DAYS = max(1, int(os.getenv("NEW_MAX_AGE_DAYS", "2")))
NOTIFY_REFRESHES = os.getenv("NOTIFY_REFRESHES", "0").strip() in {"1", "true", "yes"}
SOLD_INVENTORY_SEC = max(120, int(os.getenv("SOLD_INVENTORY_SEC", "600")))
HOST = os.getenv("HOST") or ("0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://{HOST}:{PORT}").strip().rstrip("/")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
UPDATE_FEED = os.getenv("UPDATE_FEED", "").strip()
UPDATE_TOKEN = os.getenv("UPDATE_TOKEN", "").strip()
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "1").strip() in {"1", "true", "yes"}
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").replace("\\n", "\n").strip()
VAPID_MAILTO = os.getenv("VAPID_MAILTO", "mailto:ahoj@realitify.cz").strip() or "mailto:ahoj@realitify.cz"
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or "587")
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip() or SMTP_USER
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "1").strip() in {"1", "true", "yes"}
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "").strip()
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
WHATSAPP_TEMPLATE = os.getenv("WHATSAPP_TEMPLATE", "").strip()
WHATSAPP_TEMPLATE_LANG = os.getenv("WHATSAPP_TEMPLATE_LANG", "cs").strip() or "cs"
WHATSAPP_BUSINESS_NUMBER = os.getenv("WHATSAPP_BUSINESS_NUMBER", "").strip()
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL", "admin@realitify.cz") or "admin@realitify.cz").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()


def _on_railway() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))


def _resolve_data_dir() -> Path:
    explicit = os.getenv("DATA_DIR", "").strip()
    if explicit:
        return Path(explicit)
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if mount:
        return Path(mount)
    if _on_railway():
        for candidate in (Path("/data"), Path("/mnt/data")):
            try:
                if candidate.is_dir() and candidate.is_mount():
                    return candidate
            except OSError:
                continue
    return ROOT / "data"


def _migrate_legacy_data(dest: Path) -> None:
    src = ROOT / "data"
    try:
        if not src.is_dir() or src.resolve() == dest.resolve():
            return
    except OSError:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("monitor.sqlite", "monitor.sqlite-wal", "monitor.sqlite-shm", "vapid.json", "vapid-private.pem"):
        from_path = src / name
        to_path = dest / name
        if from_path.exists() and not to_path.exists():
            shutil.copy2(from_path, to_path)


DATA_DIR = _resolve_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
_migrate_legacy_data(DATA_DIR)
DB_PATH = DATA_DIR / "monitor.sqlite"
ON_RAILWAY = _on_railway()
try:
    _data_is_mount = DATA_DIR.is_mount()
except OSError:
    _data_is_mount = False
PERSISTENT_STORAGE = (not ON_RAILWAY) or bool(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()) or _data_is_mount
WEB_DIR = resource_root() / "web"
