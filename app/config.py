from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from dotenv import dotenv_values


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


def _apply_dotenv(path: Path) -> None:
    """Fill os.environ from .env. Keep real process/Railway values; replace empty placeholders."""
    if not path.is_file():
        return
    for key, value in (dotenv_values(path) or {}).items():
        if value is None:
            continue
        current = os.environ.get(key)
        if current is None or not str(current).strip() or "..." in str(current):
            os.environ[key] = value


ROOT = app_root()
_apply_dotenv(ROOT / ".env")
_apply_dotenv(Path.cwd() / ".env")

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


def _api_key_env(name: str) -> str:
    """Ignore .env.example placeholders like sk_test_... so we never call Stripe with them."""
    raw = os.getenv(name, "").strip()
    if not raw or "..." in raw:
        return ""
    return raw


DISCORD_WEBHOOK_URL = _webhook_env("DISCORD_WEBHOOK_URL")
DISCORD_LEADS_WEBHOOK_URL = _webhook_env("DISCORD_LEADS_WEBHOOK_URL")
DISCORD_SIGNUPS_WEBHOOK_URL = _webhook_env("DISCORD_SIGNUPS_WEBHOOK_URL")
BEZREALITKY_WEBHOOK_URL = _webhook_env("BEZREALITKY_WEBHOOK_URL")
SOLD_WEBHOOK_URL = _webhook_env("SOLD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_SERVER_INVITE = os.getenv("DISCORD_SERVER_INVITE", "").strip()
EXTENSION_IDS = os.getenv("EXTENSION_IDS", "").strip()  # CSV povolených chrome-extension ID pro CORS
SEARCH_URL = os.getenv("SEARCH_URL", DEFAULT_SEARCH_URL).strip()
POLL_INTERVAL_SEC = max(20, int((os.getenv("POLL_INTERVAL_SEC") or "60")))
POLL_PAGES = max(1, int((os.getenv("POLL_PAGES") or "2")))
SCRAPE_MONITOR_LOOP_SEC = max(2, int((os.getenv("SCRAPE_MONITOR_LOOP_SEC") or "5")))
SCRAPE_DISCOVERY_LOOP_SEC = max(10, int((os.getenv("SCRAPE_DISCOVERY_LOOP_SEC") or "30")))
SCRAPE_DEEP_LOOP_SEC = max(1, int((os.getenv("SCRAPE_DEEP_LOOP_SEC") or "2")))
# all = single-process local; web/worker = supervisord split (Railway).
SCRAPE_ROLE = (os.getenv("SCRAPE_ROLE", "all") or "all").strip().lower()
if SCRAPE_ROLE not in {"all", "web", "worker"}:
    SCRAPE_ROLE = "all"
# The catalog (all portals) is scraped once by the catalog owner (the admin user's hub); other users'
# hubs only run their own monitors and read the shared catalog. SHARED_CATALOG=0 restores the old
# behaviour (every user scrapes and stores a private copy of the whole catalog).
# Parse scraped HTML/JSON in worker processes instead of GIL-bound threads (see app/parse_pool.py).
# Default: 3 processes in the scrape worker, 0 (threads) everywhere else.
SCRAPE_PARSE_PROCS = max(0, int((os.getenv("SCRAPE_PARSE_PROCS") or "3" if SCRAPE_ROLE == "worker" else "0") or 0))
SCRAPE_PARSE_TIMEOUT_SEC = max(5.0, float((os.getenv("SCRAPE_PARSE_TIMEOUT_SEC") or "60") or 60))
SHARED_CATALOG = (os.getenv("SHARED_CATALOG", "1") or "1").strip().lower() not in {"0", "false", "no", "off"}
SCRAPE_CONCURRENCY = max(4, min(64, int((os.getenv("SCRAPE_CONCURRENCY") or "16"))))
SCRAPE_CONCURRENCY_FLOOR = max(1, min(SCRAPE_CONCURRENCY, int((os.getenv("SCRAPE_CONCURRENCY_FLOOR") or "4"))))
# Hard cap across all portals. Per-portal ceilings cannot exceed this in aggregate.
SCRAPE_GLOBAL_CONCURRENCY = max(
    SCRAPE_CONCURRENCY,
    min(128, int((os.getenv("SCRAPE_GLOBAL_CONCURRENCY") or str(SCRAPE_CONCURRENCY + 8)))),
)
SCRAPE_FAST_TOUCH = os.getenv("SCRAPE_FAST_TOUCH", "1") != "0"
SCRAPE_DISCOVERY_EARLY_STOP = os.getenv("SCRAPE_DISCOVERY_EARLY_STOP", "1") != "0"
SCRAPE_DISCOVERY_FULL_EVERY = max(1, int((os.getenv("SCRAPE_DISCOVERY_FULL_EVERY") or "10")))
SCRAPE_RECENT_PAGES = max(1, min(10, int((os.getenv("SCRAPE_RECENT_PAGES") or "4"))))
SCRAPE_DISCOVERY_DEADLINE_SEC = max(15, int((os.getenv("SCRAPE_DISCOVERY_DEADLINE_SEC") or "50")))
SCRAPE_MONITOR_DEADLINE_SEC = max(20, int((os.getenv("SCRAPE_MONITOR_DEADLINE_SEC") or "55")))
SCRAPE_FULL_MARKET_DEADLINE_SEC = max(30, int((os.getenv("SCRAPE_FULL_MARKET_DEADLINE_SEC") or "70")))
SCRAPE_FULL_MARKET_URLS = max(10, int((os.getenv("SCRAPE_FULL_MARKET_URLS") or "80")))
# Rolling deep list crawl: cover ~all Sreality shards over time (not just newest 2k).
SCRAPE_DEEP_SHARDS_PER_TICK = max(2, min(40, int((os.getenv("SCRAPE_DEEP_SHARDS_PER_TICK") or "10"))))
SCRAPE_DEEP_PAGES = max(1, min(40, int((os.getenv("SCRAPE_DEEP_PAGES") or "25"))))
SCRAPE_DEEP_DEADLINE_SEC = max(15, int((os.getenv("SCRAPE_DEEP_DEADLINE_SEC") or "40")))
SCRAPE_BATCH_COMMIT = max(50, int((os.getenv("SCRAPE_BATCH_COMMIT") or "500")))
# 1 = refresh also writes listings/events/photos (legacy). 0 = catalog_listings + listing_links only.
REFRESH_WRITES_FULL_ROW = os.getenv("REFRESH_WRITES_FULL_ROW", "1").strip().lower() in {"1", "true", "yes"}
EVENTS_TTL_DAYS = max(7, int((os.getenv("EVENTS_TTL_DAYS") or "30")))
LISTING_PHOTOS_CAP = max(1, min(40, int((os.getenv("LISTING_PHOTOS_CAP") or "8"))))
FACETS_CACHE_SEC = max(30, int((os.getenv("FACETS_CACHE_SEC") or "600")))
PRUNE_APPLY = os.getenv("PRUNE_APPLY", "0").strip().lower() in {"1", "true", "yes"}
SCRAPE_DEFERRED_MAX_PER_SHARD = max(1, int((os.getenv("SCRAPE_DEFERRED_MAX_PER_SHARD") or "8")))
SCRAPE_ERROR_RATE_ALERT = max(0.01, min(1.0, float((os.getenv("SCRAPE_ERROR_RATE_ALERT") or "0.10"))))
# Per-page scrape_metrics_log + parse/fetch split. Off in production.
SCRAPE_METRICS_DETAIL = os.getenv("SCRAPE_METRICS_DETAIL", "0").strip().lower() in {"1", "true", "yes"}
# JSON dict of portal → concurrency ceiling, e.g. '{"sreality":24,"mmreality":4}'.
# Fragile HTML portals default to 1–2 so a 16-wide crawl does not 429 them.
_DEFAULT_CONCURRENCY_OVERRIDES = {
    "realitycz": 1,
    "ulovdomov": 1,
    "mmreality": 2,
    # idnes answers 429 + Retry-After: 3600 after ~2500 req / 20 min per IP.
    "idnes": 2,
}
_raw_scrape_overrides = (os.getenv("SCRAPE_CONCURRENCY_OVERRIDES", "") or "").strip()
try:
    _env_overrides = {
        str(key).strip().lower(): max(1, min(64, int(value)))
        for key, value in (json.loads(_raw_scrape_overrides) if _raw_scrape_overrides else {}).items()
    }
except (json.JSONDecodeError, TypeError, ValueError):
    _env_overrides = {}
SCRAPE_CONCURRENCY_OVERRIDES = {**_DEFAULT_CONCURRENCY_OVERRIDES, **_env_overrides}
# Split connect/read so they cannot stack into 50s+ zombie waits.
# Successful Sreality/Bazos fetches are typically <1s; 8s is ~10× that p95.
SCRAPE_HTTP_CONNECT_TIMEOUT = max(1.0, min(15.0, float((os.getenv("SCRAPE_HTTP_CONNECT_TIMEOUT") or "5"))))
SCRAPE_HTTP_TIMEOUT = max(2.0, min(60.0, float((os.getenv("SCRAPE_HTTP_TIMEOUT") or "15"))))
# List-crawl attempts per request (1 = no retry). 403/500 will not improve on retry.
SCRAPE_HTTP_RETRIES = max(1, min(5, int((os.getenv("SCRAPE_HTTP_RETRIES") or "3"))))
SCRAPE_PORTAL_FAILS_TO_DISABLE = max(2, min(30, int((os.getenv("SCRAPE_PORTAL_FAILS_TO_DISABLE") or "10"))))
SCRAPE_PORTAL_COOLDOWN_SEC = max(30, int((os.getenv("SCRAPE_PORTAL_COOLDOWN_SEC") or str(5 * 60))))
SCRAPE_PORTAL_COOLDOWN_CAP_SEC = max(SCRAPE_PORTAL_COOLDOWN_SEC, int((os.getenv("SCRAPE_PORTAL_COOLDOWN_CAP_SEC") or str(30 * 60))))


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
    "eurobydleni": _hour_env("CATALOG_SYNC_HOUR_EUROBYDLENI", 11),
    "realitymix": _hour_env("CATALOG_SYNC_HOUR_REALITYMIX", 12),
    "realingo": _hour_env("CATALOG_SYNC_HOUR_REALINGO", 13),
    "espolubydleni": _hour_env("CATALOG_SYNC_HOUR_ESPOLUBYDLENI", 14),
}
# Rychlý refresh nejnovějších nabídek - běží častěji než full catalog
# Hodiny (UTC) kdy se spouští "newest" refresh pro klíčové portály
CATALOG_NEWEST_HOURS = [int(h) for h in os.getenv("CATALOG_NEWEST_HOURS", "6,12,18").split(",") if h.strip().isdigit()]
CATALOG_NEWEST_PORTALS = [p.strip() for p in os.getenv("CATALOG_NEWEST_PORTALS", "sreality,bezrealitky,idnes,eurobydleni,realitymix").split(",") if p.strip()]
IDNES_WEBHOOK_URL = _webhook_env("IDNES_WEBHOOK_URL")
BAZOS_WEBHOOK_URL = _webhook_env("BAZOS_WEBHOOK_URL")
NEW_MAX_AGE_DAYS = max(1, int((os.getenv("NEW_MAX_AGE_DAYS") or "2")))
NOTIFY_REFRESHES = os.getenv("NOTIFY_REFRESHES", "0").strip() in {"1", "true", "yes"}
SOLD_INVENTORY_SEC = max(120, int((os.getenv("SOLD_INVENTORY_SEC") or "600")))
HOST = os.getenv("HOST") or ("0.0.0.0" if os.getenv("RAILWAY_ENVIRONMENT") else "127.0.0.1")
PORT = int((os.getenv("PORT") or "8080"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", f"http://{HOST}:{PORT}").strip().rstrip("/")
# Local development only (PUBLIC_BASE_URL points at localhost): the code 000000 confirms an e-mail without a mailbox.
DEV_VERIFY_CODE = (
    "000000" if any(h in PUBLIC_BASE_URL for h in ("://localhost", "://127.0.0.1", "://[::1]")) else ""
)
STRIPE_PUBLISHABLE_KEY = _api_key_env("STRIPE_PUBLISHABLE_KEY")
STRIPE_SECRET_KEY = _api_key_env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _api_key_env("STRIPE_WEBHOOK_SECRET")
UPDATE_FEED = os.getenv("UPDATE_FEED", "").strip()
UPDATE_TOKEN = os.getenv("UPDATE_TOKEN", "").strip()
AUTO_UPDATE = os.getenv("AUTO_UPDATE", "1").strip() in {"1", "true", "yes"}
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").replace("\\n", "\n").strip()
VAPID_MAILTO = os.getenv("VAPID_MAILTO", "mailto:ahoj@realitify.cz").strip() or "mailto:ahoj@realitify.cz"
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int((os.getenv("SMTP_PORT") or "587") or "587")
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip() or SMTP_USER
SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "1").strip() in {"1", "true", "yes"}
EMAIL_FROM_TRANSACTIONAL = os.getenv("EMAIL_FROM_TRANSACTIONAL", "").strip()
EMAIL_FROM_ALERTS = os.getenv("EMAIL_FROM_ALERTS", "").strip()
EMAIL_FROM_LIFECYCLE = os.getenv("EMAIL_FROM_LIFECYCLE", "").strip()
EMAIL_REPLY_TO = os.getenv("EMAIL_REPLY_TO", "podpora@realitify.cz").strip()
# Signs tracking / unsubscribe links. Empty = a random secret is generated and kept in the DB.
EMAIL_TOKEN_SECRET = os.getenv("EMAIL_TOKEN_SECRET", "").strip()
# Set to 0 to turn off the background e-mail queue + lifecycle scheduler.
# Finishes "autory odměníme ..." in the "found a home" follow-up (6.1b).
EMAIL_STORY_REWARD = os.getenv("EMAIL_STORY_REWARD", "poděkováním").strip() or "poděkováním"
EMAIL_AUTOMATION = os.getenv("EMAIL_AUTOMATION", "1").strip() in {"1", "true", "yes"}
# Temporary pause: only transactional e-mails (account, billing) leave; alerts, lifecycle and campaigns are skipped.
EMAIL_ONLY_TRANSACTIONAL = os.getenv("EMAIL_ONLY_TRANSACTIONAL", "0").strip().lower() in {"1", "true", "yes"}
# Alerts off, everything else (lifecycle, campaigns, transactional) keeps sending. Browser push is not affected.
EMAIL_ALERTS_PAUSED = os.getenv("EMAIL_ALERTS_PAUSED", "0").strip().lower() in {"1", "true", "yes"}
# Amazon SES (SMTP interface): optional configuration set + SNS webhook protection + send rate limits.
SES_CONFIGURATION_SET = os.getenv("SES_CONFIGURATION_SET", "").strip()
# Optional separate set for the transactional stream (falls back to SES_CONFIGURATION_SET).
SES_CONFIGURATION_SET_TRANSACTIONAL = os.getenv("SES_CONFIGURATION_SET_TRANSACTIONAL", "").strip()
SES_WEBHOOK_TOKEN = os.getenv("SES_WEBHOOK_TOKEN", "").strip()
SES_SNS_TOPIC_ARN = os.getenv("SES_SNS_TOPIC_ARN", "").strip()

# Google Form "měsíc PRO zdarma": odpovědi jdou do Google Sheets publikovaného jako CSV (Soubor > Sdílet > Publikovat na webu)
FREE_PRO_SHEET_CSV_URL = os.getenv("FREE_PRO_SHEET_CSV_URL", "").strip()
FREE_PRO_DISCORD_WEBHOOK_URL = _webhook_env("FREE_PRO_DISCORD_WEBHOOK_URL")
FREE_PRO_DAYS = int((os.getenv("FREE_PRO_DAYS") or "30") or 30)
FREE_PRO_POLL_SECONDS = int((os.getenv("FREE_PRO_POLL_SECONDS") or "300") or 300)


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, "") or default))
    except ValueError:
        return default


