"""Portal-level circuit breaker for chronically dead sites.

Distinct from AdaptiveLimiter (temporary 429/403 rate limits). After N consecutive
403/5xx/timeouts the portal is skipped until an exponential cooldown elapses.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app import config

HARD_FAIL_CODES = {403, 500, 502, 503, 504}
# Timeouts that hit many portals in the same instant are an event-loop stall,
# not evidence that each portal is dead. 403/500 still trip the breaker.
_STALL_WINDOW_SEC = 2.0
_STALL_PORTAL_COUNT = 3


@dataclass
class PortalState:
    consecutive: int = 0
    cooldown_sec: int = 0
    disabled_until: float = 0.0
    reason: str = ""
    disable_count: int = 0
    last_ok_at: float = 0.0
    last_fail_at: float = 0.0
    logged_until: float = 0.0
    skips: int = 0


_lock = threading.Lock()
_state: dict[str, PortalState] = {}
_recent_timeouts: deque[tuple[float, str]] = deque(maxlen=80)


def reset() -> None:
    with _lock:
        _state.clear()
        _recent_timeouts.clear()


def _key(portal: str) -> str:
    return (portal or "").strip().lower() or "unknown"


def _state_for(portal: str) -> PortalState:
    key = _key(portal)
    item = _state.get(key)
    if item is None:
        item = PortalState(cooldown_sec=int(config.SCRAPE_PORTAL_COOLDOWN_SEC))
        _state[key] = item
    return item


def is_timeout(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return True
    text = str(exc).lower()
    return "timeout" in text or "timed out" in text


def is_hard_fail(status_code: int | None, exc: BaseException | None = None) -> bool:
    if status_code in HARD_FAIL_CODES:
        return True
    return is_timeout(exc)


def is_disabled(portal: str) -> bool:
    with _lock:
        item = _state.get(_key(portal))
        if item is None or item.disabled_until <= 0:
            return False
        if time.time() >= item.disabled_until:
            item.disabled_until = 0.0
            item.reason = ""
            return False
        item.skips += 1
        return True


def _loop_stall(now: float, portal: str, reason: str) -> bool:
    """True when several portals time out together — the loop was frozen."""
    if (reason or "").strip().lower() != "timeout":
        return False
    key = _key(portal)
    _recent_timeouts.append((now, key))
    cutoff = now - _STALL_WINDOW_SEC
    while _recent_timeouts and _recent_timeouts[0][0] < cutoff:
        _recent_timeouts.popleft()
    portals = {name for _, name in _recent_timeouts}
    return len(portals) >= _STALL_PORTAL_COUNT


def record_ok(portal: str) -> None:
    if not portal:
        return
    now = time.time()
    with _lock:
        item = _state_for(portal)
        # In-flight 200s after a trip must not silently re-enable a dead portal.
        if item.disabled_until > now:
            return
        item.consecutive = 0
        item.cooldown_sec = int(config.SCRAPE_PORTAL_COOLDOWN_SEC)
        item.disabled_until = 0.0
        item.reason = ""
        item.last_ok_at = now


def record_fail(portal: str, reason: str) -> bool:
    """Return True if this failure just tripped the breaker."""
    if not portal:
        return False
    now = time.time()
    with _lock:
        item = _state_for(portal)
        if item.disabled_until > now:
            return False
        if _loop_stall(now, portal, reason):
            for st in _state.values():
                if st.reason == "timeout" and st.disabled_until <= now:
                    st.consecutive = 0
            return False
        item.consecutive += 1
        item.last_fail_at = now
        item.reason = reason[:180]
        threshold = int(config.SCRAPE_PORTAL_FAILS_TO_DISABLE)
        # A probe after cooldown failed again — disable immediately.
        probe_fail = item.disable_count > 0 and item.consecutive == 1
        if item.consecutive < threshold and not probe_fail:
            return False
        item.disabled_until = now + item.cooldown_sec
        item.disable_count += 1
        item.consecutive = 0
        retry_at = datetime.fromtimestamp(item.disabled_until, timezone.utc).isoformat(timespec="seconds")
        if item.logged_until < item.disabled_until:
            print(
                f"portal {_key(portal)} disabled: {item.reason}; retry at {retry_at} "
                f"(cooldown={item.cooldown_sec}s)",
                flush=True,
            )
            item.logged_until = item.disabled_until
        item.cooldown_sec = min(
            int(config.SCRAPE_PORTAL_COOLDOWN_CAP_SEC),
            max(int(config.SCRAPE_PORTAL_COOLDOWN_SEC), item.cooldown_sec * 2),
        )
        return True


def snapshot() -> dict[str, Any]:
    now = time.time()
    with _lock:
        out = {}
        for name, item in sorted(_state.items()):
            disabled = item.disabled_until > now
            out[name] = {
                "consecutive": item.consecutive,
                "cooldown_sec": item.cooldown_sec,
                "disabled": disabled,
                "disabled_until": (
                    datetime.fromtimestamp(item.disabled_until, timezone.utc).isoformat(timespec="seconds")
                    if item.disabled_until
                    else None
                ),
                "reason": item.reason,
                "disable_count": item.disable_count,
                "skips": item.skips,
            }
        return out
