from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
            "Neomezeně + API",
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
    data["downgrade_losses"] = {f"{src}_{dst}": rows for (src, dst), rows in DOWNGRADE_LOSSES.items()}
    pending = data.get("pending_plan") if data.get("pending_plan") in PLANS and data.get("pending_plan") != plan else ""
    data["pending_plan"] = pending
    data["pending_label"] = PLANS[pending]["label"] if pending else ""
    data["pending_at"] = data.get("pending_at") or None
    return data


def watch_limit_for(store: Store) -> int | None:
    plan = (store.billing_record() or {}).get("plan") or "free"
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


def create_checkout(store: Store, plan_id: str, email: str = "") -> dict[str, Any]:
    if plan_id not in PAID_PLANS:
        raise ValueError("Objednat lze jen tarify Start a PRO")
    _configure()
    current = store.billing_record() or {}
    customer_id = _customer(store, (email or current.get("email") or "").strip())
    live = _enforce_single_subscription(customer_id, current.get("subscription_id") or "")
    if live:
        return switch_existing_subscription(store, customer_id, plan_id, live)
    _expire_open_checkouts(customer_id)
    had_subscription = bool(stripe.Subscription.list(customer=customer_id, status="all", limit=1).data)
    subscription_data: dict[str, Any] = {"metadata": {"plan": plan_id}}
    if not had_subscription:
        subscription_data["trial_period_days"] = int(PLANS[plan_id]["trial_days"])
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": _ensure_price(plan_id), "quantity": 1}],
        success_url=f"{config.PUBLIC_BASE_URL}/nastaveni/predplatne?billing=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{config.PUBLIC_BASE_URL}/nastaveni/predplatne?billing=cancel",
        locale="cs",
        allow_promotion_codes=True,
        automatic_tax={"enabled": False},
        managed_payments={"enabled": False},
        subscription_data=subscription_data,
        metadata={"plan": plan_id},
    )
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
