"""Marketing games: Higher/Lower and rent-value guessing with admin leaderboards."""

from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from typing import Any

from app.identity import portal_from_url, portal_label
from app.localities import slugify
from app.sources import PORTAL_LABELS
from app.store import utc_now

_IMG = {
    "s1": "/static/site/assets/sold-1.webp",
    "s2": "/static/site/assets/sold-2.webp",
    "s3": "/static/site/assets/sold-3.webp",
    "s4": "/static/site/assets/sold-4.webp",
    "d1": "/static/site/assets/db-1.webp",
    "d2": "/static/site/assets/db-2.webp",
    "d3": "/static/site/assets/db-3.webp",
}


def locality_key(value: str | None) -> str:
    """Canonical city/district/neighborhood key.

    Same key means the same place: ``Praha 2 – Vinohrady`` matches
    ``Vinohrady, Praha 2``. Different neighborhoods never match
    (Vinohrady ≠ Žižkov ≠ Bedihošť).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("–", "-").replace("—", "-").replace(",", "-").replace("/", "-")
    slug = slugify(normalized)
    tokens = [token for token in slug.split("-") if token]
    if not tokens:
        return ""
    praha: str | None = None
    rest: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        nxt = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token in {"praha", "prague"} and nxt.isdigit():
            praha = f"praha-{nxt}"
            index += 2
            continue
        if token in {"praha", "prague"}:
            if praha is None:
                praha = "praha"
            index += 1
            continue
        rest.append(token)
        index += 1
    if praha:
        rest = [token for token in rest if token not in {"praha", "prague"}]
        return "-".join([praha, *rest])
    return "-".join(tokens)


def _seed(
    key: str,
    *,
    locality: str,
    disposition: str,
    area_m2: int,
    price_czk: int,
    portal: str,
    vanish_hours: float,
    image: str,
) -> dict[str, Any]:
    return {
        "id": key,
        "name": f"Pronájem bytu {disposition}, {locality}",
        "locality": locality,
        "disposition": disposition,
        "area_m2": area_m2,
        "price_czk": price_czk,
        "image_url": image,
        "portal": portal,
        "vanish_hours": vanish_hours,
        "locality_key": locality_key(locality),
    }


# Same-locality pairs on purpose: every neighborhood has at least one pedagogical
# contrast (better+cheaper vs worse+dearer) so the game never mixes cities.
SEED: list[dict[str, Any]] = [
    _seed("seed-zizkov-2kk", locality="Praha 3 – Žižkov", disposition="2+kk", area_m2=54, price_czk=16500, portal="bezrealitky", vanish_hours=3.1, image=_IMG["s1"]),
    _seed("seed-zizkov-1kk", locality="Praha 3 – Žižkov", disposition="1+kk", area_m2=38, price_czk=18900, portal="sreality", vanish_hours=11.0, image=_IMG["s2"]),
    _seed("seed-zizkov-3kk", locality="Praha 3 – Žižkov", disposition="3+kk", area_m2=72, price_czk=21400, portal="idnes", vanish_hours=6.4, image=_IMG["d1"]),
    _seed("seed-vinohrady-3kk", locality="Praha 2 – Vinohrady", disposition="3+kk", area_m2=82, price_czk=21900, portal="bezrealitky", vanish_hours=1.2, image=_IMG["s3"]),
    _seed("seed-vinohrady-2kk", locality="Praha 2 – Vinohrady", disposition="2+kk", area_m2=46, price_czk=26800, portal="sreality", vanish_hours=9.5, image=_IMG["s4"]),
    _seed("seed-vinohrady-1kk", locality="Praha 2 – Vinohrady", disposition="1+kk", area_m2=32, price_czk=14200, portal="idnes", vanish_hours=5.4, image=_IMG["d2"]),
    _seed("seed-smichov-3kk", locality="Praha 5 – Smíchov", disposition="3+kk", area_m2=78, price_czk=27500, portal="sreality", vanish_hours=0.8, image=_IMG["s2"]),
    _seed("seed-smichov-2kk", locality="Praha 5 – Smíchov", disposition="2+kk", area_m2=51, price_czk=31800, portal="remax", vanish_hours=14.0, image=_IMG["s1"]),
    _seed("seed-brno-3kk", locality="Brno – střed", disposition="3+kk", area_m2=64, price_czk=17200, portal="ulovdomov", vanish_hours=4.1, image=_IMG["d3"]),
    _seed("seed-brno-2kk", locality="Brno – střed", disposition="2+kk", area_m2=48, price_czk=18900, portal="ulovdomov", vanish_hours=6.2, image=_IMG["s1"]),
    _seed("seed-brno-1kk", locality="Brno – střed", disposition="1+kk", area_m2=28, price_czk=21000, portal="annonce", vanish_hours=18.0, image=_IMG["s4"]),
    _seed("seed-karlin-3kk", locality="Praha 8 – Karlín", disposition="3+kk", area_m2=74, price_czk=22900, portal="bezrealitky", vanish_hours=1.1, image=_IMG["s3"]),
    _seed("seed-karlin-2kk", locality="Praha 8 – Karlín", disposition="2+kk", area_m2=61, price_czk=24500, portal="remax", vanish_hours=1.5, image=_IMG["s2"]),
    _seed("seed-holesovice-3kk", locality="Praha 7 – Holešovice", disposition="3+kk", area_m2=82, price_czk=31500, portal="ceskereality", vanish_hours=4.0, image=_IMG["d1"]),
    _seed("seed-holesovice-2kk", locality="Praha 7 – Holešovice", disposition="2+kk", area_m2=55, price_czk=33900, portal="idnes", vanish_hours=12.2, image=_IMG["s4"]),
    _seed("seed-ostrava-2kk", locality="Ostrava – Poruba", disposition="2+kk", area_m2=56, price_czk=12900, portal="bazos", vanish_hours=18.0, image=_IMG["s1"]),
    _seed("seed-ostrava-1kk", locality="Ostrava – Poruba", disposition="1+kk", area_m2=29, price_czk=14900, portal="realitycz", vanish_hours=22.0, image=_IMG["s2"]),
    _seed("seed-plzen-2kk", locality="Plzeň – Jižní Předměstí", disposition="2+kk", area_m2=44, price_czk=8900, portal="annonce", vanish_hours=8.5, image=_IMG["d2"]),
    _seed("seed-plzen-1kk", locality="Plzeň – Jižní Předměstí", disposition="1+kk", area_m2=28, price_czk=9900, portal="annonce", vanish_hours=22.5, image=_IMG["s2"]),
    _seed("seed-dejvice-4kk", locality="Praha 6 – Dejvice", disposition="4+kk", area_m2=112, price_czk=42900, portal="mmreality", vanish_hours=2.2, image=_IMG["d3"]),
    _seed("seed-dejvice-2kk", locality="Praha 6 – Dejvice", disposition="2+kk", area_m2=58, price_czk=45500, portal="sreality", vanish_hours=16.0, image=_IMG["s3"]),
    _seed("seed-nusle-2kk", locality="Praha 4 – Nusle", disposition="2+kk", area_m2=52, price_czk=19800, portal="realitycz", vanish_hours=7.8, image=_IMG["s1"]),
    _seed("seed-nusle-1kk", locality="Praha 4 – Nusle", disposition="1+kk", area_m2=31, price_czk=21500, portal="ceskereality", vanish_hours=19.4, image=_IMG["s4"]),
]

POINTS_PER_PROPERTY = 1000
ERROR_ZERO_AT = 0.5  # 50 % odchylka = 0 bodů
RENT_ROUND_SIZE = 5
TEACHING_RATIO = 0.8
# (monotonic_ts, items, seed_only)
_CACHE: tuple[float, list[dict[str, Any]], bool] | None = None
_CACHE_TTL = 45.0
_SEED_CACHE_TTL = 3.0
_REFRESH_LOCK = threading.Lock()
_REFRESHING = False

_INTRO_COPY = (
    "Oba byty jsou ve stejné lokalitě. Který je levnější? "
    "Dobré kousky mizí v řádu minut — v reálu vyhrává ten, kdo dostane upozornění první."
)
_TEACH_OK = (
    "Přesně tak: lepší byt může stát míň. Takové nabídky mizí jako první — "
    "podobné byty mizí {vanish}."
)
_TEACH_MISS = (
    "Dražší byt byl menší nebo v horší dispozici. Dobré ceny v jedné ulici mizí {vanish}."
)
_RANDOM_OK = (
    "Správně. Ve stejné lokalitě se ceny hodně rozcházejí — a výhodné kousky mizí {vanish}."
)
_RANDOM_MISS = (
    "Špatně. Levnější byt ve stejné lokalitě už často není. Podobné nabídky mizí {vanish}."
)

_DISP_RE = re.compile(r"(\d+)\s*\+?\s*(kk|1)?", re.I)


def disposition_rank(value: str | None) -> int:
    """Order living size: 1+kk < 1+1 < 2+kk < 2+1 < 3+kk … Unknown is 0."""
    text = str(value or "").strip().lower().replace(" ", "")
    match = _DISP_RE.search(text)
    if not match:
        return 0
    rooms = int(match.group(1))
    kind = (match.group(2) or "1").lower()
    return rooms * 2 - (1 if kind == "kk" else 0)


def is_teaching_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when the dearer flat is also worse (smaller and/or worse disposition).

    Pedagogical lesson: a clearly better flat can still be cheaper — and those
    vanish first.
    """
    cheap, dear = _price_order(left, right)
    if cheap is None:
        return False
    cheap_area = _as_int(cheap.get("area_m2")) or 0
    dear_area = _as_int(dear.get("area_m2")) or 0
    cheap_disp = disposition_rank(str(cheap.get("disposition") or ""))
    dear_disp = disposition_rank(str(dear.get("disposition") or ""))
    worse_area = dear_area < cheap_area
    worse_disp = dear_disp < cheap_disp
    return bool(worse_area or worse_disp)


