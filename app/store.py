from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app import config, localities, places
from app.identity import (
    fingerprint,
    link_payload,
    listing_identity,
    listing_key,
    portal_from_url,
    portal_label,
    same_listing,
    url_canonical,
)
from app.sreality import IMAGE_TRANSFORM, Listing, cdn_image_url, google_maps_url, listing_from_dict
from app.sources import is_discord_webhook, usable_discord_webhook, webhook_for
from app.templates import default_template_config


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_day_start() -> str:
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_cs_datetime(value: datetime, *, time: bool = True) -> str:
    stamp = f"{value.day}. {value.month}. {value.year}"
    if time and not (value.hour == 0 and value.minute == 0 and value.second == 0):
        stamp += f" v {value.strftime('%H:%M')}"
    return stamp


def _format_cs_duration(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    if minutes < 60:
        return f"{minutes} min"
    hours = seconds / 3600
    rounded = round(hours * 2) / 2
    if abs(rounded - round(rounded)) < 0.05:
        return f"{int(round(rounded))} h"
    text = f"{rounded:.1f}".replace(".", ",")
    return f"{text} h"


def _is_rental_row(row: dict[str, Any]) -> bool:
    extras = row.get("extras") or {}
    if isinstance(extras, str):
        try:
            extras = json.loads(extras) if extras else {}
        except json.JSONDecodeError:
            extras = {}
    if not isinstance(extras, dict):
        extras = {}
    url = (row.get("url") or "").lower()
    label = (row.get("price_label") or "").lower()
    offer = str(extras.get("offer") or "")
    sale = offer == "Prodej" or "/prodej/" in url or "nemovitost" in label or "kč/ks" in label
    rent = offer == "Pronájem" or "/pronajem/" in url or "měsíc" in label or "mesic" in label
    return rent and not sale


def _is_game_rental(row: dict[str, Any]) -> bool:
    """Rent filter for games without reading extras (avoids huge JSON blobs)."""
    url = str(row.get("url") or "").lower()
    label = str(row.get("price_label") or "").casefold()
    name = str(row.get("name") or "").casefold()
    if "/prodej/" in url or "kč/ks" in label or "kc/ks" in label:
        return False
    return (
        "měsíc" in label
        or "mesic" in label
        or "/pronajem/" in url
        or "/pronajmu/" in url
        or "byty-k-pronajmu" in url
        or "pronáj" in name
        or "pronajem" in name
        or "pronajmu" in name
    )


def _game_vanish_hours(first_seen: Any, last_seen: Any) -> float:
    first = _parse_iso(first_seen)
    last = _parse_iso(last_seen)
    if not first or not last:
        return 8.0
    hours = max(0.2, (last - first).total_seconds() / 3600.0)
    return round(min(48.0, hours), 1)


def _gone_rental_card(row: dict[str, Any]) -> dict[str, Any] | None:
    if not _is_game_rental(row):
        return None
    try:
        price = int(row["price_czk"]) if row.get("price_czk") is not None else 0
    except (TypeError, ValueError):
        price = 0
    if price < 4000 or price > 120000:
        return None
    disposition = (row.get("disposition") or "").strip()
    if "+" not in disposition:
        return None
    added = _parse_iso(row.get("created_on")) or _parse_iso(row.get("first_seen"))
    gone = _parse_iso(row.get("last_seen"))
    first = _parse_iso(row.get("first_seen"))
    if added and added.hour == 0 and added.minute == 0 and added.second == 0 and first:
        added = first
    if not added or not gone:
        return None
    seconds = (gone - added).total_seconds()
    if seconds < 30 * 60 or seconds > 72 * 3600:
        return None
    locality = (row.get("locality") or "").strip()
    place = locality.split("–")[-1].split("-")[-1].strip().lower() if locality else ""
    area = row.get("area_m2")
    spec = disposition if not area else f"{disposition} • {int(area)} m²"
    label = (row.get("price_label") or "").replace("měsíc", "měs.").replace("mesic", "měs.")
    if not label and price:
        label = f"{price:,} Kč/měs.".replace(",", " ")
    image = cdn_image_url(row.get("image_url")) or row.get("image_url")
    return {
        "locality": locality,
        "price": label,
        "spec": spec,
        "badge": f"PRONAJATO ZA {_format_cs_duration(seconds)}",
        "when": f"Přidáno {_format_cs_datetime(added)} · Pronajato {_format_cs_datetime(gone)}",
        "image": image,
        "_hours": seconds / 3600,
        "_url": (row.get("url") or "").strip(),
        "_place": place or locality.lower(),
        "_gone": gone.strftime("%Y-%m-%d %H:%M"),
    }


def catalog_item_needs_live_fetch(item: dict[str, Any] | None) -> bool:
    """Skip portal scrape when the catalog row already has a usable gallery."""
    if not item or item.get("gone"):
        return False
    photos = item.get("photos") or []
    if len(photos) >= 2:
        return False
    extras = item.get("extras") if isinstance(item.get("extras"), dict) else {}
    description = str(item.get("description") or "").strip()
    address = str((extras or {}).get("address") or "").strip()
    if photos and (description or address) and item.get("lat") is not None:
        return False
    return bool(item.get("url"))


def _is_discord_webhook(url: str) -> bool:
    return is_discord_webhook(url)


def _pin_item(row: dict[str, Any]) -> dict[str, Any]:
    identity = listing_identity(row)
    return {
        "id": row.get("id"),
        "monitor_id": row.get("monitor_id"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "price_czk": row.get("price_czk"),
        "price_label": row.get("price_label"),
        "name": row.get("name"),
        "locality": row.get("locality"),
        "listing_key": identity,
        "canonical_key": identity,
        "url": row.get("url") or "",
        "count": int(row["count"]) if row.get("count") else 1,
    }


def _place_center(geoms: list[dict[str, Any]]) -> tuple[float, float] | None:
    for geom in geoms:
        try:
            return float(geom["lat"]), float(geom["lon"])
        except (TypeError, ValueError, KeyError):
            continue
    return None


def _pin_with_place(row: dict[str, Any], geoms: list[dict[str, Any]] | None = None, approx: bool = False) -> dict[str, Any] | None:
    pin = _pin_item(row)
    if pin.get("lat") is not None and pin.get("lon") is not None:
        if approx:
            pin["approx"] = True
        return pin
    locality = str(row.get("locality") or "")
    if "," in locality:
        return None
    center = _place_center(geoms or [])
    if not center:
        return None
    seed = abs(int(row.get("id") or 0))
    pin["lat"] = center[0] + ((seed % 17) - 8) * 0.00016
    pin["lon"] = center[1] + ((seed % 13) - 6) * 0.0002
    pin["approx"] = True
    return pin


def _hidden_listing_sql() -> str:
    canon = "COALESCE(NULLIF(listings.canonical_key, ''), listings.listing_key)"
    return f"""
        EXISTS (
            SELECT 1 FROM listing_user
            WHERE listing_user.status = 'hidden'
              AND (
                listing_user.url = listings.url
                OR EXISTS (
                    SELECT 1 FROM listing_links
                    WHERE listing_links.url = listing_user.url
                      AND listing_links.canonical_key = {canon}
                )
              )
        )
    """


def _saved_listing_sql() -> str:
    canon = "COALESCE(NULLIF(listings.canonical_key, ''), listings.listing_key)"
    return f"""
        EXISTS (
            SELECT 1 FROM listing_user
            WHERE listing_user.status = 'saved'
              AND (
                listing_user.url = listings.url
                OR EXISTS (
                    SELECT 1 FROM listing_links
                    WHERE listing_links.url = listing_user.url
                      AND listing_links.canonical_key = {canon}
                )
              )
        )
    """


def _monitor_hit_sql() -> str:
    canon = "COALESCE(NULLIF(listings.canonical_key, ''), listings.listing_key, listings.url)"
    return f"""
        EXISTS (
            SELECT 1 FROM monitor_hits
            WHERE monitor_hits.monitor_id = ?
              AND (
                monitor_hits.listing_key = {canon}
                OR monitor_hits.listing_key = listings.listing_key
                OR monitor_hits.listing_key = listings.url
                OR EXISTS (
                    SELECT 1 FROM listing_links
                    WHERE listing_links.canonical_key = {canon}
                      AND (
                        listing_links.url_key = monitor_hits.listing_key
                        OR listing_links.url = monitor_hits.listing_key
                      )
                )
              )
        )
    """


CATALOG_MONITOR_ID = "__catalog__"


def channel_key(webhook_url: str) -> str:
    parsed = urlparse((webhook_url or "").strip())
    parts = [part for part in (parsed.path or "").split("/") if part]
    if "webhooks" in parts:
        index = parts.index("webhooks")
        if index + 1 < len(parts):
            return parts[index + 1]
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/").lower()
    return f"{host}{path}" or (webhook_url or "").strip()


def listing_identity_sql(alias: str = "listings") -> str:
    return (
        f"COALESCE(NULLIF({alias}.canonical_key, ''), NULLIF({alias}.listing_key, ''), {alias}.url, "
        f"{alias}.monitor_id || ':' || {alias}.id)"
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


def _apply_map_bbox(where: list[str], params: list[Any], filters: dict[str, Any]) -> None:
    try:
        south = float(filters.get("south"))
        north = float(filters.get("north"))
        west = float(filters.get("west"))
        east = float(filters.get("east"))
    except (TypeError, ValueError):
        return
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        return
    gps = (
        "listings.lat IS NOT NULL AND listings.lon IS NOT NULL "
        "AND listings.lat BETWEEN ? AND ? AND listings.lon BETWEEN ? AND ?"
    )
    span = (north - south) + (east - west)
    anchors = places.anchors_in_bbox(south, north, west, east)
    if not anchors:
        # Jen když ve výřezu není žádný centroid: vezmi okolí / nejbližší,
        # ať zoom do čtvrti nepřijde o locality match.
        pad_lat = max(0.05, (north - south) * 0.5)
        pad_lon = max(0.05, (east - west) * 0.5)
        anchors = places.anchors_in_bbox(south - pad_lat, north + pad_lat, west - pad_lon, east + pad_lon)
    if not anchors:
        anchors = places.nearest_anchors((south + north) / 2, (west + east) / 2, limit=6)
    filters["_bbox"] = (south, north, west, east)
    filters["_bbox_anchors"] = anchors
    filters["_bbox_span"] = span
    if anchors:
        # U těsného zoomu nech district labels (Praha 4…); coarse „Praha“ až u širšího výřezu.
        if span >= 0.85:
            labels = places.coarse_anchor_labels(anchors)
        else:
            labels = [name for name, _lat, _lon in anchors]
            # „Praha“ / „Brno“ by přes „Praha %“ vytáhly celý katalog čtvrtí.
            folded = [places._fold_label(name) for name in labels]
            if any(item.startswith("praha ") for item in folded):
                labels = [name for name, key in zip(labels, folded) if key != "praha"]
            if any(item.startswith("brno") and item != "brno" for item in folded):
                labels = [name for name, key in zip(labels, folded) if key != "brno"]
        text_sql, text_params = places.locality_match_sql("listings.locality", labels)
        # Locality match platí i když má inzerát GPS jinde (centroid dump / špatný approx).
        where.append(f"(({gps}) OR {text_sql})")
        params.extend([south, north, west, east, *text_params])
    else:
        where.append(gps)
        params.extend([south, north, west, east])


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
_PRAHA_PLACE_RE = re.compile(r"praha[\s-]*(\d+)", re.I)


def _prague_number_from_place(geom: dict[str, Any]) -> str | None:
    ident = str(geom.get("id") or "")
    slug = str(localities.OSM_TO_SREALITY.get(ident) or "")
    match = re.match(r"praha-(\d+)$", slug, re.I)
    if match:
        return match.group(1)
    match = _PRAHA_PLACE_RE.search(str(geom.get("label") or ""))
    return match.group(1) if match else None


def _place_text_sql(geoms: list[dict[str, Any]]) -> tuple[str, list[Any]]:
    parts: list[str] = []
    params: list[Any] = []
    for geom in geoms:
        number = _prague_number_from_place(geom)
        if number:
            parts.append("(listings.locality GLOB ? OR listings.locality GLOB ? OR listings.url LIKE ?)")
            params.extend([f"*Praha {number}", f"*Praha {number}[!0-9]*", f"%praha-{number}%"])
            continue
        label = str(geom.get("label") or "").strip()
        if label:
            parts.append("(listings.locality LIKE ? OR listings.name LIKE ?)")
            params.extend([f"%{label}%", f"%{label}%"])
    if not parts:
        return "", []
    return "(" + " OR ".join(parts) + ")", params


def listing_matches_place_text(row: dict[str, Any], geoms: list[dict[str, Any]]) -> bool:
    locality = str(row.get("locality") or "")
    url = str(row.get("url") or "").casefold()
    name = str(row.get("name") or "")
    hay = f"{locality} {name}".casefold()
    for geom in geoms:
        number = _prague_number_from_place(geom)
        if number:
            if re.search(rf"praha\s*{number}(?!\d)", locality, re.I) or f"praha-{number}" in url:
                return True
            continue
        label = str(geom.get("label") or "").strip()
        if label and label.casefold() in hay:
            return True
    return False


def listing_matches_places(row: dict[str, Any], geoms: list[dict[str, Any]]) -> bool:
    if not geoms:
        return True
    lat, lon = row.get("lat"), row.get("lon")
    if lat is not None and lon is not None:
        return places.point_matches(lat, lon, geoms)
    return listing_matches_place_text(row, geoms)


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._facets_cache: dict[str, Any] | None = None
        self._facets_at = 0.0
        self._city_pin_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]], int, list[str]]] = {}
        self._gone_fast_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._new_today_cache: tuple[float, int] | None = None
        self._landing_preview_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._monitor_index = None
        self._monitor_index_at = 0.0
        self._init()

    def connect(self, readonly: bool = False, *, quick: bool = False) -> sqlite3.Connection:
        on_loop = threading.current_thread() is threading.main_thread()
        if quick:
            # WAL-friendly read: do not use mode=ro (it can stall on -shm / checkpoint).
            conn = sqlite3.connect(self.path, timeout=0.2)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=200")
            try:
                conn.execute("PRAGMA query_only=ON")
            except sqlite3.OperationalError:
                pass
        elif readonly:
            uri = f"file:{Path(self.path).resolve().as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=250")
        elif on_loop:
            # Short wait for request handlers so UI fails fast under writer load.
            conn = sqlite3.connect(self.path, timeout=0.08)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=80")
        else:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _connect_bootstrap(self, timeout: float = 0.45) -> sqlite3.Connection:
        """Short wait so uvicorn --reload is not stuck behind a scrape writer."""
        wait = max(0.05, float(timeout))
        conn = sqlite3.connect(self.path, timeout=wait)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={max(50, int(wait * 1000))}")
        return conn

    def _schema_present(self) -> bool:
        try:
            conn = sqlite3.connect(self.path, timeout=0.2)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=200")
            try:
                conn.execute("PRAGMA query_only=ON")
            except sqlite3.OperationalError:
                pass
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='listings' LIMIT 1"
            ).fetchone()
            conn.close()
            return bool(row)
        except sqlite3.Error:
            return False

    def _try_init(self, *, timeout: float, attempts: int) -> bool:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(max(1, int(attempts))):
            conn = self._connect_bootstrap(timeout)
            try:
                with conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
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
                    self._ensure_scrape_schema(conn)
                    self._migrate_monitors(conn)
                    self._ensure_defaults(conn)
                    self._ensure_ping_queue(conn)
                    self._ensure_push(conn)
                    self._ensure_analytics(conn)
                    self._ensure_guest_searches(conn)
                return True
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower():
                    raise
                if attempt == attempts - 1:
                    return False
                time.sleep(0.04 * (attempt + 1))
            finally:
                conn.close()
        if last_error and "locked" not in str(last_error).lower():
            raise last_error
        return False

    def _init(self) -> None:
        if self._schema_present():
            if self._try_init(timeout=0.25, attempts=2):
                return
            threading.Thread(
                target=self._try_init,
                kwargs={"timeout": 5.0, "attempts": 6},
                daemon=True,
                name="db-migrate",
            ).start()
            return
        if self._try_init(timeout=0.5, attempts=8):
            return
        if self._schema_present():
            threading.Thread(
                target=self._try_init,
                kwargs={"timeout": 5.0, "attempts": 6},
                daemon=True,
                name="db-migrate",
            ).start()
            return
        if not self._try_init(timeout=8.0, attempts=6):
            raise sqlite3.OperationalError("database is locked")

    def _ensure_guest_searches(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS guest_searches (
                token TEXT PRIMARY KEY,
                ip TEXT NOT NULL DEFAULT '',
                visitor_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_guest_searches_ip ON guest_searches(ip)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_guest_searches_vid ON guest_searches(visitor_id)")

    def _ensure_analytics(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                visitor_id TEXT,
                path TEXT,
                device TEXT,
                country TEXT,
                country_name TEXT,
                city TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_kind_at ON analytics_events(kind, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_vid ON analytics_events(visitor_id, created_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_presence (
                visitor_id TEXT PRIMARY KEY,
                last_seen TEXT NOT NULL,
                path TEXT,
                device TEXT,
                country TEXT,
                country_name TEXT,
                city TEXT,
                email TEXT,
                name TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_presence_seen ON analytics_presence(last_seen)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(analytics_presence)")}
        if "ip" not in cols:
            conn.execute("ALTER TABLE analytics_presence ADD COLUMN ip TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_presence_ip ON analytics_presence(ip)")

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
            ("listing_key", "TEXT"),
            ("canonical_key", "TEXT"),
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        for column, decl in extras:
            if column not in existing:
                conn.execute(f"ALTER TABLE listings ADD COLUMN {column} {decl}")
        if "sold_notified" not in existing:
            conn.execute("UPDATE listings SET sold_notified = 1 WHERE IFNULL(gone, 0) = 1")
        self._backfill_listing_keys(conn)
        self._ensure_listing_links(conn)
        self._unify_listing_identities(conn)
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

    def _backfill_listing_keys(self, conn: sqlite3.Connection) -> None:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_listing_key ON listings(listing_key)")
        rows = conn.execute(
            "SELECT rowid, url FROM listings WHERE listing_key IS NULL OR listing_key = ''"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE listings SET listing_key = ? WHERE rowid = ?",
                (listing_key(row["url"] or ""), row["rowid"]),
            )

    def _ensure_listing_links(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_links (
                url TEXT PRIMARY KEY,
                url_key TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                portal TEXT NOT NULL,
                native_id TEXT,
                agency TEXT,
                last_seen TEXT,
                gone INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_listing_links_canonical ON listing_links(canonical_key);
            CREATE INDEX IF NOT EXISTS idx_listing_links_url_key ON listing_links(url_key);
            """
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
        if "canonical_key" not in existing:
            conn.execute("ALTER TABLE listings ADD COLUMN canonical_key TEXT")
        cat_cols = {row[1] for row in conn.execute("PRAGMA table_info(catalog_listings)")}
        if cat_cols and "canonical_key" not in cat_cols:
            conn.execute("ALTER TABLE catalog_listings ADD COLUMN canonical_key TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_canonical ON listings(canonical_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings(first_seen)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_locality ON listings(locality)")
        if cat_cols:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_canonical ON catalog_listings(canonical_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_first_seen ON catalog_listings(first_seen)")

    def _upsert_listing_link(self, conn: sqlite3.Connection, listing: Listing | dict[str, Any], canonical: str, *, gone: bool = False) -> None:
        if isinstance(listing, Listing):
            url = listing.url
            native_id = listing.id
            extras = listing.extras
            last_seen = utc_now()
        else:
            url = str(listing.get("url") or "")
            native_id = listing.get("id")
            extras = listing.get("extras")
            last_seen = str(listing.get("last_seen") or utc_now())
        payload = link_payload(url, native_id=native_id, extras=extras, last_seen=last_seen, gone=gone)
        if not payload["url"]:
            return
        conn.execute(
            """
            INSERT INTO listing_links(url, url_key, canonical_key, portal, native_id, agency, last_seen, gone)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                url_key = excluded.url_key,
                canonical_key = excluded.canonical_key,
                portal = excluded.portal,
                native_id = COALESCE(excluded.native_id, listing_links.native_id),
                agency = CASE WHEN excluded.agency != '' THEN excluded.agency ELSE listing_links.agency END,
                last_seen = excluded.last_seen,
                gone = excluded.gone
            """,
            (
                payload["url"],
                payload["url_key"],
                canonical,
                payload["portal"],
                payload["native_id"] or None,
                payload["agency"],
                payload["last_seen"],
                1 if gone else 0,
            ),
        )

    def _listing_dict(self, listing: Listing) -> dict[str, Any]:
        return {
            "url": listing.url,
            "lat": listing.lat,
            "lon": listing.lon,
            "disposition": listing.disposition,
            "area_m2": listing.area_m2,
            "price_czk": listing.price_czk,
            "price_label": listing.price_label,
            "extras": listing.extras or {},
            "id": listing.id,
        }

    def _resolve_canonical(
        self,
        conn: sqlite3.Connection,
        listing: Listing | dict[str, Any],
        *,
        fast: bool = False,
    ) -> str:
        row = listing if isinstance(listing, dict) else self._listing_dict(listing)
        url = str(row.get("url") or "")
        url_key = listing_key(url)
        found = ""
        if url:
            link = conn.execute(
                "SELECT canonical_key FROM listing_links WHERE url = ? OR url_key = ? LIMIT 1",
                (url, url_key),
            ).fetchone()
            if link and link["canonical_key"]:
                found = str(link["canonical_key"])
        if fast and found:
            return found
        if fast and url_key:
            existing = conn.execute(
                "SELECT canonical_key FROM catalog_listings WHERE listing_key = ? LIMIT 1",
                (url_key,),
            ).fetchone()
            if existing and existing["canonical_key"]:
                return str(existing["canonical_key"])
        nearby = self._find_canonical_nearby(conn, row) or ""
        keep = nearby or found or url_canonical(url)
        if found and found != keep:
            self._retarget_canonical(conn, found, keep)
        return keep

    def _retarget_canonical(self, conn: sqlite3.Connection, drop: str, keep: str) -> None:
        if not drop or not keep or drop == keep:
            return
        conn.execute("UPDATE listing_links SET canonical_key = ? WHERE canonical_key = ?", (keep, drop))
        conn.execute("UPDATE listings SET canonical_key = ? WHERE canonical_key = ?", (keep, drop))
        try:
            conn.execute("UPDATE catalog_listings SET canonical_key = ? WHERE canonical_key = ?", (keep, drop))
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO monitor_hits(monitor_id, listing_key, first_matched)
                SELECT monitor_id, ?, first_matched FROM monitor_hits WHERE listing_key = ?
                """,
                (keep, drop),
            )
            conn.execute("DELETE FROM monitor_hits WHERE listing_key = ?", (drop,))
        except sqlite3.OperationalError:
            pass

    def _find_canonical_nearby(self, conn: sqlite3.Connection, row: dict[str, Any]) -> str | None:
        try:
            lat = float(row["lat"])
            lon = float(row["lon"])
        except (TypeError, ValueError, KeyError):
            return None
        box = 0.0005
        candidates = conn.execute(
            """
            SELECT canonical_key, url, lat, lon, disposition, area_m2, price_czk, price_label, extras
            FROM listings
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
              AND IFNULL(canonical_key, '') != ''
            LIMIT 80
            """,
            (lat - box, lat + box, lon - box, lon + box),
        ).fetchall()
        for cand in candidates:
            if same_listing(row, dict(cand)):
                return str(cand["canonical_key"])
        catalog = conn.execute(
            """
            SELECT canonical_key, url, lat, lon, disposition, area_m2, price_czk, price_label, extras
            FROM catalog_listings
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
              AND IFNULL(canonical_key, '') != ''
            LIMIT 80
            """,
            (lat - box, lat + box, lon - box, lon + box),
        ).fetchall()
        for cand in catalog:
            if same_listing(row, dict(cand)):
                return str(cand["canonical_key"])
        return None

    def _links_for(self, conn: sqlite3.Connection, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        keys = [key for key in keys if key]
        if not keys:
            return {}
        holders = ",".join("?" * len(keys))
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in conn.execute(
            f"""
            SELECT * FROM listing_links
            WHERE canonical_key IN ({holders})
            ORDER BY gone, portal, url
            """,
            keys,
        ):
            item = dict(row)
            item["label"] = portal_label(item.get("portal") or "")
            item["gone"] = bool(item.get("gone"))
            grouped.setdefault(str(item["canonical_key"]), []).append(item)
        return grouped

    def _unify_listing_identities(self, conn: sqlite3.Connection) -> None:
        v4 = conn.execute("SELECT value FROM meta WHERE key = 'listing_identity_v4'").fetchone()
        if not v4:
            conn.execute("UPDATE listing_links SET canonical_key = 'url:' || url_key WHERE IFNULL(url_key, '') != ''")
            rows = [dict(row) for row in conn.execute("SELECT rowid AS rid, * FROM listings")]
            assigned: dict[int, str] = {}
            for row in rows:
                assigned[int(row["rid"])] = url_canonical(str(row.get("url") or ""))
            assigned = self._merge_nearby_assigned(rows, assigned, "rid")
            seen_old: dict[str, str] = {}
            for row in rows:
                rid = int(row["rid"])
                new = assigned[rid]
                old = url_canonical(str(row.get("url") or ""))
                conn.execute("UPDATE listings SET canonical_key = ? WHERE rowid = ?", (new, rid))
                self._upsert_listing_link(conn, row, new, gone=bool(row.get("gone")))
                if old != new:
                    seen_old[old] = new
            for old, new in seen_old.items():
                self._retarget_canonical(conn, old, new)
            self._collapse_duplicate_listings(conn)
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('listing_identity_v4', '1')")
            self._backfill_catalog_canonical(conn, merge=True)
            return
        self._backfill_catalog_canonical(conn, merge=False)

    def schedule_identity_relink(self) -> None:
        if getattr(self, "_identity_relink_started", False):
            return
        self._identity_relink_started = True

        def run() -> None:
            time.sleep(8)
            try:
                self.merge_duplicate_listings()
            except Exception as exc:
                print(f"Sloučení duplicit selhalo: {exc}", flush=True)

        threading.Thread(target=run, name="identity-v5", daemon=True).start()

    def _unify_listing_identities_v5(self) -> None:
        self.merge_duplicate_listings()

    def merge_duplicate_listings(self, *, force: bool = False) -> dict[str, int]:
        with self.connect() as conn:
            if not force and conn.execute("SELECT value FROM meta WHERE key = 'listing_identity_v5'").fetchone():
                print("Sloučení duplicit už proběhlo.", flush=True)
                return {"skipped": 1}

        print("Načítám inzeráty ke sloučení…", flush=True)
        listing_rows, catalog_rows = self._load_identity_rows()

        print(
            f"Hledám duplicity v {len(listing_rows)} inzerátech a {len(catalog_rows)} katalogových záznamech…",
            flush=True,
        )
        listing_assigned = {
            int(row["rid"]): str(row.get("canonical_key") or url_canonical(str(row.get("url") or "")))
            for row in listing_rows
        }
        listing_assigned = self._merge_nearby_assigned(listing_rows, listing_assigned, "rid")
        to_relink = sum(
            1
            for row in listing_rows
            if listing_assigned[int(row["rid"])] != str(row.get("canonical_key") or "")
        )
        print(f"Přepojuji {to_relink} inzerátů…", flush=True)
        listing_changed = self._commit_listing_canonicals(listing_rows, listing_assigned)

        catalog_assigned = {
            row["listing_key"]: str(row.get("canonical_key") or url_canonical(str(row.get("url") or "")))
            for row in catalog_rows
        }
        catalog_assigned = self._merge_nearby_assigned(catalog_rows, catalog_assigned, "listing_key")
        to_relink_cat = sum(
            1
            for row in catalog_rows
            if catalog_assigned[row["listing_key"]] != str(row.get("canonical_key") or "")
        )
        print(f"Přepojuji {to_relink_cat} katalogových záznamů…", flush=True)
        catalog_changed = self._commit_catalog_canonicals(catalog_rows, catalog_assigned)

        print("Mažu zdvojené řádky…", flush=True)
        listing_collapsed, catalog_collapsed = self._collapse_duplicates_chunked()

        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('listing_identity_v5', '1')")

        stats = {
            "listings": len(listing_rows),
            "catalog": len(catalog_rows),
            "listings_relinked": listing_changed,
            "catalog_relinked": catalog_changed,
            "listings_collapsed": listing_collapsed,
            "catalog_collapsed": catalog_collapsed,
        }
        print(
            f"Sloučení duplicit je hotové. Přepojené inzeráty: {listing_changed}, "
            f"katalog: {catalog_changed}, smazané duplicity: {listing_collapsed + catalog_collapsed}.",
            flush=True,
        )
        self.patch_dedupe_meta(
            {
                "status": "done",
                "last_kind": "merge",
                "last_run": utc_now(),
                "last_stats": stats,
                "error": "",
            }
        )
        return stats

    def _load_identity_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self.connect() as conn:
            listing_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT rowid AS rid, url, lat, lon, disposition, area_m2, price_czk, price_label,
                           extras, canonical_key, gone, id, last_seen, name, locality
                    FROM listings
                    """
                )
            ]
            catalog_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT listing_key, url, lat, lon, disposition, area_m2, price_czk, price_label,
                           extras, canonical_key, gone, id, last_seen
                    FROM catalog_listings
                    """
                )
            ]
        return listing_rows, catalog_rows

    def dedupe_settings(self) -> dict[str, Any]:
        raw = self.get_meta("dedupe") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        try:
            hour = int(data.get("hour", 3))
        except (TypeError, ValueError):
            hour = 3
        return {
            "enabled": bool(data.get("enabled")),
            "hour": max(0, min(23, hour)),
            "status": str(data.get("status") or "idle"),
            "last_kind": str(data.get("last_kind") or ""),
            "last_run": data.get("last_run") or "",
            "last_attempt": data.get("last_attempt") or "",
            "last_scan": data.get("last_scan") or "",
            "last_stats": data.get("last_stats") if isinstance(data.get("last_stats"), dict) else {},
            "last_scan_stats": data.get("last_scan_stats") if isinstance(data.get("last_scan_stats"), dict) else {},
            "samples": data.get("samples") if isinstance(data.get("samples"), list) else [],
            "error": str(data.get("error") or ""),
        }

    def patch_dedupe_meta(self, updates: dict[str, Any]) -> dict[str, Any]:
        current = self.dedupe_settings()
        current.update(updates)
        self.set_meta("dedupe", json.dumps(current, ensure_ascii=False))
        return current

    def save_dedupe_schedule(self, *, enabled: bool, hour: int) -> dict[str, Any]:
        return self.patch_dedupe_meta({"enabled": bool(enabled), "hour": max(0, min(23, int(hour)))})

    def duplicate_row_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            listings = int(conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0])
            catalog = int(conn.execute("SELECT COUNT(*) FROM catalog_listings").fetchone()[0])
            listing_dup = conn.execute(
                """
                SELECT COUNT(*) AS groups, COALESCE(SUM(n - 1), 0) AS extra
                FROM (
                    SELECT COUNT(*) AS n
                    FROM listings
                    WHERE canonical_key IS NOT NULL AND canonical_key != ''
                    GROUP BY monitor_id, canonical_key
                    HAVING n > 1
                )
                """
            ).fetchone()
            catalog_dup = conn.execute(
                """
                SELECT COUNT(*) AS groups, COALESCE(SUM(n - 1), 0) AS extra
                FROM (
                    SELECT COUNT(*) AS n
                    FROM catalog_listings
                    WHERE canonical_key IS NOT NULL AND canonical_key != ''
                    GROUP BY canonical_key
                    HAVING n > 1
                )
                """
            ).fetchone()
        return {
            "listings": listings,
            "catalog": catalog,
            "listing_dup_groups": int(listing_dup["groups"] or 0),
            "listing_dup_extra": int(listing_dup["extra"] or 0),
            "catalog_dup_groups": int(catalog_dup["groups"] or 0),
            "catalog_dup_extra": int(catalog_dup["extra"] or 0),
        }

    def preview_duplicate_listings(self, *, sample_limit: int = 12) -> dict[str, Any]:
        listing_rows, catalog_rows = self._load_identity_rows()
        listing_assigned = {
            int(row["rid"]): str(row.get("canonical_key") or url_canonical(str(row.get("url") or "")))
            for row in listing_rows
        }
        listing_assigned = self._merge_nearby_assigned(listing_rows, listing_assigned, "rid")
        catalog_assigned = {
            row["listing_key"]: str(row.get("canonical_key") or url_canonical(str(row.get("url") or "")))
            for row in catalog_rows
        }
        catalog_assigned = self._merge_nearby_assigned(catalog_rows, catalog_assigned, "listing_key")
        listing_relink = sum(
            1
            for row in listing_rows
            if listing_assigned[int(row["rid"])] != str(row.get("canonical_key") or "")
        )
        catalog_relink = sum(
            1
            for row in catalog_rows
            if catalog_assigned[row["listing_key"]] != str(row.get("canonical_key") or "")
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in listing_rows:
            new = listing_assigned[int(row["rid"])]
            old = str(row.get("canonical_key") or "")
            if new == old:
                continue
            grouped.setdefault(new, []).append(row)
        samples = []
        for key, members in sorted(grouped.items(), key=lambda item: -len(item[1]))[:sample_limit]:
            first = members[0]
            samples.append(
                {
                    "canonical": key,
                    "n": len(members),
                    "name": str(first.get("name") or first.get("locality") or first.get("url") or "")[:120],
                    "disposition": str(first.get("disposition") or ""),
                    "area_m2": first.get("area_m2"),
                    "price_czk": first.get("price_czk"),
                    "urls": [str(item.get("url") or "") for item in members[:4]],
                }
            )
        stats = {
            "listings": len(listing_rows),
            "catalog": len(catalog_rows),
            "listings_relinkable": listing_relink,
            "catalog_relinkable": catalog_relink,
            "sample_groups": len(grouped),
        }
        counts = self.duplicate_row_counts()
        self.patch_dedupe_meta(
            {
                "status": "done",
                "last_kind": "scan",
                "last_scan": utc_now(),
                "last_scan_stats": {**stats, **counts},
                "samples": samples,
                "error": "",
            }
        )
        return {"stats": {**stats, **counts}, "samples": samples}

    def _write_retry(self, fn) -> None:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(8):
            conn = self.connect()
            conn.execute("PRAGMA busy_timeout=8000")
            try:
                with conn:
                    fn(conn)
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.25 * (attempt + 1))
            finally:
                conn.close()
        if last_error:
            raise last_error

    def _commit_listing_canonicals(self, rows: list[dict[str, Any]], assigned: dict[Any, str]) -> int:
        changes: list[tuple[dict[str, Any], str, str]] = []
        remaps: dict[str, str] = {}
        for row in rows:
            rid = int(row["rid"])
            new = assigned[rid]
            old = str(row.get("canonical_key") or "")
            if new != old:
                changes.append((row, old, new))
                if old:
                    remaps[old] = new
        batch = 40
        for index in range(0, len(changes), batch):
            chunk = changes[index : index + batch]

            def apply(conn: sqlite3.Connection, chunk=chunk) -> None:
                for row, _old, new in chunk:
                    conn.execute("UPDATE listings SET canonical_key = ? WHERE rowid = ?", (new, int(row["rid"])))
                    self._upsert_listing_link(conn, row, new, gone=bool(row.get("gone")))

            self._write_retry(apply)
            if index and index % 400 == 0:
                print(f"  inzeráty {min(index + batch, len(changes))}/{len(changes)}", flush=True)
            time.sleep(0.01)
        remap_items = list(remaps.items())
        for index in range(0, len(remap_items), batch):
            chunk = remap_items[index : index + batch]

            def apply(conn: sqlite3.Connection, chunk=chunk) -> None:
                for old, new in chunk:
                    self._retarget_canonical(conn, old, new)

            self._write_retry(apply)
            time.sleep(0.01)
        return len(changes)

    def _commit_catalog_canonicals(self, rows: list[dict[str, Any]], assigned: dict[Any, str]) -> int:
        changes: list[tuple[dict[str, Any], str, str]] = []
        remaps: dict[str, str] = {}
        for row in rows:
            new = assigned[row["listing_key"]]
            old = str(row.get("canonical_key") or "")
            if new != old:
                changes.append((row, old, new))
                if old:
                    remaps[old] = new
        batch = 40
        for index in range(0, len(changes), batch):
            chunk = changes[index : index + batch]

            def apply(conn: sqlite3.Connection, chunk=chunk) -> None:
                for row, _old, new in chunk:
                    conn.execute(
                        "UPDATE catalog_listings SET canonical_key = ? WHERE listing_key = ?",
                        (new, row["listing_key"]),
                    )
                    self._upsert_listing_link(conn, row, new, gone=bool(row.get("gone")))

            self._write_retry(apply)
            if index and index % 800 == 0:
                print(f"  katalog {min(index + batch, len(changes))}/{len(changes)}", flush=True)
            time.sleep(0.01)
        remap_items = list(remaps.items())
        for index in range(0, len(remap_items), batch):
            chunk = remap_items[index : index + batch]

            def apply(conn: sqlite3.Connection, chunk=chunk) -> None:
                for old, new in chunk:
                    self._retarget_canonical(conn, old, new)

            self._write_retry(apply)
            time.sleep(0.01)
        return len(changes)

    def _collapse_duplicates_chunked(self) -> tuple[int, int]:
        listing_count = 0
        catalog_count = 0

        def collapse_listings(conn: sqlite3.Connection) -> None:
            nonlocal listing_count
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_monitor_listing ON events(monitor_id, listing_id)")
            conn.execute("DROP TABLE IF EXISTS listing_keep")
            conn.execute("DROP TABLE IF EXISTS listing_map")
            conn.execute(
                """
                CREATE TEMP TABLE listing_keep AS
                SELECT monitor_id, canonical_key, MIN(rowid) AS keep_rid
                FROM listings
                WHERE canonical_key IS NOT NULL AND canonical_key != ''
                GROUP BY monitor_id, canonical_key
                HAVING COUNT(*) > 1
                """
            )
            listing_count = conn.execute("SELECT COUNT(*) FROM listing_keep").fetchone()[0]
            conn.execute(
                """
                CREATE TEMP TABLE listing_map AS
                SELECT l.monitor_id AS monitor_id, l.id AS extra_id, k.id AS keep_id, l.rowid AS extra_rid
                FROM listings l
                JOIN listing_keep g ON g.monitor_id = l.monitor_id AND g.canonical_key = l.canonical_key
                JOIN listings k ON k.rowid = g.keep_rid
                WHERE l.rowid != g.keep_rid
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO listing_photos(monitor_id, listing_id, url, sort_order)
                SELECT m.monitor_id, m.keep_id, p.url, p.sort_order
                FROM listing_photos p
                JOIN listing_map m ON m.monitor_id = p.monitor_id AND m.extra_id = p.listing_id
                WHERE m.extra_id != m.keep_id
                """
            )
            conn.execute(
                """
                DELETE FROM listing_photos
                WHERE EXISTS (
                    SELECT 1 FROM listing_map m
                    WHERE m.monitor_id = listing_photos.monitor_id
                      AND m.extra_id = listing_photos.listing_id
                      AND m.extra_id != m.keep_id
                )
                """
            )
            conn.execute(
                """
                UPDATE price_history
                SET listing_id = (
                    SELECT keep_id FROM listing_map m
                    WHERE m.monitor_id = price_history.monitor_id AND m.extra_id = price_history.listing_id
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM listing_map m
                    WHERE m.monitor_id = price_history.monitor_id AND m.extra_id = price_history.listing_id
                      AND m.extra_id != m.keep_id
                )
                """
            )
            conn.execute(
                """
                UPDATE events
                SET listing_id = (
                    SELECT keep_id FROM listing_map m
                    WHERE m.monitor_id = events.monitor_id AND m.extra_id = events.listing_id
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM listing_map m
                    WHERE m.monitor_id = events.monitor_id AND m.extra_id = events.listing_id
                      AND m.extra_id != m.keep_id
                )
                """
            )
            conn.execute("DELETE FROM listings WHERE rowid IN (SELECT extra_rid FROM listing_map WHERE extra_id = keep_id)")
            conn.execute(
                """
                DELETE FROM listings
                WHERE EXISTS (
                    SELECT 1 FROM listing_map m
                    WHERE m.monitor_id = listings.monitor_id AND m.extra_id = listings.id AND m.extra_id != m.keep_id
                )
                """
            )

        def collapse_catalog(conn: sqlite3.Connection) -> None:
            nonlocal catalog_count
            conn.execute("DROP TABLE IF EXISTS catalog_extra")
            conn.execute(
                """
                CREATE TEMP TABLE catalog_extra AS
                SELECT listing_key, canonical_key
                FROM (
                    SELECT listing_key, canonical_key,
                           ROW_NUMBER() OVER (
                               PARTITION BY canonical_key
                               ORDER BY first_seen ASC, listing_key ASC
                           ) AS rn
                    FROM catalog_listings
                    WHERE canonical_key IS NOT NULL AND canonical_key != ''
                )
                WHERE rn > 1
                """
            )
            catalog_count = conn.execute(
                "SELECT COUNT(DISTINCT canonical_key) FROM catalog_extra"
            ).fetchone()[0]
            conn.execute(
                """
                INSERT OR IGNORE INTO monitor_hits(monitor_id, listing_key, first_matched)
                SELECT h.monitor_id, e.canonical_key, h.first_matched
                FROM monitor_hits h
                JOIN catalog_extra e ON e.listing_key = h.listing_key
                """
            )
            conn.execute(
                """
                DELETE FROM monitor_hits
                WHERE listing_key IN (SELECT listing_key FROM catalog_extra)
                """
            )
            conn.execute(
                """
                DELETE FROM catalog_listings
                WHERE listing_key IN (SELECT listing_key FROM catalog_extra)
                """
            )

        print("  collapse listings…", flush=True)
        self._write_retry(collapse_listings)
        print(f"  listing groups: {listing_count}", flush=True)
        print("  collapse catalog…", flush=True)
        self._write_retry(collapse_catalog)
        print(f"  catalog groups: {catalog_count}", flush=True)
        return listing_count, catalog_count

    def _relink_nearby_identities(self, conn: sqlite3.Connection) -> None:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT rowid AS rid, url, lat, lon, disposition, area_m2, price_czk, price_label,
                       extras, canonical_key, gone, id, last_seen
                FROM listings
                """
            )
        ]
        if not rows:
            return
        assigned = {
            int(row["rid"]): str(row.get("canonical_key") or url_canonical(str(row.get("url") or "")))
            for row in rows
        }
        assigned = self._merge_nearby_assigned(rows, assigned, "rid")
        seen_old: dict[str, str] = {}
        for row in rows:
            rid = int(row["rid"])
            new = assigned[rid]
            old = str(row.get("canonical_key") or "")
            if new == old:
                continue
            conn.execute("UPDATE listings SET canonical_key = ? WHERE rowid = ?", (new, rid))
            self._upsert_listing_link(conn, row, new, gone=bool(row.get("gone")))
            if old:
                seen_old[old] = new
        for old, new in seen_old.items():
            self._retarget_canonical(conn, old, new)
        self._collapse_duplicate_listings(conn)
        self._collapse_duplicate_catalog(conn)

    def _merge_nearby_assigned(self, rows: list[dict[str, Any]], assigned: dict[Any, str], id_field: str) -> dict[Any, str]:
        remap: dict[str, str] = {}

        def resolve(value: str) -> str:
            seen: set[str] = set()
            while value in remap and value not in seen:
                seen.add(value)
                value = remap[value]
            return value

        def prefer(left: str, right: str) -> tuple[str, str]:
            if left.startswith("g:") or not right.startswith("g:"):
                return left, right
            return right, left

        by_fp: dict[str, list[Any]] = {}
        for row in rows:
            token = fingerprint(row)
            if token:
                by_fp.setdefault(token, []).append(row[id_field])
        for members in by_fp.values():
            if len(members) < 2:
                continue
            keep = resolve(assigned[members[0]])
            for other_id in members[1:]:
                target = resolve(assigned[other_id])
                if target == keep:
                    continue
                keep, drop = prefer(keep, target)
                remap[drop] = keep

        cells: dict[tuple[float, float], list[Any]] = {}
        by_id: dict[Any, dict[str, Any]] = {}
        for row in rows:
            key = row[id_field]
            by_id[key] = row
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError, KeyError):
                continue
            cells.setdefault((round(lat, 3), round(lon, 3)), []).append(key)

        checked = 0
        for row in rows:
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
            except (TypeError, ValueError, KeyError):
                continue
            current = resolve(assigned[row[id_field]])
            gy, gx = round(lat, 3), round(lon, 3)
            nearby_ids: list[Any] = []
            for dy in (-0.001, 0.0, 0.001):
                for dx in (-0.001, 0.0, 0.001):
                    nearby_ids.extend(cells.get((round(gy + dy, 3), round(gx + dx, 3)), []))
            for other_id in nearby_ids:
                checked += 1
                if checked % 5000 == 0:
                    time.sleep(0.001)
                if other_id == row[id_field]:
                    continue
                other = by_id[other_id]
                if not same_listing(row, other):
                    continue
                target = resolve(assigned[other_id])
                if target == current:
                    continue
                keep, drop = prefer(current, target)
                remap[drop] = keep
                current = keep
        for key, value in list(assigned.items()):
            assigned[key] = resolve(value)
        return assigned

    def _backfill_catalog_canonical(self, conn: sqlite3.Connection, *, merge: bool = False) -> None:
        try:
            missing = conn.execute(
                "SELECT listing_key, url, lat, lon, disposition, area_m2, price_czk, price_label, extras, gone, id, last_seen FROM catalog_listings WHERE IFNULL(canonical_key, '') = ''"
            ).fetchall()
        except sqlite3.OperationalError:
            return
        for row in missing:
            item = dict(row)
            canon = url_canonical(str(item.get("url") or ""))
            link = conn.execute(
                "SELECT canonical_key FROM listing_links WHERE url = ? OR url_key = ? LIMIT 1",
                (item.get("url") or "", listing_key(item.get("url") or "")),
            ).fetchone()
            if link and link["canonical_key"]:
                canon = str(link["canonical_key"])
            conn.execute(
                "UPDATE catalog_listings SET canonical_key = ? WHERE listing_key = ?",
                (canon, item["listing_key"]),
            )
            self._upsert_listing_link(conn, item, canon, gone=bool(item.get("gone")))
        if not merge:
            return
        catalog_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT listing_key, url, lat, lon, disposition, area_m2, price_czk, price_label, extras, canonical_key FROM catalog_listings"
            )
        ]
        cat_assigned = {
            row["listing_key"]: str(row.get("canonical_key") or url_canonical(str(row.get("url") or "")))
            for row in catalog_rows
        }
        cat_assigned = self._merge_nearby_assigned(catalog_rows, cat_assigned, "listing_key")
        for row in catalog_rows:
            new = cat_assigned[row["listing_key"]]
            if new != (row.get("canonical_key") or ""):
                conn.execute(
                    "UPDATE catalog_listings SET canonical_key = ? WHERE listing_key = ?",
                    (new, row["listing_key"]),
                )
                self._upsert_listing_link(conn, row, new, gone=False)
        self._collapse_duplicate_catalog(conn)

    def _collapse_duplicate_listings(self, conn: sqlite3.Connection) -> None:
        groups = conn.execute(
            """
            SELECT monitor_id, canonical_key, COUNT(*) AS n
            FROM listings
            WHERE IFNULL(canonical_key, '') != ''
            GROUP BY monitor_id, canonical_key
            HAVING n > 1
            """
        ).fetchall()
        for group in groups:
            members = conn.execute(
                """
                SELECT rowid AS rid, id, first_seen FROM listings
                WHERE monitor_id = ? AND canonical_key = ?
                ORDER BY first_seen ASC, rowid ASC
                """,
                (group["monitor_id"], group["canonical_key"]),
            ).fetchall()
            keep = members[0]
            keep_id = int(keep["id"])
            for extra in members[1:]:
                extra_id = int(extra["id"])
                if extra_id == keep_id:
                    conn.execute("DELETE FROM listings WHERE rowid = ?", (extra["rid"],))
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO listing_photos(monitor_id, listing_id, url, sort_order)
                    SELECT monitor_id, ?, url, sort_order FROM listing_photos
                    WHERE monitor_id = ? AND listing_id = ?
                    """,
                    (keep_id, group["monitor_id"], extra_id),
                )
                conn.execute(
                    "DELETE FROM listing_photos WHERE monitor_id = ? AND listing_id = ?",
                    (group["monitor_id"], extra_id),
                )
                conn.execute(
                    "UPDATE price_history SET listing_id = ? WHERE monitor_id = ? AND listing_id = ?",
                    (keep_id, group["monitor_id"], extra_id),
                )
                conn.execute(
                    "UPDATE events SET listing_id = ? WHERE monitor_id = ? AND listing_id = ?",
                    (keep_id, group["monitor_id"], extra_id),
                )
                conn.execute("DELETE FROM listings WHERE monitor_id = ? AND id = ?", (group["monitor_id"], extra_id))

    def _collapse_duplicate_catalog(self, conn: sqlite3.Connection) -> None:
        groups = conn.execute(
            """
            SELECT canonical_key, COUNT(*) AS n
            FROM catalog_listings
            WHERE IFNULL(canonical_key, '') != ''
            GROUP BY canonical_key
            HAVING n > 1
            """
        ).fetchall()
        for group in groups:
            members = conn.execute(
                """
                SELECT listing_key, first_seen FROM catalog_listings
                WHERE canonical_key = ?
                ORDER BY first_seen ASC, listing_key ASC
                """,
                (group["canonical_key"],),
            ).fetchall()
            keep = members[0]["listing_key"]
            canon = group["canonical_key"]
            for extra in members[1:]:
                extra_key = extra["listing_key"]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO monitor_hits(monitor_id, listing_key, first_matched)
                    SELECT monitor_id, ?, first_matched FROM monitor_hits WHERE listing_key = ?
                    """,
                    (canon, extra_key),
                )
                conn.execute("DELETE FROM monitor_hits WHERE listing_key = ?", (extra_key,))
                conn.execute("DELETE FROM catalog_listings WHERE listing_key = ?", (extra_key,))

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
        if conn.execute("SELECT id FROM monitors LIMIT 1").fetchone():
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('monitors_initialized', '1')")
        elif not conn.execute("SELECT value FROM meta WHERE key = 'monitors_initialized'").fetchone():
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
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('monitors_initialized', '1')")
        migrated = conn.execute("SELECT value FROM meta WHERE key = 'monitor_portals_v1'").fetchone()
        if not migrated:
            conn.execute("UPDATE monitors SET portals = 'all', seeded = 0 WHERE id = 'default'")
            conn.execute("UPDATE monitors SET enabled = 0 WHERE id = 'bezrealitky'")
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('monitor_portals_v1', '1')")
        crossed = conn.execute("SELECT value FROM meta WHERE key = 'monitor_portals_v2'").fetchone()
        if not crossed:
            conn.execute("UPDATE monitors SET portals = 'all', seeded = 0 WHERE id = 'default'")
            conn.execute("UPDATE monitors SET enabled = 0 WHERE id = 'bezrealitky'")
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('monitor_portals_v2', '1')")
        unified = conn.execute("SELECT value FROM meta WHERE key = 'monitor_portals_v3'").fetchone()
        if not unified:
            conn.execute("UPDATE monitors SET portals = 'all', seeded = 0 WHERE id = 'default'")
            enabled = conn.execute("SELECT COUNT(*) AS n FROM monitors WHERE enabled = 1").fetchone()["n"]
            if enabled and enabled > 1:
                conn.execute(
                    """
                    UPDATE monitors SET enabled = 0
                    WHERE id = 'bezrealitky'
                       OR lower(name) IN ('bezrealitky praha', 'bezrealitky')
                    """
                )
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('monitor_portals_v3', '1')")

    def _ensure_ping_queue(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS discord_channel_sent (
                listing_key TEXT NOT NULL,
                channel_key TEXT NOT NULL,
                ping_type TEXT NOT NULL,
                listing_url TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                monitor_id TEXT,
                kind TEXT,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (listing_key, channel_key, ping_type)
            );
            CREATE TABLE IF NOT EXISTS ping_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                listing_key TEXT NOT NULL,
                channel_key TEXT NOT NULL,
                ping_type TEXT NOT NULL,
                webhook_url TEXT NOT NULL,
                listing_url TEXT NOT NULL,
                listing_id INTEGER,
                monitor_id TEXT,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ping_queue_pending_uniq
                ON ping_queue(listing_key, channel_key, ping_type)
                WHERE status IN ('queued', 'sending');
            CREATE INDEX IF NOT EXISTS ping_queue_status_idx ON ping_queue(status, id);
            """
        )
        conn.execute("UPDATE ping_queue SET status = 'queued' WHERE status = 'sending'")
        conn.execute(
            """
            UPDATE ping_queue
            SET status = 'failed', error = 'Ukázkový Discord webhook ID/TOKEN. Propoj Discord přes /link nebo nastav skutečnou DISCORD_WEBHOOK_URL.'
            WHERE status IN ('queued', 'sending')
              AND (webhook_url LIKE '%webhooks/ID/TOKEN%' OR webhook_url LIKE '%webhooks/id/token%')
            """
        )
        self._backfill_channel_sent(conn)

    def _ensure_push(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                endpoint TEXT PRIMARY KEY,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                user_agent TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

    def _backfill_channel_sent(self, conn: sqlite3.Connection) -> None:
        existing = conn.execute("SELECT value FROM meta WHERE key = 'ping_sent_backfill'").fetchone()
        if existing and existing["value"] == "1":
            return
        rows = conn.execute(
            """
            SELECT listings.url, listings.id, listings.monitor_id, listings.last_kind,
                   listings.first_seen, monitors.search_url, monitors.webhook_url
            FROM listings
            LEFT JOIN monitors ON monitors.id = listings.monitor_id
            WHERE listings.notified = 1 AND IFNULL(listings.url, '') != ''
            """
        ).fetchall()
        now = utc_now()
        for row in rows:
            webhook = (row["webhook_url"] or "").strip() or webhook_for(row["search_url"] or "")
            if not webhook:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO discord_channel_sent(
                    listing_key, channel_key, ping_type, listing_url, webhook_url,
                    monitor_id, kind, sent_at
                ) VALUES (?, ?, 'listing', ?, ?, ?, ?, ?)
                """,
                (
                    listing_key(row["url"]),
                    channel_key(webhook),
                    row["url"],
                    webhook,
                    row["monitor_id"],
                    row["last_kind"] or "new",
                    row["first_seen"] or now,
                ),
            )
        sold_webhook = (config.SOLD_WEBHOOK_URL or "").strip()
        if sold_webhook:
            sold_rows = conn.execute(
                """
                SELECT url, monitor_id, last_kind, first_seen
                FROM listings
                WHERE IFNULL(sold_notified, 0) = 1 AND IFNULL(url, '') != ''
                """
            ).fetchall()
            for row in sold_rows:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO discord_channel_sent(
                        listing_key, channel_key, ping_type, listing_url, webhook_url,
                        monitor_id, kind, sent_at
                    ) VALUES (?, ?, 'sold', ?, ?, ?, 'sold', ?)
                    """,
                    (
                        listing_key(row["url"]),
                        channel_key(sold_webhook),
                        row["url"],
                        sold_webhook,
                        row["monitor_id"],
                        row["first_seen"] or now,
                    ),
                )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('ping_sent_backfill', '1') ON CONFLICT(key) DO UPDATE SET value = '1'"
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
            tracked, today = self._monitor_count_maps(conn)
        return [
            self._monitor_row(row, tracked=tracked.get(row["id"], 0), new_today=today.get(row["id"], 0))
            for row in rows
        ]

    def list_monitors_light(self) -> list[dict[str, Any]]:
        """Monitor-loop view without expensive aggregate counts."""
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM monitors ORDER BY created_at").fetchall()
        return [self._monitor_row(row) for row in rows]

    def _monitor_count_maps(self, conn: sqlite3.Connection) -> tuple[dict[str, int], dict[str, int]]:
        hits = {
            str(row[0]): int(row[1])
            for row in conn.execute("SELECT monitor_id, COUNT(*) FROM monitor_hits GROUP BY monitor_id")
        }
        listings = {
            str(row[0]): int(row[1])
            for row in conn.execute("SELECT monitor_id, COUNT(*) FROM listings GROUP BY monitor_id")
        }
        tracked = dict(listings)
        tracked.update(hits)
        identity = listing_identity_sql()
        today = {
            str(row[0]): int(row[1])
            for row in conn.execute(
                f"""
                SELECT monitor_id, COUNT(*) FROM (
                    SELECT events.monitor_id AS monitor_id, {identity} AS ident
                    FROM events
                    JOIN listings ON listings.id = events.listing_id AND listings.monitor_id = events.monitor_id
                    WHERE events.kind IN ('new', 'changed') AND events.created_at >= ?
                    GROUP BY events.monitor_id, ident
                )
                GROUP BY monitor_id
                """,
                (local_day_start(),),
            )
        }
        return tracked, today

    def get_monitor(self, monitor_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,)).fetchone()
        return self._monitor_row(row) if row else None

    def enabled_monitor_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM monitors WHERE enabled = 1").fetchone()[0])

    def disable_monitors_over_limit(self, limit: int | None) -> list[str]:
        if limit is None:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, name FROM monitors WHERE enabled = 1 ORDER BY created_at, id"
            ).fetchall()
            extras = rows[int(limit) :]
            names = []
            for row in extras:
                conn.execute("UPDATE monitors SET enabled = 0 WHERE id = ?", (row["id"],))
                names.append(row["name"] or row["id"])
            return names

    def save_monitor(self, payload: dict[str, Any]) -> dict[str, Any]:
        monitor_id = payload.get("id") or uuid.uuid4().hex[:10]
        existing = self.get_monitor(monitor_id)
        if existing is None:
            from app.billing import watch_limit_for

            limit = watch_limit_for(self)
            enabled = self.enabled_monitor_count()
            if limit is not None and enabled >= limit:
                raise ValueError(f"Limit tarifu je {limit} aktivních hlídacích psů. Upgradujte plán.")
        elif payload.get("enabled", True) and not existing.get("enabled"):
            from app.billing import watch_limit_for

            limit = watch_limit_for(self)
            if limit is not None and self.enabled_monitor_count() >= limit:
                raise ValueError("Limit tarifu je naplněný. Upgradujte plán, abyste tohoto hlídacího psa znovu aktivovali.")
        now = utc_now()
        search_url = (payload.get("search_url") or "").strip()
        from app.catalog_sync import normalize_portals

        if "portals" in payload:
            portals = normalize_portals(payload.get("portals"))
        else:
            portals = normalize_portals((existing or {}).get("portals"))
        webhook = self.discord_webhook_url() or (payload.get("webhook_url") or "").strip() or webhook_for(search_url)
        interval = payload.get("interval_sec")
        try:
            interval_sec = max(20, int(interval)) if interval not in (None, "") else None
        except (TypeError, ValueError):
            interval_sec = None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, interval_sec, portals, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    search_url = excluded.search_url,
                    webhook_url = excluded.webhook_url,
                    template_id = excluded.template_id,
                    enabled = excluded.enabled,
                    interval_sec = excluded.interval_sec,
                    portals = excluded.portals
                """,
                (
                    monitor_id,
                    (payload.get("name") or "Monitor").strip(),
                    search_url,
                    webhook,
                    payload.get("template_id") or "default",
                    1 if payload.get("enabled", True) else 0,
                    interval_sec,
                    portals,
                    now,
                ),
            )
        url_changed = (existing is None) or existing.get("search_url") != search_url or existing.get("portals") != portals
        saved = self.get_monitor(monitor_id)
        assert saved
        self.attach_monitor_live_jobs(saved)
        if url_changed:
            self.seed_monitor_from_catalog(saved)
        self.set_monitor_seeded(monitor_id, True)
        saved = self.get_monitor(monitor_id)
        assert saved
        return saved

    def delete_monitor(self, monitor_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
            conn.execute("DELETE FROM listings WHERE monitor_id = ?", (monitor_id,))
            conn.execute("DELETE FROM events WHERE monitor_id = ?", (monitor_id,))
            conn.execute("DELETE FROM monitor_jobs WHERE monitor_id = ?", (monitor_id,))
            conn.execute("DELETE FROM monitor_hits WHERE monitor_id = ?", (monitor_id,))
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('monitors_initialized', '1')")

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
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                conn = self._connect_bootstrap() if attempt else self.connect(readonly=True)
                try:
                    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
                    return row["value"] if row else None
                finally:
                    conn.close()
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower() or attempt == 5:
                    break
                time.sleep(0.1 * (attempt + 1))
            except Exception as exc:
                last_error = exc
                break
        if last_error and "locked" in str(last_error).lower():
            return None
        if last_error:
            raise last_error
        return None

    def set_meta(self, key: str, value: str | None) -> None:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(10):
            conn = self._connect_bootstrap()
            try:
                with conn:
                    if value is None:
                        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
                    else:
                        conn.execute(
                            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (key, value),
                        )
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower() or attempt == 9:
                    raise
                time.sleep(0.15 * (attempt + 1))
            finally:
                conn.close()
        if last_error:
            raise last_error

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
        raw_prefs = self.get_meta("watch_prefs") or "{}"
        try:
            watch_prefs = json.loads(raw_prefs)
        except json.JSONDecodeError:
            watch_prefs = {}
        if not isinstance(watch_prefs, dict):
            watch_prefs = {}
        return {
            "digest_hour": max(0, min(23, hour)),
            "digest_webhook": "" if self.discord_webhook_url() else (self.get_meta("digest_webhook") or "").strip(),
            "digest_last": self.get_meta("digest_last"),
            "commute_points": points[:2],
            "watch_prefs": watch_prefs,
            "notify": self.notify_prefs(),
            "push_devices": self.push_subscription_count(),
        }

    def notify_prefs(self) -> dict[str, Any]:
        defaults = {
            "discord": True,
            "push": False,
            "email": True,
            "whatsapp": False,
            "instant": True,
            "quiet": False,
            "quietFrom": "22:00",
            "quietTo": "07:00",
            "ntNew": True,
            "ntPrice": True,
            "ntExpire": True,
            "ntDigest": True,
            "ntTips": False,
        }
        raw = self.get_meta("notify_prefs") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        out = {**defaults, **data}
        out["discord"] = bool(out.get("discord"))
        out["push"] = bool(out.get("push"))
        out["email"] = bool(out.get("email"))
        out["whatsapp"] = False
        out["instant"] = bool(out.get("instant"))
        out["quiet"] = bool(out.get("quiet"))
        for key in ("ntNew", "ntPrice", "ntExpire", "ntDigest", "ntTips"):
            out[key] = bool(out.get(key))
        out["quietFrom"] = str(out.get("quietFrom") or "22:00")[:5]
        out["quietTo"] = str(out.get("quietTo") or "07:00")[:5]
        return out

    def save_notify_prefs(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        current = self.notify_prefs()
        if isinstance(payload, dict):
            for key in current:
                if key in payload:
                    current[key] = payload[key]
        current["whatsapp"] = False
        current["email"] = bool(current.get("email"))
        self.set_meta("notify_prefs", json.dumps(current, ensure_ascii=False))
        return current

    def save_push_subscription(self, payload: dict[str, Any], user_agent: str = "") -> None:
        endpoint = str(payload.get("endpoint") or "").strip()
        keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
        p256dh = str(keys.get("p256dh") or "").strip()
        auth = str(keys.get("auth") or "").strip()
        if not endpoint or not p256dh or not auth:
            raise ValueError("Neplatná push subscription")
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(6):
            try:
                with self.connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO push_subscriptions(endpoint, p256dh, auth, user_agent, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(endpoint) DO UPDATE SET
                            p256dh = excluded.p256dh,
                            auth = excluded.auth,
                            user_agent = excluded.user_agent
                        """,
                        (endpoint, p256dh, auth, (user_agent or "")[:240], utc_now()),
                    )
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                if "locked" not in str(exc).lower() or attempt == 5:
                    raise
                time.sleep(0.25 * (attempt + 1))
        if last_error:
            raise last_error

    def delete_push_subscription(self, endpoint: str) -> None:
        endpoint = (endpoint or "").strip()
        if not endpoint:
            return
        with self.connect() as conn:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def list_push_subscriptions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions").fetchall()
        return [dict(row) for row in rows]

    def push_subscription_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0])

    def billing_record(self) -> dict[str, Any]:
        raw = self.get_meta("billing") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {}

    def save_billing_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = {key: value for key, value in payload.items() if value is not None}
        self.set_meta("billing", json.dumps(data, ensure_ascii=False))
        return self.billing_record()

    def discord_webhook_url(self) -> str:
        raw = self.get_meta("account") or "{}"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            return ""
        return usable_discord_webhook(data.get("discord_webhook_url") or "")

    def notify_webhook(self, search_url: str = "", monitor_webhook: str | None = None, *, sold: bool = False) -> str:
        linked = self.discord_webhook_url()
        if linked:
            return linked
        if sold:
            return usable_discord_webhook(config.SOLD_WEBHOOK_URL)
        return usable_discord_webhook(webhook_for(search_url, monitor_webhook))

    def apply_discord_webhook(self, url: str) -> None:
        hooked = (url or "").strip()
        if not hooked:
            return
        with self.connect() as conn:
            conn.execute("UPDATE monitors SET webhook_url = ?", (hooked,))
        self.set_meta("digest_webhook", hooked)

    def digest_webhook(self) -> str:
        return (
            self.discord_webhook_url()
            or usable_discord_webhook(self.get_meta("digest_webhook") or "")
            or usable_discord_webhook(config.DISCORD_WEBHOOK_URL)
        )

    def save_app_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "digest_hour" in payload:
            try:
                hour = max(0, min(23, int(payload.get("digest_hour"))))
            except (TypeError, ValueError):
                hour = 8
            self.set_meta("digest_hour", str(hour))
        if "digest_webhook" in payload and not self.discord_webhook_url():
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
        if "watch_prefs" in payload:
            prefs = payload.get("watch_prefs") or {}
            if not isinstance(prefs, dict):
                prefs = {}
            self.set_meta("watch_prefs", json.dumps(prefs, ensure_ascii=False))
        if "notify" in payload:
            self.save_notify_prefs(payload.get("notify") if isinstance(payload.get("notify"), dict) else {})
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
                hits = conn.execute(
                    "SELECT COUNT(*) FROM monitor_hits WHERE monitor_id = ?", (monitor_id,)
                ).fetchone()[0]
                if hits:
                    return int(hits)
                return int(
                    conn.execute("SELECT COUNT(*) FROM listings WHERE monitor_id = ?", (monitor_id,)).fetchone()[0]
                )
            catalog = conn.execute("SELECT COUNT(*) FROM catalog_listings").fetchone()[0]
            if catalog:
                return int(catalog)
            identity = listing_identity_sql()
            return int(conn.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM listings GROUP BY {identity})").fetchone()[0])

    def new_today_count(self, monitor_id: str | None = None) -> int:
        since = local_day_start()
        identity = listing_identity_sql()
        sql = f"""
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM events
                JOIN listings ON listings.id = events.listing_id AND listings.monitor_id = events.monitor_id
                WHERE events.kind IN ('new', 'changed') AND events.created_at >= ?
        """
        params: list[Any] = [since]
        if monitor_id:
            sql += " AND events.monitor_id = ?"
            params.append(monitor_id)
        sql += f" GROUP BY {identity})"
        with self.connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def catalog_new_today_count(self) -> int:
        now = time.monotonic()
        cached = self._new_today_cache
        if cached and now - cached[0] < 15.0:
            return cached[1]
        since = local_day_start()
        try:
            with self.connect(quick=True) as conn:
                exists = conn.execute("SELECT 1 FROM catalog_listings LIMIT 1").fetchone()
                if exists:
                    n = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM catalog_listings WHERE first_seen >= ?",
                            (since,),
                        ).fetchone()[0]
                    )
                    self._new_today_cache = (now, n)
                    return n
        except sqlite3.OperationalError:
            pass
        n = self.new_today_count()
        self._new_today_cache = (now, n)
        return n

    def guest_search_has_access(self, token: str) -> bool:
        token = (token or "").strip()
        if not token:
            return False
        with self.connect(readonly=True) as conn:
            row = conn.execute("SELECT 1 FROM guest_searches WHERE token = ? LIMIT 1", (token,)).fetchone()
        return bool(row)

    def guest_search_used(self, ip: str, visitor_id: str) -> bool:
        ip = (ip or "").strip()
        visitor_id = (visitor_id or "").strip()
        with self.connect() as conn:
            if visitor_id and conn.execute(
                "SELECT 1 FROM guest_searches WHERE visitor_id = ? LIMIT 1", (visitor_id,)
            ).fetchone():
                return True
            if ip and conn.execute("SELECT 1 FROM guest_searches WHERE ip = ? LIMIT 1", (ip,)).fetchone():
                return True
        return False

    def grant_guest_search(self, ip: str, visitor_id: str) -> str | None:
        ip = (ip or "").strip()
        visitor_id = (visitor_id or "").strip()
        if self.guest_search_used(ip, visitor_id):
            return None
        token = secrets.token_hex(16)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO guest_searches(token, ip, visitor_id, created_at) VALUES (?, ?, ?, ?)",
                (token, ip, visitor_id, utc_now()),
            )
        return token

    def landing_preview_listings(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._landing_preview_cache
        if cached and now - cached[0] < 20.0:
            return cached[1]
        data = self.catalog(
            {
                "district": "Brno",
                "disposition": "1+kk,1+1,2+kk,2+1,3+kk,3+1",
                "price_to": 22000,
                "offer": "pronajem",
                "estate": "byt",
                "sort": "newest",
                "limit": 48,
                "offset": 0,
            }
        )
        items = [item for item in (data.get("items") or []) if item.get("id") is not None]
        cleaned: list[dict[str, Any]] = []
        for item in items:
            try:
                price = int(item.get("price_czk") or 0)
            except (TypeError, ValueError):
                price = 0
            try:
                area = int(item.get("area_m2") or 0)
            except (TypeError, ValueError):
                area = 0
            if price < 4000:
                continue
            if area and area < 16:
                continue
            cleaned.append(item)
        items = cleaned
        picked: list[dict[str, Any]] = []
        seen: set[Any] = set()

        def take(item: dict[str, Any]) -> None:
            key = item.get("canonical_key") or item.get("listing_key") or item.get("id")
            if key in seen:
                return
            seen.add(key)
            picked.append(item)

        def matches_portal(item: dict[str, Any], needle: str) -> bool:
            blob = f"{item.get('portal') or ''} {item.get('url') or ''}".casefold()
            return needle in blob

        for needle in ("bezrealitky", "sreality", "idnes", "bazos"):
            hit = next((item for item in items if matches_portal(item, needle)), None)
            if hit:
                take(hit)
            if len(picked) >= 3:
                break
        for item in items:
            if len(picked) >= 3:
                break
            take(item)
        cards = [self._landing_card(item) for item in picked[:3]]
        self._landing_preview_cache = (now, cards)
        return cards

    def _landing_card(self, item: dict[str, Any]) -> dict[str, Any]:
        portal = str(item.get("portal") or "")
        if not portal:
            portal = portal_label(portal_from_url(str(item.get("url") or "")))
        price = str(item.get("price_label") or "").strip()
        if not price and item.get("price_czk") is not None:
            try:
                price = f"{int(item['price_czk']):,} Kč".replace(",", " ")
            except (TypeError, ValueError):
                price = ""
        area = item.get("area_m2")
        locality = str(item.get("locality") or "").strip()
        spec = locality
        if area:
            try:
                spec = f"{locality} • {int(area)} m²" if locality else f"{int(area)} m²"
            except (TypeError, ValueError):
                spec = locality
        return {
            "monitor_id": item.get("monitor_id") or "",
            "id": item.get("id"),
            "listing_key": item.get("listing_key") or item.get("canonical_key") or "",
            "url": item.get("url") or "",
            "name": item.get("name") or locality or "Byt",
            "portal": portal.split(" · ")[0] if portal else "Web",
            "price": price,
            "locality": spec,
            "image": item.get("image_url") or "",
            "disposition": item.get("disposition") or "",
        }

    def catalog_prev(self, listing: Listing) -> dict[str, Any] | None:
        key = listing_key(listing.url)
        with self.connect() as conn:
            canon = self._resolve_canonical(conn, listing)
            row = conn.execute(
                "SELECT * FROM catalog_listings WHERE canonical_key = ? OR listing_key = ? LIMIT 1",
                (canon, key),
            ).fetchone()
        return dict(row) if row else None

    def upsert_catalog_listing(
        self,
        listing: Listing,
        *,
        kind: str = "refresh",
        conn: sqlite3.Connection | None = None,
        fast: bool = False,
    ) -> dict[str, Any]:
        key = listing_key(listing.url)
        now = utc_now()
        portal = portal_from_url(listing.url)
        cm = self.connect() if conn is None else nullcontext(conn)
        with cm as conn:
            canon = self._resolve_canonical(conn, listing, fast=fast)
            prev_row = conn.execute(
                "SELECT * FROM catalog_listings WHERE canonical_key = ? OR listing_key = ? LIMIT 1",
                (canon, key),
            ).fetchone()
            prev = dict(prev_row) if prev_row else None
            changed = False
            if prev and prev.get("price_czk") is not None and listing.price_czk is not None:
                try:
                    changed = int(prev["price_czk"]) != int(listing.price_czk)
                except (TypeError, ValueError):
                    changed = False
            self._upsert_listing_link(conn, listing, canon, gone=False)
            values = (
                listing.id,
                listing.name,
                listing.price_czk,
                listing.price_label,
                listing.disposition,
                listing.area_m2,
                listing.locality,
                listing.url,
                cdn_image_url(listing.image_url),
                now if not prev else prev.get("first_seen") or now,
                listing.created_on,
                listing.edited_on,
                listing.old_price_czk,
                kind,
                listing.lat,
                listing.lon,
                (listing.description or "").strip() or None,
                _extras_json(listing.extras),
                now,
                portal,
                canon,
            )
            if prev:
                extras_json = _extras_json(listing.extras)
                conn.execute(
                    """
                    UPDATE catalog_listings SET
                        name = ?,
                        price_czk = ?,
                        price_label = ?,
                        disposition = ?,
                        area_m2 = ?,
                        locality = ?,
                        image_url = COALESCE(?, image_url),
                        created_on = COALESCE(?, created_on),
                        edited_on = COALESCE(?, edited_on),
                        old_price_czk = ?,
                        last_kind = ?,
                        lat = COALESCE(?, lat),
                        lon = COALESCE(?, lon),
                        description = COALESCE(?, description),
                        extras = CASE
                            WHEN ? IS NOT NULL AND ? != '' AND ? != '{}' THEN ? ELSE extras
                        END,
                        last_seen = ?,
                        gone = 0,
                        canonical_key = ?
                    WHERE listing_key = ?
                    """,
                    (
                        listing.name,
                        listing.price_czk,
                        listing.price_label,
                        listing.disposition,
                        listing.area_m2,
                        listing.locality,
                        cdn_image_url(listing.image_url),
                        listing.created_on,
                        listing.edited_on,
                        listing.old_price_czk,
                        kind,
                        listing.lat,
                        listing.lon,
                        (listing.description or "").strip() or None,
                        extras_json,
                        extras_json,
                        extras_json,
                        extras_json,
                        now,
                        canon,
                        prev["listing_key"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO catalog_listings(
                        listing_key, id, name, price_czk, price_label, disposition, area_m2,
                        locality, url, image_url, first_seen, created_on, edited_on, old_price_czk,
                        last_kind, lat, lon, description, extras, last_seen, gone, portal, canonical_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (key, *values),
                )
            self.upsert_seen(CATALOG_MONITOR_ID, listing, notified=False, kind=kind, conn=conn)
        return {"listing_key": canon, "url_key": key, "canonical_key": canon, "new": prev is None, "changed": changed, "prev": prev}

    def upsert_catalog_listings_batch(
        self,
        listings: list[Listing],
        *,
        kind: str = "seeded",
        commit_every: int | None = None,
        fast: bool = True,
    ) -> dict[str, int]:
        if not listings:
            return {"n": 0, "new": 0, "updated": 0, "same": 0}
        every = max(50, int(commit_every or config.SCRAPE_BATCH_COMMIT))
        new = updated = same = 0
        with self.connect() as conn:
            conn.execute("PRAGMA busy_timeout=8000")
            for index, listing in enumerate(listings, start=1):
                result = self.upsert_catalog_listing(listing, kind=kind, conn=conn, fast=fast)
                if result.get("new"):
                    new += 1
                elif result.get("changed"):
                    updated += 1
                else:
                    same += 1
                if index % every == 0:
                    conn.commit()
        return {"n": len(listings), "new": new, "updated": updated, "same": same}

    def upsert_catalog_listings_results(
        self,
        listings: list[Listing],
        *,
        kind: str = "refresh",
        fast: bool = True,
    ) -> list[dict[str, Any]]:
        """Batch monitored listings in one transaction while preserving previous snapshots."""
        if not listings:
            return []
        results: list[dict[str, Any]] = []
        with self.connect() as conn:
            conn.execute("PRAGMA busy_timeout=8000")
            for listing in listings:
                results.append(
                    self.upsert_catalog_listing(listing, kind=kind, conn=conn, fast=fast)
                )
        return results

    def record_scrape_tick(self, tick: dict[str, Any], *, keep: int = 120) -> None:
        """Persist latest minute scrape tick + rolling history for admin/provoz."""
        payload = dict(tick or {})
        payload.setdefault("at", utc_now())
        encoded = json.dumps(payload, ensure_ascii=False)
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000")
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'scrape_tick_history'"
                ).fetchone()
                history: list[Any] = []
                if row and row["value"]:
                    try:
                        parsed = json.loads(str(row["value"]))
                        if isinstance(parsed, list):
                            history = parsed
                    except json.JSONDecodeError:
                        pass
                history.insert(0, payload)
                conn.execute(
                    """
                    INSERT INTO meta(key, value) VALUES ('scrape_worker_tick', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (encoded,),
                )
                conn.execute(
                    """
                    INSERT INTO meta(key, value) VALUES ('scrape_tick_history', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (json.dumps(history[: max(5, keep)], ensure_ascii=False),),
                )
        finally:
            conn.close()

    def list_scrape_ticks(self, limit: int = 20) -> list[dict[str, Any]]:
        raw = self.get_meta("scrape_tick_history")
        if not raw:
            latest = self.get_meta("scrape_worker_tick")
            if not latest:
                return []
            try:
                item = json.loads(str(latest))
                return [item] if isinstance(item, dict) else []
            except json.JSONDecodeError:
                return []
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        out: list[dict[str, Any]] = []
        for item in parsed[: max(1, limit)]:
            if isinstance(item, dict):
                out.append(item)
        return out

    def list_scrape_schedules(self) -> list[dict[str, Any]]:
        raw = self.get_meta("scrape_schedules")
        if not raw:
            return []
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        out: list[dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict) and item.get("id") and item.get("run_at"):
                out.append(item)
        out.sort(key=lambda row: str(row.get("run_at") or ""))
        return out

    def add_scrape_schedule(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job or {})
        job_id = str(payload.get("id") or "").strip() or f"scrape-{uuid.uuid4().hex[:10]}"
        payload["id"] = job_id
        payload.setdefault("created_at", utc_now())
        rows = self.list_scrape_schedules()
        rows.append(payload)
        rows.sort(key=lambda row: str(row.get("run_at") or ""))
        self.set_meta("scrape_schedules", json.dumps(rows, ensure_ascii=False))
        return payload

    def remove_scrape_schedule(self, job_id: str) -> bool:
        wanted = str(job_id or "").strip()
        if not wanted:
            return False
        rows = self.list_scrape_schedules()
        kept = [row for row in rows if str(row.get("id") or "") != wanted]
        if len(kept) == len(rows):
            return False
        self.set_meta("scrape_schedules", json.dumps(kept, ensure_ascii=False))
        return True

    def claim_due_scrape_schedules(self, *, now: str | None = None) -> list[dict[str, Any]]:
        stamp = str(now or utc_now())
        rows = self.list_scrape_schedules()
        due: list[dict[str, Any]] = []
        kept: list[dict[str, Any]] = []
        for row in rows:
            run_at = str(row.get("run_at") or "")
            if run_at and run_at <= stamp:
                due.append(row)
            else:
                kept.append(row)
        if due:
            self.set_meta("scrape_schedules", json.dumps(kept, ensure_ascii=False))
        return due

    def add_monitor_hit(self, monitor_id: str, listing_key_value: str) -> None:
        if not listing_key_value:
            return
        with self.connect() as conn:
            keys = {listing_key_value}
            row = conn.execute(
                "SELECT canonical_key FROM listing_links WHERE url_key = ? OR canonical_key = ? OR url = ? LIMIT 1",
                (listing_key_value, listing_key_value, listing_key_value),
            ).fetchone()
            if row and row["canonical_key"]:
                keys.add(str(row["canonical_key"]))
            cat = conn.execute(
                "SELECT canonical_key, listing_key FROM catalog_listings WHERE listing_key = ? OR canonical_key = ? LIMIT 1",
                (listing_key_value, listing_key_value),
            ).fetchone()
            if cat:
                if cat["canonical_key"]:
                    keys.add(str(cat["canonical_key"]))
                if cat["listing_key"]:
                    keys.add(str(cat["listing_key"]))
            now = utc_now()
            for key in keys:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO monitor_hits(monitor_id, listing_key, first_matched)
                    VALUES (?, ?, ?)
                    """,
                    (monitor_id, key, now),
                )

    def ensure_scrape_job(self, *, kind: str, portal: str, shard_key: str, search_url: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO scrape_jobs(id, kind, portal, shard_key, search_url, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                ON CONFLICT(kind, shard_key) DO UPDATE SET
                    search_url = excluded.search_url,
                    portal = excluded.portal
                """,
                (job_id, kind, portal, shard_key, search_url),
            )
            row = conn.execute(
                "SELECT * FROM scrape_jobs WHERE kind = ? AND shard_key = ?",
                (kind, shard_key),
            ).fetchone()
        return dict(row)

    def list_scrape_jobs(self, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM scrape_jobs"
        params: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY shard_key"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    def update_scrape_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as conn:
            conn.execute(f"UPDATE scrape_jobs SET {assignments} WHERE id = ?", (*fields.values(), job_id))

    def attach_monitor_live_job(self, monitor: dict[str, Any]) -> dict[str, Any]:
        jobs = self.attach_monitor_live_jobs(monitor)
        if jobs:
            return jobs[0]
        from app.catalog_sync import normalize_search_url
        from app.sources import portal_of

        url = normalize_search_url(monitor.get("search_url") or "")
        portal = portal_of(url)
        return self.ensure_scrape_job(kind="monitor_live", portal=portal, shard_key=f"live:{url}", search_url=url)

    def attach_monitor_live_jobs(self, monitor: dict[str, Any]) -> list[dict[str, Any]]:
        from app.catalog_sync import monitor_search_targets

        jobs = [
            self.ensure_scrape_job(
                kind="monitor_live",
                portal=target["portal"],
                shard_key=f"live:{target['search_url']}",
                search_url=target["search_url"],
            )
            for target in monitor_search_targets(monitor)
        ]
        with self.connect() as conn:
            conn.execute("DELETE FROM monitor_jobs WHERE monitor_id = ?", (monitor["id"],))
            for job in jobs:
                conn.execute(
                    "INSERT OR IGNORE INTO monitor_jobs(monitor_id, job_id) VALUES (?, ?)",
                    (monitor["id"], job["id"]),
                )
        return jobs

    def monitors_for_job(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT monitors.* FROM monitors
                JOIN monitor_jobs ON monitor_jobs.monitor_id = monitors.id
                WHERE monitor_jobs.job_id = ?
                """,
                (job_id,),
            ).fetchall()
        return [self._monitor_row(row) for row in rows]

    def seed_monitor_from_catalog(self, monitor: dict[str, Any]) -> int:
        from app.catalog_sync import listing_matches_monitor

        matched = 0
        with self.connect() as conn:
            rows = conn.execute("SELECT listing_key, canonical_key, url, price_czk, price_label, area_m2, locality, disposition, extras, created_on FROM catalog_listings WHERE IFNULL(gone, 0) = 0").fetchall()
        for row in rows:
            if listing_matches_monitor(dict(row), monitor):
                self.add_monitor_hit(monitor["id"], row["canonical_key"] or row["listing_key"])
                matched += 1
        return matched

    def matching_monitors(self, listing: Listing, job_id: str | None = None) -> list[dict[str, Any]]:
        from app.monitor_index import MonitorIndex

        found: dict[str, dict[str, Any]] = {}
        if job_id:
            for item in self.monitors_for_job(job_id):
                if item.get("enabled"):
                    found[item["id"]] = item
        now = time.monotonic()
        if self._monitor_index is None or (now - self._monitor_index_at) > 30.0:
            self._monitor_index = MonitorIndex(self.list_monitors())
            self._monitor_index_at = now
        for item in self._monitor_index.matching_monitors(listing):
            found[item["id"]] = item
        return list(found.values())

    def mark_catalog_stale_gone(self, seen_before: str, complete_portals: list[str]) -> int:
        if not complete_portals:
            return 0
        holders = ",".join("?" * len(complete_portals))
        gone_keys: list[str] = []
        with self.connect() as conn:
            conn.execute(
                f"""
                UPDATE listing_links
                SET gone = 1
                WHERE portal IN ({holders})
                  AND IFNULL(gone, 0) = 0
                  AND (last_seen IS NULL OR last_seen < ?)
                """,
                (*complete_portals, seen_before),
            )
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT listing_key, canonical_key, portal, last_seen
                    FROM catalog_listings
                    WHERE IFNULL(gone, 0) = 0
                    """
                )
            ]
            for row in rows:
                canon = row.get("canonical_key") or row.get("listing_key")
                live = conn.execute(
                    "SELECT 1 FROM listing_links WHERE canonical_key = ? AND IFNULL(gone, 0) = 0 LIMIT 1",
                    (canon,),
                ).fetchone()
                if live:
                    continue
                links = conn.execute(
                    "SELECT 1 FROM listing_links WHERE canonical_key = ? LIMIT 1",
                    (canon,),
                ).fetchone()
                if not links:
                    if row.get("portal") not in complete_portals:
                        continue
                    last_seen = row.get("last_seen")
                    if last_seen and last_seen >= seen_before:
                        continue
                conn.execute("UPDATE catalog_listings SET gone = 1 WHERE listing_key = ?", (row["listing_key"],))
                gone_keys.append(row["listing_key"])
        for key in gone_keys:
            self._fanout_catalog_gone(key)
        return len(gone_keys)

    def mark_catalog_listing_gone(self, key: str) -> None:
        if not key:
            return
        fanout_key = ""
        with self.connect() as conn:
            conn.execute(
                "UPDATE listing_links SET gone = 1 WHERE url_key = ? OR url = ?",
                (key, key),
            )
            row = conn.execute(
                "SELECT listing_key, canonical_key, url FROM catalog_listings WHERE listing_key = ? OR canonical_key = ? LIMIT 1",
                (key, key),
            ).fetchone()
            if row and listing_key(str(row["url"] or "")) == key:
                conn.execute(
                    "UPDATE listing_links SET gone = 1 WHERE url = ? OR url_key = ?",
                    (row["url"], listing_key(str(row["url"] or ""))),
                )
            canon = ""
            if row:
                canon = str(row["canonical_key"] or row["listing_key"] or "")
            if not canon:
                link = conn.execute(
                    "SELECT canonical_key FROM listing_links WHERE url_key = ? OR url = ? LIMIT 1",
                    (key, key),
                ).fetchone()
                if link:
                    canon = str(link["canonical_key"])
                    row = conn.execute(
                        "SELECT listing_key, canonical_key FROM catalog_listings WHERE canonical_key = ? LIMIT 1",
                        (canon,),
                    ).fetchone()
            if not canon:
                conn.execute("UPDATE catalog_listings SET gone = 1 WHERE listing_key = ?", (key,))
                fanout_key = key
            else:
                live = conn.execute(
                    "SELECT 1 FROM listing_links WHERE canonical_key = ? AND IFNULL(gone, 0) = 0 LIMIT 1",
                    (canon,),
                ).fetchone()
                if not live:
                    conn.execute(
                        "UPDATE catalog_listings SET gone = 1 WHERE canonical_key = ? OR listing_key = ?",
                        (canon, key),
                    )
                    fanout_key = str(row["listing_key"]) if row else key
        if fanout_key:
            self._fanout_catalog_gone(fanout_key)

    def _fanout_catalog_gone(self, key: str) -> None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM catalog_listings WHERE listing_key = ? OR canonical_key = ? LIMIT 1",
                (key, key),
            ).fetchone()
            if not row:
                return
            canon = str(row["canonical_key"] or row["listing_key"] or key)
            link_keys = [
                item["url_key"]
                for item in conn.execute("SELECT url_key FROM listing_links WHERE canonical_key = ?", (canon,))
            ]
            hit_keys = {key, canon, str(row["listing_key"] or ""), *link_keys}
            holders = ",".join("?" * len(hit_keys))
            hits = [
                item["monitor_id"]
                for item in conn.execute(
                    f"SELECT DISTINCT monitor_id FROM monitor_hits WHERE listing_key IN ({holders})",
                    tuple(hit_keys),
                )
            ]
        listing = _listing_from_catalog_dict(dict(row))
        self.upsert_seen(CATALOG_MONITOR_ID, listing, notified=False, kind="sold")
        self.mark_gone(CATALOG_MONITOR_ID, listing.id)
        for monitor_id in hits:
            self.upsert_seen(monitor_id, listing, notified=False, kind="sold")
            self.mark_gone(monitor_id, listing.id)

    def stale_catalog_listings(self, days: int = 1, limit: int = 8) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM catalog_listings
                WHERE IFNULL(gone, 0) = 0
                  AND (last_seen IS NULL OR last_seen < ?)
                ORDER BY last_seen IS NULL DESC, last_seen ASC
                LIMIT ?
                """,
                (cutoff_iso, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def catalog_sync_status(self) -> dict[str, Any]:
        from app.catalog_sync import daily_shards

        jobs = self.list_scrape_jobs("catalog_daily")
        expected = len(daily_shards())
        done = sum(1 for item in jobs if item.get("status") == "done")
        running = next((item for item in jobs if item.get("status") == "running"), None)
        errors = [item.get("last_error") for item in jobs if item.get("last_error")]
        with self.connect() as conn:
            total = int(conn.execute("SELECT COUNT(*) FROM catalog_listings").fetchone()[0])
        return {
            "jobs": expected or len(jobs),
            "done": done,
            "running": running.get("shard_key") if running else None,
            "status": self.get_meta("catalog_sync_status") or ("idle" if not running else "running"),
            "last_run": self.get_meta("catalog_sync_last"),
            "last_error": errors[0] if errors else self.get_meta("catalog_sync_error"),
            "listings": total,
            "upserts": sum(int(item.get("upserts") or 0) for item in jobs),
        }

    def upsert_seen(
        self,
        monitor_id: str,
        listing: Listing,
        notified: bool,
        kind: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        now = utc_now()
        if kind:
            event_kind = kind
        elif notified:
            event_kind = listing.kind or "new"
        else:
            event_kind = "seeded"
        change_text = "; ".join(f"{label}: {before} → {after}" for label, before, after in listing.changes)
        detail = change_text or listing.name
        cm = self.connect() if conn is None else nullcontext(conn)
        with cm as conn:
            canon = self._resolve_canonical(conn, listing)
            url_key = listing_key(listing.url)
            conn.execute(
                "UPDATE listings SET canonical_key = ? WHERE url = ? OR listing_key = ?",
                (canon, listing.url, url_key),
            )
            existing = conn.execute(
                "SELECT id, url FROM listings WHERE monitor_id = ? AND canonical_key = ?",
                (monitor_id, canon),
            ).fetchone()
            listing_id = int(existing["id"]) if existing else listing.id
            keep_url = str(existing["url"]) if existing and existing["url"] else listing.url
            self._upsert_listing_link(conn, listing, canon, gone=False)
            stored = listing if listing_id == listing.id else replace(listing, id=listing_id)
            conn.execute(
                """
                INSERT INTO listings (
                    id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                    locality, url, listing_key, canonical_key, image_url, first_seen, notified,
                    created_on, edited_on, views, old_price_czk, last_kind, lat, lon,
                    description, extras, last_seen, gone
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ON CONFLICT(monitor_id, id) DO UPDATE SET
                    name = excluded.name,
                    price_czk = excluded.price_czk,
                    price_label = excluded.price_label,
                    disposition = excluded.disposition,
                    area_m2 = excluded.area_m2,
                    locality = excluded.locality,
                    listing_key = listings.listing_key,
                    canonical_key = excluded.canonical_key,
                    image_url = COALESCE(excluded.image_url, listings.image_url),
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
                    listing_id,
                    monitor_id,
                    listing.name,
                    listing.price_czk,
                    listing.price_label,
                    listing.disposition,
                    listing.area_m2,
                    listing.locality,
                    keep_url,
                    listing_key(keep_url),
                    canon,
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
            if event_kind != "seeded":
                conn.execute(
                    "INSERT INTO events(listing_id, monitor_id, kind, created_at, detail) VALUES (?, ?, ?, ?, ?)",
                    (listing_id, monitor_id, event_kind, now, detail),
                )
                self._record_price(conn, monitor_id, stored, now)
                self._save_photos(conn, monitor_id, stored)

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

    def recent_notified(
        self,
        limit: int = 12,
        monitor_id: str | None = None,
        *,
        since: str | None = None,
        extras: bool = True,
        twins: bool = False,
    ) -> list[dict[str, Any]]:
        fetch_limit = max(limit * 4, limit)
        params: list[Any] = []
        if since:
            sql = """
                SELECT listings.*, monitors.name AS monitor_name, hits.hit_at
                FROM (
                    SELECT listing_id, monitor_id, MAX(created_at) AS hit_at
                    FROM events
                    WHERE kind IN ('new', 'changed') AND created_at >= ?
            """
            params.append(since)
            if monitor_id:
                sql += " AND monitor_id = ?"
                params.append(monitor_id)
            sql += """
                    GROUP BY listing_id, monitor_id
                    ORDER BY hit_at DESC
                    LIMIT ?
                ) hits
                JOIN listings ON listings.id = hits.listing_id AND listings.monitor_id = hits.monitor_id
                LEFT JOIN monitors ON monitors.id = listings.monitor_id
                ORDER BY hits.hit_at DESC
            """
            params.append(fetch_limit)
        else:
            sql = """
                SELECT listings.*, monitors.name AS monitor_name
                FROM listings
                LEFT JOIN monitors ON monitors.id = listings.monitor_id
                WHERE listings.notified = 1
            """
            if monitor_id:
                sql += " AND listings.monitor_id = ?"
                params.append(monitor_id)
            sql += """
                ORDER BY listings.first_seen DESC
                LIMIT ?
            """
            params.append(fetch_limit)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            item = public_listing(dict(row))
            key = listing_identity(item)
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            if len(items) >= limit:
                break
        if extras:
            self._attach_catalog_extras(items, twins=twins)
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

    def _ensure_scrape_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS catalog_listings (
                listing_key TEXT PRIMARY KEY,
                id INTEGER NOT NULL,
                name TEXT NOT NULL,
                price_czk INTEGER,
                price_label TEXT,
                disposition TEXT,
                area_m2 INTEGER,
                locality TEXT,
                url TEXT NOT NULL,
                image_url TEXT,
                first_seen TEXT NOT NULL,
                created_on TEXT,
                edited_on TEXT,
                old_price_czk INTEGER,
                last_kind TEXT,
                lat REAL,
                lon REAL,
                description TEXT,
                extras TEXT,
                last_seen TEXT,
                gone INTEGER NOT NULL DEFAULT 0,
                portal TEXT
            );
            CREATE TABLE IF NOT EXISTS scrape_jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                portal TEXT NOT NULL,
                shard_key TEXT NOT NULL,
                search_url TEXT NOT NULL,
                page INTEGER NOT NULL DEFAULT 1,
                upserts INTEGER NOT NULL DEFAULT 0,
                last_total INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT,
                started_at TEXT,
                finished_at TEXT,
                UNIQUE(kind, shard_key)
            );
            CREATE TABLE IF NOT EXISTS monitor_jobs (
                monitor_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                PRIMARY KEY (monitor_id, job_id)
            );
            CREATE TABLE IF NOT EXISTS monitor_hits (
                monitor_id TEXT NOT NULL,
                listing_key TEXT NOT NULL,
                first_matched TEXT NOT NULL,
                PRIMARY KEY (monitor_id, listing_key)
            );
            CREATE INDEX IF NOT EXISTS idx_catalog_last_seen ON catalog_listings(last_seen);
            CREATE INDEX IF NOT EXISTS idx_monitor_hits_key ON monitor_hits(listing_key);
            CREATE INDEX IF NOT EXISTS idx_scrape_jobs_kind ON scrape_jobs(kind, status);
            CREATE INDEX IF NOT EXISTS idx_listings_notified_seen ON listings(notified, first_seen);
            CREATE INDEX IF NOT EXISTS idx_listings_lat_lon ON listings(lat, lon);
            CREATE TABLE IF NOT EXISTS locality_geocode (
                key TEXT PRIMARY KEY,
                lat REAL,
                lon REAL,
                fetched_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_kind_created ON events(kind, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_monitor_kind ON events(monitor_id, kind, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_monitor_listing ON events(monitor_id, listing_id);
            CREATE INDEX IF NOT EXISTS idx_monitor_hits_monitor ON monitor_hits(monitor_id);
            CREATE TABLE IF NOT EXISTS listing_links (
                url TEXT PRIMARY KEY,
                url_key TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                portal TEXT NOT NULL,
                native_id TEXT,
                agency TEXT,
                last_seen TEXT,
                gone INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_listing_links_canonical ON listing_links(canonical_key);
            CREATE INDEX IF NOT EXISTS idx_listing_links_url_key ON listing_links(url_key);
            """
        )
        cat_cols = {row[1] for row in conn.execute("PRAGMA table_info(catalog_listings)")}
        if "canonical_key" not in cat_cols:
            conn.execute("ALTER TABLE catalog_listings ADD COLUMN canonical_key TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_canonical ON catalog_listings(canonical_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_first_seen ON catalog_listings(first_seen)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_canonical ON listings(canonical_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_portal_seen ON catalog_listings(portal, last_seen)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_gone_portal ON catalog_listings(gone, portal)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_catalog_gone_last_seen ON catalog_listings(gone, last_seen DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listing_links_portal ON listing_links(portal, gone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listings_gone_seen ON listings(gone, last_seen)")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS game_rent_rounds (
                id TEXT PRIMARY KEY,
                player_name TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                accuracy REAL NOT NULL DEFAULT 0,
                guesses_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_game_rent_score ON game_rent_rounds(score DESC, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_game_rent_created ON game_rent_rounds(created_at DESC);
            """
        )
        self._unify_listing_identities(conn)
        self._rekey_idnes_listing_ids(conn)

    def _rekey_idnes_listing_ids(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM meta WHERE key = 'idnes_ids_js_safe'").fetchone():
            return
        from app.idnes import oid_to_int

        oid_re = re.compile(r"/detail/[^/]+/[^/]+/[^/]+/([0-9a-f]{24})/?", re.I)
        related = (("listing_photos", "listing_id"), ("price_history", "listing_id"), ("events", "listing_id"))
        rows = list(conn.execute("SELECT monitor_id, id, url FROM listings WHERE url LIKE '%idnes.cz%'"))
        for row in rows:
            match = oid_re.search(row["url"] or "")
            if not match:
                continue
            new_id = oid_to_int(match.group(1))
            old_id = int(row["id"])
            if new_id == old_id:
                continue
            clash = conn.execute(
                "SELECT 1 FROM listings WHERE monitor_id = ? AND id = ?",
                (row["monitor_id"], new_id),
            ).fetchone()
            if clash:
                for table, column in related:
                    try:
                        conn.execute(
                            f"UPDATE OR IGNORE {table} SET {column} = ? WHERE monitor_id = ? AND {column} = ?",
                            (new_id, row["monitor_id"], old_id),
                        )
                        conn.execute(
                            f"DELETE FROM {table} WHERE monitor_id = ? AND {column} = ?",
                            (row["monitor_id"], old_id),
                        )
                    except sqlite3.OperationalError:
                        continue
                conn.execute("DELETE FROM listings WHERE monitor_id = ? AND id = ?", (row["monitor_id"], old_id))
                continue
            conn.execute(
                "UPDATE listings SET id = ? WHERE monitor_id = ? AND id = ?",
                (new_id, row["monitor_id"], old_id),
            )
            for table, column in related:
                try:
                    conn.execute(
                        f"UPDATE {table} SET {column} = ? WHERE monitor_id = ? AND {column} = ?",
                        (new_id, row["monitor_id"], old_id),
                    )
                except sqlite3.OperationalError:
                    continue
        for row in conn.execute("SELECT listing_key, id, url FROM catalog_listings WHERE url LIKE '%idnes.cz%'"):
            match = oid_re.search(row["url"] or "")
            if not match:
                continue
            new_id = oid_to_int(match.group(1))
            if int(row["id"] or 0) != new_id:
                conn.execute("UPDATE catalog_listings SET id = ? WHERE listing_key = ?", (new_id, row["listing_key"]))
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('idnes_ids_js_safe', '1')")

    def _migrate_monitors(self, conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(monitors)")}
        if "interval_sec" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN interval_sec INTEGER")
        if "last_inventory" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN last_inventory TEXT")
        if "portals" not in cols:
            conn.execute("ALTER TABLE monitors ADD COLUMN portals TEXT NOT NULL DEFAULT 'all'")
            conn.execute("UPDATE monitors SET portals = 'all' WHERE IFNULL(portals, '') = ''")

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

    def _save_photos(self, conn: sqlite3.Connection, monitor_id: str, listing: Listing, replace: bool = False) -> None:
        photos = [cdn_image_url(url) for url in (listing.photos or []) if url] or (
            [cdn_image_url(listing.image_url)] if listing.image_url else []
        )
        photos = [url for url in photos if url]
        if not photos:
            return
        if replace:
            conn.execute(
                "DELETE FROM listing_photos WHERE monitor_id = ? AND listing_id = ?",
                (monitor_id, listing.id),
            )
        else:
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
                    locality = CASE WHEN ? != '' THEN ? ELSE listings.locality END,
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
                    (listing.locality or "").strip(),
                    (listing.locality or "").strip(),
                    listing.lat,
                    listing.lon,
                    cdn_image_url(listing.image_url),
                    now,
                    monitor_id,
                    listing.id,
                ),
            )
            self._record_price(conn, monitor_id, listing, now)
            self._save_photos(conn, monitor_id, listing, replace=True)

    def _persist_locality_coords(self, locality: str, point: tuple[float, float]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE listings SET lat = ?, lon = ?
                WHERE locality = ? AND (lat IS NULL OR lon IS NULL)
                """,
                (point[0], point[1], locality),
            )

    def _fill_missing_coords(self, rows: list[dict[str, Any]], geoms: list[dict[str, Any]] | None = None) -> None:
        pending: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("lat") is not None and row.get("lon") is not None:
                continue
            loc = str(row.get("locality") or "").strip()
            if not loc:
                continue
            pending.setdefault(loc, []).append(row)
        if not pending:
            return
        streets = places.street_index_sync(geoms)
        for loc, group in pending.items():
            point = streets.get(places._norm_street(places.street_from_locality(loc)))
            persist = bool(point)
            if not point:
                point = places.approx_point_from_locality(loc)
            if not point:
                continue
            if persist:
                try:
                    self._persist_locality_coords(loc, point)
                except sqlite3.OperationalError:
                    pass
            for row in group:
                row["lat"], row["lon"] = point
                row["_geo_approx"] = True

    def catalog(self, filters: dict[str, Any]) -> dict[str, Any]:
        from app.sources import PORTAL_IDS, url_likes

        where = ["1=1"]
        params: list[Any] = []
        portal = (filters.get("portal") or "").strip()
        if portal in PORTAL_IDS:
            likes = url_likes(portal) or (f"%{portal}.cz%",)
            like_sql = " OR ".join("listings.url LIKE ?" for _ in likes)
            where.append(
                f"""
                (
                  EXISTS (
                    SELECT 1 FROM listing_links
                    WHERE listing_links.canonical_key = COALESCE(NULLIF(listings.canonical_key, ''), listings.listing_key)
                      AND listing_links.portal = ?
                      AND IFNULL(listing_links.gone, 0) = 0
                  )
                  OR {like_sql}
                )
                """
            )
            params.append(portal)
            params.extend(likes)
        monitor_id = (filters.get("monitor_id") or "").strip()
        if monitor_id:
            where.append(_monitor_hit_sql())
            params.append(monitor_id)
        query = (filters.get("q") or "").strip()
        place_geoms = [item for item in (filters.get("place_geoms") or []) if isinstance(item, dict)]
        if filters.get("place_empty") and not place_geoms:
            if filters.get("pins_only"):
                return {"items": []}
            limit = min(max(int(filters.get("limit") or 36), 1), 120)
            offset = max(int(filters.get("offset") or 0), 0)
            return {"items": [], "total": 0, "limit": limit, "offset": offset, "facets": self.catalog_facets()}
        if query and not place_geoms:
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
                elif offer == "drazba":
                    offer_parts.append("(listings.extras LIKE ? OR listings.url LIKE ?)")
                    params.extend(['%"offer": "Dražba"%', "%/drazba/%"])
            if offer_parts:
                where.append("(" + " OR ".join(offer_parts) + ")")
        districts = _csv(filters.get("district"))
        geo_ids = {str(item.get("id") or "") for item in place_geoms}
        if districts and not place_geoms:
            district_parts = []
            for district in districts:
                number = district.replace("praha-", "")
                if number.isdigit():
                    district_parts.append("(listings.locality GLOB ? OR listings.locality GLOB ?)")
                    params.extend([f"*Praha {number}", f"*Praha {number}[!0-9]*"])
                    for area in PRAGUE_DISTRICTS.get(number) or []:
                        district_parts.append("listings.locality LIKE ?")
                        params.append(f"%{area}%")
                    continue
                if district.startswith("R") and district[1:].isdigit():
                    continue
                label = district.strip()
                if not label:
                    continue
                district_parts.append("listings.locality LIKE ?")
                params.append(f"%{label}%")
            if district_parts:
                where.append("(" + " OR ".join(district_parts) + ")")
        elif districts:
            leftover = []
            for district in districts:
                osm = localities.SREALITY_TO_OSM.get(district.casefold())
                if osm and osm in geo_ids:
                    continue
                if district.startswith("R") and district[1:].isdigit():
                    continue
                label = district.strip()
                if label:
                    leftover.append(label)
            if leftover and not place_geoms:
                _or_likes(where, params, "listings.locality", [f"%{label}%" for label in leftover])
        if place_geoms:
            box = places.bbox_of(place_geoms)
            geo_sql = "listings.lat IS NOT NULL AND listings.lon IS NOT NULL"
            geo_params: list[Any] = []
            if box:
                geo_sql += " AND listings.lat BETWEEN ? AND ? AND listings.lon BETWEEN ? AND ?"
                geo_params.extend([box[0], box[1], box[2], box[3]])
            text_sql, text_params = _place_text_sql(place_geoms)
            if text_sql:
                where.append(f"(({geo_sql}) OR ((listings.lat IS NULL OR listings.lon IS NULL) AND {text_sql}))")
                params.extend([*geo_params, *text_params])
            else:
                where.append(geo_sql)
                params.extend(geo_params)
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
                elif estate == "pozemek":
                    estate_parts.append(
                        "(listings.extras LIKE '%\"estate\": \"Pozemek%' OR listings.name LIKE '%pozemek%')"
                    )
                elif estate == "projekt":
                    estate_parts.append(
                        "(listings.extras LIKE '%\"estate\": \"Projekt%' OR listings.name LIKE '%projekt%')"
                    )
                elif estate == "komercni":
                    estate_parts.append(
                        "(listings.extras LIKE '%\"estate\": \"Komerč%' OR listings.extras LIKE '%\"estate\": \"Kancel%' OR listings.name LIKE '%kancel%')"
                    )
                elif estate == "ostatni":
                    estate_parts.append(
                        "(listings.extras LIKE '%\"estate\": \"Ostatní%' OR listings.extras LIKE '%\"estate\": \"Garáž%')"
                    )
            if estate_parts:
                where.append("(" + " OR ".join(estate_parts) + ")")
        ownership = _csv(filters.get("ownership"))
        if ownership:
            _or_likes(
                where,
                params,
                "listings.extras",
                [f'%"Vlastnictví", "value": "{label}"%' for label in ownership]
                + [f'%"ownership": "{label}"%' for label in ownership],
            )
        conditions = _csv(filters.get("condition"))
        if conditions:
            patterns = []
            for label in conditions:
                patterns.append(f'%"Stav", "value": "{label}"%')
                patterns.append(f'%"condition": "{label}"%')
                if label == "Po rekonstrukci":
                    patterns.append('%"Stav", "value": "Po částečné rekonstrukci"%')
                if label == "Dobrý":
                    patterns.append("%Dobrý stav%")
                if label == "Špatný":
                    patterns.append("%Špatný stav%")
                if label == "Projekt":
                    patterns.append("%Projekt%")
            _or_likes(where, params, "listings.extras", patterns)
        buildings = _csv(filters.get("building"))
        if buildings:
            patterns = []
            for item in buildings:
                if item == "cihlova":
                    patterns.append("%Cihl%")
                elif item == "panelova":
                    patterns.append("%Panel%")
                elif item == "drevena":
                    patterns.append("%Dřev%")
                elif item == "kamenna":
                    patterns.append("%Kamenn%")
                elif item == "skeletova":
                    patterns.append("%Skelet%")
                elif item == "montovana":
                    patterns.append("%Montovan%")
                elif item == "smisena":
                    patterns.append("%Smíšen%")
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
                            '%"Vybavení", "value": "Zařízený"%',
                            '%"equipped": "Zařízený"%',
                        ]
                    )
                elif item == "castecne":
                    patterns.append('%"Vybavení", "value": "Částečně"%')
                    patterns.append('%"equipped": "Částečně zařízený"%')
                elif item == "nevybaveny":
                    patterns.extend(
                        [
                            '%"Vybavení", "value": "Nevybaven"%',
                            '%"Vybavení", "value": "Ne"%',
                            '%"Vybavení", "value": "Nezařízený"%',
                            '%"equipped": "Nezařízený"%',
                        ]
                    )
            _or_likes(where, params, "listings.extras", patterns)
        circle_active = False
        try:
            circle_active = (
                -90 <= float(filters.get("lat") or "") <= 90
                and -180 <= float(filters.get("lon") or "") <= 180
                and 50 <= float(filters.get("radius_m") or "") <= 50_000
            )
        except (TypeError, ValueError):
            circle_active = False
        if not place_geoms and not circle_active:
            _apply_map_bbox(where, params, filters)
        _apply_circle(where, params, filters)
        status = (filters.get("status") or "").strip()
        if status == "saved":
            where.append(_saved_listing_sql())
        elif status == "hidden":
            where.append(_hidden_listing_sql())
        elif status != "all":
            where.append(f"NOT {_hidden_listing_sql()}")
        hits = (filters.get("hits") or "").strip()
        if hits in {"today", "day"}:
            where.append("listings.notified = 1")
            where.append(
                """
                EXISTS (
                    SELECT 1 FROM events
                    WHERE events.listing_id = listings.id
                      AND events.monitor_id = listings.monitor_id
                      AND events.kind IN ('new', 'changed')
                      AND events.created_at >= ?
                )
                """
            )
            params.append(local_day_start())
        elif hits in {"1", "all", "notified", "hits"}:
            where.append("listings.notified = 1")
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
            if str(filters.get("places") or "").strip() and not place_geoms:
                return {"items": []}
            return self._catalog_pins(where, params, place_geoms, filters)
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
        identity = listing_identity_sql()
        if place_geoms:
            light_sql = f"""
                SELECT listings.id, listings.monitor_id, listings.lat, listings.lon,
                       listings.price_czk, listings.price_label, listings.name, listings.locality,
                       listings.listing_key, listings.canonical_key, listings.url
                FROM listings
                WHERE {clause}
                ORDER BY {order}
                LIMIT 8000
            """
            with self.connect(readonly=True) as conn:
                fetched = [dict(row) for row in conn.execute(light_sql, params).fetchall()]
            seen_keys: set[str] = set()
            matched: list[dict[str, Any]] = []
            for row in fetched:
                key = listing_identity(row)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if not listing_matches_places(row, place_geoms):
                    continue
                matched.append(row)
            total = len(matched)
            self._fill_missing_coords(matched, place_geoms)
            page = matched[offset : offset + limit]
            rows = []
            if page:
                holders = " OR ".join("(listings.monitor_id = ? AND listings.id = ?)" for _ in page)
                flat = [item for row in page for item in (row["monitor_id"], row["id"])]
                full_sql = f"""
                    SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                    FROM listings
                    LEFT JOIN monitors ON monitors.id = listings.monitor_id
                    WHERE {holders}
                """
                with self.connect(readonly=True) as conn:
                    by_id = {
                        (row["monitor_id"], row["id"]): dict(row)
                        for row in conn.execute(full_sql, flat)
                    }
                rows = [by_id[key] for row in page if (key := (row["monitor_id"], row["id"])) in by_id]
        else:
            fetch_limit = min((offset + limit) * 4, 2000)
            sql = f"""
                SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                FROM listings
                LEFT JOIN monitors ON monitors.id = listings.monitor_id
                WHERE {clause}
                ORDER BY {order}
                LIMIT ?
            """
            if filters.get("_bbox") is not None:
                with self.connect(readonly=True) as conn:
                    total = int(conn.execute(f"SELECT COUNT(*) FROM listings WHERE {clause}", params).fetchone()[0])
                    fetched = [dict(row) for row in conn.execute(sql, (*params, fetch_limit)).fetchall()]
            else:
                count_sql = f"SELECT COUNT(*) FROM listings WHERE {clause}"
                with self.connect(readonly=True) as conn:
                    total = int(conn.execute(count_sql, params).fetchone()[0])
                    fetched = [dict(row) for row in conn.execute(sql, (*params, fetch_limit)).fetchall()]
            seen_keys = set()
            rows = []
            for row in fetched:
                key = listing_identity(row)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                rows.append(row)
            rows = rows[offset : offset + limit]
        keys = [(row["monitor_id"], row["id"]) for row in rows]
        photos: dict[tuple[str, int], list[str]] = {}
        if keys:
            holders = " OR ".join("(monitor_id = ? AND listing_id = ?)" for _ in keys)
            flat = [item for pair in keys for item in pair]
            with self.connect(readonly=True) as conn:
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
        self._attach_catalog_extras(items, twins=False)
        facets = self.catalog_facets()
        payload = {"items": items, "total": total, "limit": limit, "offset": offset, "facets": facets}
        include_pins = str(filters.get("include_pins") or "1").strip().lower() not in {"0", "false", "no"}
        if place_geoms:
            payload["places"] = places.public_geoms(place_geoms)
            payload["pins"] = (
                [
                    pin
                    for row in matched
                    if (pin := _pin_with_place(row, place_geoms, approx=bool(row.get("_geo_approx"))))
                ]
                if include_pins
                else []
            )
        elif include_pins:
            payload["pins"] = self._catalog_pins(where, params, None, filters)["items"]
        else:
            payload["pins"] = []
        if not payload["total"]:
            payload["total"] = offset + len(items) + (limit if len(items) >= limit else 0)
        return payload

    def _catalog_pins(
        self,
        where: list[str],
        params: list[Any],
        place_geoms: list[dict[str, Any]] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clause = " AND ".join(where)
        filters = filters or {}
        anchors = [item for item in (filters.get("_bbox_anchors") or []) if isinstance(item, tuple)]
        bbox = filters.get("_bbox")
        wide = False
        if isinstance(bbox, tuple) and len(bbox) == 4:
            south, north, west, east = bbox
            wide = (north - south) + (east - west) > 1.5
        pin_limit = 8000 if place_geoms or wide or len(anchors) > 3 else 800
        span = float(filters.get("_bbox_span") or 0)
        cache_hit = False
        cache_key: tuple[Any, ...] | None = None
        leftover = 0
        leftover_sample: list[str] = []
        select = """
            SELECT listings.id, listings.monitor_id, listings.lat, listings.lon,
                   listings.price_czk, listings.price_label, listings.name, listings.locality,
                   listings.listing_key, listings.canonical_key, listings.url
            FROM listings
        """
        fetched: list[dict[str, Any]] = []
        if anchors and not place_geoms and wide and span >= 3.5:
            skip = {"south", "north", "west", "east", "lat", "lon", "radius_m", "limit", "offset", "sort", "places", "place_geoms", "place_empty"}
            cache_key = tuple(sorted((str(k), str(v)) for k, v in filters.items() if not str(k).startswith("_") and k not in skip))
            hit = self._city_pin_cache.get(cache_key)
            if hit and time.monotonic() - hit[0] < 45:
                fetched, leftover, leftover_sample = hit[1], hit[2], hit[3]
                cache_hit = True
                filters["_pin_leftover"] = leftover
                filters["_pin_leftover_sample"] = leftover_sample
        if not cache_hit:
            with self.connect(readonly=True) as conn:
                if anchors and not place_geoms and wide:
                    agg_sql = f"""
                    SELECT listings.locality AS locality, COUNT(*) AS n,
                           AVG(listings.lat) AS lat, AVG(listings.lon) AS lon
                    FROM (
                        SELECT listings.locality, listings.lat, listings.lon
                        FROM listings
                        WHERE {clause}
                        GROUP BY {listing_identity_sql()}
                    ) listings
                    GROUP BY listings.locality
                    """
                    cities: dict[str, dict[str, Any]] = {}
                    leftover = 0
                    leftover_sample = []

                    def add_city(label: str, alat: float, alon: float, n: int) -> None:
                        key = f"{round(alat, 3)}:{round(alon, 3)}:{label}"
                        bucket = cities.get(key)
                        if not bucket:
                            seed = 0
                            for ch in key:
                                seed = (seed * 33 + ord(ch)) & 0x7FFFFFFF
                            bucket = {
                                "id": seed,
                                "monitor_id": "__cluster__",
                                "lat": alat,
                                "lon": alon,
                                "locality": label,
                                "name": label,
                                "listing_key": f"cluster:{key}",
                                "canonical_key": f"cluster:{key}",
                                "url": "",
                                "count": 0,
                                "_geo_approx": True,
                            }
                            cities[key] = bucket
                        bucket["count"] += int(n or 0)

                    for row in conn.execute(agg_sql, params).fetchall():
                        loc = str(row["locality"] or "")
                        n = int(row["n"] or 0)
                        match = places.locality_anchor_match(loc, collapse_prague=False)
                        lat = row["lat"]
                        lon = row["lon"]
                        if match:
                            add_city(match[0], match[1], match[2], n)
                        elif lat is not None and lon is not None:
                            label = loc.split(",")[-1].strip() or loc or "Další"
                            add_city(label[:40], round(float(lat), 2), round(float(lon), 2), n)
                        else:
                            leftover += n
                            if len(leftover_sample) < 8:
                                leftover_sample.append(loc[:60])
                    if leftover:
                        add_city("Další", 49.82, 15.47, leftover)
                    fetched.extend(cities.values())
                    filters["_pin_leftover"] = leftover
                    filters["_pin_leftover_sample"] = leftover_sample
                    if cache_key is not None:
                        self._city_pin_cache[cache_key] = (time.monotonic(), list(fetched), leftover, leftover_sample)
                elif anchors and not place_geoms:
                    # Piny jen se souřadnicemi ve viewportu — dřívější LIMIT 400
                    # z celého locality-match setu podvzorkoval čtvrti (11→22 při zoomu).
                    pin_cap = max(int(pin_limit), 8000)
                    if isinstance(bbox, tuple) and len(bbox) == 4:
                        b_south, b_north, b_west, b_east = bbox
                        gps_sql = f"""
                        {select}
                        WHERE {clause}
                          AND listings.lat IS NOT NULL AND listings.lon IS NOT NULL
                          AND listings.lat BETWEEN ? AND ?
                          AND listings.lon BETWEEN ? AND ?
                        ORDER BY listings.notified DESC, listings.rowid ASC
                        LIMIT {pin_cap}
                        """
                        gps_rows = [
                            dict(row)
                            for row in conn.execute(
                                gps_sql, (*params, b_south, b_north, b_west, b_east)
                            ).fetchall()
                        ]
                    else:
                        gps_sql = f"""
                        {select}
                        WHERE {clause}
                          AND listings.lat IS NOT NULL AND listings.lon IS NOT NULL
                        ORDER BY listings.notified DESC, listings.rowid ASC
                        LIMIT {pin_cap}
                        """
                        gps_rows = [dict(row) for row in conn.execute(gps_sql, params).fetchall()]
                    fetched.extend(gps_rows)
                    null_sql = f"""
                    {select}
                    WHERE {clause}
                      AND (listings.lat IS NULL OR listings.lon IS NULL)
                    ORDER BY listings.first_seen DESC
                    LIMIT 2000
                    """
                    buckets: dict[str, list[dict[str, Any]]] = {}
                    for row in conn.execute(null_sql, params).fetchall():
                        item = dict(row)
                        loc = str(item.get("locality") or "")
                        point = places.approx_point_from_locality(loc)
                        if not point:
                            continue
                        if isinstance(bbox, tuple) and len(bbox) == 4:
                            b_south, b_north, b_west, b_east = bbox
                            if not (b_south <= point[0] <= b_north and b_west <= point[1] <= b_east):
                                continue
                        seed = abs(int(item.get("id") or 0))
                        item["lat"] = point[0] + ((seed % 17) - 8) * 0.0012
                        item["lon"] = point[1] + ((seed % 13) - 6) * 0.0016
                        item["_geo_approx"] = True
                        key = f"{round(point[0], 2)}:{round(point[1], 2)}"
                        bucket = buckets.setdefault(key, [])
                        if len(bucket) < 50:
                            bucket.append(item)
                    for bucket in buckets.values():
                        fetched.extend(bucket)
                else:
                    sql = f"""
                    {select}
                    WHERE {clause}
                      AND listings.lat IS NOT NULL AND listings.lon IS NOT NULL
                    ORDER BY listings.notified DESC, listings.rowid ASC
                    LIMIT {int(pin_limit)}
                    """
                    fetched.extend(dict(row) for row in conn.execute(sql, params).fetchall())
        items = []
        seen: set[str] = set()
        for row in fetched:
            key = listing_identity(row)
            if key in seen:
                continue
            if place_geoms and not places.point_matches(row.get("lat"), row.get("lon"), place_geoms):
                continue
            seen.add(key)
            pin = _pin_item(row)
            if row.get("_geo_approx"):
                pin["approx"] = True
            items.append(pin)
            if not wide and len(items) >= pin_limit:
                break
        return {"items": items}

    def catalog_facets(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._facets_cache is not None and now - self._facets_at < 30:
            return self._facets_cache
        with self.connect(readonly=True) as conn:
            dispositions = [
                row[0]
                for row in conn.execute(
                    "SELECT disposition FROM listings WHERE disposition IS NOT NULL AND disposition != '' GROUP BY disposition ORDER BY COUNT(*) DESC"
                )
            ]
            monitors = [
                {"id": row["id"], "name": row["name"], "enabled": bool(row["enabled"])}
                for row in conn.execute("SELECT id, name, enabled FROM monitors ORDER BY name")
            ]
            portals = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT portal FROM listing_links WHERE IFNULL(gone, 0) = 0 AND portal != '' ORDER BY portal"
                )
            ]
            if not portals:
                from app.sources import PORTAL_IDS, url_likes

                for portal_id in PORTAL_IDS:
                    likes = url_likes(portal_id) or (f"%{portal_id}%",)
                    if any(
                        conn.execute("SELECT 1 FROM listings WHERE url LIKE ? LIMIT 1", (like,)).fetchone()
                        for like in likes
                    ):
                        portals.append(portal_id)
        payload = {"dispositions": dispositions, "portals": portals, "monitors": monitors}
        self._facets_cache = payload
        self._facets_at = now
        return payload

    def catalog_item(
        self,
        monitor_id: str,
        listing_id: int | str | None = None,
        listing_key: str = "",
        url: str = "",
    ) -> dict[str, Any] | None:
        number: int | None = None
        try:
            if listing_id not in (None, ""):
                number = int(listing_id)
        except (TypeError, ValueError):
            number = None
        with self.connect() as conn:
            row = None
            if monitor_id and number is not None:
                row = conn.execute(
                    """
                    SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                    FROM listings
                    LEFT JOIN monitors ON monitors.id = listings.monitor_id
                    WHERE listings.monitor_id = ? AND listings.id = ?
                    """,
                    (monitor_id, number),
                ).fetchone()
            if row is None and (listing_key or url):
                row = conn.execute(
                    """
                    SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                    FROM listings
                    LEFT JOIN monitors ON monitors.id = listings.monitor_id
                    WHERE listings.listing_key = ? OR listings.canonical_key = ? OR listings.url = ?
                    ORDER BY listings.last_seen DESC
                    LIMIT 1
                    """,
                    (listing_key or url, listing_key or url, url or listing_key),
                ).fetchone()
            if row is None and number is not None:
                row = conn.execute(
                    """
                    SELECT listings.*, monitors.name AS monitor_name, monitors.search_url AS search_url
                    FROM listings
                    LEFT JOIN monitors ON monitors.id = listings.monitor_id
                    WHERE listings.id = ?
                    ORDER BY listings.last_seen DESC
                    LIMIT 1
                    """,
                    (number,),
                ).fetchone()
            # Extension-ingested rows may live only in catalog_listings briefly / primarily
            if row is None and (listing_key or url or number is not None):
                cat = None
                if listing_key or url:
                    cat = conn.execute(
                        """
                        SELECT * FROM catalog_listings
                        WHERE listing_key = ? OR canonical_key = ? OR url = ?
                        ORDER BY last_seen DESC
                        LIMIT 1
                        """,
                        (listing_key or url, listing_key or url, url or listing_key),
                    ).fetchone()
                if cat is None and number is not None:
                    cat = conn.execute(
                        """
                        SELECT * FROM catalog_listings
                        WHERE CAST(id AS TEXT) = ?
                        ORDER BY last_seen DESC
                        LIMIT 1
                        """,
                        (str(number),),
                    ).fetchone()
                if cat is not None:
                    data = dict(cat)
                    data["monitor_id"] = CATALOG_MONITOR_ID
                    data["monitor_name"] = "Katalog"
                    data["search_url"] = ""
                    item = public_listing(data)
                    item["photos"] = _photo_urls([data["image_url"]] if data.get("image_url") else [])
                    item["price_history"] = _price_series([], item)
                    self._attach_catalog_extras([item], twins=True)
                    return item
            if not row:
                return None
            item = public_listing(dict(row))
            monitor_id = item["monitor_id"]
            listing_id = item["id"]
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
        self._attach_catalog_extras([item], twins=True)
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
        url_key = listing_key(url)
        sql = """
            SELECT 1 FROM listings
            WHERE notified = 1
              AND (
                url = ?
                OR canonical_key = (
                    SELECT canonical_key FROM listing_links WHERE url = ? OR url_key = ? LIMIT 1
                )
                OR listing_key = ?
              )
        """
        params: list[Any] = [url, url, url_key, url_key]
        if exclude_monitor_id:
            sql += " AND monitor_id != ?"
            params.append(exclude_monitor_id)
        with self.connect() as conn:
            return bool(conn.execute(sql, params).fetchone())

    def enqueue_listing_ping(
        self,
        listing: Listing,
        webhook_url: str,
        *,
        monitor_id: str,
        template_config: dict[str, Any] | None,
        monitor_name: str = "",
    ) -> bool:
        return self._enqueue_ping(
            ping_type="listing",
            kind=listing.kind or "new",
            listing_url=listing.url,
            listing_id=listing.id,
            monitor_id=monitor_id,
            webhook_url=webhook_url,
            payload={
                "listing": listing.to_dict(),
                "template_config": template_config,
                "monitor_name": monitor_name,
            },
        )

    def enqueue_sold_ping(self, row: dict[str, Any], webhook_url: str, monitor_name: str = "") -> bool:
        return self._enqueue_ping(
            ping_type="sold",
            kind="sold",
            listing_url=row.get("url") or "",
            listing_id=int(row["id"]) if row.get("id") is not None else None,
            monitor_id=str(row.get("monitor_id") or ""),
            webhook_url=webhook_url,
            payload={"row": dict(row), "monitor_name": monitor_name},
        )

    def _enqueue_ping(
        self,
        *,
        ping_type: str,
        kind: str,
        listing_url: str,
        listing_id: int | None,
        monitor_id: str,
        webhook_url: str,
        payload: dict[str, Any],
    ) -> bool:
        webhook_url = usable_discord_webhook(webhook_url)
        listing_url = (listing_url or "").strip()
        if not webhook_url or not listing_url:
            return False
        key = listing_key(listing_url)
        with self.connect() as conn:
            found = conn.execute(
                "SELECT canonical_key FROM listing_links WHERE url = ? OR url_key = ? LIMIT 1",
                (listing_url, key),
            ).fetchone()
            if found and found["canonical_key"]:
                key = str(found["canonical_key"])
        channel = channel_key(webhook_url)
        now = utc_now()
        with self.connect() as conn:
            pending = conn.execute(
                """
                SELECT 1 FROM ping_queue
                WHERE listing_key = ? AND channel_key = ? AND ping_type = ?
                  AND status IN ('queued', 'sending')
                """,
                (key, channel, ping_type),
            ).fetchone()
            if pending:
                return False
            if kind != "changed":
                sent = conn.execute(
                    """
                    SELECT 1 FROM discord_channel_sent
                    WHERE listing_key = ? AND channel_key = ? AND ping_type = ?
                    """,
                    (key, channel, ping_type),
                ).fetchone()
                if sent:
                    return False
            try:
                conn.execute(
                    """
                    INSERT INTO ping_queue(
                        listing_key, channel_key, ping_type, webhook_url, listing_url,
                        listing_id, monitor_id, kind, payload, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        key,
                        channel,
                        ping_type,
                        webhook_url,
                        listing_url,
                        listing_id,
                        monitor_id,
                        kind,
                        json.dumps(payload, ensure_ascii=False),
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def claim_next_ping(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM ping_queue
                WHERE status = 'queued'
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE ping_queue
                SET status = 'sending', attempts = attempts + 1
                WHERE id = ? AND status = 'queued'
                """,
                (row["id"],),
            )
            if conn.total_changes == 0:
                return None
            data = dict(row)
            data["status"] = "sending"
            data["attempts"] = int(row["attempts"] or 0) + 1
            return data

    def mark_ping_sent(self, ping: dict[str, Any]) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE ping_queue SET status = 'sent', processed_at = ?, error = NULL WHERE id = ?",
                (now, ping["id"]),
            )
            conn.execute(
                """
                INSERT INTO discord_channel_sent(
                    listing_key, channel_key, ping_type, listing_url, webhook_url,
                    monitor_id, kind, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(listing_key, channel_key, ping_type) DO UPDATE SET
                    listing_url = excluded.listing_url,
                    webhook_url = excluded.webhook_url,
                    monitor_id = excluded.monitor_id,
                    kind = excluded.kind,
                    sent_at = excluded.sent_at
                """,
                (
                    ping["listing_key"],
                    ping["channel_key"],
                    ping["ping_type"],
                    ping["listing_url"],
                    ping["webhook_url"],
                    ping.get("monitor_id"),
                    ping.get("kind"),
                    now,
                ),
            )

    def mark_ping_failed(self, ping_id: int, error: str, attempts: int, *, max_attempts: int = 8) -> None:
        now = utc_now()
        status = "failed" if attempts >= max_attempts else "queued"
        with self.connect() as conn:
            conn.execute(
                "UPDATE ping_queue SET status = ?, processed_at = ?, error = ? WHERE id = ?",
                (status, now, (error or "")[:500], ping_id),
            )

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

    def game_listing_pool(self, *, limit: int = 240, budget_sec: float = 0.2) -> list[dict[str, Any]]:
        """Newest priced catalog rows for marketing games. Bounded, extras-free, interruptible."""
        cap = max(12, min(400, int(limit or 240)))
        deadline = time.monotonic() + max(0.05, float(budget_sec or 0.2))
        try:
            conn = self.connect(quick=True)
        except sqlite3.OperationalError:
            return []
        fetched: list[Any] = []
        try:
            def _abort() -> int:
                return 1 if time.monotonic() >= deadline else 0

            conn.set_progress_handler(_abort, 20_000)
            fetched = conn.execute(
                """
                SELECT listing_key, name, locality, disposition, area_m2, price_czk, price_label,
                       image_url, url, portal, first_seen, last_seen
                FROM catalog_listings
                WHERE gone = 0
                  AND price_czk BETWEEN 6000 AND 90000
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (cap,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        finally:
            try:
                conn.set_progress_handler(None, 0)
            except Exception:
                pass
            conn.close()
        rows: list[dict[str, Any]] = []
        for row in fetched:
            item = dict(row)
            if not _is_game_rental(item):
                continue
            image = str(item.get("image_url") or "").strip()
            if not image:
                continue
            price = item.get("price_czk")
            try:
                price_n = int(price) if price not in (None, "") else 0
            except (TypeError, ValueError):
                price_n = 0
            if price_n < 6000:
                continue
            rows.append(
                {
                    "id": str(item.get("listing_key") or item.get("url") or ""),
                    "name": item.get("name") or "",
                    "locality": item.get("locality") or "",
                    "disposition": item.get("disposition") or "",
                    "area_m2": item.get("area_m2"),
                    "price_czk": price_n,
                    "price_label": item.get("price_label") or "",
                    "image_url": image,
                    "url": item.get("url"),
                    "portal": item.get("portal") or portal_from_url(str(item.get("url") or "")),
                    "vanish_hours": _game_vanish_hours(item.get("first_seen"), item.get("last_seen")),
                }
            )
        return rows

    def public_gone_fast_rentals(self, *, days: int = 3, limit: int = 4) -> list[dict[str, Any]]:
        now = time.monotonic()
        cached = self._gone_fast_cache
        if cached and now - cached[0] < 20.0:
            return cached[1]
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
        cap = max(24, min(200, int(limit or 4) * 40))
        try:
            conn = self.connect(quick=True)
        except sqlite3.OperationalError:
            return cached[1] if cached else []
        try:
            rows = conn.execute(
                """
                SELECT locality, disposition, area_m2, price_czk, price_label, first_seen, last_seen,
                       created_on, image_url, url
                FROM listings
                WHERE gone = 1
                  AND image_url != ''
                  AND last_seen IS NOT NULL
                  AND last_seen >= ?
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (since, cap),
            ).fetchall()
        except sqlite3.OperationalError:
            return cached[1] if cached else []
        finally:
            conn.close()
        ranked: list[tuple[float, dict[str, Any], str, str, str]] = []
        seen_url: set[str] = set()
        for row in rows:
            item = _gone_rental_card(dict(row))
            if not item:
                continue
            url = item.pop("_url", "")
            place = item.pop("_place", "")
            gone_key = item.pop("_gone", "")
            if not url or url in seen_url:
                continue
            seen_url.add(url)
            ranked.append((float(item.pop("_hours")), item, url, place, gone_key))
        ranked.sort(key=lambda row: row[0])
        picked: list[dict[str, Any]] = []
        seen_place: set[str] = set()
        seen_gone: set[str] = set()
        for _hours, item, _url, place, gone_key in ranked:
            if place in seen_place or (gone_key and gone_key in seen_gone):
                continue
            seen_place.add(place)
            if gone_key:
                seen_gone.add(gone_key)
            picked.append(item)
            if len(picked) >= limit:
                break
        self._gone_fast_cache = (now, picked)
        return picked

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
                  AND listings.monitor_id != ?
                ORDER BY listings.last_seen DESC
                LIMIT ?
                """,
                (CATALOG_MONITOR_ID, limit),
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
        self._attach_catalog_extras(items, twins=False)
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
            key = listing_identity(row)
            if key in seen:
                continue
            seen.add(key)
            item = public_listing(row)
            item["kind"] = row.get("event_kind") or item.get("last_kind")
            items.append(item)
        return items

    def _attach_catalog_extras(self, items: list[dict[str, Any]], *, twins: bool = False) -> None:
        if not items:
            return
        canons = [listing_identity(item) for item in items]
        urls = [item.get("url") for item in items if item.get("url")]
        with self.connect(readonly=True) as conn:
            links_map = self._links_for(conn, canons)
            extra_urls = [
                str(link.get("url") or "")
                for group in links_map.values()
                for link in group
                if link.get("url")
            ]
            lookup_urls = [url for url in dict.fromkeys([*urls, *extra_urls]) if url]
            users: dict[str, dict[str, str]] = {}
            if lookup_urls:
                holders = ",".join("?" * len(lookup_urls))
                users = {
                    row["url"]: {"status": row["status"] or "", "note": row["note"] or ""}
                    for row in conn.execute(
                        f"SELECT url, status, note FROM listing_user WHERE url IN ({holders})",
                        lookup_urls,
                    )
                }
            monitor_sql = """
                SELECT listings.url, listings.listing_key, listings.canonical_key, monitors.id, monitors.name
                FROM listings
                JOIN monitors ON monitors.id = listings.monitor_id
                WHERE 0
            """
            monitor_params: list[Any] = []
            if lookup_urls:
                url_holders = ",".join("?" * len(lookup_urls))
                monitor_sql += f" OR listings.url IN ({url_holders})"
                monitor_params.extend(lookup_urls)
            key_holders = ",".join("?" * len(canons)) if canons else ""
            if canons:
                monitor_sql += f" OR listings.canonical_key IN ({key_holders}) OR listings.listing_key IN ({key_holders})"
                monitor_params.extend(canons)
                monitor_params.extend(canons)
            monitor_sql += " GROUP BY COALESCE(NULLIF(listings.canonical_key, ''), NULLIF(listings.listing_key, ''), listings.url), monitors.id ORDER BY monitors.name"
            monitor_rows = conn.execute(monitor_sql, monitor_params).fetchall() if monitor_params else []
            history = {}
            hist_keys = [(item.get("monitor_id"), item.get("id")) for item in items if item.get("monitor_id") and item.get("id") is not None]
            if hist_keys:
                clause = " OR ".join("(monitor_id = ? AND listing_id = ?)" for _ in hist_keys)
                flat = [item for pair in hist_keys for item in pair]
                for row in conn.execute(
                    f"SELECT monitor_id, listing_id, price_czk FROM price_history WHERE {clause} AND price_czk IS NOT NULL",
                    flat,
                ):
                    history.setdefault((row["monitor_id"], int(row["listing_id"])), []).append(int(row["price_czk"]))
        monitors_by_url: dict[str, list[dict[str, str]]] = {}
        monitors_by_key: dict[str, list[dict[str, str]]] = {}
        for row in monitor_rows:
            entry = {"id": row["id"], "name": row["name"]}
            for bucket, map_key in (
                (monitors_by_url, row["url"]),
                (monitors_by_key, row["listing_key"]),
                (monitors_by_key, row["canonical_key"]),
            ):
                if not map_key:
                    continue
                seen_ids = {item["id"] for item in bucket.setdefault(map_key, [])}
                if entry["id"] not in seen_ids:
                    bucket[map_key].append(entry)
        for item in items:
            identity = listing_identity(item)
            item["canonical_key"] = identity
            item["listing_key"] = identity
            links = links_map.get(identity) or []
            if not links and item.get("url"):
                links = [link_payload(str(item["url"]), extras=item.get("extras"), gone=bool(item.get("gone")))]
            live = [link for link in links if not link.get("gone") and link.get("url")]
            public_links = [
                {
                    "url": link.get("url"),
                    "portal": link.get("portal"),
                    "label": link.get("label") or portal_label(str(link.get("portal") or "")),
                    "agency": link.get("agency") or "",
                    "gone": bool(link.get("gone")),
                    "last_seen": link.get("last_seen") or "",
                }
                for link in links
            ]
            item["links"] = public_links
            if live:
                item["url"] = live[0]["url"]
            labels = list(dict.fromkeys(link["label"] for link in live if link.get("label")))
            if labels:
                item["portal"] = " · ".join(labels)
            user = users.get(item.get("url") or "", {})
            if not user:
                for link in links:
                    found = users.get(str(link.get("url") or ""))
                    if found:
                        user = found
                        break
            item["status"] = user.get("status") or ""
            item["note"] = user.get("note") or ""
            item["monitors"] = (
                monitors_by_key.get(identity)
                or monitors_by_url.get(item.get("url") or "")
                or [{"id": item.get("monitor_id"), "name": item.get("monitor_name")}]
            )
            others = [link for link in live if listing_key(str(link.get("url") or "")) != listing_key(str(item.get("url") or ""))]
            item["twin"] = (
                {
                    "url": others[0]["url"],
                    "portal": others[0].get("label") or others[0].get("portal"),
                    "agency": others[0].get("agency") or "",
                }
                if others
                else None
            )
            item["gone"] = bool(item.get("gone")) and not live
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
              AND listings.rowid IN (
                    SELECT MIN(l.rowid) FROM listings l
                    GROUP BY COALESCE(NULLIF(l.listing_key, ''), l.url)
              )
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

    def _monitor_row(
        self,
        row: sqlite3.Row,
        tracked: int | None = None,
        new_today: int | None = None,
    ) -> dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data["enabled"])
        data["seeded"] = bool(data["seeded"])
        data["interval_sec"] = data.get("interval_sec")
        data["portals"] = (data.get("portals") or "all") or "all"
        if data["portals"] not in {"all", "sreality", "bezrealitky", "idnes", "bazos"}:
            data["portals"] = "all"
        data["tracked"] = self.count(data["id"]) if tracked is None else int(tracked)
        data["new_today"] = self.new_today_count(data["id"]) if new_today is None else int(new_today)
        from app.catalog_sync import monitor_search_targets

        data["search_targets"] = monitor_search_targets(data)
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


def _listing_from_catalog_dict(row: dict[str, Any]) -> Listing:
    extras = row.get("extras") or {}
    if isinstance(extras, str):
        try:
            extras = json.loads(extras)
        except json.JSONDecodeError:
            extras = {}
    return listing_from_dict(
        {
            "id": int(row.get("id") or 0),
            "name": row.get("name") or "",
            "price_czk": row.get("price_czk"),
            "price_label": row.get("price_label") or "",
            "disposition": row.get("disposition") or "",
            "area_m2": row.get("area_m2"),
            "locality": row.get("locality") or "",
            "url": row.get("url") or "",
            "image_url": row.get("image_url"),
            "photos": row.get("photos") or [],
            "lat": row.get("lat"),
            "lon": row.get("lon"),
            "created_on": row.get("created_on"),
            "edited_on": row.get("edited_on"),
            "old_price_czk": row.get("old_price_czk"),
            "description": row.get("description"),
            "extras": extras or {},
        }
    )


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
    item.pop("_rn", None)
    identity = listing_identity(item)
    item["canonical_key"] = identity
    item["listing_key"] = identity
    item["portal"] = portal_label(portal_from_url(item.get("url") or ""))
    extras = item.get("extras")
    if isinstance(extras, str) and extras:
        try:
            extras = json.loads(extras)
        except json.JSONDecodeError:
            extras = {}
    item["extras"] = extras or {}
    item["flags"] = item["extras"].get("flags") or []
    item["gone"] = bool(item.get("gone"))
    item["links"] = item.get("links") or []
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
