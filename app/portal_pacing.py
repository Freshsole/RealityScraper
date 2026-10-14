"""Per-portal request pacing + 429 cooldown (honours Retry-After).

Why: reality.idnes.cz answers 429 with `Retry-After: 3600` once an IP exceeds roughly
~2500 requests / 20 min (measured: block hit exactly at 2533 req in the preceding 20 min,
~4 req/s bursts of 15/s). The block is per IP, covers the whole subdomain (HTML + XHR) and
every further request while blocked is just wasted (and probably renews the ban). So:

* `wait_turn(portal)`  - token-bucket style spacing: at least `min_interval` seconds (+ jitter)
  between request *starts* to the same portal, shared by every client in the process.
* `note_response(portal, response)` - on 429 start a cooldown (Retry-After, capped, with
  exponential growth on repeated 429), on a 2xx reset it.
* `check_cooldown(portal)` - raises a synthetic httpx 429 error while the cooldown runs, so
  ScrapeEngine / AdaptiveLimiter treat it exactly like a real 429 but no packet is sent.

Everything is keyed by portal name; portals without configured spacing are untouched.
"""

from __future__ import annotations

import asyncio
import os
import random
import time

import httpx

# seconds between request starts, per portal (env PORTAL_MIN_INTERVAL_<PORTAL> overrides, "0" = off)
DEFAULT_MIN_INTERVAL: dict[str, float] = {
    "idnes": 1.0,
    # HTML portals measured at ~1 req/s without 403/429; their pages are 70-430 kB.
    "remax": 0.5,
    "realitymix": 0.5,
    "realitycz": 1.0,
    "ceskereality": 1.0,
    "mmreality": 1.0,
    "annonce": 0.5,
    "espolubydleni": 0.5,
    "eurobydleni": 0.5,
}
JITTER = 0.25  # +-25 %
MAX_COOLDOWN_SEC = 3600.0
DEFAULT_COOLDOWN_SEC = 300.0

_next_slot: dict[str, float] = {}
_cooldown_until: dict[str, float] = {}
_strikes: dict[str, int] = {}
_locks: dict[str, asyncio.Lock] = {}


def _key(portal: str) -> str:
    return (portal or "").strip().lower()


def reset() -> None:
    _next_slot.clear()
    _cooldown_until.clear()
    _strikes.clear()
    _locks.clear()


def min_interval(portal: str) -> float:
    key = _key(portal)
    raw = os.getenv(f"PORTAL_MIN_INTERVAL_{key.upper()}")
    if raw not in (None, ""):
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_MIN_INTERVAL.get(key, 0.0)


def cooldown_remaining(portal: str) -> float:
    return max(0.0, _cooldown_until.get(_key(portal), 0.0) - time.monotonic())


def check_cooldown(portal: str, url: str = "") -> None:
    left = cooldown_remaining(portal)
    if left <= 0:
        return
    request = httpx.Request("GET", url or f"https://{_key(portal)}.invalid/")
    response = httpx.Response(429, headers={"Retry-After": str(int(left) + 1)}, request=request)
    raise httpx.HTTPStatusError(
        f"429 Too Many Requests (local cooldown {int(left)}s left for {_key(portal)})",
        request=request,
        response=response,
    )


async def wait_turn(portal: str) -> None:
    gap = min_interval(portal)
    if gap <= 0:
        return
    key = _key(portal)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:  # serialises slot hand-out only; the sleep is outside the HTTP call
        now = time.monotonic()
        slot = max(now, _next_slot.get(key, 0.0))
        _next_slot[key] = slot + gap * random.uniform(1.0 - JITTER, 1.0 + JITTER)
    delay = slot - now
    if delay > 0:
        await asyncio.sleep(delay)


def _retry_after(response: httpx.Response) -> float | None:
    raw = (response.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def note_response(portal: str, response: httpx.Response) -> None:
    key = _key(portal)
    if not key or min_interval(key) <= 0:
        return
    if response.status_code == 429:
        strikes = _strikes.get(key, 0) + 1
        _strikes[key] = strikes
        base = _retry_after(response)
        wait = base if base is not None else DEFAULT_COOLDOWN_SEC * (2 ** (strikes - 1))
        wait = min(MAX_COOLDOWN_SEC, max(30.0, wait))
        _cooldown_until[key] = time.monotonic() + wait
    elif response.status_code < 400:
        _strikes.pop(key, None)
