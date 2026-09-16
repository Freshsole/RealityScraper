"""Marketing games: Higher/Lower and rent-value guessing with admin leaderboards."""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from typing import Any

from app.identity import portal_from_url, portal_label
from app.sources import PORTAL_LABELS
from app.store import utc_now

SEED: list[dict[str, Any]] = [
    {
        "id": "seed-zizkov-2kk",
        "name": "Pronájem bytu 2+kk, Praha 3 – Žižkov",
        "locality": "Praha 3 – Žižkov",
        "disposition": "2+kk",
        "area_m2": 54,
        "price_czk": 16500,
        "image_url": "/static/site/assets/sold-1.png",
        "portal": "bezrealitky",
        "vanish_hours": 3.1,
    },
    {
        "id": "seed-smichov-3kk",
        "name": "Pronájem bytu 3+kk, Praha 5 – Smíchov",
        "locality": "Praha 5 – Smíchov",
        "disposition": "3+kk",
        "area_m2": 78,
        "price_czk": 28900,
        "image_url": "/static/site/assets/sold-2.png",
        "portal": "sreality",
        "vanish_hours": 0.8,
    },
    {
        "id": "seed-vinohrady-1kk",
        "name": "Pronájem bytu 1+kk, Praha 2 – Vinohrady",
        "locality": "Praha 2 – Vinohrady",
        "disposition": "1+kk",
        "area_m2": 32,
        "price_czk": 14200,
        "image_url": "/static/site/assets/hero-apart.png",
        "portal": "idnes",
        "vanish_hours": 5.4,
    },
    {
        "id": "seed-brno-2kk",
        "name": "Pronájem bytu 2+kk, Brno – střed",
        "locality": "Brno – střed",
        "disposition": "2+kk",
        "area_m2": 48,
        "price_czk": 18900,
        "image_url": "/static/site/assets/sold-1.png",
        "portal": "ulovdomov",
        "vanish_hours": 6.2,
    },
    {
        "id": "seed-karlin-2kk",
        "name": "Pronájem bytu 2+kk, Praha 8 – Karlín",
        "locality": "Praha 8 – Karlín",
        "disposition": "2+kk",
        "area_m2": 61,
        "price_czk": 24500,
        "image_url": "/static/site/assets/sold-2.png",
        "portal": "remax",
        "vanish_hours": 1.5,
    },
    {
        "id": "seed-holesovice-3kk",
        "name": "Pronájem bytu 3+kk, Praha 7 – Holešovice",
        "locality": "Praha 7 – Holešovice",
        "disposition": "3+kk",
        "area_m2": 82,
        "price_czk": 31500,
        "image_url": "/static/site/assets/hero-apart.png",
        "portal": "ceskereality",
        "vanish_hours": 4.0,
    },
    {
        "id": "seed-ostrava-2kk",
        "name": "Pronájem bytu 2+kk, Ostrava – Poruba",
        "locality": "Ostrava – Poruba",
        "disposition": "2+kk",
        "area_m2": 56,
        "price_czk": 12900,
        "image_url": "/static/site/assets/sold-1.png",
        "portal": "bazos",
        "vanish_hours": 18.0,
    },
    {
        "id": "seed-plzen-1kk",
        "name": "Pronájem bytu 1+kk, Plzeň – Jižní Předměstí",
        "locality": "Plzeň – Jižní Předměstí",
        "disposition": "1+kk",
        "area_m2": 28,
        "price_czk": 9900,
        "image_url": "/static/site/assets/sold-2.png",
        "portal": "annonce",
        "vanish_hours": 22.5,
    },
    {
        "id": "seed-dejvice-4kk",
        "name": "Pronájem bytu 4+kk, Praha 6 – Dejvice",
        "locality": "Praha 6 – Dejvice",
        "disposition": "4+kk",
        "area_m2": 112,
        "price_czk": 42900,
        "image_url": "/static/site/assets/hero-apart.png",
        "portal": "mmreality",
        "vanish_hours": 2.2,
    },
    {
        "id": "seed-nusle-2kk",
        "name": "Pronájem bytu 2+kk, Praha 4 – Nusle",
        "locality": "Praha 4 – Nusle",
        "disposition": "2+kk",
        "area_m2": 52,
        "price_czk": 19800,
        "image_url": "/static/site/assets/sold-1.png",
        "portal": "realitycz",
        "vanish_hours": 7.8,
    },
]

