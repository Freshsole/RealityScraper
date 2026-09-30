#!/usr/bin/env python3
"""Migrate a single-user Realitify DB to the multi-user layout.

Idempotent: safe to run multiple times.

Steps:
  1. Back up the source DB.
  2. Read the legacy ``account`` JSON (email, name, password hash/salt).
  3. Create the central admin user (preserving the password hash).
  4. Copy the DB to /data/users/{admin_id}/monitor.sqlite (the user's private DB).
  5. Scrub the password hash/salt out of the copied account JSON (identity now
     lives in the central DB).

Usage:
    python scripts/migrate_multiuser.py [--db /data/monitor.sqlite] [--data-dir /data]
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import users as user_registry  # noqa: E402
from app.store import Store  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_legacy_account(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'account'").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        raise SystemExit(f"No legacy 'account' record found in {db_path}")
    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        raise SystemExit(f"Legacy 'account' record in {db_path} is not valid JSON")
    if not isinstance(data, dict) or not data.get("email"):
        raise SystemExit(f"Legacy 'account' record in {db_path} has no e-mail")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate single-user DB to multi-user layout")
    parser.add_argument("--db", default="/data/monitor.sqlite", help="Source (legacy) DB path")
    parser.add_argument("--data-dir", default="/data", help="Data directory holding users/")
    parser.add_argument("--backup-dir", default="", help="Where to put the backup (default: <data-dir>/backups)")
    args = parser.parse_args()

    db_path = Path(args.db)
    data_dir = Path(args.data_dir)
    if not db_path.exists():
        raise SystemExit(f"Source DB not found: {db_path}")

    backup_dir = Path(args.backup_dir) if args.backup_dir else data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"monitor.sqlite.pre-multiuser-{stamp}"
    shutil.copy2(db_path, backup_path)
    print(f"[migrate] backup -> {backup_path}")

    legacy = read_legacy_account(db_path)
    email = str(legacy.get("email") or "").strip().lower()
    first = str(legacy.get("first") or "").strip()
    last = str(legacy.get("last") or "").strip()
    pw_hash = str(legacy.get("password_hash") or "")
    pw_salt = str(legacy.get("password_salt") or "")
    if not pw_hash or not pw_salt:
        raise SystemExit("Legacy account has no password hash/salt; cannot migrate safely")

    store = Store(db_path)
    existing = user_registry.get_user_by_email(store, email)
    if existing:
        user_id = str(existing["id"])
        # Ensure the admin role (the legacy owner becomes the admin).
        user_registry.set_role(store, user_id, "admin")
        print(f"[migrate] user {email} already exists as id={user_id}; role ensured=admin")
    else:
        user_id = secrets.token_hex(8)
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, password_salt, first, last, role, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'admin', ?)",
                (user_id, email, pw_hash, pw_salt, first, last, _now_iso()),
            )
        print(f"[migrate] created admin user {email} id={user_id} (password hash preserved)")

    user_db_path = data_dir / "users" / user_id / "monitor.sqlite"
    user_db_path.parent.mkdir(parents=True, exist_ok=True)
    if not user_db_path.exists():
        shutil.copy2(db_path, user_db_path)
        print(f"[migrate] copied DB -> {user_db_path}")
    else:
        print(f"[migrate] user DB already exists at {user_db_path}; leaving untouched")

    # Scrub password material from the copied private DB; identity lives centrally now.
    ustore = Store(user_db_path)
    raw = ustore.get_meta("account") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if isinstance(data, dict) and ("password_hash" in data or "password_salt" in data):
        data.pop("password_hash", None)
        data.pop("password_salt", None)
        ustore.set_meta("account", json.dumps(data, ensure_ascii=False))
        print("[migrate] scrubbed password hash/salt from user account meta")
    else:
        print("[migrate] user account meta already clean")

    print("[migrate] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
