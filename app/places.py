from __future__ import annotations

import asyncio
import json
import math
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from app import bezrealitky_url, config, localities

HEADERS = {"User-Agent": "RealityScraper/1.2"}
STREET_BUFFER_M = 45
POINT_BUFFER_M = 220
CACHE_VERSION = 10
CACHE_PATH = config.DATA_DIR / "place_geo_cache.json"
_NOMINATIM = "https://nominatim.openstreetmap.org"
_OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
_lock = asyncio.Lock()
_last_nominatim = 0.0
_memory: dict[str, dict[str, Any]] = {}
_BUNDLED: dict[str, dict[str, Any]] | None = None
_SHAPES_PATH = Path(__file__).resolve().parent / "place_shapes.json"


def bundled_shapes() -> dict[str, dict[str, Any]]:
    global _BUNDLED
    if _BUNDLED is None:
        if _SHAPES_PATH.exists():
            try:
                _BUNDLED = json.loads(_SHAPES_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                _BUNDLED = {}
        else:
            _BUNDLED = {}
    return _BUNDLED


def cached_item(ident: str) -> dict[str, Any] | None:
    ident = normalize_osm_id(ident)
    raw = bundled_shapes().get(ident) or _memory.get(ident)
    if not isinstance(raw, dict) or not raw.get("geojson"):
        return None
    geo = raw.get("geojson") if isinstance(raw.get("geojson"), dict) else {}
    if raw.get("kind") == "street" and geo.get("type") == "Point":
        return None
    item = prepare_item(deepcopy(raw))
    if item.get("kind") == "area" and not _shape_ready(item):
        return None
    return item


def public_geoms(geoms: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items = []
    for geom in geoms or []:
        if not geom.get("geojson"):
            continue
        geo = geom.get("geojson") if isinstance(geom.get("geojson"), dict) else None
        kind = geom.get("kind") or "area"
        if kind == "street" and geo and geo.get("type") == "Point":
            continue
        if not geo:
            continue
        items.append(
            {
                "id": geom.get("id"),
                "label": geom.get("label"),
                "kind": kind,
                "geojson": geo,
                "buffer_m": geom.get("buffer_m") or 0,
                "lat": geom.get("lat"),
                "lon": geom.get("lon"),
            }
        )
    return items


def normalize_osm_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw[0] in "rRwWnN" and raw[1:].isdigit():
        return f"{raw[0].upper()}{raw[1:]}"
    return raw


def parse_place_token(token: str) -> dict[str, Any]:
    from urllib.parse import unquote

    raw = str(token or "").strip()
    parts = raw.split("~")
    ident = normalize_osm_id(parts[0] if parts else "")
    lat = lon = None
    if len(parts) >= 3 and parts[1] and parts[2]:
        try:
            lat = float(parts[1])
            lon = float(parts[2])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                lat = lon = None
        except (TypeError, ValueError):
            lat = lon = None
    label = unquote("~".join(parts[3:])).strip() if len(parts) >= 4 else ""
    return {"id": ident, "lat": lat, "lon": lon, "label": label}


def point_fallback(ident: str, lat: float, lon: float, kind: str | None = None, label: str = "") -> dict[str, Any]:
    kind = kind or ("street" if ident.startswith("W") else "point")
    return prepare_item(
        {
            "id": ident,
            "label": label or ident,
            "kind": kind,
            "lat": lat,
            "lon": lon,
            "buffer_m": STREET_BUFFER_M if kind == "street" else POINT_BUFFER_M,
            "geojson": {"type": "Point", "coordinates": [lon, lat]},
        }
    )


def ids_from_filters(places: str = "", district: str = "") -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        ident = parse_place_token(token)["id"]
        if not ident or ident in seen:
            return
        if ident[0] in "RWN" and ident[1:].isdigit():
            seen.add(ident)
            found.append(ident)

    for raw in str(places or "").split(","):
        add(raw)
    for raw in str(district or "").split(","):
        token = raw.strip()
        if not token:
            continue
        add(token)
        osm = localities.SREALITY_TO_OSM.get(token.casefold())
        if osm:
            add(osm)
    return found


def _load_disk_cache() -> None:
    if _memory or not CACHE_PATH.exists():
        return
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, dict) and value.get("geojson") and value.get("v") == CACHE_VERSION:
                _memory[str(key)] = value


def _save_disk_cache() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_memory, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _short_label(row: dict[str, Any]) -> str:
    addr = row.get("address") if isinstance(row.get("address"), dict) else {}
    name = (
        (row.get("namedetails") or {}).get("name")
        if isinstance(row.get("namedetails"), dict)
        else ""
    )
    display = str(row.get("display_name") or "").split(",")[0].strip()
    road = str(addr.get("road") or addr.get("pedestrian") or "").strip()
    city = str(addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or "").strip()
    suburb = str(addr.get("suburb") or addr.get("city_district") or addr.get("quarter") or "").strip()
    if road:
        parts = [road]
        if suburb and suburb.casefold() != city.casefold():
            parts.append(suburb)
        if city:
            parts.append(city)
        return ", ".join(parts)
    return str(name or display or suburb or city or "Místo").strip()


def classify_row(row: dict[str, Any]) -> str | None:
    osm_type = str(row.get("osm_type") or "")
    cls = str(row.get("class") or "")
    typ = str(row.get("type") or "")
    geo = row.get("geojson") if isinstance(row.get("geojson"), dict) else {}
    geo_type = str(geo.get("type") or "")
    if cls in {"amenity", "shop", "tourism", "office", "craft", "leisure"} and osm_type != "relation":
        return None
    if cls == "highway" or geo_type in {"LineString", "MultiLineString"}:
        return "street"
    if cls in {"boundary", "place", "landuse"} or osm_type == "relation":
        return "area"
    if geo_type in {"Polygon", "MultiPolygon"}:
        return "area"
    if osm_type in {"node", "way"}:
        return "point"
    return None


def _osm_prefix(osm_type: str) -> str:
    mapping = {"relation": "R", "way": "W", "node": "N"}
    return mapping.get(str(osm_type), "")


async def _throttle() -> None:
    global _last_nominatim
    async with _lock:
        wait = 1.1 - (time.monotonic() - _last_nominatim)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_nominatim = time.monotonic()


async def _nominatim_get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    await _throttle()
    response = await client.get(f"{_NOMINATIM}{path}", params=params)
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else []


async def _nominatim_lookup(ids: list[str]) -> list[dict[str, Any]]:
    wanted = [normalize_osm_id(item) for item in ids if normalize_osm_id(item)]
    if not wanted:
        return []
    async with httpx.AsyncClient(timeout=16.0, headers=HEADERS) as client:
        return await _nominatim_get(
            client,
            "/lookup",
            {
                "osm_ids": ",".join(wanted),
                "format": "json",
                "addressdetails": 1,
                "polygon_geojson": 1,
            },
        )


def _lines_from_geo(geo: dict[str, Any] | None) -> list[list[list[float]]]:
    if not geo:
        return []
    kind = geo.get("type")
    coords = geo.get("coordinates")
    if kind == "LineString" and isinstance(coords, list) and len(coords) >= 2:
        return [coords]
    if kind == "MultiLineString":
        return [line for line in coords or [] if isinstance(line, list) and len(line) >= 2]
    return []


def _item_from_row(row: dict[str, Any], kind: str) -> dict[str, Any]:
    prefix = _osm_prefix(str(row.get("osm_type") or ""))
    osm_id = row.get("osm_id")
    ident = f"{prefix}{osm_id}" if prefix and osm_id else ""
    try:
        lat = float(row.get("lat"))
        lon = float(row.get("lon"))
    except (TypeError, ValueError):
        lat = lon = None
    return {
        "id": ident,
        "label": _short_label(row),
        "kind": kind,
        "lat": lat,
        "lon": lon,
    }


def _photon_kind(props: dict[str, Any]) -> str | None:
    key = str(props.get("osm_key") or "")
    value = str(props.get("osm_value") or "")
    osm_type = str(props.get("osm_type") or "").upper()[:1]
    if key == "highway":
        if osm_type != "W" or value in {"bus_stop", "platform", "rest_area", "services"}:
            return None
        return "street"
    if key in {"boundary", "place"} or value in {
        "administrative",
        "cadastral_community",
        "city",
        "town",
        "village",
        "suburb",
        "neighbourhood",
        "quarter",
        "city_district",
        "municipality",
        "borough",
        "district",
    }:
        return "area"
    return None


def _photon_label(props: dict[str, Any]) -> str:
    name = str(props.get("name") or props.get("street") or "").strip()
    district = str(props.get("district") or props.get("locality") or "").strip()
    city = str(props.get("city") or props.get("town") or props.get("village") or "").strip()
    parts = [part for part in (name, district if district.casefold() != city.casefold() else "", city) if part]
    return ", ".join(parts) or name


def _place_role(item: dict[str, Any] | None) -> str:
    raw = item or {}
    value = str(raw.get("osm_value") or raw.get("place_type") or raw.get("type") or "").casefold()
    key = str(raw.get("osm_key") or raw.get("place_class") or raw.get("class") or "").casefold()
    if value in {"city", "town", "village", "municipality", "administrative"} or (
        key == "boundary" and value == "administrative"
    ):
        return "muni"
    if "cadastral" in value:
        return "cadastral"
    if value in {"suburb", "neighbourhood", "quarter", "city_district", "borough", "district"}:
        return "part"
    return "other"


def _rank_place(item: dict[str, Any], needle: str) -> tuple:
    ident = str(item.get("id") or "")
    label = str(item.get("label") or "").casefold()
    name = label.split(",")[0].strip()
    query = needle.strip().casefold()
    kind = item.get("kind")
    role = _place_role(item)
    score = 0
    if ident.startswith("R"):
        score += 80
    elif ident.startswith("W") and kind == "area":
        score += 50
    elif ident.startswith("W"):
        score += 25
    else:
        score -= 40
    if role == "muni":
        score += 120
    elif role == "cadastral":
        score += 25 if "praha" in label or "prague" in label else -70
    elif role == "part":
        score += 15 if "praha" in label or "prague" in label else -20
    if "praha" in label or "prague" in label:
        score += 20 if "praha" in query or "prague" in query or len(query.split()) == 1 else -10
    if name == query:
        score += 50
    elif name.startswith(query):
        score += 20
    extent = item.get("extent")
    if isinstance(extent, (list, tuple)) and len(extent) >= 4:
        try:
            width = abs(float(extent[2]) - float(extent[0]))
            height = abs(float(extent[1]) - float(extent[3]))
            score += min(40, int(width * height * 20000))
        except (TypeError, ValueError):
            pass
    return (-score, ident)


_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_SEARCH_TTL = 600.0


async def search_places(query: str) -> list[dict[str, Any]]:
    needle = query.strip()
    if len(needle) < 2:
        return []
    cache_key = needle.casefold()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _SEARCH_TTL:
        return cached[1]
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    folded = needle.casefold()
    for ident, label in bezrealitky_url.DISTRICTS:
        if folded not in label.casefold():
            continue
        if ident in seen:
            continue
        seen.add(ident)
        items.append({"id": ident, "label": label, "kind": "area", "lat": None, "lon": None})
    try:
        async with httpx.AsyncClient(timeout=4.0, headers=HEADERS) as client:
            response = await client.get(
                "https://photon.komoot.io/api/",
                params={"q": needle, "limit": 12, "lat": 50.087, "lon": 14.421},
            )
            response.raise_for_status()
            for feature in (response.json() or {}).get("features") or []:
                props = feature.get("properties") or {}
                kind = _photon_kind(props)
                if not kind:
                    continue
                osm_type = str(props.get("osm_type") or "").upper()[:1]
                osm_id = props.get("osm_id")
                if osm_type not in {"R", "W", "N"} or not osm_id:
                    continue
                ident = f"{osm_type}{osm_id}"
                if ident in seen:
                    continue
                seen.add(ident)
                coords = (feature.get("geometry") or {}).get("coordinates") or []
                lat = lon = None
                if len(coords) >= 2 and isinstance(coords[0], (int, float)):
                    lon, lat = float(coords[0]), float(coords[1])
                items.append(
                    {
                        "id": ident,
                        "label": _photon_label(props),
                        "kind": kind,
                        "lat": lat,
                        "lon": lon,
                        "osm_key": str(props.get("osm_key") or ""),
                        "osm_value": str(props.get("osm_value") or ""),
                        "extent": props.get("extent"),
                    }
                )
                if len(items) >= 12:
                    break
    except Exception:
        pass
    items.sort(key=lambda item: _rank_place(item, needle))
    items = items[:12]
    _SEARCH_CACHE[cache_key] = (time.monotonic(), items)
    return items


def _walk_coords(coords: Any, acc: list[tuple[float, float]]) -> None:
    if not isinstance(coords, list) or not coords:
        return
    if isinstance(coords[0], (int, float)) and len(coords) >= 2:
        acc.append((float(coords[1]), float(coords[0])))
        return
    for item in coords:
        _walk_coords(item, acc)


def bbox_of(geoms: list[dict[str, Any]]) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []
    pad_m = 0.0
    for geom in geoms:
        geo = geom.get("geojson") if isinstance(geom.get("geojson"), dict) else {}
        _walk_coords(geo.get("coordinates"), points)
        try:
            if geom.get("lat") is not None and geom.get("lon") is not None:
                points.append((float(geom["lat"]), float(geom["lon"])))
        except (TypeError, ValueError):
            pass
        pad_m = max(pad_m, float(geom.get("buffer_m") or 0), STREET_BUFFER_M if geom.get("kind") == "street" else 0)
    if not points:
        return None
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    mid_lat = (min(lats) + max(lats)) / 2
    pad_lat = max(0.003, pad_m / 111000.0)
    pad_lon = max(0.003, pad_m / max(30000.0, 111000.0 * math.cos(math.radians(mid_lat))))
    return (min(lats) - pad_lat, max(lats) + pad_lat, min(lons) - pad_lon, max(lons) + pad_lon)


def _perp_dist(point: list[float], start: list[float], end: list[float]) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - (start[0] + t * dx), point[1] - (start[1] + t * dy))


