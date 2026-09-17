"""Marketing games: Higher/Lower and rent-value guessing with admin leaderboards."""

from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.identity import estate_kind, portal_from_url, portal_label
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
_LOCAL_IMAGES = tuple(_IMG.values())


def _fast_game_image(image: str | None, key: str) -> str:
    """Keep first paint on local webp — remote portal photos are LCP killers."""
    text = str(image or "")
    if text.startswith("/static/site/assets/") and text.endswith((".webp", ".svg")):
        return text
    idx = abs(hash(str(key or text))) % len(_LOCAL_IMAGES)
    return _LOCAL_IMAGES[idx]


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
    _seed("seed-zizkov-4kk", locality="Praha 3 – Žižkov", disposition="4+kk", area_m2=91, price_czk=19900, portal="ulovdomov", vanish_hours=1.6, image=_IMG["d3"]),
    _seed("seed-zizkov-21", locality="Praha 3 – Žižkov", disposition="2+1", area_m2=47, price_czk=24200, portal="annonce", vanish_hours=15.0, image=_IMG["s4"]),
    _seed("seed-vinohrady-3kk", locality="Praha 2 – Vinohrady", disposition="3+kk", area_m2=82, price_czk=21900, portal="bezrealitky", vanish_hours=1.2, image=_IMG["s3"]),
    _seed("seed-vinohrady-2kk", locality="Praha 2 – Vinohrady", disposition="2+kk", area_m2=46, price_czk=26800, portal="sreality", vanish_hours=9.5, image=_IMG["s4"]),
    _seed("seed-vinohrady-1kk", locality="Praha 2 – Vinohrady", disposition="1+kk", area_m2=32, price_czk=14200, portal="idnes", vanish_hours=5.4, image=_IMG["d2"]),
    _seed("seed-vinohrady-4kk", locality="Praha 2 – Vinohrady", disposition="4+kk", area_m2=108, price_czk=24800, portal="ulovdomov", vanish_hours=2.4, image=_IMG["s1"]),
    _seed("seed-vinohrady-11", locality="Praha 2 – Vinohrady", disposition="1+1", area_m2=35, price_czk=23500, portal="remax", vanish_hours=13.5, image=_IMG["d3"]),
    _seed("seed-smichov-3kk", locality="Praha 5 – Smíchov", disposition="3+kk", area_m2=78, price_czk=27500, portal="sreality", vanish_hours=0.8, image=_IMG["s2"]),
    _seed("seed-smichov-2kk", locality="Praha 5 – Smíchov", disposition="2+kk", area_m2=51, price_czk=31800, portal="remax", vanish_hours=14.0, image=_IMG["s1"]),
    _seed("seed-brno-3kk", locality="Brno – střed", disposition="3+kk", area_m2=64, price_czk=17200, portal="ulovdomov", vanish_hours=4.1, image=_IMG["d3"]),
    _seed("seed-brno-2kk", locality="Brno – střed", disposition="2+kk", area_m2=48, price_czk=18900, portal="ulovdomov", vanish_hours=6.2, image=_IMG["s1"]),
    _seed("seed-brno-1kk", locality="Brno – střed", disposition="1+kk", area_m2=28, price_czk=21000, portal="annonce", vanish_hours=18.0, image=_IMG["s4"]),
    _seed("seed-brno-4kk", locality="Brno – střed", disposition="4+kk", area_m2=86, price_czk=18100, portal="idnes", vanish_hours=2.8, image=_IMG["s2"]),
    _seed("seed-brno-over", locality="Brno – střed", disposition="2+kk", area_m2=40, price_czk=22800, portal="sreality", vanish_hours=16.5, image=_IMG["s3"]),
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
# Pedagogical rounds stay on flats. "mixed" allows houses; land/plots never teach.
DEFAULT_GAME_ESTATE = "byty"
# Prefer live catalog once at least one locality has two priced, distinct listings.
MIN_LIVE_LOCALITY_PAIRS = 1
# Live cards are noisy: ignore missing fields and tiny gaps so "teaching" still means
# worse = dearer AND (clearly smaller OR worse disposition) AND a clearer Kč/m² deal.
MIN_TEACH_PRICE_GAP = 1500
MIN_TEACH_PRICE_RATIO = 0.08
MIN_TEACH_AREA_GAP = 10
MIN_TEACH_M2_GAP = 40
MIN_TEACH_M2_RATIO = 0.10
# 2+kk vs 2+1 is one rank — too noisy to teach on disposition alone.
MIN_TEACH_DISP_GAP = 2
# When several teaching pairs exist, stay in the high-contrast band (price/m² + size).
CLEAR_TEACH_KEEP = 0.75
DEFAULT_VANISH_HOURS = 8.0
# first_seen == last_seen on ingest is not a vanish time.
MIN_OBSERVED_VANISH_HOURS = 0.75
TYPICAL_VANISH_LABEL = "v řádu hodin"
# (monotonic_ts, items, seed_only)
_CACHE: tuple[float, list[dict[str, Any]], bool] | None = None
_CACHE_TTL = 45.0
_SEED_CACHE_TTL = 3.0
_REFRESH_LOCK = threading.Lock()
_REFRESHING = False
# Short-lived prices for InstantSiteASGI rent-score — never wait on SQLite.
_PRICE_HINTS: dict[str, tuple[float, dict[str, Any]]] = {}
_HINT_TTL = 1800.0
_HINT_CAP = 600