POINTS_PER_PROPERTY = 1000
ERROR_ZERO_AT = 0.5  # 50 % odchylka = 0 bodů
RENT_ROUND_SIZE = 5
# (monotonic_ts, items, seed_only)
_CACHE: tuple[float, list[dict[str, Any]], bool] | None = None
_CACHE_TTL = 45.0
_SEED_CACHE_TTL = 3.0
_REFRESH_LOCK = threading.Lock()
_REFRESHING = False


def score_guess(actual: int, guess: int) -> dict[str, Any]:
    actual_n = max(0, int(actual or 0))
    guess_n = int(guess or 0)
    if actual_n <= 0:
        return {"actual": actual_n, "guess": guess_n, "error_pct": 100.0, "points": 0}
    error = abs(guess_n - actual_n) / actual_n
    points = max(0, int(round(POINTS_PER_PROPERTY * max(0.0, 1.0 - error / ERROR_ZERO_AT))))
    return {
        "actual": actual_n,
        "guess": guess_n,
        "error_pct": round(error * 100.0, 1),
        "points": points,
    }


def score_round(items: list[dict[str, Any]], guesses: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(item.get("id")): item for item in items}
    results: list[dict[str, Any]] = []
    total = 0
    errors: list[float] = []
    for row in guesses:
        listing_id = str(row.get("id") or "")
        item = by_id.get(listing_id)
        if not item:
            continue
        try:
            guess = int(row.get("guess") or 0)
        except (TypeError, ValueError):
            guess = 0
        scored = score_guess(int(item.get("price_czk") or 0), guess)
        total += scored["points"]
        errors.append(min(1.0, scored["error_pct"] / 100.0))
        results.append({**public_card(item, include_price=True), **scored})
    accuracy = round((1.0 - (sum(errors) / len(errors))) * 100.0, 1) if errors else 0.0
    return {
        "score": total,
        "max_score": POINTS_PER_PROPERTY * max(1, len(results) or RENT_ROUND_SIZE),
        "accuracy": accuracy,
        "items": results,
    }


def public_card(item: dict[str, Any], *, include_price: bool = False) -> dict[str, Any]:
    portal = str(item.get("portal") or portal_from_url(str(item.get("url") or "")))
    card = {
        "id": str(item.get("id") or ""),
        "name": item.get("name") or "",
        "locality": item.get("locality") or "",
        "disposition": item.get("disposition") or "",
        "area_m2": item.get("area_m2"),
        "image_url": item.get("image_url") or "/static/site/assets/hero-apart.png",
        "portal": portal,
        "portal_label": PORTAL_LABELS.get(portal, portal_label(portal)),
        "vanish_hours": item.get("vanish_hours"),
    }
    if include_price:
        card["price_czk"] = item.get("price_czk")
        card["price_label"] = item.get("price_label") or _price_label(item.get("price_czk"))
    return card


def _price_label(price: Any) -> str:
    try:
        return f"{int(price):,} Kč/měsíc".replace(",", " ")
    except (TypeError, ValueError):
        return ""


def _as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def reset_pool_cache() -> None:
    global _CACHE, _REFRESHING
    with _REFRESH_LOCK:
        _CACHE = None
        _REFRESHING = False


def wait_refresh(timeout: float = 1.0) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        with _REFRESH_LOCK:
            busy = _REFRESHING
        if not busy:
            return
        time.sleep(0.01)


def _seed_pool() -> list[dict[str, Any]]:
    return list(SEED)


def _finalize_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = list(rows or [])
    if len(merged) < 6:
        merged = _seed_pool() + merged
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in merged:
        key = str(item.get("id") or "")
        price = _as_int(item.get("price_czk"))
        if not key or key in seen or not price:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _cache_fresh(now: float) -> list[dict[str, Any]] | None:
    cached = _CACHE
    if not cached:
        return None
    at, items, seed_only = cached
    ttl = _SEED_CACHE_TTL if seed_only else _CACHE_TTL
    if now - at < ttl:
        return items
    return None


