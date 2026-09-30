"""Lehký in-memory rate limiter pro auth endpointy (sliding window).

Bez externí závislosti. Pro jeden proces dostačující; při více worker
procesech by bylo potřeba sdílené úložiště (Redis).
"""
from __future__ import annotations

import threading
import time
from collections import deque

_lock = threading.Lock()
# (scope, key) -> deque[timestamps]
_buckets: dict[tuple[str, str], deque[float]] = {}


def _prune(bucket: deque[float], now: float, window_s: float) -> None:
    cutoff = now - window_s
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


def check(scope: str, key: str, limit: int, window_s: float) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.monotonic()
    bucket_key = (scope, key)
    with _lock:
        bucket = _buckets.get(bucket_key)
        if bucket is None:
            bucket = deque()
            _buckets[bucket_key] = bucket
        _prune(bucket, now, window_s)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        # Ochrana proti neomezenému růstu paměti
        if len(_buckets) > 20000:
            _buckets.clear()
        return True


def reset() -> None:
    """Clear all buckets (tests)."""
    with _lock:
        _buckets.clear()
