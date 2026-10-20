"""When each automatic e-mail is sent: event hooks called from the app and the time-based scheduler.

Rules follow the wording doc. Anything that needs data the app does not have (market numbers, matches,
neighbour districts) is skipped instead of sent half-empty.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app import config
from app.emailing import service, tracking
from app.emailing import store as estore

PRAGUE = ZoneInfo("Europe/Prague")
FREE_PORTALS = ("sreality", "bezrealitky", "idnes", "bazos")
_MONTHS = ["leden", "únor", "březen", "duben", "květen", "červen", "červenec", "srpen", "září", "říjen", "listopad", "prosinec"]
_SPEED = {"free": ("5 minut", "do 5 minut"), "start": ("60 sekund", "do 60 sekund"), "pro": ("30 sekund", "do 30 sekund")}
# neighbouring Prague districts (for the "widen the filter" suggestion)
_PRAHA_NEIGHBOURS = {
    1: (2, 5, 7, 8), 2: (1, 3, 4, 10), 3: (2, 8, 9, 10), 4: (2, 10, 11, 12), 5: (1, 13, 15),
    6: (7, 17, 16), 7: (1, 6, 8), 8: (1, 3, 7, 9), 9: (3, 8, 14, 19), 10: (2, 3, 4, 15),
}
StoreFor = Callable[[str], Any]


# ---------------------------------------------------------------- helpers --

def link(path: str, key: str, stream: str = "lifecycle") -> str:
    sep = "&" if "?" in path else "?"
    return f"{tracking.base_url()}{path}{sep}utm_source=email&utm_medium={stream}&utm_campaign={key}"


def fmt_czk(value: Any) -> str:
    try:
        return f"{int(round(float(value))):,}".replace(",", " ")
    except (TypeError, ValueError):
        return ""


def fmt_date(value: Any) -> str:
    stamp: datetime | None = None
    if isinstance(value, (int, float)) and value:
        stamp = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, str) and value:
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    elif isinstance(value, datetime):
        stamp = value
    if stamp is None:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    local = stamp.astimezone(PRAGUE)
    return f"{local.day}. {local.month}. {local.year}"


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _g(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        value = getattr(obj, key)
    except AttributeError:
        try:
            value = obj[key]
        except Exception:
            return default
    return default if value is None else value


def user_id_for_store(ustore: Any) -> str:
    return Path(str(ustore.path)).parent.name


def get_user(user_id: str) -> dict[str, Any] | None:
    from app import users

    return users.get_user_by_id(estore.global_store(), user_id)


def user_for_store(ustore: Any) -> dict[str, Any] | None:
    return get_user(user_id_for_store(ustore))


def _plan(ustore: Any) -> dict[str, Any]:
    from app import billing

    return billing.billing_state(ustore)


def _meta_get(key: str) -> str:
    return estore.get_meta(key) or ""


def _once(key: str, value: str = "1") -> bool:
    """True the first time a marker is set."""
    with estore.db() as conn:
        cur = conn.execute("INSERT OR IGNORE INTO email_meta(key, value) VALUES (?, ?)", (key, value))
        return cur.rowcount == 1


def _send(key: str, user: dict[str, Any], variables: dict[str, Any], dedupe: str | None, **kw: Any) -> int | None:
    if not service.is_enabled(key):
        return None
    return service.send_template(key, user=user, variables=variables, dedupe_key=dedupe, **kw)


def _recent(user_id: str, keys: tuple[str, ...], days: float) -> bool:
    since = estore.iso_in(-days * 86400)
    marks = ",".join("?" for _ in keys)
    with estore.db() as conn:
        row = conn.execute(
            f"SELECT 1 FROM email_log WHERE user_id = ? AND template_key IN ({marks}) AND status IN ('queued','sending','sent')"
            " AND created_at >= ? LIMIT 1",
            [user_id, *keys, since],
        ).fetchone()
    return row is not None


def describe_monitor(monitor: dict[str, Any]) -> str:
    """Human readable filter for the e-mails; the dog's own name is the best short description."""
    name = str(monitor.get("name") or "").strip()
    return name or "vaše hledání"


def _is_untouched_seed(monitor: dict[str, Any]) -> bool:
    """Every new account starts with a seeded "Praha pronájmy" monitor; it is not a dog the user set up."""
    return (
        monitor.get("id") == "default"
        and monitor.get("name") == "Praha pronájmy"
        and str(monitor.get("search_url") or "") == config.SEARCH_URL
    )


def _monitors(ustore: Any) -> list[dict[str, Any]]:
    """The user's own dogs (the untouched seed monitor does not count)."""
    try:
        return [m for m in ustore.list_monitors_light() if not _is_untouched_seed(m)]
    except Exception:
        return []


def portal_label(url: str) -> str:
    low = (url or "").lower()
    for needle, label in (
        ("sreality", "Sreality"), ("bezrealitky", "Bezrealitky"), ("idnes", "Reality.iDNES"), ("bazos", "Bazoš"),
        ("ceskereality", "ČeskéReality"), ("annonce", "Annonce"), ("mmreality", "M&M Reality"), ("remax", "RE/MAX"),
        ("reality.cz", "Reality.cz"), ("ulovdomov", "UlovDomov"),
    ):
        if needle in low:
            return label
    return "Portál"


def _portal_id(url: str) -> str:
    try:
        from app.identity import portal_from_url

        return portal_from_url((url or "").lower())
    except Exception:
        return ""


# ------------------------------------------------------- account events --

def on_verification_requested(user: dict[str, Any], verify_url: str) -> int | None:
    return service.send_template("verify_email", user=user, variables={"odkaz": verify_url}, immediate=True)


def on_password_reset_requested(user_or_email: Any, reset_url: str) -> int | None:
    user = user_or_email if isinstance(user_or_email, dict) else {"email": str(user_or_email)}
    return service.send_template("password_reset", user=user, variables={"odkaz": reset_url}, immediate=True)


def on_verified(user: dict[str, Any]) -> None:
    uid = str(user.get("id") or "")
    if not uid:
        return
    _once(f"verified:{uid}", estore.now_iso())
    _send("welcome", user, {"odkaz": link("/monitory", "welcome")}, f"welcome:{uid}")


def on_password_changed(user: dict[str, Any], reset_url: str) -> None:
    now = datetime.now(PRAGUE)
    service.fire(
        "password_changed",
        user=user,
        variables={"datum_cas": f"{now.day}. {now.month}. {now.year} {now:%H:%M}", "odkaz": reset_url},
    )


