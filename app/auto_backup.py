"""Automatický denní backup centrální DB a per-user DB.

Běží ve web procesu jako asyncio smyčka. Zálohuje:
- /data/monitor.sqlite (centrální DB: identity, sessions)
- /data/ssr_cache.sqlite (předpočítané SSR stránky)
- /data/users/*/monitor.sqlite (per-user data)
do /data/backups/YYYY-MM-DD/ s retencí BACKUP_KEEP_DAYS dní.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import config

log = logging.getLogger("auto_backup")

BACKUP_KEEP_DAYS = int(__import__("os").getenv("BACKUP_KEEP_DAYS", "14") or "14")


def _backup_sqlite(src: Path, dst: Path) -> None:
    """Konzistentní kopie SQLite DB přes backup API (funguje i za běhu s WAL)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(src))
    try:
        dst_conn = sqlite3.connect(str(dst))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def run_backup_once(data_dir: Path | None = None, keep_days: int = BACKUP_KEEP_DAYS) -> Path | None:
    """Provede jeden backup. Vrací cestu k záloze, nebo None při chybě."""
    data_dir = data_dir or config.DATA_DIR
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest_root = data_dir / "backups" / day
    try:
        # Centrální DB
        central = data_dir / "monitor.sqlite"
        if central.exists():
            _backup_sqlite(central, dest_root / "monitor.sqlite")
        ssr_cache = data_dir / "ssr_cache.sqlite"
        if ssr_cache.exists():
            _backup_sqlite(ssr_cache, dest_root / "ssr_cache.sqlite")
        # Per-user DB
        users_dir = data_dir / "users"
        if users_dir.is_dir():
            for user_db in sorted(users_dir.glob("*/monitor.sqlite")):
                rel = user_db.relative_to(data_dir)
                _backup_sqlite(user_db, dest_root / rel)
        # Retence
        backups_root = data_dir / "backups"
        if backups_root.is_dir():
            days = sorted(p for p in backups_root.iterdir() if p.is_dir())
            for old in days[:-keep_days] if len(days) > keep_days else []:
                shutil.rmtree(old, ignore_errors=True)
        log.info("backup hotový: %s", dest_root)
        return dest_root
    except Exception as exc:
        log.exception("backup selhal: %s", exc)
        return None


async def backup_loop(stop_event: asyncio.Event | None = None, interval_s: float = 86400) -> None:
    """Denní smyčka; první backup hned po startu."""
    await asyncio.to_thread(run_backup_once)
    while True:
        try:
            if stop_event is not None:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
                if stop_event.is_set():
                    return
            else:
                await asyncio.sleep(interval_s)
        except asyncio.TimeoutError:
            pass
        await asyncio.to_thread(run_backup_once)