def schedule_pool_refresh(store: Any) -> None:
    """Kick a single background catalog refresh. Never waits for scrape/DB."""
    global _REFRESHING
    if store is None:
        return
    if _cache_fresh(time.monotonic()) is not None:
        return
    with _REFRESH_LOCK:
        if _REFRESHING:
            return
        if _cache_fresh(time.monotonic()) is not None:
            return
        _REFRESHING = True
    threading.Thread(target=_refresh_pool, args=(store,), name="game-pool", daemon=True).start()


def refresh_pool_now(store: Any) -> list[dict[str, Any]]:
    """Synchronous refresh for tests / startup warmup with a tight DB budget."""
    return _refresh_pool(store, background=False)


def _refresh_pool(store: Any, *, background: bool = True) -> list[dict[str, Any]]:
    global _CACHE, _REFRESHING
    unique: list[dict[str, Any]] = []
    try:
        rows: list[dict[str, Any]] = []
        method = getattr(store, "game_listing_pool", None)
        if callable(method):
            rows = method(limit=240, budget_sec=0.2) or []
        unique = _finalize_pool(rows)
        seed_only = bool(unique) and all(str(item.get("id") or "").startswith("seed-") for item in unique)
        if unique:
            _CACHE = (time.monotonic(), unique, seed_only)
        elif _CACHE is None:
            unique = _seed_pool()
            _CACHE = (time.monotonic(), unique, True)
        else:
            unique = _CACHE[1]
    except Exception:
        unique = _CACHE[1] if _CACHE else _seed_pool()
        if _CACHE is None:
            _CACHE = (time.monotonic(), unique, True)
    finally:
        if background:
            with _REFRESH_LOCK:
                _REFRESHING = False
    return unique


def _catalog_pool(store: Any) -> list[dict[str, Any]]:
    """Memory-only on the request path. Catalog I/O happens in the background."""
    now = time.monotonic()
    cached = _CACHE
    fresh = _cache_fresh(now)
    if fresh is not None:
        return fresh
    schedule_pool_refresh(store)
    if cached:
        return cached[1]
    return _seed_pool()


def higher_lower_pair(store: Any) -> dict[str, Any]:
    pool = [item for item in _catalog_pool(store) if _as_int(item.get("price_czk"))]
    if len(pool) < 2:
        pool = list(SEED)
    random.shuffle(pool)
    left = pool[0]
    right = next((item for item in pool[1:] if int(item["price_czk"]) != int(left["price_czk"])), pool[-1])
    if left["id"] == right["id"]:
        ranked = sorted(pool, key=lambda item: int(item.get("price_czk") or 0))
        left, right = ranked[0], ranked[-1]
    vanish = min(
        float(left.get("vanish_hours") or 8),
        float(right.get("vanish_hours") or 8),
    )
    cheaper = "left" if int(left["price_czk"]) <= int(right["price_czk"]) else "right"
    return {
        "left": public_card(left, include_price=True),
        "right": public_card(right, include_price=True),
        "cheaper": cheaper,
        "vanish_hours": vanish,
        "copy": (
            "Dobré byty mizí v řádu minut. Tohle je hra o cenu — v reálu vyhrává ten, "
            "kdo dostane upozornění první."
        ),
        "seeded": all(str(item.get("id") or "").startswith("seed-") for item in (left, right)),
    }