def on_email_changed(user: dict[str, Any], old_email: str, new_email: str, verify_url: str) -> None:
    service.fire("email_changed_old", user=user, to=old_email, variables={"novy_email": new_email})
    service.fire("email_changed_confirm", user=user, to=new_email, variables={"odkaz": verify_url})


def on_account_deleted(user: dict[str, Any]) -> None:
    service.send_template("account_deleted", user=user, immediate=True)


# -------------------------------------------------------------- billing --

def _plan_vars(plan_id: str) -> dict[str, str]:
    from app import billing

    meta = billing.PLANS.get(plan_id) or billing.PLANS["free"]
    limit = meta.get("watch_limit")
    return {
        "tarif": str(meta["label"]),
        "cena": f"{fmt_czk(meta['price_czk'])} Kč" if meta.get("price_czk") else "",
        "rychlost": _SPEED.get(plan_id, _SPEED["free"])[0],
        "pocet_psu": "neomezeně" if limit is None else str(limit),
    }


def _offers_since(ustore: Any, days: float) -> tuple[int, int]:
    """(new offers for the user's dogs, those on portals outside the free tier) in the last ``days``."""
    since = estore.iso_in(-days * 86400)
    try:
        with ustore.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT l.url FROM events e JOIN listings l ON l.id = e.listing_id AND l.monitor_id = e.monitor_id"
                " WHERE e.kind = 'new' AND e.created_at >= ?",
                (since,),
            ).fetchall()
    except Exception:
        return 0, 0
    total = len(rows)
    extra = sum(1 for r in rows if _portal_id(r[0]) not in FREE_PORTALS)
    return total, extra


def on_billing_event(ustore: Any, etype: str, obj: Any, before: dict[str, Any], after: dict[str, Any]) -> None:
    """Called by billing with the billing record before and after syncing the event."""
    if not estore.is_bound():
        return
    user = user_for_store(ustore)
    if not user:
        return
    uid = user["id"]
    paid = ("start", "pro")
    b_plan, a_plan = before.get("plan") or "free", after.get("plan") or "free"
    b_status, a_status = before.get("status") or "", after.get("status") or ""
    sub_id = str(after.get("subscription_id") or before.get("subscription_id") or "")
    period_end = after.get("current_period_end") or before.get("current_period_end")

    if etype in ("invoice.paid", "invoice.payment_succeeded"):
        _on_invoice_paid(user, ustore, obj, after)
    elif etype == "invoice.payment_failed":
        _on_invoice_failed(user, obj, after, before)

    if a_plan in paid and a_status == "trialing" and b_status != "trialing" and sub_id:
        variables = {**_plan_vars(a_plan), "datum_konec": fmt_date(period_end), "odkaz": link("/monitory", "trial_started", "transactional")}
        _send("trial_started", user, variables, f"trial_started:{sub_id}")
        end = _parse(period_end)
        if end:
            delay = max(0.0, (end - timedelta(hours=24) - datetime.now(timezone.utc)).total_seconds())
            total, extra = _offers_since(ustore, 2)
            _send(
                "trial_ending", user,
                {**_plan_vars(a_plan), "datum_konec": fmt_date(period_end), "pocet": str(total), "pocet_navic": str(extra),
                 "odkaz": link("/nastaveni/predplatne", "trial_ending", "transactional"),
                 "odkaz_zrusit": link("/nastaveni/predplatne", "trial_ending", "transactional")},
                f"trial_ending:{sub_id}", delay_sec=delay, meta={"sub_id": sub_id},
            )
    if a_plan == "pro" and b_plan != "pro" and a_status in ("trialing", "active") and sub_id:
        _send("pro_mcp", user, {"odkaz": link("/nastaveni/agenti", "pro_mcp")}, f"pro_mcp:{uid}", delay_sec=86400)

    if after.get("cancel_at_period_end") and not before.get("cancel_at_period_end") and a_plan in paid:
        _send(
            "subscription_canceled", user,
            {**_plan_vars(a_plan), "datum_konec": fmt_date(period_end), "odkaz": link("/nastaveni/predplatne", "subscription_canceled", "transactional"),
             **{f"odkaz_duvod_{r}": tracking.action_url(uid, f"duvod_{r}") for r in ("bydleni", "draha", "relevance", "jiny")}},
            f"sub_canceled:{sub_id}:{period_end}",
        )

    if b_plan in paid and a_plan == "free":
        _once(f"paid_ended:{uid}:{sub_id}", estore.now_iso())
        estore.set_meta(f"paid_ended:{uid}", estore.now_iso())
        monitors = _monitors(ustore)
        active = next((m for m in monitors if m.get("enabled")), None)
        variables = {
            **_plan_vars(b_plan),
            "pes_aktivni": describe_monitor(active) if active else "",
            "pocet_pozastaveno": str(max(0, len([m for m in monitors if not m.get("enabled")]))),
            "odkaz": link("/nastaveni/predplatne" if b_status in ("past_due", "unpaid") else "/monitory", "downgrade", "transactional"),
        }
        if b_status in ("past_due", "unpaid") or estore.get_meta(f"payfail:{uid}"):
            _send("downgraded_after_failure", user, variables, f"downgraded:{sub_id}")
        else:
            _send("subscription_ended", user, variables, f"sub_ended:{sub_id}")


def _on_invoice_paid(user: dict[str, Any], ustore: Any, inv: Any, after: dict[str, Any]) -> None:
    amount = int(_g(inv, "amount_paid", 0) or 0)
    estore.set_meta(f"payfail:{user['id']}", "")
    if amount <= 0:
        return  # the free trial invoice
    lines = _g(_g(inv, "lines"), "data", []) or []
    period = _g(lines[0], "period") if lines else None
    plan = after.get("plan") or "start"
    card = after.get("payment") or {}
    number = str(_g(inv, "number", "") or _g(inv, "id", ""))
    variables = {
        **_plan_vars(plan),
        "cena": f"{fmt_czk(amount / 100)} Kč",
        "obdobi_od": fmt_date(_g(period, "start")),
        "obdobi_do": fmt_date(_g(period, "end")),
        "cislo_faktury": number,
        "karta_typ": str(card.get("brand") or "").title(),
        "karta_posledni4": str(card.get("last4") or ""),
        "odkaz": link("/nastaveni/predplatne", "payment_ok", "transactional"),
    }
    pdf = str(_g(inv, "invoice_pdf", "") or "")
    _send("payment_ok", user, variables, f"payment_ok:{_g(inv, 'id', number)}",
          meta={"attachment_url": pdf, "attachment_name": f"faktura-{number}.pdf"} if pdf else None)


