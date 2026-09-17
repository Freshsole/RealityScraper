from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from statistics import median
from typing import Any
from urllib.parse import quote, urlparse

from app import config
from app.billing import PLAN_RANK, billing_state
from app.identity import listing_key
from app.location_amenities import location_accessibility, warm_amenity_cache
from app.store import Store

_ID_RE = re.compile(r"/(\d+)/?(?:\?|#|$)")
_CITY_RE = re.compile(r"(praha(?:\s*-?\s*\d+)?|brno|ostrava|plzeň|plzen|olomouc|liberec|hradec|pardubice|české\s+budějovice|ceske\s+budejovice|zlin|zlín)", re.I)

TIER_LOW = "low"
TIER_MID = "mid"
TIER_HIGH = "high"

TIER_META = {
    TIER_LOW: {
        "key": TIER_LOW,
        "label": "Pod průměrem",
        "verdict": "Pod průměrem • Slabší inzerát",
        "color": "#e05a3e",
        "pill_bg": "#fff7f2",
        "pill_border": "#f4d2cb",
    },
    TIER_MID: {
        "key": TIER_MID,
        "label": "Dobrý deal",
        "verdict": "Dobrý deal • Nadprůměrný inzerát",
        "color": "#e0be3e",
        "pill_bg": "#fffbeb",
        "pill_border": "#f5e6a8",
    },
    TIER_HIGH: {
        "key": TIER_HIGH,
        "label": "Výborný deal",
        "verdict": "Výborný deal • Top inzerát",
        "color": "#7ed321",
        "pill_bg": "#f4faf0",
        "pill_border": "#c8e9a8",
    },
}


def _parse_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def extract_sreality_id(value: str | int | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if text.isdigit():
        return text
    match = _ID_RE.search(text)
    return match.group(1) if match else ""


def normalize_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    if text.startswith("/"):
        text = "https://www.sreality.cz" + text
    parsed = urlparse(text)
    if not parsed.scheme:
        text = "https://" + text.lstrip("/")
    return text.split("#")[0].split("?")[0].rstrip("/")


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _days_on_market(row: dict[str, Any]) -> int | None:
    for key in ("created_on", "first_seen", "last_seen"):
        dt = _parse_dt(row.get(key))
        if dt:
            return max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 86400))
    return None


def _price_m2(price: Any, area: Any) -> float | None:
    try:
        price_n = float(price)
        area_n = float(area)
    except (TypeError, ValueError):
        return None
    if price_n <= 0 or area_n <= 0:
        return None
    return price_n / area_n


_PEER_CACHE: dict[str, tuple[float, float | None]] = {}
_PEER_CACHE_TTL = 120.0
_SCORE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SCORE_CACHE_TTL = 60.0


def _peer_cache_get(key: str) -> float | None | object:
    hit = _PEER_CACHE.get(key)
    if not hit:
        return _MISSING
    at, value = hit
    if time.time() - at > _PEER_CACHE_TTL:
        return _MISSING
    return value


def _peer_cache_set(key: str, value: float | None) -> None:
    _PEER_CACHE[key] = (time.time(), value)
    if len(_PEER_CACHE) > 500:
        oldest = sorted(_PEER_CACHE.items(), key=lambda kv: kv[1][0])[:100]
        for k, _ in oldest:
            _PEER_CACHE.pop(k, None)


class _Missing:
    pass


_MISSING = _Missing()


def _is_rental(row: dict[str, Any]) -> bool:
    extras = row.get("extras") if isinstance(row.get("extras"), dict) else _parse_json(row.get("extras"))
    url = (row.get("url") or "").lower()
    label = (row.get("price_label") or "").lower()
    offer = str(extras.get("offer") or "")
    sale = offer == "Prodej" or "/prodej/" in url or "nemovitost" in label or "kč/ks" in label
    rent = offer == "Pronájem" or "/pronajem/" in url or "měsíc" in label or "mesic" in label
    if rent and not sale:
        return True
    if sale and not rent:
        return False
    # Heuristic: monthly rents are typically under ~150k Kč
    try:
        price = float(row.get("price_czk") or 0)
    except (TypeError, ValueError):
        price = 0
    return 0 < price < 150_000


def _locality_bucket(locality: str) -> str:
    text = (locality or "").strip().lower()
    match = _CITY_RE.search(text)
    if match:
        return re.sub(r"\s+", " ", match.group(1).lower())
    if not text:
        return ""
    parts = [p.strip() for p in re.split(r"[,–-]", text) if p.strip()]
    return parts[-1] if parts else text[:40]


