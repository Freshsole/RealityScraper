from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import config
from app.store import Store

PRAGUE = ZoneInfo("Europe/Prague")
KIND_PREF = {
    "new": "ntNew",
    "changed": "ntPrice",
    "sold": "ntExpire",
    "expire": "ntExpire",
    "digest": "ntDigest",
    "tips": "ntTips",
    "test": None,
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def vapid_keys() -> dict[str, str]:
    pem_path = config.DATA_DIR / "vapid-private.pem"
    path = config.DATA_DIR / "vapid.json"
    mailto = config.VAPID_MAILTO
    if config.VAPID_PUBLIC_KEY and config.VAPID_PRIVATE_KEY:
        private = config.VAPID_PRIVATE_KEY
        if "BEGIN" in private and not pem_path.exists():
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            pem_path.write_text(private if private.endswith("\n") else private + "\n", encoding="utf-8")
        elif "BEGIN" not in private:
            pem_path = pem_path
        return {
            "public": config.VAPID_PUBLIC_KEY,
            "private": str(pem_path) if pem_path.exists() else private,
            "mailto": mailto,
        }
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("public") and data.get("private"):
            private = data["private"]
            if "BEGIN" in str(private):
                config.DATA_DIR.mkdir(parents=True, exist_ok=True)
                pem_path.write_text(private if str(private).endswith("\n") else str(private) + "\n", encoding="utf-8")
                data["private"] = str(pem_path)
            data.setdefault("mailto", mailto)
            return data
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    pem_path.write_text(private_pem if private_pem.endswith("\n") else private_pem + "\n", encoding="utf-8")
    data = {
        "public": _b64url(public_raw),
        "private": str(pem_path),
        "mailto": mailto,
    }
    path.write_text(json.dumps({"public": data["public"], "private": private_pem, "mailto": mailto}), encoding="utf-8")
    return data


def public_key() -> str:
    return vapid_keys()["public"]


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = (value or "00:00").split(":")
    try:
        hour = max(0, min(23, int(parts[0])))
        minute = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return 0, 0
    return hour, minute


def in_quiet_hours(prefs: dict[str, Any]) -> bool:
    if not prefs.get("quiet"):
        return False
    now = datetime.now(PRAGUE)
    minutes = now.hour * 60 + now.minute
    start_h, start_m = _parse_hhmm(str(prefs.get("quietFrom") or "22:00"))
    end_h, end_m = _parse_hhmm(str(prefs.get("quietTo") or "07:00"))
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    if start == end:
        return True
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end


def kind_allowed(prefs: dict[str, Any], kind: str) -> bool:
    key = KIND_PREF.get(kind)
    if key is None:
        return True
    return bool(prefs.get(key, key != "ntTips"))


def should_send(store: Store, kind: str, *, instant: bool = True, ignore_quiet: bool = False) -> bool:
    prefs = store.notify_prefs()
    if not prefs.get("push"):
        return False
    if not kind_allowed(prefs, kind):
        return False
    if instant and not prefs.get("instant", True) and kind not in {"digest", "test"}:
        return False
    if not ignore_quiet and kind != "test" and in_quiet_hours(prefs):
        return False
    return True


def _payload(title: str, body: str, url: str = "/nabidka", tag: str = "realitify") -> str:
    return json.dumps(
        {
            "title": title,
            "body": body[:180],
            "url": url or "/nabidka",
            "tag": tag,
            "icon": "/static/site/assets/logo.svg",
        },
        ensure_ascii=False,
    )


def send_all(store: Store, title: str, body: str, url: str = "/nabidka", tag: str = "realitify") -> int:
    keys = vapid_keys()
    from pywebpush import WebPushException, webpush

    sent = 0
    for item in store.list_push_subscriptions():
        try:
            webpush(
                subscription_info={
                    "endpoint": item["endpoint"],
                    "keys": {"p256dh": item["p256dh"], "auth": item["auth"]},
                },
                data=_payload(title, body, url, tag),
                vapid_private_key=keys["private"],
                vapid_claims={"sub": keys.get("mailto") or "mailto:ahoj@realitify.cz"},
                ttl=86400,
            )
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {404, 410}:
                store.delete_push_subscription(item["endpoint"])
        except Exception:
            continue
    return sent


def listing_copy(listing: Any, kind: str, monitor_name: str = "") -> tuple[str, str, str]:
    data = listing.to_dict() if hasattr(listing, "to_dict") else dict(listing or {})
    locality = (data.get("locality") or "").strip()
    disposition = (data.get("disposition") or "").strip()
    price = data.get("price_label") or (f"{data.get('price_czk')} Kč" if data.get("price_czk") else "")
    name = (data.get("name") or "Nabídka").strip()
    url = data.get("url") or "/nabidka"
    bits = [part for part in (locality, disposition, price) if part]
    body = " · ".join(bits) or name
    if kind == "test":
        title = "Test notifikace"
    elif kind == "changed":
        title = "Změna ceny"
        if data.get("old_price_czk") and data.get("price_czk"):
            body = f"{price} (dřív {data['old_price_czk']} Kč) · {body}"
    elif kind == "sold":
        title = "Nabídka zmizela"
    else:
        title = "Nová nabídka"
    if monitor_name:
        body = f"{monitor_name}: {body}"
    return title, body, url


def notify_listing(store: Store, listing: Any, kind: str, monitor_name: str = "", *, ignore_quiet: bool = False) -> int:
    if not should_send(store, kind, ignore_quiet=ignore_quiet):
        return 0
    title, body, url = listing_copy(listing, kind, monitor_name)
    tag = f"listing-{getattr(listing, 'id', None) or (listing or {}).get('id') or kind}"
    return send_all(store, title, body, url, str(tag))


def notify_digest(store: Store, items: list[dict[str, Any]], *, test: bool = False) -> int:
    kind = "test" if test else "digest"
    if not should_send(store, kind, instant=False, ignore_quiet=test):
        return 0
    count = len(items)
    title = "Test souhrnu" if test else "Ranní souhrn"
    body = f"{count} nových zásahů k prohlédnutí" if count else "Žádné nové zásahy"
    return send_all(store, title, body, "/nabidka", "digest")


def notify_test(store: Store) -> int:
    if not store.notify_prefs().get("push"):
        return 0
    return send_all(
        store,
        "Realitify je připravené",
        "Push notifikace na tomto zařízení fungují.",
        "/nastaveni/notifikace",
        "push-test",
    )
