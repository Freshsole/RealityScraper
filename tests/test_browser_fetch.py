from pathlib import Path

from app.block_page import classify_block
from app.browser_fetch import allowed, should_try


def test_browser_fetch_disabled_on_web_role(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_FETCH", True)
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_PORTALS", frozenset({"mmreality"}))
    assert allowed("mmreality") is False


def test_browser_fetch_opt_in_on_worker(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_FETCH", True)
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_PORTALS", frozenset({"mmreality"}))
    assert allowed("mmreality") is True
    assert allowed("ulovdomov") is False
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_FETCH", False)
    assert allowed("mmreality") is False


def test_hard_cf_does_not_launch_browser_by_default(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_FETCH", True)
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_PORTALS", frozenset({"mmreality"}))
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_ON_HARD_CF", False)
    html = (Path(__file__).parent / "fixtures" / "mm_cloudflare.html").read_text()
    signal = classify_block(403, html, {"server": "cloudflare", "cf-ray": "x"})
    assert signal is not None
    assert signal.detail == "hard"
    assert should_try("mmreality", signal) is False
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_ON_HARD_CF", True)
    assert should_try("mmreality", signal) is True


def test_challenge_cf_may_use_browser(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_FETCH", True)
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_PORTALS", frozenset({"mmreality"}))
    monkeypatch.setattr("app.config.SCRAPE_BROWSER_ON_HARD_CF", False)
    signal = classify_block(
        403,
        "<html>Just a moment... Checking your browser before accessing</html>",
        {"server": "cloudflare"},
    )
    assert signal is not None
    assert signal.detail == "challenge"
    assert should_try("mmreality", signal) is True


def test_instant_site_module_stays_off_browser_path():
    src = Path("app/site_pages.py").read_text()
    assert "browser_fetch" not in src
    assert "curl_cffi" not in src
    assert "playwright" not in src
    assert "google-chrome" not in src
