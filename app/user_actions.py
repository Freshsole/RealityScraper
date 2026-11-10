"""User actions shared by the HTTP routes and the AI chat tools.

One implementation per action: the route handlers in main.py and the chat tools both call these functions, so
validation, plan checks and side effects cannot drift apart. Nothing here knows about FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from app import account as user_account
from app import billing as stripe_billing
from app import email_notify as mail_notify
from app import localities
from app import whatsapp as wa_notify
from app.emailing import triggers as email_triggers
from app.catalog_sync import monitor_search_targets
from app.store import Store

logger = logging.getLogger("realitify")

EventHook = Callable[[str, dict[str, Any]], None]


def _no_event(_name: str, _props: dict[str, Any]) -> None:
    return None


class ActionError(Exception):
    """A user-facing failure (validation, plan limit, delivery). `status` mirrors the HTTP status of the route."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


PRO_ONLY_MESSAGE = "Tato funkce je dostupná v PRO"


# ---- search URL from structured filters (POST /api/filters/build) ---------------------------------------------

def build_search_url(payload: dict[str, Any], filter_mod: Callable[..., Any]) -> dict[str, Any]:
    """Filters -> portal search URL + targets. `filter_mod(source)` returns the URL builder module of a portal."""
    from app import bazos_url, bezrealitky_url, idnes_url

    filters = payload.get("filters") or {}
    mod = filter_mod(filters.get("source"))
    if not filters.get("source"):
        filters = {
            **filters,
            "source": "idnes"
            if mod is idnes_url
            else "bazos"
            if mod is bazos_url
            else "bezrealitky"
            if mod is bezrealitky_url
            else "sreality",
        }
    filters = localities.normalize_filters(filters)
    built = mod.build_url(filters or mod.default_filters())
    portals = str(payload.get("portals") or "all")
    targets = monitor_search_targets({"search_url": built, "portals": portals})
    return {"url": built, "filters": mod.parse_url(built), "targets": targets}


# ---- monitors (POST /api/monitors, DELETE /api/monitors/{id}) ---------------------------------------------------

def save_monitor(
    ustore: Store,
    payload: dict[str, Any],
    *,
    on_event: EventHook = _no_event,
    track_signup: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Create or update a monitor. Check interval comes from the plan; plan limits are enforced by Store.save_monitor."""
    if not (payload.get("search_url") or "").strip():
        raise ActionError("Chybí URL hledání. Vyplňte ho tlačítkem „Použít URL v profilu“.")
    existing = ustore.get_monitor(str(payload.get("id") or "")) if payload.get("id") else None
    payload = {**payload, "interval_sec": stripe_billing.billing_state(ustore)["check_interval_sec"]}
    try:
        try:
            saved = ustore.save_monitor(payload)
        except ValueError as exc:
            if "Limit tarifu" in str(exc):
                _fire_and_forget(email_triggers.on_dog_limit_hit, ustore)
            raise
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    if existing is None and track_signup is not None:
        try:
            track_signup()
        except Exception:
            pass
    on_event(
        "monitor_created" if existing is None else "monitor_updated",
        {"portal_count": len(saved.get("portals") or []), "is_active": bool(saved.get("enabled", True))},
    )
    return saved


def _fire_and_forget(fn: Callable[..., Any], *args: Any) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(asyncio.to_thread(fn, *args), name="email-dog-limit")


def delete_monitor(ustore: Store, monitor_id: str, *, on_event: EventHook = _no_event) -> None:
    try:
        ustore.delete_monitor(monitor_id)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    on_event("monitor_deleted", {})


def watch_slots_left(ustore: Store) -> tuple[int | None, int]:
    """(plan limit or None for unlimited, number of enabled monitors)."""
    return stripe_billing.watch_limit_for(ustore), ustore.enabled_monitor_count()


# ---- saved / hidden listings and notes (POST /api/catalog/user) -----------------------------------------------

def set_listing_user(
    ustore: Store,
    url: str,
    status: str | None,
    note: str | None,
    *,
    after: Callable[[Store], None] | None = None,
    on_event: EventHook = _no_event,
) -> dict[str, Any]:
    try:
        result = ustore.set_listing_user(url or "", status, note)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    if after is not None:
        after(ustore)
    on_event(
        "listing_status_updated",
        {"listing_status": str(status or ""), "has_note": bool(str(note or "").strip())},
    )
    return result


# ---- notification settings ---------------------------------------------------------------------------------------

def save_settings(ustore: Store, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return ustore.save_app_settings(payload)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc


# ---- test notifications ---------------------------------------------------------------------------------------------

async def send_test_email(ustore: Store) -> dict[str, Any]:
    try:
        sent = await mail_notify.notify_listing(
            ustore, {"name": "Testovací zpráva z Realitify"}, "test", ignore_quiet=True
        )
    except Exception as exc:
        logger.warning("test email failed: %s", exc)
        raise ActionError("Testovací e-mail se nepodařilo odeslat. Zkuste to později.", 502) from exc
    if not sent:
        raise ActionError("Zapněte e-mail a na serveru nastavte SMTP_HOST a SMTP_FROM")
    return {"ok": True}


def require_pro_whatsapp(ustore: Store) -> None:
    if not wa_notify.plan_allows(ustore):
        raise ActionError("WhatsApp notifikace jsou jen v tarifu PRO", 403)


async def send_test_whatsapp(ustore: Store) -> dict[str, Any]:
    require_pro_whatsapp(ustore)
    try:
        sent = await wa_notify.notify_listing(
            ustore, {"name": "Testovací zpráva z Realitify"}, "test", ignore_quiet=True
        )
    except Exception as exc:
        logger.warning("whatsapp test failed: %s", exc)
        raise ActionError("Testovací zprávu se nepodařilo odeslat. Zkuste to později.", 502) from exc
    if not sent:
        raise ActionError("Zapněte WhatsApp a vyplňte telefonní číslo")
    return {"ok": True}


def unlink_discord(ustore: Store) -> dict[str, Any]:
    return user_account.unlink_discord(ustore)
