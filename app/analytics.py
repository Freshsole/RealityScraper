from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from app import account as user_account
from app.store import Store

VISITOR_COOKIE = "rf_vid"
CONSENT_COOKIE = "rf_consent"
KIND_PAGE = "pageview"
KIND_SIGNUP = "signup"
KIND_MONITOR = "monitor"
KIND_DISCORD = "discord"
KIND_NOTIFY = "notify"
KIND_NOTIFY_FAIL = "notify_fail"

TZ_GEO: dict[str, tuple[str, str, str]] = {
    "Europe/Prague": ("CZ", "Česká republika", "Praha"),
    "Europe/Bratislava": ("SK", "Slovensko", "Bratislava"),
    "Europe/Berlin": ("DE", "Německo", "Berlín"),
    "Europe/Vienna": ("AT", "Rakousko", "Vídeň"),
    "Europe/Warsaw": ("PL", "Polsko", "Varšava"),
    "Europe/Budapest": ("HU", "Maďarsko", "Budapešť"),
    "Europe/Zurich": ("CH", "Švýcarsko", "Curych"),
    "Europe/London": ("GB", "Velká Británie", "Londýn"),
    "Europe/Paris": ("FR", "Francie", "Paříž"),
    "Europe/Amsterdam": ("NL", "Nizozemsko", "Amsterdam"),
    "Europe/Brussels": ("BE", "Belgie", "Brusel"),
    "Europe/Madrid": ("ES", "Španělsko", "Madrid"),
    "Europe/Rome": ("IT", "Itálie", "Řím"),
    "Europe/Lisbon": ("PT", "Portugalsko", "Lisabon"),
    "America/New_York": ("US", "USA", "New York"),
    "America/Los_Angeles": ("US", "USA", "Los Angeles"),
    "America/Chicago": ("US", "USA", "Chicago"),
}

