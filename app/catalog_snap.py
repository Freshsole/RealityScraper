"""Read snapshot of the catalog: a small, deduplicated, fully indexed copy of the light listing columns.

The live `listings` table is wide (descriptions, JSON extras), is written by the scrapers all the time
and does not fit in the page cache of a small container, so every catalog query that scanned it was
slow and fought the writers for the disk. Catalog reads that only need the light columns are answered
from this file instead. It lives in its own SQLite file, so reads never wait for scraper writes.

- full rebuild: rarely (SNAP_FULL_EVERY_S), in a background thread, written to a temp file and swapped in
- incremental refresh: every SNAP_DELTA_EVERY_S, only rows first seen since the last refresh (index scan)
  and listings that were marked gone since then
- the page itself is still fetched from the live database by primary key, so prices/photos shown are
  always current; the snapshot only decides *which* offers match
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app import config

log = logging.getLogger("realitify.catalog_snap")

SNAP_PATH = config.DATA_DIR / "catalog_snap.sqlite"
SNAP_FULL_EVERY_S = 60 * 60
SNAP_DELTA_EVERY_S = 45
SNAP_MAX_AGE_S = 15 * 60  # older than this and readers fall back to the live table

# Columns a query may reference as `listings.<col>` to be answered from the snapshot.
COLUMNS = (
    "id", "monitor_id", "name", "price_czk", "price_label", "disposition", "area_m2", "locality", "url",
    "first_seen", "last_seen", "old_price_czk", "lat", "lon", "gone", "listing_key", "canonical_key", "extras",
    "x_live", "x_pub", "x_apt", "x_rent", "x_sale", "ident",
)

IDENT_SQL = "COALESCE(NULLIF(canonical_key, ''), NULLIF(listing_key, ''), url, monitor_id || ':' || id)"

# Only the two keys the catalog filters look at, written the way the scrapers write them
# (`"offer": "Pronájem"`), so the existing LIKE filters keep working on the snapshot.
_EXTRAS_SQL = (
    "'{' || CASE "
    "WHEN json_extract(extras,'$.offer') IS NOT NULL AND json_extract(extras,'$.estate') IS NOT NULL THEN "
    "'\"offer\": ' || json_quote(json_extract(extras,'$.offer')) || ', \"estate\": ' || json_quote(json_extract(extras,'$.estate')) "
    "WHEN json_extract(extras,'$.offer') IS NOT NULL THEN '\"offer\": ' || json_quote(json_extract(extras,'$.offer')) "
    "WHEN json_extract(extras,'$.estate') IS NOT NULL THEN '\"estate\": ' || json_quote(json_extract(extras,'$.estate')) "
    "ELSE '' END || '}'"
)

_SELECT = f"""
    SELECT {IDENT_SQL} AS ident, id, monitor_id, name, price_czk, price_label, disposition, area_m2, locality, url,
           first_seen, last_seen, old_price_czk, lat, lon, IFNULL(gone, 0), listing_key, canonical_key,
           CASE WHEN json_valid(extras) THEN {_EXTRAS_SQL} ELSE extras END,
           x_live, x_pub, x_apt, x_rent, x_sale
    FROM src.listings
"""

_SCHEMA = """
    CREATE TABLE listings (
        ident TEXT PRIMARY KEY, id INTEGER, monitor_id TEXT, name TEXT, price_czk INTEGER, price_label TEXT,
        disposition TEXT, area_m2 INTEGER, locality TEXT, url TEXT, first_seen TEXT, last_seen TEXT,
        old_price_czk INTEGER, lat REAL, lon REAL, gone INTEGER, listing_key TEXT, canonical_key TEXT, extras TEXT,
        x_live INTEGER, x_pub INTEGER, x_apt INTEGER, x_rent INTEGER, x_sale INTEGER
    );
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

_INDEXES = (
    "CREATE INDEX idx_s_first_seen ON listings(first_seen)",
    "CREATE INDEX idx_s_rent ON listings(x_live, x_pub, x_apt, x_rent, price_czk)",
    "CREATE INDEX idx_s_sale ON listings(x_live, x_pub, x_apt, x_sale, price_czk)",
    "CREATE INDEX idx_s_geo ON listings(lat, lon)",
    "CREATE INDEX idx_s_price ON listings(price_czk)",
    "CREATE INDEX idx_s_area ON listings(area_m2)",
    "CREATE INDEX idx_s_locality ON listings(locality COLLATE NOCASE)",
)

_state: dict[str, Any] = {"source": "", "built_at": 0.0, "delta_at": 0.0, "last_first_seen": "", "last_seen": ""}
_lock = threading.Lock()


def _src_uri(path: str) -> str:
    return f"file:{path}?mode=ro"


def ready_for(store_path: Any) -> bool:
    """True when the snapshot was built from this database and is fresh enough to trust."""
    if not _state["built_at"] or _state["source"] != str(store_path):
        return False
    return time.monotonic() - max(_state["built_at"], _state["delta_at"]) <= SNAP_MAX_AGE_S and SNAP_PATH.exists()


