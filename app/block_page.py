"""Detect Cloudflare / maintenance / rate-limit HTML so scrapers stop paging a dead portal."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any


CLOUDFLARE = "cloudflare"
MAINTENANCE = "maintenance"
RATE_LIMIT = "rate_limit"
FORBIDDEN = "forbidden"
SERVER_ERROR = "server_error"

# Keep cooldowns inside one NewDiscovery minute so the next tick retries.
MAX_COOLDOWN_SEC = 50.0
DEFAULT_COOLDOWN = {
    CLOUDFLARE: 45.0,
    MAINTENANCE: 45.0,
    RATE_LIMIT: 25.0,
    FORBIDDEN: 30.0,
    SERVER_ERROR: 15.0,
}

_CF_HINTS = (
    "attention required! | cloudflare",
    "sorry, you have been blocked",
    "you are unable to access",
    "cf-error-details",
    "cf-error-code",
    "cf-browser-verification",
    "just a moment...",
    "checking your browser before accessing",
    "cdn-cgi/challenge",
    "cf-challenge-running",
    "_cf_chl",
    "cloudflare ray id",
    "enable javascript and cookies to continue",
)
_CF_HARD_HINTS = (
    "sorry, you have been blocked",
    "you are unable to access",
    "the action you just performed triggered the security solution",
)
_CF_CHALLENGE_HINTS = (
    "just a moment...",
    "checking your browser before accessing",
    "cf-browser-verification",
    "cf-challenge-running",
    "cdn-cgi/challenge",
    "_cf_chl",
    "enable javascript and cookies to continue",
)
_MAINT_HINTS = (
    "údržba server",
    "udrzba server",
    "probíhá údržba",
    "probiha udrzba",
    "stránka je dočasně",
    "stranka je docasne",
    "dočasně nedostupn",
    "docasne nedostupn",
    "we are currently undergoing maintenance",
    "under maintenance",
)
_RATE_HINTS = (
    "too many requests",
    "rate limit",
    "příliš mnoho požadavků",
    "prilis mnoho pozadavku",
)
RETRY_AFTER_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


@dataclass
class BlockSignal:
    kind: str
    status_code: int
    retry_after: float | None = None
    detail: str = ""


class PortalBlocked(Exception):
    """This portal page is a block / maintenance / rate-limit body, not a listing list."""

    def __init__(
        self,
        kind: str,
        status_code: int = 0,
        *,
        portal: str = "",
        retry_after: float | None = None,
        detail: str = "",
    ) -> None:
        self.kind = kind
        self.status_code = int(status_code or 0)
        self.portal = portal or ""
        self.retry_after = retry_after
        self.detail = detail or kind
        super().__init__(f"blocked:{self.kind}:{self.status_code}:{self.portal}".rstrip(":"))


def _header(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if callable(getter):
        try:
            return str(getter(name) or getter(name.lower()) or "")
        except Exception:
            return ""
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def parse_retry_after(headers: Any) -> float | None:
    raw = _header(headers, "Retry-After").strip()
    if not raw:
        return None
    match = RETRY_AFTER_RE.match(raw)
    if not match:
        return None
    try:
        return max(0.0, float(match.group(1)))
    except ValueError:
        return None


def classify_block(
    status_code: int,
    html: str = "",
    headers: Any = None,
) -> BlockSignal | None:
    """Return a block signal when the response is not a usable listing page."""
    status = int(status_code or 0)
    folded = (html or "").casefold()
    server = _header(headers, "Server").casefold()
    cf_ray = _header(headers, "cf-ray")
    retry_after = parse_retry_after(headers)

    if status == 429 or any(hint in folded for hint in _RATE_HINTS):
        return BlockSignal(RATE_LIMIT, status or 429, retry_after=retry_after)
    if any(hint in folded for hint in _MAINT_HINTS):
        return BlockSignal(MAINTENANCE, status or 503, retry_after=retry_after)
    cf_html = any(hint in folded for hint in _CF_HINTS)
    cf_detail = ""
    if any(hint in folded for hint in _CF_HARD_HINTS):
        cf_detail = "hard"
    elif any(hint in folded for hint in _CF_CHALLENGE_HINTS):
        cf_detail = "challenge"
    if status == 403 and (cf_html or cf_ray or "cloudflare" in server):
        return BlockSignal(CLOUDFLARE, 403, retry_after=retry_after, detail=cf_detail or "cloudflare")
    if cf_html or (cf_ray and status in {0, 403, 503} and "cloudflare" in folded):
        return BlockSignal(CLOUDFLARE, status or 403, retry_after=retry_after, detail=cf_detail or "cloudflare")
    if status == 403:
        return BlockSignal(FORBIDDEN, 403, retry_after=retry_after)
    if status in {503, 502, 500} and not folded.strip():
        return BlockSignal(SERVER_ERROR, status, retry_after=retry_after)
    if status == 503:
        return BlockSignal(SERVER_ERROR, 503, retry_after=retry_after)
    return None


class PortalCooldown:
    """Per-portal skip window. Does not sleep and does not hold the global limiter."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._reason: dict[str, str] = {}

    def note(
        self,
        portal: str,
        kind: str,
        *,
        retry_after: float | None = None,
        now: float | None = None,
    ) -> float:
        key = (portal or "").strip().lower()
        if not key:
            return 0.0
        delay = DEFAULT_COOLDOWN.get(kind, 20.0)
        if retry_after is not None:
            delay = max(delay if kind != RATE_LIMIT else 0.0, float(retry_after))
            if kind == RATE_LIMIT:
                delay = min(MAX_COOLDOWN_SEC, max(3.0, float(retry_after) or delay))
        delay = min(MAX_COOLDOWN_SEC, max(1.0, delay))
        stamp = time.monotonic() if now is None else now
        until = stamp + delay
        prev = self._until.get(key, 0.0)
        if until > prev:
            self._until[key] = until
            self._reason[key] = kind
        return delay

    def note_block(self, blocked: PortalBlocked, *, now: float | None = None) -> float:
        return self.note(blocked.portal, blocked.kind, retry_after=blocked.retry_after, now=now)

    def remaining(self, portal: str, *, now: float | None = None) -> float:
        key = (portal or "").strip().lower()
        if not key:
            return 0.0
        stamp = time.monotonic() if now is None else now
        left = self._until.get(key, 0.0) - stamp
        return left if left > 0 else 0.0

    def reason(self, portal: str) -> str:
        return self._reason.get((portal or "").strip().lower(), "")

    def active(self, portal: str, *, now: float | None = None) -> bool:
        return self.remaining(portal, now=now) > 0