COUNTRY_NAMES = {
    "CZ": "Česká republika",
    "SK": "Slovensko",
    "DE": "Německo",
    "AT": "Rakousko",
    "PL": "Polsko",
    "HU": "Maďarsko",
    "GB": "Velká Británie",
    "US": "USA",
    "FR": "Francie",
    "IT": "Itálie",
    "ES": "Španělsko",
    "NL": "Nizozemsko",
    "CH": "Švýcarsko",
    "BE": "Belgie",
    "UA": "Ukrajina",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_device(ua: str, width: int | None = None) -> str:
    text = (ua or "").lower()
    if "ipad" in text or "tablet" in text or "kindle" in text:
        return "tablet"
    if "mobile" in text or "android" in text or "iphone" in text:
        return "mobil"
    if width is not None and width < 768:
        return "mobil"
    if width is not None and width < 1100:
        return "tablet"
    return "desktop"


def geo_from(tz: str, lang: str, header_country: str) -> tuple[str, str, str]:
    code = (header_country or "").strip().upper()[:2]
    if code in COUNTRY_NAMES:
        city = TZ_GEO.get(tz, ("", "", ""))[2] if TZ_GEO.get(tz, ("", "", ""))[0] == code else ""
        return code, COUNTRY_NAMES[code], city or TZ_GEO.get(tz, ("", "", ""))[2]
    if tz in TZ_GEO:
        return TZ_GEO[tz]
    lang = (lang or "").lower()
    if lang.startswith("cs"):
        return "CZ", COUNTRY_NAMES["CZ"], "Praha"
    if lang.startswith("sk"):
        return "SK", COUNTRY_NAMES["SK"], "Bratislava"
    if lang.startswith("de"):
        return "DE", COUNTRY_NAMES["DE"], ""
    return "", "Neznámé", ""


def ensure_schema(store: Store) -> None:
    with store.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                visitor_id TEXT,
                path TEXT,
                device TEXT,
                country TEXT,
                country_name TEXT,
                city TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_kind_at ON analytics_events(kind, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_vid ON analytics_events(visitor_id, created_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_presence (
                visitor_id TEXT PRIMARY KEY,
                last_seen TEXT NOT NULL,
                path TEXT,
                device TEXT,
                country TEXT,
                country_name TEXT,
                city TEXT,
                email TEXT,
                name TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_presence_seen ON analytics_presence(last_seen)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(analytics_presence)")}
        if "ip" not in cols:
            conn.execute("ALTER TABLE analytics_presence ADD COLUMN ip TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_analytics_presence_ip ON analytics_presence(ip)")


def track(
    store: Store,
    kind: str,
    *,
    visitor_id: str = "",
    path: str = "",
    device: str = "",
    country: str = "",
    country_name: str = "",
    city: str = "",
    created_at: str | None = None,
) -> None:
    ensure_schema(store)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO analytics_events(created_at, kind, visitor_id, path, device, country, country_name, city)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (created_at or _now(), kind, visitor_id, path[:240], device, country, country_name, city),
        )


def backfill(store: Store) -> None:
    ensure_schema(store)
    if store.get_meta("analytics_backfill") == "2":
        return
    data = user_account.account_record(store)
    email = (data.get("email") or "").strip()
    created = data.get("created_at") or ""
    if email and created:
        with store.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM analytics_events WHERE kind = ? LIMIT 1",
                (KIND_SIGNUP,),
            ).fetchone()
        if not exists:
            track(store, KIND_SIGNUP, path="/registrace", created_at=created, visitor_id="local")
    with store.connect() as conn:
        has_mon = conn.execute("SELECT 1 FROM analytics_events WHERE kind = ? LIMIT 1", (KIND_MONITOR,)).fetchone()
    if not has_mon:
        for item in store.list_monitors():
            track(
                store,
                KIND_MONITOR,
                path="/monitory",
                created_at=item.get("created_at") or _now(),
                visitor_id="local",
            )
    if data.get("discord_channel_id") or data.get("discord_webhook_url"):
        with store.connect() as conn:
            has_d = conn.execute("SELECT 1 FROM analytics_events WHERE kind = ? LIMIT 1", (KIND_DISCORD,)).fetchone()
        if not has_d:
            track(store, KIND_DISCORD, path="/nastaveni", created_at=data.get("discord_linked_at") or created or _now(), visitor_id="local")
    store.set_meta("analytics_backfill", "2")


PATH_LABELS = {
    "/": "Úvod",
    "/nabidka": "Databáze bytů",
    "/monitory": "Monitory",
    "/prehled": "Přehled",
    "/filtry": "Filtry",
    "/zprava": "Zpráva",
    "/nastaveni": "Nastavení",
    "/prihlaseni": "Přihlášení",
    "/registrace": "Registrace",
    "/heslo": "Obnova hesla",
    "/kontakt": "Kontakt",
    "/obchodni-podminky": "Obchodní podmínky",
    "/ochrana-soukromi": "Ochrana soukromí",
    "/nastaveni-cookies": "Nastavení cookies",
    "/uspechy": "Vaše úspěchy",
}


def path_label(path: str) -> str:
    raw = path or "/"
    if raw in PATH_LABELS:
        return PATH_LABELS[raw]
    if raw.startswith("/uspechy"):
        return "Vaše úspěchy"
    if raw.startswith("/nastaveni"):
        return "Nastavení"
    if raw.startswith("/nabidka"):
        return "Databáze bytů"
    return raw


def client_ip(request: Request) -> str:
    headers = request.headers
    raw = (
        headers.get("cf-connecting-ip")
        or headers.get("x-real-ip")
        or headers.get("x-forwarded-for")
        or (request.client.host if request.client else "")
        or ""
    )
    ip = raw.split(",")[0].strip()
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    if ip in {":1", "::1"}:
        ip = "127.0.0.1"
    return ip[:64]


def presence_key(ip: str, visitor_id: str) -> str:
    if ip:
        return f"ip:{ip}"
    return f"vid:{visitor_id}" if visitor_id else ""


def touch_presence(
    store: Store,
    *,
    visitor_id: str,
    ip: str = "",
    path: str = "",
    device: str = "",
    country: str = "",
    country_name: str = "",
    city: str = "",
    email: str = "",
    name: str = "",
) -> None:
    ip = (ip or "").strip()
    key = presence_key(ip, visitor_id)
    if not key:
        return
    ensure_schema(store)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO analytics_presence(visitor_id, last_seen, path, device, country, country_name, city, email, name, ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(visitor_id) DO UPDATE SET
                last_seen = excluded.last_seen,
                path = excluded.path,
                device = excluded.device,
                country = excluded.country,
                country_name = excluded.country_name,
                city = excluded.city,
                email = CASE WHEN excluded.email != '' THEN excluded.email ELSE analytics_presence.email END,
                name = CASE WHEN excluded.name != '' THEN excluded.name ELSE analytics_presence.name END,
                ip = excluded.ip
            """,
            (key, _now(), path[:240], device, country, country_name, city, email[:180], name[:120], ip),
        )
        if ip:
            conn.execute(
                "DELETE FROM analytics_presence WHERE ip = ? AND visitor_id != ?",
                (ip, key),
            )


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def ago_cs(iso: str) -> str:
    at = _parse_iso(iso)
    if not at:
        return "—"
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    seconds = int((datetime.now(timezone.utc) - at.astimezone(timezone.utc)).total_seconds())
    if seconds < 8:
        return "teď"
    if seconds < 60:
        return f"před {seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"před {minutes} min"
    return f"před {minutes // 60} h"


def live_visitors(store: Store, *, within: int = 90) -> dict[str, Any]:
    ensure_schema(store)
    since = (datetime.now(timezone.utc) - timedelta(seconds=within)).isoformat()
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT visitor_id, last_seen, path, device, country, country_name, city, email, name, ip
            FROM analytics_presence
            WHERE last_seen >= ?
            ORDER BY last_seen DESC
            LIMIT 80
            """,
            (since,),
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not _parse_iso(row["last_seen"] or ""):
            continue
        ip = (row["ip"] or "").strip()
        key = ip or (row["visitor_id"] or "")
        if not key:
            continue
        current = grouped.get(key)
        if current is None or (row["last_seen"] or "") > (current["at"] or ""):
            grouped[key] = {
                "id": key,
                "ip": ip,
                "email": (row["email"] or "").strip(),
                "name": (row["name"] or "").strip(),
                "path": row["path"] or "/",
                "device": row["device"] or "—",
                "city": row["city"] or "",
                "country": row["country"] or "",
                "at": row["last_seen"],
            }
        elif (row["email"] or "").strip() and not grouped[key]["email"]:
            grouped[key]["email"] = (row["email"] or "").strip()
            grouped[key]["name"] = (row["name"] or "").strip()
    visitors = []
    signed = 0
    for item in sorted(grouped.values(), key=lambda row: row["at"] or "", reverse=True):
        email = item["email"]
        if email:
            signed += 1
        visitors.append(
            {
                "id": item["id"],
                "ip": item["ip"],
                "email": email,
                "name": item["name"],
                "signed_in": bool(email),
                "path": item["path"],
                "page": path_label(item["path"]),
                "device": item["device"],
                "city": item["city"],
                "country": item["country"],
                "place": ", ".join(p for p in [item["city"].strip(), item["country"].strip()] if p) or "—",
                "at": item["at"],
                "ago": ago_cs(item["at"]),
            }
        )
    return {
        "n": len(visitors),
        "signed_in": signed,
        "guests": len(visitors) - signed,
        "visitors": visitors,
    }


def analytics_allowed(request: Request) -> bool:
    raw = (request.cookies.get(CONSENT_COOKIE) or "").strip()
    if not raw:
        return True
    try:
        data = json.loads(raw)
        if "a" in data:
            return bool(data.get("a"))
        if "analytics" in data:
            return bool(data.get("analytics"))
    except Exception:
        pass
    return "a=0" not in raw and '"a":0' not in raw


def ingest(store: Store, request: Request, payload: dict[str, Any] | None) -> str:
    if not analytics_allowed(request):
        return ""
    body = payload or {}
    path = str(body.get("path") or request.headers.get("referer") or "")
    if "://" in path:
        try:
            from urllib.parse import urlparse

            path = urlparse(path).path or "/"
        except Exception:
            path = "/"
    path = path or "/"
    if path.startswith("/api") or path.startswith("/static") or path.startswith("/admin"):
        return request.cookies.get(VISITOR_COOKIE) or ""
    ua = str(body.get("ua") or request.headers.get("user-agent") or "")
    try:
        width = int(body.get("w") or 0) or None
    except (TypeError, ValueError):
        width = None
    device = parse_device(ua, width)
    header_cc = str(
        request.headers.get("cf-ipcountry")
        or request.headers.get("x-vercel-ip-country")
        or request.headers.get("x-country-code")
        or ""
    )
    country, country_name, city = geo_from(str(body.get("tz") or ""), str(body.get("lang") or ""), header_cc)
    if body.get("city"):
        city = str(body.get("city"))[:80]
    visitor = (request.cookies.get(VISITOR_COOKIE) or str(body.get("vid") or "") or secrets.token_hex(8))[:32]
    user = user_account.user_from_session(store, request.cookies.get(user_account.SESSION_COOKIE))
    touch_presence(
        store,
        visitor_id=visitor,
        ip=client_ip(request),
        path=path,
        device=device,
        country=country,
        country_name=country_name,
        city=city,
        email=(user or {}).get("email") or "",
        name=(user or {}).get("name") or "",
    )
    if body.get("heartbeat"):
        return visitor
    ensure_schema(store)
    with store.connect() as conn:
        recent = conn.execute(
            """
            SELECT 1 FROM analytics_events
            WHERE kind = ? AND visitor_id = ? AND path = ? AND created_at >= ?
            LIMIT 1
            """,
            (KIND_PAGE, visitor, path, (datetime.now(timezone.utc) - timedelta(seconds=4)).isoformat()),
        ).fetchone()
    if recent:
        return visitor
    track(
        store,
        KIND_PAGE,
        visitor_id=visitor,
        path=path,
        device=device,
        country=country,
        country_name=country_name,
        city=city,
    )
    return visitor


def attach_cookie(response: Response, visitor: str) -> None:
    if not visitor:
        return
    response.set_cookie(VISITOR_COOKIE, visitor, max_age=60 * 60 * 24 * 400, samesite="lax", path="/")


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _day_start(days_ago: int = 0) -> str:
    local = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return local.astimezone().isoformat()


def unique_visitors(store: Store, since: str, until: str | None = None, *, kind: str = KIND_PAGE) -> int:
    sql = "SELECT COUNT(DISTINCT visitor_id) FROM analytics_events WHERE kind = ? AND created_at >= ? AND visitor_id IS NOT NULL AND visitor_id != ''"
    params: list[Any] = [kind, since]
    if until:
        sql += " AND created_at < ?"
        params.append(until)
    with store.connect() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def count_kind(store: Store, kind: str, since: str, until: str | None = None) -> int:
    sql = "SELECT COUNT(*) FROM analytics_events WHERE kind = ? AND created_at >= ?"
    params: list[Any] = [kind, since]
    if until:
        sql += " AND created_at < ?"
        params.append(until)
    with store.connect() as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def story_view_counts(store: Store) -> dict[str, int]:
    ensure_schema(store)
    counts: dict[str, int] = {}
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT path, COUNT(*) AS n
            FROM analytics_events
            WHERE kind = ? AND path LIKE '/uspechy/%'
            GROUP BY path
            """,
            (KIND_PAGE,),
        ).fetchall()
    for path, n in rows:
        parts = str(path or "").split("?")[0].rstrip("/").split("/")
        if len(parts) >= 3 and parts[1] == "uspechy" and parts[2]:
            slug = parts[2]
            counts[slug] = counts.get(slug, 0) + int(n or 0)
    return counts


def pageviews_by_day(store: Store, days: int = 30) -> list[int]:
    start = _since(days)
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n
            FROM analytics_events
            WHERE kind = ? AND created_at >= ?
            GROUP BY day
            ORDER BY day
            """,
            (KIND_PAGE, start),
        ).fetchall()
    by_day = {str(row["day"])[:10]: int(row["n"]) for row in rows}
    out: list[int] = []
    today = datetime.now().date()
    for i in range(days, -1, -1):
        key = (today - timedelta(days=i)).isoformat()
        out.append(by_day.get(key, 0))
    return out


def signups_by_day(store: Store, days: int = 30) -> list[int]:
    start = _since(days)
    with store.connect() as conn:
        rows = conn.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS n
            FROM analytics_events
            WHERE kind = ? AND created_at >= ?
            GROUP BY day
            """,
            (KIND_SIGNUP, start),
        ).fetchall()
    by_day = {str(row["day"])[:10]: int(row["n"]) for row in rows}
    out: list[int] = []
    today = datetime.now().date()
    for i in range(days, -1, -1):
        key = (today - timedelta(days=i)).isoformat()
        out.append(by_day.get(key, 0))
    return out


def breakdown(store: Store, column: str, since: str) -> list[tuple[str, int]]:
    if column not in {"country_name", "city", "device", "country"}:
        return []
    with store.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT {column} AS key, COUNT(DISTINCT visitor_id) AS n
            FROM analytics_events
            WHERE kind = ? AND created_at >= ? AND IFNULL({column}, '') != ''
            GROUP BY {column}
            ORDER BY n DESC
            LIMIT 8
            """,
            (KIND_PAGE, since),
        ).fetchall()
    return [(str(row["key"]), int(row["n"])) for row in rows]


def device_share(store: Store, since: str) -> dict[str, int]:
    rows = breakdown(store, "device", since)
    total = sum(n for _, n in rows) or 1
    out = {"desktop": 0, "mobil": 0, "tablet": 0}
    for key, n in rows:
        if key in out:
            out[key] = round(n / total * 100)
    return out


def fmt_int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def delta_pct(current: int, previous: int) -> str:
    if previous <= 0:
        return "+100%" if current else "0%"
    change = (current - previous) / previous * 100
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.1f}%"
