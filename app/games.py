"""Marketing games: Higher/Lower and rent-value guessing with admin leaderboards."""

from __future__ import annotations

import json
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
_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_CACHE_TTL = 45.0


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


def _catalog_pool(store: Any) -> list[dict[str, Any]]:
    global _CACHE
    now = time.monotonic()
    if _CACHE and now - _CACHE[0] < _CACHE_TTL:
        return _CACHE[1]
    rows: list[dict[str, Any]] = []
    try:
        with store.connect(readonly=True) as conn:
            fetched = conn.execute(
                """
                SELECT listing_key, name, locality, disposition, area_m2, price_czk, price_label,
                       image_url, url, portal, first_seen, last_seen, extras
                FROM catalog_listings
                WHERE IFNULL(gone, 0) = 0
                  AND price_czk BETWEEN 6000 AND 90000
                  AND IFNULL(image_url, '') != ''
                ORDER BY last_seen DESC
                LIMIT 160
                """
            ).fetchall()
        for row in fetched:
            item = dict(row)
            extras = item.get("extras")
            if isinstance(extras, str):
                try:
                    extras = json.loads(extras) if extras else {}
                except json.JSONDecodeError:
                    extras = {}
            offer = str((extras or {}).get("offer") or "")
            url = str(item.get("url") or "").lower()
            label = str(item.get("price_label") or "")
            rent = (
                "pronáj" in offer.casefold()
                or "pronaj" in offer.casefold()
                or "měsíc" in label.casefold()
                or "/pronajem/" in url
                or "/pronajmu/" in url
                or "byty-k-pronajmu" in url
            )
            if not rent:
                continue
            vanish = 8.0
            try:
                from datetime import datetime, timezone

                first = datetime.fromisoformat(str(item.get("first_seen") or "").replace("Z", "+00:00"))
                last = datetime.fromisoformat(str(item.get("last_seen") or "").replace("Z", "+00:00"))
                if first.tzinfo is None:
                    first = first.replace(tzinfo=timezone.utc)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                hours = max(0.2, (last - first).total_seconds() / 3600.0)
                vanish = round(min(48.0, hours), 1)
            except Exception:
                pass
            rows.append(
                {
                    "id": str(item.get("listing_key") or item.get("url") or ""),
                    "name": item.get("name") or "",
                    "locality": item.get("locality") or "",
                    "disposition": item.get("disposition") or "",
                    "area_m2": _as_int(item.get("area_m2")),
                    "price_czk": _as_int(item.get("price_czk")),
                    "price_label": item.get("price_label") or _price_label(item.get("price_czk")),
                    "image_url": item.get("image_url"),
                    "url": item.get("url"),
                    "portal": item.get("portal") or portal_from_url(str(item.get("url") or "")),
                    "vanish_hours": vanish,
                }
            )
    except Exception:
        rows = []
    if len(rows) < 6:
        rows = list(SEED) + rows
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in rows:
        key = str(item.get("id") or "")
        price = _as_int(item.get("price_czk"))
        if not key or key in seen or not price:
            continue
        seen.add(key)
        unique.append(item)
    _CACHE = (now, unique)
    return unique


def higher_lower_pair(store: Any) -> dict[str, Any]:
    pool = _catalog_pool(store)
    ranked = sorted(pool, key=lambda item: int(item.get("price_czk") or 0))
    if len(ranked) < 2:
        ranked = list(SEED)
    # Prefer a visible gap so the guess is meaningful, still snappy.
    left = ranked[len(ranked) // 3]
    right = ranked[(len(ranked) * 2) // 3]
    if left["id"] == right["id"] or left.get("price_czk") == right.get("price_czk"):
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
    missing = wanted - set(found)
    if not missing:
        return found
    try:
        holders = ",".join("?" * len(missing))
        with store.connect(readonly=True) as conn:
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
