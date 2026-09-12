from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import calendar
import json
import re
import time

import stripe

from app import config
from app.store import Store

PLANS: dict[str, dict[str, Any]] = {
    "free": {
        "id": "free",
        "label": "Zdarma",
        "price_czk": 0,
        "watch_limit": 1,
        "features": [
            "1 hlídací pes",
            "4 hlavní portály (Sreality, Bezrealitky, iDNES, Bazoš)",
            "Notifikace do 5 minut",
            "Historie cen 30 dní, základní filtry",
            "Podpora e-mailem",
        ],
    },
    "start": {
        "id": "start",
        "label": "Start",
        "price_czk": 149,
        "watch_limit": 10,
        "lookup_key": "realitify_start_month",
        "product_name": "Realitify Start",
        "trial_days": 3,
        "features": [
            "10 hlídacích psů",
            "12+ portálů, notifikace do 60 s",
            "E-mail, historie 1 rok, CSV 1× měsíčně",
            "Pokročilé filtry (RK, klíčová slova)",
            "3 dny zdarma, prioritní e-mail",
        ],
    },
    "pro": {
        "id": "pro",
        "label": "PRO",
        "price_czk": 349,
        "watch_limit": None,
        "lookup_key": "realitify_pro_month",
        "product_name": "Realitify PRO",
        "trial_days": 3,
        "features": [
            "Neomezeně hlídacích psů",
            "20+ portálů + dražby, notifikace do 30 s",
            "Email + Push + Discord (10 SMS/měs)",
            "AI filtry a MCP (ChatGPT, Claude, Grok)",
            "Telefon + chat, Excel/CSV neomezeně",
        ],
    },
    "individual": {
        "id": "individual",
        "label": "INDIVIDUAL",
        "price_czk": None,
        "watch_limit": None,
        "features": [
            "Neomezeně + API a MCP",
            "Všechny portály + vlastní zdroje",
            "Webhook do 30 s",
            "Export a filtry na míru",
            "Osobní manažer",
        ],
    },
}

PAID_PLANS = ("start", "pro")
PLAN_RANK = {"free": 0, "start": 1, "pro": 2, "individual": 3}
LIVE_SUB_STATUSES = frozenset({"active", "trialing", "past_due", "unpaid", "incomplete", "paused"})
DOWNGRADE_LOSSES: dict[tuple[str, str], list[str]] = {
    ("pro", "start"): [
        "Neomezený počet hlídacích psů — Start má nejvýš 10 aktivních",
        "20+ portálů a dražby",
        "Notifikace do 30 s",
        "Push + Discord SMS balíček",
        "AI filtry a MCP (ChatGPT, Claude, Grok)",
        "Telefon + chat a neomezený Excel/CSV",
    ],
    ("pro", "free"): [
        "Všechny placené funkce tarifu PRO",
        "Neomezené hlídací psy — Zdarma má 1 aktivní",
        "20+ portálů, dražby, AI a MCP",
        "Rychlé notifikace a extra kanály",
    ],
    ("start", "free"): [
        "Až 10 hlídacích psů — Zdarma má 1 aktivní",
        "12+ portálů a notifikace do 60 s",
        "Historie 1 rok a CSV export",
        "Pokročilé filtry a prioritní e-mail",
    ],
}


def _configure() -> None:
    if not config.STRIPE_SECRET_KEY:
        raise RuntimeError("Chybí STRIPE_SECRET_KEY v .env")
    stripe.api_key = config.STRIPE_SECRET_KEY


def empty_billing() -> dict[str, Any]:
    return {
        "plan": "free",
        "status": "active",
        "customer_id": "",
        "subscription_id": "",
        "price_id": "",
        "email": "",
        "current_period_end": None,
        "cancel_at_period_end": False,
        "payment": None,
        "pending_plan": "",
        "pending_at": "",
        "schedule_id": "",
    }


def billing_state(store: Store) -> dict[str, Any]:
    data = {**empty_billing(), **(store.billing_record() or {})}
    plan = data.get("plan") if data.get("plan") in PLANS else "free"
    data["plan"] = plan
    catalog = PLANS[plan]
    data["label"] = catalog["label"]
    data["price_czk"] = catalog["price_czk"]
    data["watch_limit"] = catalog["watch_limit"]
    data["features"] = catalog["features"]
    data["plans"] = [
        {
            "id": item["id"],
            "label": item["label"],
            "price_czk": item["price_czk"],
            "watch_limit": item["watch_limit"],
            "features": item["features"],
            "checkout": item["id"] in PAID_PLANS,
        }
        for item in PLANS.values()
    ]
    data["publishable_key"] = config.STRIPE_PUBLISHABLE_KEY
    data["configured"] = bool(config.STRIPE_SECRET_KEY)
    data["plan_rank"] = PLAN_RANK
    data["whatsapp"] = PLAN_RANK.get(plan, 0) >= PLAN_RANK["pro"]
    data["mcp"] = PLAN_RANK.get(plan, 0) >= PLAN_RANK["pro"]
    data["downgrade_losses"] = {f"{src}_{dst}": rows for (src, dst), rows in DOWNGRADE_LOSSES.items()}
    pending = data.get("pending_plan") if data.get("pending_plan") in PLANS and data.get("pending_plan") != plan else ""
    data["pending_plan"] = pending
    data["pending_label"] = PLANS[pending]["label"] if pending else ""
    data["pending_at"] = data.get("pending_at") or None
    data["stripe_plan"] = plan
    comp_plan = data.get("comp_plan") if data.get("comp_plan") in PLANS else ""
    data["comp"] = bool(comp_plan)
    if comp_plan:
        plan = comp_plan
        catalog = PLANS[plan]
        data["plan"] = plan
        data["label"] = catalog["label"]
        data["price_czk"] = catalog["price_czk"]
        data["watch_limit"] = catalog["watch_limit"]
        data["features"] = catalog["features"]
        data["whatsapp"] = PLAN_RANK.get(plan, 0) >= PLAN_RANK["pro"]
        data["mcp"] = PLAN_RANK.get(plan, 0) >= PLAN_RANK["pro"]
    data["pending_promo_code"] = str(data.get("pending_promo_code") or "").strip().upper()
    data["first_order"] = plan == "free" and not data.get("subscription_id")
    return data


def watch_limit_for(store: Store) -> int | None:
    record = store.billing_record() or {}
    plan = record.get("comp_plan") if record.get("comp_plan") in PLANS else (record.get("plan") or "free")
    return PLANS.get(plan, PLANS["free"])["watch_limit"]


def apply_watch_limit(store: Store) -> list[str]:
    return store.disable_monitors_over_limit(watch_limit_for(store))


