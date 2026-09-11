from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from app import config
from app.store import Store

LINK_TTL_SEC = 20 * 60

SESSION_COOKIE = "realitify_session"
_ITERATIONS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def split_name(full: str) -> tuple[str, str]:
    parts = [part for part in (full or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt_hex = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _ITERATIONS)
    return digest.hex(), salt_hex


def _verify_password(password: str, hashed: str, salt: str) -> bool:
    if not hashed or not salt:
        return False
    candidate, _ = _hash_password(password, salt)
    return hmac.compare_digest(candidate, hashed)


def account_record(store: Store) -> dict[str, Any]:
    raw = store.get_meta("account") or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def public_account(store: Store) -> dict[str, Any]:
    data = account_record(store)
    first = (data.get("first") or "").strip()
    last = (data.get("last") or "").strip()
    return {
        "first": first,
        "last": last,
        "name": f"{first} {last}".strip(),
        "email": (data.get("email") or "").strip(),
        "phone": (data.get("phone") or "").strip(),
        "email_verified": bool(data.get("email_verified")),
        "authenticated": bool(data.get("email")),
    }


def save_account(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    store.set_meta("account", json.dumps(payload, ensure_ascii=False))
    return public_account(store)


def _new_session(store: Store) -> str:
    token = secrets.token_urlsafe(32)
    store.set_meta("auth_session", token)
    return token


def clear_session(store: Store) -> None:
    store.set_meta("auth_session", None)


def user_from_session(store: Store, token: str | None) -> dict[str, Any] | None:
    stored = store.get_meta("auth_session") or ""
    given = token or ""
    if not stored or not given or len(stored) != len(given):
        return None
    if not secrets.compare_digest(given, stored):
        return None
    public = public_account(store)
    if not public.get("email"):
        return None
    return public


def register(store: Store, name: str, email: str, password: str) -> tuple[dict[str, Any], str]:
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValueError("Zadej platný e-mail")
    if len(password or "") < 8:
        raise ValueError("Heslo musí mít alespoň 8 znaků")
    existing = account_record(store)
    if existing.get("email"):
        if existing.get("email") == email_norm:
            raise ValueError("Tento účet už existuje, přihlaste se")
        raise ValueError("Účet už je založený. Přihlaste se e-mailem z registrace.")
    first, last = split_name(name)
    if not first:
        raise ValueError("Zadej jméno a příjmení")
    hashed, salt = _hash_password(password)
    save_account(
        store,
        {
            "first": first,
            "last": last,
            "email": email_norm,
            "phone": "",
            "password_hash": hashed,
            "password_salt": salt,
            "email_verified": True,
            "created_at": _now(),
        },
    )
    billing = store.billing_record() or {}
    billing["email"] = email_norm
    store.save_billing_record(billing)
    return public_account(store), _new_session(store)


def login(store: Store, email: str, password: str) -> tuple[dict[str, Any], str]:
    email_norm = (email or "").strip().lower()
    data = account_record(store)
    if not data.get("email"):
        raise ValueError("Účet ještě není založený. Nejdřív se zaregistrujte.")
    if data.get("email") != email_norm or not _verify_password(password, data.get("password_hash") or "", data.get("password_salt") or ""):
        raise ValueError("E-mail nebo heslo nesedí")
    return public_account(store), _new_session(store)


def update_profile(store: Store, first: str, last: str, phone: str) -> dict[str, Any]:
    data = account_record(store)
    if not data.get("email"):
        raise ValueError("Účet není založený")
    first = (first or "").strip()
    last = (last or "").strip()
    if not first:
        raise ValueError("Jméno je povinné")
    data["first"] = first
    data["last"] = last
    data["phone"] = (phone or "").strip()
    return save_account(store, data)


def save_whatsapp_phone(store: Store, digits: str) -> dict[str, Any]:
    data = account_record(store)
    if not data.get("email"):
        raise ValueError("Účet není založený")
    data["whatsapp_phone"] = (digits or "").strip()
    if not data.get("phone"):
        data["phone"] = data["whatsapp_phone"]
    return save_account(store, data)


def change_password(store: Store, current: str, new: str) -> str:
    data = account_record(store)
    if not data.get("email"):
        raise ValueError("Účet není založený")
    if not _verify_password(current or "", data.get("password_hash") or "", data.get("password_salt") or ""):
        raise ValueError("Současné heslo nesedí")
    if len(new or "") < 8:
        raise ValueError("Nové heslo musí mít alespoň 8 znaků")
    hashed, salt = _hash_password(new)
    data["password_hash"] = hashed
    data["password_salt"] = salt
    save_account(store, data)
    return _new_session(store)


def discord_webhook_url(store: Store) -> str:
    return (account_record(store).get("discord_webhook_url") or "").strip()


def _link_payload(store: Store) -> dict[str, Any] | None:
    raw = store.get_meta("discord_link_code") or ""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not data.get("code"):
        return None
    try:
        expires_at = float(data.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= time.time():
        store.set_meta("discord_link_code", None)
        return None
    data["expires_at"] = expires_at
    return data


def discord_status(store: Store) -> dict[str, Any]:
    data = account_record(store)
    linked = bool(discord_webhook_url(store) and data.get("discord_channel_id"))
    pending = _link_payload(store)
    return {
        "bot_ready": bool(config.DISCORD_BOT_TOKEN and config.DISCORD_GUILD_ID),
        "linked": linked,
        "channel_name": (data.get("discord_channel_name") or "").strip(),
        "discord_username": (data.get("discord_username") or "").strip(),
        "server_invite": config.DISCORD_SERVER_INVITE,
        "pending_code": (pending or {}).get("code") or "",
        "pending_expires_in": max(0, int((pending or {}).get("expires_at", 0) - time.time())) if pending else 0,
    }


def create_discord_link_code(store: Store) -> dict[str, Any]:
    if not account_record(store).get("email"):
        raise ValueError("Nejste přihlášeni")
    if not config.DISCORD_BOT_TOKEN or not config.DISCORD_GUILD_ID:
        raise ValueError("Discord bot není nastavený (DISCORD_BOT_TOKEN a DISCORD_GUILD_ID)")
    code = secrets.token_hex(3).upper()
    store.set_meta(
        "discord_link_code",
        json.dumps({"code": code, "expires_at": time.time() + LINK_TTL_SEC}),
    )
    return {
        "code": code,
        "command": f"/link {code}",
        "expires_in": LINK_TTL_SEC,
        **discord_status(store),
        "pending_code": code,
        "pending_expires_in": LINK_TTL_SEC,
    }


def match_discord_link_code(store: Store, code: str) -> bool:
    pending = _link_payload(store)
    if not pending:
        return False
    given = (code or "").strip().upper()
    expected = str(pending.get("code") or "").upper()
    if not given or len(given) != len(expected):
        return False
    return secrets.compare_digest(given, expected)


def clear_discord_link_code(store: Store) -> None:
    store.set_meta("discord_link_code", None)


def save_discord_connection(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    data = account_record(store)
    for key, value in payload.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    store.set_meta("account", json.dumps(data, ensure_ascii=False))
    webhook = (data.get("discord_webhook_url") or "").strip()
    if webhook:
        store.apply_discord_webhook(webhook)
    return discord_status(store)


def unlink_discord(store: Store) -> dict[str, Any]:
    data = account_record(store)
    for key in (
        "discord_user_id",
        "discord_username",
        "discord_channel_id",
        "discord_channel_name",
        "discord_webhook_url",
        "discord_webhook_id",
        "discord_linked_at",
    ):
        data.pop(key, None)
    store.set_meta("account", json.dumps(data, ensure_ascii=False))
    clear_discord_link_code(store)
    return discord_status(store)