def _on_invoice_failed(user: dict[str, Any], inv: Any, after: dict[str, Any], before: dict[str, Any]) -> None:
    uid = user["id"]
    attempt = int(_g(inv, "attempt_count", 1) or 1)
    first = estore.get_meta(f"payfail:{uid}")
    if not first:
        first = estore.now_iso()
        estore.set_meta(f"payfail:{uid}", first)
    deadline = (_parse(first) or datetime.now(timezone.utc)) + timedelta(days=7)
    plan = after.get("plan") if after.get("plan") in ("start", "pro") else (before.get("plan") or "start")
    amount = int(_g(inv, "amount_due", 0) or 0)
    variables = {
        **_plan_vars(plan),
        "datum_downgrade": fmt_date(deadline),
        "odkaz": link("/nastaveni/predplatne", "payment_failed", "transactional"),
    }
    if amount:
        variables["cena"] = f"{fmt_czk(amount / 100)} Kč"
    inv_id = str(_g(inv, "id", "") or "")
    key = "payment_failed_last" if attempt >= 3 else "payment_failed"
    if attempt <= 3:
        _send(key, user, variables, f"payment_failed:{inv_id}:{min(attempt, 3)}")


def on_dog_limit_hit(ustore: Any) -> None:
    """A free user tried to add a second dog: upsell after an hour unless they upgraded in the meantime."""
    user = user_for_store(ustore) if estore.is_bound() else None
    if not user or _plan(ustore).get("plan") != "free":
        return
    if _recent(user["id"], ("upsell_missed_offers", "upsell_dog_limit", "upsell_speed"), 7):
        return
    day = datetime.now(PRAGUE).strftime("%Y%m%d")
    _send("upsell_dog_limit", user, {"odkaz": link("/nastaveni/predplatne?plan=start", "upsell_dog_limit")},
          f"upsell_limit:{user['id']}:{day}", delay_sec=3600)


# ---------------------------------------------------------------- guards --

def _guard_plan_free(row: dict[str, Any]) -> str | None:
    store_for = _STATE.get("store_for")
    if not store_for or not row.get("user_id"):
        return None
    return "upgraded" if _plan(store_for(row["user_id"])).get("plan") != "free" else None


def _guard_trial_ending(row: dict[str, Any]) -> str | None:
    store_for = _STATE.get("store_for")
    if not store_for or not row.get("user_id"):
        return None
    rec = _plan(store_for(row["user_id"]))
    if rec.get("status") != "trialing":
        return "trial_not_active"
    if rec.get("cancel_at_period_end"):
        return "canceled"
    return None


def _guard_pro_mcp(row: dict[str, Any]) -> str | None:
    store_for = _STATE.get("store_for")
    if not store_for or not row.get("user_id"):
        return None
    ustore = store_for(row["user_id"])
    if _plan(ustore).get("plan") != "pro":
        return "not_pro"
    try:
        from app import agents

        if agents.list_keys(ustore):
            return "mcp_connected"
    except Exception:
        pass
    return None


service.GUARDS.update({
    "upsell_dog_limit": _guard_plan_free,
    "trial_ending": _guard_trial_ending,
    "pro_mcp": _guard_pro_mcp,
})
_STATE: dict[str, Any] = {"store_for": None, "last": {}}


# ---------------------------------------------------------------- alerts --

def _alert_dedupe(uid: str, kind: str, item: Any) -> str:
    def attr(name: str) -> str:
        value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
        return "" if value is None else str(value)

    content = "|".join(attr(n) for n in ("disposition", "area_m2", "locality", "price_czk"))
    ident = hashlib.sha1(content.encode()).hexdigest()[:16] if attr("price_czk") and attr("locality") else attr("id")
    return f"alert:{uid}:{kind}:{ident}"


