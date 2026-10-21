"""Měsíc PRO zdarma z Google Formuláře (omluva za výpadek e-mailů).

Odpovědi formuláře se zapisují do Google Sheets, který je publikovaný jako CSV (``FREE_PRO_SHEET_CSV_URL``).
Appka CSV čte každých ``FREE_PRO_POLL_SECONDS`` s; sloupec s e-mailem pozná podle hlavičky obsahující „mail“.
Účet zdarma nebo se Startem dostane PRO na ``FREE_PRO_DAYS`` dní (``comp_plan`` + ``comp_until``, konec řeší
``billing.expire_comp_if_due``; Start se dál platí jako dřív). Účtu s placeným PRO se o ty dny posune další platba
(Stripe ``trial_end``). E-mail bez účtu se uloží jako lead (/hledat) a zkusí se znovu při dalším čtení, takže si
měsíc vyzvedne i ten, kdo se zaregistruje později. Jeden grant na e-mail.
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
from app import guest as guest_access
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
    "extended": "✅ Měsíc PRO zdarma: platba posunuta o měsíc",
    "has_higher_plan": "⚠️ Má PRO/vyšší tarif, který nejde posunout, nic se nezměnilo",
    "stripe_error": "❌ Posun platby se nepovedl",
    "no_account": "⏳ Zatím nemá účet (uložen jako lead, zkusíme znovu)",
}
_TERMINAL = {"granted", "extended"}


def _notify_discord(entry: dict[str, Any], detail: str = "") -> None:
    url = config.FREE_PRO_DISCORD_WEBHOOK_URL
    if not url or entry.get("status") not in _LABELS:
        return
    text = f"{_LABELS[entry['status']]}: {entry['email']} (formulář PRO zdarma)" + (f"\n{detail}" if detail else "")
    try:
        httpx.post(url, json={"content": text[:1800], "allowed_mentions": {"parse": []}}, timeout=10.0)
    except Exception as exc:  # never break the sync because of Discord
        print(f"[free-pro] discord notify failed: {type(exc).__name__}", flush=True)


def _kind(record: dict[str, Any]) -> str:
    """free | start_paid | pro_paid | higher: what the account is on right now (an admin grant of "free" is free)."""
    comp = billing.comp_plan_of(record)
    plan = str(record.get("plan") or "free")
    live = bool(record.get("subscription_id")) and str(record.get("status") or "") in billing.LIVE_SUB_STATUSES
    if comp in {"pro", "individual"} or plan == "individual":
        return "higher"
    if live and plan == "pro":
        return "pro_paid"
    if live and plan == "start":
        return "start_paid"
    return "free"


def _apply(ustore: Store, now: datetime) -> tuple[str, dict[str, Any]]:
    """Give the month to one account. Returns (status, extra entry fields)."""
    record = ustore.billing_record() or {}
    kind = _kind(record)
    if kind == "higher":
        return "has_higher_plan", {}
    if kind == "pro_paid":
        try:
            result = billing.extend_subscription(ustore, config.FREE_PRO_DAYS)
        except Exception as exc:
            return "stripe_error", {"error": str(exc)[:300]}
        return "extended", {"until": result["next_charge"]}
    # free account or Start: PRO on top for the period, the Start subscription keeps its own billing untouched
    until = now + timedelta(days=config.FREE_PRO_DAYS)
    record.update({"comp_plan": "pro", "comp_until": until.isoformat(), "comp_at": now.isoformat()})
    ustore.save_billing_record(record)
    billing.apply_watch_limit(ustore)
    billing.append_billing_log(ustore, {"kind": "Grant", "plan": "PRO", "amount": "0 Kč", "status": "Bez platby", "ok": True,
                                        "note": f"Měsíc PRO zdarma z formuláře (do {until:%d. %m. %Y})"
                                                + (", Start se dál platí jako dřív" if kind == "start_paid" else "")})
    return "granted", {"until": until.isoformat(), "was": kind}


def _add_lead(email: str) -> None:
    try:
        known = {str(r.get("email") or "").strip().lower() for r in guest_access.list_leads(1_000_000)}
        if email not in known:
            guest_access.add_lead(email, source="pro-zdarma-form")
    except Exception as exc:
        print(f"[free-pro] lead save failed: {type(exc).__name__}", flush=True)


def redeem(global_store: Store, store_for: Callable[[str], Store], email: str) -> dict[str, Any]:
    """Zpracuje jednu žádost. Stav: granted | extended | has_higher_plan | stripe_error | no_account | already | invalid.

    Hotové (granted/extended) se už nikdy neopakuje. Ostatní stavy se při dalším čtení tabulky zkusí znovu (účet se
    mohl zaregistrovat, admin změnil tarif), ale Discord se ozve jen při změně stavu.
    """
    email_norm = (email or "").strip().lower()
    now = datetime.now(timezone.utc)
    if not _EMAIL.match(email_norm):
        return {"status": "invalid"}
    entry: dict[str, Any] = {"email": email_norm, "at": now.isoformat(), "status": "invalid"}
    detail = ""
    with _LOCK:
        rows = _load(global_store)
        prior = next((r for r in rows if r.get("email") == email_norm), None)
        if prior and prior.get("status") in _TERMINAL:
            return {"status": "already"}
        if prior and prior.get("status") == "stripe_error":  # do not hammer Stripe every poll
            try:
                if now - datetime.fromisoformat(str(prior.get("at"))) < timedelta(hours=1):
                    return {"status": "stripe_error"}
            except ValueError:
                pass
        user = users.get_user_by_email(global_store, email_norm)
        if not user:
            entry["status"] = "no_account"
            if not (prior and prior.get("lead_saved")):
                _add_lead(email_norm)
            entry["lead_saved"] = True
        else:
            status, extra = _apply(store_for(str(user["id"])), now)
            entry.update(status=status, user_id=str(user["id"]), **extra)
            detail = str(extra.get("error") or "")
        if prior and prior.get("first_at"):
            entry["first_at"] = prior["first_at"]
        else:
            entry["first_at"] = entry["at"]
        notify = not prior or prior.get("status") != entry["status"]
        rows = [r for r in rows if r.get("email") != email_norm]
        rows.insert(0, entry)
        global_store.set_meta(META_KEY, json.dumps(rows[:2000], ensure_ascii=False))
    if notify:
        _notify_discord(entry, detail)
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
