"""Precomputed SSR page cache in a separate SQLite file (never blocks on catalog scrapes)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config

log = logging.getLogger("realitify.ssr_cache")

SSR_CACHE_PATH = config.DATA_DIR / "ssr_cache.sqlite"
REBUILD_INTERVAL_SEC = 15 * 60

_lock = threading.Lock()
_last_rebuild_at: float = 0.0
_rebuild_running = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(writable: bool = False) -> sqlite3.Connection:
    path = Path(SSR_CACHE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    if writable:
        conn = sqlite3.connect(str(path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
    else:
        if not path.exists():
            # Create empty file so mode=ro open works after first writer init.
            init = sqlite3.connect(str(path), timeout=5)
            try:
                init.execute("PRAGMA journal_mode=WAL")
                init.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ssr_pages (
                        page_key TEXT PRIMARY KEY,
                        body TEXT NOT NULL,
                        data_as_of TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                init.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ssr_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                init.commit()
            finally:
                init.close()
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ssr_pages (
            page_key TEXT PRIMARY KEY,
            body TEXT NOT NULL,
            data_as_of TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ssr_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def get_page(page_key: str) -> dict[str, str] | None:
    """Return {body, data_as_of, updated_at} from cache, or None if missing."""
    try:
        with _connect(writable=False) as conn:
            row = conn.execute(
                "SELECT body, data_as_of, updated_at FROM ssr_pages WHERE page_key = ?",
                (page_key,),
            ).fetchone()
    except sqlite3.Error as exc:
        log.error("ssr_cache read failed key=%s: %s", page_key, exc)
        return None
    if not row:
        return None
    return {
        "body": str(row["body"] or ""),
        "data_as_of": str(row["data_as_of"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def get_json(page_key: str) -> Any | None:
    page = get_page(page_key)
    if not page:
        return None
    try:
        return json.loads(page["body"])
    except json.JSONDecodeError:
        log.error("ssr_cache invalid JSON key=%s", page_key)
        return None


def get_meta(key: str) -> str | None:
    try:
        with _connect(writable=False) as conn:
            row = conn.execute("SELECT value FROM ssr_meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    return str(row["value"]) if row else None


def last_success_as_of() -> str:
    return get_meta("last_success_data_as_of") or ""


def _put_many(conn: sqlite3.Connection, rows: list[tuple[str, str, str, str]]) -> None:
    conn.executemany(
        """
        INSERT INTO ssr_pages(page_key, body, data_as_of, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(page_key) DO UPDATE SET
            body = excluded.body,
            data_as_of = excluded.data_as_of,
            updated_at = excluded.updated_at
        """,
        rows,
    )


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO ssr_meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def trh_key(locality: str, offer: str, disposition: str = "") -> str:
    from app.market_pages import slugify_locality
    from app.market_seo import slugify_disposition

    base = f"trh:{slugify_locality(locality)}:{offer}"
    if disposition:
        return f"{base}:{slugify_disposition(disposition)}"
    return base


def _flush_pages(
    rows: list[tuple[str, str, str, str]],
    *,
    data_as_of: str,
    reason: str,
    stage: str,
) -> None:
    """Persist a batch immediately so / and /trh stop 503 before the full trh rebuild finishes."""
    if not rows:
        return
    with _connect(writable=True) as conn:
        _init_schema(conn)
        with conn:
            _put_many(conn, rows)
            _set_meta(conn, "last_success_data_as_of", data_as_of)
            _set_meta(conn, "last_success_at", data_as_of)
            _set_meta(conn, "last_success_reason", f"{reason}:{stage}")
            _set_meta(conn, "last_success_page_count", str(len(rows)))
            _set_meta(conn, "last_error", "")
    print(f"ssr_cache flush stage={stage} pages={len(rows)} as_of={data_as_of}", flush=True)


def _retry_catalog(fn, *, label: str, attempts: int = 8, delay_s: float = 2.0) -> Any:
    """Retry catalog reads under scrape lock pressure (background thread, long busy_timeout)."""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            print(f"ssr_cache {label} attempt={i + 1}/{attempts} err={exc}", flush=True)
            time.sleep(delay_s * (1 + i * 0.5))
    assert last is not None
    raise last


def _catalog_active_count(store: Any) -> int:
    # Background thread → store.connect() uses 30s busy_timeout (not the 5s readonly path).
    with store.connect() as conn:
        try:
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.OperationalError:
            pass
        row = conn.execute(
            "SELECT COUNT(*) AS active_count FROM listings WHERE IFNULL(gone, 0) = 0"
        ).fetchone()
    return int(row["active_count"] or 0) if row else 0


def rebuild(store: Any, *, reason: str = "manual") -> dict[str, Any]:
    """Heavy recompute from catalog store → write to ssr_cache.sqlite. Keeps old pages on failure."""
    global _last_rebuild_at, _rebuild_running
    with _lock:
        if _rebuild_running:
            return {"ok": False, "reason": "already_running"}
        _rebuild_running = True
    started = time.monotonic()
    data_as_of = _utc_now()
    rows: list[tuple[str, str, str, str]] = []
    trh_count = 0
    print(f"ssr_cache rebuild start reason={reason}", flush=True)
    try:
        from app import market_pages, market_seo

        # 1) Core public pages first — flush ASAP so homepage /trh leave 503.
        active = int(_retry_catalog(lambda: _catalog_active_count(store), label="catalog_total_active"))
        top_html = _retry_catalog(lambda: market_seo.top_localities_html(store, 10), label="top_localities_html")
        home_payload = {
            "active_count": active,
            "portals_count": len(market_pages.PUBLIC_PORTAL_LABELS),
            "portals_list": market_pages.public_portals_sentence(),
            "top_localities_html": top_html,
            "data_as_of": data_as_of,
        }
        core: list[tuple[str, str, str, str]] = [
            ("home", json.dumps(home_payload, ensure_ascii=False), data_as_of, data_as_of),
            ("home:md", market_pages.render_home_md(store), data_as_of, data_as_of),
            ("trh_hub:html", market_seo.render_trh_hub(store), data_as_of, data_as_of),
            ("trh_hub:md", market_seo.render_trh_hub_md(store), data_as_of, data_as_of),
            ("llms", market_pages.llms_txt(store), data_as_of, data_as_of),
            ("llms_full", market_pages.llms_full_txt(store), data_as_of, data_as_of),
            ("index:html", market_pages.render_index(store), data_as_of, data_as_of),
            ("index:md", market_pages.render_index_md(store), data_as_of, data_as_of),
            ("index:csv", market_pages.index_csv(store), data_as_of, data_as_of),
            ("faq:html", market_pages.render_faq(store), data_as_of, data_as_of),
            ("faq:md", market_pages.render_faq_md(store), data_as_of, data_as_of),
            ("about:html", market_pages.render_about_cs(store), data_as_of, data_as_of),
            ("about:md", market_pages.render_about_cs_md(store), data_as_of, data_as_of),
            ("about_en:html", market_pages.render_about_en(), data_as_of, data_as_of),
            ("mcp_docs:md", market_pages.render_mcp_docs_md(), data_as_of, data_as_of),
        ]
        rows.extend(core)
        _flush_pages(core, data_as_of=data_as_of, reason=reason, stage="core")

        # 2) Inventory + per-/trh pages (can take minutes under scrape load).
        inv = _retry_catalog(lambda: market_pages.market_inventory(store), label="market_inventory")
        included = inv.get("included") or []
        try:
            disp_paths = market_seo.list_disposition_paths(store)
        except Exception as exc:
            log.error("ssr_cache disposition paths failed: %s", exc)
            print(f"ssr_cache disposition paths failed: {exc}", flush=True)
            disp_paths = []

        redirects: dict[str, str] = {}
        trh_rows: list[tuple[str, str, str, str]] = []
        for row in included:
            loc = row["locality"]
            offer = row["offer"]
            key = trh_key(loc, offer)
            try:
                report = store.catalog_locality_report(loc, offer)
                html = market_seo.render_trh_page(store, loc, offer)
                md = market_seo.render_trh_page_md(store, loc, offer)
            except ValueError:
                path = row.get("path") or f"/trh/{loc}/{offer}"
                redirects[path] = market_seo.redirect_path_for_thin(store, loc, offer) or "/trh"
                continue
            except Exception as exc:
                log.error("ssr_cache trh page failed %s/%s: %s", loc, offer, exc)
                continue
            trh_rows.append((f"{key}:html", html, data_as_of, data_as_of))
            trh_rows.append((f"{key}:md", md, data_as_of, data_as_of))
            trh_rows.append((f"{key}:report", json.dumps(report, ensure_ascii=False), data_as_of, data_as_of))
            trh_count += 1
            if len(trh_rows) >= 30:
                _flush_pages(trh_rows, data_as_of=data_as_of, reason=reason, stage="trh_batch")
                rows.extend(trh_rows)
                trh_rows = []

        for path, loc, offer, disp in disp_paths:
            key = trh_key(loc, offer, disp)
            try:
                report = store.catalog_locality_report(loc, offer, disp)
                html = market_seo.render_trh_page(store, loc, offer, disp)
                md = market_seo.render_trh_page_md(store, loc, offer, disp)
            except ValueError:
                redirects[path] = market_seo.redirect_path_for_thin(store, loc, offer, disp) or f"/trh/{market_pages.slugify_locality(loc)}/{offer}"
                continue
            except Exception as exc:
                log.error("ssr_cache trh disp failed %s/%s/%s: %s", loc, offer, disp, exc)
                continue
            trh_rows.append((f"{key}:html", html, data_as_of, data_as_of))
            trh_rows.append((f"{key}:md", md, data_as_of, data_as_of))
            trh_rows.append((f"{key}:report", json.dumps(report, ensure_ascii=False), data_as_of, data_as_of))
            trh_count += 1
            if len(trh_rows) >= 30:
                _flush_pages(trh_rows, data_as_of=data_as_of, reason=reason, stage="trh_disp_batch")
                rows.extend(trh_rows)
                trh_rows = []

        trh_rows.append(("redirects", json.dumps(redirects, ensure_ascii=False), data_as_of, data_as_of))
        trh_rows.append(
            (
                "inventory",
                json.dumps({"included": included, "data_as_of": data_as_of}, ensure_ascii=False),
                data_as_of,
                data_as_of,
            )
        )
        _flush_pages(trh_rows, data_as_of=data_as_of, reason=reason, stage="final")
        rows.extend(trh_rows)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        _last_rebuild_at = time.monotonic()
        msg = (
            f"ssr_cache rebuilt reason={reason} pages={len(rows)} "
            f"trh={trh_count} ms={elapsed_ms} as_of={data_as_of}"
        )
        log.info(msg)
        print(msg, flush=True)
        return {
            "ok": True,
            "reason": reason,
            "pages": len(rows),
            "trh_pages": trh_count,
            "data_as_of": data_as_of,
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        log.exception("ssr_cache rebuild failed reason=%s: %s", reason, exc)
        print(f"ssr_cache rebuild failed reason={reason}: {exc}", flush=True)
        try:
            with _connect(writable=True) as conn:
                _init_schema(conn)
                with conn:
                    _set_meta(conn, "last_error", f"{_utc_now()} {type(exc).__name__}: {exc}"[:2000])
                    _set_meta(conn, "last_error_reason", reason)
        except Exception:
            log.exception("ssr_cache failed to persist rebuild error")
        return {"ok": False, "reason": reason, "error": str(exc)}
    finally:
        with _lock:
            _rebuild_running = False


def ensure_fresh(store: Any, *, force: bool = False, reason: str = "interval") -> dict[str, Any]:
    global _last_rebuild_at
    if not force and (time.monotonic() - _last_rebuild_at) < REBUILD_INTERVAL_SEC:
        # Still rebuild if cache is empty (first boot).
        if get_page("home") is not None:
            return {"ok": True, "skipped": True, "reason": "fresh"}
        reason = "empty_cache"
    return rebuild(store, reason=reason)


def schedule_loop(store_factory: Any) -> None:
    """Daemon thread: rebuild every 15 minutes (and immediately if cache empty)."""

    def _run() -> None:
        time.sleep(5)
        while True:
            try:
                store = store_factory() if callable(store_factory) else store_factory
                if store is not None:
                    ensure_fresh(store, reason="interval")
            except Exception:
                log.exception("ssr_cache schedule loop error")
            time.sleep(REBUILD_INTERVAL_SEC)

    threading.Thread(target=_run, name="ssr-cache-loop", daemon=True).start()