_INTRO_COPY = (
    "Oba byty jsou ve stejné lokalitě. Který je levnější? "
    "Lepší byt může stát míň — i za metr — a přesně ty mizí první."
)
_TEACH_OK = (
    "Lepší byt stál míň i za metr. Takové nabídky mizí jako první — "
    "podobné byty mizí {vanish}."
)
_TEACH_MISS = (
    "Dražší byt byl horší za metr. Lepší dispozice za míň peněz mizí {vanish}."
)
_RANDOM_OK = (
    "Ve stejné lokalitě se ceny hodně rozcházejí — výhodné kousky mizí {vanish}."
)
_RANDOM_MISS = (
    "Levnější byt ve stejné lokalitě už často není. Podobné nabídky mizí {vanish}."
)
_RENT_INTRO = (
    "Pět bytů ze stejné čtvrti. Napište měsíční nájem čísly — mezery doplníme. "
    "Vedle sebe uvidíte výhodné kousky i přestřelené ceny. "
    "Dobré nabídky mizí {vanish}."
)
_RENT_HINT = "např. 18 000 · jen čísla, mezery doplníme"
_RENT_TEACH = (
    "Ve stejné čtvrti může větší byt stát míň. Takové nabídky mizí {vanish}."
)
_RENT_RANDOM = (
    "Ve stejné lokalitě se ceny hodně rozcházejí — výhodné kousky mizí {vanish}."
)

_DISP_RE = re.compile(r"(\d+)\s*\+?\s*(kk|1)?", re.I)
_NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def disposition_rank(value: str | None) -> int:
    """Order living size: 1+kk < 1+1 < 2+kk < 2+1 < 3+kk … Unknown is 0."""
    text = str(value or "").strip().lower().replace(" ", "")
    match = _DISP_RE.search(text)
    if not match:
        return 0
    rooms = int(match.group(1))
    kind = (match.group(2) or "1").lower()
    return rooms * 2 - (1 if kind == "kk" else 0)


def pair_locality_key(value: str | None) -> str:
    """Same-place bucket for pairing noisy live labels.

    ``Praha 3``, ``Praha 3 – Žižkov`` and ``Žižkov, Praha 3`` share ``praha-3``.
    Numbered Praha districts never mix (Vinohrady ≠ Žižkov). Other cities keep
    the full neighborhood key so Brno-střed does not pair with Brno-Bystrc.
    """
    key = locality_key(value)
    if not key:
        return ""
    tokens = [token for token in key.split("-") if token]
    if tokens and tokens[0] in {"praha", "prague"} and len(tokens) >= 2 and tokens[1].isdigit():
        return f"praha-{tokens[1]}"
    return key


def _neighborhood_from_key(key: str) -> str | None:
    tokens = [token for token in str(key or "").split("-") if token]
    if not tokens:
        return None
    if tokens[0] in {"praha", "prague"} and len(tokens) >= 3 and tokens[1].isdigit():
        return "-".join(tokens[2:])
    if tokens[0] not in {"praha", "prague"}:
        return None
    if len(tokens) == 1:
        return None
    return None


def _unit_rent(item: dict[str, Any]) -> float | None:
    """Kč / m² from live area. None when area is missing — do not invent it."""
    price = _as_int(item.get("price_czk"))
    area = _as_int(item.get("area_m2"))
    if not price or not area or area <= 0:
        return None
    return price / area


def _m2_deal_gap(cheap: dict[str, Any], dear: dict[str, Any]) -> float | None:
    """Dearer Kč/m² minus cheaper. None if either card has no area."""
    cheap_m2 = _unit_rent(cheap)
    dear_m2 = _unit_rent(dear)
    if cheap_m2 is None or dear_m2 is None:
        return None
    return dear_m2 - cheap_m2


def _meaningful_m2_gap(gap: float, cheap_m2: float) -> bool:
    return gap >= MIN_TEACH_M2_GAP or (cheap_m2 > 0 and gap / cheap_m2 >= MIN_TEACH_M2_RATIO)


def _m2_label(item: dict[str, Any]) -> str:
    rent = _unit_rent(item)
    if rent is None:
        return ""
    return f"{int(round(rent)):,} Kč/m²".replace(",", " ")


def _teach_axes(
    cheap: dict[str, Any], dear: dict[str, Any]
) -> tuple[bool, bool, int, int | None, int | None]:
    """worse_area, worse_disp, disp_gap, cheap_area, dear_area — live-noise safe."""
    cheap_area = _as_int(cheap.get("area_m2"))
    dear_area = _as_int(dear.get("area_m2"))
    cheap_disp = disposition_rank(str(cheap.get("disposition") or ""))
    dear_disp = disposition_rank(str(dear.get("disposition") or ""))
    worse_area = (
        cheap_area is not None
        and dear_area is not None
        and cheap_area > 0
        and dear_area > 0
        and dear_area + MIN_TEACH_AREA_GAP <= cheap_area
    )
    disp_gap = cheap_disp - dear_disp if cheap_disp > 0 and dear_disp > 0 else 0
    worse_disp = disp_gap >= MIN_TEACH_DISP_GAP
    return worse_area, worse_disp, disp_gap, cheap_area, dear_area


