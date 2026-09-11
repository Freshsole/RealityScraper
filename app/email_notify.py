from __future__ import annotations

import asyncio
import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any

from app import config
from app import account as user_account
from app.push import in_quiet_hours, kind_allowed
from app.store import Store


def account_email(store: Store) -> str:
    return (user_account.account_record(store).get("email") or "").strip().lower()


def configured() -> bool:
    return bool(config.SMTP_HOST and (config.SMTP_FROM or config.SMTP_USER))


def from_addr() -> str:
    return (config.SMTP_FROM or config.SMTP_USER or "").strip()


def should_send(store: Store, kind: str, *, ignore_quiet: bool = False) -> bool:
    prefs = store.notify_prefs()
    if not prefs.get("email"):
        return False
    if not account_email(store):
        return False
    if not configured():
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


def listing_text(item: Any, kind: str, monitor_name: str = "", prefix: str = "") -> tuple[str, str]:
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
    subject = f"{head}: {title}"[:180]
    lines = [prefix, head, title] if prefix else [head, title]
    if monitor_name:
        lines.append(f"Hlídací profil: {monitor_name}")
    meta = " · ".join(part for part in (locality, price) if part)
    if meta:
        lines.append(meta)
    if url:
        lines.append(url)
    body = "\n".join(line for line in lines if line).strip()
    return subject, body


def _send_sync(to: str, subject: str, body: str) -> None:
    sender = from_addr()
    if not config.SMTP_HOST or not sender:
        raise RuntimeError("E-mail není nastavený (SMTP_HOST a SMTP_FROM)")
    msg = EmailMessage()
    msg["Subject"] = subject
    name, addr = parseaddr(sender)
    msg["From"] = formataddr((name or "Realitify", addr or sender))
    msg["To"] = to
    msg.set_content(body)
    html_body = "".join(
        f"<p>{html.escape(line)}</p>" if line else "<br>" for line in body.split("\n")
    )
    msg.add_alternative(
        f'<div style="font-family:Inter,Arial,sans-serif;font-size:15px;color:#163300">{html_body}</div>',
        subtype="html",
    )
    port = config.SMTP_PORT
    if port == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, port, timeout=20, context=ssl.create_default_context()) as smtp:
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(config.SMTP_HOST, port, timeout=20) as smtp:
        if config.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)


def send_message(to: str, subject: str, body: str) -> None:
    _send_sync(to, subject, body)


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
    subject, body = listing_text(item, kind, monitor_name, prefix=prefix)
    await asyncio.to_thread(send_message, account_email(store), subject, body)
    return True


async def notify_digest(store: Store, items: list[dict[str, Any]], *, test: bool = False) -> bool:
    kind = "test" if test else "digest"
    if not should_send(store, kind, ignore_quiet=test):
        return False
    if not items:
        subject = "Test souhrnu Realitify" if test else "Ranní souhrn Realitify"
        body = f"{subject}\nžádné nové zásahy"
    else:
        subject = f"{'Test souhrnu' if test else 'Ranní souhrn'} Realitify · {len(items)} zásahů"
        lines = [subject]
        for item in items[:12]:
            name = (item.get("name") or "Nabídka").strip()
            url = (item.get("url") or "").strip()
            lines.append(name + (f"\n{url}" if url else ""))
        body = "\n\n".join(lines)
    await asyncio.to_thread(send_message, account_email(store), subject, body)
    return True
