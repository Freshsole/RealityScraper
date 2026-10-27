"""In-memory fingerprints of catalog rows so list crawls can skip unchanged listings.

A list page is ~95 % listings we already store unchanged. Instead of re-writing
every column for each of them, the upsert only touches `last_seen` for rows whose
fingerprint matches, and discovery stops paginating once a page holds nothing new.
"""

from __future__ import annotations

import threading
from typing import Any

Fingerprint = tuple[Any, ...]

_LOCK = threading.Lock()
_CACHES: dict[str, dict[str, Fingerprint]] = {}


def listing_fingerprint(listing: Any) -> Fingerprint:
    return (
        listing.price_czk,
        listing.name,
        listing.disposition,
        listing.area_m2,
        listing.locality,
        listing.edited_on,
        listing.old_price_czk,
        listing.lat is not None,
        bool(listing.image_url),
    )


def _row_fingerprint(row: Any) -> Fingerprint:
    return (
        row[1],
        row[2],
        row[3],
        row[4],
        row[5],
        row[6],
        row[7],
        row[8] is not None,
        bool(row[9]),
    )


def cache_for(path: str, conn: Any, limit: int | None = None) -> dict[str, Fingerprint]:
    """Fingerprints of live catalog rows, loaded once per DB file.

    `limit` loads only the most recently seen rows: enough for discovery, which only compares the newest pages of
    every portal, at a fraction of the memory/time of the whole catalog. Rows missing from a partial cache are
    simply treated as changed, i.e. written normally. Ordered by last_seen because (gone, last_seen) is indexed;
    ordering by first_seen made SQLite walk every gone row of the table (130 s for 26k live rows).
    """
    with _LOCK:
        cache = _CACHES.get(path)
        if cache is not None:
            return cache
    loaded: dict[str, Fingerprint] = {}
    sql = (
        "SELECT listing_key, price_czk, name, disposition, area_m2, locality, edited_on,"
        " old_price_czk, lat, image_url FROM catalog_listings WHERE gone = 0"
    )
    if limit:
        sql += f" ORDER BY last_seen DESC LIMIT {int(limit)}"
    for row in conn.execute(sql):
        loaded[str(row[0])] = _row_fingerprint(row)
    with _LOCK:
        return _CACHES.setdefault(path, loaded)


def peek(path: str) -> dict[str, Fingerprint] | None:
    with _LOCK:
        return _CACHES.get(path)


def is_known(path: str, key: str, listing: Any) -> bool:
    cache = peek(path)
    return cache is not None and cache.get(key) == listing_fingerprint(listing)


def forget(path: str, keys: list[str]) -> None:
    cache = peek(path)
    if cache:
        for key in keys:
            cache.pop(key, None)


_RESULTS: dict[str, dict[str, tuple[Fingerprint, str, float]]] = {}


def results_cache(path: str) -> dict[str, tuple[Fingerprint, str, float]]:
    """url key -> (fingerprint, catalog row key) for the monitor poll path.

    Single-listing upserts store rows under their canonical key (`url:host/path`), batch upserts under
    host+path, so the row key to touch has to be remembered next to the fingerprint. Filled by the first
    full upsert of each listing; a restart just means one full poll."""
    with _LOCK:
        return _RESULTS.setdefault(path, {})