def _rdp(points: list[Any], epsilon: float) -> list[Any]:
    cleaned = [pt for pt in points if isinstance(pt, (list, tuple)) and len(pt) >= 2]
    if len(cleaned) < 3:
        return cleaned
    dmax = 0.0
    idx = 0
    start, end = cleaned[0], cleaned[-1]
    for i in range(1, len(cleaned) - 1):
        dist = _perp_dist(cleaned[i], start, end)
        if dist > dmax:
            idx = i
            dmax = dist
    if dmax > epsilon:
        left = _rdp(cleaned[: idx + 1], epsilon)
        right = _rdp(cleaned[idx:], epsilon)
        return left[:-1] + right
    return [cleaned[0], cleaned[-1]]


def _simplify_geo(geojson: dict[str, Any] | None, epsilon: float = 0.00025) -> dict[str, Any] | None:
    if not geojson:
        return geojson
    kind = geojson.get("type")
    coords = geojson.get("coordinates")
    if kind == "LineString":
        pts = _rdp(coords or [], epsilon)
        return {"type": "LineString", "coordinates": pts if len(pts) >= 2 else coords}
    if kind == "MultiLineString":
        lines = []
        for line in coords or []:
            pts = _rdp(line, epsilon)
            if len(pts) >= 2:
                lines.append(pts)
        return {"type": "MultiLineString", "coordinates": lines or coords}
    if kind == "Polygon":
        rings = []
        for i, ring in enumerate(coords or []):
            pts = _rdp(ring, epsilon if i == 0 else epsilon * 0.6)
            if len(pts) >= 3:
                if pts[0] != pts[-1]:
                    pts = pts + [pts[0]]
                rings.append(pts)
        return {"type": "Polygon", "coordinates": rings or coords}
    if kind == "MultiPolygon":
        polys = []
        for poly in coords or []:
            simplified = _simplify_geo({"type": "Polygon", "coordinates": poly}, epsilon)
            if simplified and simplified.get("coordinates"):
                polys.append(simplified["coordinates"])
        return {"type": "MultiPolygon", "coordinates": polys or coords}
    if kind == "GeometryCollection":
        geoms = [_simplify_geo(item, epsilon) for item in geojson.get("geometries") or [] if isinstance(item, dict)]
        return {"type": "GeometryCollection", "geometries": [item for item in geoms if item]}
    return geojson


