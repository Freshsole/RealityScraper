from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app import config
from app.store import Store, utc_now
from app.version import current_version

ENV_KEYS = {
    "discord_webhook_url": "DISCORD_WEBHOOK_URL",
    "sold_webhook_url": "SOLD_WEBHOOK_URL",
    "search_url": "SEARCH_URL",
    "poll_interval_sec": "POLL_INTERVAL_SEC",
    "poll_pages": "POLL_PAGES",
    "update_feed": "UPDATE_FEED",
    "auto_update": "AUTO_UPDATE",
    "host": "HOST",
    "port": "PORT",
}


def current_settings() -> dict[str, Any]:
    return {
        "discord_webhook_url": config.DISCORD_WEBHOOK_URL,
        "sold_webhook_url": config.SOLD_WEBHOOK_URL,
        "search_url": config.SEARCH_URL,
        "poll_interval_sec": config.POLL_INTERVAL_SEC,
        "poll_pages": config.POLL_PAGES,
        "update_feed": config.UPDATE_FEED,
        "auto_update": config.AUTO_UPDATE,
        "host": config.HOST,
        "port": config.PORT,
    }


def export_config(store: Store) -> dict[str, Any]:
    with store.connect() as conn:
        monitors = [dict(row) for row in conn.execute("SELECT * FROM monitors ORDER BY created_at")]
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        try:
            listing_user = [dict(row) for row in conn.execute("SELECT * FROM listing_user")]
        except sqlite3.OperationalError:
            listing_user = []
    return {
        "kind": "sreality-monitor",
        "version": current_version(),
        "exported_at": utc_now(),
        "settings": current_settings(),
        "monitors": monitors,
        "templates": store.list_templates(),
        "meta": meta,
        "listing_user": listing_user,
    }


def export_sqlite(store: Store) -> bytes:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    dest = Path(tmp.name)
    try:
        with store.connect() as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
        return dest.read_bytes()
    finally:
        dest.unlink(missing_ok=True)


def export_pack(store: Store) -> bytes:
    payload = json.dumps(export_config(store), ensure_ascii=False, indent=2).encode("utf-8")
    database = export_sqlite(store)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("config.json", payload)
        archive.writestr("monitor.sqlite", database)
    return buffer.getvalue()


def apply_settings(settings: dict[str, Any] | None) -> None:
    if not settings:
        return
    if settings.get("discord_webhook_url"):
        config.DISCORD_WEBHOOK_URL = str(settings["discord_webhook_url"]).strip()
    if settings.get("sold_webhook_url"):
        config.SOLD_WEBHOOK_URL = str(settings["sold_webhook_url"]).strip()
    if settings.get("search_url"):
        config.SEARCH_URL = str(settings["search_url"]).strip()
    if settings.get("poll_interval_sec"):
        config.POLL_INTERVAL_SEC = max(20, int(settings["poll_interval_sec"]))
    if settings.get("poll_pages"):
        config.POLL_PAGES = max(1, int(settings["poll_pages"]))
    if "update_feed" in settings:
        config.UPDATE_FEED = str(settings.get("update_feed") or "").strip()
    if "auto_update" in settings:
        config.AUTO_UPDATE = bool(settings["auto_update"])
    _write_env(settings)


def _write_env(settings: dict[str, Any]) -> None:
    path = config.ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    order: list[str] = []
    comments: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            comments.append(line)
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        values[key] = value
        order.append(key)
    for json_key, env_key in ENV_KEYS.items():
        if json_key not in settings or settings[json_key] in (None, ""):
            continue
        raw = settings[json_key]
        if isinstance(raw, bool):
            values[env_key] = "1" if raw else "0"
        else:
            values[env_key] = str(raw)
        if env_key not in order:
            order.append(env_key)
    written = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        out.append(f"{key}={values.get(key, '')}")
        written.add(key)
    for key in order:
        if key not in written:
            out.append(f"{key}={values[key]}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def import_config(store: Store, payload: dict[str, Any], *, reset_seeded: bool) -> None:
    if payload.get("kind") not in {None, "sreality-monitor"}:
        raise ValueError("Soubor není export z Sreality monitoru")
    apply_settings(payload.get("settings") if isinstance(payload.get("settings"), dict) else None)
    now = utc_now()
    with store.connect() as conn:
        templates = payload.get("templates") or []
        if templates:
            conn.execute("DELETE FROM templates")
            for item in templates:
                cfg = item.get("config")
                if isinstance(cfg, dict):
                    cfg = json.dumps(cfg, ensure_ascii=False)
                conn.execute(
                    "INSERT INTO templates(id, name, config, created_at) VALUES (?, ?, ?, ?)",
                    (
                        item.get("id") or "default",
                        item.get("name") or "Discord šablona",
                        cfg or json.dumps({}),
                        item.get("created_at") or now,
                    ),
                )
        monitors = payload.get("monitors") or []
        if monitors:
            conn.execute("DELETE FROM monitors")
            for item in monitors:
                seeded = 0 if reset_seeded else (1 if item.get("seeded") else 0)
                conn.execute(
                    """
                    INSERT INTO monitors(
                        id, name, search_url, webhook_url, template_id, enabled, seeded,
                        last_check, last_error, last_total, last_found, interval_sec, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("id") or "default",
                        item.get("name") or "Monitor",
                        item.get("search_url") or "",
                        item.get("webhook_url") or "",
                        item.get("template_id") or "default",
                        1 if item.get("enabled", True) else 0,
                        seeded,
                        item.get("last_check"),
                        item.get("last_error"),
                        item.get("last_total"),
                        item.get("last_found"),
                        item.get("interval_sec"),
                        item.get("created_at") or now,
                    ),
                )
        users = payload.get("listing_user") or []
        if users:
            conn.execute("DELETE FROM listing_user")
            for item in users:
                if not item.get("url"):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO listing_user(url, status, note, updated_at) VALUES (?, ?, ?, ?)",
                    (item["url"], item.get("status") or "", item.get("note") or "", item.get("updated_at") or now),
                )


def replace_sqlite(store: Store, data: bytes) -> Store:
    if data[:16] != b"SQLite format 3\x00":
        raise ValueError("Soubor není SQLite databáze")
    path = store.path
    path.write_bytes(data)
    return Store(path)
