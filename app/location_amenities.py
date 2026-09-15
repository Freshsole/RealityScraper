"""Lokalita & dostupnost — skóre z MHD, obchodů, parků (OSM Overpass)."""

from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from app import places

# Cache raw OSM elements by ~100 m grid cell (shared across listings).
_ELEMENTS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL = 6 * 3600
_EMPTY_TTL = 20 * 60
_OVERPASS_TIMEOUT = 8.0
_RADIUS_M = 850

_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Soft priors when OSM is unavailable (Praha-focused). Values push top areas to ~8+.
_DISTRICT_BONUS: list[tuple[re.Pattern[str], float, str]] = [
    (
        re.compile(
            r"josefov|malá\s+strana|staré\s+město|nové\s+město|vinohrady|dejvice|"
            r"holešovice|holesovice|karlín|karlin|letná|letna",
            re.I,
        ),
        4.6,
        "Centrum / top lokalita",
    ),
    (
        re.compile(
            r"smíchov|smichov|vršovice|vrsovice|bubeneč|bubenec|nusle|žížkov|zizkov|"
            r"podolí|podoli|výšehrad|vysehrad",
            re.I,
        ),
        3.6,
        "Dobrá městská lokalita",
    ),
    (re.compile(r"praha\s*[1-3]\b|centro|centrum", re.I), 4.2, "Praha 1–3"),
    (re.compile(r"praha\s*[4-7]\b", re.I), 2.4, "Praha 4–7"),
    (re.compile(r"praha\s*[8-9]\b|praha\s*10", re.I), 1.4, "Širší Praha"),
]

_TEXT_HINTS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\bmetro\b|stanic[ei]\s+metr", re.I), "metro", 0.7),
    (re.compile(r"tramvaj|tram\b|zastávk", re.I), "tramvaj", 0.55),
    (re.compile(r"autobus|bus\b|mhd", re.I), "MHD", 0.25),
    (re.compile(r"supermarket|albert|billa|tesco|lidl|penny|kaufmann|smíšené\s+zboží|potraviny", re.I), "obchod", 0.45),
    (re.compile(r"obchodní\s+centrum|\boc\b|mall|palladium|quadrio|nový\s+smíchov", re.I), "OC", 0.4),
    (re.compile(r"park\b|stromovka|letná|petřín|petrin", re.I), "park", 0.35),
    (re.compile(r"lékárn|pharmacy|škola|skola|školka", re.I), "služby", 0.2),
]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 3)}:{round(lon, 3)}"


def _element_xy(el: dict[str, Any]) -> tuple[float, float] | None:
    try:
        if el.get("lat") is not None and el.get("lon") is not None:
            return float(el["lat"]), float(el["lon"])
        center = el.get("center") or {}
        if center.get("lat") is not None and center.get("lon") is not None:
            return float(center["lat"]), float(center["lon"])
    except (TypeError, ValueError):
        return None
    return None


def _classify(tags: dict[str, Any]) -> str | None:
    railway = str(tags.get("railway") or "")
    highway = str(tags.get("highway") or "")
    station = str(tags.get("station") or "")
    pt = str(tags.get("public_transport") or "")
    shop = str(tags.get("shop") or "")
    amenity = str(tags.get("amenity") or "")
    leisure = str(tags.get("leisure") or "")

    if railway in {"subway_entrance", "station"} or station == "subway" or tags.get("subway") == "yes":
        return "metro"
    if railway == "tram_stop" or tags.get("tram") == "yes" or (pt and tags.get("tram") == "yes"):
        return "tram"
    if highway == "bus_stop" or tags.get("bus") == "yes" or railway == "halt":
        return "bus"
    if shop in {"supermarket", "convenience", "grocery", "greengrocer"} or amenity == "marketplace":
        return "grocery"
    if shop in {"mall", "department_store"} or "shopping_centre" in shop:
        return "mall"
    if leisure == "park" or tags.get("landuse") == "recreation_ground":
        return "park"
    if amenity in {"cafe", "restaurant", "fast_food", "pharmacy", "clinic", "doctors"}:
        return "services"
    return None


