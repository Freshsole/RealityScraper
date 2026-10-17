"""Set up a Stripe account for Realitify and (optionally) copy data from another account.

Everything is key-driven, so the same script prepares a sandbox and later the live account:

    # 1) idempotent setup of the target account: plan products/prices, billing portal, webhook
    STRIPE_SECRET_KEY=sk_... PUBLIC_BASE_URL=https://realitify.cz python scripts/stripe_migrate.py setup

    # 2) copy promo codes, customers (+ remap them in the local user stores) from another account
    STRIPE_SOURCE_KEY=sk_old STRIPE_SECRET_KEY=sk_new python scripts/stripe_migrate.py migrate [--apply] [--subscriptions]

`migrate` is a dry run unless --apply is given. --subscriptions recreates active subscriptions with the
Stripe test card and only works when the target key is a sk_test_ key (cards cannot be copied between
live accounts through the API; ask Stripe support for a PAN migration in that case).
DATA_DIR (env) selects the data directory whose billing records get the new customer/subscription ids.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import stripe

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import billing, config  # noqa: E402
from app import users as user_registry  # noqa: E402
from app.store import Store  # noqa: E402

WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "subscription_schedule.created",
    "subscription_schedule.updated",
    "subscription_schedule.released",
    "subscription_schedule.completed",
    "subscription_schedule.canceled",
    "subscription_schedule.aborted",
]


def _d(obj: Any) -> dict[str, Any]:
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _all(fn: Any, key: str, **kwargs: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    after = None
    while True:
        page = fn(limit=100, api_key=key, **({"starting_after": after} if after else {}), **kwargs)
        out.extend(_d(x) for x in page.data)
        if not page.has_more or not page.data:
            return out
        after = page.data[-1].id


def _account(key: str) -> str:
    return stripe.Account.retrieve(api_key=key).id


# --- setup -------------------------------------------------------------------

def setup(target: str, webhook_url: str) -> None:
    stripe.api_key = target
    print(f"target account: {_account(target)} ({'TEST' if target.startswith('sk_test_') else 'LIVE'})")
    for plan_id in billing.PAID_PLANS:
        print(f"  plan {plan_id}: price {billing._ensure_price(plan_id)}")
    cfgs = [c for c in stripe.billing_portal.Configuration.list(limit=10).data if (c.business_profile or {}).get("headline") == "Realitify"]
    if cfgs:
        print(f"  billing portal: exists ({cfgs[0].id})")
    else:
        created = stripe.billing_portal.Configuration.create(
            business_profile={"headline": "Realitify"},
            features={
                "invoice_history": {"enabled": True},
                "payment_method_update": {"enabled": True},
                "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
            },
        )
        print(f"  billing portal: created ({created.id})")
    if not webhook_url:
        return
    existing = [w for w in stripe.WebhookEndpoint.list(limit=100).data if w.url == webhook_url]
    if existing:
        print(f"  webhook: {webhook_url} already exists ({existing[0].id}); its secret is only shown at creation")
        return
    hook = stripe.WebhookEndpoint.create(url=webhook_url, enabled_events=WEBHOOK_EVENTS, description="Realitify billing")
    print(f"  webhook: created {hook.id}\n  STRIPE_WEBHOOK_SECRET={hook.secret}")


# --- migrate -----------------------------------------------------------------

def _copy_promos(source: str, target: str, apply: bool) -> dict[str, str]:
    """Copy promotion codes (+coupons). Returns {code: target promotion_code id}."""
    promos = _all(stripe.PromotionCode.list, source, expand=["data.promotion.coupon"])
    have = {p["code"].upper(): p["id"] for p in _all(stripe.PromotionCode.list, target)}
    mapping = dict(have)
    now = int(time.time())
    seen: set[str] = set()
    for promo in promos:
        code = str(promo["code"]).upper()
        if code in seen:
            continue
        seen.add(code)
        if code in have:
            print(f"  promo {code}: already in target")
            continue
        coupon = (promo.get("promotion") or {}).get("coupon") or promo.get("coupon") or {}
        if isinstance(coupon, str):
            coupon = _d(stripe.Coupon.retrieve(coupon, api_key=source))
        expires = promo.get("expires_at") or coupon.get("redeem_by")
        if expires and int(expires) < now:
            print(f"  promo {code}: expired, skipped")
            continue
        coupon_args: dict[str, Any] = {"duration": coupon.get("duration") or "once", "name": coupon.get("name") or code,
                                       "metadata": coupon.get("metadata") or {}}
        for field in ("percent_off", "amount_off", "currency", "duration_in_months", "max_redemptions", "redeem_by"):
            if coupon.get(field):
                coupon_args[field] = coupon[field]
        print(f"  promo {code}: {coupon_args.get('percent_off') or coupon_args.get('amount_off')} {'%' if coupon_args.get('percent_off') else ''} -> {'create' if apply else 'would create'}")
        if not apply:
            continue
        new_coupon = stripe.Coupon.create(api_key=target, **coupon_args)
        args: dict[str, Any] = {"promotion": {"type": "coupon", "coupon": new_coupon.id}, "code": code,
                                "active": bool(promo.get("active", True)), "metadata": promo.get("metadata") or {}}
        if promo.get("expires_at"):
            args["expires_at"] = promo["expires_at"]
        if promo.get("max_redemptions"):
            args["max_redemptions"] = promo["max_redemptions"]
        restrictions = {k: v for k, v in (promo.get("restrictions") or {}).items() if v not in (None, False)}
        if restrictions:
            args["restrictions"] = restrictions
        mapping[code] = stripe.PromotionCode.create(api_key=target, **args).id
    return mapping


def _copy_customers(source: str, target: str, apply: bool) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cust in _all(stripe.Customer.list, source):
        if cust.get("deleted"):
            continue
        email = cust.get("email") or ""
        found = stripe.Customer.list(email=email, limit=1, api_key=target).data if email else []
        if found:
            mapping[cust["id"]] = found[0].id
            print(f"  customer {email}: already in target ({found[0].id})")
            continue
        print(f"  customer {email or cust['id']}: {'create' if apply else 'would create'}")
        if not apply:
            continue
        args = {k: cust[k] for k in ("email", "name", "phone", "description", "address") if cust.get(k)}
        meta = dict(cust.get("metadata") or {})
        meta["migrated_from"] = cust["id"]
        mapping[cust["id"]] = stripe.Customer.create(api_key=target, metadata=meta, **args).id
    return mapping


def _copy_subscriptions(source: str, target: str, customers: dict[str, str], promos: dict[str, str], apply: bool) -> dict[str, str]:
    mapping: dict[str, str] = {}
    prices = {p["lookup_key"]: p["id"] for p in _all(stripe.Price.list, target, active=True) if p.get("lookup_key")}
    for sub in _all(stripe.Subscription.list, source, status="all"):
        if sub["status"] not in {"active", "trialing", "past_due"}:
            continue
        new_customer = customers.get(sub["customer"])
        item = sub["items"]["data"][0]
        lookup = (item.get("price") or {}).get("lookup_key") or ""
        price = prices.get(lookup)
        if not (new_customer and price):
            print(f"  subscription {sub['id']}: no mapped customer/price ({lookup or 'no lookup key'}), skipped")
            continue
        print(f"  subscription {sub['id']} ({lookup}): {'create' if apply else 'would create'}")
        if not apply:
            continue
        pm = stripe.PaymentMethod.attach("pm_card_visa", customer=new_customer, api_key=target)
        stripe.Customer.modify(new_customer, invoice_settings={"default_payment_method": pm.id}, api_key=target)
        args: dict[str, Any] = {"customer": new_customer, "items": [{"price": price}], "default_payment_method": pm.id,
                                "metadata": {**(sub.get("metadata") or {}), "migrated_from": sub["id"]}}
        period_end = int(sub.get("current_period_end") or item.get("current_period_end") or 0)
        if period_end > time.time() + 3600:
            args["trial_end"] = period_end  # keep the renewal date, no charge now
        promo_code = str((sub.get("metadata") or {}).get("promo") or "").upper()
        if promo_code and promos.get(promo_code):
            args["discounts"] = [{"promotion_code": promos[promo_code]}]
        mapping[sub["id"]] = stripe.Subscription.create(api_key=target, **args).id
    return mapping


def _stores() -> list[Store]:
    central = Store(config.DB_PATH)
    stores = [central]
    for user in user_registry.list_users(central):
        uid = str(user.get("id") or "")
        path = user_registry.user_store_path(uid)
        if uid and Path(path).exists():
            stores.append(Store(path, seed_webhook_url=""))
    return stores


def _remap_billing(customers: dict[str, str], subs: dict[str, str], apply: bool) -> None:
    for store in _stores():
        record = store.billing_record() or {}
        old = str(record.get("customer_id") or "")
        if not old:
            continue
        new = customers.get(old)
        if not new:
            print(f"  billing {record.get('email') or old}: customer not migrated, left as is")
            continue
        record["customer_id"] = new
        old_sub = str(record.get("subscription_id") or "")
        if old_sub and old_sub in subs:
            record["subscription_id"] = subs[old_sub]
        elif old_sub:
            print(f"  billing {record.get('email') or old}: subscription {old_sub} was NOT migrated (run with --subscriptions in test mode)")
        record.pop("schedule_id", None)
        print(f"  billing {record.get('email') or new}: customer {old} -> {new}" + (f", subscription -> {record['subscription_id']}" if old_sub in subs else ""))
        if apply:
            store.save_billing_record(record)


def migrate(source: str, target: str, apply: bool, subscriptions: bool) -> None:
    if source == target:
        raise SystemExit("source and target keys are the same")
    print(f"source: {_account(source)}  ->  target: {_account(target)} ({'TEST' if target.startswith('sk_test_') else 'LIVE'})")
    print("mode:", "APPLY" if apply else "dry run (add --apply)")
    stripe.api_key = target
    for plan_id in billing.PAID_PLANS:
        billing._ensure_price(plan_id) if apply else None
    print("promo codes:")
    promos = _copy_promos(source, target, apply)
    print("customers:")
    customers = _copy_customers(source, target, apply)
    subs: dict[str, str] = {}
    if subscriptions:
        if not target.startswith("sk_test_"):
            raise SystemExit("--subscriptions is allowed only with a sk_test_ target key")
        print("subscriptions:")
        subs = _copy_subscriptions(source, target, customers, promos, apply)
    print("local billing records:")
    _remap_billing(customers, subs, apply)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "migrate"])
    ap.add_argument("--source-key", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--subscriptions", action="store_true")
    ap.add_argument("--webhook-url", default="", help="default: PUBLIC_BASE_URL/api/billing/webhook")
    args = ap.parse_args()
    target = config.STRIPE_SECRET_KEY
    if not target:
        raise SystemExit("STRIPE_SECRET_KEY is not set")
    if args.command == "setup":
        setup(target, args.webhook_url or f"{config.PUBLIC_BASE_URL}/api/billing/webhook")
    else:
        import os

        source = args.source_key or os.getenv("STRIPE_SOURCE_KEY", "")
        if not source:
            raise SystemExit("pass --source-key or STRIPE_SOURCE_KEY")
        migrate(source, target, args.apply, args.subscriptions)


if __name__ == "__main__":
    main()