EMAIL_MAX_PER_SEC = _env_float("EMAIL_MAX_PER_SEC", 10.0)
EMAIL_MAX_PER_HOUR = int(_env_float("EMAIL_MAX_PER_HOUR", 0))
# Minimum seconds between two campaign e-mails (0 = off); other mail is not held back.
EMAIL_CAMPAIGN_GAP_SEC = _env_float("EMAIL_CAMPAIGN_GAP_SEC", 0.0)
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
    if os.getenv("REALITIFY_SKIP_LEGACY_MIGRATE"):
        return
    src = ROOT / "data"
    try:
        if not src.is_dir() or src.resolve() == dest.resolve():
            return
    except OSError:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "monitor.sqlite",
        "monitor.sqlite-wal",
        "monitor.sqlite-shm",
        "ssr_cache.sqlite",
        "ssr_cache.sqlite-wal",
        "ssr_cache.sqlite-shm",
        "vapid.json",
        "vapid-private.pem",
    ):
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

# Stale sweep only marks `gone` rows unseen for this long, so listings that a (partial) crawl
# missed but rolling-deep / discovery saw recently are not flagged sold.
CATALOG_STALE_GRACE_SEC = max(0, int((os.getenv("CATALOG_STALE_GRACE_SEC") or str(3 * 3600))))
# A shard counts as fully crawled at this share of the portal-reported total.
CATALOG_COMPLETE_RATIO = min(1.0, max(0.5, float((os.getenv("CATALOG_COMPLETE_RATIO") or "0.96"))))