def _price_order(
    left: dict[str, Any], right: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | tuple[None, None]:
    price_l = _as_int(left.get("price_czk"))
    price_r = _as_int(right.get("price_czk"))
    if not price_l or not price_r or price_l == price_r:
        return None, None
    if price_l < price_r:
        return left, right
    return right, left


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
    loc = item.get("locality") or ""
    card = {
        "id": str(item.get("id") or ""),
        "name": item.get("name") or "",
        "locality": loc,
        "locality_key": item.get("locality_key") or locality_key(str(loc)),
        "disposition": item.get("disposition") or "",
        "area_m2": item.get("area_m2"),
        "image_url": item.get("image_url") or "/static/site/assets/sold-1.webp",
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
    return [dict(item) for item in SEED]


def _annotate(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    loc = str(row.get("locality") or "")
    row["locality_key"] = row.get("locality_key") or locality_key(loc)
    image = str(row.get("image_url") or "")
    if image.endswith(".png") and "/static/site/assets/" in image:
        row["image_url"] = image[:-4] + ".webp"
    elif not image:
        row["image_url"] = _IMG["s1"]
    return row


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
        unique.append(_annotate(item))
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


def _groups_by_locality(pool: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in pool:
        price = _as_int(item.get("price_czk"))
        if not price:
            continue
        key = str(item.get("locality_key") or locality_key(str(item.get("locality") or "")))
        if not key:
            continue
        groups.setdefault(key, []).append(item)
    return {key: items for key, items in groups.items() if _pairable(items)}


def _pairable(items: list[dict[str, Any]]) -> bool:
    if len(items) < 2:
        return False
    prices = {_as_int(item.get("price_czk")) for item in items}
    prices.discard(None)
    return len(prices) >= 2


def _candidate_pairs(items: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, left in enumerate(items):
        price_l = _as_int(left.get("price_czk"))
        if not price_l:
            continue
        for right in items[index + 1 :]:
            price_r = _as_int(right.get("price_czk"))
            if not price_r or price_r == price_l or left.get("id") == right.get("id"):
                continue
            pairs.append((left, right))
    return pairs


def _teaching_pairs(items: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [pair for pair in _candidate_pairs(items) if is_teaching_pair(*pair)]


def _vanish_label(hours: float) -> str:
    value = float(hours or 0)
    if value < 1:
        minutes = max(8, int(round(value * 60)))
        return f"za {minutes} minut"
    if value < 24:
        pretty = f"{value:.1f}".replace(".", ",")
        return f"za {pretty} h"
    return f"za {int(round(value))} h"


def _pair_payload(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    pair_kind: str,
    rng: random.Random,
) -> dict[str, Any]:
    if rng.random() < 0.5:
        left, right = right, left
    vanish = min(
        float(left.get("vanish_hours") or 8),
        float(right.get("vanish_hours") or 8),
    )
    cheaper = "left" if int(left["price_czk"]) <= int(right["price_czk"]) else "right"
    vanish_text = _vanish_label(vanish)
    teaching = pair_kind == "teaching" or is_teaching_pair(left, right)
    loc = left.get("locality") or right.get("locality") or ""
    return {
        "left": public_card(left, include_price=True),
        "right": public_card(right, include_price=True),
        "cheaper": cheaper,
        "vanish_hours": vanish,
        "pair_kind": "teaching" if teaching and pair_kind == "teaching" else pair_kind,
        "locality_key": left.get("locality_key") or locality_key(str(loc)),
        "locality_label": loc,
        "copy": _INTRO_COPY,
        "copy_ok": (_TEACH_OK if pair_kind == "teaching" else _RANDOM_OK).format(vanish=vanish_text),
        "copy_miss": (_TEACH_MISS if pair_kind == "teaching" else _RANDOM_MISS).format(vanish=vanish_text),
        "seeded": all(str(item.get("id") or "").startswith("seed-") for item in (left, right)),
    }


def pick_same_locality_pair(
    pool: list[dict[str, Any]],
    *,
    rng: random.Random | None = None,
    teaching_ratio: float = TEACHING_RATIO,
) -> dict[str, Any]:
    """Always same locality_key. ~80 % pedagogical, ~20 % any same-place pair."""
    rng = rng or random.Random()
    usable = [_annotate(item) for item in pool if _as_int(item.get("price_czk"))]
    groups = _groups_by_locality(usable)
    if not groups:
        usable = _seed_pool()
        groups = _groups_by_locality(usable)
    teaching_groups = {key: items for key, items in groups.items() if _teaching_pairs(items)}
    want_teaching = rng.random() < max(0.0, min(1.0, float(teaching_ratio)))
    pair_kind = "teaching"
    picked: tuple[dict[str, Any], dict[str, Any]] | None = None
    if want_teaching and teaching_groups:
        key = rng.choice(list(teaching_groups))
        picked = rng.choice(_teaching_pairs(teaching_groups[key]))
        pair_kind = "teaching"
    if picked is None:
        key = rng.choice(list(groups))
        candidates = _candidate_pairs(groups[key])
        picked = rng.choice(candidates)
        pair_kind = "random"
    left, right = picked
    return _pair_payload(left, right, pair_kind=pair_kind, rng=rng)


def higher_lower_pair(
    store: Any,
    *,
    rng: random.Random | None = None,
    pool: list[dict[str, Any]] | None = None,
    teaching_ratio: float = TEACHING_RATIO,
) -> dict[str, Any]:
    source = pool if pool is not None else _catalog_pool(store)
    return pick_same_locality_pair(source, rng=rng, teaching_ratio=teaching_ratio)


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
    random.shuffle(picked)
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
    with store.read() as conn:
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
