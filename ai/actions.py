"""WRITE actions: proposal (validation + server-written summary), confirmation, cancellation, audit log.

A WRITE tool never changes anything. It validates the request, stores it in ai_pending_actions and the user confirms
it with a click (POST /api/chat/actions/<id>/confirm). The same service functions as the HTTP routes do the work.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ai import limits
from ai.context import Services, ToolContext, ToolError
from app import user_actions
from app.locality_normalize import normalize_disposition

logger = logging.getLogger("realitify.ai")

PRO_ONLY = user_actions.PRO_ONLY_MESSAGE
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

NOTIFY_KEYS = {
    "email": "email",
    "discord": "discord",
    "push": "push",
    "instant": "instant",
    "quiet": "quiet",
    "quiet_from": "quietFrom",
    "quiet_to": "quietTo",
    "new_listings": "ntNew",
    "price_changes": "ntPrice",
    "expirations": "ntExpire",
    "digest": "ntDigest",
    "tips": "ntTips",
}
NOTIFY_LABELS = {
    "email": "e-mail",
    "discord": "Discord",
    "push": "push",
    "instant": "okamžitá upozornění",
    "quiet": "tiché hodiny",
    "new_listings": "nové nabídky",
    "price_changes": "změny cen",
    "expirations": "ukončené nabídky",
    "digest": "denní souhrn",
    "tips": "tipy",
}


def _kc(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", "\u00a0") + "\u00a0Kč"
    except (TypeError, ValueError):
        return str(value)


def _txt(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


@dataclass
class WriteSpec:
    prepare: Callable[[Services, ToolContext, dict[str, Any]], Awaitable[tuple[dict[str, Any], str]]]
    execute: Callable[[Services, ToolContext, dict[str, Any]], Awaitable[str]]


async def _settings(ctx: ToolContext) -> dict[str, Any]:
    return await asyncio.to_thread(ctx.ustore.app_settings)


async def _monitor(ctx: ToolContext, monitor_id: str) -> dict[str, Any]:
    monitor = await asyncio.to_thread(ctx.ustore.get_monitor, _txt(monitor_id, 40))
    if not monitor:
        raise ToolError("Hlídání s tímto id neexistuje. Zavolej list_my_monitors.")
    return monitor


def _limit_error(limit: int) -> ToolError:
    return ToolError(
        f"Limit tarifu je {limit} aktivních hlídacích profilů. Pro další hlídání je potřeba vyšší tarif (PRO)."
    )


# ---- create_monitor ---------------------------------------------------------------------------------------------

_PORTALS = {"sreality", "idnes", "bazos", "ceskereality", "bezrealitky", "annonce", "mmreality", "ulovdomov", "remax", "realitycz"}


async def _filters_to_url(svc: Services, a: dict[str, Any], portals: str = "all") -> tuple[str, list[str], str]:
    """Validated monitor filters -> (search_url, human summary parts, label). Shared by create and update."""
    locality = _txt(a.get("locality"), 80)
    if len(locality) < 2:
        raise ToolError("Chybí lokalita.")
    offer = _txt(a.get("offer_type")).casefold()
    if offer not in {"pronajem", "prodej"}:
        raise ToolError("offer_type musí být pronajem nebo prodej.")
    found = (await svc.resolve_locality(q=locality)).get("items") or []
    if not found:
        raise ToolError(f"Lokalitu „{locality}“ se nepodařilo najít. Zkus jiný název.")
    place = found[0]
    sizes = [normalize_disposition(str(d)) for d in (a.get("disposition") or []) if str(d).strip()]
    filters: dict[str, Any] = {"source": "sreality", "offers": [offer], "localities": [place], "sizes": [s for s in sizes if s]}
    for key in ("price_from", "price_to", "area_from", "area_to"):
        value = a.get(key)
        if value not in (None, "", 0):
            try:
                filters[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{key} musí být číslo.") from exc
    built = await asyncio.to_thread(user_actions.build_search_url, {"filters": filters, "portals": portals}, svc.filter_mod)
    label = _txt(place.get("label"), 80) or locality
    parts = [", ".join(filters["sizes"]) or "všechny dispozice", label, "pronájem" if offer == "pronajem" else "prodej"]
    if filters.get("price_to"):
        parts.append(f"do {_kc(filters['price_to'])}")
    if filters.get("price_from"):
        parts.append(f"od {_kc(filters['price_from'])}")
    if filters.get("area_from"):
        parts.append(f"od {filters['area_from']} m²")
    if filters.get("area_to"):
        parts.append(f"do {filters['area_to']} m²")
    default_name = f"{', '.join(filters['sizes']) or 'Byty'} · {label}"
    return built["url"], parts, default_name


async def _prepare_create_monitor(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    limit, enabled = await asyncio.to_thread(user_actions.watch_slots_left, ctx.ustore)
    if limit is not None and enabled >= limit:
        raise _limit_error(limit)
    url, parts, default_name = await _filters_to_url(svc, a)
    name = _txt(a.get("name"), 80) or default_name
    args = {"name": name, "search_url": url, "portals": "all"}
    return args, "Vytvořit hlídání: " + ", ".join(parts)


async def _exec_create_monitor(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    saved = await asyncio.to_thread(
        user_actions.save_monitor,
        ctx.ustore,
        {"name": a["name"], "search_url": a["search_url"], "portals": a["portals"], "enabled": True},
        on_event=svc.on_event,
        track_signup=svc.track_monitor_created,
    )
    return f"Hlídání „{saved.get('name') or a['name']}“ je vytvořené."


# ---- update_monitor / delete_monitor -----------------------------------------------------------------------------

_FILTER_ARGS = ("locality", "offer_type", "disposition", "price_from", "price_to", "area_from", "area_to")


async def _prepare_update_monitor(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    monitor = await _monitor(ctx, a.get("monitor_id"))
    new_name = _txt(a.get("name"), 80)
    enabled = a.get("enabled")
    portal = _txt(a.get("portal"), 20).casefold()
    changes_filters = any(a.get(k) not in (None, "", [], 0) for k in _FILTER_ARGS)
    if portal and portal != "all" and portal not in _PORTALS:
        raise ToolError("portal musí být all nebo jeden z podporovaných portálů (sreality, idnes, bazos, ceskereality, bezrealitky, annonce, mmreality, ulovdomov, remax, realitycz).")
    if not new_name and enabled is None and not changes_filters and not portal:
        raise ToolError("Chybí, co změnit: name, enabled, portal nebo filtry hlídání.")
    if enabled is True and not monitor.get("enabled"):
        limit, count = await asyncio.to_thread(user_actions.watch_slots_left, ctx.ustore)
        if limit is not None and count >= limit:
            raise _limit_error(limit)
    parts = []
    if new_name:
        parts.append(f"přejmenovat na „{new_name}“")
    if enabled is not None:
        parts.append("zapnout" if enabled else "vypnout")
    args: dict[str, Any] = {"monitor_id": monitor["id"], "name": new_name, "enabled": enabled}
    if portal:
        args["portals"] = portal
        parts.append("hlídat všechny portály" if portal == "all" else f"hlídat jen portál {portal}")
    if changes_filters:
        url, filter_parts, _name = await _filters_to_url(svc, a, portal or str(monitor.get("portals") or "all"))
        args["search_url"] = url
        parts.append("nové filtry: " + ", ".join(filter_parts))
    return args, f"Upravit hlídání „{monitor.get('name')}“: " + "; ".join(parts)


async def _exec_update_monitor(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    monitor = await _monitor(ctx, a["monitor_id"])
    payload = {
        **monitor,
        "name": a.get("name") or monitor.get("name"),
        "enabled": monitor.get("enabled") if a.get("enabled") is None else bool(a["enabled"]),
    }
    if a.get("search_url"):
        payload["search_url"] = a["search_url"]
    if a.get("portals"):
        payload["portals"] = a["portals"]
    saved = await asyncio.to_thread(user_actions.save_monitor, ctx.ustore, payload, on_event=svc.on_event)
    return f"Hlídání „{saved.get('name')}“ je upravené."


async def _prepare_delete_monitor(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    monitor = await _monitor(ctx, a.get("monitor_id"))
    return {"monitor_id": monitor["id"]}, f"Smazat hlídání „{monitor.get('name')}“"


async def _exec_delete_monitor(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    monitor = await _monitor(ctx, a["monitor_id"])
    await asyncio.to_thread(user_actions.delete_monitor, ctx.ustore, a["monitor_id"], on_event=svc.on_event)
    return f"Hlídání „{monitor.get('name')}“ je smazané."


# ---- saved / hidden listings + notes -----------------------------------------------------------------------------

async def _listing_title(svc: Services, url: str) -> str:
    store = svc.public_store()
    item = await asyncio.to_thread(store.catalog_item, "", None, "", url) if store else None
    return _txt((item or {}).get("name"), 100) or url[:100]


def _check_listing_url(url: Any) -> str:
    url = _txt(url, 500)
    if not url.startswith(("http://", "https://")):
        raise ToolError("listing_url musí být zdrojová adresa inzerátu (pole source_url z karty nebo get_listing).")
    return url


async def _prepare_listing_status(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    url = _check_listing_url(a.get("listing_url"))
    status = _txt(a.get("status")).casefold()
    if status not in {"saved", "hidden", "none"}:
        raise ToolError("status musí být saved, hidden nebo none.")
    title = await _listing_title(svc, url)
    verb = {"saved": "Uložit do oblíbených", "hidden": "Skrýt inzerát", "none": "Vrátit inzerát mezi nabídky"}[status]
    return {"url": url, "status": status}, f"{verb}: {title}"


async def _exec_listing_status(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    await asyncio.to_thread(
        user_actions.set_listing_user,
        ctx.ustore,
        a["url"],
        "" if a["status"] == "none" else a["status"],
        None,
        after=svc.after_listing_user,
        on_event=svc.on_event,
    )
    return "Hotovo."


async def _prepare_listings_status(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    urls = []
    for raw in a.get("listing_urls") or []:
        url = _check_listing_url(raw)
        if url not in urls:
            urls.append(url)
    if not urls:
        raise ToolError("Chybí listing_urls.")
    if len(urls) > 20:
        raise ToolError("Najednou jde změnit nejvýš 20 inzerátů.")
    status = _txt(a.get("status")).casefold()
    if status not in {"saved", "hidden", "none"}:
        raise ToolError("status musí být saved, hidden nebo none.")
    verb = {"saved": "Uložit do oblíbených", "hidden": "Skrýt", "none": "Vrátit mezi nabídky"}[status]
    return {"urls": urls, "status": status}, f"{verb}: {len(urls)} inzerátů"


async def _exec_listings_status(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    for url in a["urls"]:
        await asyncio.to_thread(
            user_actions.set_listing_user,
            ctx.ustore,
            url,
            "" if a["status"] == "none" else a["status"],
            None,
            after=svc.after_listing_user,
            on_event=svc.on_event,
        )
    return f"Hotovo ({len(a['urls'])} inzerátů)."


async def _prepare_monitor_from_url(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    limit, enabled = await asyncio.to_thread(user_actions.watch_slots_left, ctx.ustore)
    if limit is not None and enabled >= limit:
        raise _limit_error(limit)
    url = _txt(a.get("search_url"), 500)
    if not url.startswith(("http://", "https://")):
        raise ToolError("search_url musí být adresa hledání na portálu.")
    try:
        mod = svc.filter_mod(None, url)
        mod.parse_url(url)
    except Exception as exc:
        raise ToolError("Tuhle adresu hledání se nepodařilo rozpoznat. Zkus jiný portál nebo filtry.") from exc
    portal = _txt(a.get("portal"), 20).casefold() or "all"
    if portal != "all" and portal not in _PORTALS:
        raise ToolError("portal musí být all nebo jeden z podporovaných portálů.")
    name = _txt(a.get("name"), 80) or "Hlídání z adresy"
    return {"name": name, "search_url": url, "portals": portal}, f"Vytvořit hlídání „{name}“ z adresy hledání ({'všechny portály' if portal == 'all' else portal})"


async def _prepare_watch_prefs(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if a.get("clear"):
        return {"prefs": {}}, "Smazat výchozí preference hledání"
    prefs: dict[str, Any] = {}
    parts = []
    sizes = [normalize_disposition(str(d)) for d in (a.get("sizes") or []) if str(d).strip()]
    sizes = [x for x in sizes if x]
    if sizes:
        prefs["sizes"] = sizes
        parts.append("dispozice " + ", ".join(sizes))
    for key, label in (("price_from", "cena od"), ("price_to", "cena do"), ("area_from", "plocha od")):
        value = a.get(key)
        if value not in (None, "", 0):
            try:
                prefs[key] = int(value)
            except (TypeError, ValueError) as exc:
                raise ToolError(f"{key} musí být číslo.") from exc
            parts.append(f"{label} {prefs[key]}")
    if not parts:
        raise ToolError("Chybí, co nastavit.")
    current = (await _settings(ctx)).get("watch_prefs") or {}
    return {"prefs": {**current, **prefs}}, "Výchozí preference hledání: " + ", ".join(parts)


async def _exec_watch_prefs(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    await asyncio.to_thread(user_actions.save_settings, ctx.ustore, {"watch_prefs": a["prefs"]})
    return "Preference jsou uložené."


async def _prepare_listing_note(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    url = _check_listing_url(a.get("listing_url"))
    note = _txt(a.get("note"), 1000)
    title = await _listing_title(svc, url)
    summary = f"Poznámka k inzerátu {title}: „{note}“" if note else f"Smazat poznámku k inzerátu {title}"
    return {"url": url, "note": note}, summary


async def _exec_listing_note(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    await asyncio.to_thread(
        user_actions.set_listing_user,
        ctx.ustore,
        a["url"],
        None,
        a["note"],
        after=svc.after_listing_user,
        on_event=svc.on_event,
    )
    return "Poznámka je uložená."


# ---- notification settings ---------------------------------------------------------------------------------------------

async def _prepare_notify(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    notify: dict[str, Any] = {}
    parts = []
    for arg, key in NOTIFY_KEYS.items():
        if a.get(arg) is None:
            continue
        if arg in {"quiet_from", "quiet_to"}:
            value = _txt(a[arg], 5)
            if not _HHMM.match(value):
                raise ToolError(f"{arg} musí být ve tvaru HH:MM.")
            notify[key] = value
            parts.append(f"tiché hodiny {'od' if arg == 'quiet_from' else 'do'} {value}")
        else:
            notify[key] = bool(a[arg])
            parts.append(f"{NOTIFY_LABELS[arg]} {'zapnout' if notify[key] else 'vypnout'}")
    args: dict[str, Any] = {"notify": notify}
    if a.get("digest_hour") is not None:
        try:
            hour = int(a["digest_hour"])
        except (TypeError, ValueError) as exc:
            raise ToolError("digest_hour musí být číslo 0 až 23.") from exc
        if not 0 <= hour <= 23:
            raise ToolError("digest_hour musí být 0 až 23.")
        args["digest_hour"] = hour
        parts.append(f"denní souhrn v {hour}:00")
    if not parts:
        raise ToolError("Chybí, co změnit.")
    return args, "Nastavení notifikací: " + ", ".join(parts)


async def _exec_notify(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    payload: dict[str, Any] = {"notify": a["notify"]} if a.get("notify") else {}
    if "digest_hour" in a:
        payload["digest_hour"] = a["digest_hour"]
    await asyncio.to_thread(user_actions.save_settings, ctx.ustore, payload)
    return "Nastavení notifikací je uložené."


# ---- commute points ---------------------------------------------------------------------------------------------------

async def _prepare_commute_set(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    name = _txt(a.get("name"), 40)
    address = _txt(a.get("address"), 200)
    if not name or len(address) < 3:
        raise ToolError("Chybí název nebo adresa bodu.")
    points = (await _settings(ctx)).get("commute_points") or []
    point_id = _txt(a.get("point_id"), 20)
    if point_id and not any(p.get("id") == point_id for p in points):
        raise ToolError("Bod s tímto id neexistuje. Zavolej get_settings.")
    if not point_id and len(points) >= 2:
        raise ToolError("Už máte 2 body dojezdu. Jeden nejdřív smažte, nebo ho uprav přes point_id.")
    found = (await svc.geocode(q=address)).get("items") or []
    if not found:
        raise ToolError(f"Adresu „{address}“ se nepodařilo najít.")
    hit = found[0]
    args = {"point_id": point_id, "name": name, "address": address, "lat": float(hit["lat"]), "lon": float(hit["lon"])}
    return args, f"Uložit bod dojezdu „{name}“: {address} (nalezeno: {_txt(hit.get('label'), 100)})"


async def _exec_commute_set(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    points = list((await _settings(ctx)).get("commute_points") or [])
    new = {"name": a["name"], "address": a["address"], "lat": a["lat"], "lon": a["lon"]}
    if a.get("point_id"):
        points = [{**p, **new} if p.get("id") == a["point_id"] else p for p in points]
    elif len(points) < 2:
        points.append(new)
    else:
        raise user_actions.ActionError("Už máte 2 body dojezdu.")
    await asyncio.to_thread(user_actions.save_settings, ctx.ustore, {"commute_points": points})
    return f"Bod dojezdu „{a['name']}“ je uložený."


async def _prepare_commute_delete(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    points = (await _settings(ctx)).get("commute_points") or []
    point = next((p for p in points if p.get("id") == _txt(a.get("point_id"), 20)), None)
    if not point:
        raise ToolError("Bod s tímto id neexistuje. Zavolej get_settings.")
    return {"point_id": point["id"]}, f"Smazat bod dojezdu „{point.get('name')}“"


async def _exec_commute_delete(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    points = [p for p in ((await _settings(ctx)).get("commute_points") or []) if p.get("id") != a["point_id"]]
    await asyncio.to_thread(user_actions.save_settings, ctx.ustore, {"commute_points": points})
    return "Bod dojezdu je smazaný."


# ---- manual check, test messages, discord ----------------------------------------------------------------------

async def _prepare_check(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    monitor_id = _txt(a.get("monitor_id"), 40)
    if not monitor_id:
        return {"monitor_id": None}, "Zkontrolovat všechna hlídání hned"
    monitor = await _monitor(ctx, monitor_id)
    return {"monitor_id": monitor["id"]}, f"Zkontrolovat hlídání „{monitor.get('name')}“ hned"


async def _exec_check(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    await svc.check_once(ctx.user_id, a.get("monitor_id"))
    return "Kontrola proběhla."


_CHANNELS = {"email": "e-mail", "discord": "Discord", "whatsapp": "WhatsApp"}


async def _prepare_test(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    channel = _txt(a.get("channel")).casefold()
    if channel not in _CHANNELS:
        raise ToolError("channel musí být email, discord nebo whatsapp.")
    if channel == "whatsapp":
        try:
            await asyncio.to_thread(user_actions.require_pro_whatsapp, ctx.ustore)
        except user_actions.ActionError as exc:
            raise ToolError(PRO_ONLY) from exc
    return {"channel": channel}, f"Poslat testovací zprávu: {_CHANNELS[channel]}"


async def _exec_test(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    if a["channel"] == "email":
        await user_actions.send_test_email(ctx.ustore)
    elif a["channel"] == "whatsapp":
        await user_actions.send_test_whatsapp(ctx.ustore)
    else:
        await svc.discord_test(ctx.user_id, None)
    return f"Testovací zpráva ({_CHANNELS[a['channel']]}) byla odeslána."


async def _prepare_unlink(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> tuple[dict[str, Any], str]:
    return {}, "Odpojit Discord od účtu"


async def _exec_unlink(svc: Services, ctx: ToolContext, a: dict[str, Any]) -> str:
    await asyncio.to_thread(user_actions.unlink_discord, ctx.ustore)
    return "Discord je odpojený."


WRITE_SPECS: dict[str, WriteSpec] = {
    "create_monitor": WriteSpec(_prepare_create_monitor, _exec_create_monitor),
    "update_monitor": WriteSpec(_prepare_update_monitor, _exec_update_monitor),
    "delete_monitor": WriteSpec(_prepare_delete_monitor, _exec_delete_monitor),
    "set_listing_status": WriteSpec(_prepare_listing_status, _exec_listing_status),
    "set_listing_note": WriteSpec(_prepare_listing_note, _exec_listing_note),
    "set_listings_status": WriteSpec(_prepare_listings_status, _exec_listings_status),
    "create_monitor_from_url": WriteSpec(_prepare_monitor_from_url, _exec_create_monitor),
    "update_watch_prefs": WriteSpec(_prepare_watch_prefs, _exec_watch_prefs),
    "update_notify_settings": WriteSpec(_prepare_notify, _exec_notify),
    "set_commute_point": WriteSpec(_prepare_commute_set, _exec_commute_set),
    "delete_commute_point": WriteSpec(_prepare_commute_delete, _exec_commute_delete),
    "check_monitor_now": WriteSpec(_prepare_check, _exec_check),
    "send_test_notification": WriteSpec(_prepare_test, _exec_test),
    "unlink_discord": WriteSpec(_prepare_unlink, _exec_unlink),
}


# ---- proposal / confirm / cancel ---------------------------------------------------------------------------------

async def propose(svc: Services, ctx: ToolContext, name: str, raw_args: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Validate and store a WRITE action. Nothing is changed. Returns (tool result for the model, SSE events)."""
    if ctx.proposals >= limits.MAX_PROPOSALS_PER_MESSAGE:
        raise ToolError(f"Příliš mnoho navržených akcí najednou (max {limits.MAX_PROPOSALS_PER_MESSAGE}).")
    spec = WRITE_SPECS[name]
    args, summary = await spec.prepare(svc, ctx, raw_args)
    action_id = await asyncio.to_thread(
        svc.global_store.ai_action_create, ctx.user_id, ctx.chat_id, name, args, summary, limits.PENDING_TTL_SECONDS
    )
    ctx.proposals += 1
    result = '{"status":"pending_confirmation","message":"Čeká na potvrzení uživatele. Akce zatím NEBYLA provedena."}'
    return result, [{"type": "confirm", "action_id": action_id, "summary": summary}]