def send_alert(ustore: Any, item: Any, monitor_name: str = "") -> bool:
    """5.1: one e-mail per new offer (a duplicate from another portal produces the same dedupe key)."""
    user = user_for_store(ustore)
    if not user or not service.is_enabled("alert_new_listing"):
        return False

    def attr(name: str, default: str = "") -> str:
        value = item.get(name) if isinstance(item, dict) else getattr(item, name, None)
        return default if value is None else str(value).strip()

    price = attr("price_czk")
    url = attr("url")
    first_seen = _parse(attr("created_on")) or _parse(attr("first_seen"))
    minutes = max(0, int((datetime.now(timezone.utc) - first_seen).total_seconds() // 60)) if first_seen else 0
    local = first_seen.astimezone(PRAGUE) if first_seen else None
    offer_rent = "pronaj" in (attr("name") + attr("url")).lower() or (price.isdigit() and int(price) < 200000)
    plan = _plan(ustore).get("plan", "free")
    upsell = ""
    if plan == "free":
        flagged = estore.get_meta(f"alert_upsell:{user['id']}")
        last = _parse(flagged)
        if not last or datetime.now(timezone.utc) - last > timedelta(days=1):
            estore.set_meta(f"alert_upsell:{user['id']}", estore.now_iso())
            upsell = (
                '<p style="margin:16px 0 0;font:14px/1.5 Inter,Arial,sans-serif;color:#555b54;text-align:center;">Se Startem by vám tohle přišlo do 60 sekund. '
                f'<a href="{link("/nastaveni/predplatne?plan=start", "alert_upsell", "alerts")}" style="color:#163300;font-weight:600;">Vyzkoušet zdarma</a></p>'
            )
    variables = {
        "dispozice": attr("disposition"),
        "plocha": attr("area_m2"),
        "lokalita_detail": attr("locality"),
        "cena": fmt_czk(price) if price else attr("price_label"),
        "cena_suffix": "/měsíc" if offer_rent else "",
        "pes_nazev": monitor_name,
        "portal": portal_label(url),
        "minuty": str(minutes),
        "foto_url": attr("image_url"),
        "adresa": attr("locality"),
        "poplatky": "",
        "cas_zverejneni": f"{local:%-d. %-m. %H:%M}" if local else "právě teď",
        "rk_nebo_majitel": "",
        "odkaz": url,
        "odkaz_mapa": link("/nabidka", "alert", "alerts"),
        "odkaz_upravit": link("/monitory", "alert", "alerts"),
        "odkaz_pozastavit": tracking.action_url(
            user["id"], "pauza", str(attr("monitor_id") or _monitor_id_by_name(ustore, monitor_name))),
        "upsell_html": upsell,
    }
    log_id = service.send_template(
        "alert_new_listing", user=user, variables=variables, dedupe_key=_alert_dedupe(user["id"], "new", item),
        meta={"listing_id": attr("id"), "url": url, "monitor": monitor_name},
    )
    return bool(log_id)


def send_digest(ustore: Any, items: list[dict[str, Any]]) -> bool:
    user = user_for_store(ustore)
    if not user or not items or not service.is_enabled("alert_digest"):
        return False

    def price_of(it: dict[str, Any]) -> int:
        try:
            return int(it.get("price_czk") or 10**12)
        except (TypeError, ValueError):
            return 10**12

    cheapest = min(items, key=price_of)
    rows = "".join(
        '<tr><td style="padding:12px 0;border-top:1px solid #e8ebe6;font:600 15px/1.4 Inter,Arial,sans-serif;color:#163300;">'
        f"{_esc(str(it.get('disposition') or ''))} {_esc(str(it.get('area_m2') or ''))}{' m²' if it.get('area_m2') else ''}"
        f"{', ' if it.get('disposition') else ''}{_esc(str(it.get('locality') or it.get('name') or ''))}</td>"
        f'<td style="padding:12px 0 12px 12px;border-top:1px solid #e8ebe6;font:600 15px/1.4 Inter,Arial,sans-serif;color:#163300;white-space:nowrap;">'
        f"{_esc(fmt_czk(it.get('price_czk')) + ' Kč' if it.get('price_czk') else '')}</td>"
        f'<td style="padding:12px 0 12px 12px;border-top:1px solid #e8ebe6;font:14px/1.4 Inter,Arial,sans-serif;color:#555b54;">{_esc(portal_label(str(it.get("url") or "")))}</td>'
        f'<td align="right" style="padding:12px 0 12px 12px;border-top:1px solid #e8ebe6;font:600 14px/1.4 Inter,Arial,sans-serif;">'
        f'<a href="{_esc(str(it.get("url") or ""))}" style="color:#163300;">Otevřít</a></td></tr>'
        for it in items[:25]
    )
    variables = {
        "pocet": str(len(items)),
        "pes_nazev": str(cheapest.get("monitor_name") or ""),
        "dispozice_min": str(cheapest.get("disposition") or ""),
        "cena_min": fmt_czk(cheapest.get("price_czk")),
        "lokalita_min": str(cheapest.get("locality") or ""),
        "radky_html": rows,
        "odkaz": link("/nabidka", "alert_digest", "alerts"),
        "odkaz_upravit": link("/nastaveni/notifikace", "alert_digest", "alerts"),
        "odkaz_pozastavit": tracking.action_url(
            user["id"], "pauza",
            str(cheapest.get("monitor_id") or _monitor_id_by_name(ustore, str(cheapest.get("monitor_name") or "")))),
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    return bool(service.send_template("alert_digest", user=user, variables=variables, dedupe_key=f"digest:{user['id']}:{stamp}"))


def _esc(value: str) -> str:
    import html

    return html.escape(value or "", quote=True)


# ------------------------------------------------------- market / matches --

class _Ctx:
    def __init__(self, store_for: StoreFor) -> None:
        self.store_for = store_for
        self.gs = estore.global_store()
        self._owner: Any = None
        self._rows: dict[int, list[dict[str, Any]]] = {}

    @property
    def owner(self) -> Any:
        if self._owner is None:
            from app import users

            oid = users.catalog_owner_id(self.gs)
            self._owner = self.store_for(oid) if oid else False
        return self._owner or None

    def rows(self, days: int) -> list[dict[str, Any]]:
        """Active catalog rows first seen in the last ``days`` days (bounded)."""
        if days not in self._rows:
            owner = self.owner
            out: list[dict[str, Any]] = []
            if owner is not None:
                since = estore.iso_in(-days * 86400)
                try:
                    with owner.connect(readonly=True) as conn:
                        cur = conn.execute(
                            "SELECT name, price_czk, disposition, area_m2, locality, url, image_url, first_seen, lat, lon FROM listings"
                            " WHERE IFNULL(gone, 0) = 0 AND first_seen >= ? ORDER BY first_seen DESC LIMIT 20000",
                            (since,),
                        )
                        out = [dict(r) for r in cur.fetchall()]
                except Exception:
                    out = []
            self._rows[days] = out
        return self._rows[days]

    def users(self, *, verified: bool = True) -> list[dict[str, Any]]:
        from app import users

        return [u for u in users.list_users(self.gs) if u.get("email") and (u.get("email_verified") or not verified)]


def _matches(row: dict[str, Any], monitor: dict[str, Any]) -> bool:
    from app.catalog_sync import listing_matches_monitor

    try:
        return bool(listing_matches_monitor(row, monitor))
    except Exception:
        return False


def _suggestions(ctx: _Ctx, monitor: dict[str, Any]) -> dict[str, Any] | None:
    """Counts for a wider filter (price +10 %, neighbouring Prague districts). None when it cannot be computed."""
    from app import url_builder
    from app.catalog_sync import listing_matches_filters

    try:
        filters = url_builder.parse_url(str(monitor.get("search_url") or ""))
    except Exception:
        return None
    price_to = filters.get("price_to")
    if not price_to:
        return None
    rows = ctx.rows(14)
    if not rows:
        return None
    higher = dict(filters, price_to=int(int(price_to) * 1.1))
    base_hits = {id(r) for r in rows if listing_matches_filters(r, filters, ignore_source=True)}
    pocet_cena = sum(1 for r in rows if id(r) not in base_hits and listing_matches_filters(r, higher, ignore_source=True))
    out: dict[str, Any] = {"cena_navrh": int(price_to * 1.1), "pocet_cena": pocet_cena}
    districts = list(filters.get("districts") or [])
    nums = [int(d.rsplit("-", 1)[1]) for d in districts if d.startswith("praha-") and d.rsplit("-", 1)[1].isdigit()]
    extra = sorted({n for num in nums for n in _PRAHA_NEIGHBOURS.get(num, ())} - set(nums))[:2]
    if extra:
        wider = dict(filters, districts=[*districts, *[f"praha-{n}" for n in extra]])
        out["lokality_navrh"] = ", ".join(f"Praha {n}" for n in extra)
        out["pocet_lokalita"] = sum(1 for r in rows if id(r) not in base_hits and listing_matches_filters(r, wider, ignore_source=True))
    return out


def _locality_of(monitors: list[dict[str, Any]]) -> str:
    from app import url_builder

    for m in monitors:
        try:
            districts = url_builder.parse_url(str(m.get("search_url") or "")).get("districts") or []
        except Exception:
            districts = []
        for d in districts:
            if d.startswith("praha-") and d.rsplit("-", 1)[1].isdigit():
                return f"Praha {d.rsplit('-', 1)[1]}"
            if d:
                return d.replace("-", " ").title()
    return "Praha"


def _locality_stats(ctx: _Ctx, locality: str) -> dict[str, dict[str, Any]]:
    owner = ctx.owner
    out: dict[str, dict[str, Any]] = {}
    if owner is None:
        return out
    for disp in ("1+kk", "2+kk", "3+kk"):
        try:
            st = owner.catalog_stats(locality, "pronajem", disp)
        except Exception:
            continue
        if st.get("median_price") and int(st.get("active_count") or 0) >= 5:
            out[disp] = st
    return out


# -------------------------------------------------------------- scheduler --

def _due(rule: str, every: float) -> bool:
    now = time.monotonic()
    last = _STATE["last"].get(rule)
    if last is not None and now - last < every:
        return False
    _STATE["last"][rule] = now
    return True


def run_scheduler(store_for: StoreFor | None) -> None:
    """Evaluate every time-based rule. Each rule has its own cadence; failures never stop the others."""
    if store_for is None:
        return
    _STATE["store_for"] = store_for
    ctx = _Ctx(store_for)
    rules: list[tuple[str, float, Callable[[_Ctx], None]]] = [
        ("onboarding", 300, _rule_onboarding),
        ("no_matches", 1800, _rule_dog_no_matches),
        ("upsell_missed", 1800, _rule_upsell_missed),
        ("upsell_speed", 1800, _rule_upsell_speed),
        ("inactive", 3600, _rule_dog_inactive),
        ("unopened", 1800, _rule_alerts_unopened),
        ("found_home", 3600, _rule_found_home),
        ("market", 3600, _rule_market_monthly),
        ("winback", 3600, _rule_winback),
        ("resume", 600, _rule_resume),
        ("verify_retry", 600, _rule_verify_retry),
    ]
    for name, every, fn in rules:
        if not _due(name, every):
            continue
        try:
            fn(ctx)
        except Exception as exc:
            print(f"[email] rule {name}: {exc}", flush=True)


def _rule_onboarding(ctx: _Ctx) -> None:
    since = estore.iso_in(-14 * 86400)
    with estore.db() as conn:
        rows = conn.execute("SELECT key, value FROM email_meta WHERE key LIKE 'verified:%' AND value >= ?", (since,)).fetchall()
    now = datetime.now(timezone.utc)
    for row in rows:
        uid = row["key"].split(":", 1)[1]
        user = get_user(uid)
        verified_at = _parse(row["value"])
        if not user or not verified_at:
            continue
        ustore = ctx.store_for(uid)
        monitors = _monitors(ustore)
        age = now - verified_at
        if monitors:
            if estore.get_meta(f"first_dog:{uid}") is None and service.is_enabled("first_dog_confirm"):
                first = monitors[0]
                plan = _plan(ustore).get("plan", "free")
                portals = "Sreality, Bezrealitky, Reality.iDNES, Bazoš" if plan == "free" else (
                    "Sreality, Bezrealitky, Reality.iDNES, Bazoš, ČeskéReality, Annonce, M&M Reality, RE/MAX, Reality.cz")
                if service.send_template(
                    "first_dog_confirm", user=user, dedupe_key=f"first_dog:{uid}",
                    variables={"pes_nazev": describe_monitor(first), "pes_filtr": describe_monitor(first), "portaly": portals,
                               "rychlost": _SPEED[plan][1], "odkaz": link("/nabidka", "first_dog_confirm")},
                ) is not None:
                    estore.set_meta(f"first_dog:{uid}", estore.now_iso())
            continue
        if age >= timedelta(days=1) and age < timedelta(days=3):
            locality = "Praha"
            new = len([r for r in ctx.rows(1) if locality.lower() in str(r.get("locality") or "").lower()])
            if new:
                _send("nudge_no_dog", user, {"lokalita": locality, "pocet": str(new), "pes_filtr": f"pronájem bytů, {locality}",
                                             "odkaz": link("/monitory", "nudge_no_dog")}, f"nudge_no_dog:{uid}")
        elif age >= timedelta(days=3):
            locality = "Praha"
            stats = _locality_stats(ctx, locality)
            if len(stats) == 3:
                total = sum(int(s.get("active_count") or 0) for s in stats.values())
                _send("nudge_last_rent_index", user, {
                    "lokalita": locality, "pocet": fmt_czk(total),
                    "cena_1kk": fmt_czk(stats["1+kk"]["median_price"]), "cena_2kk": fmt_czk(stats["2+kk"]["median_price"]),
                    "cena_3kk": fmt_czk(stats["3+kk"]["median_price"]), "odkaz": link("/index", "nudge_last_rent_index")},
                    f"nudge_rent_index:{uid}")


def _rule_dog_no_matches(ctx: _Ctx) -> None:
    cutoff_new = datetime.now(timezone.utc) - timedelta(days=60)
    for user in ctx.users():
        created = _parse(user.get("created_at"))
        if not created or created < cutoff_new:
            continue
        uid = user["id"]
        ustore = ctx.store_for(uid)
        for m in _monitors(ustore):
            made = _parse(m.get("created_at"))
            if not made or not m.get("enabled") or datetime.now(timezone.utc) - made < timedelta(days=7):
                continue
            if int(m.get("tracked") or 0) > 0 or estore.get_meta(f"no_matches:{m['id']}"):
                continue
            try:
                hits = ustore.count(m["id"])
            except Exception:
                hits = 0
            if hits:
                continue
            sug = _suggestions(ctx, m)
            if not sug or not sug.get("pocet_cena") or not sug.get("lokality_navrh") or not sug.get("pocet_lokalita"):
                continue
            sent = _send("dog_no_matches", user, {
                "pes_nazev": describe_monitor(m), "pocet": "0", "cena_navrh": fmt_czk(sug["cena_navrh"]),
                "pocet_cena": str(sug["pocet_cena"]), "lokality_navrh": sug["lokality_navrh"],
                "pocet_lokalita": str(sug["pocet_lokalita"]), "odkaz": link("/monitory", "dog_no_matches")},
                f"dog_no_matches:{m['id']}")
            if sent:
                estore.set_meta(f"no_matches:{m['id']}", estore.now_iso())


def _free_eligible(ctx: _Ctx, user: dict[str, Any]) -> tuple[Any, dict[str, Any]] | None:
    ustore = ctx.store_for(user["id"])
    state = _plan(ustore)
    if state.get("plan") != "free" or state.get("subscription_id") or state.get("customer_id"):
        return None
    if _recent(user["id"], ("trial_started",), 3650):
        return None
    return ustore, state


def _rule_upsell_missed(ctx: _Ctx) -> None:
    now = datetime.now(PRAGUE)
    if now.weekday() != 0 or now.hour < 8:
        return
    week = now.strftime("%G-W%V")
    rows = ctx.rows(7)
    if not rows:
        return
    for user in ctx.users():
        uid = user["id"]
        if estore.get_meta(f"upsell_missed:{uid}:{week}") is not None:
            continue
        got = _free_eligible(ctx, user)
        if not got or _recent(uid, ("upsell_missed_offers", "upsell_dog_limit", "upsell_speed"), 7):
            continue
        # 4 in a row, then a 30 day pause
        with estore.db() as conn:
            last4 = conn.execute(
                "SELECT created_at FROM email_log WHERE user_id = ? AND template_key = 'upsell_missed_offers' AND status = 'sent'"
                " ORDER BY id DESC LIMIT 4", (uid,)).fetchall()
        if len(last4) == 4 and (_parse(last4[0]["created_at"]) or now) > datetime.now(timezone.utc) - timedelta(days=30):
            continue
        ustore = got[0]
        best: tuple[int, dict[str, Any], list[dict[str, Any]]] | None = None
        for m in _monitors(ustore):
            if not m.get("enabled"):
                continue
            hit = [r for r in rows if _portal_id(r["url"]) not in FREE_PORTALS and _matches(r, {**m, "portals": "all"})]
            if len(hit) >= 3 and (best is None or len(hit) > best[0]):
                best = (len(hit), m, hit)
        if not best:
            continue
        count, monitor, hit = best
        listing_rows = "".join(
            '<tr><td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 15px/1.4 Inter,Arial,sans-serif;color:#163300;">'
            f'{_esc(str(r.get("disposition") or ""))}{", " if r.get("disposition") else ""}{_esc(str(r.get("locality") or ""))}<br>'
            f'<span style="font-weight:400;color:#555b54;">{_esc(fmt_czk(r.get("price_czk")))} Kč · {_esc(portal_label(r["url"]))}</span></td></tr>'
            for r in hit[:3]
        )
        if _send("upsell_missed_offers", user, {
            "pes_nazev": describe_monitor(monitor), "pocet": str(count), "nabidky_html": listing_rows,
            "odkaz": link("/nastaveni/predplatne?plan=start", "upsell_missed_offers")}, f"upsell_missed:{uid}:{week}"):
            estore.set_meta(f"upsell_missed:{uid}:{week}", "1")


def _rule_upsell_speed(ctx: _Ctx) -> None:
    with estore.db() as conn:
        rows = conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM email_log WHERE template_key = 'alert_new_listing' AND status = 'sent'"
            " GROUP BY user_id HAVING n >= 10").fetchall()
    for row in rows:
        uid = row["user_id"]
        user = get_user(uid) if uid else None
        if not user or estore.get_meta(f"upsell_speed:{uid}") is not None:
            continue
        got = _free_eligible(ctx, user)
        if not got or _recent(uid, ("upsell_missed_offers", "upsell_dog_limit", "upsell_speed"), 7):
            continue
        try:
            with got[0].connect(readonly=True) as conn:
                gone = int(conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'sold'").fetchone()[0])
        except Exception:
            continue
        if gone < 1:
            continue
        if _send("upsell_speed", user, {"pocet": str(row["n"]), "pocet_stazeno": str(min(gone, row["n"])),
                                        "odkaz": link("/nastaveni/predplatne", "upsell_speed")}, f"upsell_speed:{uid}"):
            estore.set_meta(f"upsell_speed:{uid}", estore.now_iso())


def _rule_dog_inactive(ctx: _Ctx) -> None:
    limit = datetime.now(timezone.utc) - timedelta(days=14)
    for user in ctx.users():
        uid = user["id"]
        ustore = ctx.store_for(uid)
        for m in _monitors(ustore):
            if not m.get("enabled"):
                continue
            try:
                with ustore.connect(readonly=True) as conn:
                    last = conn.execute(
                        "SELECT MAX(created_at) FROM events WHERE monitor_id = ? AND kind IN ('new', 'changed')", (m["id"],)
                    ).fetchone()[0]
            except Exception:
                continue
            last_dt = _parse(last)
            if not last_dt or last_dt > limit:
                continue
            prev = _parse(estore.get_meta(f"dog_inactive:{m['id']}"))
            if prev and datetime.now(timezone.utc) - prev < timedelta(days=30):
                continue
            sug = _suggestions(ctx, m)
            if not sug or not sug.get("pocet_cena"):
                continue
            if _send("dog_inactive", user, {
                "pes_nazev": describe_monitor(m), "pocet_cena": str(sug["pocet_cena"]),
                "odkaz": link("/monitory", "dog_inactive", "alerts"),
                "odkaz_pozastavit": tracking.action_url(uid, "pauza", str(m["id"]))},
                    f"dog_inactive:{m['id']}:{datetime.now(timezone.utc):%Y%m}"):
                estore.set_meta(f"dog_inactive:{m['id']}", estore.now_iso())


def _pause_monitors(ustore: Any, monitor_id: str = "") -> list[dict[str, Any]]:
    """Switch dogs off (one dog when ``monitor_id`` is given); returns the dogs that were running."""
    with ustore.connect() as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT id, name FROM monitors WHERE enabled = 1" + (" AND id = ?" if monitor_id else "")
        rows = [dict(r) for r in conn.execute(sql, (monitor_id,) if monitor_id else ()).fetchall()]
        for r in rows:
            conn.execute("UPDATE monitors SET enabled = 0 WHERE id = ?", (r["id"],))
    return rows


def _monitor_id_by_name(ustore: Any, name: str) -> str:
    if not name:
        return ""
    try:
        for m in ustore.list_monitors_light():
            if str(m.get("name") or "").strip() == name.strip():
                return str(m.get("id") or "")
    except Exception:
        pass
    return ""


def _rule_alerts_unopened(ctx: _Ctx) -> None:
    with estore.db() as conn:
        uids = [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM email_log WHERE template_key = 'alert_new_listing' AND user_id IS NOT NULL"
            " GROUP BY user_id HAVING COUNT(*) >= 10").fetchall()]
    for uid in uids:
        last = estore.recent_alerts(uid, 10)
        if any(r["opened_at"] for r in last):
            estore.set_meta(f"unopened_notice:{uid}", "")
            continue
        if len(last) < 10 or any(r["status"] == "failed" for r in last):
            continue  # a failed delivery (bounce) means the address is bad: do not keep mailing it
        user = get_user(uid)
        if not user:
            continue
        notice = _parse(estore.get_meta(f"unopened_notice:{uid}"))
        if not notice:
            if _send("alerts_unopened", user, {"odkaz": link("/nastaveni/notifikace", "alerts_unopened", "alerts")},
                     f"alerts_unopened:{uid}:{last[0]['id']}"):
                estore.set_meta(f"unopened_notice:{uid}", estore.now_iso())
        elif datetime.now(timezone.utc) - notice >= timedelta(days=7) and not _recent_open(uid, notice):
            # nobody reads them: stop the dogs to protect the sender reputation
            paused = _pause_monitors(ctx.store_for(uid))
            if paused:
                estore.set_meta(f"auto_paused:{uid}", estore.now_iso())
                names = [describe_monitor(m) for m in paused]
                _send("dog_auto_paused", user, {
                    "pes_nazev": names[0] if len(names) == 1 else f"{names[0]} a další ({len(names) - 1})",
                    "odkaz": tracking.action_url(uid, "zapnout")},
                      f"dog_auto_paused:{uid}:{estore.now_iso()[:10]}")
            estore.set_meta(f"unopened_notice:{uid}", estore.now_iso())


def _recent_open(uid: str, since: datetime) -> bool:
    with estore.db() as conn:
        row = conn.execute("SELECT 1 FROM email_log WHERE user_id = ? AND opened_at >= ? LIMIT 1", (uid, since.isoformat(timespec="seconds"))).fetchone()
    return row is not None


def _rule_found_home(ctx: _Ctx) -> None:
    now = datetime.now(timezone.utc)
    for user in ctx.users():
        uid = user["id"]
        if estore.get_meta(f"found_exit:{uid}"):
            continue
        ustore = ctx.store_for(uid)
        monitors = _monitors(ustore)
        stamps = [d for d in (_parse(m.get("created_at")) for m in monitors) if d]
        if not stamps or not any(m.get("enabled") for m in monitors):
            continue
        days = (now - min(stamps)).days
        step = 60 if days >= 60 else 30 if days >= 30 else 0
        if not step:
            continue
        with estore.db() as conn:
            alerts = conn.execute("SELECT COUNT(*) FROM email_log WHERE user_id = ? AND template_key = 'alert_new_listing' AND status = 'sent'", (uid,)).fetchone()[0]
        if not alerts:
            continue
        _send("found_home", user, {
            "pocet_dni": str(days), "pocet": str(alerts), "odkaz": tracking.action_url(uid, "found"),
            "odkaz_hledam": tracking.action_url(uid, "hledam"), "odkaz_pozastavit": tracking.action_url(uid, "pauza")},
              f"found_home:{uid}:{step}")


def _rule_market_monthly(ctx: _Ctx) -> None:
    now = datetime.now(PRAGUE)
    first = now.replace(day=1)
    # first working day of the month, from 8:00
    day = first
    while day.weekday() >= 5:
        day += timedelta(days=1)
    if now.date() != day.date() or now.hour < 8:
        return
    month = now.strftime("%Y-%m")
    prev_month = (first - timedelta(days=1))
    prev_key = prev_month.strftime("%Y-%m")
    snapshots: dict[str, dict[str, Any]] = {}
    for user in ctx.users():
        uid = user["id"]
        if estore.get_meta(f"market_sent:{uid}:{month}") is not None:
            continue
        locality = _locality_of(_monitors(ctx.store_for(uid)))
        if locality not in snapshots:
            stats = _locality_stats(ctx, locality)
            snapshots[locality] = stats
            for disp, st in stats.items():
                estore.set_meta(f"market_snap:{locality}:{disp}:{month}", str(st["median_price"]))
        stats = snapshots[locality]
        if len(stats) < 3:
            continue
        changes: dict[str, str] = {}
        ok = True
        for disp, st in stats.items():
            old = estore.get_meta(f"market_snap:{locality}:{disp}:{prev_key}")
            if not old:
                ok = False
                break
            pct = (float(st["median_price"]) / float(old) - 1) * 100
            changes[disp] = f"{pct:+.1f} %".replace(".", ",").replace("-", "−")
        if not ok:
            continue  # no last-month snapshot yet: the first month only records the data
        since = estore.iso_in(-30 * 86400)
        new_count = len([r for r in ctx.rows(30) if locality.split()[0].lower() in str(r.get("locality") or "").lower()])
        life = _listing_lifetime_days(ctx, since)
        if not new_count or life is None:
            continue
        avg = sum(float(stats[d]["median_price"]) / float(estore.get_meta(f"market_snap:{locality}:{d}:{prev_key}")) for d in stats) / 3
        pct_all = f"{(avg - 1) * 100:+.1f} %".replace(".", ",").replace("-", "−")
        _send("market_monthly", user, {
            "lokalita": locality, "mesic": _MONTHS[prev_month.month - 1], "zmena_procenta": pct_all,
            "cena_1kk": fmt_czk(stats["1+kk"]["median_price"]), "cena_2kk": fmt_czk(stats["2+kk"]["median_price"]),
            "cena_3kk": fmt_czk(stats["3+kk"]["median_price"]), "zmena_1kk": changes["1+kk"], "zmena_2kk": changes["2+kk"],
            "zmena_3kk": changes["3+kk"], "pocet_novych": fmt_czk(new_count), "dny_zivotnost": str(life),
            "odkaz": link("/index", "market_monthly")}, f"market:{uid}:{month}")
        estore.set_meta(f"market_sent:{uid}:{month}", "1")


def _listing_lifetime_days(ctx: _Ctx, since: str) -> int | None:
    owner = ctx.owner
    if owner is None:
        return None
    try:
        with owner.connect(readonly=True) as conn:
            rows = conn.execute(
                "SELECT first_seen, last_seen FROM listings WHERE gone = 1 AND last_seen >= ? AND first_seen IS NOT NULL LIMIT 20000", (since,)
            ).fetchall()
    except Exception:
        return None
    spans = sorted(
        (b - a).total_seconds() / 86400 for a, b in ((_parse(r[0]), _parse(r[1])) for r in rows) if a and b and b >= a
    )
    return int(round(spans[len(spans) // 2])) if len(spans) >= 20 else None


def _rule_winback(ctx: _Ctx) -> None:
    now = datetime.now(timezone.utc)
    with estore.db() as conn:
        rows = conn.execute("SELECT key, value FROM email_meta WHERE key LIKE 'paid_ended:%' AND key NOT LIKE 'paid_ended:%:%'").fetchall()
    for row in rows:
        uid = row["key"].split(":", 1)[1]
        ended = _parse(row["value"])
        user = get_user(uid)
        if not ended or not user:
            continue
        days = (now - ended).days
        step = 90 if days >= 90 else 30 if days >= 30 else 0
        if not step or days > 120:
            continue
        reason = estore.get_meta(f"cancel_reason:{uid}") or ""
        if reason == "duvod_bydleni" or estore.get_meta(f"found_exit:{uid}") == "found":
            continue
        ustore = ctx.store_for(uid)
        if _plan(ustore).get("plan") != "free":
            continue
        monitors = _monitors(ustore)
        if not monitors:
            continue
        mon = monitors[0]
        locality = _locality_of(monitors)
        count = len([r for r in ctx.rows(max(days, 1)) if locality.split()[0].lower() in str(r.get("locality") or "").lower()
                     and _matches(r, {**mon, "portals": "all"})])
        if not count:
            continue
        _send("winback", user, {"lokalita": locality, "pocet": str(count), "pes_nazev": describe_monitor(mon),
                                "odkaz": link("/monitory", "winback")}, f"winback:{uid}:{step}")


def _rule_verify_retry(ctx: _Ctx) -> None:
    """1.1: one more confirmation mail when the address is still unconfirmed after 24 hours."""
    from app import users

    now = datetime.now(timezone.utc)
    for user in ctx.users(verified=False):
        created = _parse(user.get("created_at"))
        if user.get("email_verified") or not created or not (timedelta(hours=24) <= now - created <= timedelta(days=7)):
            continue
        uid = user["id"]
        if estore.get_meta(f"verify_retry:{uid}") is not None:
            continue
        token = users.create_email_verification(ctx.gs, uid)
        estore.set_meta(f"verify_retry:{uid}", estore.now_iso())
        service.send_template("verify_email", user=user, variables={"odkaz": f"{tracking.base_url()}/overeni?token={token}"},
                              dedupe_key=f"verify_retry:{uid}", immediate=True)


def _rule_resume(ctx: _Ctx) -> None:
    """Dogs paused for a month from an e-mail link are switched on again."""
    now = estore.now_iso()
    with estore.db() as conn:
        rows = conn.execute("SELECT key, value FROM email_meta WHERE key LIKE 'resume:%' AND value <= ? AND value != ''", (now,)).fetchall()
    for row in rows:
        uid = row["key"].split(":", 1)[1]
        try:
            from app import billing

            ustore = ctx.store_for(uid)
            with ustore.connect() as conn:
                conn.execute("UPDATE monitors SET enabled = 1 WHERE enabled = 0")
            billing.apply_watch_limit(ustore)
        except Exception as exc:
            print(f"[email] resume {uid}: {exc}", flush=True)
        estore.set_meta(row["key"], "")
    with estore.db() as conn:
        dogs = conn.execute("SELECT key FROM email_meta WHERE key LIKE 'resume_dog:%' AND value <= ? AND value != ''", (now,)).fetchall()
    for row in dogs:
        _, uid, mid = row["key"].split(":", 2)
        try:
            from app import billing

            ustore = ctx.store_for(uid)
            with ustore.connect() as conn:
                conn.execute("UPDATE monitors SET enabled = 1 WHERE id = ?", (mid,))
            billing.apply_watch_limit(ustore)
        except Exception as exc:
            print(f"[email] resume dog {uid}/{mid}: {exc}", flush=True)
        estore.set_meta(row["key"], "")


# ----------------------------------------------------------- one-click --

def handle_action(user_id: str, action: str, store_for: StoreFor, monitor_id: str = "") -> str:
    """Link actions from e-mails (6.1 choices, cancellation reason). Returns the confirmation message."""
    user = get_user(user_id)
    if not user:
        return "Odkaz už neplatí."
    ustore = store_for(user_id)
    if action.startswith("duvod_"):
        estore.set_meta(f"cancel_reason:{user_id}", action)
        return "Děkujeme za odpověď, pomůže nám to zlepšit Realitify."
    if action == "zapnout":
        from app import billing

        with ustore.connect() as conn:
            conn.execute("UPDATE monitors SET enabled = 1 WHERE enabled = 0")
        billing.apply_watch_limit(ustore)
        estore.set_meta(f"unopened_notice:{user_id}", "")
        return "Hlídání jsme znovu zapnuli."
    if action == "pauza" and monitor_id:
        # one dog from an alert: only that dog is paused, the others keep running
        if _pause_monitors(ustore, monitor_id):
            estore.set_meta(f"resume_dog:{user_id}:{monitor_id}", estore.iso_in(30 * 86400))
        return "Tohoto hlídacího psa jsme pozastavili na měsíc. Pak ho samo znovu zapneme."
    estore.set_meta(f"found_exit:{user_id}", "found" if action == "found" else "1")
    if action == "hledam":
        return "Díky, hlídáme dál a držíme palce."
    _pause_monitors(ustore)
    if action == "pauza":
        estore.set_meta(f"resume:{user_id}", estore.iso_in(30 * 86400))
        return "Hlídání jsme pozastavili na měsíc. Pak ho samo znovu zapneme."
    story_reward = config.EMAIL_STORY_REWARD
    _send("found_home_followup", user, {
        "odmena": story_reward, "odkaz": link("/uspechy", "found_home_followup"),
        "odkaz_predplatne": link("/nastaveni/predplatne", "found_home_followup")}, f"found_followup:{user_id}")
    return "Gratulujeme! Hlídací psy jsme pozastavili."


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
