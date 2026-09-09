from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app import config
from app.bezrealitky import default_search_url as bezrealitky_default_url
from app.sreality import IMAGE_TRANSFORM, Listing, cdn_image_url, google_maps_url
from app.sources import webhook_for
from app.templates import default_template_config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_discord_webhook(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and "/api/webhooks/" in (parsed.path or "")
        and (host == "discord.com" or host == "discordapp.com" or host.endswith(".discord.com"))
    )


def _csv(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _or_likes(where: list[str], params: list[Any], column: str, patterns: list[str]) -> None:
    if not patterns:
        return
    where.append("(" + " OR ".join(f"{column} LIKE ?" for _ in patterns) + ")")
    params.extend(patterns)


def _tri_state(where: list[str], params: list[Any], value: str, clauses: list[str], clause_params: list[Any]) -> None:
    if value not in {"s", "bez"} or not clauses:
        return
    known = "(" + " OR ".join(clauses) + ")"
    where.append(known if value == "s" else f"NOT {known}")
    params.extend(clause_params)


def _apply_circle(where: list[str], params: list[Any], filters: dict[str, Any]) -> None:
    try:
        lat = float(filters.get("lat") or "")
        lon = float(filters.get("lon") or "")
        radius = float(filters.get("radius_m") or "")
    except (TypeError, ValueError):
        return
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 50 <= radius <= 50_000):
        return
    where.append("listings.lat IS NOT NULL AND listings.lon IS NOT NULL")
    where.append(
        """
        (6371000 * acos(max(-1.0, min(1.0,
            cos(? * 0.017453292519943295) * cos(listings.lat * 0.017453292519943295)
            * cos((listings.lon - ?) * 0.017453292519943295)
            + sin(? * 0.017453292519943295) * sin(listings.lat * 0.017453292519943295)
        )))) <= ?
        """
    )
    params.extend([lat, lon, lat, radius])