def is_teaching_pair(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """True when the dearer flat is also worse (smaller and/or worse disposition).

    Pedagogical lesson: a clearly better flat can still be cheaper — and those
    vanish first. Missing area (unclear Kč/m²), near-ties, and 2+kk vs 2+1
    disposition noise do not count. Both cards need area so the cheaper one is
    also the better Kč/m² deal — a tiny 3+kk cannot masquerade against a larger
    dearer 2+kk.
    """
    cheap, dear = _price_order(left, right)
    if cheap is None:
        return False
    cheap_price = _as_int(cheap.get("price_czk"))
    dear_price = _as_int(dear.get("price_czk"))
    if not cheap_price or not dear_price:
        return False
    gap = dear_price - cheap_price
    if gap < MIN_TEACH_PRICE_GAP and gap / cheap_price < MIN_TEACH_PRICE_RATIO:
        return False
    worse_area, worse_disp, _disp_gap, cheap_area, dear_area = _teach_axes(cheap, dear)
    if not cheap_area or not dear_area:
        return False
    if not (worse_area or worse_disp):
        return False
    m2_gap = _m2_deal_gap(cheap, dear)
    if m2_gap is None:
        return False
    cheap_m2 = _unit_rent(cheap) or 0.0
    return _meaningful_m2_gap(m2_gap, cheap_m2)


def teaching_contrast(left: dict[str, Any], right: dict[str, Any]) -> float:
    """Larger = clearer better+cheaper vs worse+dearer lesson.

    Combines Kč/m² gap, living area, and disposition. 0 means not a teaching pair.
    """
    if not is_teaching_pair(left, right):
        return 0.0
    cheap, dear = _price_order(left, right)
    if cheap is None:
        return 0.0
    cheap_price = _as_int(cheap.get("price_czk")) or 1
    dear_price = _as_int(dear.get("price_czk")) or 0
    worse_area, worse_disp, disp_gap, cheap_area, dear_area = _teach_axes(cheap, dear)
    cheap_m2 = _unit_rent(cheap)
    dear_m2 = _unit_rent(dear)
    if cheap_m2 and dear_m2 and dear_m2 > cheap_m2:
        score = (dear_m2 - cheap_m2) / cheap_m2
        m2_axis = True
    else:
        score = (dear_price - cheap_price) / cheap_price
        m2_axis = False
    if worse_area and cheap_area and dear_area:
        score += (cheap_area - dear_area) / max(dear_area, 1)
    if worse_disp:
        score += disp_gap / 4.0
    axes = int(m2_axis) + int(bool(worse_area)) + int(bool(worse_disp))
    if axes >= 2:
        score += 0.4 * (axes - 1)
    return score


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
    hours, vanish_label = _item_vanish(item)
    card = {
        "id": str(item.get("id") or ""),
        "name": item.get("name") or "",
        "locality": loc,
        "locality_key": item.get("locality_key") or locality_key(str(loc)),
        "pair_key": item.get("pair_key") or pair_locality_key(str(loc)),
        "disposition": item.get("disposition") or "",
        "area_m2": item.get("area_m2"),
        "image_url": _fast_game_image(item.get("image_url"), str(item.get("id") or "")),
        "portal": portal,
        "portal_label": PORTAL_LABELS.get(portal, portal_label(portal)),
        "vanish_hours": hours,
        "vanish_label": vanish_label,
    }
    if include_price:
        card["price_czk"] = item.get("price_czk")
        card["price_label"] = item.get("price_label") or _price_label(item.get("price_czk"))
        m2_label = _m2_label(item)
        if m2_label:
            rent = _unit_rent(item)
            card["price_m2"] = int(round(rent)) if rent is not None else None
            card["price_m2_label"] = m2_label
    return card


def _price_label(price: Any) -> str:
    try:
        return f"{int(price):,} Kč/měsíc".replace(",", " ")
    except (TypeError, ValueError):
        return ""


def _as_int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    match = _NUM_RE.search(str(value).replace("\xa0", " "))
    if not match:
        return None
    try:
        return int(float(match.group(0).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def reset_pool_cache() -> None:
    global _CACHE, _REFRESHING
    with _REFRESH_LOCK:
        _CACHE = None
        _REFRESHING = False
    _PRICE_HINTS.clear()


def _remember_price(item: dict[str, Any]) -> None:
    key = str(item.get("id") or "")
    if not key or not _as_int(item.get("price_czk")):
        return
    _PRICE_HINTS[key] = (time.monotonic(), dict(item))
    if len(_PRICE_HINTS) <= _HINT_CAP:
        return
    oldest = min(_PRICE_HINTS, key=lambda name: _PRICE_HINTS[name][0])
    _PRICE_HINTS.pop(oldest, None)


def _hint_item(key: str) -> dict[str, Any] | None:
    hit = _PRICE_HINTS.get(key)
    if not hit:
        return None
    at, item = hit
    if time.monotonic() - at > _HINT_TTL:
        _PRICE_HINTS.pop(key, None)
        return None
    return item


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


def _is_seed_id(key: Any) -> bool:
    return str(key or "").startswith("seed-")


def _pool_is_seed_only(pool: list[dict[str, Any]] | None) -> bool:
    """True when there is no live catalog to consider — empty or hard-coded seed."""
    rows = pool or []
    if not rows:
        return True
    return all(_is_seed_id(item.get("id")) for item in rows)


def _annotate(item: dict[str, Any]) -> dict[str, Any]:
    row = dict(item)
    loc = str(row.get("locality") or "")
    full = str(row.get("locality_key") or locality_key(loc))
    row["locality_key"] = full
    if not row.get("pair_key"):
        tokens = [token for token in full.split("-") if token]
        if tokens and tokens[0] in {"praha", "prague"} and len(tokens) >= 2 and tokens[1].isdigit():
            row["pair_key"] = f"praha-{tokens[1]}"
        else:
            row["pair_key"] = pair_locality_key(loc) or full
    row["image_url"] = _fast_game_image(row.get("image_url"), str(row.get("id") or ""))
    if row.get("first_seen") or row.get("last_seen"):
        hours, label = _item_vanish(row)
        row["vanish_hours"] = hours
        row["vanish_label"] = label
    return row


def _is_live_game_item(item: dict[str, Any]) -> bool:
    """Priced + imaged catalog row with a locality — never a hard-coded seed."""
    if _is_seed_id(item.get("id")):
        return False
    if not _as_int(item.get("price_czk")):
        return False
    if not str(item.get("image_url") or "").strip():
        return False
    loc = str(item.get("locality_key") or item.get("locality") or "").strip()
    return bool(loc)


def item_estate_kind(item: dict[str, Any]) -> str:
    extras = item.get("extras") if isinstance(item.get("extras"), dict) else None
    return estate_kind(
        url=str(item.get("url") or ""),
        extras=extras,
        name=str(item.get("name") or ""),
        disposition=str(item.get("disposition") or ""),
    )


def _estate_mode(estate: str | None) -> str:
    token = str(estate or DEFAULT_GAME_ESTATE).casefold().strip()
    if token in {"mixed", "mix", "all", "any"}:
        return "mixed"
    return "byty"


def _allows_game_estate(item: dict[str, Any], estate: str | None) -> bool:
    """Default apartment rounds; mixed keeps houses but still drops land/plots."""
    kind = item_estate_kind(item)
    if _estate_mode(estate) == "mixed":
        return kind != "pozemek"
    return kind == "byt"


def filter_game_estate(pool: list[dict[str, Any]] | None, estate: str | None = None) -> list[dict[str, Any]]:
    return [item for item in (pool or []) if _allows_game_estate(item, estate)]


def live_pairable_pool(
    pool: list[dict[str, Any]] | None,
    *,
    estate: str | None = None,
) -> list[dict[str, Any]] | None:
    """Live same-locality listings when the catalog has enough pairs; else None."""
    live = [
        _annotate(item)
        for item in (pool or [])
        if _is_live_game_item(item) and _allows_game_estate(item, estate)
    ]
    groups = _groups_by_locality(live)
    if len(groups) >= MIN_LIVE_LOCALITY_PAIRS:
        return live
    return None


def preferred_game_pool(
    pool: list[dict[str, Any]] | None,
    *,
    estate: str | None = None,
) -> list[dict[str, Any]]:
    """Catalog when it can pair same-locality flats; seed otherwise. Memory-only."""
    rows = pool or []
    if _pool_is_seed_only(rows):
        return _seed_pool()
    live = live_pairable_pool(rows, estate=estate)
    if live is not None:
        return live
    priced = [
        _annotate(item)
        for item in rows
        if _as_int(item.get("price_czk")) and _allows_game_estate(item, estate)
    ]
    if priced and _groups_by_locality(priced):
        return priced
    return _seed_pool()


def _finalize_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduped priced catalog rows. Seed is a pick-time fallback, not mixed in here."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in rows or []:
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
        live = live_pairable_pool(unique)
        prev = _CACHE[1] if _CACHE else []
        prev_live = live_pairable_pool(prev)
        if live is not None:
            unique = live
            _CACHE = (time.monotonic(), unique, False)
        elif prev_live is not None:
            # Budget-aborted / thin refresh must not evict a pairable live pool.
            unique = prev_live
            _CACHE = (time.monotonic(), unique, False)
        else:
            unique = _seed_pool()
            _CACHE = (time.monotonic(), unique, True)
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


def _locality_buckets(pool: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Same-place buckets, including thin groups used to pad a rent round."""
    groups: dict[str, list[dict[str, Any]]] = {}
    homes: dict[str, str] = {}
    for item in pool:
        price = _as_int(item.get("price_czk"))
        if not price:
            continue
        loc = str(item.get("locality") or "")
        full = str(item.get("locality_key") or locality_key(loc))
        if not full:
            continue
        bucket = str(item.get("pair_key") or pair_locality_key(loc) or full)
        item["locality_key"] = full
        item["pair_key"] = bucket
        groups.setdefault(bucket, []).append(item)
        hood = _neighborhood_from_key(full)
        if hood and bucket.startswith("praha-"):
            homes[hood] = bucket
    for key in list(groups):
        home = homes.get(key)
        if home and home != key and key in groups:
            groups.setdefault(home, []).extend(groups.pop(key))
    return groups


def _groups_by_locality(pool: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {key: items for key, items in _locality_buckets(pool).items() if _pairable(items)}


_SEED_GROUPS: dict[str, list[dict[str, Any]]] | None = None


def _seed_groups() -> dict[str, list[dict[str, Any]]]:
    """Same-locality seed buckets, built once. Teaching contrast is unchanged."""
    global _SEED_GROUPS
    cached = _SEED_GROUPS
    if cached is None:
        cached = _groups_by_locality(_seed_pool())
        _SEED_GROUPS = cached
    return cached


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
    if _m2_spread_too_tight(items):
        return []
    return [pair for pair in _candidate_pairs(items) if is_teaching_pair(*pair)]


def _m2_spread_too_tight(items: list[dict[str, Any]]) -> bool:
    """True when every priced m² is within the noise band — no unit-price lesson."""
    rents: list[float] = []
    for item in items:
        rent = _unit_rent(item)
        if rent is None:
            return False
        rents.append(rent)
    if len(rents) < 2:
        return False
    lo, hi = min(rents), max(rents)
    return not _meaningful_m2_gap(hi - lo, lo)


def _has_teaching_pair(items: list[dict[str, Any]]) -> bool:
    if len(items) < 2:
        return False
    if _m2_spread_too_tight(items):
        return False
    ordered = sorted(items, key=_unit_price)
    if is_teaching_pair(ordered[0], ordered[-1]):
        return True
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            if is_teaching_pair(left, right):
                return True
    return False


def _pick_teaching_pair(
    items: list[dict[str, Any]], rng: random.Random
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if len(items) < 2 or _m2_spread_too_tight(items):
        return None
    ordered = sorted(items, key=_unit_price)
    n = len(ordered)
    band = max(4, min(8, (n + 2) // 3))
    cheap_band = ordered[:band]
    dear_band = ordered[-band:]
    scored: list[tuple[tuple[dict[str, Any], dict[str, Any]], float]] = []
    seen: set[frozenset[str]] = set()
    for cheap in cheap_band:
        for dear in dear_band:
            left_id = str(cheap.get("id") or "")
            right_id = str(dear.get("id") or "")
            if not left_id or left_id == right_id:
                continue
            key = frozenset((left_id, right_id))
            if key in seen:
                continue
            seen.add(key)
            score = teaching_contrast(cheap, dear)
            if score > 0:
                scored.append(((cheap, dear), score))
    if not scored:
        pairs = _teaching_pairs(items)
        if not pairs:
            return None
        scored = [(pair, teaching_contrast(*pair)) for pair in pairs]
    scored.sort(key=lambda row: row[1], reverse=True)
    best = scored[0][1]
    floor = best * CLEAR_TEACH_KEEP
    clear = [pair for pair, score in scored if score >= floor]
    keep = max(1, min(2, len(clear)))
    return rng.choice(clear[:keep])


def _parse_stamp(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def game_vanish_hours(first_seen: Any, last_seen: Any) -> tuple[float, bool]:
    """Vanish window from live first/last_seen. last_seen means still listed.

    A just-ingested row has first_seen == last_seen — that is not FOMO evidence.
    Observed spans (last - first) become the copy when they are real.
    """
    first = _parse_stamp(first_seen)
    last = _parse_stamp(last_seen)
    if not first or not last:
        return DEFAULT_VANISH_HOURS, False
    if last < first:
        first, last = last, first
    observed = (last - first).total_seconds() / 3600.0
    if observed < MIN_OBSERVED_VANISH_HOURS:
        return DEFAULT_VANISH_HOURS, False
    return round(min(24.0, max(0.8, observed)), 1), True


def _item_vanish(item: dict[str, Any]) -> tuple[float, str]:
    if item.get("first_seen") or item.get("last_seen"):
        hours, observed = game_vanish_hours(item.get("first_seen"), item.get("last_seen"))
        if observed:
            return hours, _vanish_label(hours)
        return hours, TYPICAL_VANISH_LABEL
    if item.get("vanish_hours") is not None:
        hours = float(item.get("vanish_hours") or DEFAULT_VANISH_HOURS)
        return hours, str(item.get("vanish_label") or _vanish_label(hours))
    return DEFAULT_VANISH_HOURS, TYPICAL_VANISH_LABEL


def _vanish_label(hours: float) -> str:
    value = float(hours or 0)
    if value <= 0:
        return TYPICAL_VANISH_LABEL
    if value < 1:
        minutes = max(8, int(round(value * 60)))
        return f"za {minutes} minut"
    if value < 24:
        pretty = f"{value:.1f}".replace(".", ",")
        return f"za {pretty} h"
    return f"za {int(round(value))} h"


def _pair_vanish(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, str]:
    left_hours, left_label = _item_vanish(left)
    right_hours, right_label = _item_vanish(right)
    observed = {
        label: hours
        for hours, label in ((left_hours, left_label), (right_hours, right_label))
        if label != TYPICAL_VANISH_LABEL
    }
    if observed:
        hours = min(observed.values())
        return hours, _vanish_label(hours)
    return min(left_hours, right_hours), TYPICAL_VANISH_LABEL


def _pair_payload(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    pair_kind: str,
    rng: random.Random,
) -> dict[str, Any]:
    if rng.random() < 0.5:
        left, right = right, left
    vanish, vanish_text = _pair_vanish(left, right)
    cheaper = "left" if int(left["price_czk"]) <= int(right["price_czk"]) else "right"
    loc = left.get("locality") or right.get("locality") or ""
    pair_key = left.get("pair_key") or right.get("pair_key") or pair_locality_key(str(loc))
    return {
        "left": public_card(left, include_price=True),
        "right": public_card(right, include_price=True),
        "cheaper": cheaper,
        "vanish_hours": vanish,
        "pair_kind": pair_kind,
        "locality_key": left.get("locality_key") or locality_key(str(loc)),
        "pair_key": pair_key,
        "locality_label": loc,
        "copy": _INTRO_COPY,
        "copy_ok": (_TEACH_OK if pair_kind == "teaching" else _RANDOM_OK).format(vanish=vanish_text),
        "copy_miss": (_TEACH_MISS if pair_kind == "teaching" else _RANDOM_MISS).format(vanish=vanish_text),
        "vanish_label": vanish_text,
        "seeded": all(_is_seed_id(item.get("id")) for item in (left, right)),
    }


def pick_same_locality_pair(
    pool: list[dict[str, Any]],
    *,
    rng: random.Random | None = None,
    teaching_ratio: float = TEACHING_RATIO,
    estate: str | None = None,
) -> dict[str, Any]:
    """Always same locality_key. ~80 % pedagogical, ~20 % any same-place pair.

    Live hydrated catalog wins over seed whenever it has same-locality pairs.
    Default estate is flats so land/houses do not pollute apartment rounds.
    """
    rng = rng or random.Random()
    if _pool_is_seed_only(pool) and _estate_mode(estate) == "byty":
        groups = _seed_groups()
    else:
        usable = preferred_game_pool(pool, estate=estate)
        groups = _groups_by_locality(usable)
        if not groups:
            groups = _seed_groups()
    want_teaching = rng.random() < max(0.0, min(1.0, float(teaching_ratio)))
    pair_kind = "teaching"
    picked: tuple[dict[str, Any], dict[str, Any]] | None = None
    if want_teaching:
        teaching_groups = {key: items for key, items in groups.items() if _has_teaching_pair(items)}
        if teaching_groups:
            key = rng.choice(list(teaching_groups))
            picked = _pick_teaching_pair(teaching_groups[key], rng)
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
    estate: str | None = None,
) -> dict[str, Any]:
    source = pool if pool is not None else _catalog_pool(store)
    return pick_same_locality_pair(source, rng=rng, teaching_ratio=teaching_ratio, estate=estate)


def _unit_price(item: dict[str, Any]) -> float:
    """Kč per m² when area is known; disposition-scaled fallback otherwise."""
    price = _as_int(item.get("price_czk")) or 0
    area = _as_int(item.get("area_m2"))
    if area and area > 0:
        return price / area
    rank = disposition_rank(str(item.get("disposition") or ""))
    if rank > 0:
        return price / (rank * 22.0)
    return float(price)


def _spread_pick(ordered: list[dict[str, Any]], need: int) -> list[dict[str, Any]]:
    """Even sample of the deal spectrum, always keeping the cheap and dear ends."""
    if need <= 0:
        return []
    if need >= len(ordered):
        return list(ordered)
    if need == 1:
        return [ordered[0]]
    last = len(ordered) - 1
    indexes: list[int] = []
    seen: set[int] = set()
    for step in range(need):
        idx = int(round(step * last / (need - 1)))
        if idx not in seen:
            seen.add(idx)
            indexes.append(idx)
    for idx in range(len(ordered)):
        if len(indexes) >= need:
            break
        if idx not in seen:
            seen.add(idx)
            indexes.append(idx)
    return [ordered[idx] for idx in indexes[:need]]


def _pick_rent_flats(
    items: list[dict[str, Any]],
    *,
    rng: random.Random,
    size: int = RENT_ROUND_SIZE,
    teaching: bool = True,
) -> list[dict[str, Any]]:
    """Same-place flats: live first, then a teaching contrast + deal-spectrum fill."""
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("id") or "")
        if not key or key in seen or not _as_int(item.get("price_czk")):
            continue
        seen.add(key)
        unique.append(item)
    live = [item for item in unique if not _is_seed_id(item.get("id"))]
    seed = [item for item in unique if _is_seed_id(item.get("id"))]
    if 0 < len(live) < size:
        chosen = list(live)
        chosen_ids = {str(item.get("id")) for item in chosen}
        rest = [item for item in unique if str(item.get("id")) not in chosen_ids]
        if teaching and not _has_teaching_pair(chosen):
            extra = _pick_teaching_pair(unique, rng)
            if extra:
                for item in extra:
                    eid = str(item.get("id"))
                    if eid not in chosen_ids:
                        chosen.append(item)
                        chosen_ids.add(eid)
                rest = [item for item in rest if str(item.get("id")) not in chosen_ids]
        chosen.extend(_spread_pick(sorted(rest, key=_unit_price), size - len(chosen)))
        rng.shuffle(chosen)
        return chosen[:size]
    pool = live if len(live) >= size else [*live, *seed]
    if teaching and not _has_teaching_pair(pool) and _has_teaching_pair(unique):
        pool = unique
    if len(pool) <= size:
        rng.shuffle(pool)
        return pool
    chosen = []
    if teaching:
        live_pair = _pick_teaching_pair(live, rng) if len(live) >= 2 else None
        picked = live_pair or _pick_teaching_pair(pool, rng)
        if picked:
            chosen.extend(picked)
    chosen_ids = {str(item.get("id")) for item in chosen}
    rest = [item for item in pool if str(item.get("id")) not in chosen_ids]
    chosen.extend(_spread_pick(sorted(rest, key=_unit_price), size - len(chosen)))
    rng.shuffle(chosen)
    return chosen[:size]


def _round_locality_label(items: list[dict[str, Any]], pair_key: str) -> str:
    labels = [str(item.get("locality") or "").strip() for item in items]
    labels = [label for label in labels if label]
    if not labels:
        return pair_key or ""
    return max(labels, key=len)


def _round_vanish(items: list[dict[str, Any]]) -> tuple[float, str]:
    stamped = [item for item in items if item.get("first_seen") or item.get("last_seen")]
    source = stamped or items
    observed: list[tuple[float, str]] = []
    typical: list[float] = []
    for item in source:
        hours, label = _item_vanish(item)
        if label != TYPICAL_VANISH_LABEL:
            observed.append((hours, label))
        typical.append(hours)
    if observed:
        hours = min(row[0] for row in observed)
        return hours, _vanish_label(hours)
    return min(typical or [DEFAULT_VANISH_HOURS]), TYPICAL_VANISH_LABEL


def _rent_candidates(
    groups: dict[str, list[dict[str, Any]]],
    seed_buckets: dict[str, list[dict[str, Any]]],
    key: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = list(groups.get(key) or [])
    seen = {str(item.get("id")) for item in out}
    for extra in seed_buckets.get(key) or []:
        eid = str(extra.get("id") or "")
        if eid and eid not in seen:
            seen.add(eid)
            out.append(extra)
    return out


def _has_live(items: list[dict[str, Any]]) -> bool:
    return any(not _is_seed_id(item.get("id")) for item in items)


def pick_rent_round(
    pool: list[dict[str, Any]],
    *,
    rng: random.Random | None = None,
    teaching_ratio: float = TEACHING_RATIO,
    estate: str | None = None,
) -> dict[str, Any]:
    """Five flats, one district. ~80 % include a pedagogical deal-vs-overpriced contrast.

    Live catalog listings in that district win; same-district seed only pads a
    thin live group. Never mix Vinohrady with Žižkov. Cold path uses seed.
    Land/plots stay out of apartment rounds unless estate=mixed (houses only).
    """
    rng = rng or random.Random()
    live = live_pairable_pool(pool, estate=estate)
    seed = _seed_pool()
    buckets = _locality_buckets([*(live or []), *seed])
    groups = {key: items for key, items in buckets.items() if _pairable(items)}
    seed_buckets = _locality_buckets(seed)
    if not groups:
        groups = _groups_by_locality(seed)

    filled = {key: _rent_candidates(groups, seed_buckets, key) for key in groups}
    live_full = {key: items for key, items in filled.items() if _has_live(items) and len(items) >= RENT_ROUND_SIZE}
    seed_full = {key: items for key, items in filled.items() if len(items) >= RENT_ROUND_SIZE}
    source = live_full or seed_full or filled

    want_teaching = rng.random() < max(0.0, min(1.0, float(teaching_ratio)))
    pair_kind = "random"
    key = ""
    candidates: list[dict[str, Any]] = []
    if want_teaching:
        teaching_keys = [name for name, items in source.items() if _has_teaching_pair(items)]
        if teaching_keys:
            full = [name for name in teaching_keys if len(source[name]) >= RENT_ROUND_SIZE]
            key = rng.choice(full or teaching_keys)
            candidates = source[key]
            pair_kind = "teaching"
    if not candidates:
        key = rng.choice(list(source))
        candidates = source[key]
        pair_kind = "random"

    chosen = _pick_rent_flats(candidates, rng=rng, teaching=(pair_kind == "teaching"))
    if pair_kind == "teaching" and not _has_teaching_pair(chosen):
        pair_kind = "random"
    if len(chosen) < RENT_ROUND_SIZE:
        seen = {str(item.get("id")) for item in chosen}
        for item in seed_buckets.get(key) or []:
            eid = str(item.get("id") or "")
            if eid and eid not in seen:
                seen.add(eid)
                chosen.append(item)
            if len(chosen) >= RENT_ROUND_SIZE:
                break
        chosen = chosen[:RENT_ROUND_SIZE]
    if len(chosen) < RENT_ROUND_SIZE and seed_full:
        key = rng.choice(list(seed_full))
        chosen = _pick_rent_flats(seed_full[key], rng=rng, teaching=want_teaching)
        pair_kind = "teaching" if want_teaching and _has_teaching_pair(chosen) else "random"

    vanish, vanish_text = _round_vanish(chosen)
    pair_key = key or str(chosen[0].get("pair_key") or "")
    loc = _round_locality_label(chosen, pair_key)
    copy = (_RENT_TEACH if pair_kind == "teaching" else _RENT_RANDOM).format(vanish=vanish_text)
    for item in chosen:
        _remember_price(item)
    seeded = all(_is_seed_id(item.get("id")) for item in chosen)
    return {
        "round_id": uuid.uuid4().hex,
        "items": [public_card(item, include_price=False) for item in chosen],
        "hidden": {str(item["id"]): int(item["price_czk"]) for item in chosen},
        "pool": chosen,
        "seeded": seeded,
        "round_kind": pair_kind,
        "locality_key": chosen[0].get("locality_key") or locality_key(loc),
        "pair_key": pair_key,
        "locality_label": loc,
        "copy": _RENT_INTRO.format(vanish=vanish_text),
        "copy_hint": _RENT_HINT,
        "copy_ok": copy,
        "vanish_hours": vanish,
        "vanish_label": vanish_text,
    }


def rent_round(
    store: Any,
    *,
    rng: random.Random | None = None,
    pool: list[dict[str, Any]] | None = None,
    teaching_ratio: float = TEACHING_RATIO,
    estate: str | None = None,
) -> dict[str, Any]:
    source = pool if pool is not None else _catalog_pool(store)
    return pick_rent_round(source, rng=rng, teaching_ratio=teaching_ratio, estate=estate)


def public_higher_lower(store: Any = None) -> dict[str, Any]:
    """Memory Higher/Lower payload. Live catalog when pairable; seed if cold."""
    schedule_pool_refresh(store)
    return higher_lower_pair(store)


def public_rent_round(store: Any = None) -> dict[str, Any]:
    """Memory rent-round cards. Live catalog when pairable; seed if cold."""
    schedule_pool_refresh(store)
    payload = rent_round(store)
    return {
        "round_id": payload["round_id"],
        "items": payload["items"],
        "locality_label": payload.get("locality_label"),
        "locality_key": payload.get("locality_key"),
        "pair_key": payload.get("pair_key"),
        "vanish_hours": payload.get("vanish_hours"),
        "vanish_label": payload.get("vanish_label"),
        "copy": payload.get("copy"),
        "copy_hint": payload.get("copy_hint") or _RENT_HINT,
        "copy_ok": payload.get("copy_ok"),
        "round_kind": payload.get("round_kind"),
        "seeded": payload.get("seeded"),
    }


def public_rent_score(
    store: Any,
    body: dict[str, Any] | None,
    *,
    allow_db: bool = True,
) -> dict[str, Any]:
    """Score a rent round without blocking the request on a scrape writer."""
    payload = body or {}
    guesses = payload.get("guesses") or []
    if not isinstance(guesses, list) or len(guesses) < 1:
        return {"error": "Chybí tipy", "status": 400}
    ids = [str(item.get("id") or "") for item in guesses if isinstance(item, dict)]
    found = lookup_prices(store, ids, allow_db=allow_db)
    items = [found[key] for key in ids if key in found]
    if len(items) < 1:
        return {"error": "Neznámé byty", "status": 400}
    scored = score_round(items, [item for item in guesses if isinstance(item, dict)])
    name = str(payload.get("name") or "")
    round_id = uuid.uuid4().hex
    player = (name or "").strip()[:64] or "Anonym"
    if store is not None:
        threading.Thread(
            target=_save_rent_round_safe,
            args=(store, name, scored, round_id),
            name="rf-ui-game-save",
            daemon=True,
        ).start()
    return {
        "ok": True,
        "id": round_id,
        "player_name": player,
        "score": scored["score"],
        "max_score": scored["max_score"],
        "accuracy": scored["accuracy"],
        "items": scored["items"],
        "status": 200,
    }


def lookup_prices(store: Any, ids: list[str], *, allow_db: bool = True) -> dict[str, dict[str, Any]]:
    wanted = {str(item) for item in ids if item}
    found: dict[str, dict[str, Any]] = {}
    for item in SEED:
        if item["id"] in wanted:
            found[item["id"]] = item
    for key in list(wanted):
        if key in found:
            continue
        hint = _hint_item(key)
        if hint:
            found[key] = hint
    cached = _CACHE[1] if _CACHE else []
    for item in cached:
        key = str(item.get("id") or "")
        if key in wanted and key not in found:
            found[key] = item
    missing = wanted - set(found)
    if not missing or not allow_db or store is None:
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


def _save_rent_round_safe(
    store: Any, player_name: str, scored: dict[str, Any], round_id: str
) -> None:
    try:
        save_rent_round(store, player_name=player_name, scored=scored, round_id=round_id)
    except Exception:
        return


def save_rent_round(
    store: Any, *, player_name: str, scored: dict[str, Any], round_id: str | None = None
) -> dict[str, Any]:
    name = (player_name or "").strip()[:64] or "Anonym"
    payload = {
        "id": (round_id or uuid.uuid4().hex),
        "player_name": name,
        "score": int(scored.get("score") or 0),
        "accuracy": float(scored.get("accuracy") or 0),
        "guesses_json": json.dumps(scored.get("items") or [], ensure_ascii=False),
        "created_at": utc_now(),
        "saved": False,
    }
    try:
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
        payload["saved"] = True
    except Exception:
        payload["saved"] = False
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