def settle_pending_if_due(store: Store) -> None:
    record = store.billing_record() or {}
    pending_at = record.get("pending_at") or ""
    if not pending_at or not config.STRIPE_SECRET_KEY or not record.get("customer_id"):
        return
    try:
        when = datetime.fromisoformat(str(pending_at).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < when:
            return
    except Exception:
        return
    try:
        recover_from_stripe(store)
        apply_watch_limit(store)
    except Exception:
        pass


def _dt(ts: int | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def _ensure_price(plan_id: str) -> str:
    spec = PLANS[plan_id]
    found = stripe.Price.list(lookup_keys=[spec["lookup_key"]], active=True, limit=1)
    if found.data:
        product_id = found.data[0].product if isinstance(found.data[0].product, str) else found.data[0].product.id
        try:
            stripe.Product.modify(product_id, tax_code="txcd_10103001")
        except Exception:
            pass
        return found.data[0].id
    product = stripe.Product.create(
        name=spec["product_name"],
        metadata={"plan": plan_id},
        tax_code="txcd_10103001",
    )
    price = stripe.Price.create(
        product=product.id,
        currency="czk",
        unit_amount=int(spec["price_czk"]) * 100,
        recurring={"interval": "month"},
        lookup_key=spec["lookup_key"],
        transfer_lookup_key=True,
        metadata={"plan": plan_id},
    )
    return price.id


def _sget(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    try:
        return obj[key]
    except Exception:
        return default


def _sid(obj: Any) -> str:
    if not obj:
        return ""
    if isinstance(obj, str):
        return obj
    return str(_sget(obj, "id") or "")


def _plan_from_price(price_id: str | None) -> str:
    if not price_id:
        return "free"
    try:
        price = stripe.Price.retrieve(price_id)
    except Exception:
        return "free"
    key = _sget(price, "lookup_key") or ""
    meta = _sget(price, "metadata") or {}
    plan_meta = _sget(meta, "plan") if not isinstance(meta, dict) else meta.get("plan")
    for plan_id, spec in PLANS.items():
        if spec.get("lookup_key") == key or plan_meta == plan_id:
            return plan_id
    return "free"


def _card_from_customer(customer_id: str) -> dict[str, str] | None:
    try:
        methods = stripe.PaymentMethod.list(customer=customer_id, type="card", limit=1)
    except Exception:
        return None
    if not methods.data:
        return None
    card = methods.data[0].card
    if not card:
        return None
    return {
        "brand": (card.brand or "card").upper(),
        "last4": card.last4 or "",
        "exp": f"{int(card.exp_month):02d}/{card.exp_year}",
    }


def sync_subscription(store: Store, subscription: Any, customer_id: str | None = None, plan_hint: str = "") -> dict[str, Any]:
    sub_id = subscription if isinstance(subscription, str) else _sid(subscription)
    subscription = stripe.Subscription.retrieve(sub_id, expand=["items.data.price"])
    status = _sget(subscription, "status") or ""
    items = _sget(_sget(subscription, "items"), "data") or []
    price = _sget(items[0], "price") if items else None
    price_id = _sid(price)
    customer = customer_id or _sid(_sget(subscription, "customer"))
    period_end = _sget(subscription, "current_period_end") or (items and _sget(items[0], "current_period_end"))
    cancel_at = bool(_sget(subscription, "cancel_at_period_end"))
    active = status in {"active", "trialing", "past_due"}
    meta = _sget(subscription, "metadata") or {}
    hinted = plan_hint or (meta.get("plan") if isinstance(meta, dict) else _sget(meta, "plan")) or ""
    from_price = _plan_from_price(price_id)
    if from_price in PAID_PLANS:
        plan = from_price
    elif hinted in PAID_PLANS:
        plan = hinted
    else:
        plan = "free"
    if not active:
        plan = "free"
        price_id = ""
    pending_plan = ""
    pending_at = ""
    schedule_id = _sid(_sget(subscription, "schedule")) if active else ""
    if active and cancel_at:
        pending_plan = "free"
        pending_at = _dt(period_end) or ""
    elif active and schedule_id:
        pending_plan, pending_at = _pending_from_schedule(schedule_id)
    record = {
        **(store.billing_record() or {}),
        "plan": plan if active else "free",
        "status": status,
        "customer_id": customer,
        "subscription_id": _sid(subscription) if active else "",
        "price_id": price_id if active else "",
        "current_period_end": _dt(period_end),
        "cancel_at_period_end": cancel_at,
        "pending_plan": pending_plan or "",
        "pending_at": pending_at or "",
        "schedule_id": schedule_id or "",
        "payment": _card_from_customer(customer) if customer else None,
    }
    store.save_billing_record(record)
    apply_watch_limit(store)
    return billing_state(store)


def sync_checkout_session(store: Store, session_id: str) -> dict[str, Any]:
    _configure()
    session = stripe.checkout.Session.retrieve(session_id, expand=["subscription", "subscription.items.data.price", "customer"])
    customer_id = _sid(_sget(session, "customer"))
    details = _sget(session, "customer_details")
    email = _sget(details, "email") or ""
    record = store.billing_record() or {}
    if email:
        record["email"] = email
    if customer_id:
        record["customer_id"] = customer_id
    store.save_billing_record(record)
    sub = _sget(session, "subscription")
    hint = _sget(_sget(session, "metadata"), "plan") or ""
    if hint not in PAID_PLANS:
        hint = ""
    if sub:
        keep_id = sub if isinstance(sub, str) else _sid(sub)
        if customer_id:
            _enforce_single_subscription(customer_id, keep_id)
        return sync_subscription(store, sub, customer_id, plan_hint=str(hint))
    return recover_from_stripe(store)


def recover_from_stripe(store: Store) -> dict[str, Any]:
    _configure()
    record = store.billing_record() or {}
    customer_id = record.get("customer_id") or ""
    if not customer_id:
        return billing_state(store)
    kept = _enforce_single_subscription(customer_id, record.get("subscription_id") or "")
    if kept:
        return sync_subscription(store, kept, customer_id)
    return billing_state(store)


def _live_subscriptions(customer_id: str) -> list[Any]:
    if not customer_id:
        return []
    found = stripe.Subscription.list(customer=customer_id, status="all", limit=20)
    live = [sub for sub in found.data if _sget(sub, "status") in LIVE_SUB_STATUSES]
    live.sort(key=lambda sub: int(_sget(sub, "created") or 0), reverse=True)
    return live


def _cancel_extra_subscription(sub_id: str) -> None:
    if not sub_id:
        return
    try:
        stripe.Subscription.cancel(sub_id)
    except Exception:
        try:
            stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
        except Exception:
            pass


def _enforce_single_subscription(customer_id: str, keep_id: str = "") -> Any | None:
    live = _live_subscriptions(customer_id)
    if not live:
        return None
    keep = next((sub for sub in live if _sid(sub) == keep_id), None) or live[0]
    keep_sid = _sid(keep)
    for sub in live:
        extra_id = _sid(sub)
        if extra_id and extra_id != keep_sid:
            _cancel_extra_subscription(extra_id)
    return keep


def _expire_open_checkouts(customer_id: str) -> None:
    if not customer_id:
        return
    sessions = stripe.checkout.Session.list(customer=customer_id, status="open", limit=10)
    for session in sessions.data:
        try:
            stripe.checkout.Session.expire(session.id)
        except Exception:
            pass


def _plan_of_subscription(subscription: Any) -> str:
    items = _sget(_sget(subscription, "items"), "data") or []
    price = _sget(items[0], "price") if items else None
    from_price = _plan_from_price(price if isinstance(price, str) else _sid(price))
    if from_price in PAID_PLANS:
        return from_price
    meta = _sget(subscription, "metadata") or {}
    hinted = meta.get("plan") if isinstance(meta, dict) else _sget(meta, "plan")
    if hinted in PAID_PLANS:
        return str(hinted)
    return "free"


def _item_price_id(item: Any) -> str:
    price = _sget(item, "price")
    return price if isinstance(price, str) else _sid(price)


def _period_end(subscription: Any) -> int | None:
    items = _sget(_sget(subscription, "items"), "data") or []
    return _sget(subscription, "current_period_end") or (items and _sget(items[0], "current_period_end")) or _sget(subscription, "trial_end")


def _period_start(subscription: Any) -> int | None:
    items = _sget(_sget(subscription, "items"), "data") or []
    return _sget(subscription, "current_period_start") or (items and _sget(items[0], "current_period_start"))


def _release_schedule(subscription: Any) -> None:
    sched_id = _sid(_sget(subscription, "schedule"))
    if not sched_id:
        return
    try:
        stripe.SubscriptionSchedule.release(sched_id)
    except Exception:
        try:
            stripe.SubscriptionSchedule.cancel(sched_id)
        except Exception:
            pass


def _pending_from_schedule(schedule_id: str) -> tuple[str, str]:
    try:
        sched = stripe.SubscriptionSchedule.retrieve(schedule_id)
    except Exception:
        return "", ""
    phases = list(_sget(sched, "phases") or [])
    if len(phases) < 2:
        return "", ""
    nxt = phases[-1]
    items = _sget(nxt, "items") or []
    price = _sget(items[0], "price") if items else None
    plan = _plan_from_price(price if isinstance(price, str) else _sid(price))
    start = _sget(nxt, "start_date")
    return (plan if plan in PLANS else ""), (_dt(start) or "")


def _schedule_downgrade(subscription: Any, plan_id: str) -> tuple[int, str]:
    _release_schedule(subscription)
    sub = stripe.Subscription.retrieve(_sid(subscription), expand=["items.data.price"])
    items = _sget(_sget(sub, "items"), "data") or []
    if not items:
        raise ValueError("Předplatné nemá tarifovou položku")
    current_price = _item_price_id(items[0])
    start = _period_start(sub)
    end = _period_end(sub)
    if not start or not end:
        raise ValueError("Stripe neposlal konec zúčtovacího období")
    current_phase: dict[str, Any] = {
        "items": [{"price": current_price, "quantity": 1}],
        "start_date": int(start),
        "end_date": int(end),
        "proration_behavior": "none",
    }
    if (_sget(sub, "status") or "") == "trialing":
        trial_end = _sget(sub, "trial_end")
        if trial_end:
            current_phase["trial_end"] = int(trial_end)
    sched = stripe.SubscriptionSchedule.create(from_subscription=_sid(sub))
    stripe.SubscriptionSchedule.modify(
        getattr(sched, "id", None) or _sid(sched),
        end_behavior="release",
        phases=[
            current_phase,
            {
                "items": [{"price": _ensure_price(plan_id), "quantity": 1}],
                "start_date": int(end),
                "proration_behavior": "none",
                "metadata": {"plan": plan_id},
            },
        ],
    )
    return int(end), getattr(sched, "id", None) or _sid(sched)


def _latest_update_charge_czk(customer_id: str, subscription_id: str) -> float | None:
    invoices = stripe.Invoice.list(customer=customer_id, limit=6)
    for inv in invoices.data:
        if _sget(inv, "billing_reason") != "subscription_update":
            continue
        sub = _sget(inv, "subscription")
        if sub and _sid(sub) not in {"", subscription_id} and sub != subscription_id:
            continue
        return float((_sget(inv, "amount_paid") or _sget(inv, "amount_due") or 0) / 100)
    return None


def switch_existing_subscription(store: Store, customer_id: str, plan_id: str, subscription: Any | None = None) -> dict[str, Any]:
    record = store.billing_record() or {}
    kept = subscription or _enforce_single_subscription(customer_id, record.get("subscription_id") or "")
    if not kept:
        raise ValueError("Nemáš aktivní předplatné k přepnutí")
    sub = stripe.Subscription.retrieve(_sid(kept), expand=["items.data.price"])
    current_plan = _plan_of_subscription(sub)
    if PLAN_RANK.get(plan_id, 0) == PLAN_RANK.get(current_plan, 0):
        if _sget(sub, "schedule") or _sget(sub, "cancel_at_period_end"):
            _release_schedule(sub)
            stripe.Subscription.modify(_sid(sub), cancel_at_period_end=False)
            billing = sync_subscription(store, _sid(sub), customer_id)
            return {"url": None, "billing": billing, "switched": True, "upgraded": False, "scheduled": False, "paused_monitors": []}
        raise ValueError(f"Tarif {PLANS[plan_id]['label']} už máš aktivní")
    items = _sget(_sget(sub, "items"), "data") or []
    if not items:
        raise ValueError("Předplatné nemá tarifovou položku")
    upgrading = PLAN_RANK.get(plan_id, 0) > PLAN_RANK.get(current_plan, 0)
    if not upgrading:
        if plan_id not in PAID_PLANS:
            raise ValueError("Nižší tarif musí být Start")
        _schedule_downgrade(sub, plan_id)
        billing = sync_subscription(store, _sid(sub), customer_id)
        return {
            "url": None,
            "billing": billing,
            "switched": True,
            "upgraded": False,
            "scheduled": True,
            "charged_czk": None,
            "paused_monitors": [],
        }
    _release_schedule(sub)
    if _sget(sub, "cancel_at_period_end"):
        stripe.Subscription.modify(_sid(sub), cancel_at_period_end=False)
    payload_items: list[dict[str, Any]] = [{"id": _sid(items[0]), "price": _ensure_price(plan_id)}]
    for extra in items[1:]:
        payload_items.append({"id": _sid(extra), "deleted": True})
    status = _sget(sub, "status") or ""
    params: dict[str, Any] = {
        "items": payload_items,
        "cancel_at_period_end": False,
        "metadata": {"plan": plan_id},
    }
    if status == "active":
        params["proration_behavior"] = "always_invoice"
        params["payment_behavior"] = "error_if_incomplete"
    else:
        params["proration_behavior"] = "none"
    updated = stripe.Subscription.modify(_sid(sub), **params)
    billing = sync_subscription(store, updated, customer_id, plan_hint=plan_id)
    charged = None
    if params.get("proration_behavior") == "always_invoice":
        charged = _latest_update_charge_czk(customer_id, _sid(updated))
    return {
        "url": None,
        "billing": billing,
        "switched": True,
        "upgraded": True,
        "scheduled": False,
        "charged_czk": charged,
        "paused_monitors": [],
    }


def _customer(store: Store, email: str) -> str:
    record = store.billing_record() or {}
    if record.get("customer_id"):
        if email and email != record.get("email"):
            try:
                stripe.Customer.modify(record["customer_id"], email=email)
            except Exception:
                pass
            record["email"] = email
            store.save_billing_record(record)
        return record["customer_id"]
    kwargs: dict[str, Any] = {"metadata": {"app": "realitify"}}
    if email:
        kwargs["email"] = email
    customer = stripe.Customer.create(**kwargs)
    record["customer_id"] = customer.id
    record["email"] = email
    store.save_billing_record(record)
    return customer.id


def create_checkout(store: Store, plan_id: str, email: str = "", promo_code: str = "") -> dict[str, Any]:
    if plan_id not in PAID_PLANS:
        raise ValueError("Objednat lze jen tarify Start a PRO")
    _configure()
    current = store.billing_record() or {}
    customer_id = _customer(store, (email or current.get("email") or "").strip())
    live = _enforce_single_subscription(customer_id, current.get("subscription_id") or "")
    code = normalize_promo_code(promo_code or "")
    if live:
        if code:
            raise ValueError("Slevový kód platí jen na první objednávku")
        return switch_existing_subscription(store, customer_id, plan_id, live)
    _expire_open_checkouts(customer_id)
    had_subscription = bool(stripe.Subscription.list(customer=customer_id, status="all", limit=1).data)
    promo = None
    if code:
        if had_subscription:
            raise ValueError("Slevový kód platí jen na první objednávku")
        promo = lookup_promotion_code(code)
        current["pending_promo_code"] = promo["code"]
        store.save_billing_record(current)
    subscription_data: dict[str, Any] = {"metadata": {"plan": plan_id}}
    if promo:
        subscription_data["metadata"]["promo"] = promo["code"]
    elif not had_subscription:
        subscription_data["trial_period_days"] = int(PLANS[plan_id]["trial_days"])
    session_kwargs: dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items": [{"price": _ensure_price(plan_id), "quantity": 1}],
        "success_url": f"{config.PUBLIC_BASE_URL}/nastaveni/predplatne?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{config.PUBLIC_BASE_URL}/nastaveni/predplatne?billing=cancel",
        "locale": "cs",
        "automatic_tax": {"enabled": False},
        "managed_payments": {"enabled": False},
        "subscription_data": subscription_data,
        "metadata": {"plan": plan_id, "promo": promo["code"] if promo else ""},
    }
    if promo:
        session_kwargs["discounts"] = [{"promotion_code": promo["id"]}]
    else:
        session_kwargs["allow_promotion_codes"] = True
    session = stripe.checkout.Session.create(**session_kwargs)
    return {"url": session.url, "switched": False}


def create_portal(store: Store) -> str:
    _configure()
    customer_id = (store.billing_record() or {}).get("customer_id")
    if not customer_id:
        raise ValueError("Nejdřív objednej tarif Start nebo PRO")
    config_id = ""
    configs = stripe.billing_portal.Configuration.list(limit=10)
    for cfg in configs.data:
        if _sget(_sget(cfg, "business_profile"), "headline") == "Realitify":
            config_id = cfg.id
            break
    if not config_id:
        created = stripe.billing_portal.Configuration.create(
            business_profile={"headline": "Realitify"},
            features={
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
            },
        )
        config_id = created.id
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{config.PUBLIC_BASE_URL}/nastaveni/predplatne",
        configuration=config_id,
    )
    return session.url


def cancel_subscription(store: Store) -> dict[str, Any]:
    _configure()
    record = store.billing_record() or {}
    sub_id = record.get("subscription_id")
    if not sub_id:
        record.update({"plan": "free", "status": "active", "cancel_at_period_end": False, "subscription_id": "", "price_id": "", "pending_plan": "", "pending_at": "", "schedule_id": ""})
        store.save_billing_record(record)
        return {**billing_state(store), "scheduled": True, "paused_monitors": []}
    sub = stripe.Subscription.retrieve(sub_id)
    _release_schedule(sub)
    updated = stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
    billing = sync_subscription(store, updated, record.get("customer_id"))
    return {**billing, "scheduled": True, "paused_monitors": []}


def _line_price_id(line: Any) -> str | None:
    price = _sget(line, "price")
    if price:
        return price if isinstance(price, str) else _sid(price)
    details = _sget(_sget(line, "pricing"), "price_details") or {}
    raw = details.get("price") if isinstance(details, dict) else _sget(details, "price")
    return raw if isinstance(raw, str) else _sid(raw) or None


def list_invoices(store: Store) -> list[dict[str, Any]]:
    record = store.billing_record() or {}
    customer_id = record.get("customer_id")
    if not customer_id or not config.STRIPE_SECRET_KEY:
        return []
    _configure()
    try:
        invoices = stripe.Invoice.list(customer=customer_id, limit=12)
    except Exception:
        return []
    items = []
    for inv in invoices.data:
        lines = _sget(_sget(inv, "lines"), "data") or []
        plan = _plan_from_price(_line_price_id(lines[0]) if lines else None)
        items.append(
            {
                "id": _sid(inv),
                "date": _dt(_sget(inv, "created")),
                "amount": (_sget(inv, "amount_paid") or 0) / 100,
                "plan": PLANS.get(plan, PLANS["free"])["label"],
                "paid": _sget(inv, "status") == "paid",
                "pdf": _sget(inv, "invoice_pdf"),
            }
        )
    return items


def _invoice_status_cs(status: str, paid: bool) -> str:
    if paid or status == "paid":
        return "Zaplaceno"
    return {
        "open": "Nezaplaceno",
        "uncollectible": "Selhalo",
        "void": "Stornováno",
        "draft": "Koncept",
        "unpaid": "Nezaplaceno",
    }.get(status, status or "—")


def _czk(cents: Any) -> str:
    value = int(cents or 0) / 100
    text = f"{value:,.0f}".replace(",", " ")
    return f"{text} Kč"


def _czk_num(czk: float | int) -> str:
    return f"{int(round(float(czk or 0))):,}".replace(",", " ")


def _delta_pct(now: float, prev: float) -> tuple[str, str]:
    if prev <= 0 and now <= 0:
        return "", ""
    if prev <= 0:
        return "+100%", "up"
    change = (now - prev) / prev * 100
    if abs(change) < 0.05:
        return "0%", ""
    sign = "+" if change > 0 else ""
    tone = "down" if change < 0 else "up"
    return f"{sign}{change:.1f}%".replace(".", ","), tone


def _pct(part: float, whole: float) -> str:
    if whole <= 0:
        return "0%"
    return f"{round(100 * part / whole, 1)}%".replace(".", ",")


def _stripe_pages(list_fn: Any, **kwargs: Any) -> list[Any]:
    items: list[Any] = []
    kwargs = dict(kwargs)
    kwargs["limit"] = min(int(kwargs.get("limit") or 100), 100)
    starting = None
    for _ in range(25):
        page = list_fn(**({**kwargs, "starting_after": starting} if starting else kwargs))
        data = list(getattr(page, "data", None) or [])
        items.extend(data)
        if not getattr(page, "has_more", False) or not data:
            break
        starting = data[-1].id
    return items


def _plan_from_expanded_price(price: Any) -> str:
    if not price:
        return "free"
    if isinstance(price, str):
        return _plan_from_price(price)
    key = _sget(price, "lookup_key") or ""
    meta = _sget(price, "metadata") or {}
    plan_meta = meta.get("plan") if isinstance(meta, dict) else _sget(meta, "plan")
    for plan_id, spec in PLANS.items():
        if spec.get("lookup_key") == key or plan_meta == plan_id:
            return plan_id
    amount = int(_sget(price, "unit_amount") or 0)
    if amount in {14900, 149000}:
        return "start"
    if amount in {34900, 349000}:
        return "pro"
    return "free"


def _mrr_cents(price: Any, quantity: int = 1) -> int:
    amount = int(_sget(price, "unit_amount") or 0) * max(1, int(quantity or 1))
    rec = _sget(price, "recurring") or {}
    interval = rec.get("interval") if isinstance(rec, dict) else _sget(rec, "interval")
    count = int((rec.get("interval_count") if isinstance(rec, dict) else _sget(rec, "interval_count")) or 1)
    count = max(1, count)
    if interval == "year":
        return int(round(amount / (12 * count)))
    if interval == "week":
        return int(round(amount * 52 / (12 * count)))
    if interval == "day":
        return int(round(amount * 30 / count))
    return int(round(amount / count))


def _kind_from_invoice(inv: Any) -> str:
    if int(_sget(inv, "amount_refunded") or 0) > 0:
        return "Vráceno"
    reason = str(_sget(inv, "billing_reason") or "")
    return {
        "subscription_create": "Nové předplatné",
        "subscription_cycle": "Obnova předplatného",
        "subscription_update": "Změna předplatného",
        "subscription_threshold": "Doplatek",
        "manual": "Manuální faktura",
        "upcoming": "Nadcházející",
    }.get(reason, "Platba")


_MONTHS_CS = ["Led", "Úno", "Bře", "Dub", "Kvě", "Čer", "Čvc", "Srp", "Zář", "Říj", "Lis", "Pro"]
_FINANCE_CACHE: dict[str, Any] = {}
_FINANCE_AT = 0.0


def admin_finance(store: Store) -> dict[str, Any]:
    global _FINANCE_CACHE, _FINANCE_AT
    if _FINANCE_CACHE and time.time() - _FINANCE_AT < 60:
        return _FINANCE_CACHE
    empty = _empty_finance(store)
    if not config.STRIPE_SECRET_KEY:
        _FINANCE_CACHE, _FINANCE_AT = empty, time.time()
        return empty
    try:
        _configure()
        data = _stripe_finance(store)
    except Exception:
        data = empty
        data["error"] = "Stripe API teď neodpovědělo."
    _FINANCE_CACHE, _FINANCE_AT = data, time.time()
    return data


def _empty_finance(store: Store) -> dict[str, Any]:
    from app import account as user_account

    local_user = 1 if user_account.public_account(store).get("email") else 0
    local_plan = billing_state(store).get("plan") or "free"
    counts = {"free": 0, "start": 0, "pro": 0, "individual": 0}
    if local_user:
        counts[local_plan if local_plan in counts else "free"] = local_user
    now = datetime.now()
    labels = []
    for i in range(11, -1, -1):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        labels.append(_MONTHS_CS[month - 1])
    return {
        "configured": False,
        "mrr": 0,
        "arr": 0,
        "forecast": 0,
        "days_left": max(0, calendar.monthrange(now.year, now.month)[1] - now.day),
        "lifetime": 0,
        "mrr_delta": "",
        "mrr_tone": "",
        "users_total": local_user,
        "split": [
            {"id": "total", "label": "Celkem uživatelů", "n": local_user, "share": 100, "color": "#e8ebe6", "bar": 100},
            {"id": "free", "label": "Zdarma", "n": counts["free"], "share": 100 if counts["free"] and local_user else 0, "color": "#3b82f6", "bar": 100 if counts["free"] else 0},
            {"id": "start", "label": "Start (149 Kč)", "n": counts["start"], "share": 100 if counts["start"] else 0, "color": "#22c55e", "bar": 100 if counts["start"] else 0},
            {"id": "pro", "label": "Pro (349 Kč)", "n": counts["pro"], "share": 100 if counts["pro"] else 0, "color": "#163300", "bar": 100 if counts["pro"] else 0},
            {"id": "individual", "label": "Individual", "n": counts["individual"], "share": 100 if counts["individual"] else 0, "color": "#163300", "bar": 100 if counts["individual"] else 0},
        ],
        "revenue": {"points": [0] * 12, "labels": labels, "max": 0},
        "conversion": [
            {"label": "Free → Placené konverze", "hint": "Zkušební verze konvertující na předplatné", "value": "—"},
            {"label": "Průměrná doba free trial", "hint": "Doba od registrace k zakoupení tarifu", "value": "—"},
            {"label": "ARPU (průměr. příjem na uživatele)", "hint": "Průměrný denní výnos na aktivního člena", "value": "—"},
            {"label": "LTV (životní hodnota zákazníka)", "hint": "Celkový předpokládaný přínos jednoho uživatele", "value": "—"},
            {"label": "CAC (cena za akvizici)", "hint": "Náklady na získání jednoho platícího zákazníka", "value": "—"},
            {"label": "LTV/CAC poměr", "hint": "Vynikající zdraví jednotkové ekonomiky (>3x)", "value": "—"},
        ],
        "retention": [
            {"label": "Měsíční churn rate", "hint": "Uživatelé, kteří zrušili předplatné tento měsíc", "value": "—"},
            {"label": "Roční churn rate", "hint": "Předpokládaný odchod za 12 měsíců", "value": "—"},
            {"label": "Retence po 1 měsíci", "hint": "Uživatelé aktivní po prvním měsíci", "value": "—"},
            {"label": "Retence po 3 měsících", "hint": "Uživatelé aktivní po čtvrtletí", "value": "—"},
            {"label": "Retence po 6 měsících", "hint": "Uživatelé aktivní po půl roce", "value": "—"},
            {"label": "Retence po 12 měsících", "hint": "Uživatelé aktivní po jednom roce", "value": "—"},
        ],
        "plans": [
            {"name": "Zdarma", "price": "0 Kč", "active": counts["free"], "mrr": "0 Kč", "share": "0%", "churn": "—", "churn_ok": True, "total": False},
            {"name": "Start", "price": "149 Kč", "active": counts["start"], "mrr": "0 Kč", "share": "0%", "churn": "—", "churn_ok": True, "total": False},
            {"name": "Pro", "price": "349 Kč", "active": counts["pro"], "mrr": "0 Kč", "share": "0%", "churn": "—", "churn_ok": True, "total": False},
            {"name": "Celkem", "price": "—", "active": local_user, "mrr": "0 Kč", "share": "100%", "churn": "—", "churn_ok": True, "total": True},
        ],
        "transactions": [],
    }


def _stripe_finance(store: Store) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    year_ago = int((now - timedelta(days=400)).timestamp())
    subs = _stripe_pages(stripe.Subscription.list, status="all", expand=["data.items.data.price"])
    try:
        invoices = _stripe_pages(
            stripe.Invoice.list,
            created={"gte": year_ago},
            expand=["data.lines.data.price"],
        )
    except Exception:
        invoices = _stripe_pages(stripe.Invoice.list, created={"gte": year_ago})
    customers = _stripe_pages(stripe.Customer.list)
    paid_live = {"active", "trialing", "past_due", "unpaid", "paused"}
    plan_mrr: dict[str, int] = {key: 0 for key in ("free", "trial", "start", "pro", "individual")}
    plan_n: dict[str, int] = {key: 0 for key in plan_mrr}
    plan_cancel_30: dict[str, int] = {key: 0 for key in plan_mrr}
    plan_base_30: dict[str, int] = {key: 0 for key in plan_mrr}
    mrr_cents = 0
    trial_days: list[float] = []
    converted = 0
    had_trial = 0
    canceled_30 = 0
    canceled_365 = 0
    ever_paid = 0
    live_paid = 0
    cohorts: dict[int, list[bool]] = {1: [], 3: [], 6: [], 12: []}
    for sub in subs:
        status = str(_sget(sub, "status") or "")
        items = _sget(_sget(sub, "items"), "data") or []
        price = _sget(items[0], "price") if items else None
        plan = _plan_from_expanded_price(price)
        qty = int(_sget(items[0], "quantity") or 1) if items else 1
        cents = _mrr_cents(price, qty) if price else 0
        created = int(_sget(sub, "created") or 0)
        ended = int(_sget(sub, "ended_at") or _sget(sub, "canceled_at") or 0)
        trial_start = int(_sget(sub, "trial_start") or 0)
        trial_end = int(_sget(sub, "trial_end") or 0)
        if trial_start and trial_end and trial_end > trial_start:
            trial_days.append((trial_end - trial_start) / 86400)
            had_trial += 1
            if status in {"active", "past_due"}:
                converted += 1
        if status == "trialing":
            plan_n["trial"] += 1
        elif status in paid_live and plan in PAID_PLANS:
            plan_n[plan] += 1
            plan_mrr[plan] += cents
            mrr_cents += cents
            live_paid += 1
            ever_paid += 1
        elif status in paid_live and plan == "individual":
            plan_n["individual"] += 1
            plan_mrr["individual"] += cents
            mrr_cents += cents
            live_paid += 1
            ever_paid += 1
        elif status in {"canceled", "unpaid", "incomplete_expired"}:
            ever_paid += 1 if cents else ever_paid
            if ended and now.timestamp() - ended <= 30 * 86400:
                canceled_30 += 1
                plan_cancel_30[plan if plan in plan_cancel_30 else "free"] += 1
            if ended and now.timestamp() - ended <= 365 * 86400:
                canceled_365 += 1
        if created:
            age_days = (now.timestamp() - created) / 86400
            still = status in paid_live
            for months, rows in cohorts.items():
                if age_days >= months * 30:
                    rows.append(still)
        if created and now.timestamp() - created <= 30 * 86400 + 1:
            plan_base_30[plan if plan in plan_base_30 else "free"] += 1
        elif status in paid_live:
            plan_base_30[plan if plan in plan_base_30 else "free"] += 1
    customer_n = len(customers)
    from app import account as user_account

    local = 1 if user_account.public_account(store).get("email") else 0
    users_total = max(customer_n, local, sum(plan_n.values()))
    free_n = max(0, users_total - plan_n["trial"] - plan_n["start"] - plan_n["pro"] - plan_n["individual"])
    plan_n["free"] = free_n
    month_paid = 0
    prev_paid = 0
    lifetime = 0
    series = [0] * 12
    labels = []
    buckets: dict[str, int] = {}
    for i in range(11, -1, -1):
        stamp = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        month = stamp.month - i
        year = stamp.year
        while month <= 0:
            month += 12
            year -= 1
        key = f"{year:04d}-{month:02d}"
        buckets[key] = 0
        labels.append(_MONTHS_CS[month - 1])
    tx = []
    for inv in invoices:
        created = int(_sget(inv, "created") or 0)
        status = str(_sget(inv, "status") or "")
        paid = status == "paid"
        cents = int(_sget(inv, "amount_paid") or 0) if paid else int(_sget(inv, "amount_due") or 0)
        if paid:
            lifetime += cents
            when = datetime.fromtimestamp(created, tz=timezone.utc)
            key = when.strftime("%Y-%m")
            if key in buckets:
                buckets[key] += cents
            if when >= month_start:
                month_paid += cents
            elif when >= prev_start:
                prev_paid += cents
        lines = _sget(_sget(inv, "lines"), "data") or []
        price = None
        if lines:
            price = _sget(lines[0], "price") or _sget(_sget(lines[0], "pricing"), "price_details")
            if isinstance(price, dict):
                price = price.get("price")
        plan = _plan_from_expanded_price(price) if price else "free"
        if plan == "free":
            paid_amt = int(_sget(inv, "amount_paid") or 0) or int(_sget(inv, "amount_due") or 0)
            if paid_amt == 14900:
                plan = "start"
            elif paid_amt == 34900:
                plan = "pro"
        email = _sget(inv, "customer_email") or ""
        name = _sget(inv, "customer_name") or ""
        user = f"{name} ({email})" if name and email else (email or name or "—")
        st = _invoice_status_cs(status, paid)
        if int(_sget(inv, "amount_refunded") or 0) > 0:
            st = "Vráceno"
        tx.append(
            {
                "at": datetime.fromtimestamp(created, tz=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M") if created else "—",
                "at_iso": _dt(created) or "",
                "user": user,
                "type": _kind_from_invoice(inv),
                "plan": PLANS.get(plan, PLANS["free"])["label"],
                "amount": _czk(cents),
                "amount_cents": cents,
                "status": st,
                "ok": paid and st == "Zaplaceno",
                "warn": st in {"Vráceno", "Nezaplaceno"},
                "pdf": _sget(inv, "invoice_pdf") or "",
                "url": _sget(inv, "hosted_invoice_url") or "",
            }
        )
    tx.sort(key=lambda row: row.get("at_iso") or "", reverse=True)
    series = [int(round(buckets[key] / 100)) for key in buckets]
    mrr = int(round(mrr_cents / 100))
    arr = mrr * 12
    days_in = calendar.monthrange(now.year, now.month)[1]
    days_left = max(0, days_in - now.day)
    collected = int(round(month_paid / 100))
    forecast = max(mrr, collected)
    if mrr and days_in:
        forecast = max(collected, int(round(mrr * days_in / days_in)))
    mrr_delta, mrr_tone = _delta_pct(month_paid / 100, prev_paid / 100)
    free_like = free_n + plan_n["trial"]
    split = [
        {"id": "total", "label": "Celkem uživatelů", "n": users_total, "share": 100, "color": "#e8ebe6", "bar": 100},
        {
            "id": "free",
            "label": "Zdarma / trial" if plan_n["trial"] else "Zdarma",
            "n": free_like,
            "share": round(100 * free_like / max(users_total, 1), 1),
            "color": "#3b82f6",
            "bar": round(100 * free_like / max(users_total, 1)),
        },
        {"id": "start", "label": "Start (149 Kč)", "n": plan_n["start"], "share": round(100 * plan_n["start"] / max(users_total, 1), 1), "color": "#22c55e", "bar": round(100 * plan_n["start"] / max(users_total, 1))},
        {"id": "pro", "label": "Pro (349 Kč)", "n": plan_n["pro"], "share": round(100 * plan_n["pro"] / max(users_total, 1), 1), "color": "#163300", "bar": round(100 * plan_n["pro"] / max(users_total, 1))},
        {"id": "individual", "label": "Individual", "n": plan_n["individual"], "share": round(100 * plan_n["individual"] / max(users_total, 1), 1), "color": "#163300", "bar": round(100 * plan_n["individual"] / max(users_total, 1))},
    ]
    denom = had_trial or users_total
    if had_trial:
        conv = 100 * converted / had_trial
    else:
        conv = 100 * live_paid / max(users_total, 1)
    avg_trial = sum(trial_days) / len(trial_days) if trial_days else float(PLANS["start"].get("trial_days") or 0)
    paying = max(live_paid, 1) if mrr else 0
    arpu_month = mrr / paying if paying else 0
    arpu_day = arpu_month / 30 if paying else 0
    churn_m = 100 * canceled_30 / max(live_paid + canceled_30, 1)
    churn_y = 100 * (1 - (1 - churn_m / 100) ** 12) if live_paid or canceled_30 else 0
    ltv = (arpu_month / (churn_m / 100)) if churn_m > 0 else 0
    conversion = [
        {"label": "Free → Placené konverze", "hint": "Zkušební verze konvertující na předplatné", "value": f"{conv:.1f}%".replace(".", ",") if denom else "—"},
        {"label": "Průměrná doba free trial", "hint": "Doba od registrace k zakoupení tarifu", "value": f"{avg_trial:.1f} dní".replace(".", ",") if trial_days or avg_trial else "—"},
        {"label": "ARPU (průměr. příjem na uživatele)", "hint": "Průměrný denní výnos na aktivního člena", "value": f"{arpu_day:.1f} Kč/den".replace(".", ",") if paying else "—"},
        {"label": "LTV (životní hodnota zákazníka)", "hint": "Celkový předpokládaný přínos jednoho uživatele", "value": f"{_czk_num(ltv)} Kč" if ltv else "—"},
        {"label": "CAC (cena za akvizici)", "hint": "Náklady na získání jednoho platícího zákazníka", "value": "—"},
        {"label": "LTV/CAC poměr", "hint": "Vynikající zdraví jednotkové ekonomiky (>3x)", "value": "—"},
    ]
    def _ret(months: int) -> str:
        rows = cohorts.get(months) or []
        if not rows:
            return "—"
        return f"{round(100 * sum(1 for item in rows if item) / len(rows))}%"
    retention = [
        {"label": "Měsíční churn rate", "hint": "Uživatelé, kteří zrušili předplatné tento měsíc", "value": f"{churn_m:.1f}%".replace(".", ","), "tone": "bad" if churn_m else "", "arrow": bool(churn_m)},
        {"label": "Roční churn rate", "hint": "Předpokládaný odchod za 12 měsíců", "value": f"{churn_y:.1f}%".replace(".", ",")},
        {"label": "Retence po 1 měsíci", "hint": "Uživatelé aktivní po prvním měsíci", "value": _ret(1)},
        {"label": "Retence po 3 měsících", "hint": "Uživatelé aktivní po čtvrtletí", "value": _ret(3)},
        {"label": "Retence po 6 měsících", "hint": "Uživatelé aktivní po půl roce", "value": _ret(6)},
        {"label": "Retence po 12 měsících", "hint": "Uživatelé aktivní po jednom roce", "value": _ret(12)},
    ]
    plan_rows = []
    for key, label, price in (
        ("trial", "Free trial", "0 Kč"),
        ("start", "Start", "149 Kč"),
        ("pro", "Pro", "349 Kč"),
        ("individual", "Individual", "dohodou"),
        ("free", "Zdarma", "0 Kč"),
    ):
        if key == "trial" and plan_n["trial"] == 0:
            continue
        if key == "individual" and plan_n["individual"] == 0 and plan_mrr["individual"] == 0:
            continue
        n = plan_n[key]
        row_mrr = int(round(plan_mrr[key] / 100))
        if key in {"free", "trial"} or n == 0:
            churn = "—"
            churn_ok = True
        else:
            base = max(n + plan_cancel_30[key], 1)
            churn = f"{round(100 * plan_cancel_30[key] / base, 1)}%".replace(".", ",")
            churn_ok = plan_cancel_30[key] == 0
        plan_rows.append(
            {
                "name": label,
                "price": price,
                "active": n,
                "mrr": f"{_czk_num(row_mrr)} Kč" if key not in {"free", "trial"} else "0 Kč",
                "share": _pct(row_mrr, mrr) if key not in {"free", "trial"} else "0%",
                "churn": churn,
                "churn_ok": churn_ok,
                "total": False,
            }
        )
    active_sum = sum(int(row["active"]) for row in plan_rows)
    weighted = 0.0
    paid_n = sum(plan_n[k] for k in ("start", "pro", "individual"))
    if paid_n:
        weighted = 100 * sum(plan_cancel_30[k] for k in ("start", "pro", "individual")) / max(
            paid_n + sum(plan_cancel_30[k] for k in ("start", "pro", "individual")), 1
        )
    plan_rows.append(
        {
            "name": "Celkem",
            "price": "—",
            "active": active_sum,
            "mrr": f"{_czk_num(mrr)} Kč",
            "share": "100%",
            "churn": (f"{weighted:.1f} % prům.").replace(".", ",", 1) if paid_n else "—",
            "churn_ok": True,
            "total": True,
        }
    )
    return {
        "configured": True,
        "mrr": mrr,
        "arr": arr,
        "forecast": forecast,
        "days_left": days_left,
        "lifetime": int(round(lifetime / 100)),
        "mrr_delta": mrr_delta,
        "mrr_tone": mrr_tone,
        "users_total": users_total,
        "split": split,
        "revenue": {"points": series, "labels": labels, "max": max(series) if series else 0},
        "conversion": conversion,
        "retention": retention,
        "plans": plan_rows,
        "transactions": tx[:40],
    }


def _billing_log(store: Store) -> list[dict[str, Any]]:
    raw = store.get_meta("admin_billing_log") or "[]"
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        rows = []
    return rows if isinstance(rows, list) else []


def append_billing_log(store: Store, entry: dict[str, Any]) -> None:
    rows = _billing_log(store)
    rows.insert(0, {"at": datetime.now(timezone.utc).isoformat(), **entry})
    store.set_meta("admin_billing_log", json.dumps(rows[:80], ensure_ascii=False))


def list_billing_history(store: Store) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in _billing_log(store):
        items.append(
            {
                "at": row.get("at") or "",
                "kind": row.get("kind") or "Zásah",
                "plan": row.get("plan") or "—",
                "amount": row.get("amount") or "—",
                "status": row.get("status") or "—",
                "ok": row.get("ok", True),
                "note": row.get("note") or "",
                "source": "admin",
            }
        )
    record = store.billing_record() or {}
    customer_id = record.get("customer_id")
    if customer_id and config.STRIPE_SECRET_KEY:
        try:
            _configure()
            invoices = stripe.Invoice.list(customer=customer_id, limit=24)
            for inv in invoices.data:
                lines = _sget(_sget(inv, "lines"), "data") or []
                plan = _plan_from_price(_line_price_id(lines[0]) if lines else None)
                status = _sget(inv, "status") or ""
                paid = status == "paid"
                cents = _sget(inv, "amount_paid") if paid else (_sget(inv, "amount_due") or _sget(inv, "amount_paid") or 0)
                reason = _sget(inv, "billing_reason") or ""
                kind = "Upgrade" if reason == "subscription_update" else "Faktura"
                items.append(
                    {
                        "at": _dt(_sget(inv, "created")) or "",
                        "kind": kind,
                        "plan": PLANS.get(plan, PLANS["free"])["label"],
                        "amount": _czk(cents),
                        "status": _invoice_status_cs(status, paid),
                        "ok": paid,
                        "note": _sget(inv, "number") or _sid(inv),
                        "number": _sget(inv, "number") or _sid(inv),
                        "source": "stripe",
                        "pdf": _sget(inv, "invoice_pdf") or "",
                        "url": _sget(inv, "hosted_invoice_url") or "",
                    }
                )
            charges = stripe.Charge.list(customer=customer_id, limit=24)
            for charge in charges.data:
                paid = bool(_sget(charge, "paid"))
                failed = (_sget(charge, "status") or "") == "failed" or bool(_sget(charge, "failure_code"))
                if paid and not failed:
                    continue
                items.append(
                    {
                        "at": _dt(_sget(charge, "created")) or "",
                        "kind": "Platba",
                        "plan": "—",
                        "amount": _czk(_sget(charge, "amount")),
                        "status": "Selhalo" if failed or not paid else _sget(charge, "status") or "—",
                        "ok": False,
                        "note": _sget(charge, "failure_message") or _sid(charge),
                        "source": "stripe",
                    }
                )
        except Exception:
            pass
    items.sort(key=lambda row: row.get("at") or "", reverse=True)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in items:
        key = f"{row.get('at')}|{row.get('amount')}|{row.get('status')}|{row.get('note')}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique[:40]


def _cancel_stripe_now(store: Store, record: dict[str, Any]) -> None:
    sub_id = record.get("subscription_id")
    if not sub_id or not config.STRIPE_SECRET_KEY:
        return
    try:
        _configure()
        sub = stripe.Subscription.retrieve(sub_id)
        _release_schedule(sub)
        stripe.Subscription.cancel(sub_id)
    except Exception:
        try:
            stripe.Subscription.modify(sub_id, cancel_at_period_end=True)
        except Exception:
            pass
    record["subscription_id"] = ""
    record["price_id"] = ""
    record["cancel_at_period_end"] = False
    record["pending_plan"] = ""
    record["pending_at"] = ""
    record["schedule_id"] = ""


def admin_set_plan(store: Store, plan_id: str, charge: bool, email: str = "") -> dict[str, Any]:
    if plan_id not in PLANS:
        raise ValueError("Neznámý tarif")
    record = store.billing_record() or {}
    label = PLANS[plan_id]["label"]
    if charge:
        if plan_id == "individual":
            raise ValueError("INDIVIDUAL nelze strhnout přes Stripe — přiděl ho bez platby")
        record["comp_plan"] = ""
        store.save_billing_record(record)
        if plan_id == "free":
            result = cancel_subscription(store)
            append_billing_log(
                store,
                {
                    "kind": "Změna tarifu",
                    "plan": label,
                    "amount": "poměrně / konec období",
                    "status": "Naplánováno",
                    "ok": True,
                    "note": "Zrušení jako u uživatele (do konce období)",
                },
            )
            return {"ok": True, "mode": "charge", "billing": result, "message": "Předplatné se zruší na konci období, stejně jako kdyby to udělal uživatel."}
        if not config.STRIPE_SECRET_KEY:
            raise ValueError("Chybí STRIPE_SECRET_KEY — tarif bez platby nastav jako grant")
        result = create_checkout(store, plan_id, email or record.get("email") or "")
        if result.get("url"):
            append_billing_log(
                store,
                {
                    "kind": "Checkout",
                    "plan": label,
                    "amount": "čeká na platbu",
                    "status": "Nedokončeno",
                    "ok": False,
                    "note": "Uživatel nemá aktivní předplatné k přepnutí",
                },
            )
            raise ValueError("Uživatel nemá aktivní Stripe předplatné ani kartu. Přepni bez platby, nebo ať si tarif koupí sám.")
        charged = result.get("charged_czk")
        amount = f"{charged:.0f} Kč".replace(".", " ") if isinstance(charged, (int, float)) and charged is not None else ("poměrná část" if result.get("upgraded") else "—")
        note = "Upgrade se stržením poměrné části" if result.get("upgraded") else ("Downgrade od dalšího období" if result.get("scheduled") else "Přepnuto")
        append_billing_log(
            store,
            {
                "kind": "Změna tarifu",
                "plan": label,
                "amount": amount,
                "status": "Hotovo" if not result.get("scheduled") else "Naplánováno",
                "ok": True,
                "note": note,
            },
        )
        return {"ok": True, "mode": "charge", "billing": result.get("billing") or billing_state(store), "message": note, "charged_czk": charged}
    record["comp_plan"] = plan_id
    record["plan"] = plan_id
    record["status"] = "active"
    record["comp_at"] = datetime.now(timezone.utc).isoformat()
    _cancel_stripe_now(store, record)
    store.save_billing_record(record)
    apply_watch_limit(store)
    append_billing_log(
        store,
        {
            "kind": "Grant",
            "plan": label,
            "amount": "0 Kč",
            "status": "Bez platby",
            "ok": True,
            "note": f"Admin přidělil tarif {label} bez stržení platby",
        },
    )
    return {"ok": True, "mode": "grant", "billing": billing_state(store), "message": f"Tarif {label} je aktivní bez platby."}


def handle_webhook(store: Store, payload: bytes, signature: str | None) -> dict[str, Any]:
    _configure()
    if config.STRIPE_WEBHOOK_SECRET:
        if not signature:
            raise ValueError("Chybí Stripe-Signature")
        event = stripe.Webhook.construct_event(payload, signature, config.STRIPE_WEBHOOK_SECRET)
        etype = event["type"]
        obj = event["data"]["object"]
    else:
        import json

        event = json.loads(payload.decode("utf-8"))
        etype = event.get("type") or ""
        obj = (event.get("data") or {}).get("object") or {}
    if etype == "checkout.session.completed":
        session_id = obj.get("id") if isinstance(obj, dict) else obj.id
        return sync_checkout_session(store, session_id)
    if etype.startswith("customer.subscription.") or etype.startswith("subscription_schedule."):
        sub_id = obj.get("id") if isinstance(obj, dict) else getattr(obj, "id", None)
        if etype.startswith("subscription_schedule."):
            sub_id = obj.get("subscription") if isinstance(obj, dict) else _sget(obj, "subscription")
        if sub_id:
            state = sync_subscription(store, sub_id)
            apply_watch_limit(store)
            return state
    return billing_state(store)


def _invoice_promo_keys(inv: Any) -> set[str]:
    keys: set[str] = set()
    blobs: list[Any] = []
    disc = _sget(inv, "discount")
    if disc:
        blobs.append(disc)
    blobs.extend(_sget(inv, "discounts") or [])
    for item in blobs:
        if isinstance(item, str):
            keys.add(item)
            continue
        promo = _sget(item, "promotion_code")
        coupon = _sget(item, "coupon")
        if isinstance(promo, str):
            keys.add(promo)
        elif promo:
            keys.add(str(_sget(promo, "code") or ""))
            keys.add(_sid(promo))
        if isinstance(coupon, str):
            keys.add(coupon)
        elif coupon:
            keys.add(str(_sget(coupon, "name") or ""))
            keys.add(_sid(coupon))
    return {key for key in keys if key}


def _fmt_day(ts: int | None) -> str:
    if not ts:
        return "neurčito"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone().strftime("%d.%m.%Y")


def list_promotion_codes() -> list[dict[str, Any]]:
    if not config.STRIPE_SECRET_KEY:
        return []
    _configure()
    try:
        items = _stripe_pages(stripe.PromotionCode.list, expand=["data.promotion.coupon"])
    except Exception:
        return []
    rows = []
    for item in items:
        promo = _sget(item, "promotion") or {}
        coupon = _sget(promo, "coupon") if promo else _sget(item, "coupon")
        if isinstance(coupon, str):
            try:
                coupon = stripe.Coupon.retrieve(coupon)
            except Exception:
                coupon = None
        percent = int(_sget(coupon, "percent_off") or 0) if coupon else 0
        amount = int(_sget(coupon, "amount_off") or 0) if coupon else 0
        created = int(_sget(item, "created") or 0)
        expires = int(_sget(item, "expires_at") or 0)
        if coupon and not expires:
            expires = int(_sget(coupon, "redeem_by") or 0)
        meta_obj = _sget(item, "metadata") or {}
        if isinstance(meta_obj, dict):
            meta = meta_obj
        else:
            meta = {
                key: _sget(meta_obj, key) or ""
                for key in ("kind", "name", "cut", "valid_from", "valid_to", "from_iso", "to_iso")
            }
        code = str(_sget(item, "code") or "").upper()
        rows.append(
            {
                "id": _sid(item),
                "code": code,
                "discount": percent or (int(round(amount / 100)) if amount else 0),
                "from": meta.get("valid_from") or _fmt_day(created),
                "to": meta.get("valid_to") or _fmt_day(expires or None),
                "used": int(_sget(item, "times_redeemed") or 0),
                "on": bool(_sget(item, "active")),
                "kind": meta.get("kind") or "discount",
                "name": meta.get("name") or "",
                "cut": int(float(meta.get("cut") or 0)),
                "coupon_id": _sid(coupon) if coupon else "",
            }
        )
    rows.sort(key=lambda row: row.get("code") or "")
    return rows


def normalize_promo_code(code: str) -> str:
    return (code or "").strip().upper().replace(" ", "")


def lookup_promotion_code(code: str) -> dict[str, Any]:
    code = normalize_promo_code(code)
    if not re.fullmatch(r"[A-Z0-9-]{3,32}", code):
        raise ValueError("Neplatný formát slevového kódu")
    if not config.STRIPE_SECRET_KEY:
        raise ValueError("Slevové kódy teď nejsou dostupné")
    _configure()
    found = stripe.PromotionCode.list(code=code, limit=1, expand=["data.promotion.coupon"])
    if not found.data:
        raise ValueError("Slevový kód neexistuje")
    promo = found.data[0]
    if not _sget(promo, "active"):
        raise ValueError("Slevový kód není aktivní")
    expires = _sget(promo, "expires_at")
    if expires and int(expires) < int(time.time()):
        raise ValueError("Slevový kód vypršel")
    coupon = _sget(_sget(promo, "promotion"), "coupon") or _sget(promo, "coupon")
    percent = int(_sget(coupon, "percent_off") or 0)
    amount = int(_sget(coupon, "amount_off") or 0)
    return {
        "ok": True,
        "code": code,
        "id": _sid(promo),
        "percent": percent,
        "amount_czk": int(round(amount / 100)) if amount else 0,
        "first_order": True,
    }


def save_pending_promo(store: Store, code: str) -> dict[str, Any]:
    code = normalize_promo_code(code)
    record = store.billing_record() or {}
    if not code:
        record["pending_promo_code"] = ""
        store.save_billing_record(record)
        return {"ok": True, "code": "", "percent": 0, "amount_czk": 0, "first_order": True}
    looked = lookup_promotion_code(code)
    record["pending_promo_code"] = looked["code"]
    store.save_billing_record(record)
    return looked


def create_promotion_code(
    code: str,
    percent: int,
    *,
    valid_from: str = "",
    valid_to: str = "",
    kind: str = "discount",
    name: str = "",
    cut: int = 0,
) -> dict[str, Any]:
    if not config.STRIPE_SECRET_KEY:
        raise ValueError("Doplň STRIPE_SECRET_KEY v .env")
    code = (code or "").strip().upper().replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9-]{3,32}", code):
        raise ValueError("Kód musí mít 3–32 znaků (A–Z, 0–9 nebo -)")
    percent = int(percent)
    if percent < 1 or percent > 100:
        raise ValueError("Sleva musí být 1–100 %")
    _configure()
    existing = stripe.PromotionCode.list(code=code, limit=1)
    if existing.data:
        raise ValueError(f"Kód {code} už ve Stripe existuje")
    expires = None
    if valid_to:
        end = datetime.fromisoformat(valid_to)
        if end.tzinfo is None:
            end = end.replace(hour=23, minute=59, tzinfo=timezone.utc)
        else:
            end = end.astimezone(timezone.utc).replace(hour=23, minute=59)
        expires = int(end.timestamp())
    from_label = datetime.fromisoformat(valid_from).strftime("%d.%m.%Y") if valid_from else _fmt_day(int(time.time()))
    to_label = datetime.fromisoformat(valid_to).strftime("%d.%m.%Y") if valid_to else "neurčito"
    coupon_data = {
        "percent_off": percent,
        "duration": "once",
        "name": code,
        "metadata": {"kind": kind, "first_order": "1"},
    }
    if expires:
        coupon_data["redeem_by"] = expires
    coupon = stripe.Coupon.create(**coupon_data)
    promo_data = {
        "promotion": {"type": "coupon", "coupon": coupon.id},
        "code": code,
        "active": True,
        "restrictions": {"first_time_transaction": True},
        "metadata": {
            "kind": kind,
            "name": name or code,
            "cut": str(int(cut or 0)),
            "valid_from": from_label,
            "valid_to": to_label,
            "from_iso": valid_from or "",
            "to_iso": valid_to or "",
            "first_order": "1",
        },
    }
    if expires:
        promo_data["expires_at"] = expires
    try:
        promo = stripe.PromotionCode.create(**promo_data)
    except Exception:
        promo_data.pop("restrictions", None)
        promo = stripe.PromotionCode.create(**promo_data)
    return {"id": promo.id, "code": code}


def set_promotion_active(code: str, on: bool) -> dict[str, Any]:
    if not config.STRIPE_SECRET_KEY:
        raise ValueError("Doplň STRIPE_SECRET_KEY v .env")
    _configure()
    code = (code or "").strip().upper()
    found = stripe.PromotionCode.list(code=code, limit=1)
    if not found.data:
        raise ValueError("Kód ve Stripe není")
    stripe.PromotionCode.modify(found.data[0].id, active=bool(on))
    return {"ok": True, "code": code, "on": bool(on)}


def promo_invoice_stats() -> list[dict[str, Any]]:
    if not config.STRIPE_SECRET_KEY:
        return []
    _configure()
    year_ago = int((datetime.now(timezone.utc) - timedelta(days=400)).timestamp())
    try:
        invoices = _stripe_pages(stripe.Invoice.list, created={"gte": year_ago}, status="paid")
    except Exception:
        return []
    rows = []
    for inv in invoices:
        rows.append(
            {
                "at": int(_sget(inv, "created") or 0),
                "customer": _sid(_sget(inv, "customer")),
                "amount": int(_sget(inv, "amount_paid") or 0),
                "keys": _invoice_promo_keys(inv),
            }
        )
    return rows
