"""Persistence of the e-mailing layer: overrides, send log, campaigns (tables live in the global DB)."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from app.store import Store

_bound: "Store | None" = None
_lock = threading.Lock()
_schema_ready: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_templates (
            key TEXT PRIMARY KEY,
            subject TEXT NOT NULL DEFAULT '',
            preheader TEXT NOT NULL DEFAULT '',
            html TEXT NOT NULL DEFAULT '',
            text_body TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT,
            updated_by TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            scheduled_at TEXT,
            sent_at TEXT,
            template_key TEXT,
            campaign_id INTEGER,
            user_id TEXT,
            to_email TEXT NOT NULL,
            stream TEXT NOT NULL DEFAULT 'transactional',
            subject TEXT NOT NULL DEFAULT '',
            html TEXT NOT NULL DEFAULT '',
            text_body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            dedupe_key TEXT,
            opened_at TEXT,
            open_count INTEGER NOT NULL DEFAULT 0,
            clicked_at TEXT,
            click_count INTEGER NOT NULL DEFAULT 0,
            meta TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_email_log_dedupe ON email_log(dedupe_key) WHERE dedupe_key IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_log_status ON email_log(status, scheduled_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_log_user ON email_log(user_id, template_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_log_campaign ON email_log(campaign_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            mode TEXT NOT NULL DEFAULT 'text',
            subject TEXT NOT NULL DEFAULT '',
            preheader TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            segment_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft',
            scheduled_at TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            total INTEGER NOT NULL DEFAULT 0,
            created_by TEXT
        )
        """
    )
    conn.execute("CREATE TABLE IF NOT EXISTS email_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS email_suppressions (email TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)"
    )


def bind(store: "Store") -> None:
    """Remember the global store (web / worker call this on startup; tests bind a temp one)."""
    global _bound
    _bound = store


def is_bound() -> bool:
    """True once the app (or a test) has bound the global store; hooks stay silent before that."""
    return _bound is not None


def global_store() -> "Store":
    global _bound
    with _lock:
        if _bound is None:
            from app import config
            from app.store import Store

            _bound = Store(config.DB_PATH)
        return _bound


@contextmanager
def db(store: "Store | None" = None) -> Iterator[sqlite3.Connection]:
    target = store or global_store()
    conn = target.connect()
    try:
        key = str(getattr(target, "path", ""))
        if key not in _schema_ready:
            ensure_schema(conn)
            _schema_ready.add(key)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# ---------------------------------------------------------------- meta --

