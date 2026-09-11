from __future__ import annotations

import re
from typing import Any

import httpx

from app import config
from app import account as user_account
from app.push import in_quiet_hours, kind_allowed
from app.store import Store

GRAPH = "https://graph.facebook.com/v21.0"


def plan_allows(store: Store) -> bool:
    from app.billing import PLAN_RANK, billing_state

    plan = billing_state(store).get("plan") or "free"
    return PLAN_RANK.get(plan, 0) >= PLAN_RANK["pro"]


def server_configured() -> bool:
    return bool(config.WHATSAPP_TOKEN and config.WHATSAPP_PHONE_NUMBER_ID)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9 and digits[0] in "67":
        digits = "420" + digits
    if digits.startswith("420") and len(digits) == 12:
        return digits
    if 11 <= len(digits) <= 15:
        return digits
    raise ValueError("Zadej telefon s předvolbou, např. +420 777 123 456")


def display_phone(digits: str) -> str:
    if digits.startswith("420") and len(digits) == 12:
        rest = digits[3:]
        return f"+420 {rest[:3]} {rest[3:6]} {rest[6:]}"
    return f"+{digits}" if digits else ""


def account_phone(store: Store) -> str:
    data = user_account.account_record(store)
    raw = (data.get("whatsapp_phone") or data.get("phone") or "").strip()
    if not raw:
        return ""
    try:
        return normalize_phone(raw)
    except ValueError:
        return ""


def save_phone(store: Store, raw: str) -> str:
    digits = normalize_phone(raw)
    user_account.save_whatsapp_phone(store, digits)
    return digits


def chat_url() -> str:
    digits = re.sub(r"\D", "", config.WHATSAPP_BUSINESS_NUMBER or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits:
        return ""
    return f"https://wa.me/{digits}?text=Ahoj%20Realitify"


def status(store: Store) -> dict[str, Any]:
    digits = account_phone(store)
    prefs = store.notify_prefs()
    allowed = plan_allows(store)
    return {
        "allowed": allowed,
        "configured": server_configured(),
        "enabled": bool(allowed and prefs.get("whatsapp") and digits),
        "phone": display_phone(digits),
        "phone_digits": digits,
        "chat_url": chat_url(),
    }


def should_send(store: Store, kind: str, *, ignore_quiet: bool = False) -> bool:
    if not plan_allows(store) or not server_configured():
        return False
    prefs = store.notify_prefs()
    if not prefs.get("whatsapp"):
        return False
    if not account_phone(store):
        return False
    if not kind_allowed(prefs, kind):
        return False
    if kind not in {"digest", "test"} and not prefs.get("instant", True):
        return False
    if not ignore_quiet and kind != "test" and in_quiet_hours(prefs):
        return False
    return True


def _attr(item: Any, key: str, default: str = "") -> str:
    if isinstance(item, dict):
        value = item.get(key)
    else:
        value = getattr(item, key, None)
    if value is None:
        return default
    return str(value).strip() or default


def listing_text(item: Any, kind: str, monitor_name: str = "", prefix: str = "") -> str:
    labels = {
        "new": "Nový byt",
        "changed": "Změna ceny",
        "sold": "Nabídka je pryč",
        "expire": "Nabídka je pryč",
        "digest": "Souhrn Realitify",
        "test": "Test Realitify",
    }
    title = _attr(item, "name") or "Nová nabídka"
    locality = _attr(item, "locality")
    url = _attr(item, "url")
    price = _attr(item, "price_label")
    if not price:
        raw = item.get("price_czk") if isinstance(item, dict) else getattr(item, "price_czk", None)
        if raw not in (None, ""):
            try:
                price = f"{int(raw):,}".replace(",", " ") + " Kč"
            except (TypeError, ValueError):
                price = str(raw)
    head = labels.get(kind, "Realitify")
    if monitor_name:
        head = f"{head} · {monitor_name}"
    lines = [prefix, head, title] if prefix else [head, title]
    meta = " · ".join(part for part in (locality, price) if part)
    if meta:
        lines.append(meta)
    if url:
        lines.append(url)
    return "\n".join(line for line in lines if line).strip()


async def send_text(phone: str, text: str) -> None:
    if not server_configured():
        raise RuntimeError("WhatsApp Cloud API není nastavené (WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID)")
    body = (text or "").strip()
    if not body:
        raise RuntimeError("Prázdná WhatsApp zpráva")
    url = f"{GRAPH}/{config.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    if config.WHATSAPP_TEMPLATE:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": config.WHATSAPP_TEMPLATE,
                "language": {"code": config.WHATSAPP_TEMPLATE_LANG},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": body[:1024]}],
                    }
                ],
            },
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"preview_url": True, "body": body[:4096]},
        }
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            detail = response.text[:400]
            raise RuntimeError(f"WhatsApp se neodeslal: {detail}")


async def notify_listing(
    store: Store,
    item: Any,
    kind: str,
    monitor_name: str = "",
    *,
    ignore_quiet: bool = False,
    prefix: str = "",
) -> bool:
    if not should_send(store, kind, ignore_quiet=ignore_quiet):
        return False
    phone = account_phone(store)
    await send_text(phone, listing_text(item, kind, monitor_name, prefix=prefix))
    return True


async def notify_digest(store: Store, items: list[dict[str, Any]], *, test: bool = False) -> bool:
    kind = "test" if test else "digest"
    if not should_send(store, kind, ignore_quiet=test):
        return False
    if not items:
        text = "Ranní souhrn Realitify · žádné nové zásahy" if not test else "Test souhrnu Realitify · žádné nové zásahy"
    else:
        title = "Test souhrnu Realitify" if test else "Ranní souhrn Realitify"
        lines = [f"{title} · {len(items)} zásahů"]
        for item in items[:8]:
            name = (item.get("name") or "Nabídka").strip()
            url = (item.get("url") or "").strip()
            lines.append(f"• {name}" + (f"\n{url}" if url else ""))
        text = "\n".join(lines)
    await send_text(account_phone(store), text)
    return True
