from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from app import account as user_account
from app import billing as stripe_billing
from app import config
from app import whatsapp as wa_notify
from app.store import Store, local_day_start

ADMIN_COOKIE = "realitify_admin"
DEMO = "demo"
LIVE = "live"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_meta(store: Store, key: str, fallback: Any) -> Any:
    raw = store.get_meta(key) or ""
    if not raw:
        return fallback
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return fallback
    return data if data is not None else fallback


def _set_json_meta(store: Store, key: str, value: Any) -> None:
    store.set_meta(key, json.dumps(value, ensure_ascii=False))


def initials(name: str, email: str = "") -> str:
    parts = [p for p in (name or "").split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    if parts:
        return parts[0][:2].upper()
    local = (email or "?").split("@")[0]
    return local[:2].upper()


def fmt_dt(value: str | None) -> str:
    if not value:
        return "—"
    text = str(value).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return str(value)[:16].replace("T", " ")
    if stamp.tzinfo:
        stamp = stamp.astimezone()
    return stamp.strftime("%d.%m.%Y %H:%M")


def fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    return fmt_dt(value)[:10]


def relative_cs(value: str | None) -> str:
    if not value:
        return "—"
    text = str(value).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return "—"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
    secs = int(delta.total_seconds())
    if secs < 60:
        return "před chvílí"
    if secs < 3600:
        return f"před {secs // 60} min"
    if secs < 86400:
        hours = secs // 3600
        return f"před {hours} h"
    days = secs // 86400
    if days == 1:
        return "před 1 dnem"
    if days < 45:
        return f"před {days} dny"
    return fmt_date(value)


def relative_short(value: str | None) -> str:
    if not value:
        return "—"
    text = str(value).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return "—"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    if secs < 60:
        return "teď"
    if secs < 3600:
        return f"před {secs // 60}m"
    if secs < 86400:
        return f"před {secs // 3600}h"
    return f"před {secs // 86400}d"


def relative_long(value: str | None) -> str:
    if not value:
        return "—"
    text = str(value).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return "—"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
    if secs < 60:
        return "před chvílí"
    if secs < 3600:
        mins = max(1, secs // 60)
        return f"před {mins} min"
    if secs < 86400:
        hours = max(1, secs // 3600)
        return "před 1 hodinou" if hours == 1 else f"před {hours} hodinami"
    days = secs // 86400
    if days == 1:
        return "před 1 dnem"
    if days < 45:
        return f"před {days} dny"
    return fmt_date(value)


def _last_activity(store: Store, fallback: str | None) -> str | None:
    last_at = fallback
    with store.connect() as conn:
        last_ev = conn.execute("SELECT MAX(created_at) FROM events").fetchone()[0]
        try:
            last_pv = conn.execute(
                "SELECT MAX(created_at) FROM analytics_events WHERE kind = 'pageview'"
            ).fetchone()[0]
        except Exception:
            last_pv = None
    return last_pv or last_ev or last_at


def demo(value: Any, label: str = "ukázková data") -> dict[str, Any]:
    return {"value": value, "source": DEMO, "note": label}


def live(value: Any) -> dict[str, Any]:
    return {"value": value, "source": LIVE, "note": ""}


def _admin_session_ok(store: Store, token: str | None) -> bool:
    stored = store.get_meta("admin_session") or ""
    given = token or ""
    if not stored or not given or len(stored) != len(given):
        return False
    return secrets.compare_digest(stored, given)


def admin_from_cookie(store: Store, token: str | None) -> dict[str, Any] | None:
    if not _admin_session_ok(store, token):
        return None
    account = user_account.public_account(store)
    name = account.get("name") or "Admin"
    email = config.ADMIN_EMAIL
    return {
        "email": email,
        "name": name if (account.get("email") or "").lower() == email else "Administrátor",
        "initials": initials(name if (account.get("email") or "").lower() == email else "Admin Realitify", email),
        "role": "Admin účet",
    }


def new_admin_session(store: Store) -> str:
    token = secrets.token_urlsafe(32)
    store.set_meta("admin_session", token)
    return token


def clear_admin_session(store: Store) -> None:
    store.set_meta("admin_session", None)


def login_admin(store: Store, email: str, password: str) -> str:
    email_norm = (email or "").strip().lower()
    data = user_account.account_record(store)
    account_email = (data.get("email") or "").strip().lower()
    role = user_account.account_role_from_data(data)
    allowed = email_norm == config.ADMIN_EMAIL or (bool(email_norm) and email_norm == account_email and role == "admin")
    if not allowed:
        raise ValueError("Do administrace se lze přihlásit jen jako admin")
    password = password or ""
    ok = False
    env_pw = config.ADMIN_PASSWORD
    if env_pw and email_norm == config.ADMIN_EMAIL:
        left = hashlib.sha256(f"admin|{env_pw}".encode()).digest()
        right = hashlib.sha256(f"admin|{password}".encode()).digest()
        if hmac.compare_digest(left, right):
            ok = True
    if not ok and data.get("password_hash") and email_norm in {config.ADMIN_EMAIL, account_email}:
        if user_account._verify_password(password, data.get("password_hash") or "", data.get("password_salt") or ""):
            ok = True
    if not ok:
        if not env_pw and account_email != config.ADMIN_EMAIL and role != "admin":
            raise ValueError("Nastav ADMIN_PASSWORD v .env, nebo založ účet admin@realitify.cz")
        raise ValueError("E-mail nebo heslo nesedí")
    return new_admin_session(store)


def _audit(store: Store, text: str, actor: str = "Admin") -> None:
    rows = _json_meta(store, "admin_audit", [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, {"at": _now(), "actor": actor, "text": text})
    _set_json_meta(store, "admin_audit", rows[:80])


def _blocked(store: Store) -> bool:
    return bool(_json_meta(store, "admin_user_blocked", False))


def _watch_override(store: Store) -> int | None:
    raw = store.get_meta("admin_watch_limit")
    if raw in (None, ""):
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _count_events(store: Store, *, since: str | None = None, until: str | None = None, kinds: tuple[str, ...] = ("new", "changed")) -> int:
    sql = "SELECT COUNT(*) FROM events WHERE kind IN ({})".format(",".join("?" * len(kinds)))
    params: list[Any] = list(kinds)
    if since:
        sql += " AND created_at >= ?"
        params.append(since)
    if until:
        sql += " AND created_at < ?"
        params.append(until)
    with store.connect() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def _count_sql(store: Store, sql: str, params: tuple[Any, ...] = ()) -> int:
    with store.connect() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def _city_name(locality: str) -> str:
    text = (locality or "").strip()
    if not text:
        return ""
    text = re.split(r"[–,]", text)[0].strip()
    text = re.sub(r"\s+\d.*$", "", text)
    return text


def _offer_from_url(url: str) -> str:
    lowered = (url or "").lower()
    if "prodej" in lowered:
        return "Prodej"
    if "pronajem" in lowered or "pronájem" in lowered:
        return "Pronájem"
    return ""


def _locality_from_url(url: str) -> str:
    parsed = urlparse(url or "")
    qs = parse_qs(parsed.query)
    osm = unquote((qs.get("osm_value") or [""])[0]).strip()
    if osm:
        return osm.split(",")[0]
    path = parsed.path.lower()
    parts = [p for p in path.split("/") if p and p not in {"hledani", "search", "cs", "vyhledat"}]
    skip = {"pronajem", "prodej", "byty", "dum", "domy", "pozemky", "drazby", "podily"}
    names = [p.replace("-", " ") for p in parts if p not in skip and not p.isdigit()]
    if not names:
        return ""
    return names[-1].title()


def _disposition_from_url(url: str) -> str:
    qs = parse_qs(urlparse(url or "").query)
    velikost = unquote((qs.get("velikost") or [""])[0])
    if velikost:
        return velikost.replace(",", ", ")[:40]
    disp = qs.get("disposition") or []
    if disp:
        return ", ".join(item.replace("DISP_", "").replace("_", "+") for item in disp[:4])
    return "—"


def _fmt_czk(value: str) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def _price_from_url(url: str) -> str:
    qs = parse_qs(urlparse(url or "").query)
    low = (qs.get("cena-od") or qs.get("priceFrom") or [""])[0]
    high = (qs.get("cena-do") or qs.get("priceTo") or [""])[0]
    if low and high:
        return f"{_fmt_czk(low)}-{_fmt_czk(high)} Kč"
    if high:
        return f"do {_fmt_czk(high)} Kč"
    if low:
        return f"od {_fmt_czk(low)} Kč"
    return "—"


def _disp_bucket(text: str) -> str:
    first = (text or "").split(",")[0].strip().lower().replace(" ", "")
    first = re.sub(r"(\d)kk", r"\1+kk", first)
    first = first.replace("++", "+")
    for key in ("3+kk", "2+kk", "1+kk", "3+1", "2+1"):
        if key in first:
            return key
    return "Ostatní"


def _kpi_delta(now: float | int, prev: float | int) -> dict[str, str]:
    try:
        now_n = float(now)
        prev_n = float(prev)
    except (TypeError, ValueError):
        return {"delta": "n/a", "delta_tone": "na"}
    if prev_n <= 0:
        return {"delta": "n/a", "delta_tone": "na"}
    pct = (now_n - prev_n) / prev_n * 100
    sign = "+" if pct >= 0 else ""
    return {"delta": f"{sign}{pct:.1f}%", "delta_tone": "down" if pct < 0 else "up"}


def _share_rows(counter: Counter[str], order: list[str], tones: list[str]) -> list[dict[str, Any]]:
    total = sum(counter.values())
    rows = []
    for i, label in enumerate(order):
        share = round(counter[label] / total * 100) if total else 0
        rows.append({"name": label, "label": label, "share": share, "tone": tones[i] if i < len(tones) else "green"})
    return rows


def _top_localities(counter: Counter[str], limit: int = 6) -> list[dict[str, Any]]:
    cleaned: Counter[str] = Counter()
    for name, n in counter.items():
        cleaned["Neuvedeno" if not name or name == "—" else name] += n
    total = sum(cleaned.values()) or 1
    top = cleaned.most_common(limit)
    used = {name for name, _ in top}
    rest = sum(n for name, n in cleaned.items() if name not in used)
    rows = [{"name": name, "share": round(n / total * 100), "tone": "forest"} for name, n in top]
    if rest:
        rows.append({"name": "Ostatní", "share": round(rest / total * 100), "tone": "forest"})
    return rows


def _disp_short(text: str) -> str:
    if not text or text == "—":
        return "—"
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return "—"
    if len(parts) <= 2:
        return ", ".join(parts)
    return f"{parts[0]}, {parts[1]}…"


def _monitor_view(item: dict[str, Any], email: str, hits: int | None = None) -> dict[str, Any]:
    url = item.get("search_url") or ""
    offer = _offer_from_url(url) or "Pronájem"
    enabled = bool(item.get("enabled"))
    locality = _city_name(_locality_from_url(url)) or _locality_from_url(url) or "—"
    disp = _disposition_from_url(url)
    return {
        "id": item.get("id"),
        "user_id": "local",
        "name": item.get("name") or "Monitor",
        "email": email,
        "locality": locality,
        "offer": offer,
        "disposition": _disp_short(disp),
        "price": _price_from_url(url),
        "status": "Aktivní" if enabled else "Pozastavený",
        "enabled": enabled,
        "created": fmt_date(item.get("created_at")),
        "created_at": item.get("created_at") or "",
        "hits": int(hits if hits is not None else item.get("tracked") or 0),
        "error": item.get("last_error") or "",
        "interval_sec": item.get("interval_sec") or config.POLL_INTERVAL_SEC,
        "last_check": relative_cs(item.get("last_check")),
        "url": url,
    }


def overview_payload(store: Store, hub: Any) -> dict[str, Any]:
    from app import analytics as stats

    stats.backfill(store)
    today = stats._day_start(0)
    yday = stats._day_start(1)
    d7 = stats._since(7)
    d30 = stats._since(30)
    prev7 = stats._since(14)
    prev30 = stats._since(60)
    active_today = stats.unique_visitors(store, today)
    active_yday = stats.unique_visitors(store, yday, today)
    active_7 = stats.unique_visitors(store, d7)
    active_7_prev = stats.unique_visitors(store, prev7, d7)
    active_30 = stats.unique_visitors(store, d30)
    active_30_prev = stats.unique_visitors(store, prev30, d30)
    views_today = stats.count_kind(store, stats.KIND_PAGE, today)
    views_yday = stats.count_kind(store, stats.KIND_PAGE, yday, today)
    sign_today = stats.count_kind(store, stats.KIND_SIGNUP, today)
    sign_yday = stats.count_kind(store, stats.KIND_SIGNUP, yday, today)
    sign_30 = stats.count_kind(store, stats.KIND_SIGNUP, d30)
    sign_30_prev = stats.count_kind(store, stats.KIND_SIGNUP, prev30, d30)
    monitors_30 = stats.count_kind(store, stats.KIND_MONITOR, d30)
    monitors_prev = stats.count_kind(store, stats.KIND_MONITOR, prev30, d30)
    sent_30 = _count_events(store, since=d30)
    sent_prev = _count_events(store, since=prev30, until=d30)
    fail_30 = stats.count_kind(store, stats.KIND_NOTIFY_FAIL, d30)
    fail_prev = stats.count_kind(store, stats.KIND_NOTIFY_FAIL, prev30, d30)
    disc_30 = stats.count_kind(store, stats.KIND_DISCORD, d30)
    got = sent_30
    base = max(active_30, 1)
    regs = max(sign_30, 1) if sign_30 or disc_30 or got else 0

    def _pct(n: int, den: int) -> int:
        if den <= 0:
            return 100 if n else 0
        return min(100, round(n / den * 100))

    flow_visit = 100 if active_30 else 0
    flow_reg = _pct(sign_30, base) if active_30 else (100 if sign_30 else 0)
    flow_mon = _pct(monitors_30, base) if active_30 else (100 if monitors_30 else 0)
    flow_disc = _pct(disc_30, regs) if regs else (100 if disc_30 else 0)
    flow_note = _pct(1 if got else 0, regs) if regs else (100 if got else 0)
    countries = stats.breakdown(store, "country_name", d30)
    c_total = sum(n for _, n in countries) or 1
    cities = stats.breakdown(store, "city", d30)
    city_total = sum(n for _, n in cities) or 1
    devices = stats.device_share(store, d30)
    traffic_views = stats.pageviews_by_day(store, 30)
    traffic_sign = stats.signups_by_day(store, 30)
    name_to_code = {name: code for code, name in stats.COUNTRY_NAMES.items()}
    country_rows = []
    rest_n = 0
    for i, (name, n) in enumerate(countries):
        if i < 5:
            code = name_to_code.get(name, "")
            label = f"{name} ({code})" if code else name
            country_rows.append({"name": label, "share": round(n / c_total * 100), "n": n, "code": code})
        else:
            rest_n += n
    if rest_n:
        country_rows.append({"name": "Ostatní", "share": round(rest_n / c_total * 100), "n": rest_n, "code": ""})
    city_rows = []
    with store.connect() as conn:
        for name, n in cities[:5]:
            row = conn.execute(
                "SELECT country FROM analytics_events WHERE city = ? AND IFNULL(country,'') != '' LIMIT 1",
                (name,),
            ).fetchone()
            city_rows.append({"name": name, "country": (row["country"] if row else "—") or "—", "n": n, "share": round(n / city_total * 100)})
    def kpi(label: str, value: int, cur: int, prev: int) -> dict[str, Any]:
        item = live(stats.fmt_int(value))
        item["delta"] = stats.delta_pct(cur, prev)
        item["down"] = cur < prev
        return {"label": label, **item}

    return {
        "kpis": [
            kpi("Aktivní dnes", active_today, active_today, active_yday),
            kpi("Aktivní 7 dní", active_7, active_7, active_7_prev),
            kpi("Aktivní 30 dní", active_30, active_30, active_30_prev),
            kpi("Návštěvy dnes", views_today, views_today, views_yday),
            kpi("Registrace dnes", sign_today, sign_today, sign_yday),
            kpi("Registrace 30 dní", sign_30, sign_30, sign_30_prev),
            kpi("Nové monitory 30 dní", monitors_30, monitors_30, monitors_prev),
            kpi("Notifikace odeslány (30d)", sent_30, sent_30, max(sent_prev, 0)),
            kpi("Notifikace selhaly (30d)", fail_30, fail_30, fail_prev),
        ],
        "traffic": {
            "source": LIVE,
            "note": "",
            "mode": "views",
            "points": traffic_views,
            "signups": traffic_sign,
        },
        "flow": [
            {"label": "Navštívil web", **live(flow_visit)},
            {"label": "Zaregistroval se", **live(flow_reg)},
            {"label": "Vytvořil monitor", **live(flow_mon)},
            {"label": "Propojil Discord", **live(flow_disc)},
            {"label": "Dostal notifikaci", **live(flow_note)},
        ],
        "countries": {"source": LIVE, "note": "", "rows": country_rows},
        "devices": {"source": LIVE, "note": "", **devices},
        "cities": {"source": LIVE, "note": "", "rows": city_rows},
        "live": stats.live_visitors(store),
    }


def users_payload(store: Store) -> dict[str, Any]:
    account = user_account.public_account(store)
    data = user_account.account_record(store)
    monitors = store.list_monitors()
    disc = user_account.discord_status(store)
    wa = wa_notify.status(store)
    blocked = _blocked(store)
    user = None
    if account.get("email"):
        hits = _count_events(store)
        uniq = 0
        with store.connect() as conn:
            uniq = int(conn.execute("SELECT COUNT(DISTINCT listing_id) FROM events WHERE kind IN ('new','changed')").fetchone()[0] or 0)
        last_at = _last_activity(store, data.get("created_at"))
        billing = stripe_billing.billing_state(store)
        def _spc(n: int) -> str:
            return f"{int(n):,}".replace(",", " ")
        user = {
            "id": "local",
            "email": account.get("email"),
            "name": account.get("name") or "—",
            "registered": fmt_date(data.get("created_at")),
            "registered_at": data.get("created_at") or "",
            "status": "Zablokovaný" if blocked else "Aktivní",
            "blocked": blocked,
            "plan": billing.get("plan") or "free",
            "plan_label": billing.get("label") or "Zdarma",
            "role": user_account.account_role(store),
            "monitors": len(monitors),
            "discord": bool(disc.get("linked")),
            "whatsapp": bool(wa.get("enabled") or wa.get("phone")),
            "activity": relative_short(last_at),
            "activity_at": last_at or "",
            "hits": hits,
            "notif": f"{_spc(hits)} / {_spc(uniq)}",
        }
    audit = _json_meta(store, "admin_audit", [])
    return {
        "total": 1 if user else 0,
        "users": [user] if user else [],
        "audit": audit[:8],
        "note": "Platforma má teď jeden lokální účet — tabulka ukazuje reálná data z této instance.",
    }


def user_detail_payload(store: Store) -> dict[str, Any]:
    account = user_account.public_account(store)
    data = user_account.account_record(store)
    if not account.get("email"):
        raise ValueError("Žádný uživatel")
    monitors = store.list_monitors()
    disc = user_account.discord_status(store)
    wa = wa_notify.status(store)
    billing = stripe_billing.billing_state(store)
    override = _watch_override(store)
    limit = override if override is not None else billing.get("watch_limit")
    events = store.recent_notified(limit=12, twins=False)
    timeline = []
    if data.get("created_at"):
        timeline.append({"title": "Registrace", "at": fmt_dt(data.get("created_at"))})
    for item in monitors:
        name = item.get("name") or "monitor"
        timeline.append({"title": f"Vytvořil monitor {name}", "at": fmt_dt(item.get("created_at"))})
        if not item.get("enabled"):
            timeline.append({"title": f"Pozastavil monitor {name}", "at": fmt_dt(item.get("updated_at") or item.get("created_at"))})
    if disc.get("linked"):
        timeline.append({"title": "Propojil Discord", "at": fmt_dt(data.get("discord_linked_at"))})
    timeline.sort(key=lambda row: row.get("at") or "", reverse=True)
    return {
        "user": {
            "id": "USR-LOCAL",
            "name": account.get("name") or "—",
            "email": account.get("email"),
            "registered": fmt_date(data.get("created_at")),
            "status": "Zablokovaný" if _blocked(store) else "Aktivní",
            "blocked": _blocked(store),
            "activity": relative_long(_last_activity(store, data.get("created_at"))),
            "watch_limit": limit if limit is not None else "neomezeně",
            "phone": account.get("phone") or "—",
            "role": user_account.account_role(store),
            "plan": billing.get("plan") or "free",
            "plan_label": billing.get("label") or "Zdarma",
            "comp": bool(billing.get("comp")),
        },
        "monitors": [_monitor_view(item, account.get("email") or "") for item in monitors],
        "discord": {
            "linked": bool(disc.get("linked")),
            "channel": "Ano" if disc.get("linked") else "Ne",
            "at": fmt_date(data.get("discord_linked_at")),
            "server": data.get("discord_channel_id") or "—",
        },
        "whatsapp": {
            "linked": bool(wa.get("phone")),
            "phone": wa.get("phone") or "—",
            "at": fmt_date(data.get("whatsapp_linked_at") or wa.get("linked_at")),
        },
        "notifications": [
            {
                "at": fmt_dt(item.get("hit_at") or item.get("first_seen")),
                "channel": "Discord" if disc.get("linked") else "E-mail",
                "monitor": item.get("monitor_name") or "—",
                "status": "Úspěšné",
                "detail": item.get("name") or item.get("price_label") or "",
            }
            for item in events
        ],
        "timeline": timeline[:80],
        "audit": _json_meta(store, "admin_audit", [])[:8],
        "billing": {
            "plan": billing.get("plan") or "free",
            "label": billing.get("label") or "Zdarma",
            "price_czk": billing.get("price_czk"),
            "status": billing.get("status") or "—",
            "comp": bool(billing.get("comp")),
            "period_end": fmt_dt(billing.get("current_period_end")),
            "pending_label": billing.get("pending_label") or "",
            "pending_at": fmt_dt(billing.get("pending_at")),
            "payment": billing.get("payment") if isinstance(billing.get("payment"), dict) else None,
            "configured": bool(billing.get("configured")),
            "history": [
                {
                    "at": fmt_dt(row.get("at")),
                    "kind": row.get("kind"),
                    "plan": row.get("plan"),
                    "amount": row.get("amount"),
                    "status": row.get("status"),
                    "ok": bool(row.get("ok")),
                    "note": row.get("note") or "",
                    "number": row.get("number") or row.get("note") or "",
                    "pdf": row.get("pdf") or "",
                    "url": row.get("url") or "",
                    "source": row.get("source") or "",
                }
                for row in stripe_billing.list_billing_history(store)
            ],
        },
    }


def monitors_payload(store: Store) -> dict[str, Any]:
    account = user_account.public_account(store)
    email = account.get("email") or "—"
    monitors = store.list_monitors()
    enabled_n = sum(1 for item in monitors if item.get("enabled"))
    paused_n = len(monitors) - enabled_n
    now = datetime.now(timezone.utc)
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_yday = start_today - timedelta(days=1)
    today_iso = start_today.isoformat()
    yday_iso = start_yday.isoformat()
    since_30 = _since(30)
    since_60 = _since(60)

    def _created(item: dict[str, Any]) -> str:
        return item.get("created_at") or ""

    created_today = sum(1 for item in monitors if _created(item) >= today_iso)
    created_yday = sum(1 for item in monitors if yday_iso <= _created(item) < today_iso)
    created_30 = sum(1 for item in monitors if _created(item) >= since_30)
    created_prev30 = sum(1 for item in monitors if since_60 <= _created(item) < since_30)
    existed_30 = sum(1 for item in monitors if _created(item) and _created(item) < since_30)
    n_users = 1 if account.get("email") else 0
    avg = round(len(monitors) / max(n_users, 1), 1)
    hit_map: dict[str, int] = {}
    with store.connect() as conn:
        for row in conn.execute(
            "SELECT monitor_id, COUNT(*) FROM events WHERE kind IN ('new','changed') GROUP BY monitor_id"
        ):
            hit_map[str(row[0])] = int(row[1])
        listing_rows = conn.execute(
            "SELECT price_czk, price_label FROM catalog_listings WHERE IFNULL(gone,0)=0 AND price_czk IS NOT NULL"
        ).fetchall()
    loc_counts: Counter[str] = Counter()
    offer_counts: Counter[str] = Counter()
    disp_counts: Counter[str] = Counter()
    views = []
    for item in monitors:
        hits = hit_map.get(str(item.get("id")), int(item.get("tracked") or 0))
        view = _monitor_view(item, email, hits)
        views.append(view)
        loc_counts[view["locality"]] += 1
        offer_counts[view["offer"]] += 1
        disp_counts[_disp_bucket(_disposition_from_url(item.get("search_url") or ""))] += 1
    rent_order = ["10 000 - 20 000 Kč", "do 10 000 Kč", "20 000 - 30 000 Kč", "30 000 - 50 000 Kč", "nad 50 000 Kč"]
    sale_order = ["3 - 5 mil. Kč", "do 3 mil. Kč", "5 - 8 mil. Kč", "nad 8 mil. Kč"]
    rent_buckets: Counter[str] = Counter()
    sale_buckets: Counter[str] = Counter()
    for row in listing_rows:
        price = int(row["price_czk"] or 0)
        label = (row["price_label"] or "").lower()
        rent = "měs" in label or price < 200000
        if rent:
            if price < 10000:
                rent_buckets["do 10 000 Kč"] += 1
            elif price < 20000:
                rent_buckets["10 000 - 20 000 Kč"] += 1
            elif price < 30000:
                rent_buckets["20 000 - 30 000 Kč"] += 1
            elif price < 50000:
                rent_buckets["30 000 - 50 000 Kč"] += 1
            else:
                rent_buckets["nad 50 000 Kč"] += 1
        else:
            if price < 3_000_000:
                sale_buckets["do 3 mil. Kč"] += 1
            elif price < 5_000_000:
                sale_buckets["3 - 5 mil. Kč"] += 1
            elif price < 8_000_000:
                sale_buckets["5 - 8 mil. Kč"] += 1
            else:
                sale_buckets["nad 8 mil. Kč"] += 1
    localities = sorted({row["locality"] for row in views if row["locality"] and row["locality"] != "—"})
    return {
        "kpis": [
            {"label": "Celkem monitorů", **live(len(monitors)), **_kpi_delta(len(monitors), existed_30)},
            {"label": "Aktivních", **live(enabled_n), "delta": "n/a", "delta_tone": "na"},
            {"label": "Pozastavených", **live(paused_n), "delta": "n/a", "delta_tone": "na"},
            {"label": "Vytvořeno dnes", **live(created_today), **_kpi_delta(created_today, created_yday)},
            {"label": "Vytvořeno 30d", **live(created_30), **_kpi_delta(created_30, created_prev30)},
            {"label": "Průměr na uživatele", **live(avg), "delta": "n/a", "delta_tone": "na"},
        ],
        "localities": _top_localities(loc_counts),
        "offers": _share_rows(offer_counts, ["Pronájem", "Prodej"], ["forest", "green"]),
        "dispositions": _share_rows(
            disp_counts,
            ["2+kk", "3+kk", "1+kk", "2+1", "3+1", "Ostatní"],
            ["green", "green", "green", "green", "mint", "line"],
        ),
        "monitors": views,
        "locality_options": localities,
        "rent": _share_rows(rent_buckets, rent_order, ["forest", "green", "green", "mint", "line"]),
        "sale": _share_rows(sale_buckets, sale_order, ["forest", "green", "green", "mint"]),
        "total": len(views),
    }


def _spc(n: Any) -> str:
    try:
        num = float(n)
    except (TypeError, ValueError):
        return str(n)
    if num.is_integer():
        return f"{int(num):,}".replace(",", " ")
    return f"{num:.1f}".replace(".", ".")


def _ping_stats(store: Store, since: str) -> tuple[int, int]:
    with store.connect() as conn:
        sent = int(
            conn.execute(
                "SELECT COUNT(*) FROM ping_queue WHERE status = 'sent' AND COALESCE(processed_at, created_at) >= ?",
                (since,),
            ).fetchone()[0]
            or 0
        )
        fail = int(
            conn.execute(
                "SELECT COUNT(*) FROM ping_queue WHERE status IN ('failed', 'error') AND COALESCE(processed_at, created_at) >= ?",
                (since,),
            ).fetchone()[0]
            or 0
        )
    return sent, fail


def _notify_logs(store: Store, email: str) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    pinged: set[tuple[Any, Any]] = set()
    with store.connect() as conn:
        pings = conn.execute(
            """
            SELECT ping_queue.created_at, ping_queue.processed_at, ping_queue.status, ping_queue.error,
                   ping_queue.kind, ping_queue.listing_id, ping_queue.monitor_id, monitors.name AS monitor_name
            FROM ping_queue
            LEFT JOIN monitors ON monitors.id = ping_queue.monitor_id
            ORDER BY COALESCE(ping_queue.processed_at, ping_queue.created_at) DESC
            LIMIT 200
            """
        ).fetchall()
        events = conn.execute(
            """
            SELECT events.created_at, events.kind, events.listing_id, events.monitor_id,
                   monitors.name AS monitor_name, listings.notified
            FROM events
            LEFT JOIN monitors ON monitors.id = events.monitor_id
            LEFT JOIN listings ON listings.id = events.listing_id AND listings.monitor_id = events.monitor_id
            WHERE events.kind IN ('new', 'changed')
            ORDER BY events.created_at DESC
            LIMIT 200
            """
        ).fetchall()
    for row in pings:
        status = row["status"] or ""
        ok = status == "sent"
        failed = status in {"failed", "error"}
        pinged.add((str(row["monitor_id"] or ""), row["listing_id"]))
        logs.append(
            {
                "at": fmt_dt(row["processed_at"] or row["created_at"]),
                "at_iso": row["processed_at"] or row["created_at"] or "",
                "user": email or "—",
                "monitor": row["monitor_name"] or "—",
                "channel": "Discord",
                "kind": "Auto",
                "kind_key": "auto",
                "status": "Doručeno" if ok else ("Selhalo" if failed else "Čeká"),
                "ok": ok,
                "error": (row["error"] or "—")[:90],
            }
        )
    for row in events:
        key = (str(row["monitor_id"] or ""), row["listing_id"])
        if key in pinged:
            continue
        logs.append(
            {
                "at": fmt_dt(row["created_at"]),
                "at_iso": row["created_at"] or "",
                "user": email or "—",
                "monitor": row["monitor_name"] or "—",
                "channel": "E-mail" if config.SMTP_HOST else "Discord",
                "kind": "Auto",
                "kind_key": "auto",
                "status": "Doručeno" if row["notified"] else "Selhalo",
                "ok": bool(row["notified"]),
                "error": "—" if row["notified"] else "Nepodařilo se odeslat",
            }
        )
    for item in _json_meta(store, "admin_broadcasts", []) or []:
        if not isinstance(item, dict):
            continue
        logs.append(
            {
                "at": fmt_dt(item.get("at")),
                "at_iso": item.get("at") or "",
                "user": "ALL",
                "monitor": "—",
                "channel": item.get("channel") or "E-mail",
                "kind": "Hromadná",
                "kind_key": "bulk",
                "status": "Doručeno" if int(item.get("delivered") or 0) else ("Naplánováno" if not item.get("immediate", True) else "Selhalo"),
                "ok": int(item.get("delivered") or 0) > 0,
                "error": (item.get("error") or "—")[:90],
            }
        )
    logs.sort(key=lambda row: row.get("at_iso") or "", reverse=True)
    return logs[:250]


def notifications_payload(store: Store) -> dict[str, Any]:
    from app import analytics as stats

    stats.backfill(store)
    today = stats._day_start(0)
    yday = stats._day_start(1)
    d7 = stats._since(7)
    d14 = stats._since(14)
    d30 = stats._since(30)
    d60 = stats._since(60)
    sent_today = _count_events(store, since=today)
    sent_yday = _count_events(store, since=yday, until=today)
    sent_7 = _count_events(store, since=d7)
    sent_7_prev = _count_events(store, since=d14, until=d7)
    sent_30 = _count_events(store, since=d30)
    sent_prev30 = _count_events(store, since=d60, until=d30)
    fail_30 = stats.count_kind(store, stats.KIND_NOTIFY_FAIL, d30)
    fail_prev = stats.count_kind(store, stats.KIND_NOTIFY_FAIL, d60, d30)
    disc_sent, disc_fail = _ping_stats(store, d30)
    fail_30 = fail_30 + disc_fail
    fail_prev = fail_prev
    account = user_account.public_account(store)
    email = account.get("email") or ""
    n_users = 1 if email else 0
    total_30 = sent_30 + fail_30
    rate = round(sent_30 / total_30 * 100, 1) if total_30 else 0
    prev_total = sent_prev30 + fail_prev
    prev_rate = round(sent_prev30 / prev_total * 100, 1) if prev_total else 0
    avg = round(sent_30 / max(n_users, 1) / 30, 1) if sent_30 else 0
    avg_prev = round(sent_prev30 / max(n_users, 1) / 30, 1) if sent_prev30 else 0
    rate_delta = _kpi_delta(rate, prev_rate)
    if fail_30 == 0 or (rate_delta.get("delta_tone") != "na" and abs(rate - prev_rate) < 0.5):
        rate_delta = {"delta": "stabilní", "delta_tone": "na"}
    fail_delta = _kpi_delta(fail_30, fail_prev)
    if fail_delta.get("delta_tone") == "down":
        fail_delta["delta_tone"] = "bad"
    disc = user_account.discord_status(store)
    wa = wa_notify.status(store)
    mail_on = bool(config.SMTP_HOST)
    mail_sent = sent_30 if mail_on else 0
    mail_fail = 0
    wa_sent = 0
    wa_fail = 0
    broadcasts = _json_meta(store, "admin_broadcasts", [])
    if not isinstance(broadcasts, list):
        broadcasts = []

    def _ch_ok(sent: int, failed: int) -> float:
        total = sent + failed
        return round(sent / total * 100, 1) if total else 0

    logs = _notify_logs(store, email)
    return {
        "kpis": [
            {"label": "Odesláno dnes", **live(_spc(sent_today)), **_kpi_delta(sent_today, sent_yday)},
            {"label": "Odesláno 7d", **live(_spc(sent_7)), **_kpi_delta(sent_7, sent_7_prev)},
            {"label": "Odesláno 30d", **live(_spc(sent_30)), **_kpi_delta(sent_30, sent_prev30)},
            {"label": "Selhalo 30d", **live(_spc(fail_30)), **fail_delta},
            {"label": "Úspěšnost", **live(f"{rate}%" if total_30 else "—"), **rate_delta},
            {"label": "Průměr/uživatel/den", **live(avg), **_kpi_delta(avg, avg_prev)},
        ],
        "broadcasts": [
            {
                **row,
                "at": fmt_dt(row.get("at")),
                "recipients": row.get("recipients") if row.get("recipients") is not None else n_users,
            }
            for row in broadcasts
            if isinstance(row, dict)
        ],
        "logs": logs,
        "channels": [
            {
                "name": "E-mail",
                "icon": "mail",
                "sent": mail_sent,
                "failed": mail_fail,
                "ok": _ch_ok(mail_sent, mail_fail),
                "source": LIVE if mail_on else DEMO,
            },
            {
                "name": "Discord",
                "icon": "discord",
                "sent": disc_sent,
                "failed": disc_fail,
                "ok": _ch_ok(disc_sent, disc_fail),
                "source": LIVE,
            },
            {
                "name": "WhatsApp",
                "icon": "phone",
                "sent": wa_sent,
                "failed": wa_fail,
                "ok": _ch_ok(wa_sent, wa_fail),
                "source": LIVE if wa.get("configured") else DEMO,
            },
        ],
        "users": n_users,
    }


def save_broadcast(store: Store, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    rows = _json_meta(store, "admin_broadcasts", [])
    if not isinstance(rows, list):
        rows = []
    item = {
        "at": payload.get("at") or _now(),
        "actor": actor,
        "subject": (payload.get("subject") or "").strip() or "Hromadná zpráva",
        "body": (payload.get("body") or "").strip(),
        "channel": payload.get("channel") or "E-mail",
        "audience": payload.get("audience") or "Všichni uživatelé",
        "immediate": bool(payload.get("immediate", True)),
        "scheduled_at": payload.get("scheduled_at") or "",
        "recipients": int(payload.get("recipients") or 0),
        "delivered": int(payload.get("delivered") or 0),
        "failed": int(payload.get("failed") or 0),
        "error": payload.get("error") or "—",
        "note": payload.get("note") or "",
    }
    rows.insert(0, item)
    _set_json_meta(store, "admin_broadcasts", rows[:40])
    _audit(store, f"uložil hromadnou zprávu „{item['subject']}“", actor)
    return item


async def send_broadcast(store: Store, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    from app import email_notify as mail
    from app.discord_notify import send_text as discord_send

    account = user_account.public_account(store)
    email = (account.get("email") or "").strip()
    n_users = 1 if email else 0
    subject = (payload.get("subject") or "").strip() or "Hromadná zpráva"
    body = (payload.get("body") or "").strip()
    raw_ch = (payload.get("channel") or "all").strip()
    immediate = bool(payload.get("immediate", True))
    scheduled_at = (payload.get("scheduled_at") or "").strip()
    channels: list[str] = []
    if raw_ch in {"", "all", "Všechny kanály"}:
        channels = ["E-mail", "Discord", "WhatsApp"]
        channel_label = "Všechny"
    elif raw_ch.lower() in {"email", "e-mail"}:
        channels = ["E-mail"]
        channel_label = "E-mail"
    elif raw_ch.lower() == "discord":
        channels = ["Discord"]
        channel_label = "Discord"
    else:
        channels = ["WhatsApp"]
        channel_label = "WhatsApp"
    delivered = 0
    failed = 0
    errors: list[str] = []
    if not immediate:
        item = save_broadcast(
            store,
            {
                "subject": subject,
                "body": body,
                "channel": channel_label,
                "audience": payload.get("audience") or "Všichni uživatelé",
                "immediate": False,
                "scheduled_at": scheduled_at,
                "recipients": n_users,
                "delivered": 0,
                "failed": 0,
                "error": "Naplánováno",
                "note": "Čeká na naplánovaný čas.",
            },
            actor,
        )
        return item
    if "E-mail" in channels:
        if email and mail.configured():
            try:
                await asyncio.to_thread(mail.send_message, email, subject, body)
                delivered += 1
            except Exception as exc:
                failed += 1
                errors.append(str(exc)[:120])
        else:
            failed += 1
            errors.append("E-mail není nastavený")
    if "Discord" in channels:
        webhook = store.notify_webhook() or store.discord_webhook_url()
        if webhook:
            try:
                await discord_send(webhook, f"{subject}\n\n{body}"[:1900])
                delivered += 1
            except Exception as exc:
                failed += 1
                errors.append(str(exc)[:120])
        else:
            failed += 1
            errors.append("Discord webhook chybí")
    if "WhatsApp" in channels:
        phone = wa_notify.account_phone(store)
        if phone and wa_notify.server_configured():
            try:
                await wa_notify.send_text(phone, f"{subject}\n{body}"[:1000])
                delivered += 1
            except Exception as exc:
                failed += 1
                errors.append(str(exc)[:120])
        else:
            failed += 1
            errors.append("WhatsApp není nastavený")
    item = save_broadcast(
        store,
        {
            "subject": subject,
            "body": body,
            "channel": channel_label,
            "audience": payload.get("audience") or "Všichni uživatelé",
            "immediate": True,
            "recipients": n_users,
            "delivered": delivered,
            "failed": failed,
            "error": "; ".join(errors) if errors else "—",
        },
        actor,
    )
    return item


_APP_STARTED = time.time()
_OPS_SAMPLES: deque[dict[str, Any]] = deque(maxlen=4000)


def record_api_sample(ms: float, ok: bool = True) -> None:
    _OPS_SAMPLES.append({"t": time.time(), "ms": max(0, int(round(ms))), "ok": bool(ok)})


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _dur(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    secs = int(max(0, seconds))
    if secs < 60:
        return f"{secs}s"
    minutes, secs = divmod(secs, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _job_secs(job: dict[str, Any]) -> float | None:
    start = _parse_iso(job.get("started_at"))
    end = _parse_iso(job.get("finished_at")) or start
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds())


def _uptime_pct(ok: bool, samples: list[dict[str, Any]]) -> str:
    if not samples:
        return "100%" if ok else "0%"
    good = sum(1 for item in samples if item.get("ok"))
    return f"{round(100 * good / max(len(samples), 1), 2):.2f}%"


def _api_series() -> list[int]:
    now = time.time()
    buckets: list[list[int]] = [[] for _ in range(24)]
    for item in _OPS_SAMPLES:
        ago = now - float(item.get("t") or 0)
        if ago < 0 or ago >= 86400:
            continue
        index = min(23, 23 - int(ago // 3600))
        buckets[index].append(int(item.get("ms") or 0))
    last = 0
    out = []
    for bucket in buckets:
        if bucket:
            last = int(sum(bucket) / len(bucket))
        out.append(last)
    return out


def _err_series(store: Store) -> tuple[list[float], float]:
    from app import analytics as stats

    start = stats._since(30)
    fail_days: Counter[str] = Counter()
    try:
        with store.connect() as conn:
            for row in conn.execute(
                "SELECT substr(created_at, 1, 10) d, COUNT(*) n FROM analytics_events WHERE kind = ? AND created_at >= ? GROUP BY d",
                (stats.KIND_NOTIFY_FAIL, start),
            ):
                fail_days[str(row[0])] += int(row[1])
            for row in conn.execute(
                "SELECT substr(COALESCE(processed_at, created_at), 1, 10) d, COUNT(*) n FROM ping_queue WHERE status IN ('failed','error') AND COALESCE(processed_at, created_at) >= ? GROUP BY d",
                (start,),
            ):
                fail_days[str(row[0])] += int(row[1])
            for row in conn.execute(
                "SELECT substr(COALESCE(finished_at, started_at), 1, 10) d, COUNT(*) n FROM scrape_jobs WHERE last_error IS NOT NULL AND last_error != '' AND COALESCE(finished_at, started_at) >= ? GROUP BY d",
                (start,),
            ):
                fail_days[str(row[0])] += int(row[1])
    except Exception:
        pass
    points = []
    today = datetime.now(timezone.utc).date()
    for i in range(29, -1, -1):
        key = (today - timedelta(days=i)).isoformat()
        points.append(float(fail_days.get(key, 0)))
    hour = [item for item in _OPS_SAMPLES if time.time() - float(item.get("t") or 0) < 3600]
    now_pct = round(100 * sum(1 for item in hour if not item.get("ok")) / max(len(hour), 1), 2) if hour else 0.0
    return points, now_pct


def _next_in(last_iso: str | None, interval_sec: int) -> str:
    last = _parse_iso(last_iso)
    if not last:
        return "—"
    nxt = last + timedelta(seconds=max(1, interval_sec))
    delta = (nxt - datetime.now(timezone.utc)).total_seconds()
    if delta <= 0:
        return "teď"
    return f"za {_dur(delta)}"


def ops_payload(store: Store, hub: Any) -> dict[str, Any]:
    status = hub.status()
    jobs = store.list_scrape_jobs()
    catalog = store.catalog_sync_status()
    disc = user_account.discord_status(store)
    wa = wa_notify.status(store)
    t0 = time.perf_counter()
    db_ok = False
    try:
        with store.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        db_ok = False
    db_ms = int((time.perf_counter() - t0) * 1000)
    recent = [item for item in _OPS_SAMPLES if time.time() - float(item.get("t") or 0) < 3600]
    api_ms = int(sum(int(item.get("ms") or 0) for item in recent[-20:]) / max(len(recent[-20:]), 1)) if recent else 0
    live_jobs = [item for item in jobs if item.get("kind") == "monitor_live"]
    catalog_jobs = [item for item in jobs if item.get("kind") == "catalog_daily"]
    scraper_ok = bool(status.get("running"))
    secs_list = [s for s in (_job_secs(item) for item in live_jobs) if s is not None]
    ping_ms = 0
    with store.connect() as conn:
        ping_row = conn.execute(
            "SELECT created_at, processed_at FROM ping_queue WHERE processed_at IS NOT NULL ORDER BY processed_at DESC LIMIT 8"
        ).fetchall()
    ping_durs = []
    for row in ping_row:
        a = _parse_iso(row["created_at"])
        b = _parse_iso(row["processed_at"])
        if a and b:
            ping_durs.append((b - a).total_seconds() * 1000)
    if ping_durs:
        ping_ms = int(sum(ping_durs) / len(ping_durs))
    disc_ok = bool(disc.get("bot_ready") or disc.get("linked"))
    wa_ok = bool(wa.get("configured"))
    hour_samples = recent
    scrape_lat = _dur(sum(secs_list) / len(secs_list)) if secs_list else "—"
    services = [
        {"name": "API Server", "ok": True, "latency": f"{api_ms}ms" if api_ms else "—", "uptime": _uptime_pct(True, hour_samples)},
        {"name": "Databáze", "ok": db_ok, "latency": f"{db_ms}ms", "uptime": "100%" if db_ok else "0%"},
        {"name": "Scraper", "ok": scraper_ok, "latency": scrape_lat, "uptime": "100%" if scraper_ok else "0%"},
        {"name": "Discord Bot", "ok": disc_ok, "latency": f"{ping_ms}ms" if ping_ms else "—", "uptime": "100%" if disc_ok else "0%"},
        {"name": "WhatsApp Gateway", "ok": wa_ok, "latency": "—", "uptime": "100%" if wa_ok else "0%"},
    ]
    since_day = local_day_start()
    started_today = [item for item in jobs if (item.get("started_at") or "") >= since_day]
    avg_secs = [s for s in (_job_secs(item) for item in started_today or live_jobs) if s is not None]
    last_job = None
    for item in jobs:
        stamp = item.get("finished_at") or item.get("started_at")
        if stamp and (last_job is None or stamp > (last_job.get("finished_at") or last_job.get("started_at") or "")):
            last_job = item
    portals: dict[str, dict[str, Any]] = {}
    with store.connect() as conn:
        for row in conn.execute(
            "SELECT IFNULL(portal,'neznámý') AS portal, COUNT(*) n, MAX(last_seen) seen FROM catalog_listings WHERE IFNULL(gone,0)=0 GROUP BY portal"
        ):
            name = str(row["portal"] or "neznámý")
            portals[name] = {"name": name.title() if name != "sreality" else "Sreality", "n": int(row["n"]), "seen": row["seen"]}
    if "bezrealitky" in portals:
        portals["bezrealitky"]["name"] = "Bezrealitky"
    for item in live_jobs:
        portal = str(item.get("portal") or "")
        if portal not in portals:
            portals[portal] = {"name": portal.title(), "n": 0, "seen": item.get("finished_at")}
        portals[portal]["error"] = item.get("last_error")
        portals[portal]["job"] = item
    sources = []
    for key, row in portals.items():
        if not key:
            continue
        job = row.get("job") or {}
        sources.append(
            {
                "name": row["name"],
                "status": "Pozastavený" if row.get("error") else "Aktivní",
                "ok": not bool(row.get("error")),
                "n": row["n"],
                "ago": relative_cs(row.get("seen") or job.get("finished_at") or catalog.get("last_run")),
            }
        )
    sources.sort(key=lambda row: -int(row.get("n") or 0))
    logs: list[dict[str, Any]] = []
    if hub.last_error:
        logs.append({"at": fmt_dt(_now()), "at_iso": _now(), "level": "Error", "service": "API", "message": str(hub.last_error)[:180]})
    if catalog.get("last_error"):
        logs.append({"at": fmt_dt(catalog.get("last_run")), "at_iso": catalog.get("last_run") or "", "level": "Error", "service": "Scraper", "message": str(catalog.get("last_error"))[:180]})
    for item in jobs:
        stamp = item.get("finished_at") or item.get("started_at") or ""
        portal = (item.get("portal") or "Scraper").title()
        if item.get("last_error"):
            logs.append({"at": fmt_dt(stamp), "at_iso": stamp, "level": "Error", "service": "Scraper", "message": f"{portal}: {item.get('last_error')}"[:180]})
        elif item.get("status") == "done" or item.get("finished_at"):
            logs.append(
                {
                    "at": fmt_dt(stamp),
                    "at_iso": stamp,
                    "level": "Info",
                    "service": "Scraper",
                    "message": f"{portal}: běh dokončen, {item.get('upserts') or 0} aktualizací",
                }
            )
    with store.connect() as conn:
        for row in conn.execute(
            "SELECT processed_at, created_at, error, status FROM ping_queue WHERE status IN ('failed','error') ORDER BY COALESCE(processed_at, created_at) DESC LIMIT 40"
        ):
            logs.append(
                {
                    "at": fmt_dt(row["processed_at"] or row["created_at"]),
                    "at_iso": row["processed_at"] or row["created_at"] or "",
                    "level": "Error",
                    "service": "Discord",
                    "message": (row["error"] or "Webhook selhal")[:180],
                }
            )
    for item in store.list_monitors():
        if item.get("last_error"):
            logs.append(
                {
                    "at": fmt_dt(item.get("last_check")),
                    "at_iso": item.get("last_check") or "",
                    "level": "Warning",
                    "service": "Scraper",
                    "message": f"{item.get('name')}: {item.get('last_error')}"[:180],
                }
            )
    logs.sort(key=lambda row: row.get("at_iso") or "", reverse=True)
    seen_msg: set[str] = set()
    unique_logs = []
    for row in logs:
        key = f"{row.get('at_iso')}|{row.get('message')}"
        if key in seen_msg:
            continue
        seen_msg.add(key)
        unique_logs.append(row)
    logs = unique_logs[:200]
    interval = int(config.POLL_INTERVAL_SEC)
    cron = []
    by_portal: dict[str, dict[str, Any]] = {}
    for item in live_jobs:
        portal = str(item.get("portal") or "scraper")
        stamp = item.get("finished_at") or item.get("started_at") or ""
        if not stamp:
            continue
        prev = by_portal.get(portal)
        if not prev or stamp > (prev.get("finished_at") or prev.get("started_at") or ""):
            by_portal[portal] = item
    titles = {"sreality": "Sreality", "bezrealitky": "Bezrealitky"}
    seen_portals = set(by_portal) | {key for key in portals if key in titles}
    for portal in sorted(seen_portals, key=lambda key: titles.get(key, key)):
        title = titles.get(portal, portal.title())
        item = by_portal.get(portal)
        if not item:
            cron.append(
                {
                    "name": f"Scraper - {title}",
                    "interval": f"každých {interval}s" if interval < 60 else f"každých {max(1, interval // 60)} min",
                    "last": relative_short(catalog.get("last_run")),
                    "duration": "—",
                    "status": "OK" if scraper_ok else "Stojí",
                    "ok": scraper_ok,
                    "next": _next_in(catalog.get("last_run"), interval),
                }
            )
            continue
        secs = _job_secs(item)
        cron.append(
            {
                "name": f"Scraper - {titles.get(portal, portal.title())}",
                "interval": f"každých {interval}s" if interval < 60 else f"každých {max(1, interval // 60)} min",
                "last": relative_short(item.get("finished_at") or item.get("started_at")),
                "duration": _dur(secs),
                "status": "Error" if item.get("last_error") else "OK",
                "ok": not bool(item.get("last_error")),
                "next": _next_in(item.get("finished_at") or item.get("started_at"), interval),
            }
        )
    cat_secs = [_job_secs(item) for item in catalog_jobs]
    cat_secs_n = [s for s in cat_secs if s is not None]
    cron.append(
        {
            "name": "Katalog — denní agregace",
            "interval": f"denně {config.CATALOG_SYNC_HOUR}:00",
            "last": relative_short(catalog.get("last_run")),
            "duration": _dur(sum(cat_secs_n) / len(cat_secs_n)) if cat_secs_n else "—",
            "status": "Error" if catalog.get("last_error") else ("OK" if catalog.get("status") != "running" else "Běží"),
            "ok": not bool(catalog.get("last_error")),
            "next": f"zítra {config.CATALOG_SYNC_HOUR}:00",
        }
    )
    last_ping = ping_row[0]["processed_at"] if ping_row else None
    cron.append(
        {
            "name": "Notifikace - batch send",
            "interval": f"každých {interval}s" if interval < 60 else f"každých {max(1, interval // 60)} min",
            "last": relative_short(last_ping),
            "duration": _dur((ping_ms / 1000) if ping_ms else None),
            "status": "OK" if disc_ok else "Stojí",
            "ok": disc_ok,
            "next": _next_in(last_ping, interval),
        }
    )
    api_points = _api_series()
    err_points, err_now = _err_series(store)
    return {
        "services": services,
        "api_series": {"source": LIVE, "note": "", "points": api_points, "labels": ["00:00", "08:00", "16:00", "24:00"]},
        "err_series": {"source": LIVE, "note": "", "points": err_points, "now": f"{err_now}%", "labels": ["Před 30 dny", "Před 15 dny", "Dnes"]},
        "scraper": {
            "runs_today": live(_spc(len(started_today))),
            "new_today": live(_spc(store.new_today_count())),
            "updated": live(_spc(sum(int(item.get("upserts") or 0) for item in started_today))),
            "expired": live(
                _spc(
                    _count_sql(
                        store,
                        "SELECT COUNT(*) FROM catalog_listings WHERE IFNULL(gone,0)=1 AND COALESCE(last_seen,'') >= ?",
                        (since_day,),
                    )
                )
            ),
            "avg": live(_dur(sum(avg_secs) / len(avg_secs)) if avg_secs else "—"),
            "last": live(relative_cs((last_job or {}).get("finished_at") or catalog.get("last_run"))),
        },
        "sources": sources,
        "logs": logs,
        "jobs": cron,
        "running": bool(status.get("running")),
        "checking": bool(status.get("checking")),
        "uptime_sec": int(time.time() - _APP_STARTED),
    }


def billing_payload(store: Store) -> dict[str, Any]:
    finance = stripe_billing.admin_finance(store)
    return {
        "configured": finance.get("configured"),
        "error": finance.get("error") or "",
        "kpis": [
            {
                "label": "MRR (měsíční opakovaný příjem)",
                "value": f"{finance.get('mrr', 0):,}".replace(",", " ") + " Kč",
                "source": LIVE,
                "delta": finance.get("mrr_delta") or "",
                "delta_tone": "down" if finance.get("mrr_tone") == "down" else "",
            },
            {
                "label": "ARR (roční opakovaný příjem)",
                "value": f"{finance.get('arr', 0):,}".replace(",", " ") + " Kč",
                "source": LIVE,
                "delta": finance.get("mrr_delta") or "",
                "delta_tone": "down" if finance.get("mrr_tone") == "down" else "",
            },
            {
                "label": "Předpoklad do konce měsíce",
                "value": f"{finance.get('forecast', 0):,}".replace(",", " ") + " Kč",
                "source": LIVE,
                "hint": f"zbývá {finance.get('days_left', 0)} dní",
            },
            {
                "label": "Celkový příjem od spuštění",
                "value": f"{finance.get('lifetime', 0):,}".replace(",", " ") + " Kč",
                "source": LIVE,
            },
        ],
        "split": finance.get("split") or [],
        "revenue": finance.get("revenue") or {"points": [], "labels": [], "max": 0},
        "conversion": finance.get("conversion") or [],
        "retention": finance.get("retention") or [],
        "plans": finance.get("plans") or [],
        "transactions": finance.get("transactions") or [],
    }


def _promo_program(store: Store) -> dict[str, Any]:
    data = _json_meta(store, "admin_promo_program", {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("campaigns", [])
    data.setdefault("influencers", [])
    data.setdefault("payouts", {})
    return data


def _save_promo_program(store: Store, data: dict[str, Any]) -> None:
    _set_json_meta(store, "admin_promo_program", data)


def _promo_matches(row: dict[str, Any], keys: set[str]) -> bool:
    code = str(row.get("code") or "").upper()
    return bool(keys & {code, str(row.get("id") or ""), str(row.get("coupon_id") or "")})


def _month_options() -> list[dict[str, str]]:
    now = datetime.now()
    labels = ["Leden", "Únor", "Březen", "Duben", "Květen", "Červen", "Červenec", "Srpen", "Září", "Říjen", "Listopad", "Prosinec"]
    rows = []
    for i in range(11, -1, -1):
        month = now.month - i
        year = now.year
        while month <= 0:
            month += 12
            year -= 1
        rows.append({"id": f"{year:04d}-{month:02d}", "label": f"{labels[month - 1]} {year}"})
    return rows


def promo_payload(store: Store, month: str = "") -> dict[str, Any]:
    program = _promo_program(store)
    codes = stripe_billing.list_promotion_codes()
    invoices = stripe_billing.promo_invoice_stats()
    months = _month_options()
    month = month or months[-1]["id"]
    by_code = {str(item.get("code") or "").upper(): item for item in codes}

    def uses(item: dict[str, Any], since: int = 0, until: int = 0) -> list[dict[str, Any]]:
        out = []
        for inv in invoices:
            if since and int(inv.get("at") or 0) < since:
                continue
            if until and int(inv.get("at") or 0) >= until:
                continue
            if _promo_matches(item, inv.get("keys") or set()):
                out.append(inv)
        return out

    discount_rows = []
    for item in codes:
        if item.get("kind") == "influencer":
            continue
        discount_rows.append(
            {
                "code": item["code"],
                "discount": item.get("discount") or 0,
                "from": item.get("from") or "—",
                "to": item.get("to") or "neurčito",
                "used": item.get("used") or 0,
                "on": bool(item.get("on")),
            }
        )

    campaigns = []
    for camp in program.get("campaigns") or []:
        code = str(camp.get("code") or "").upper()
        stripe_row = by_code.get(code) or {"code": code, "id": "", "coupon_id": ""}
        paid_inv = uses(stripe_row)
        customers = {inv.get("customer") for inv in paid_inv if inv.get("customer")}
        paid = len(paid_inv)
        regs = max(len(customers), paid)
        conv = f"{round(100 * paid / regs, 1)} %".replace(".", ",") if regs else "—"
        campaigns.append(
            {
                "name": camp.get("name") or code,
                "period": f"{camp.get('from') or '—'} – {camp.get('to') or '—'}",
                "code": code,
                "regs": regs,
                "paid": paid,
                "conv": conv,
            }
        )
    for item in codes:
        if item.get("kind") != "campaign":
            continue
        if any(str(c.get("code") or "").upper() == item["code"] for c in campaigns):
            continue
        paid_inv = uses(item)
        customers = {inv.get("customer") for inv in paid_inv if inv.get("customer")}
        paid = len(paid_inv) or int(item.get("used") or 0)
        regs = max(len(customers), paid)
        campaigns.append(
            {
                "name": item.get("name") or item["code"],
                "period": f"{item.get('from')} – {item.get('to')}",
                "code": item["code"],
                "regs": regs,
                "paid": paid,
                "conv": f"{round(100 * paid / regs, 1)} %".replace(".", ",") if regs else "—",
            }
        )

    influencers = []
    seen_inf: set[str] = set()
    merged = list(program.get("influencers") or [])
    for item in codes:
        if item.get("kind") != "influencer":
            continue
        if any(str(row.get("code") or "").upper() == item["code"] for row in merged):
            continue
        merged.append({"name": item.get("name") or item["code"], "code": item["code"], "cut": item.get("cut") or 0})
    payouts_map = program.get("payouts") if isinstance(program.get("payouts"), dict) else {}
    y, m = [int(part) for part in month.split("-")]
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    if m == 12:
        end = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(y, m + 1, 1, tzinfo=timezone.utc)
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())
    payout_rows = []
    month_regs = 0
    month_paid = 0
    month_due = 0
    for person in merged:
        code = str(person.get("code") or "").upper()
        if not code or code in seen_inf:
            continue
        seen_inf.add(code)
        stripe_row = by_code.get(code) or {"code": code, "id": "", "coupon_id": "", "used": 0}
        cut = int(person.get("cut") or stripe_row.get("cut") or 0)
        all_inv = uses(stripe_row)
        month_inv = uses(stripe_row, start_ts, end_ts)
        paid_n = len(all_inv) or int(stripe_row.get("used") or 0)
        regs = max(len({inv.get("customer") for inv in all_inv if inv.get("customer")}), paid_n)
        due_cents = int(round(sum(int(inv.get("amount") or 0) for inv in all_inv) * cut / 100))
        paid_out = int(person.get("paid_cents") or 0)
        debt = max(0, due_cents - paid_out)
        influencers.append(
            {
                "name": person.get("name") or code,
                "code": code,
                "cut": cut,
                "regs": regs,
                "paid": paid_n,
                "debt": f"{due_cents // 100:,}".replace(",", " ") + " Kč" if due_cents else "0 Kč",
            }
        )
        m_paid = len(month_inv)
        m_regs = max(len({inv.get("customer") for inv in month_inv if inv.get("customer")}), m_paid)
        m_due = int(round(sum(int(inv.get("amount") or 0) for inv in month_inv) * cut / 100))
        settled = bool((payouts_map.get(code) or {}).get(month))
        payout_rows.append(
            {
                "name": person.get("name") or code,
                "code": code,
                "regs": m_regs,
                "paid": m_paid,
                "cut": f"{cut} %",
                "amount": f"{m_due // 100:,}".replace(",", " ") + " Kč",
                "status": "VYPLACENO" if settled else "NEVYPLACENO",
                "ok": settled,
            }
        )
        month_regs += m_regs
        month_paid += m_paid
        month_due += m_due

    now = time.time()
    d30 = now - 30 * 86400
    d60 = now - 60 * 86400
    def bucket(kind: str, since: float, until: float | None = None) -> tuple[int, int]:
        regs = 0
        paid = 0
        for item in codes:
            if kind == "promo" and item.get("kind") == "influencer":
                continue
            if kind == "inf" and item.get("kind") != "influencer":
                continue
            if kind == "all":
                pass
            rows = uses(item, int(since), int(until or now + 1))
            paid += len(rows)
            regs += len({inv.get("customer") for inv in rows if inv.get("customer")}) or len(rows)
        return regs, paid

    promo_regs, promo_paid = bucket("promo", d30)
    inf_regs, inf_paid = bucket("inf", d30)
    prev_promo, prev_promo_paid = bucket("promo", d60, d30)
    prev_inf, prev_inf_paid = bucket("inf", d60, d30)
    from app import analytics as stats

    signups = stats.count_kind(store, stats.KIND_SIGNUP, stats._since(30))
    signups_prev = stats.count_kind(store, stats.KIND_SIGNUP, stats._since(60), stats._since(30))
    visitors = stats.unique_visitors(store, stats._since(30))
    visitors_prev = stats.unique_visitors(store, stats._since(60), stats._since(30))
    attributed = promo_regs + inf_regs
    organic = max(0, (signups or visitors) - attributed)
    organic_prev = max(0, (signups_prev or visitors_prev) - (prev_promo + prev_inf))
    direct_paid = max(0, len([inv for inv in invoices if int(inv.get("at") or 0) >= d30 and not inv.get("keys")]) )
    def delta(now_n: int, prev_n: int) -> str:
        if prev_n <= 0:
            return "+100 %" if now_n else "0 %"
        change = (now_n - prev_n) / prev_n * 100
        sign = "+" if change >= 0 else ""
        return f"{sign}{change:.0f} %".replace(".", ",")

    sources = [
        {"label": "Organické", "delta": delta(organic, organic_prev), "n": organic, "sub": f"Registrace + {organic} předplatných" if organic else "Bez přiřazeného kódu"},
        {"label": "Promo kódy", "delta": delta(promo_regs, prev_promo), "n": promo_regs, "sub": f"Registrace + {promo_paid} předplatných"},
        {"label": "Influenceři", "delta": delta(inf_regs, prev_inf), "n": inf_regs, "sub": f"Registrace + {inf_paid} předplatných"},
        {"label": "Přímý odkaz", "delta": delta(direct_paid, 0), "n": direct_paid, "sub": f"Platby bez slevového kódu"},
    ]
    return {
        "configured": bool(config.STRIPE_SECRET_KEY),
        "codes": discount_rows,
        "campaigns": campaigns,
        "influencers": influencers,
        "payouts": payout_rows,
        "payout_total": {"regs": month_regs, "paid": month_paid, "amount": f"{month_due // 100:,}".replace(",", " ") + " Kč"},
        "months": months,
        "month": month,
        "month_label": next((row["label"] for row in months if row["id"] == month), month),
        "sources": sources,
    }


def toggle_promo_code(store: Store, code: str, on: bool) -> dict[str, Any]:
    stripe_billing.set_promotion_active(code, on)
    _audit(store, f"{'zapnul' if on else 'vypnul'} slevový kód {code.upper()}", "Admin")
    return promo_payload(store)


def create_promo_code(store: Store, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    kind = str(payload.get("kind") or "discount")
    code = str(payload.get("code") or "")
    percent = int(payload.get("discount") or payload.get("percent") or 0)
    name = str(payload.get("name") or "")
    cut = int(payload.get("cut") or 0)
    if kind == "influencer" and percent < 1:
        percent = max(cut, 10)
    valid_from = str(payload.get("from") or "")
    valid_to = str(payload.get("to") or "")
    stripe_billing.create_promotion_code(code, percent, valid_from=valid_from, valid_to=valid_to, kind=kind, name=name, cut=cut)
    program = _promo_program(store)
    if kind == "campaign":
        program["campaigns"].insert(0, {"name": name or code.upper(), "code": code.upper(), "from": valid_from, "to": valid_to})
        _save_promo_program(store, program)
        _audit(store, f"vytvořil promo akci {name or code}", actor)
    elif kind == "influencer":
        program["influencers"].insert(0, {"name": name or code.upper(), "code": code.upper(), "cut": cut, "paid_cents": 0})
        _save_promo_program(store, program)
        _audit(store, f"přidal influencera {name or code}", actor)
    else:
        _audit(store, f"vytvořil slevový kód {code.upper()}", actor)
    return promo_payload(store)


def mark_promo_payout(store: Store, code: str, month: str, paid: bool) -> dict[str, Any]:
    program = _promo_program(store)
    payouts = program.get("payouts") if isinstance(program.get("payouts"), dict) else {}
    code = (code or "").upper()
    row = payouts.get(code) if isinstance(payouts.get(code), dict) else {}
    row[month] = bool(paid)
    payouts[code] = row
    program["payouts"] = payouts
    _save_promo_program(store, program)
    _audit(store, f"{'vyplatil' if paid else 'zrušil výplatu'} {code} za {month}", "Admin")
    return promo_payload(store, month)


def apply_action(store: Store, action: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    action = (action or "").strip()
    if action == "block":
        _set_json_meta(store, "admin_user_blocked", True)
        _audit(store, f"zablokoval účet {user_account.public_account(store).get('email')}", actor)
        return {"ok": True}
    if action == "unblock":
        _set_json_meta(store, "admin_user_blocked", False)
        _audit(store, "odblokoval účet", actor)
        return {"ok": True}
    if action == "unlink_discord":
        user_account.unlink_discord(store)
        _audit(store, "odpojil Discord", actor)
        return {"ok": True}
    if action == "unlink_whatsapp":
        user_account.save_whatsapp_phone(store, "")
        _audit(store, "odpojil WhatsApp", actor)
        return {"ok": True}
    if action == "set_role":
        role = user_account.set_account_role(store, str(payload.get("role") or ""))
        label = "admin" if role == "admin" else "běžný uživatel"
        _audit(store, f"nastavil roli na {label}", actor)
        return {"ok": True, "role": role}
    if action == "set_plan":
        plan_id = str(payload.get("plan") or "").strip()
        charge = bool(payload.get("charge"))
        account = user_account.public_account(store)
        result = stripe_billing.admin_set_plan(store, plan_id, charge, account.get("email") or "")
        mode = "se stržením platby" if charge else "bez platby"
        _audit(store, f"nastavil tarif {stripe_billing.PLANS.get(plan_id, {}).get('label', plan_id)} ({mode})", actor)
        return result
    if action == "watch_limit":
        try:
            limit = int(payload.get("limit"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Zadej číslo limitu") from exc
        store.set_meta("admin_watch_limit", str(max(0, limit)))
        _audit(store, f"upravil limit monitorů na {limit}", actor)
        return {"ok": True, "limit": limit}
    if action == "toggle_monitor":
        monitor_id = str(payload.get("id") or "")
        item = store.get_monitor(monitor_id)
        if not item:
            raise ValueError("Monitor neexistuje")
        store.save_monitor({**item, "enabled": not item.get("enabled")})
        _audit(store, f"{'pozastavil' if item.get('enabled') else 'aktivoval'} monitor {item.get('name')}", actor)
        return {"ok": True}
    if action == "reset_password":
        _audit(store, "reset hesla není dokončený (chybí e-mailový tok)", actor)
        raise ValueError("Reset hesla e-mailem ještě není napojený")
    if action == "delete_user":
        _audit(store, "GDPR smazání účtu — akce je zablokovaná v této instanci", actor)
        raise ValueError("Smazání účtu je v lokální instanci vypnuté")
    raise ValueError("Neznámá akce")
