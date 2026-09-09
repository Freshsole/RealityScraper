from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx

from app import config

HEADERS = {"User-Agent": "RealityScraper/1.2"}
WALK_FACTOR = 1.2
DRIVE_TRAFFIC = 1.5


def _seconds(value: Any) -> int | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return int(round(seconds))


def _parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _scale(seconds: int | None, factor: float) -> int | None:
    if seconds is None:
        return None
    return int(round(seconds * factor))


async def _osrm(client: httpx.AsyncClient, base: str, lon1: float, lat1: float, lon2: float, lat2: float) -> int | None:
    try:
        response = await client.get(
            f"{base}/{lon1},{lat1};{lon2},{lat2}",
            params={"overview": "false"},
        )
        response.raise_for_status()
        routes = (response.json() or {}).get("routes") or []
        return _seconds((routes[0] or {}).get("duration")) if routes else None
    except Exception:
        return None


def _has_transit(item: dict[str, Any]) -> bool:
    for leg in item.get("legs") or []:
        mode = str(leg.get("mode") or "").upper()
        if mode and mode not in {"WALK", "BICYCLE", "CAR"}:
            return True
    return False


def _transit_itinerary_seconds(item: dict[str, Any]) -> int | None:
    ride = _seconds(item.get("duration"))
    end = _parse_when(item.get("endTime"))
    if end is None:
        return ride
    door = int((end - datetime.now(timezone.utc)).total_seconds())
    if door < 60:
        return ride
    if ride is None:
        return door
    return max(ride, min(door, ride + 40 * 60))


async def _transit(client: httpx.AsyncClient, lat1: float, lon1: float, lat2: float, lon2: float) -> int | None:
    try:
        response = await client.get(
            "https://api.transitous.org/api/v1/plan",
            params={
                "fromPlace": f"{lat1},{lon1}",
                "toPlace": f"{lat2},{lon2}",
                "numItineraries": 5,
            },
        )
        response.raise_for_status()
        for item in (response.json() or {}).get("itineraries") or []:
            if not _has_transit(item):
                continue
            seconds = _transit_itinerary_seconds(item)
            if seconds:
                return seconds
        return None
    except Exception:
        return None


async def _google_mode(
    client: httpx.AsyncClient,
    origin: str,
    destination: str,
    mode: str,
) -> int | None:
    key = config.GOOGLE_MAPS_API_KEY
    if not key:
        return None
    params = {
        "origin": origin,
        "destination": destination,
        "mode": mode,
        "language": "cs",
        "region": "cz",
        "key": key,
    }
    if mode in {"driving", "transit"}:
        params["departure_time"] = "now"
    try:
        response = await client.get("https://maps.googleapis.com/maps/api/directions/json", params=params)
        response.raise_for_status()
        routes = (response.json() or {}).get("routes") or []
        legs = (routes[0] or {}).get("legs") or [] if routes else []
        if not legs:
            return None
        leg = legs[0]
        if mode == "driving":
            traffic = (leg.get("duration_in_traffic") or {}).get("value")
            if traffic is not None:
                return _seconds(traffic)
        return _seconds((leg.get("duration") or {}).get("value"))
    except Exception:
        return None


async def route_times(
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    from_address: str = "",
    to_address: str = "",
) -> dict[str, int | None]:
    origin = from_address.strip() or f"{from_lat},{from_lon}"
    destination = to_address.strip() or f"{to_lat},{to_lon}"
    async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as client:
        if config.GOOGLE_MAPS_API_KEY:
            drive, walk, transit = await asyncio.gather(
                _google_mode(client, origin, destination, "driving"),
                _google_mode(client, origin, destination, "walking"),
                _google_mode(client, origin, destination, "transit"),
            )
            if drive or walk or transit:
                return {"drive": drive, "walk": walk, "transit": transit}
        drive, walk, transit = await asyncio.gather(
            _osrm(
                client,
                "https://routing.openstreetmap.de/routed-car/route/v1/driving",
                from_lon,
                from_lat,
                to_lon,
                to_lat,
            ),
            _osrm(
                client,
                "https://routing.openstreetmap.de/routed-foot/route/v1/driving",
                from_lon,
                from_lat,
                to_lon,
                to_lat,
            ),
            _transit(client, from_lat, from_lon, to_lat, to_lon),
        )
    return {
        "drive": _scale(drive, DRIVE_TRAFFIC),
        "walk": _scale(walk, WALK_FACTOR),
        "transit": transit,
    }
