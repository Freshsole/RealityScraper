"""Optional worker-only HTML fetch for Cloudflare-gated list pages.

Never imported by InstantSiteASGI. Off unless SCRAPE_BROWSER_FETCH=1, and always
disabled when SCRAPE_ROLE=web so /hry* TTFB cannot pay for Chrome or curl_cffi.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from typing import Any

from app import config
from app.block_page import classify_block
from app.html_listing import BROWSER_HEADERS


@dataclass
class BrowserFetchResult:
    status_code: int
    text: str
    headers: dict[str, str]
    backend: str


def allowed(portal: str) -> bool:
    return config.browser_fetch_allowed(portal)


def should_try(portal: str, signal: Any | None) -> bool:
    if not allowed(portal) or signal is None:
        return False
    kind = getattr(signal, "kind", "") or ""
    if kind != "cloudflare":
        return False
    detail = getattr(signal, "detail", "") or ""
    if detail == "hard" and not config.SCRAPE_BROWSER_ON_HARD_CF:
        # Datacenter IPs get a WAF hard-block; launching Chrome just burns the tick.
        return False
    return True


def _timeout() -> float:
    return float(config.SCRAPE_BROWSER_TIMEOUT_SEC)


async def fetch_html(url: str, *, timeout: float | None = None, headers: dict[str, str] | None = None) -> BrowserFetchResult:
    """Fetch listing HTML via JA3 impersonation or a hard-timeout browser.

    Backends are optional and tried in order: curl_cffi → Playwright → system Chrome.
    """
    budget = _timeout() if timeout is None else max(1.0, float(timeout))
    hdrs = dict(BROWSER_HEADERS)
    if headers:
        hdrs.update(headers)
    last_error = "no-backend"
    for factory in (_curl_cffi_fetch, _playwright_fetch, _chrome_dump_fetch):
        try:
            result = await asyncio.wait_for(factory(url, budget, hdrs), timeout=budget + 0.5)
        except Exception as exc:
            last_error = f"{factory.__name__}:{type(exc).__name__}"
            continue
        if result is None:
            continue
        return result
    return BrowserFetchResult(status_code=0, text="", headers={"x-browser-fetch": last_error}, backend="skipped")


def looks_blocked(result: BrowserFetchResult) -> bool:
    return classify_block(result.status_code, result.text, result.headers) is not None


async def _curl_cffi_fetch(url: str, timeout: float, headers: dict[str, str]) -> BrowserFetchResult | None:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None
    impersonate = os.getenv("SCRAPE_BROWSER_IMPERSONATE", "chrome").strip() or "chrome"
    async with AsyncSession(impersonate=impersonate, timeout=timeout) as session:
        response = await session.get(url, headers=headers, allow_redirects=True)
    hdrs = {str(key).lower(): str(value) for key, value in (response.headers or {}).items()}
    return BrowserFetchResult(int(response.status_code or 0), response.text or "", hdrs, "curl_cffi")


async def _playwright_fetch(url: str, timeout: float, headers: dict[str, str]) -> BrowserFetchResult | None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None
    ms = int(timeout * 1000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )
        try:
            page = await browser.new_page(extra_http_headers=headers)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=ms)
            text = await page.content()
            status = int(response.status if response is not None else 0)
            hdrs = {}
            if response is not None:
                try:
                    hdrs = {str(key).lower(): str(value) for key, value in response.headers.items()}
                except Exception:
                    hdrs = {}
            return BrowserFetchResult(status, text, hdrs, "playwright")
        finally:
            await browser.close()


def _chrome_bin() -> str:
    explicit = os.getenv("CHROME_PATH", "").strip()
    if explicit:
        return explicit
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return ""


async def _chrome_dump_fetch(url: str, timeout: float, headers: dict[str, str]) -> BrowserFetchResult | None:
    chrome = _chrome_bin()
    if not chrome:
        return None
    ms = max(1000, int(timeout * 1000))
    proc = await asyncio.create_subprocess_exec(
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        f"--virtual-time-budget={ms}",
        f"--timeout={ms}",
        f"--user-agent={headers.get('User-Agent') or BROWSER_HEADERS['User-Agent']}",
        "--dump-dom",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    text = (out or b"").decode("utf-8", "replace")
    status = 403 if classify_block(403 if "attention required" in text.casefold() else 200, text, {}) else 200
    if "attention required" in text.casefold() or "sorry, you have been blocked" in text.casefold():
        status = 403
    return BrowserFetchResult(status, text, {"server": "chrome-dump"}, "chrome")