def connect(deadline: float | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(SNAP_PATH, timeout=5)
    if deadline is not None:
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 200_000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA cache_size=-65536")
    conn.execute("PRAGMA mmap_size=536870912")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def build_full(source_path: str) -> None:
    """Rebuild the whole snapshot from `source_path` and swap it in atomically."""
    started = time.monotonic()
    tmp = Path(str(SNAP_PATH) + ".building")
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(str(tmp) + suffix)
        except OSError:
            pass
    conn = sqlite3.connect(tmp, timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-131072")
        conn.executescript(_SCHEMA)
        conn.execute("ATTACH DATABASE ? AS src", (_src_uri(source_path),))
        # One pass over the live table; duplicates of the same offer collapse to one row.
        conn.execute(f"INSERT OR IGNORE INTO listings {_SELECT} ORDER BY first_seen DESC")
        row = conn.execute("SELECT MAX(first_seen), MAX(last_seen), COUNT(*) FROM listings").fetchone()
        for sql in _INDEXES:
            conn.execute(sql)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('source', ?)", (source_path,))
        conn.commit()
        conn.execute("DETACH DATABASE src")
        conn.execute("ANALYZE")
        conn.commit()
        conn.execute("PRAGMA journal_mode=WAL")  # persistent: readers never wait for the delta writer
    finally:
        conn.close()
    os.replace(tmp, SNAP_PATH)
    with _lock:
        _state.update(
            source=source_path,
            built_at=time.monotonic(),
            delta_at=time.monotonic(),
            last_first_seen=row[0] or "",
            last_seen=row[1] or "",
        )
    log.info("catalog snapshot built: %s rows in %.1fs", row[2], time.monotonic() - started)


def refresh_delta(source_path: str) -> None:
    """Add offers first seen since the last refresh and flag the ones that went away."""
    if _state["source"] != source_path or not _state["last_first_seen"]:
        return
    conn = sqlite3.connect(SNAP_PATH, timeout=10)
    try:
        conn.execute("ATTACH DATABASE ? AS src", (_src_uri(source_path),))
        since_new = _state["last_first_seen"]
        since_seen = _state["last_seen"]
        conn.execute(f"INSERT OR REPLACE INTO listings {_SELECT} WHERE first_seen >= ?", (since_new,))
        # Rows touched since the last refresh: copy their live flag / price / position.
        conn.execute(
            f"INSERT OR REPLACE INTO listings {_SELECT} WHERE last_seen >= ? AND IFNULL(gone, 0) = 1",
            (since_seen,),
        )
        row = conn.execute("SELECT MAX(first_seen), MAX(last_seen) FROM listings").fetchone()
        conn.commit()
        conn.execute("DETACH DATABASE src")
    finally:
        conn.close()
    with _lock:
        _state["delta_at"] = time.monotonic()
        _state["last_first_seen"] = row[0] or since_new
        _state["last_seen"] = row[1] or since_seen


def run_loop(source_path_fn) -> None:
    """Background thread: keep the snapshot fresh. Errors never leave the loop."""
    adopted = bool(_state["built_at"])
    last_full = time.monotonic() if adopted else 0.0
    if not adopted:
        time.sleep(90)  # let the web finish starting before the first full pass over the live table
    while True:
        try:
            path = str(source_path_fn())
            now = time.monotonic()
            if _state["source"] != path or now - last_full >= SNAP_FULL_EVERY_S:
                build_full(path)
                last_full = time.monotonic()
            else:
                refresh_delta(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("catalog snapshot refresh failed: %r", exc)
            _state["built_at"] = _state["built_at"]  # keep the old snapshot until it ages out
        time.sleep(SNAP_DELTA_EVERY_S)


def adopt_existing(source_path: str) -> bool:
    """Reuse the snapshot file a previous process left on the volume, so a restart is not a cold start."""
    try:
        newest = max(p.stat().st_mtime for p in (SNAP_PATH, Path(str(SNAP_PATH) + '-wal')) if p.exists())
        age = time.time() - newest
        conn = connect()
        try:
            src = conn.execute("SELECT value FROM meta WHERE key = 'source'").fetchone()
            row = conn.execute("SELECT MAX(first_seen), MAX(last_seen) FROM listings").fetchone()
        finally:
            conn.close()
        if not src or src[0] != source_path or age > SNAP_MAX_AGE_S * 4:
            return False
        with _lock:
            _state.update(
                source=source_path,
                built_at=time.monotonic() - age,
                delta_at=time.monotonic() - age,
                last_first_seen=row[0] or "",
                last_seen=row[1] or "",
            )
        return True
    except Exception:  # noqa: BLE001
        return False


def start(source_path_fn) -> threading.Thread:
    adopt_existing(str(source_path_fn()))
    thread = threading.Thread(target=run_loop, args=(source_path_fn,), name="catalog-snap", daemon=True)
    thread.start()
    return thread
