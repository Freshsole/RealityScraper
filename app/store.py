from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.sreality import Listing, google_maps_url
from app.templates import default_template_config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS listings (
                    id INTEGER NOT NULL,
                    monitor_id TEXT NOT NULL DEFAULT 'default',
                    name TEXT NOT NULL,
                    price_czk INTEGER,
                    price_label TEXT,
                    disposition TEXT,
                    area_m2 INTEGER,
                    locality TEXT,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    first_seen TEXT NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    created_on TEXT,
                    edited_on TEXT,
                    views INTEGER,
                    old_price_czk INTEGER,
                    last_kind TEXT,
                    lat REAL,
                    lon REAL,
                    PRIMARY KEY (monitor_id, id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    listing_id INTEGER,
                    monitor_id TEXT,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    detail TEXT
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS templates (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    config TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    search_url TEXT NOT NULL,
                    webhook_url TEXT,
                    template_id TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    seeded INTEGER NOT NULL DEFAULT 0,
                    last_check TEXT,
                    last_error TEXT,
                    last_total INTEGER,
                    last_found INTEGER,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._migrate_listings(conn)
            self._migrate_events(conn)
            self._ensure_defaults(conn)

    def _migrate_listings(self, conn: sqlite3.Connection) -> None:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info(listings)")}
        pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(listings)") if row[5]]
        if "monitor_id" not in cols:
            conn.execute("ALTER TABLE listings ADD COLUMN monitor_id TEXT NOT NULL DEFAULT 'default'")
        extras = (
            ("created_on", "TEXT"),
            ("edited_on", "TEXT"),
            ("views", "INTEGER"),
            ("old_price_czk", "INTEGER"),
            ("last_kind", "TEXT"),
            ("lat", "REAL"),
            ("lon", "REAL"),
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        for column, decl in extras:
            if column not in existing:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {column} {decl}")
        if pk_cols == ["id"]:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS listings_v2 (
                    id INTEGER NOT NULL,
                    monitor_id TEXT NOT NULL DEFAULT 'default',
                    name TEXT NOT NULL,
                    price_czk INTEGER,
                    price_label TEXT,
                    disposition TEXT,
                    area_m2 INTEGER,
                    locality TEXT,
                    url TEXT NOT NULL,
                    image_url TEXT,
                    first_seen TEXT NOT NULL,
                    notified INTEGER NOT NULL DEFAULT 0,
                    created_on TEXT,
                    edited_on TEXT,
                    views INTEGER,
                    old_price_czk INTEGER,
                    last_kind TEXT,
                    lat REAL,
                    lon REAL,
                    PRIMARY KEY (monitor_id, id)
                );
                """
            )
            src = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
            select_cols = [
                "id",
                "COALESCE(monitor_id, 'default')",
                "name",
                "price_czk",
                "price_label",
                "disposition",
                "area_m2",
                "locality",
                "url",
                "image_url",
                "first_seen",
                "notified",
            ]
            optional = ["created_on", "edited_on", "views", "old_price_czk", "last_kind", "lat", "lon"]
            select_cols.extend(col if col in src else "NULL" for col in optional)
            conn.execute(
                f"INSERT OR IGNORE INTO listings_v2 SELECT {', '.join(select_cols)} FROM listings"
            )
            conn.execute("DROP TABLE listings")
            conn.execute("ALTER TABLE listings_v2 RENAME TO listings")

    def _migrate_events(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        if "monitor_id" not in cols:
            conn.execute("ALTER TABLE events ADD COLUMN monitor_id TEXT")
            conn.execute(
                """
                UPDATE events
                SET monitor_id = COALESCE(
                    (SELECT listings.monitor_id FROM listings WHERE listings.id = events.listing_id LIMIT 1),
                    'default'
                )
                WHERE monitor_id IS NULL
                """
            )

    def _ensure_defaults(self, conn: sqlite3.Connection) -> None:
        now = utc_now()
        if not conn.execute("SELECT id FROM templates LIMIT 1").fetchone():
            conn.execute(
                "INSERT INTO templates(id, name, config, created_at) VALUES (?, ?, ?, ?)",
                ("default", "Výchozí Discord zpráva", json.dumps(default_template_config(), ensure_ascii=False), now),
            )
        if not conn.execute("SELECT id FROM monitors LIMIT 1").fetchone():
            seeded = conn.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
            conn.execute(
                """
                INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    "default",
                    "Praha pronájmy",
                    config.SEARCH_URL,
                    config.DISCORD_WEBHOOK_URL,
                    "default",
                    1 if seeded and seeded["value"] == "1" else 0,
                    now,
                ),
            )

    def list_templates(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM templates ORDER BY created_at").fetchall()
        return [self._template_row(row) for row in rows]

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        return self._template_row(row) if row else None

    def save_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_id = (payload.get("id") or "").strip()
        template_id = raw_id if raw_id and raw_id != "__new__" else uuid.uuid4().hex[:10]
        name = (payload.get("name") or "Discord šablona").strip()
        cfg = payload.get("config") or default_template_config()
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO templates(id, name, config, created_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name, config = excluded.config
                """,
                (template_id, name, json.dumps(cfg, ensure_ascii=False), now),
            )
        saved = self.get_template(template_id)
        assert saved
        return saved

    def delete_template(self, template_id: str) -> None:
        if template_id == "default":
            raise ValueError("Výchozí šablonu nelze smazat")
        with self.connect() as conn:
            used = conn.execute("SELECT id FROM monitors WHERE template_id = ?", (template_id,)).fetchone()
            if used:
                raise ValueError("Šablona je přiřazená k monitoru")
            conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))

    def list_monitors(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM monitors ORDER BY created_at").fetchall()
        return [self._monitor_row(row) for row in rows]

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,)).fetchone()
        return self._monitor_row(row) if row else None

    def save_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        monitor_id = payload.get("id") or uuid.uuid4().hex[:10]
        existing = self.get_monitor(monitor_id)
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    search_url = excluded.search_url,
                    webhook_url = excluded.webhook_url,
                    template_id = excluded.template_id,
                    enabled = excluded.enabled
                """,
                (
                    monitor_id,
                    (payload.get("name") or "Monitor").strip(),
                    (payload.get("search_url") or "").strip(),
                    (payload.get("webhook_url") or "").strip(),
                    payload.get("template_id") or "default",
                    1 if payload.get("enabled", True) else 0,
                    now,
                ),
            )
        if existing and existing.get("search_url") != (payload.get("search_url") or "").strip():
            self.set_monitor_seeded(monitor_id, False)
        saved = self.get_monitor(monitor_id)
        assert saved
        return saved

    def delete_monitor(self, monitor_id: str) -> None:
        with self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM monitors").fetchone()[0]
            if count <= 1:
                raise ValueError("Poslední monitor nelze smazat")
            conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
            conn.execute("DELETE FROM listings WHERE monitor_id = ?", (monitor_id,))
            conn.execute("DELETE FROM events WHERE monitor_id = ?", (monitor_id,))

    def set_monitor_seeded(self, monitor_id: str, seeded: bool) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE monitors SET seeded = ? WHERE id = ?", (1 if seeded else 0, monitor_id))

    def update_monitor_stats(self, monitor_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE monitors SET {assignments} WHERE id = ?",
                (*fields.values(), monitor_id),
            )

    def get_meta(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def get_many(self, monitor_id: str, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM listings WHERE monitor_id = ? AND id IN ({placeholders})",
                (monitor_id, *ids),
            ).fetchall()
        return {int(row["id"]): dict(row) for row in rows}

    def count(self, monitor_id: str | None = None) -> int:
        with self.connect() as conn:
            if monitor_id:
                return int(
                    conn.execute("SELECT COUNT(*) FROM listings WHERE monitor_id = ?", (monitor_id,)).fetchone()[0]
                )
            return int(conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0])

    def new_today_count(self, monitor_id: str | None = None) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        sql = "SELECT COUNT(*) FROM listings WHERE notified = 1 AND first_seen LIKE ?"
        params: list[Any] = [f"{today}%"]
        if monitor_id:
            sql += " AND monitor_id = ?"
            params.append(monitor_id)
        with self.connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def upsert_seen(self, monitor_id: str, listing: Listing, notified: bool, kind: str | None = None) -> None:
        now = utc_now()
        if kind:
            event_kind = kind
        elif notified:
            event_kind = listing.kind or "new"
        else:
            event_kind = "seeded"
        change_text = "; ".join(f"{label}: {before} → {after}" for label, before, after in listing.changes)
        detail = change_text or listing.name
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO listings (
                    id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                    locality, url, image_url, first_seen, notified,
                    created_on, edited_on, views, old_price_czk, last_kind, lat, lon
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(monitor_id, id) DO UPDATE SET
                    name = excluded.name,
                    price_czk = excluded.price_czk,
                    price_label = excluded.price_label,
                    disposition = excluded.disposition,
                    area_m2 = excluded.area_m2,
                    locality = excluded.locality,
                    url = excluded.url,
                    image_url = excluded.image_url,
                    created_on = COALESCE(excluded.created_on, listings.created_on),
                    edited_on = COALESCE(excluded.edited_on, listings.edited_on),
                    views = COALESCE(excluded.views, listings.views),
                    old_price_czk = excluded.old_price_czk,
                    last_kind = excluded.last_kind,
                    lat = COALESCE(excluded.lat, listings.lat),
                    lon = COALESCE(excluded.lon, listings.lon),
                    notified = CASE WHEN excluded.notified = 1 THEN 1 ELSE listings.notified END
                """,
                (
                    listing.id,
                    monitor_id,
                    listing.name,
                    listing.price_czk,
                    listing.price_label,
                    listing.disposition,
                    listing.area_m2,
                    listing.locality,
                    listing.url,
                    listing.image_url,
                    now,
                    1 if notified else 0,
                    listing.created_on,
                    listing.edited_on,
                    listing.views,
                    listing.old_price_czk,
                    event_kind,
                    listing.lat,
                    listing.lon,
                ),
            )
            conn.execute(
                "INSERT INTO events(listing_id, monitor_id, kind, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (listing.id, monitor_id, event_kind, now, detail),
            )

    def recent_notified(self, limit: int = 12, monitor_id: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT listings.*, monitors.name AS monitor_name
            FROM listings
            LEFT JOIN monitors ON monitors.id = listings.monitor_id
            WHERE listings.notified = 1
        """
        params: list[Any] = []
        if monitor_id:
            sql += " AND listings.monitor_id = ?"
            params.append(monitor_id)
        sql += " ORDER BY listings.first_seen DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [enrich_maps(dict(row)) for row in rows]

    def missing_coords(self, notified_only: bool = True) -> list[dict[str, Any]]:
        clause = "WHERE lat IS NULL AND url IS NOT NULL"
        if notified_only:
            clause += " AND notified = 1"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT monitor_id, id, name, price_czk, price_label, disposition, area_m2, locality, url, image_url FROM listings {clause} LIMIT 40"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_location(self, listing: Listing, monitor_id: str | None = None) -> None:
        if listing.lat is None or listing.lon is None:
            return
        with self.connect() as conn:
            if monitor_id:
                conn.execute(
                    "UPDATE listings SET lat = ?, lon = ? WHERE monitor_id = ? AND id = ?",
                    (listing.lat, listing.lon, monitor_id, listing.id),
                )
            else:
                conn.execute(
                    "UPDATE listings SET lat = ?, lon = ? WHERE id = ?",
                    (listing.lat, listing.lon, listing.id),
                )

    def _template_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["config"] = json.loads(data["config"])
        return data

    def _monitor_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["seeded"] = bool(data["seeded"])
        data["tracked"] = self.count(data["id"])
        data["new_today"] = self.new_today_count(data["id"])
        return data


def enrich_maps(row: dict[str, Any]) -> dict[str, Any]:
    row["maps_url"] = google_maps_url(row.get("lat"), row.get("lon"), row.get("locality") or "")
    return row
