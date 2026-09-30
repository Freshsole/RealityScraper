"""Multi-user identity layer.

The GLOBAL store (``config.DB_PATH``) holds the ``users``, ``sessions`` and
``password_resets`` tables.  Every user additionally owns a private SQLite
file (see :func:`user_store_path`) that carries the full single-user schema
(monitors, listings, notification prefs, billing, ...).

The scrape worker keeps running as one process; it opens one Hub per user
store.  The web resolves the current user from the session cookie against
the global store and then serves that user's private store.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app import config

if TYPE_CHECKING:
    from app.store import Store

SESSION_TTL_SEC = 30 * 24 * 3600
RESET_TTL_SEC = 24 * 3600
_ITERATIONS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def user_data_dir() -> Path:
    return Path(config.DATA_DIR) / "users"


def user_store_path(user_id: str) -> Path:
    safe = "".join(ch for ch in (user_id or "") if ch.isalnum() or ch in "-_") or "unknown"
    return user_data_dir() / safe / "monitor.sqlite"


def ensure_users_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            first TEXT NOT NULL DEFAULT '',
            last TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id)")


# ---------------------------------------------------------------- password --

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _ITERATIONS)
    return digest.hex(), salt_hex


def verify_password(password: str, hashed: str, salt: str) -> bool:
    if not hashed or not salt:
        return False
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, hashed)


# ------------------------------------------------------------------- users --

def _row_to_user(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "email": row["email"],
        "first": row["first"] or "",
        "last": row["last"] or "",
        "role": (row["role"] or "user").strip().lower() or "user",
        "created_at": row["created_at"],
    }


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user or not user.get("id"):
        return None
    first = (user.get("first") or "").strip()
    last = (user.get("last") or "").strip()
    email = (user.get("email") or "").strip()
    role = (user.get("role") or "user").strip().lower()
    if role not in {"admin", "user"}:
        role = "admin" if email and email == config.ADMIN_EMAIL else "user"
    return {
        "id": user.get("id"),
        "first": first,
        "last": last,
        "name": f"{first} {last}".strip(),
        "email": email,
        "phone": (user.get("phone") or "").strip(),
        "email_verified": True,
        "authenticated": bool(email),
        "role": role,
    }


def is_admin(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    if (user.get("role") or "") == "admin":
        return True
    email = (user.get("email") or "").strip().lower()
    return bool(email) and email == config.ADMIN_EMAIL


def create_user(
    store: Store,
    *,
    email: str,
    password: str,
    first: str = "",
    last: str = "",
    role: str = "user",
) -> dict[str, Any]:
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValueError("Zadej platný e-mail")
    if len(password or "") < 8:
        raise ValueError("Heslo musí mít alespoň 8 znaků")
    first = (first or "").strip()
    last = (last or "").strip()
    if not first:
        raise ValueError("Zadej jméno a příjmení")
    role = (role or "user").strip().lower()
    if role not in {"admin", "user"}:
        role = "user"
    if email_norm == config.ADMIN_EMAIL:
        role = "admin"
    hashed, salt = hash_password(password)
    user_id = secrets.token_hex(8)
    created = _now()
    try:
        with store.connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, password_salt, first, last, role, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, email_norm, hashed, salt, first, last, role, created),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Tento e-mail už je registrovaný. Přihlaste se.") from exc
    user = get_user_by_id(store, user_id)
    assert user is not None
    return user


def get_user_by_id(store: Store, user_id: str) -> dict[str, Any] | None:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row)


def get_user_by_email(store: Store, email: str) -> dict[str, Any] | None:
    email_norm = (email or "").strip().lower()
    if not email_norm:
        return None
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email_norm,)).fetchone()
        return _row_to_user(row)


def _get_user_row_auth(store: Store, email: str) -> sqlite3.Row | None:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()


def verify_user_password(store: Store, email: str, password: str) -> dict[str, Any] | None:
    row = _get_user_row_auth(store, email)
    if row is None:
        return None
    if not verify_password(password or "", row["password_hash"] or "", row["password_salt"] or ""):
        return None
    return _row_to_user(row)


def list_users(store: Store) -> list[dict[str, Any]]:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return [u for u in (_row_to_user(r) for r in rows) if u]


def count_users(store: Store) -> int:
    with store.connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        return int(row[0] or 0)


def set_role(store: Store, user_id: str, role: str) -> dict[str, Any] | None:
    role = (role or "").strip().lower()
    if role not in ("admin", "user"):
        raise ValueError("Neplatná role")
    with store.connect() as conn:
        conn.execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (role, str(user_id)),
        )
        conn.commit()
    return get_user_by_id(store, user_id)


def update_user_profile(store: Store, user_id: str, first: str, last: str) -> dict[str, Any] | None:
    first = (first or "").strip()
    last = (last or "").strip()
    if not first:
        raise ValueError("Jméno je povinné")
    with store.connect() as conn:
        cur = conn.execute("UPDATE users SET first = ?, last = ? WHERE id = ?", (first, last, user_id))
        if cur.rowcount == 0:
            return None
    return get_user_by_id(store, user_id)


def set_user_password(store: Store, user_id: str, password: str) -> None:
    if len(password or "") < 8:
        raise ValueError("Heslo musí mít alespoň 8 znaků")
    hashed, salt = hash_password(password)
    with store.connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (hashed, salt, user_id),
        )


def set_user_role(store: Store, user_id: str, role: str) -> dict[str, Any] | None:
    role = (role or "").strip().lower()
    if role not in {"admin", "user"}:
        raise ValueError("Role je admin nebo běžný uživatel")
    with store.connect() as conn:
        cur = conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        if cur.rowcount == 0:
            return None
    return get_user_by_id(store, user_id)


# ---------------------------------------------------------------- sessions --

def create_session(store: Store, user_id: str, ttl_sec: int = SESSION_TTL_SEC) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, _now(), now + ttl_sec),
        )
    return token


def get_user_by_session(store: Store, token: str | None) -> dict[str, Any] | None:
    given = (token or "").strip()
    if not given:
        return None
    now = time.time()
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token = ? AND s.expires_at > ?",
            (given, now),
        ).fetchone()
        return _row_to_user(row)


def delete_session(store: Store, token: str | None) -> None:
    given = (token or "").strip()
    if not given:
        return
    with store.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (given,))


def delete_user_sessions(store: Store, user_id: str) -> None:
    with store.connect() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def prune_expired_sessions(store: Store) -> int:
    with store.connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
        return cur.rowcount or 0


# ---------------------------------------------------------- password reset --

def _hash_reset_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_password_reset(store: Store, email: str) -> str | None:
    user = get_user_by_email(store, email)
    if not user:
        return None
    token = secrets.token_urlsafe(32)
    with store.connect() as conn:
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
        conn.execute(
            "INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (_hash_reset_token(token), user["id"], time.time() + RESET_TTL_SEC),
        )
    return token


def password_reset_ok(store: Store, token: str) -> bool:
    return get_password_reset_user(store, token) is not None


def get_password_reset_user(store: Store, token: str) -> dict[str, Any] | None:
    given = (token or "").strip()
    if not given:
        return None
    token_hash = _hash_reset_token(given)
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT u.* FROM password_resets r JOIN users u ON u.id = r.user_id"
            " WHERE r.token_hash = ? AND r.expires_at > ?",
            (token_hash, time.time()),
        ).fetchone()
        return _row_to_user(row)


def consume_password_reset(store: Store, token: str) -> dict[str, Any] | None:
    user = get_password_reset_user(store, token)
    if not user:
        return None
    with store.connect() as conn:
        conn.execute("DELETE FROM password_resets WHERE token_hash = ?", (_hash_reset_token((token or "").strip()),))
    return user


def clear_password_resets(store: Store, user_id: str) -> None:
    with store.connect() as conn:
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