def get_meta(key: str, store: "Store | None" = None) -> str | None:
    with db(store) as conn:
        row = conn.execute("SELECT value FROM email_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str, store: "Store | None" = None) -> None:
    with db(store) as conn:
        conn.execute("INSERT OR REPLACE INTO email_meta(key, value) VALUES (?, ?)", (key, value))


# --------------------------------------------- e-mail verification switch --

_VERIFY_KEY = "require_email_verification"
_VERIFY_TTL_SEC = 10.0
_verify_cache: dict[str, Any] = {"value": True, "at": 0.0}


def verification_required(store: "Store | None" = None) -> bool:
    """Admin switch (default on). Cached for a few seconds so it is not a query per request."""
    now = time.monotonic()
    if store is None and now - float(_verify_cache["at"]) < _VERIFY_TTL_SEC:
        return bool(_verify_cache["value"])
    try:
        value = get_meta(_VERIFY_KEY, store) != "0"
    except Exception:
        return bool(_verify_cache["value"])
    if store is None:
        _verify_cache.update(value=value, at=now)
    return value


def verification_setting(store: "Store | None" = None) -> dict[str, Any]:
    return {
        "required": verification_required(store),
        "updated_by": get_meta(_VERIFY_KEY + "_by", store) or "",
        "updated_at": get_meta(_VERIFY_KEY + "_at", store) or "",
    }


def set_verification_required(required: bool, by: str = "", store: "Store | None" = None) -> dict[str, Any]:
    set_meta(_VERIFY_KEY, "1" if required else "0", store)
    set_meta(_VERIFY_KEY + "_by", by or "", store)
    set_meta(_VERIFY_KEY + "_at", now_iso(), store)
    _verify_cache.update(value=bool(required), at=time.monotonic())
    return verification_setting(store)


def secret(store: "Store | None" = None) -> bytes:
    from app import config

    env = getattr(config, "EMAIL_TOKEN_SECRET", "") or ""
    if env:
        return env.encode()
    value = get_meta("secret", store)
    if not value:
        with db(store) as conn:
            conn.execute("INSERT OR IGNORE INTO email_meta(key, value) VALUES ('secret', ?)", (secrets.token_hex(32),))
            row = conn.execute("SELECT value FROM email_meta WHERE key = 'secret'").fetchone()
        value = row["value"]
    return value.encode()


# ----------------------------------------------------------- overrides --

def get_override(key: str, store: "Store | None" = None) -> dict[str, Any] | None:
    with db(store) as conn:
        row = conn.execute("SELECT * FROM email_templates WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def save_override(key: str, data: dict[str, Any], updated_by: str = "", store: "Store | None" = None) -> None:
    with db(store) as conn:
        conn.execute(
            """
            INSERT INTO email_templates(key, subject, preheader, html, text_body, enabled, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET subject=excluded.subject, preheader=excluded.preheader,
                html=excluded.html, text_body=excluded.text_body, enabled=excluded.enabled,
                updated_at=excluded.updated_at, updated_by=excluded.updated_by
            """,
            (
                key,
                str(data.get("subject") or ""),
                str(data.get("preheader") or ""),
                str(data.get("html") or ""),
                str(data.get("text_body") or ""),
                1 if data.get("enabled", True) else 0,
                now_iso(),
                updated_by,
            ),
        )


def delete_override(key: str, store: "Store | None" = None) -> None:
    with db(store) as conn:
        conn.execute("DELETE FROM email_templates WHERE key = ?", (key,))


def all_overrides(store: "Store | None" = None) -> dict[str, dict[str, Any]]:
    with db(store) as conn:
        rows = conn.execute("SELECT * FROM email_templates").fetchall()
    return {row["key"]: dict(row) for row in rows}


# ----------------------------------------------------------------- log --

def log_insert(store: "Store | None" = None, **fields: Any) -> int | None:
    """Insert a log row; returns None when ``dedupe_key`` already exists."""
    fields.setdefault("created_at", now_iso())
    if isinstance(fields.get("meta"), (dict, list)):
        fields["meta"] = json.dumps(fields["meta"], ensure_ascii=False)
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with db(store) as conn:
        try:
            cur = conn.execute(f"INSERT INTO email_log ({cols}) VALUES ({marks})", list(fields.values()))
        except sqlite3.IntegrityError:
            return None
        return int(cur.lastrowid)


def log_get(log_id: int, store: "Store | None" = None) -> dict[str, Any] | None:
    with db(store) as conn:
        row = conn.execute("SELECT * FROM email_log WHERE id = ?", (int(log_id),)).fetchone()
    return dict(row) if row else None


def log_update(log_id: int, store: "Store | None" = None, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db(store) as conn:
        conn.execute(f"UPDATE email_log SET {sets} WHERE id = ?", [*fields.values(), int(log_id)])


def claim(log_id: int, store: "Store | None" = None) -> bool:
    """Atomic queued -> sending transition; only one sender wins."""
    with db(store) as conn:
        cur = conn.execute(
            "UPDATE email_log SET status = 'sending', attempts = attempts + 1 WHERE id = ? AND status = 'queued'",
            (int(log_id),),
        )
        return cur.rowcount == 1


def due_ids(limit: int = 50, store: "Store | None" = None) -> list[int]:
    now = now_iso()
    with db(store) as conn:
        # a crashed sender leaves 'sending' rows behind: put them back after 10 minutes
        conn.execute(
            "UPDATE email_log SET status = 'queued' WHERE status = 'sending' AND COALESCE(scheduled_at, created_at) < ?",
            (iso_in(-600),),
        )
        rows = conn.execute(
            "SELECT id FROM email_log WHERE status = 'queued' AND COALESCE(scheduled_at, created_at) <= ? ORDER BY id LIMIT ?",
            (now, int(limit)),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def log_query(
    store: "Store | None" = None,
    *,
    template: str = "",
    status: str = "",
    stream: str = "",
    campaign: int | None = None,
    q: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    where: list[str] = ["1=1"]
    params: list[Any] = []
    if template:
        where.append("template_key = ?")
        params.append(template)
    if status:
        where.append("status = ?")
        params.append(status)
    if stream:
        where.append("stream = ?")
        params.append(stream)
    if campaign:
        where.append("campaign_id = ?")
        params.append(int(campaign))
    if q:
        where.append("(to_email LIKE ? OR subject LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    clause = " AND ".join(where)
    page_size = max(1, min(int(page_size or 50), 200))
    offset = (max(1, int(page or 1)) - 1) * page_size
    cols = (
        "id, created_at, sent_at, scheduled_at, template_key, campaign_id, to_email, user_id, stream, subject, "
        "status, error, attempts, opened_at, clicked_at"
    )
    with db(store) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM email_log WHERE {clause}", params).fetchone()[0]
        items = _rows(
            conn.execute(
                f"SELECT {cols} FROM email_log WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        )
        counts = {"queued": 0, "sent": 0, "failed": 0, "skipped": 0}
        for row in conn.execute("SELECT status, COUNT(*) AS c FROM email_log GROUP BY status").fetchall():
            counts[row["status"]] = int(row["c"])
    return {"items": items, "total": int(total), "counts": counts}


def template_stats(store: "Store | None" = None) -> dict[str, dict[str, Any]]:
    with db(store) as conn:
        rows = conn.execute(
            """
            SELECT template_key,
                   SUM(status = 'sent') AS sent, SUM(status = 'failed') AS failed, SUM(status = 'skipped') AS skipped,
                   SUM(opened_at IS NOT NULL) AS opened, SUM(clicked_at IS NOT NULL) AS clicked,
                   MAX(sent_at) AS last_sent_at
            FROM email_log WHERE template_key IS NOT NULL GROUP BY template_key
            """
        ).fetchall()
    return {
        r["template_key"]: {
            "sent": int(r["sent"] or 0),
            "failed": int(r["failed"] or 0),
            "skipped": int(r["skipped"] or 0),
            "opened": int(r["opened"] or 0),
            "clicked": int(r["clicked"] or 0),
            "last_sent_at": r["last_sent_at"],
        }
        for r in rows
    }


def last_log(
    user_id: str,
    template_key: str,
    store: "Store | None" = None,
    *,
    statuses: tuple[str, ...] = ("queued", "sending", "sent"),
) -> dict[str, Any] | None:
    marks = ",".join("?" for _ in statuses)
    with db(store) as conn:
        row = conn.execute(
            f"SELECT * FROM email_log WHERE user_id = ? AND template_key = ? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
            [user_id, template_key, *statuses],
        ).fetchone()
    return dict(row) if row else None


def recent_alerts(user_id: str, limit: int = 10, store: "Store | None" = None) -> list[dict[str, Any]]:
    with db(store) as conn:
        rows = conn.execute(
            "SELECT id, status, sent_at, opened_at, error FROM email_log WHERE user_id = ? AND template_key = 'alert_new_listing'"
            " AND status IN ('sent', 'failed') ORDER BY id DESC LIMIT ?",
            (user_id, int(limit)),
        ).fetchall()
    return _rows(rows)


# ----------------------------------------------------------- campaigns --

def campaign_save(data: dict[str, Any], store: "Store | None" = None) -> int:
    segment = data.get("segment")
    fields: dict[str, Any] = {
        "name": str(data.get("name") or "").strip() or "Kampaň",
        "mode": "html" if data.get("mode") == "html" else "text",
        "subject": str(data.get("subject") or ""),
        "preheader": str(data.get("preheader") or ""),
        "body": str(data.get("body") or ""),
        "segment_json": json.dumps(segment if isinstance(segment, dict) else {}, ensure_ascii=False),
        "scheduled_at": str(data.get("scheduled_at") or "") or None,
    }
    with db(store) as conn:
        cid = data.get("id")
        if cid:
            row = conn.execute("SELECT status FROM email_campaigns WHERE id = ?", (int(cid),)).fetchone()
            if not row:
                raise ValueError("Kampaň neexistuje")
            if row["status"] not in ("draft", "scheduled"):
                raise ValueError("Odeslanou kampaň už nejde upravit")
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE email_campaigns SET {sets} WHERE id = ?", [*fields.values(), int(cid)])
            return int(cid)
        fields["created_at"] = now_iso()
        fields["created_by"] = str(data.get("created_by") or "")
        cols = ", ".join(fields)
        cur = conn.execute(
            f"INSERT INTO email_campaigns ({cols}) VALUES ({', '.join('?' for _ in fields)})", list(fields.values())
        )
        return int(cur.lastrowid)


def campaign_get(cid: int, store: "Store | None" = None) -> dict[str, Any] | None:
    with db(store) as conn:
        row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (int(cid),)).fetchone()
        if not row:
            return None
        out = dict(row)
        stats = conn.execute(
            """
            SELECT COUNT(*) AS total, SUM(status = 'sent') AS sent, SUM(status = 'failed') AS failed,
                   SUM(status IN ('queued', 'sending')) AS queued, SUM(status = 'skipped') AS skipped,
                   SUM(opened_at IS NOT NULL) AS opened, SUM(clicked_at IS NOT NULL) AS clicked
            FROM email_log WHERE campaign_id = ?
            """,
            (int(cid),),
        ).fetchone()
    try:
        out["segment"] = json.loads(out.get("segment_json") or "{}")
    except json.JSONDecodeError:
        out["segment"] = {}
    out["stats"] = {k: int(stats[k] or 0) for k in stats.keys()}
    return out


def campaign_list(store: "Store | None" = None) -> list[dict[str, Any]]:
    with db(store) as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM email_campaigns ORDER BY id DESC").fetchall()]
    return [c for c in (campaign_get(i, store) for i in ids) if c]


def campaign_update(cid: int, store: "Store | None" = None, **fields: Any) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db(store) as conn:
        conn.execute(f"UPDATE email_campaigns SET {sets} WHERE id = ?", [*fields.values(), int(cid)])


def campaigns_due(store: "Store | None" = None) -> list[int]:
    with db(store) as conn:
        rows = conn.execute(
            "SELECT id FROM email_campaigns WHERE status = 'scheduled' AND (scheduled_at IS NULL OR scheduled_at <= ?)",
            (now_iso(),),
        ).fetchall()
    return [int(r["id"]) for r in rows]


# --------------------------------------------------------- suppression --

def suppress(email: str, reason: str, store: "Store | None" = None) -> bool:
    """Remember an address that bounced hard or complained. Returns True when it was new."""
    addr = (email or "").strip().lower()
    if not addr:
        return False
    with db(store) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO email_suppressions (email, reason, created_at) VALUES (?, ?, ?)",
            (addr, (reason or "")[:200], now_iso()),
        )
        return cur.rowcount == 1


def is_suppressed(email: str, store: "Store | None" = None) -> bool:
    addr = (email or "").strip().lower()
    if not addr:
        return False
    with db(store) as conn:
        return conn.execute("SELECT 1 FROM email_suppressions WHERE email = ?", (addr,)).fetchone() is not None


def count_suppressed(store: "Store | None" = None) -> int:
    with db(store) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM email_suppressions").fetchone()[0])


def sent_since(iso: str, store: "Store | None" = None) -> int:
    with db(store) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM email_log WHERE status = 'sent' AND sent_at >= ?", (iso,)).fetchone()[0])
