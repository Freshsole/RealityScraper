"""Měsíc PRO zdarma z Google Formuláře (omluva za výpadek e-mailů).

Odpovědi formuláře se zapisují do Google Sheets, který je publikovaný jako CSV (``FREE_PRO_SHEET_CSV_URL``).
Appka CSV čte každých ``FREE_PRO_POLL_SECONDS`` s; sloupec s e-mailem pozná podle hlavičky obsahující „mail“.
Když sedí e-mail na účet bez placeného tarifu, dostane účet PRO na ``FREE_PRO_DAYS`` dní (``comp_plan`` +
``comp_until``, konec řeší ``billing.expire_comp_if_due``). Řádky bez účtu se při dalším čtení zkusí znovu,
takže si PRO vyzvedne i ten, kdo se zaregistruje až po vyplnění formuláře. Jeden grant na e-mail.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from app import billing, config, users
from app.store import Store

META_KEY = "free_pro_requests"
_LOCK = threading.Lock()
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load(store: Store) -> list[dict[str, Any]]:
    try:
        rows = json.loads(store.get_meta(META_KEY) or "[]")
    except json.JSONDecodeError:
        rows = []
    return rows if isinstance(rows, list) else []


def list_requests(store: Store) -> list[dict[str, Any]]:
    return _load(store)


_LABELS = {
    "granted": "✅ PRO na měsíc přidělo",
    "has_paid_plan": "⚠️ Má placený tarif, PRO nepřidělováno",
    "no_account": "⏳ Zatím nemá účet (zkusíme znovu)",
}


def _notify_discord(entry: dict[str, Any]) -> None:
    url = config.FREE_PRO_DISCORD_WEBHOOK_URL
    if not url or entry.get("status") not in _LABELS:
        return
    text = f"{_LABELS[entry['status']]}: {entry['email']} (formulář PRO zdarma)"
    try:
        httpx.post(url, json={"content": text, "allowed_mentions": {"parse": []}}, timeout=10.0)
    except Exception as exc:  # never break the sync because of Discord
        print(f"[free-pro] discord notify failed: {type(exc).__name__}", flush=True)


def redeem(global_store: Store, store_for: Callable[[str], Store], email: str) -> dict[str, Any]:
    """Zpracuje jednu žádost. Vrací ``{"status": ...}``: granted | already | has_paid_plan | no_account | invalid."""
    email_norm = (email or "").strip().lower()
    now = datetime.now(timezone.utc)
    entry: dict[str, Any] = {"email": email_norm, "at": now.isoformat(), "status": "invalid"}
    notify = False
    with _LOCK:
        rows = _load(global_store)
        if not _EMAIL.match(email_norm):
            return {"status": "invalid"}
        prior = next((r for r in rows if r.get("email") == email_norm and r.get("status") in {"granted", "already"}), None)
        if prior:
            return {"status": "already"}
        user = users.get_user_by_email(global_store, email_norm)
        if not user:
            entry["status"] = "no_account"
        else:
            ustore = store_for(str(user["id"]))
            record = ustore.billing_record() or {}
            paid = bool(record.get("subscription_id")) and record.get("plan") in {"start", "pro", "individual"}
            if paid or billing.comp_plan_of(record):
                entry["status"] = "has_paid_plan"
            else:
                until = now + timedelta(days=config.FREE_PRO_DAYS)
                record.update({"comp_plan": "pro", "comp_until": until.isoformat(), "comp_at": now.isoformat()})
                ustore.save_billing_record(record)
                billing.apply_watch_limit(ustore)
                billing.append_billing_log(ustore, {"kind": "Grant", "plan": "PRO", "amount": "0 Kč", "status": "Bez platby", "ok": True,
                                                    "note": f"Měsíc PRO zdarma z formuláře (do {until:%d. %m. %Y})"})
                entry.update({"status": "granted", "user_id": str(user["id"]), "until": until.isoformat()})
        was_waiting = any(r.get("email") == email_norm and r.get("status") == "no_account" for r in rows)
        notify = entry["status"] != "no_account" or not was_waiting
        rows = [r for r in rows if not (r.get("email") == email_norm and r.get("status") == "no_account")]
        rows.insert(0, entry)
        global_store.set_meta(META_KEY, json.dumps(rows[:2000], ensure_ascii=False))
    if notify:
        _notify_discord(entry)
    return {"status": entry["status"]}


def parse_sheet_emails(text: str) -> list[str]:
    """E-maily ze sloupce, jehož hlavička obsahuje „mail“ (bez duplicit, v pořadí odpovědí)."""
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        return []
    col = next((i for i, h in enumerate(rows[0]) if "mail" in h.lower()), None)
    if col is None:
        return []
    seen: dict[str, None] = {}
    for row in rows[1:]:
        if col < len(row) and row[col].strip():
            seen.setdefault(row[col].strip().lower(), None)
    return list(seen)


def _fetch_csv(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Realitify/free-pro"})
    with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - URL comes from our own config
        return resp.read(2_000_000).decode("utf-8", "replace")


def sync_sheet(global_store: Store, store_for: Callable[[str], Store], url: str = "") -> dict[str, int]:
    """Projde odpovědi ze Sheetu a každému novému e-mailu zkusí přidělit PRO. Vrací počty podle stavu."""
    counts: dict[str, int] = {}
    for email in parse_sheet_emails(_fetch_csv(url or config.FREE_PRO_SHEET_CSV_URL)):
        status = redeem(global_store, store_for, email)["status"]
        counts[status] = counts.get(status, 0) + 1
    return counts


async def run_sheet_loop(global_store: Store, store_for: Callable[[str], Store]) -> None:
    while True:
        try:
            counts = await asyncio.to_thread(sync_sheet, global_store, store_for)
            if counts.get("granted"):
                print(f"[free-pro] granted {counts['granted']} ({counts})", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[free-pro] sheet sync error: {exc}", flush=True)
        await asyncio.sleep(max(60, config.FREE_PRO_POLL_SECONDS))