# Monitors (monitor_priority) and new_discovery used to stop completely while any daily catalog sync ran.
# With ~14 portals synced one after another that is most of the day, so users saw no new listings.
# They now keep running alongside the sync (priority 0/1 at the limiters, one page per poll).
# SCRAPE_PAUSE_PRIORITY_DURING_CATALOG=1 restores the old behaviour without a redeploy of code.
SCRAPE_PAUSE_PRIORITY_DURING_CATALOG = os.getenv("SCRAPE_PAUSE_PRIORITY_DURING_CATALOG", "0").strip().lower() in {"1", "true", "yes", "on"}
# Many users watch the same portal search URL. Identical live polls issued within this window by
# different hubs of one process share a single fetch (0 disables sharing).
SCRAPE_LIVE_SHARE_TTL_SEC = max(0.0, float((os.getenv("SCRAPE_LIVE_SHARE_TTL_SEC") or "10") or 0))
# Discovery results whose write was deferred (catalog lock busy) are kept and retried, up to this many.
SCRAPE_DISCOVERY_BACKLOG_MAX = max(0, int((os.getenv("SCRAPE_DISCOVERY_BACKLOG_MAX") or "4000") or 0))
# How long one monitor tick waits for live polls before reporting; slower ones continue in the background.
SCRAPE_MONITOR_TICK_WAIT_SEC = max(1.0, float((os.getenv("SCRAPE_MONITOR_TICK_WAIT_SEC") or "20")))

# Cached portal clients per Hub (LRU). Each keeps one keep-alive socket open.
CLIENT_CACHE_MAX = max(8, int((os.getenv("CLIENT_CACHE_MAX") or "96")))

# Threads for every Hub's DB work in one process. In the worker ALL user hubs (+ the catalog owner's
# discovery/deep/sync) share one pool; with 4 threads a minute's ~100 live polls queued for 60-80 s.
JOB_POOL_WORKERS = max(2, int((os.getenv("JOB_POOL_WORKERS") or "16" if (os.getenv("SCRAPE_ROLE", "all") or "all").strip().lower() == "worker" else "4")))

# Monitor-poll fast touch: how long an "unchanged" verdict is trusted before the row is fully re-upserted.
SCRAPE_FAST_TOUCH_TTL_SEC = max(30, int((os.getenv("SCRAPE_FAST_TOUCH_TTL_SEC") or "900")))

# Max. registrací z jedné IP za hodinu (anti-abuse; sdílené IP jako mobilní sítě potřebují víc než 5)
REGISTER_LIMIT_PER_HOUR = int((os.getenv("REGISTER_LIMIT_PER_HOUR") or "20") or "20")
