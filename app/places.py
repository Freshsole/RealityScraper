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
STREET_BUFFER_M = 360
POINT_BUFFER_M = 220
CACHE_VERSION = 6
CACHE_PATH = config.DATA_DIR / "place_geo_cache.json"
_NOMINATIM = "https://nominatim.openstreetmap.org"
_OVERPASS = "https://overpass-api.de/api/interpreter"
_OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
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
    return prepare_item(deepcopy(raw))


def public_geoms(geoms: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    items = []
    for geom in geoms or []:
        if not geom.get("geojson"):
            continue
        items.append(
            {
                "id": geom.get("id"),
                "label": geom.get("label"),
                "kind": geom.get("kind") or "area",
                "geojson": geom.get("geojson"),
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


def ids_from_filters(places: str = "", district: str = "") -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        ident = normalize_osm_id(token)
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


async def search_places(query: str) -> list[dict[str, Any]]:
    needle = query.strip()
    if len(needle) < 2:
        return []
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
    headers = HEADERS
    queries = [needle]
    if "praha" not in folded and "prague" not in folded:
        queries.append(f"{needle} Praha")
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
            for term in queries:
                try:
                    response = await client.get(
                        "https://photon.komoot.io/api/",
                        params={"q": term, "limit": 10, "lat": 50.087, "lon": 14.421},
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
                            }
                        )
                except Exception:
                    pass
                if len(items) >= 12:
                    break
            if len(items) < 8:
                try:
                    rows = await _nominatim_get(
                        client,
                        "/search",
                        {
                            "q": needle,
                            "format": "json",
                            "addressdetails": 1,
                            "limit": 12,
                            "countrycodes": "cz,sk",
                            "namedetails": 1,
                        },
                    )
                except Exception:
                    rows = []
                for row in rows:
                    kind = classify_row(row)
                    if not kind:
                        continue
                    item = _item_from_row(row, kind)
                    if not item["id"] or item["id"] in seen:
                        continue
                    seen.add(item["id"])
                    items.append(item)
                    if len(items) >= 12:
                        break
    except Exception:
        return items[:10]
    return items[:12]


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
    geo = item.get("geojson") if isinstance(item.get("geojson"), dict) else None
    if geo:
        item["geojson"] = _simplify_geo(geo) or geo
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
            async with httpx.AsyncClient(timeout=8.0, headers=HEADERS) as client:
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
    if item.get("street_checked") and item.get("v") == CACHE_VERSION and item.get("geojson"):
        item["buffer_m"] = STREET_BUFFER_M
        return prepare_item(item)
    lat, lon = _midpoint(item.get("geojson") if isinstance(item.get("geojson"), dict) else None, item.get("lat"), item.get("lon"))
    name = str(item.get("label") or "").split(",")[0].strip()
    way_ids: list[int] = []
    if ident.startswith("W") and ident[1:].isdigit():
        way_ids.append(int(ident[1:]))
    lines: list[list[list[float]]] = []
    try:
        if ident.startswith("W"):
            for row in await _nominatim_lookup([ident]):
                parsed = _from_nominatim_row(row)
                if not parsed:
                    continue
                name = str(parsed.get("label") or name).split(",")[0].strip() or name
                lat = parsed.get("lat") if parsed.get("lat") is not None else lat
                lon = parsed.get("lon") if parsed.get("lon") is not None else lon
                lines.extend(_lines_from_geo(parsed.get("geojson") if isinstance(parsed.get("geojson"), dict) else None))
        if name and lat is not None and lon is not None:
            way_ids.extend(await _photon_street_ids(name, float(lat), float(lon)))
        way_ids = list(dict.fromkeys(way_ids))
        if way_ids:
            merged: list[list[list[float]]] = []
            for row in await _nominatim_lookup([f"W{item_id}" for item_id in way_ids]):
                parsed = _from_nominatim_row(row)
                if parsed:
                    merged.extend(_lines_from_geo(parsed.get("geojson") if isinstance(parsed.get("geojson"), dict) else None))
            if merged:
                lines = merged
        if name and lat is not None and lon is not None:
            try:
                payload = await _overpass_json(
                    "[out:json][timeout:12];"
                    f'way["name"="{_overpass_escape(name)}"]["highway"](around:2500,{lat},{lon});'
                    "out geom;"
                )
                seen: set[tuple[float, float, float, float]] = {
                    (round(line[0][0], 5), round(line[0][1], 5), round(line[-1][0], 5), round(line[-1][1], 5))
                    for line in lines
                    if line
                }
                for element in payload.get("elements") or []:
                    geo = _way_geojson(element)
                    if not geo:
                        continue
                    coords = geo["coordinates"]
                    key = (round(coords[0][0], 5), round(coords[0][1], 5), round(coords[-1][0], 5), round(coords[-1][1], 5))
                    if key in seen:
                        continue
                    seen.add(key)
                    lines.append(coords)
            except Exception:
                pass
    except Exception:
        pass
    if not lines and isinstance(item.get("geojson"), dict):
        geo = item["geojson"]
        if geo.get("type") == "LineString":
            lines = [geo.get("coordinates") or []]
        elif geo.get("type") == "MultiLineString":
            lines = list(geo.get("coordinates") or [])
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
    if name and not str(item.get("label") or "").lower().startswith(name.lower()):
        item["label"] = name
    return prepare_item(item)


async def _overpass_way(osm_id: str) -> dict[str, Any] | None:
    if not osm_id.startswith("W"):
        return None
    try:
        payload = await _overpass_json(f"[out:json][timeout:20];way({osm_id[1:]});out geom;")
    except Exception:
        return None
    for element in payload.get("elements") or []:
        geo = _way_geojson(element)
        if geo:
            lat = geo["coordinates"][len(geo["coordinates"]) // 2][1]
            lon = geo["coordinates"][len(geo["coordinates"]) // 2][0]
            return await _expand_street(
                {
                    "id": osm_id,
                    "kind": "street",
                    "label": osm_id,
                    "lat": lat,
                    "lon": lon,
                    "geojson": geo,
                    "buffer_m": STREET_BUFFER_M,
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
    return item


async def geometries(ids: list[str], *, network: bool = True) -> list[dict[str, Any]]:
    _load_disk_cache()
    wanted = [normalize_osm_id(item) for item in ids if normalize_osm_id(item)]
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
        order = {ident: index for index, ident in enumerate(wanted)}
        found.sort(key=lambda item: order.get(item.get("id"), 999))
        return found
    for ident in way_missing:
        extra = await _overpass_way(ident)
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
                        "polygon_threshold": 0.002,
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
            extra = await _overpass_way(ident)
            if extra:
                found.append(extra)
                got.add(ident)
    prepared: list[dict[str, Any]] = []
    for item in found:
        if item.get("kind") == "street" and not item.get("street_full"):
            item = await _expand_street(item)
        else:
            item = prepare_item(item)
        _memory[item["id"]] = item
        prepared.append(item)
    if missing or any(item.get("kind") == "street" for item in prepared):
        _save_disk_cache()
    order = {ident: index for index, ident in enumerate(wanted)}
    prepared.sort(key=lambda item: order.get(item["id"], 999))
    return prepared


async def attach_geoms(filters: dict[str, Any]) -> dict[str, Any]:
    ids = ids_from_filters(str(filters.get("places") or ""), str(filters.get("district") or ""))
    if not ids:
        filters["place_geoms"] = []
        return filters
    geoms = await geometries(ids, network=False)
    have = {str(item.get("id") or "") for item in geoms}
    missing = [ident for ident in ids if ident not in have]
    if missing:
        try:
            extra = await asyncio.wait_for(geometries(missing, network=True), 2.5)
            geoms.extend(extra)
        except Exception:
            pass
    filters["place_geoms"] = geoms
    return filters