PRAGUE_DISTRICTS = {
    "1": ["Staré Město", "Josefov", "Malá Strana", "Hradčany", "Nové Město"],
    "2": ["Vinohrady", "Nové Město", "Vyšehrad", "Nusle"],
    "3": ["Žižkov", "Vinohrady"],
    "4": ["Nusle", "Podolí", "Braník", "Hodkovičky", "Krč", "Lhotka", "Kamýk", "Kunratice"],
    "5": ["Smíchov", "Košíře", "Motol", "Radlice", "Jinonice", "Hlubočepy"],
    "6": ["Dejvice", "Bubeneč", "Střešovice", "Břevnov", "Veleslavín", "Vokovice", "Liboc", "Ruzyně", "Lysolaje", "Sedlec", "Suchdol", "Nebušice"],
    "7": ["Holešovice", "Bubny", "Letná", "Troja"],
    "8": ["Karlín", "Libeň", "Bohnice", "Kobylisy", "Čimice", "Ďáblice", "Dolní Chabry", "Troja"],
    "9": ["Vysočany", "Prosek", "Střížkov", "Hloubětín", "Hrdlořezy", "Kbely"],
    "10": ["Vršovice", "Strašnice", "Malešice", "Záběhlice", "Michle"],
}


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
            self._ensure_catalog(conn)
            self._migrate_monitors(conn)
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
            ("description", "TEXT"),
            ("extras", "TEXT"),
            ("last_seen", "TEXT"),
            ("gone", "INTEGER"),
            ("sold_notified", "INTEGER"),
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        for column, decl in extras:
            if column not in existing:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {column} {decl}")
        if "sold_notified" not in existing:
            conn.execute("UPDATE listings SET sold_notified = 1 WHERE IFNULL(gone, 0) = 1")
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
        if not conn.execute("SELECT id FROM monitors WHERE id = 'default'").fetchone():
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
        existing_br = conn.execute("SELECT webhook_url FROM monitors WHERE id = 'bezrealitky'").fetchone()
        if not existing_br:
            conn.execute(
                """
                INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    "bezrealitky",
                    "Bezrealitky Praha",
                    bezrealitky_default_url(),
                    config.BEZREALITKY_WEBHOOK_URL,
                    "default",
                    now,
                ),
            )
        elif not (existing_br["webhook_url"] or "").strip() and config.BEZREALITKY_WEBHOOK_URL:
            conn.execute(
                "UPDATE monitors SET webhook_url = ? WHERE id = 'bezrealitky'",
                (config.BEZREALITKY_WEBHOOK_URL,),
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
        search_url = (payload.get("search_url") or "").strip()
        webhook = (payload.get("webhook_url") or "").strip() or webhook_for(search_url)
        interval = payload.get("interval_sec")
        try:
            interval_sec = max(20, int(interval)) if interval not in (None, "") else None
        except (TypeError, ValueError):
            interval_sec = None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, interval_sec, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    search_url = excluded.search_url,
                    webhook_url = excluded.webhook_url,
                    template_id = excluded.template_id,
                    enabled = excluded.enabled,
                    interval_sec = excluded.interval_sec
                """,
                (
                    monitor_id,
                    (payload.get("name") or "Monitor").strip(),
                    search_url,
                    webhook,
                    payload.get("template_id") or "default",
                    1 if payload.get("enabled", True) else 0,
                    interval_sec,
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

    def set_meta(self, key: str, value: str | None) -> None:
        with self.connect() as conn:
            if value is None:
                conn.execute("DELETE FROM meta WHERE key = ?", (key,))
                return
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def app_settings(self) -> dict[str, Any]:
        raw_points = self.get_meta("commute_points") or "[]"
        try:
            points = json.loads(raw_points)
        except json.JSONDecodeError:
            points = []
        if not isinstance(points, list):
            points = []
        try:
            hour = int(self.get_meta("digest_hour") or 8)
        except (TypeError, ValueError):
            hour = 8
        return {
            "digest_hour": max(0, min(23, hour)),
            "digest_webhook": (self.get_meta("digest_webhook") or "").strip(),
            "digest_last": self.get_meta("digest_last"),
            "commute_points": points[:2],
        }

    def digest_webhook(self) -> str:
        return (self.get_meta("digest_webhook") or "").strip() or config.DISCORD_WEBHOOK_URL

    def save_app_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "digest_hour" in payload:
            try:
                hour = max(0, min(23, int(payload.get("digest_hour"))))
            except (TypeError, ValueError):
                hour = 8
            self.set_meta("digest_hour", str(hour))
        if "digest_webhook" in payload:
            url = str(payload.get("digest_webhook") or "").strip()
            if url and not _is_discord_webhook(url):
                raise ValueError("Webhook musí být Discord URL")
            self.set_meta("digest_webhook", url)
        if "commute_points" in payload:
            points = []
            for raw in payload.get("commute_points") or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    lat = float(raw.get("lat"))
                    lon = float(raw.get("lon"))
                except (TypeError, ValueError):
                    continue
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                points.append(
                    {
                        "id": str(raw.get("id") or uuid.uuid4().hex[:6]),
                        "name": (raw.get("name") or "Bod").strip() or "Bod",
                        "address": (raw.get("address") or "").strip(),
                        "lat": lat,
                        "lon": lon,
                    }
                )
                if len(points) >= 2:
                    break
            self.set_meta("commute_points", json.dumps(points, ensure_ascii=False))
        return self.app_settings()

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
                    created_on, edited_on, views, old_price_czk, last_kind, lat, lon,
                    description, extras, last_seen, gone
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
                    description = COALESCE(excluded.description, listings.description),
                    extras = CASE
                        WHEN excluded.extras IS NOT NULL AND excluded.extras != '' AND excluded.extras != '{}'
                        THEN excluded.extras ELSE listings.extras
                    END,
                    last_seen = excluded.last_seen,
                    gone = 0,
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
                    cdn_image_url(listing.image_url),
                    now,
                    1 if notified else 0,
                    listing.created_on,
                    listing.edited_on,
                    listing.views,
                    listing.old_price_czk,
                    event_kind,
                    listing.lat,
                    listing.lon,
                    (listing.description or "").strip() or None,
                    _extras_json(listing.extras),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO events(listing_id, monitor_id, kind, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (listing.id, monitor_id, event_kind, now, detail),
            )
            self._record_price(conn, monitor_id, listing, now)
            self._save_photos(conn, monitor_id, listing)

    def snapshot_scrape_price(self, monitor_id: str, listing: Listing) -> None:
        now = utc_now()
        with self.connect() as conn:
            prev = conn.execute(
                "SELECT price_czk FROM listings WHERE monitor_id = ? AND id = ?",
                (monitor_id, listing.id),
            ).fetchone()
            if not prev:
                return
            old = prev["price_czk"]
            dropped = (
                listing.price_czk is not None
                and old is not None
                and int(old) != int(listing.price_czk)
                and int(old) > int(listing.price_czk)
            )
            conn.execute(
                """
                UPDATE listings SET
                    price_czk = COALESCE(?, price_czk),
                    price_label = CASE WHEN ? != '' THEN ? ELSE price_label END,
                    last_seen = ?,
                    gone = 0,
                    old_price_czk = CASE WHEN ? THEN ? ELSE old_price_czk END
                WHERE monitor_id = ? AND id = ?
                """,
                (
                    listing.price_czk,
                    listing.price_label or "",
                    listing.price_label or "",
                    now,
                    1 if dropped else 0,
                    old,
                    monitor_id,
                    listing.id,
                ),
            )
            self._record_price(conn, monitor_id, listing, now)

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
        items = [public_listing(dict(row)) for row in rows]
        self._attach_catalog_extras(items)
        return items

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
                    "UPDATE listings SET lat = ?, lon = ?, last_seen = ? WHERE monitor_id = ? AND id = ?",
                    (listing.lat, listing.lon, utc_now(), monitor_id, listing.id),
                )
            else:
                conn.execute(
                    "UPDATE listings SET lat = ?, lon = ?, last_seen = ? WHERE id = ?",
                    (listing.lat, listing.lon, utc_now(), listing.id),
                )

    def _ensure_catalog(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_photos (
                monitor_id TEXT NOT NULL,
                listing_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (monitor_id, listing_id, url)
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                monitor_id TEXT NOT NULL,
                listing_id INTEGER NOT NULL,
                price_czk INTEGER,
                price_label TEXT,
                seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_price_history_listing
                ON price_history(monitor_id, listing_id, seen_at);
            CREATE TABLE IF NOT EXISTS listing_user (
                url TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT '',
                note TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        if not conn.execute("SELECT 1 FROM price_history LIMIT 1").fetchone():
            conn.execute(
                """
                INSERT INTO price_history(monitor_id, listing_id, price_czk, price_label, seen_at)
                SELECT monitor_id, id, price_czk, price_label, first_seen
                FROM listings
                WHERE price_czk IS NOT NULL
                """
            )
        if not conn.execute("SELECT 1 FROM listing_photos LIMIT 1").fetchone():
            conn.execute(
                """
                INSERT OR IGNORE INTO listing_photos(monitor_id, listing_id, url, sort_order)
                SELECT monitor_id, id, image_url, 0 FROM listings WHERE image_url IS NOT NULL AND image_url != ''
                """
            )
        self._fix_sreality_images(conn)

    def _migrate_monitors(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(monitors)")}
        if "interval_sec" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN interval_sec INTEGER")
        if "last_inventory" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN last_inventory TEXT")

    def _fix_sreality_images(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE listings
            SET image_url = image_url || CASE WHEN image_url LIKE '%?%' THEN '&' ELSE '?' END || ?
            WHERE image_url LIKE '%sdn.cz%' AND image_url NOT LIKE '%fl=%'
            """,
            (IMAGE_TRANSFORM,),
        )
        rows = conn.execute(
            """
            SELECT monitor_id, listing_id, url, sort_order
            FROM listing_photos
            WHERE url LIKE '%sdn.cz%' AND url NOT LIKE '%fl=%'
            """
        ).fetchall()
        for row in rows:
            new_url = cdn_image_url(row["url"])
            if not new_url or new_url == row["url"]:
                continue
            exists = conn.execute(
                "SELECT 1 FROM listing_photos WHERE monitor_id = ? AND listing_id = ? AND url = ?",
                (row["monitor_id"], row["listing_id"], new_url),
            ).fetchone()
            if exists:
                conn.execute(
                    "DELETE FROM listing_photos WHERE monitor_id = ? AND listing_id = ? AND url = ?",
                    (row["monitor_id"], row["listing_id"], row["url"]),
                )
            else:
                conn.execute(
                    "UPDATE listing_photos SET url = ? WHERE monitor_id = ? AND listing_id = ? AND url = ?",
                    (new_url, row["monitor_id"], row["listing_id"], row["url"]),
                )

    def _record_price(self, conn: sqlite3.Connection, monitor_id: str, listing: Listing, seen_at: str) -> None:
        if listing.price_czk is None:
            return
        last = conn.execute(
            """
            SELECT price_czk, seen_at FROM price_history
            WHERE monitor_id = ? AND listing_id = ?
            ORDER BY seen_at DESC, id DESC LIMIT 1
            """,
            (monitor_id, listing.id),
        ).fetchone()
        if last and last["price_czk"] == listing.price_czk:
            last_day = str(last["seen_at"] or "")[:10]
            if last_day == str(seen_at)[:10]:
                return
        conn.execute(
            """
            INSERT INTO price_history(monitor_id, listing_id, price_czk, price_label, seen_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (monitor_id, listing.id, listing.price_czk, listing.price_label, seen_at),
        )

    def _save_photos(self, conn: sqlite3.Connection, monitor_id: str, listing: Listing) -> None:
        photos = [cdn_image_url(url) for url in (listing.photos or []) if url] or (
            [cdn_image_url(listing.image_url)] if listing.image_url else []
        )
        photos = [url for url in photos if url]
        if not photos:
            return
        existing = conn.execute(
            "SELECT COUNT(*) FROM listing_photos WHERE monitor_id = ? AND listing_id = ?",
            (monitor_id, listing.id),
        ).fetchone()[0]
        if existing >= len(photos):
            return
        for index, url in enumerate(photos):
            conn.execute(
                """
                INSERT OR IGNORE INTO listing_photos(monitor_id, listing_id, url, sort_order)
                VALUES (?, ?, ?, ?)
                """,
                (monitor_id, listing.id, url, index),
            )

    def save_listing_photos(self, monitor_id: str, listing: Listing) -> None:
        with self.connect() as conn:
            self._save_photos(conn, monitor_id, listing)

    def save_listing_enrichment(self, monitor_id: str, listing: Listing) -> None:
        extras = _extras_json(listing.extras)
        now = utc_now()
        with self.connect() as conn:
            prev = conn.execute(
                "SELECT price_czk FROM listings WHERE monitor_id = ? AND id = ?",
                (monitor_id, listing.id),
            ).fetchone()
            old = prev["price_czk"] if prev else None
            dropped = (
                listing.price_czk is not None
                and old is not None
                and int(old) != int(listing.price_czk)
                and int(old) > int(listing.price_czk)
            )
            conn.execute(
                """
                UPDATE listings SET
                    description = COALESCE(?, description),
                    extras = CASE
                        WHEN ? IS NOT NULL AND ? != '' AND ? != '{}' THEN ?
                        ELSE extras
                    END,
                    created_on = COALESCE(?, created_on),
                    edited_on = COALESCE(?, edited_on),
                    views = COALESCE(?, views),
                    price_czk = COALESCE(?, price_czk),
                    price_label = CASE WHEN ? != '' THEN ? ELSE price_label END,
                    old_price_czk = CASE WHEN ? THEN ? ELSE COALESCE(?, old_price_czk) END,
                    lat = COALESCE(?, lat),
                    lon = COALESCE(?, lon),
                    image_url = COALESCE(?, image_url),
                    last_seen = ?
                WHERE monitor_id = ? AND id = ?
                """,
                (
                    (listing.description or "").strip() or None,
                    extras,
                    extras,
                    extras,
                    extras,
                    listing.created_on,
                    listing.edited_on,
                    listing.views,
                    listing.price_czk,
                    listing.price_label or "",
                    listing.price_label or "",
                    1 if dropped else 0,
                    old,
                    listing.old_price_czk,
                    listing.lat,
                    listing.lon,
                    cdn_image_url(listing.image_url),
                    now,
                    monitor_id,
                    listing.id,
                ),
            )
            self._record_price(conn, monitor_id, listing, now)
            self._save_photos(conn, monitor_id, listing)

    def catalog(self, filters: dict[str, Any]) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        portal = (filters.get("portal") or "").strip()
        if portal == "sreality":
            where.append("listings.url LIKE '%sreality.cz%'")
        elif portal == "bezrealitky":
            where.append("listings.url LIKE '%bezrealitky.cz%'")
        monitor_id = (filters.get("monitor_id") or "").strip()
        if monitor_id:
            where.append("listings.monitor_id = ?")
            params.append(monitor_id)
        query = (filters.get("q") or "").strip()
        if query:
            where.append(
                "(listings.name LIKE ? OR listings.locality LIKE ? OR listings.disposition LIKE ? OR IFNULL(listings.description, '') LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like, like])
        disposition = filters.get("disposition") or []
        if isinstance(disposition, str):
            disposition = [item for item in disposition.split(",") if item]
        if disposition:
            where.append(f"listings.disposition IN ({','.join('?' * len(disposition))})")
            params.extend(disposition)
        for column, key in (("price_czk", "price_from"), ("price_czk", "price_to"), ("area_m2", "area_from"), ("area_m2", "area_to")):
            value = filters.get(key)
            if value in (None, ""):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            where.append(f"listings.{column} {'>=' if key.endswith('_from') else '<='} ?")
            params.append(number)
        amenities = filters.get("amenities") or []
        if isinstance(amenities, str):
            amenities = [item for item in amenities.split(",") if item]
        amenity_set = {item for item in amenities if item}
        roommate = (filters.get("roommate") or "").strip()
        pets = (filters.get("pets") or "").strip()
        short_term = (filters.get("short_term") or "").strip()
        if not roommate and "roommate" in amenity_set:
            roommate = "s"
        if not pets and "pets" in amenity_set:
            pets = "s"
        if not short_term and "short_term" in amenity_set:
            short_term = "s"
        for flag in amenity_set:
            if flag in {"pets", "roommate", "short_term"}:
                continue
            where.append("listings.extras LIKE ?")
            params.append(f'%"{flag}"%')
        _tri_state(
            where,
            params,
            roommate,
            [
                "IFNULL(listings.extras, '') LIKE ?",
                "IFNULL(listings.name, '') LIKE ?",
                "IFNULL(listings.description, '') LIKE ?",
                "lower(IFNULL(listings.disposition, '')) = 'pokoj'",
                "IFNULL(listings.url, '') LIKE ?",
            ],
            ['%"roommate"%', "%spolubydl%", "%spolubydl%", "%/pokoj/%"],
        )
        _tri_state(
            where,
            params,
            pets,
            ["IFNULL(listings.extras, '') LIKE ?", "IFNULL(listings.extras, '') LIKE ?"],
            ['%"pets"%', '%"Mazlíčci", "value": "povolení"%'],
        )
        _tri_state(where, params, short_term, ["IFNULL(listings.extras, '') LIKE ?"], ['%"short_term"%'])
        offers = _csv(filters.get("offer"))
        if offers:
            offer_parts = []
            for offer in offers:
                if offer == "pronajem":
                    offer_parts.append("(listings.extras LIKE ? OR ((listings.extras IS NULL OR listings.extras IN ('', '{}')) AND listings.price_label LIKE ?))")
                    params.extend(['%"offer": "Pronájem"%', "%měsíc%"])
                elif offer == "prodej":
                    offer_parts.append("(listings.extras LIKE ? OR ((listings.extras IS NULL OR listings.extras IN ('', '{}')) AND listings.price_label NOT LIKE ?))")
                    params.extend(['%"offer": "Prodej"%', "%měsíc%"])
            if offer_parts:
                where.append("(" + " OR ".join(offer_parts) + ")")
        districts = _csv(filters.get("district"))
        if districts:
            district_parts = []
            for district in districts:
                number = district.replace("praha-", "")
                if not number.isdigit():
                    continue
                district_parts.append(
                    "(listings.locality GLOB ? OR listings.locality GLOB ?)"
                )
                params.extend([f"*Praha {number}", f"*Praha {number}[!0-9]*"])
                for area in PRAGUE_DISTRICTS.get(number) or []:
                    district_parts.append("listings.locality LIKE ?")
                    params.append(f"%{area}%")
            if district_parts:
                where.append("(" + " OR ".join(district_parts) + ")")
        estates = _csv(filters.get("estate"))
        if estates:
            estate_parts = []
            empty_extras = "(listings.extras IS NULL OR listings.extras IN ('', '{}'))"
            for estate in estates:
                if estate == "byt":
                    estate_parts.append(
                        f"(listings.extras LIKE '%\"estate\": \"Byt%' OR ({empty_extras} AND listings.name LIKE '%byt%'))"
                    )
                elif estate == "dum":
                    estate_parts.append(
                        f"(listings.extras LIKE '%\"estate\": \"Dom%' OR listings.extras LIKE '%\"estate\": \"Dům%' "
                        f"OR ({empty_extras} AND (listings.name LIKE '%dům%' OR listings.name LIKE '%Dům%' OR listings.name LIKE '%dum%')))"
                    )
            if estate_parts:
                where.append("(" + " OR ".join(estate_parts) + ")")
        ownership = _csv(filters.get("ownership"))
        if ownership:
            _or_likes(where, params, "listings.extras", [f'%"Vlastnictví", "value": "{label}"%' for label in ownership])
        conditions = _csv(filters.get("condition"))
        if conditions:
            patterns = []
            for label in conditions:
                patterns.append(f'%"Stav", "value": "{label}"%')
                if label == "Po rekonstrukci":
                    patterns.append('%"Stav", "value": "Po částečné rekonstrukci"%')
            _or_likes(where, params, "listings.extras", patterns)
        buildings = _csv(filters.get("building"))
        if buildings:
            patterns = []
            for item in buildings:
                if item == "cihlova":
                    patterns.append("%Cihl%")
                elif item == "panelova":
                    patterns.append("%Panel%")
                elif item == "ostatni":
                    patterns.extend(["%Smíšená%", "%Montovan%", "%Skelet%", "%Kamenn%", "%Ostatní%"])
            _or_likes(where, params, "listings.extras", patterns)
        equipped = _csv(filters.get("equipped"))
        if equipped:
            patterns = []
            for item in equipped:
                if item == "vybaveny":
                    patterns.extend(
                        [
                            '%"Vybavení", "value": "Vybavený"%',
                            '%"Vybavení", "value": "Vybaveno"%',
                            '%"Vybavení", "value": "Ano"%',
                        ]
                    )
                elif item == "castecne":
                    patterns.append('%"Vybavení", "value": "Částečně"%')
                elif item == "nevybaveny":
                    patterns.extend(
                        [
                            '%"Vybavení", "value": "Nevybaven"%',
                            '%"Vybavení", "value": "Ne"%',
                        ]
                    )
            _or_likes(where, params, "listings.extras", patterns)
        _apply_circle(where, params, filters)
        status = (filters.get("status") or "").strip()
        if status == "saved":
            where.append("EXISTS (SELECT 1 FROM listing_user WHERE listing_user.url = listings.url AND listing_user.status = 'saved')")
        elif status == "hidden":
            where.append("EXISTS (SELECT 1 FROM listing_user WHERE listing_user.url = listings.url AND listing_user.status = 'hidden')")
        elif status != "all":
            where.append("NOT EXISTS (SELECT 1 FROM listing_user WHERE listing_user.url = listings.url AND listing_user.status = 'hidden')")
        if str(filters.get("discounted") or "").strip() in {"1", "true", "yes"}:
            where.append(
                """
                (
                    (listings.old_price_czk IS NOT NULL AND listings.price_czk IS NOT NULL AND listings.old_price_czk > listings.price_czk)
                    OR IFNULL(listings.extras, '') LIKE '%"discounted"%'
                    OR EXISTS (
                        SELECT 1 FROM price_history ph
                        WHERE ph.monitor_id = listings.monitor_id AND ph.listing_id = listings.id
                          AND ph.price_czk IS NOT NULL AND listings.price_czk IS NOT NULL
                        GROUP BY ph.monitor_id, ph.listing_id
                        HAVING MAX(ph.price_czk) > listings.price_czk
                    )
                )
                """
            )
        if filters.get("pins_only"):
            return self._catalog_pins(where, params)
        sorts = {
            "newest": "listings.first_seen DESC",
            "oldest": "listings.first_seen ASC",
            "price_asc": "listings.price_czk IS NULL, listings.price_czk ASC",
            "price_desc": "listings.price_czk IS NULL, listings.price_czk DESC",
            "area_asc": "listings.area_m2 IS NULL, listings.area_m2 ASC",
            "area_desc": "listings.area_m2 IS NULL, listings.area_m2 DESC",
            "discount_desc": "CASE WHEN listings.old_price_czk IS NOT NULL AND listings.price_czk IS NOT NULL THEN listings.old_price_czk - listings.price_czk ELSE 0 END DESC",
        }
        order = sorts.get((filters.get("sort") or "newest").strip(), sorts["newest"])
        limit = min(max(int(filters.get("limit") or 36), 1), 120)
        offset = max(int(filters.get("offset") or 0), 0)
        clause = " AND ".join(where)
        sql = f"""
            SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
            FROM listings
            LEFT JOIN monitors ON monitors.id = listings.monitor_id
            WHERE {clause}
            AND listings.rowid IN (
                SELECT MIN(rowid) FROM listings GROUP BY url
            )
            ORDER BY {order}
            LIMIT ? OFFSET ?
        """
        count_sql = f"""
            SELECT COUNT(*) FROM listings
            WHERE {clause}
            AND listings.rowid IN (SELECT MIN(rowid) FROM listings GROUP BY url)
        """
        with self.connect() as conn:
            total = int(conn.execute(count_sql, params).fetchone()[0])
            rows = [dict(row) for row in conn.execute(sql, (*params, limit, offset)).fetchall()]
            keys = [(row["monitor_id"], row["id"]) for row in rows]
            photos: dict[tuple[str, int], list[str]] = {}
            if keys:
                holders = " OR ".join("(monitor_id = ? AND listing_id = ?)" for _ in keys)
                flat = [item for pair in keys for item in pair]
                for photo in conn.execute(
                    f"SELECT monitor_id, listing_id, url FROM listing_photos WHERE {holders} ORDER BY sort_order",
                    flat,
                ):
                    photos.setdefault((photo["monitor_id"], int(photo["listing_id"])), []).append(photo["url"])
        items = []
        for row in rows:
            item = public_listing(row)
            urls = photos.get((item["monitor_id"], int(item["id"]))) or ([item["image_url"]] if item.get("image_url") else [])
            item["photos"] = _photo_urls(urls)
            items.append(item)
        self._attach_catalog_extras(items)
        facets = self.catalog_facets()
        return {"items": items, "total": total, "limit": limit, "offset": offset, "facets": facets}

    def _catalog_pins(self, where: list[str], params: list[Any]) -> dict[str, Any]:
        clause = " AND ".join(where)
        sql = f"""
            SELECT listings.id, listings.monitor_id, listings.lat, listings.lon,
                   listings.price_czk, listings.price_label, listings.name, listings.locality
            FROM listings
            WHERE {clause}
            AND listings.lat IS NOT NULL AND listings.lon IS NOT NULL
            AND listings.rowid IN (SELECT MIN(rowid) FROM listings GROUP BY url)
            LIMIT 2500
        """
        with self.connect() as conn:
            items = [dict(row) for row in conn.execute(sql, params).fetchall()]
        return {"items": items}

    def catalog_facets(self) -> dict[str, Any]:
        with self.connect() as conn:
            dispositions = [
                row[0]
                for row in conn.execute(
                    "SELECT disposition FROM listings WHERE disposition IS NOT NULL AND disposition != '' GROUP BY disposition ORDER BY COUNT(*) DESC"
                )
            ]
            monitors = [
                {"id": row["id"], "name": row["name"]}
                for row in conn.execute(
                    """
                    SELECT monitors.id, monitors.name
                    FROM monitors
                    JOIN listings ON listings.monitor_id = monitors.id
                    GROUP BY monitors.id
                    ORDER BY monitors.name
                    """
                )
            ]
            portals = []
            if conn.execute("SELECT 1 FROM listings WHERE url LIKE '%sreality.cz%' LIMIT 1").fetchone():
                portals.append("sreality")
            if conn.execute("SELECT 1 FROM listings WHERE url LIKE '%bezrealitky.cz%' LIMIT 1").fetchone():
                portals.append("bezrealitky")
        return {"dispositions": dispositions, "portals": portals, "monitors": monitors}

    def catalog_item(self, monitor_id: str, listing_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                FROM listings
                LEFT JOIN monitors ON monitors.id = listings.monitor_id
                WHERE listings.monitor_id = ? AND listings.id = ?
                """,
                (monitor_id, listing_id),
            ).fetchone()
            if not row:
                return None
            item = public_listing(dict(row))
            item["photos"] = _photo_urls(
                [
                    photo["url"]
                    for photo in conn.execute(
                        "SELECT url FROM listing_photos WHERE monitor_id = ? AND listing_id = ? ORDER BY sort_order",
                        (monitor_id, listing_id),
                    )
                ]
            )
            if not item["photos"] and item.get("image_url"):
                item["photos"] = _photo_urls([item["image_url"]])
            item["price_history"] = _price_series(
                [
                    dict(hist)
                    for hist in conn.execute(
                        """
                        SELECT price_czk, price_label, seen_at
                        FROM price_history
                        WHERE monitor_id = ? AND listing_id = ?
                        ORDER BY seen_at
                        """,
                        (monitor_id, listing_id),
                    )
                ],
                item,
            )
        self._attach_catalog_extras([item])
        return item

    def set_listing_user(self, url: str, status: str | None = None, note: str | None = None) -> dict[str, Any]:
        url = (url or "").strip()
        if not url:
            raise ValueError("Chybí url")
        current = self.get_listing_user(url)
        next_status = current.get("status") or ""
        if status is not None:
            next_status = status if status in {"saved", "hidden"} else ""
        next_note = current.get("note") if note is None else str(note)
        now = utc_now()
        with self.connect() as conn:
            if not next_status and not (next_note or "").strip():
                conn.execute("DELETE FROM listing_user WHERE url = ?", (url,))
                return {"url": url, "status": "", "note": ""}
            conn.execute(
                """
                INSERT INTO listing_user(url, status, note, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET status = excluded.status, note = excluded.note, updated_at = excluded.updated_at
                """,
                (url, next_status, next_note or "", now),
            )
        return {"url": url, "status": next_status, "note": next_note or ""}

    def get_listing_user(self, url: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute("SELECT url, status, note FROM listing_user WHERE url = ?", (url,)).fetchone()
        if not row:
            return {"url": url, "status": "", "note": ""}
        return {"url": row["url"], "status": row["status"] or "", "note": row["note"] or ""}

    def url_already_notified(self, url: str, exclude_monitor_id: str | None = None) -> bool:
        if not url:
            return False
        sql = "SELECT 1 FROM listings WHERE url = ? AND notified = 1"
        params: list[Any] = [url]
        if exclude_monitor_id:
            sql += " AND monitor_id != ?"
            params.append(exclude_monitor_id)
        with self.connect() as conn:
            return bool(conn.execute(sql, params).fetchone())

    def mark_gone(self, monitor_id: str, listing_id: int) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM listings WHERE monitor_id = ? AND id = ?",
                (monitor_id, listing_id),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("gone"):
                return data
            conn.execute(
                "UPDATE listings SET gone = 1, last_kind = 'sold' WHERE monitor_id = ? AND id = ?",
                (monitor_id, listing_id),
            )
            conn.execute(
                "INSERT INTO events(listing_id, monitor_id, kind, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                (listing_id, monitor_id, "sold", now, data.get("name") or "Staženo z inzerce"),
            )
            data["gone"] = 1
            data["gone_at"] = now
            data["last_kind"] = "sold"
            return data

    def mark_sold_notified(self, monitor_id: str, listing_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE listings SET sold_notified = 1 WHERE monitor_id = ? AND id = ?",
                (monitor_id, listing_id),
            )

    def url_sold_notified(self, url: str, exclude_monitor_id: str | None = None) -> bool:
        if not url:
            return False
        sql = "SELECT 1 FROM listings WHERE url = ? AND IFNULL(sold_notified, 0) = 1"
        params: list[Any] = [url]
        if exclude_monitor_id:
            sql += " AND monitor_id != ?"
            params.append(exclude_monitor_id)
        with self.connect() as conn:
            return bool(conn.execute(sql, params).fetchone())

    def pending_sold_pings(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT listings.*, monitors.name AS monitor_name
                FROM listings
                LEFT JOIN monitors ON monitors.id = listings.monitor_id
                WHERE IFNULL(listings.gone, 0) = 1
                  AND IFNULL(listings.sold_notified, 0) = 0
                ORDER BY listings.last_seen DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def active_missing(self, monitor_id: str, present_ids: set[int], limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM listings
                    WHERE monitor_id = ? AND IFNULL(gone, 0) = 0
                    ORDER BY last_seen IS NULL DESC, last_seen ASC
                    """,
                    (monitor_id,),
                )
            ]
        missing = [row for row in rows if int(row["id"]) not in present_ids]
        return missing[:limit]

    def touch_last_seen(self, monitor_id: str, ids: list[int]) -> None:
        if not ids:
            return
        now = utc_now()
        with self.connect() as conn:
            for offset in range(0, len(ids), 400):
                chunk = ids[offset : offset + 400]
                placeholders = ",".join("?" * len(chunk))
                conn.execute(
                    f"UPDATE listings SET last_seen = ?, gone = 0 WHERE monitor_id = ? AND id IN ({placeholders})",
                    (now, monitor_id, *chunk),
                )

    def stale_listings(self, monitor_id: str, days: int = 5, limit: int = 5) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM listings
                WHERE monitor_id = ?
                  AND IFNULL(gone, 0) = 0
                  AND (last_seen IS NULL OR last_seen < ?)
                ORDER BY last_seen IS NULL DESC, last_seen ASC
                LIMIT ?
                """,
                (monitor_id, cutoff_iso, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def monitor_preview(self, monitor_id: str, limit: int = 6) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                    FROM listings
                    LEFT JOIN monitors ON monitors.id = listings.monitor_id
                    WHERE listings.monitor_id = ?
                    ORDER BY listings.first_seen DESC
                    LIMIT ?
                    """,
                    (monitor_id, limit),
                )
            ]
        items = [public_listing(row) for row in rows]
        self._attach_catalog_extras(items)
        return items

    def digest_items(self, since: str | None = None) -> list[dict[str, Any]]:
        if not since:
            since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self.connect() as conn:
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT listings.*, monitors.name AS monitor_name, events.kind AS event_kind, events.created_at AS event_at
                    FROM events
                    JOIN listings ON listings.monitor_id = events.monitor_id AND listings.id = events.listing_id
                    LEFT JOIN monitors ON monitors.id = listings.monitor_id
                    WHERE events.kind IN ('new', 'changed') AND events.created_at >= ?
                    ORDER BY events.created_at DESC
                    """,
                    (since,),
                )
            ]
        seen: set[str] = set()
        items = []
        for row in rows:
            url = row.get("url") or ""
            if url in seen:
                continue
            seen.add(url)
            item = public_listing(row)
            item["kind"] = row.get("event_kind") or item.get("last_kind")
            items.append(item)
        return items

    def _attach_catalog_extras(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        urls = [item.get("url") for item in items if item.get("url")]
        if not urls:
            return
        holders = ",".join("?" * len(urls))
        with self.connect() as conn:
            users = {
                row["url"]: {"status": row["status"] or "", "note": row["note"] or ""}
                for row in conn.execute(f"SELECT url, status, note FROM listing_user WHERE url IN ({holders})", urls)
            }
            monitor_rows = conn.execute(
                f"""
                SELECT listings.url, monitors.id, monitors.name
                FROM listings
                JOIN monitors ON monitors.id = listings.monitor_id
                WHERE listings.url IN ({holders})
                GROUP BY listings.url, monitors.id
                ORDER BY monitors.name
                """,
                urls,
            ).fetchall()
            history = {}
            keys = [(item.get("monitor_id"), item.get("id")) for item in items if item.get("monitor_id") and item.get("id") is not None]
            if keys:
                clause = " OR ".join("(monitor_id = ? AND listing_id = ?)" for _ in keys)
                flat = [item for pair in keys for item in pair]
                for row in conn.execute(
                    f"SELECT monitor_id, listing_id, price_czk FROM price_history WHERE {clause} AND price_czk IS NOT NULL",
                    flat,
                ):
                    history.setdefault((row["monitor_id"], int(row["listing_id"])), []).append(int(row["price_czk"]))
            twins = {}
            for item in items:
                twin = self._find_twin(conn, item)
                if twin:
                    twins[item.get("url")] = twin
        monitors_by_url: dict[str, list[dict[str, str]]] = {}
        for row in monitor_rows:
            monitors_by_url.setdefault(row["url"], []).append({"id": row["id"], "name": row["name"]})
        for item in items:
            user = users.get(item.get("url") or "", {})
            item["status"] = user.get("status") or ""
            item["note"] = user.get("note") or ""
            item["monitors"] = monitors_by_url.get(item.get("url") or "", [{"id": item.get("monitor_id"), "name": item.get("monitor_name")}])
            item["twin"] = twins.get(item.get("url"))
            item["gone"] = bool(item.get("gone"))
            prices = history.get((item.get("monitor_id"), int(item["id"]) if item.get("id") is not None else -1), [])
            _apply_discount(item, prices)

    def _find_twin(self, conn: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (TypeError, ValueError, KeyError):
            return None
        url = item.get("url") or ""
        other = "%bezrealitky.cz%" if "sreality" in url else "%sreality.cz%"
        box = 0.001
        rows = conn.execute(
            """
            SELECT listings.*, monitors.name AS monitor_name
            FROM listings
            LEFT JOIN monitors ON monitors.id = listings.monitor_id
            WHERE listings.url != ? AND listings.url LIKE ?
              AND listings.lat BETWEEN ? AND ? AND listings.lon BETWEEN ? AND ?
              AND listings.rowid IN (SELECT MIN(rowid) FROM listings GROUP BY url)
            LIMIT 40
            """,
            (url, other, lat - box, lat + box, lon - box, lon + box),
        ).fetchall()
        best = None
        best_score = 0.0
        for row in rows:
            cand = dict(row)
            try:
                dist = _haversine_m(lat, lon, float(cand["lat"]), float(cand["lon"]))
            except (TypeError, ValueError):
                continue
            if dist > 80:
                continue
            disp_a = (item.get("disposition") or "").strip().lower()
            disp_b = (cand.get("disposition") or "").strip().lower()
            if disp_a and disp_b and disp_a != disp_b:
                continue
            area_a, area_b = item.get("area_m2"), cand.get("area_m2")
            if area_a and area_b and abs(int(area_a) - int(area_b)) / max(int(area_a), int(area_b)) > 0.08:
                continue
            price_a, price_b = item.get("price_czk"), cand.get("price_czk")
            if price_a and price_b and abs(int(price_a) - int(price_b)) / max(int(price_a), int(price_b)) > 0.12:
                continue
            score = 1 / (1 + dist)
            if score > best_score:
                best_score = score
                best = {
                    "monitor_id": cand.get("monitor_id"),
                    "id": cand.get("id"),
                    "url": cand.get("url"),
                    "portal": "Bezrealitky" if "bezrealitky" in (cand.get("url") or "") else "Sreality",
                    "name": cand.get("name"),
                    "price_label": cand.get("price_label"),
                    "distance_m": round(dist),
                }
        return best

    def _template_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["config"] = json.loads(data["config"])
        return data

    def _monitor_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["seeded"] = bool(data["seeded"])
        data["interval_sec"] = data.get("interval_sec")
        data["tracked"] = self.count(data["id"])
        data["new_today"] = self.new_today_count(data["id"])
        return data


def enrich_maps(row: dict[str, Any]) -> dict[str, Any]:
    row["maps_url"] = google_maps_url(row.get("lat"), row.get("lon"), row.get("locality") or "")
    row["image_url"] = cdn_image_url(row.get("image_url"))
    return row


def _photo_urls(urls: list[str] | None) -> list[str]:
    result: list[str] = []
    for url in urls or []:
        fixed = cdn_image_url(url)
        if fixed and fixed not in result:
            result.append(fixed)
    return result


def _extras_json(extras: Any) -> str | None:
    if not extras:
        return None
    if isinstance(extras, str):
        return extras
    return json.dumps(extras, ensure_ascii=False)


def _price_series(history: list[dict[str, Any]], item: dict[str, Any]) -> list[dict[str, Any]]:
    series = [dict(row) for row in history]
    current = item.get("price_czk")
    label = item.get("price_label") or ""
    stamp = item.get("last_seen") or item.get("first_seen")
    if current is None:
        return series
    point = {"price_czk": current, "price_label": label, "seen_at": stamp}
    if not series:
        return [point]
    last = series[-1]
    try:
        same = last.get("price_czk") is not None and int(last["price_czk"]) == int(current)
    except (TypeError, ValueError):
        same = last.get("price_czk") == current
    if not same:
        series.append(point)
    elif stamp and str(last.get("seen_at") or "")[:16] != str(stamp)[:16]:
        series.append(point)
    return series


def public_listing(row: dict[str, Any]) -> dict[str, Any]:
    item = enrich_maps(row)
    item["portal"] = "Bezrealitky" if "bezrealitky" in (item.get("url") or "") else "Sreality"
    extras = item.get("extras")
    if isinstance(extras, str) and extras:
        try:
            extras = json.loads(extras)
        except json.JSONDecodeError:
            extras = {}
    item["extras"] = extras or {}
    item["flags"] = item["extras"].get("flags") or []
    item["gone"] = bool(item.get("gone"))
    _apply_discount(item, [])
    return item


def _apply_discount(item: dict[str, Any], history: list[int]) -> None:
    old = item.get("old_price_czk")
    price = item.get("price_czk")
    try:
        old_n = int(old) if old is not None else None
        price_n = int(price) if price is not None else None
    except (TypeError, ValueError):
        old_n = None
        price_n = None
    if history:
        high = max(history)
        low = history[-1] if history else price_n
        if old_n is None and price_n is not None and high > price_n:
            old_n = high
        if price_n is None and low is not None:
            price_n = low
    if old_n is not None and price_n is not None and old_n > price_n:
        drop = old_n - price_n
        item["discount_czk"] = drop
        item["discount_pct"] = round(100 * drop / old_n)
    else:
        item["discount_czk"] = None
        item["discount_pct"] = None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    radius = 6371000
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = radians(lat2 - lat1)
    d_lam = radians(lon2 - lon1)
    a = sin(d_phi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(d_lam / 2) ** 2
    return 2 * radius * asin(min(1.0, sqrt(a)))