def _in_bbox(lat: float, lon: float, box: tuple[float, float, float, float] | None) -> bool:
    if not box:
        return True
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def prepare_item(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("kind") == "street":
        item["buffer_m"] = STREET_BUFFER_M
    elif item.get("kind") == "point":
        item["buffer_m"] = POINT_BUFFER_M
    geo = item.get("geojson") if isinstance(item.get("geojson"), dict) else None
    if geo:
        item["geojson"] = _simplify_geo(geo, 0.00004 if item.get("kind") == "street" else 0.00012) or geo
        geo = item["geojson"]
        if geo.get("type") == "Point":
            coords = geo.get("coordinates") or []
            if len(coords) >= 2:
                item.setdefault("lon", float(coords[0]))
                item.setdefault("lat", float(coords[1]))
    item["bbox"] = bbox_of([item])
    item["v"] = CACHE_VERSION
    return item


def _ring_contains(lat: float, lon: float, ring: list[Any]) -> bool:
    pts = []
    for pair in ring:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            pts.append((float(pair[0]), float(pair[1])))
    if len(pts) < 3:
        return False
    inside = False
    j = len(pts) - 1
    for i, (xi, yi) in enumerate(pts):
        xj, yj = pts[j]
        denom = (yj - yi) or 1e-16
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / denom + xi):
            inside = not inside
        j = i
    return inside