def _label_for(kind: str, tags: dict[str, Any], dist_m: float) -> str:
    name = str(tags.get("name") or tags.get("name:cs") or "").strip()
    kind_cs = {
        "metro": "Metro",
        "tram": "Tramvaj",
        "bus": "Bus",
        "grocery": "Obchod",
        "mall": "OC",
        "park": "Park",
        "services": "Služby",
    }.get(kind, kind)
    meters = int(round(dist_m / 10.0) * 10)
    if name:
        short = name if len(name) <= 28 else name[:26] + "…"
        return f"{kind_cs} {short} · {meters} m"
    return f"{kind_cs} · {meters} m"


def _points_for_distance(dist_m: float, *, near: float, mid: float, far: float, max_pts: float) -> float:
    if dist_m <= near:
        return max_pts
    if dist_m <= mid:
        return max_pts * 0.75
    if dist_m <= far:
        return max_pts * 0.4
    return 0.0


def score_from_elements(
    lat: float,
    lon: float,
    elements: list[dict[str, Any]],
    *,
    locality: str = "",
    text: str = "",
) -> dict[str, Any]:
    best: dict[str, tuple[float, str, dict[str, Any]]] = {}
    counts = {"metro": 0, "tram": 0, "bus": 0, "grocery": 0, "mall": 0, "park": 0, "services": 0}

    for el in elements:
        tags = el.get("tags") if isinstance(el.get("tags"), dict) else {}
        kind = _classify(tags)
        if not kind:
            continue
        xy = _element_xy(el)
        if not xy:
            continue
        dist = _haversine_m(lat, lon, xy[0], xy[1])
        if dist > _RADIUS_M + 50:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        prev = best.get(kind)
        if prev is None or dist < prev[0]:
            best[kind] = (dist, _label_for(kind, tags, dist), tags)

    score = 3.2
    factors: list[str] = []

    if "metro" in best:
        d = best["metro"][0]
        score += _points_for_distance(d, near=250, mid=550, far=900, max_pts=2.4)
        factors.append(best["metro"][1])
    if "tram" in best:
        d = best["tram"][0]
        score += _points_for_distance(d, near=180, mid=400, far=700, max_pts=2.0)
        factors.append(best["tram"][1])
    elif "bus" in best:
        d = best["bus"][0]
        score += _points_for_distance(d, near=150, mid=350, far=600, max_pts=1.1)
        factors.append(best["bus"][1])

    if "grocery" in best:
        d = best["grocery"][0]
        score += _points_for_distance(d, near=200, mid=450, far=800, max_pts=1.8)
        factors.append(best["grocery"][1])
        if counts.get("grocery", 0) >= 2:
            score += 0.25
    if "mall" in best:
        d = best["mall"][0]
        score += _points_for_distance(d, near=350, mid=700, far=1000, max_pts=1.2)
        factors.append(best["mall"][1])
    if "park" in best:
        d = best["park"][0]
        score += _points_for_distance(d, near=250, mid=500, far=800, max_pts=1.1)
        factors.append(best["park"][1])
    if counts.get("services", 0) >= 3:
        score += 0.55
        if "services" in best:
            factors.append(best["services"][1])
    elif "services" in best:
        score += 0.25

    osm_rich = len(best) >= 3
    district_label = ""
    if not osm_rich:
        for pattern, label, bonus in _TEXT_HINTS:
            if pattern.search(text) or pattern.search(locality):
                score += bonus
                if label not in " ".join(factors).lower():
                    factors.append(label.capitalize())
        for pattern, bonus, label in _DISTRICT_BONUS:
            if pattern.search(locality) or pattern.search(text):
                score += bonus
                district_label = label
                break
    else:
        for pattern, bonus, label in _DISTRICT_BONUS:
            if pattern.search(locality):
                score += min(0.5, bonus * 0.12)
                break

    score = round(max(1.0, min(10.0, score)), 1)
    if factors:
        note = " · ".join(factors[:3])
    elif district_label:
        note = district_label
    else:
        note = locality.strip() or "Lokalita bez blízkých POI"
    return {
        "score": score,
        "note": note,
        "factors": factors[:5],
        "counts": counts,
        "source": "osm" if best else "heuristic",
    }


