"""Best-effort visitor city from the request IP, used to localise the homepage preview."""
from __future__ import annotations

import ipaddress
import time

import httpx

_TTL_S = 6 * 3600
_cache: dict[str, tuple[float, str]] = {}

_CITY_CS = {
    "prague": "Praha",
    "pilsen": "Plzeň",
    "plzen": "Plzeň",
    "budweis": "České Budějovice",
    "ceske budejovice": "České Budějovice",
    "hradec kralove": "Hradec Králové",
    "usti nad labem": "Ústí nad Labem",
    "olomouc": "Olomouc",
    "pardubice": "Pardubice",
    "liberec": "Liberec",
    "ostrava": "Ostrava",
    "brno": "Brno",
}
_HEADERS = ("cf-ipcity", "x-vercel-ip-city", "x-appengine-city")


def _public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)


def _normalize(city: str) -> str:
    city = (city or "").strip()
    return _CITY_CS.get(city.casefold(), city)


async def city_for_request(headers, ip: str) -> str:
    """Czech city name for the visitor, or "" when unknown."""
    for name in _HEADERS:
        value = headers.get(name)
        if value:
            return _normalize(value)
    # Localhost / LAN visitor (dev): ask for the machine's own public IP instead.
    target = ip if _public(ip) else ""
    ip = target or "self"
    hit = _cache.get(ip)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    city = ""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"https://ipwho.is/{target}", params={"fields": "success,country_code,city"})
            data = r.json()
        if data.get("success") and data.get("country_code") == "CZ":
            city = _normalize(str(data.get("city") or ""))
    except Exception:
        city = ""
    if len(_cache) > 5000:
        _cache.clear()
    _cache[ip] = (time.time(), city)
    return city