def _in_polygon(lat: float, lon: float, rings: list[Any]) -> bool:
    if not rings:
        return False
    if not _ring_contains(lat, lon, rings[0]):
        return False
    for hole in rings[1:]:
        if _ring_contains(lat, lon, hole):
            return False
    return True


def _geo_contains(lat: float, lon: float, geojson: dict[str, Any]) -> bool:
    kind = geojson.get("type")
    coords = geojson.get("coordinates")
    if kind == "Polygon":
        return _in_polygon(lat, lon, coords or [])
    if kind == "MultiPolygon":
        return any(_in_polygon(lat, lon, poly) for poly in (coords or []) if isinstance(poly, list))
    if kind == "GeometryCollection":
        return any(_geo_contains(lat, lon, item) for item in geojson.get("geometries") or [] if isinstance(item, dict))
    return False


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _point_to_segment_m(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    lat0 = math.radians((lat + a[0] + b[0]) / 3)
    kx = 111320.0 * math.cos(lat0)
    ky = 110540.0
    px, py = lon * kx, lat * ky
    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _line_distance_m(lat: float, lon: float, coords: list[Any]) -> float:
    pts: list[tuple[float, float]] = []
    _walk_coords(coords, pts)
    if not pts:
        return 1e12
    if len(pts) == 1:
        return _haversine_m(lat, lon, pts[0][0], pts[0][1])
    best = 1e12
    for i in range(len(pts) - 1):
        best = min(best, _point_to_segment_m(lat, lon, pts[i], pts[i + 1]))
    return best


def _line_distance_geo(lat: float, lon: float, geojson: dict[str, Any]) -> float:
    kind = geojson.get("type")
    coords = geojson.get("coordinates")
    if kind == "LineString":
        return _line_distance_m(lat, lon, coords or [])
    if kind == "MultiLineString":
        return min((_line_distance_m(lat, lon, line) for line in coords or []), default=1e12)
    if kind in {"Polygon", "MultiPolygon"}:
        return 0.0 if _geo_contains(lat, lon, geojson) else 1e12
    if kind == "GeometryCollection":
        return min(
            (_line_distance_geo(lat, lon, item) for item in geojson.get("geometries") or [] if isinstance(item, dict)),
            default=1e12,
        )
    if kind == "Point":
        pts: list[tuple[float, float]] = []
        _walk_coords(coords, pts)
        return _haversine_m(lat, lon, pts[0][0], pts[0][1]) if pts else 1e12
    return 1e12


def point_matches(lat: Any, lon: Any, geoms: list[dict[str, Any]]) -> bool:
    if not geoms:
        return True
    try:
        plat = float(lat)
        plon = float(lon)
    except (TypeError, ValueError):
        return False
    for geom in geoms:
        box = geom.get("bbox")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            if not _in_bbox(plat, plon, (float(box[0]), float(box[1]), float(box[2]), float(box[3]))):
                continue
        geo = geom.get("geojson") if isinstance(geom.get("geojson"), dict) else {}
        kind = geom.get("kind") or classify_row({"geojson": geo, "class": ""}) or "area"
        buffer_m = float(geom.get("buffer_m") or (STREET_BUFFER_M if kind == "street" else POINT_BUFFER_M if kind == "point" else 0))
        if kind == "area" and geo:
            if _geo_contains(plat, plon, geo):
                return True
            continue
        if geo:
            if _line_distance_geo(plat, plon, geo) <= buffer_m:
                return True
            continue
        try:
            if _haversine_m(plat, plon, float(geom["lat"]), float(geom["lon"])) <= buffer_m:
                return True
        except (TypeError, ValueError, KeyError):
            continue
    return False


def _way_geojson(element: dict[str, Any]) -> dict[str, Any] | None:
    geom = element.get("geometry") or []
    coords = [[float(pt["lon"]), float(pt["lat"])] for pt in geom if "lat" in pt and "lon" in pt]
    if len(coords) < 2:
        return None
    return {"type": "LineString", "coordinates": coords}


def _overpass_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


async def _overpass_json(query: str) -> dict[str, Any]:
    last_error: Exception | None = None
    for url in _OVERPASS_URLS:
        try:
            async with httpx.AsyncClient(timeout=6.0, headers=HEADERS) as client:
                response = await client.post(url, data={"data": query})
                response.raise_for_status()
                payload = response.json()
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    return {}


def _midpoint(geojson: dict[str, Any] | None, lat: Any = None, lon: Any = None) -> tuple[float | None, float | None]:
    try:
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    except (TypeError, ValueError):
        pass
    pts: list[tuple[float, float]] = []
    if geojson:
        _walk_coords(geojson.get("coordinates"), pts)
    if not pts:
        return None, None
    mid = pts[len(pts) // 2]
    return mid[0], mid[1]


async def _photon_street_ids(name: str, lat: float, lon: float) -> list[int]:
    ids: list[int] = []
    terms = [name]
    folded = name.casefold()
    if "praha" not in folded and "prague" not in folded:
        terms.append(f"{name} Praha")
        terms.append(f"{name} Praha 7")
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as client:
            for term in terms:
                response = await client.get(
                    "https://photon.komoot.io/api/",
                    params={"q": term, "limit": 12, "lat": lat, "lon": lon},
                )
                response.raise_for_status()
                for feature in (response.json() or {}).get("features") or []:
                    props = feature.get("properties") or {}
                    if str(props.get("osm_key") or "") != "highway":
                        continue
                    osm_type = str(props.get("osm_type") or "").upper()[:1]
                    osm_id = props.get("osm_id")
                    if osm_type != "W" or not osm_id:
                        continue
                    coords = (feature.get("geometry") or {}).get("coordinates") or []
                    if len(coords) >= 2 and isinstance(coords[0], (int, float)):
                        if _haversine_m(lat, lon, float(coords[1]), float(coords[0])) > 4000:
                            continue
                    street_name = str(props.get("name") or props.get("street") or "").strip()
                    if street_name and street_name.casefold() != name.casefold():
                        continue
                    ids.append(int(osm_id))
    except Exception:
        return list(dict.fromkeys(ids))
    return list(dict.fromkeys(ids))


async def _expand_street(item: dict[str, Any]) -> dict[str, Any]:
    ident = str(item.get("id") or "")
    geo = item.get("geojson") if isinstance(item.get("geojson"), dict) else None
    if item.get("street_checked") and item.get("v") == CACHE_VERSION and geo and "LineString" in str(geo.get("type") or ""):
        item["buffer_m"] = STREET_BUFFER_M
        return prepare_item(item)
    lat, lon = _midpoint(geo, item.get("lat"), item.get("lon"))
    name = str(item.get("label") or "").split(",")[0].strip()
    if name.startswith("W") and name[1:].isdigit():
        name = ""
    lines = _lines_from_geo(geo)
    way_ids: list[int] = []
    if ident.startswith("W") and ident[1:].isdigit():
        way_ids.append(int(ident[1:]))
    if name and lat is not None and lon is not None:
        try:
            way_ids.extend(await _photon_street_ids(name, float(lat), float(lon)))
        except Exception:
            pass
    seen: set[tuple[float, float, float, float]] = {
        (round(line[0][0], 5), round(line[0][1], 5), round(line[-1][0], 5), round(line[-1][1], 5))
        for line in lines
        if line
    }
    for way_id in list(dict.fromkeys(way_ids))[:12]:
        extra = await _osm_api_way(f"W{way_id}")
        if not extra:
            continue
        for coords in _lines_from_geo(extra.get("geojson") if isinstance(extra.get("geojson"), dict) else None):
            if len(coords) < 2:
                continue
            key = (round(coords[0][0], 5), round(coords[0][1], 5), round(coords[-1][0], 5), round(coords[-1][1], 5))
            if key in seen:
                continue
            seen.add(key)
            lines.append(coords)
    lines = [line for line in lines if isinstance(line, list) and len(line) >= 2]
    if lines:
        item["geojson"] = (
            {"type": "LineString", "coordinates": lines[0]}
            if len(lines) == 1
            else {"type": "MultiLineString", "coordinates": lines}
        )
        lat, lon = _midpoint(item["geojson"], lat, lon)
        item["lat"] = lat
        item["lon"] = lon
    item["kind"] = "street"
    item["buffer_m"] = STREET_BUFFER_M
    item["street_full"] = True
    item["street_checked"] = True
    label = str(item.get("label") or "")
    if name and (not label or label.startswith("W")):
        item["label"] = name
    return prepare_item(item)


async def _osm_api_way(osm_id: str) -> dict[str, Any] | None:
    if not osm_id.startswith("W") or not osm_id[1:].isdigit():
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0, headers=HEADERS) as client:
            response = await client.get(f"https://api.openstreetmap.org/api/0.6/way/{osm_id[1:]}/full.json")
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    elements = payload.get("elements") if isinstance(payload, dict) else None
    if not isinstance(elements, list):
        return None
    nodes = {
        int(el["id"]): el
        for el in elements
        if el.get("type") == "node" and "id" in el and "lat" in el and "lon" in el
    }
    for element in elements:
        if element.get("type") != "way":
            continue
        coords = []
        for node_id in element.get("nodes") or []:
            node = nodes.get(int(node_id))
            if not node:
                continue
            coords.append([float(node["lon"]), float(node["lat"])])
        if len(coords) < 2:
            continue
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        mid = coords[len(coords) // 2]
        return prepare_item(
            {
                "id": osm_id,
                "kind": "street",
                "label": name or osm_id,
                "lat": mid[1],
                "lon": mid[0],
                "geojson": {"type": "LineString", "coordinates": coords},
                "buffer_m": STREET_BUFFER_M,
                "street_full": False,
                "street_checked": False,
            }
        )
    return None


async def _overpass_way(osm_id: str) -> dict[str, Any] | None:
    if not osm_id.startswith("W"):
        return None
    try:
        payload = await _overpass_json(f"[out:json][timeout:12];way({osm_id[1:]});out geom;")
    except Exception:
        return None
    for element in payload.get("elements") or []:
        geo = _way_geojson(element)
        if not geo:
            continue
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        mid = geo["coordinates"][len(geo["coordinates"]) // 2]
        return prepare_item(
            {
                "id": osm_id,
                "kind": "street",
                "label": name or osm_id,
                "lat": mid[1],
                "lon": mid[0],
                "geojson": geo,
                "buffer_m": STREET_BUFFER_M,
                "street_full": False,
                "street_checked": False,
            }
        )
    return None


def _from_nominatim_row(row: dict[str, Any]) -> dict[str, Any] | None:
    kind = classify_row(row) or "area"
    item = _item_from_row(row, kind)
    geo = row.get("geojson") if isinstance(row.get("geojson"), dict) else None
    if not item["id"]:
        return None
    if kind == "street":
        item["buffer_m"] = STREET_BUFFER_M
    elif kind == "point":
        item["buffer_m"] = POINT_BUFFER_M
    else:
        item["buffer_m"] = 0
    if geo:
        item["geojson"] = geo
    item["place_type"] = str(row.get("type") or "")
    item["place_class"] = str(row.get("class") or "")
    item["place_rank"] = row.get("place_rank")
    return item


def _primary_name(value: str | None) -> str:
    return str(value or "").split(",")[0].strip().casefold()


def _bbox_span(item: dict[str, Any] | None) -> float:
    box = (item or {}).get("bbox") or bbox_of([item] if item else [])
    if not box:
        return 0.0
    return max(0.0, float(box[1]) - float(box[0])) * max(0.0, float(box[3]) - float(box[2]))


def _adopt_polygon(item: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    item["geojson"] = source.get("geojson")
    item["kind"] = "area"
    item["buffer_m"] = 0
    item["place_type"] = source.get("place_type") or item.get("place_type")
    item["place_class"] = source.get("place_class") or item.get("place_class")
    if source.get("lat") is not None:
        item["lat"] = source.get("lat")
        item["lon"] = source.get("lon")
    return prepare_item(item)


async def _search_named_polygons(name: str) -> list[dict[str, Any]]:
    if len(name) < 2:
        return []
    query = name
    if "praha" not in name.casefold() and "prague" not in name.casefold() and len(name.split()) == 1:
        query = f"{name} Praha"
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=HEADERS) as client:
            rows = await _nominatim_get(
                client,
                "/search",
                {
                    "q": query,
                    "format": "json",
                    "addressdetails": 1,
                    "limit": 10,
                    "countrycodes": "cz",
                    "polygon_geojson": 1,
                },
            )
    except Exception:
        return []
    found: list[dict[str, Any]] = []
    for row in rows:
        parsed = _from_nominatim_row(row)
        if parsed and parsed.get("kind") != "street" and _shape_ready(parsed):
            found.append(prepare_item(parsed))
    return found


def _nearby(item: dict[str, Any], other: dict[str, Any], limit_m: float) -> bool:
    try:
        if item.get("lat") is None or other.get("lat") is None:
            return True
        return _haversine_m(float(item["lat"]), float(item["lon"]), float(other["lat"]), float(other["lon"])) <= limit_m
    except (TypeError, ValueError, KeyError):
        return True


async def _polygon_for_area(item: dict[str, Any]) -> dict[str, Any] | None:
    if _shape_ready(item):
        return item
    name = str(item.get("label") or "").split(",")[0].strip()
    rows = await _search_named_polygons(name)
    best: dict[str, Any] | None = None
    best_key: tuple = ()
    for parsed in rows:
        if not _nearby(item, parsed, 12000):
            continue
        role = _place_role(parsed)
        rank = 2 if role == "muni" else 1 if role == "part" else 0
        same = 1 if _primary_name(parsed.get("label")) == _primary_name(name) else 0
        key = (same, rank, _bbox_span(parsed))
        if not best or key > best_key:
            best, best_key = parsed, key
    if not best:
        return None
    return _adopt_polygon(item, best)


async def _upgrade_to_municipality(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("kind") != "area" or not _shape_ready(item):
        return item
    role = _place_role(item)
    if role == "muni":
        return item
    label = str(item.get("label") or "")
    if role == "part" and ("praha" in label.casefold() or "prague" in label.casefold()):
        return item
    if role not in {"cadastral", "other"}:
        return item
    name = str(label).split(",")[0].strip()
    rows = await _search_named_polygons(name)
    current = _bbox_span(item)
    best: dict[str, Any] | None = None
    for parsed in rows:
        if _place_role(parsed) != "muni":
            continue
        if _primary_name(parsed.get("label")) != _primary_name(name):
            continue
        if not _nearby(item, parsed, 12000):
            continue
        if _bbox_span(parsed) <= current * 1.08:
            continue
        if not best or _bbox_span(parsed) > _bbox_span(best):
            best = parsed
    if not best:
        return item
    return _adopt_polygon(item, best)


async def geometries(ids: list[str], *, network: bool = True) -> list[dict[str, Any]]:
    _load_disk_cache()
    wanted: list[str] = []
    hints: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for raw in ids:
        token = parse_place_token(raw)
        ident = token["id"]
        if not ident:
            continue
        if token["lat"] is not None and token["lon"] is not None:
            hints[ident] = token
        if ident in seen_ids:
            continue
        seen_ids.add(ident)
        wanted.append(ident)
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    way_missing: list[str] = []
    seen: set[str] = set()
    for ident in wanted:
        cached = cached_item(ident)
        if cached:
            found.append(cached)
            seen.add(ident)
        elif ident.startswith("W"):
            way_missing.append(ident)
        else:
            missing.append(ident)
    if not network:
        return found
    for ident in way_missing:
        extra = await _osm_api_way(ident) or await _overpass_way(ident)
        if extra:
            found.append(extra)
            seen.add(ident)
        else:
            missing.append(ident)
    if missing:
        try:
            async with httpx.AsyncClient(timeout=16.0, headers=HEADERS) as client:
                rows = await _nominatim_get(
                    client,
                    "/lookup",
                    {
                        "osm_ids": ",".join(missing),
                        "format": "json",
                        "addressdetails": 1,
                        "polygon_geojson": 1,
                    },
                )
        except Exception:
            rows = []
        got: set[str] = set()
        for row in rows:
            item = _from_nominatim_row(row)
            if not item or not item.get("geojson"):
                continue
            got.add(item["id"])
            found.append(item)
        for ident in missing:
            if ident in got:
                continue
            extra = await _osm_api_way(ident) or await _overpass_way(ident)
            if extra:
                found.append(extra)
                got.add(ident)
    prepared: list[dict[str, Any]] = []
    for item in found:
        if item.get("kind") == "street" and not item.get("street_checked"):
            try:
                item = await asyncio.wait_for(_expand_street(item), 3.0)
            except Exception:
                item = prepare_item(item)
                item["street_checked"] = True
        else:
            item = prepare_item(item)
            if network and item.get("kind") == "area":
                if not _shape_ready(item):
                    try:
                        upgraded = await asyncio.wait_for(_polygon_for_area(item), 8.0)
                    except Exception:
                        upgraded = None
                    if upgraded:
                        item = upgraded
                if _shape_ready(item):
                    try:
                        item = await asyncio.wait_for(_upgrade_to_municipality(item), 8.0)
                    except Exception:
                        pass
        if _shape_ready(item):
            _memory[item["id"]] = item
        hint = hints.get(item["id"])
        if hint and hint.get("label") and str(item.get("label") or "").startswith("W"):
            item["label"] = hint["label"]
        prepared.append(item)
    if missing or any(item.get("kind") == "street" and item.get("geojson", {}).get("type") != "Point" for item in prepared):
        _save_disk_cache()
    return prepared


def _shape_ready(item: dict[str, Any] | None) -> bool:
    geo = item.get("geojson") if isinstance((item or {}).get("geojson"), dict) else {}
    kind = str(geo.get("type") or "")
    return "LineString" in kind or "Polygon" in kind


def _place_lookup_ids(filters: dict[str, Any]) -> list[str]:
    ids = ids_from_filters(str(filters.get("places") or ""), str(filters.get("district") or ""))
    originals: dict[str, str] = {}
    for raw in str(filters.get("places") or "").split(","):
        token = parse_place_token(raw)
        if token["id"]:
            originals[token["id"]] = raw.strip()
    return [originals.get(ident, ident) for ident in ids]


async def attach_geoms(filters: dict[str, Any]) -> dict[str, Any]:
    lookup = _place_lookup_ids(filters)
    ids = ids_from_filters(str(filters.get("places") or ""), str(filters.get("district") or ""))
    if not ids:
        filters["place_geoms"] = []
        return filters
    geoms = await geometries(lookup, network=False)
    have = {
        str(item.get("id") or "")
        for item in geoms
        if _shape_ready(item) and (item.get("kind") != "street" or item.get("street_checked"))
    }
    missing = [token for token in lookup if parse_place_token(token)["id"] not in have]
    if missing:
        try:
            extra = await asyncio.wait_for(geometries(missing, network=True), 12.0)
            geoms.extend(extra)
        except Exception:
            pass
    by_id: dict[str, dict[str, Any]] = {}
    for item in geoms:
        ident = str(item.get("id") or "")
        if not ident:
            continue
        prev = by_id.get(ident)
        if not prev or (_shape_ready(item) and not _shape_ready(prev)):
            by_id[ident] = item
    for token in lookup:
        parsed = parse_place_token(token)
        ident = parsed["id"]
        if ident in by_id and _shape_ready(by_id[ident]):
            if parsed.get("label") and str(by_id[ident].get("label") or "").startswith("W"):
                by_id[ident]["label"] = parsed["label"]
            continue
        if parsed["lat"] is not None and parsed["lon"] is not None:
            by_id[ident] = point_fallback(ident, parsed["lat"], parsed["lon"], label=parsed.get("label") or "")
    filters["place_geoms"] = [by_id[ident] for ident in ids if ident in by_id]
    if ids and not filters["place_geoms"]:
        filters["place_empty"] = True
    return filters