def rent_round(store: Any) -> dict[str, Any]:
    pool = _catalog_pool(store)
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Spread prices so the round isn't five similar flats.
    ordered = sorted(pool, key=lambda item: int(item.get("price_czk") or 0))
    step = max(1, len(ordered) // RENT_ROUND_SIZE)
    for index in range(0, len(ordered), step):
        item = ordered[index]
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        picked.append(item)
        if len(picked) >= RENT_ROUND_SIZE:
            break
    if len(picked) < RENT_ROUND_SIZE:
        for item in SEED:
            if item["id"] in seen:
                continue
            picked.append(item)
            if len(picked) >= RENT_ROUND_SIZE:
                break
    return {
        "round_id": uuid.uuid4().hex,
        "items": [public_card(item, include_price=False) for item in picked[:RENT_ROUND_SIZE]],
        "hidden": {str(item["id"]): int(item["price_czk"]) for item in picked[:RENT_ROUND_SIZE]},
        "pool": picked[:RENT_ROUND_SIZE],
    }


def lookup_prices(store: Any, ids: list[str]) -> dict[str, dict[str, Any]]:
    wanted = {str(item) for item in ids if item}
    found: dict[str, dict[str, Any]] = {}
    for item in SEED:
        if item["id"] in wanted:
            found[item["id"]] = item
    cached = _CACHE[1] if _CACHE else []
    for item in cached:
        key = str(item.get("id") or "")
        if key in wanted and key not in found:
            found[key] = item
    missing = wanted - set(found)
    if not missing:
        return found
    try:
        holders = ",".join("?" * len(missing))
        with store.connect(quick=True) as conn:
            rows = conn.execute(
                f"""
                SELECT listing_key, name, locality, disposition, area_m2, price_czk, price_label,
                       image_url, url, portal
                FROM catalog_listings
                WHERE listing_key IN ({holders})
                """,
                tuple(missing),
            ).fetchall()
        for row in rows:
            item = dict(row)
            found[str(item.get("listing_key"))] = {
                "id": str(item.get("listing_key")),
                "name": item.get("name"),
                "locality": item.get("locality"),
                "disposition": item.get("disposition"),
                "area_m2": item.get("area_m2"),
                "price_czk": item.get("price_czk"),
                "price_label": item.get("price_label"),
                "image_url": item.get("image_url"),
                "portal": item.get("portal"),
            }
    except Exception:
        pass
    return found


def save_rent_round(store: Any, *, player_name: str, scored: dict[str, Any]) -> dict[str, Any]:
    round_id = uuid.uuid4().hex
    name = (player_name or "").strip()[:64] or "Anonym"
    payload = {
        "id": round_id,
        "player_name": name,
        "score": int(scored.get("score") or 0),
        "accuracy": float(scored.get("accuracy") or 0),
        "guesses_json": json.dumps(scored.get("items") or [], ensure_ascii=False),
        "created_at": utc_now(),
    }
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO game_rent_rounds(id, player_name, score, accuracy, guesses_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["player_name"],
                payload["score"],
                payload["accuracy"],
                payload["guesses_json"],
                payload["created_at"],
            ),
        )
        conn.commit()
    return payload


def leaderboard(store: Any, *, top: int = 20, recent: int = 20) -> dict[str, Any]:
    with store.connect(readonly=True) as conn:
        tops = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, player_name, score, accuracy, created_at
                FROM game_rent_rounds
                ORDER BY score DESC, accuracy DESC, created_at DESC
                LIMIT ?
                """,
                (max(1, int(top)),),
            )
        ]
        recents = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, player_name, score, accuracy, created_at, guesses_json
                FROM game_rent_rounds
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (max(1, int(recent)),),
            )
        ]
        stats = conn.execute(
            """
            SELECT COUNT(*) AS n,
                   IFNULL(AVG(score), 0) AS avg_score,
                   IFNULL(AVG(accuracy), 0) AS avg_accuracy,
                   IFNULL(MAX(score), 0) AS best
            FROM game_rent_rounds
            """
        ).fetchone()
    plays: list[dict[str, Any]] = []
    for row in recents:
        guesses = []
        try:
            guesses = json.loads(row.get("guesses_json") or "[]")
        except json.JSONDecodeError:
            guesses = []
        plays.append(
            {
                "id": row["id"],
                "player_name": row["player_name"],
                "score": row["score"],
                "accuracy": row["accuracy"],
                "created_at": row["created_at"],
                "properties": len(guesses),
                "items": guesses,
            }
        )
    return {
        "top": [
            {
                "id": row["id"],
                "player_name": row["player_name"],
                "score": row["score"],
                "accuracy": row["accuracy"],
                "created_at": row["created_at"],
            }
            for row in tops
        ],
        "recent": plays,
        "stats": {
            "n": int(stats["n"] if stats else 0),
            "avg_score": round(float(stats["avg_score"] if stats else 0), 1),
            "avg_accuracy": round(float(stats["avg_accuracy"] if stats else 0), 1),
            "best": int(stats["best"] if stats else 0),
        },
    }