def _agency_label(agency: str, extras: dict[str, Any]) -> str:
    value = (agency or "").strip()
    if not value:
        for key in ("agency", "seller", "company"):
            value = str(extras.get(key) or "").strip()
            if value:
                break
    if not value:
        return "—"
    folded = value.lower()
    if any(token in folded for token in ("soukrom", "privát", "privat", "majitel", "vlastník", "vlastnik")):
        return "Soukromé"
    return "Realitka"


def _trend_pct(row: dict[str, Any], history: list[dict[str, Any]] | None = None) -> float | None:
    try:
        price = float(row.get("price_czk") or 0)
    except (TypeError, ValueError):
        price = 0
    old = row.get("old_price_czk")
    try:
        old_n = float(old) if old not in (None, "") else 0
    except (TypeError, ValueError):
        old_n = 0
    if old_n > 0 and price > 0 and old_n != price:
        return round(((price - old_n) / old_n) * 100, 1)
    series = history or []
    prices = []
    for item in series:
        try:
            prices.append(float(item.get("price_czk")))
        except (TypeError, ValueError):
            continue
    if len(prices) >= 2 and prices[0] > 0:
        return round(((prices[-1] - prices[0]) / prices[0]) * 100, 1)
    discount = row.get("discount_pct")
    try:
        if discount not in (None, ""):
            return -abs(float(discount))
    except (TypeError, ValueError):
        pass
    return None


def _tier_for(score: int) -> dict[str, str]:
    if score < 50:
        return TIER_META[TIER_LOW]
    if score < 75:
        return TIER_META[TIER_MID]
    return TIER_META[TIER_HIGH]


def _price_vs_label(price_vs: float | None) -> str:
    if price_vs is None:
        return "—"
    if price_vs == 0:
        return "na průměru"
    direction = "pod" if price_vs < 0 else "nad"
    return f"{abs(price_vs):.0f}% {direction} průměrem"


def _format_czk(value: float | int | None) -> str:
    if value is None:
        return "—"
    n = int(round(value))
    return f"{n:,}".replace(",", " ") + " Kč"