def _overpass_nearby(lat: float, lon: float) -> list[dict[str, Any]]:
    query = f"""
[out:json][timeout:12];
(
  node["railway"="tram_stop"](around:{_RADIUS_M},{lat},{lon});
  node["railway"="subway_entrance"](around:{_RADIUS_M},{lat},{lon});
  node["highway"="bus_stop"](around:550,{lat},{lon});
  node["shop"~"supermarket|convenience|grocery|mall|department_store"](around:{_RADIUS_M},{lat},{lon});
  node["leisure"="park"](around:700,{lat},{lon});
  node["amenity"~"cafe|restaurant|pharmacy"](around:450,{lat},{lon});
);
out center tags;
""".strip()
    headers = {"User-Agent": "Realitify/1.0 (location score)"}
    for url in _OVERPASS_URLS:
        try:
            with httpx.Client(timeout=_OVERPASS_TIMEOUT, headers=headers) as client:
                response = client.post(url, data={"data": query})
                response.raise_for_status()
                payload = response.json()
            if isinstance(payload, dict):
                els = payload.get("elements")
                if isinstance(els, list) and els:
                    return els
        except Exception:
            continue
    return []


def _row_coords(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = float(row["lat"])
        lon = float(row["lon"])
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (KeyError, TypeError, ValueError):
        pass
    locality = str(row.get("locality") or "").strip()
    if not locality:
        return None
    try:
        point = places.approx_point_from_locality(locality)
    except Exception:
        point = None
    if point:
        return float(point[0]), float(point[1])
    return None


def _row_text(row: dict[str, Any]) -> str:
    extras = row.get("extras") if isinstance(row.get("extras"), dict) else {}
    bits = [
        str(row.get("name") or ""),
        str(row.get("locality") or ""),
        str(row.get("description") or ""),
        str(extras.get("description") or ""),
        str(extras.get("text") or ""),
    ]
    specs = extras.get("specs") or []
    if isinstance(specs, list):
        for spec in specs:
            if isinstance(spec, dict):
                bits.append(str(spec.get("label") or ""))
                bits.append(str(spec.get("value") or ""))
    return " ".join(bits)


def _cache_fresh(key: str) -> list[dict[str, Any]] | None:
    hit = _ELEMENTS_CACHE.get(key)
    if not hit:
        return None
    ts, elements = hit
    ttl = _CACHE_TTL if elements else _EMPTY_TTL
    if time.time() - ts >= ttl:
        return None
    return elements


def _store_elements(key: str, elements: list[dict[str, Any]]) -> None:
    _ELEMENTS_CACHE[key] = (time.time(), elements)
    if len(_ELEMENTS_CACHE) > 4000:
        oldest = sorted(_ELEMENTS_CACHE.items(), key=lambda kv: kv[1][0])[:800]
        for k, _ in oldest:
            _ELEMENTS_CACHE.pop(k, None)


def warm_amenity_cache(rows: list[dict[str, Any]], *, max_fetches: int = 16) -> None:
    """Prefetch OSM amenities for unique grid cells so every listing can score."""
    pending: dict[str, tuple[float, float]] = {}
    for row in rows:
        if not row or row.get("_stub"):
            continue
        coords = _row_coords(row)
        if not coords:
            continue
        key = _cache_key(coords[0], coords[1])
        if _cache_fresh(key) is not None:
            continue
        pending[key] = coords
        if len(pending) >= max_fetches:
            break
    if not pending:
        return

    def fetch(item: tuple[str, tuple[float, float]]) -> tuple[str, list[dict[str, Any]]]:
        key, (lat, lon) = item
        return key, _overpass_nearby(lat, lon)

    workers = min(4, len(pending))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch, item) for item in pending.items()]
        for fut in as_completed(futures):
            try:
                key, elements = fut.result()
            except Exception:
                continue
            _store_elements(key, elements)


def location_accessibility(row: dict[str, Any], *, allow_network: bool = True) -> dict[str, Any]:
    locality = str(row.get("locality") or "").strip()
    text = _row_text(row)
    coords = _row_coords(row)

    if coords is None:
        result = score_from_elements(0.0, 0.0, [], locality=locality, text=text)
        result["source"] = "heuristic"
        return result

    lat, lon = coords
    key = _cache_key(lat, lon)
    elements = _cache_fresh(key)
    if elements is None:
        elements = []
        if allow_network:
            elements = _overpass_nearby(lat, lon)
            _store_elements(key, elements)

    return score_from_elements(lat, lon, elements, locality=locality, text=text)
