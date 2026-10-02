#!/usr/bin/env python3
"""Fetch key public pages with AI crawler User-Agents; report status, H1, numbers, and latency."""

from __future__ import annotations

import re
import sys
import time
from typing import Any

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://realitify.cz").rstrip("/")
MAX_MS = int(sys.argv[2]) if len(sys.argv) > 2 else 500

USER_AGENTS = [
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "Claude-User",
    "Claude-SearchBot",
    "PerplexityBot",
    "Google-Extended",
    "Googlebot",
    "Bingbot",
    "SeznamBot",
]

# Paths that must answer quickly from SSR cache (≤ MAX_MS).
FAST_PATHS = ["/", "/trh", "/trh/praha/pronajem", "/faq", "/index"]
# Also checked, but not subject to the 500 ms gate until deploy warms cache.
OTHER_PATHS = ["/llms.txt", "/llms-full.txt"]

H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
NUMBER_RE = re.compile(r"\d[\d\s]{2,}")
CHALLENGE_RE = re.compile(r"captcha|cf-challenge|attention required|just a moment", re.I)


def check(path: str, ua: str, *, require_fast: bool) -> dict[str, Any]:
    url = f"{BASE}{path}"
    headers = {"User-Agent": f"{ua}/1.0 (+https://realitify.cz/crawler-check)"}
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            resp = client.get(url, headers=headers)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
    except httpx.TimeoutException:
        return {
            "path": path,
            "ua": ua,
            "status": "timeout",
            "ok": False,
            "detail": "timeout after 15s",
            "ms": int((time.perf_counter() - started) * 1000),
            "h1": "",
            "has_h1": None,
            "has_numbers": False,
            "location": "",
            "challenge": False,
            "redirected_login": False,
            "slow": True,
        }
    except httpx.HTTPError as exc:
        return {
            "path": path,
            "ua": ua,
            "status": "error",
            "ok": False,
            "detail": str(exc),
            "ms": int((time.perf_counter() - started) * 1000),
            "h1": "",
            "has_h1": None,
            "has_numbers": False,
            "location": "",
            "challenge": False,
            "redirected_login": False,
            "slow": True,
        }

    status = resp.status_code
    location = resp.headers.get("location") or ""
    text = resp.text or ""
    redirected_login = status in {301, 302, 303, 307, 308} and any(
        x in location for x in ("/prihlaseni", "/registrace", "/login")
    )
    challenge = bool(CHALLENGE_RE.search(text))
    is_html = "text/html" in (resp.headers.get("content-type") or "")
    h1 = ""
    if is_html:
        m = H1_RE.search(text)
        h1 = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
    has_numbers = bool(NUMBER_RE.search(text))
    bad = status in {403, 429} or challenge or redirected_login or status >= 400
    if status in {301, 302, 303, 307, 308} and not redirected_login:
        bad = False
    slow = require_fast and elapsed_ms > MAX_MS and status == 200
    if slow:
        bad = True
    return {
        "path": path,
        "ua": ua,
        "status": status,
        "ok": not bad,
        "ms": elapsed_ms,
        "h1": h1[:80],
        "has_h1": bool(h1) if is_html else None,
        "has_numbers": has_numbers,
        "location": location[:120],
        "challenge": challenge,
        "redirected_login": redirected_login,
        "slow": slow,
        "data_as_of": resp.headers.get("x-data-as-of") or "",
    }


def main() -> int:
    rows: list[dict[str, Any]] = []
    for path in FAST_PATHS:
        for ua in USER_AGENTS:
            row = check(path, ua, require_fast=True)
            rows.append(row)
            mark = "OK" if row["ok"] else "FAIL"
            extra = f" h1={row['h1']!r}" if row.get("h1") else ""
            as_of = f" as_of={row['data_as_of']}" if row.get("data_as_of") else ""
            detail = f" {row.get('detail')}" if row.get("detail") else ""
            print(f"{mark}\t{row['status']}\t{row['ms']}ms\t{ua}\t{path}{extra}{as_of}{detail}")
    for path in OTHER_PATHS:
        for ua in USER_AGENTS:
            row = check(path, ua, require_fast=False)
            rows.append(row)
            mark = "OK" if row["ok"] else "FAIL"
            print(f"{mark}\t{row['status']}\t{row['ms']}ms\t{ua}\t{path}")
    failed = [r for r in rows if not r["ok"]]
    fast_rows = [r for r in rows if r["path"] in FAST_PATHS and r.get("status") == 200]
    if fast_rows:
        p50 = sorted(r["ms"] for r in fast_rows)[len(fast_rows) // 2]
        p95 = sorted(r["ms"] for r in fast_rows)[int(len(fast_rows) * 0.95)]
        print(f"\nFast paths 200 latency: n={len(fast_rows)} p50={p50}ms p95={p95}ms gate={MAX_MS}ms")
    print(f"Checked {len(rows)} requests; failures: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