def _format_m2(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{_format_czk(value)}/m²"


def _compact_k(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value / 1000:.0f}k Kč/m²"
    return _format_m2(value)


def _location_note(extras: dict[str, Any], locality: str) -> str:
    for key in ("transit", "metro", "transport", "dostupnost"):
        raw = extras.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    specs = extras.get("specs") or []
    if isinstance(specs, list):
        for spec in specs:
            if not isinstance(spec, dict):
                continue
            label = str(spec.get("label") or "").lower()
            if any(token in label for token in ("metro", "tramvaj", "doprav", "mhd")):
                value = str(spec.get("value") or "").strip()
                if value:
                    return value
    return locality.strip() if locality else ""


def _score_components(
    *,
    price_vs_pct: float | None,
    days: int | None,
    trend: float | None,
    agency_label: str,
) -> int:
    score = 55.0
    if price_vs_pct is not None:
        # Below market (negative %) is better.
        score += max(-28, min(28, -price_vs_pct * 1.4))
    if days is not None:
        if days <= 7:
            score += 12
        elif days <= 21:
            score += 6
        elif days <= 45:
            score += 0
        elif days <= 90:
            score -= 8
        else:
            score -= 16
    if trend is not None:
        if trend < 0:
            score += min(12, abs(trend) * 0.8)
        elif trend > 0:
            score -= min(12, trend * 0.8)
    if agency_label == "Soukromé":
        score += 4
    elif agency_label == "Realitka":
        score -= 2
    return int(max(0, min(100, round(score))))


def _catalog_detail_url(row: dict[str, Any]) -> str:
    base = (config.PUBLIC_BASE_URL or "https://realitify.cz").rstrip("/")
    url = str(row.get("url") or "").strip()
    key = str(row.get("listing_key") or row.get("canonical_key") or "").strip()
    native = extract_sreality_id(row.get("id") or row.get("native_id"))
    # Prefer full portal URL — most reliable for /api/catalog/item lookup
    if url and "://" in url:
        return f"{base}/nabidka?url={quote(url)}"
    if key:
        return f"{base}/nabidka?listing_key={quote(key)}"
    if native:
        return f"{base}/nabidka?id={quote(native)}"
    return f"{base}/nabidka"


def extension_account(store: Store) -> dict[str, Any]:
    return store._hot_json(
        store._hot_json_key("extension-account"),
        lambda: _extension_account_query(store),
        fresh_age=2.0,
    )


def _extension_account_query(store: Store) -> dict[str, Any]:
    billing = billing_state(store)
    plan = str(billing.get("plan") or "free")
    rank = PLAN_RANK.get(plan, 0)
    paid = rank >= PLAN_RANK["start"]
    is_pro = rank >= PLAN_RANK["pro"]
    account = store.get_meta("account") or "{}"
    try:
        data = json.loads(account) if isinstance(account, str) else (account or {})
    except json.JSONDecodeError:
        data = {}
    first = str(data.get("first") or "").strip()
    last = str(data.get("last") or "").strip()
    name = f"{first} {last}".strip() or str(data.get("email") or "").strip()
    return {
        "authenticated": bool(data.get("email")),
        "name": name,
        "email": str(data.get("email") or "").strip(),
        "plan": plan,
        "label": billing.get("label") or plan.upper(),
        "active": paid,
        "pro": is_pro,
        "login_url": f"{config.PUBLIC_BASE_URL.rstrip('/')}/prihlaseni",
        "pricing_url": f"{config.PUBLIC_BASE_URL.rstrip('/')}/#cenik",
        "app_url": f"{config.PUBLIC_BASE_URL.rstrip('/')}/app",
    }


def _row_from_sqlite(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["extras"] = _parse_json(data.get("extras"))
    return data


def _lookup_rows(store: Store, *, ids: list[str], urls: list[str]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    id_set = [extract_sreality_id(item) for item in ids]
    id_set = list(dict.fromkeys(item for item in id_set if item))
    url_map = {normalize_url(url): url for url in urls if normalize_url(url)}

    with store.read() as conn:
        if id_set:
            placeholders = ",".join("?" * len(id_set))
            for row in conn.execute(
                f"""
                SELECT * FROM listings
                WHERE CAST(id AS TEXT) IN ({placeholders})
                ORDER BY last_seen DESC
                """,
                id_set,
            ):
                data = _row_from_sqlite(row)
                native = extract_sreality_id(data.get("id"))
                if native and native not in found:
                    found[native] = data

            missing = [i for i in id_set if i not in found]
            if missing:
                placeholders = ",".join("?" * len(missing))
                for row in conn.execute(
                    f"""
                    SELECT * FROM catalog_listings
                    WHERE CAST(id AS TEXT) IN ({placeholders})
                      AND IFNULL(portal, '') IN ('sreality', '')
                    ORDER BY last_seen DESC
                    """,
                    missing,
                ):
                    data = _row_from_sqlite(row)
                    native = extract_sreality_id(data.get("id"))
                    if native and native not in found:
                        found[native] = data

            still = [i for i in id_set if i not in found]
            if still:
                placeholders = ",".join("?" * len(still))
                for link in conn.execute(
                    f"""
                    SELECT * FROM listing_links
                    WHERE native_id IN ({placeholders}) AND portal = 'sreality'
                    ORDER BY last_seen DESC
                    """,
                    still,
                ):
                    link_d = dict(link)
                    native = extract_sreality_id(link_d.get("native_id"))
                    if not native or native in found:
                        continue
                    canon = link_d.get("canonical_key") or link_d.get("url_key") or ""
                    row = None
                    if canon:
                        row = conn.execute(
                            """
                            SELECT * FROM listings
                            WHERE listing_key = ? OR canonical_key = ? OR url = ?
                            ORDER BY last_seen DESC LIMIT 1
                            """,
                            (canon, canon, link_d.get("url") or ""),
                        ).fetchone()
                        if row is None:
                            row = conn.execute(
                                """
                                SELECT * FROM catalog_listings
                                WHERE listing_key = ? OR canonical_key = ?
                                ORDER BY last_seen DESC LIMIT 1
                                """,
                                (canon, canon),
                            ).fetchone()
                    if row is not None:
                        data = _row_from_sqlite(row)
                        data["id"] = extract_sreality_id(data.get("id")) or native
                        found[native] = data
                    else:
                        found[native] = {
                            "id": native,
                            "url": link_d.get("url") or "",
                            "listing_key": link_d.get("url_key") or "",
                            "canonical_key": link_d.get("canonical_key") or "",
                            "agency": link_d.get("agency") or "",
                            "first_seen": link_d.get("last_seen") or "",
                            "last_seen": link_d.get("last_seen") or "",
                            "extras": {},
                            "_stub": True,
                        }

        for norm_url, original in url_map.items():
            key = listing_key(norm_url)
            native = extract_sreality_id(original) or extract_sreality_id(norm_url)
            if native and native in found:
                continue
            row = conn.execute(
                """
                SELECT * FROM listings
                WHERE listing_key = ? OR canonical_key = ? OR url = ?
                ORDER BY last_seen DESC LIMIT 1
                """,
                (key, key, original),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT * FROM catalog_listings
                    WHERE listing_key = ? OR canonical_key = ? OR url = ?
                    ORDER BY last_seen DESC LIMIT 1
                    """,
                    (key, key, original),
                ).fetchone()
            if row is not None:
                data = _row_from_sqlite(row)
                nid = extract_sreality_id(data.get("id")) or native or key
                data["id"] = nid
                found[nid] = data
                if native:
                    found[native] = data

    return found


def _peer_median_m2(store: Store, row: dict[str, Any]) -> float | None:
    disposition = str(row.get("disposition") or "").strip()
    locality = _locality_bucket(str(row.get("locality") or ""))
    if not disposition:
        return None
    want_rent = _is_rental(row)
    cache_key = f"{disposition}|{locality}|{int(want_rent)}"
    cached = _peer_cache_get(cache_key)
    if cached is not _MISSING:
        return cached  # type: ignore[return-value]

    values: list[float] = []
    try:
        with store.read() as conn:
            # Prefer URL-filtered peers in SQL for speed
            if want_rent:
                url_filter = "AND (url LIKE '%/pronajem/%' OR lower(IFNULL(price_label,'')) LIKE '%měsíc%' OR lower(IFNULL(price_label,'')) LIKE '%mesic%')"
                price_bound = "AND price_czk < 150000"
            else:
                url_filter = "AND (url LIKE '%/prodej/%' OR price_czk >= 150000)"
                price_bound = "AND price_czk >= 150000"
            sql = f"""
                SELECT price_czk, area_m2, locality, url, price_label, extras FROM catalog_listings
                WHERE IFNULL(gone, 0) = 0
                  AND disposition = ?
                  AND price_czk > 0 AND area_m2 > 0
                  {url_filter}
                  {price_bound}
                LIMIT 250
            """
            peers = conn.execute(sql, (disposition,)).fetchall()
            if len(peers) < 8:
                peers = conn.execute(
                    f"""
                    SELECT price_czk, area_m2, locality, url, price_label, extras FROM listings
                    WHERE IFNULL(gone, 0) = 0
                      AND disposition = ?
                      AND price_czk > 0 AND area_m2 > 0
                      {url_filter}
                      {price_bound}
                    LIMIT 250
                    """,
                    (disposition,),
                ).fetchall() or peers
    except sqlite3.OperationalError:
        return None

    def collect(require_locality: bool) -> list[float]:
        out: list[float] = []
        for peer in peers:
            peer_d = dict(peer)
            if require_locality and locality and locality not in str(peer_d.get("locality") or "").lower():
                continue
            m2 = _price_m2(peer_d.get("price_czk"), peer_d.get("area_m2"))
            if not m2:
                continue
            if want_rent and m2 > 5000:
                continue
            if not want_rent and m2 < 8000:
                continue
            out.append(m2)
        return out

    values = collect(True)
    if len(values) < 3:
        values = collect(False)
    result = float(median(values)) if values else None
    _peer_cache_set(cache_key, result)
    return result


def _price_history(store: Store, row: dict[str, Any]) -> list[dict[str, Any]]:
    # Hot path: skip history table; trend uses old_price_czk / discount_pct.
    return []


def score_listing(store: Store, row: dict[str, Any], *, allow_network: bool = True) -> dict[str, Any]:
    extras = row.get("extras") if isinstance(row.get("extras"), dict) else _parse_json(row.get("extras"))
    history = [] if row.get("_stub") else _price_history(store, row)
    price_m2 = _price_m2(row.get("price_czk"), row.get("area_m2"))
    locality_avg = None if row.get("_stub") else _peer_median_m2(store, row)
    price_vs = None
    if price_m2 is not None and locality_avg and locality_avg > 0:
        price_vs = round(((price_m2 - locality_avg) / locality_avg) * 100, 1)
    days = _days_on_market(row)
    agency = _agency_label(str(row.get("agency") or ""), extras)
    trend = _trend_pct(row, history)
    score = _score_components(price_vs_pct=price_vs, days=days, trend=trend, agency_label=agency)
    tier = _tier_for(score)
    native = extract_sreality_id(row.get("id") or row.get("native_id"))
    trend_bar = 50
    if price_vs is not None:
        # Spectrum: under market → left, average → center, over market → right.
        trend_bar = int(max(8, min(92, 50 + price_vs * 0.45)))

    loc = location_accessibility(row, allow_network=allow_network and not row.get("_stub"))
    location_score = loc.get("score")
    location_note = str(loc.get("note") or "").strip() or _location_note(extras, str(row.get("locality") or ""))

    return {
        "found": True,
        "id": native,
        "url": row.get("url") or "",
        "score": score,
        "tier": tier["key"],
        "tier_label": tier["label"],
        "verdict": tier["verdict"],
        "color": tier["color"],
        "pill_bg": tier["pill_bg"],
        "pill_border": tier["pill_border"],
        "source": agency,
        "days_on_market": days,
        "days_label": f"{days} dní" if days is not None else "—",
        "trend_pct": trend,
        "trend_label": (f"{trend:+.0f}%" if trend is not None else "0%"),
        "price_m2": price_m2,
        "price_m2_label": _format_m2(price_m2) if price_m2 is not None else "—",
        "locality_avg_m2": locality_avg,
        "locality_avg_label": _compact_k(locality_avg),
        "price_vs_market_pct": price_vs,
        "price_vs_label": _price_vs_label(price_vs),
        "trend_bar": trend_bar,
        "location_score": location_score,
        "location_note": location_note,
        "location_factors": loc.get("factors") or [],
        "location_source": loc.get("source") or "heuristic",
        "market_note": "Aktivní, bez změn ceny" if trend in (None, 0) else ("Cena klesla" if (trend or 0) < 0 else "Cena stoupla"),
        "detail_url": _catalog_detail_url(row),
        "name": row.get("name") or "",
        "locality": row.get("locality") or "",
    }


def not_found_payload(native_id: str = "", url: str = "") -> dict[str, Any]:
    base = (config.PUBLIC_BASE_URL or "https://realitify.cz").rstrip("/")
    return {
        "found": False,
        "id": extract_sreality_id(native_id or url),
        "url": url,
        "score": None,
        "tier": TIER_MID,
        "tier_label": "Bez dat",
        "verdict": "Inzerát zatím nemáme v katalogu",
        "color": "#6e7570",
        "detail_url": f"{base}/nabidka",
    }


def invalidate_scores(*ids: str) -> None:
    for native in ids:
        key = extract_sreality_id(native) or str(native or "").strip()
        if key:
            _SCORE_CACHE.pop(key, None)


def score_batch(
    store: Store,
    *,
    ids: list[str] | None = None,
    urls: list[str] | None = None,
    allow_network: bool = False,
) -> dict[str, Any]:
    ids = [str(item).strip() for item in (ids or []) if str(item).strip()]
    urls = [str(item).strip() for item in (urls or []) if str(item).strip()]
    items: dict[str, Any] = {}
    need_ids: list[str] = []
    now = time.time()

    for native in [extract_sreality_id(i) for i in ids]:
        if not native:
            continue
        hit = _SCORE_CACHE.get(native)
        if hit and now - hit[0] < _SCORE_CACHE_TTL:
            items[native] = hit[1]
        else:
            need_ids.append(native)

    need_urls = []
    for url in urls:
        native = extract_sreality_id(url)
        if native and native in items:
            continue
        if native and native in need_ids:
            continue
        need_urls.append(url)

    try:
        rows = _lookup_rows(store, ids=need_ids, urls=need_urls) if (need_ids or need_urls) else {}
    except sqlite3.OperationalError:
        stale_items = dict(items)
        for native in need_ids:
            hit = _SCORE_CACHE.get(native)
            if hit:
                payload = hit[1]
                stale_items[native] = {**payload, "stale": True} if isinstance(payload, dict) else payload
            else:
                stale_items[native] = not_found_payload(native)
        return {"items": stale_items, "count": len(stale_items), "stale": True}
    if allow_network:
        warm_amenity_cache(list(rows.values()), max_fetches=4)

    for native in need_ids:
        row = rows.get(native)
        payload = score_listing(store, row, allow_network=allow_network) if row else not_found_payload(native)
        items[native] = payload
        _SCORE_CACHE[native] = (now, payload)

    for url in need_urls:
        native = extract_sreality_id(url)
        key = native or listing_key(normalize_url(url))
        if key in items:
            continue
        row = rows.get(native) if native else None
        if row is None:
            norm = normalize_url(url)
            for candidate in rows.values():
                if normalize_url(str(candidate.get("url") or "")) == norm or listing_key(str(candidate.get("url") or "")) == listing_key(norm):
                    row = candidate
                    break
        payload = score_listing(store, row, allow_network=allow_network) if row else not_found_payload(native, url)
        items[key] = payload
        if native:
            _SCORE_CACHE[native] = (now, payload)

    if len(_SCORE_CACHE) > 2000:
        oldest = sorted(_SCORE_CACHE.items(), key=lambda kv: kv[1][0])[:400]
        for k, _ in oldest:
            _SCORE_CACHE.pop(k, None)

    return {"items": items, "count": len(items)}