class ActionRefused(Exception):
    """The confirm/cancel request is not allowed (`status` is the HTTP status)."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def _note_in_chat(store: Any, action: dict[str, Any], text: str) -> None:
    if action.get("chat_id"):
        store.ai_chat_add_message(action["chat_id"], "assistant", text)


async def _load_owned(svc: Services, user_id: str, action_id: str) -> dict[str, Any]:
    await asyncio.to_thread(svc.global_store.ai_actions_expire, user_id)
    action = await asyncio.to_thread(svc.global_store.ai_action_get, action_id)
    if not action or action["user_id"] != user_id:
        raise ActionRefused("Tato akce vám nepatří.", 403)
    return action


async def confirm(svc: Services, ctx: ToolContext, action_id: str) -> dict[str, str]:
    store = svc.global_store
    action = await _load_owned(svc, ctx.user_id, action_id)
    if action["status"] == "expired":
        raise ActionRefused("Platnost akce vypršela. Požádejte Báru o ni znovu.", 410)
    if not await asyncio.to_thread(store.ai_action_claim, action_id, ctx.user_id, "executing"):
        raise ActionRefused("Akce už byla vyřízena.", 409)
    ctx.chat_id = action.get("chat_id")
    try:
        message = await WRITE_SPECS[action["tool"]].execute(svc, ctx, action["args"])
        result, text = "executed", f"✅ Provedeno: {action['summary']}"
    except (user_actions.ActionError, ToolError) as exc:
        message, result, text = str(exc), "error", f"⚠️ Nepovedlo se: {action['summary']}. {exc}"
    except Exception as exc:
        logger.error("ai_action_failed tool=%s error=%s", action["tool"], type(exc).__name__, exc_info=True)
        message, result, text = "Akci se nepodařilo provést.", "error", f"⚠️ Nepovedlo se: {action['summary']}."
    await asyncio.to_thread(store.ai_action_set_status, action_id, result)
    await asyncio.to_thread(store.ai_action_log, ctx.user_id, action["tool"], action["args"], result)
    await asyncio.to_thread(_note_in_chat, store, action, text)
    return {"status": result, "message": message, "note": text}


async def cancel(svc: Services, ctx: ToolContext, action_id: str) -> dict[str, str]:
    store = svc.global_store
    action = await _load_owned(svc, ctx.user_id, action_id)
    if action["status"] == "expired":
        raise ActionRefused("Platnost akce vypršela.", 410)
    if not await asyncio.to_thread(store.ai_action_claim, action_id, ctx.user_id, "cancelled"):
        raise ActionRefused("Akce už byla vyřízena.", 409)
    text = f"Zrušeno: {action['summary']}"
    await asyncio.to_thread(store.ai_action_log, ctx.user_id, action["tool"], action["args"], "cancelled")
    await asyncio.to_thread(_note_in_chat, store, action, text)
    return {"status": "cancelled", "message": "Akce byla zrušena.", "note": text}
