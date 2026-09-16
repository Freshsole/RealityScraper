from pathlib import Path

from app.block_page import (
    CLOUDFLARE,
    MAINTENANCE,
    RATE_LIMIT,
    PortalBlocked,
    PortalCooldown,
    classify_block,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_classify_cloudflare_fixture():
    html = (FIXTURES / "mm_cloudflare.html").read_text()
    signal = classify_block(403, html, {"server": "cloudflare", "cf-ray": "abc"})
    assert signal is not None
    assert signal.kind == CLOUDFLARE
    assert signal.status_code == 403
    assert signal.detail == "hard"


def test_classify_maintenance_fixture():
    html = (FIXTURES / "realitycz_maintenance.html").read_text()
    signal = classify_block(200, html)
    assert signal is not None
    assert signal.kind == MAINTENANCE


def test_classify_challenge_html():
    html = "<html><title>Just a moment...</title><p>Checking your browser before accessing</p></html>"
    signal = classify_block(403, html, {"server": "cloudflare"})
    assert signal is not None
    assert signal.kind == CLOUDFLARE
    assert signal.detail == "challenge"


def test_classify_healthy_listing_html_is_not_blocked():
    html = (FIXTURES / "ceskereality_cards.html").read_text()
    assert classify_block(200, html) is None
    vypis = (FIXTURES / "realitycz_vypis.html").read_text()
    assert classify_block(200, vypis) is None


def test_classify_429_uses_retry_after():
    signal = classify_block(429, "Too Many Requests", {"Retry-After": "8"})
    assert signal is not None
    assert signal.kind == RATE_LIMIT
    assert signal.retry_after == 8.0


def test_portal_cooldown_is_per_portal():
    cool = PortalCooldown()
    now = 1000.0
    cool.note("mmreality", CLOUDFLARE, now=now)
    cool.note("ceskereality", RATE_LIMIT, retry_after=3, now=now)
    assert cool.active("mmreality", now=now + 1)
    assert not cool.active("sreality", now=now + 1)
    assert cool.active("ceskereality", now=now + 1)
    assert not cool.active("ceskereality", now=now + 40)
    assert not cool.active("mmreality", now=now + 60)


def test_portal_blocked_message():
    exc = PortalBlocked(CLOUDFLARE, 403, portal="mmreality")
    assert "blocked:cloudflare:403:mmreality" in str(exc)
