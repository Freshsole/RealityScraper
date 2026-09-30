"""Account handling.

Identity (users, sessions, password resets) lives in the GLOBAL store via
:mod:`app.users`.  Per-user profile data (the ``account`` meta, Discord
linking, ...) lives in each user's private store.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app import config
from app import users as user_registry
from app.store import Store

LINK_TTL_SEC = 20 * 60

SESSION_COOKIE = "realitify_session"


def split_name(full: str) -> tuple[str, str]:
    parts = [part for part in (full or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


# ------------------------------------------------- per-user store profile --


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
        "role": account_role_from_data(data),
    }


def account_role_from_data(data: dict[str, Any]) -> str:
    role = str(data.get("role") or "").strip().lower()
    if role in {"admin", "user"}:
        return role
    email = (data.get("email") or "").strip().lower()
    if email and email == config.ADMIN_EMAIL:
        return "admin"
    return "user"


def account_role(store: Store) -> str:
    return account_role_from_data(account_record(store))


def set_account_role(store: Store, role: str) -> str:
    role = (role or "").strip().lower()
    if role not in {"admin", "user"}:
        raise ValueError("Role je admin nebo běžný uživatel")
    data = account_record(store)
    data["role"] = role
    save_account(store, data)
    return role


def save_account(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    store.set_meta("account", json.dumps(payload, ensure_ascii=False))
    return public_account(store)


def update_profile(
    global_store: Store,
    user_store: Store,
    user_id: str,
    first: str,
    last: str,
    phone: str,
) -> dict[str, Any]:
    first = (first or "").strip()
    last = (last or "").strip()
    if not first:
        raise ValueError("Jméno je povinné")
    updated = user_registry.update_user_profile(global_store, user_id, first, last)
    if not updated:
        raise ValueError("Účet neexistuje")
    data = account_record(user_store)
    data["first"] = first
    data["last"] = last
    data["phone"] = (phone or "").strip()
    if not data.get("email"):
        data["email"] = (updated.get("email") or "").strip()
    save_account(user_store, data)
    public = user_registry.public_user(updated) or {}
    public["phone"] = data["phone"]
    return public


def save_whatsapp_phone(store: Store, digits: str) -> dict[str, Any]:
    data = account_record(store)
    if not data.get("email"):
        raise ValueError("Účet není založený")
    data["whatsapp_phone"] = (digits or "").strip()
    if not data.get("phone"):
        data["phone"] = data["whatsapp_phone"]
    return save_account(store, data)


# ------------------------------------------------------- global identity --


def _init_user_store(
    user_store: Store,
    *,
    first: str,
    last: str,
    email: str,
    promo: str = "",
) -> None:
    payload = {
        "first": first,
        "last": last,
        "email": email,
        "phone": "",
        "email_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if promo:
        payload["pending_promo_code"] = promo
    save_account(user_store, payload)
    billing = user_store.billing_record() or {}
    billing["email"] = email
    if promo:
        billing["pending_promo_code"] = promo
    user_store.save_billing_record(billing)


def register(
    global_store: Store,
    name: str,
    email: str,
    password: str,
    promo_code: str = "",
    make_user_store: Callable[[str], Store] | None = None,
) -> tuple[dict[str, Any], str]:
    from app.billing import pending_promo_for_signup

    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValueError("Zadej platný e-mail")
    if len(password or "") < 8:
        raise ValueError("Heslo musí mít alespoň 8 znaků")
    first, last = split_name(name)
    if not first:
        raise ValueError("Zadej jméno a příjmení")
    promo = pending_promo_for_signup(promo_code)
    user = user_registry.create_user(
        global_store,
        email=email_norm,
        password=password,
        first=first,
        last=last,
    )
    if make_user_store is not None:
        user_store = make_user_store(user["id"])
        _init_user_store(user_store, first=first, last=last, email=email_norm, promo=promo)
    token = user_registry.create_session(global_store, user["id"])
    public = user_registry.public_user(user)
    assert public is not None
    return public, token


def login(global_store: Store, email: str, password: str) -> tuple[dict[str, Any], str]:
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        raise ValueError("Zadej platný e-mail")
    user = user_registry.verify_user_password(global_store, email_norm, password or "")
    if user is None:
        # Stejná hláška pro neexistující účet i špatné heslo (anti-enumeration).
        raise ValueError("E-mail nebo heslo nesedí")
    token = user_registry.create_session(global_store, user["id"])
    public = user_registry.public_user(user)
    assert public is not None
    return public, token


def user_from_session(global_store: Store, token: str | None) -> dict[str, Any] | None:
    user = user_registry.get_user_by_session(global_store, token)
    return user_registry.public_user(user)


def clear_session(global_store: Store, token: str | None) -> None:
    user_registry.delete_session(global_store, token)


def change_password(global_store: Store, user_id: str, current: str, new: str) -> str:
    user = user_registry.get_user_by_id(global_store, user_id)
    if not user:
        raise ValueError("Účet neexistuje")
    if not user_registry.verify_user_password(global_store, user["email"], current or ""):
        raise ValueError("Současné heslo nesedí")
    if len(new or "") < 8:
        raise ValueError("Nové heslo musí mít alespoň 8 znaků")
    user_registry.set_user_password(global_store, user_id, new)
    user_registry.clear_password_resets(global_store, user_id)
    user_registry.delete_user_sessions(global_store, user_id)
    return user_registry.create_session(global_store, user_id)


def create_password_reset(global_store: Store, email: str) -> str | None:
    """Vytvoří jednorázový token. Vrátí raw token jen když e-mail sedí na účet."""
    return user_registry.create_password_reset(global_store, email)


def password_reset_ok(global_store: Store, token: str) -> bool:
    return user_registry.password_reset_ok(global_store, token)


def reset_password_with_token(
    global_store: Store, token: str, new_password: str
) -> tuple[dict[str, Any], str]:
    if len(new_password or "") < 8:
        raise ValueError("Nové heslo musí mít alespoň 8 znaků")
    user = user_registry.consume_password_reset(global_store, token)
    if not user:
        raise ValueError("Odkaz pro obnovení hesla je neplatný nebo vypršel")
    user_registry.set_user_password(global_store, user["id"], new_password)
    user_registry.delete_user_sessions(global_store, user["id"])
    session = user_registry.create_session(global_store, user["id"])
    public = user_registry.public_user(user)
    assert public is not None
    return public, session


# -------------------------------------------------------------- discord --


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
        try:
            from app import analytics as site_stats

            site_stats.track(store, site_stats.KIND_DISCORD, path="/nastaveni")
        except Exception:
            pass
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
