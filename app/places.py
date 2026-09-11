from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any

import httpx

from app import bezrealitky_url, config, localities

HEADERS = {"User-Agent": "RealityScraper/1.2"}
STREET_BUFFER_M = 140
POINT_BUFFER_M = 180
CACHE_PATH = config.DATA_DIR / "place_geo_cache.json"
_NOMINATIM = "https://nominatim.openstreetmap.org"
_OVERPASS = "https://overpass-api.de/api/interpreter"
_lock = asyncio.Lock()
_last_nominatim = 0.0
_memory: dict[str, dict[str, Any]] = {}


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
            if isinstance(value, dict) and value.get("geojson"):
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
    pad_deg = max(0.002, pad_m / 100000)
    return (min(lats) - pad_deg, max(lats) + pad_deg, min(lons) - pad_deg, max(lons) + pad_deg)


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


async def _overpass_way(osm_id: str) -> dict[str, Any] | None:
    if not osm_id.startswith("W"):
        return None
    query = f"[out:json][timeout:20];way({osm_id[1:]});out geom;"
    try:
        async with httpx.AsyncClient(timeout=22.0, headers=HEADERS) as client:
            response = await client.post(_OVERPASS, data={"data": query})
            response.raise_for_status()
            payload = response.json()
    except Exception:
        return None
    for element in payload.get("elements") or []:
        geo = _way_geojson(element)
        if geo:
            lat = geo["coordinates"][len(geo["coordinates"]) // 2][1]
            lon = geo["coordinates"][len(geo["coordinates"]) // 2][0]
            return {
                "id": osm_id,
                "kind": "street",
                "label": osm_id,
                "lat": lat,
                "lon": lon,
                "geojson": geo,
                "buffer_m": STREET_BUFFER_M,
            }
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


async def geometries(ids: list[str]) -> list[dict[str, Any]]:
    _load_disk_cache()
    wanted = [normalize_osm_id(item) for item in ids if normalize_osm_id(item)]
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for ident in wanted:
        cached = _memory.get(ident)
        if cached and cached.get("geojson"):
            found.append(cached)
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
            _memory[item["id"]] = item
            got.add(item["id"])
            found.append(item)
        for ident in missing:
            if ident in got:
                continue
            extra = await _overpass_way(ident)
            if extra:
                _memory[ident] = extra
                found.append(extra)
        _save_disk_cache()
    order = {ident: index for index, ident in enumerate(wanted)}
    found.sort(key=lambda item: order.get(item["id"], 999))
    return found


async def attach_geoms(filters: dict[str, Any]) -> dict[str, Any]:
    ids = ids_from_filters(str(filters.get("places") or ""), str(filters.get("district") or ""))
    filters["place_geoms"] = await geometries(ids) if ids else []
    return filters
