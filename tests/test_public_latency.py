import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.games import reset_pool_cache, wait_refresh
from app.site_pages import (
    InstantSiteASGI,
    INSTANT_ROUTES,
    app_shell_redirect,
    app_shell_redirect_for_cookies,
    instant_asset_rel,
    instant_page_name,
    instant_root_file,
    site_asset,
    site_body,
    web_body,
    web_page,
)
from app.store import (
    LISTINGS_FTS_MATCH_SQL,
    Store,
    catalog_item_needs_live_fetch,
    listings_fts_match_query,
    _PIN_COVER_INDEX_COLS,
    _pin_gps_grid_sql,
    _pin_gps_tight_sql,
)


def test_catalog_item_skips_live_fetch_when_gallery_is_rich():
    assert catalog_item_needs_live_fetch(None) is False
    assert catalog_item_needs_live_fetch({"gone": 1, "url": "https://x", "photos": []}) is False
    assert catalog_item_needs_live_fetch({"url": "https://x", "photos": ["a", "b"]}) is False
    assert (
        catalog_item_needs_live_fetch(
            {
                "url": "https://x",
                "photos": ["a"],
                "description": "Světlý byt",
                "lat": 50.08,
                "extras": {"address": "Vinohrady"},
            }
        )
        is False
    )
    assert catalog_item_needs_live_fetch({"url": "https://x", "photos": ["a"]}) is True
    assert catalog_item_needs_live_fetch({"photos": []}) is False


def test_gone_fast_stays_bounded_on_fat_listings(tmp_path: Path):
    store = Store(tmp_path / "gone-fast.sqlite")
    blob = json.dumps({"offer": "Pronájem", "blob": "x" * 4000})
    now = datetime.now(timezone.utc)
    n = 2500
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO listings(
                id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, last_seen, gone, extras, notified
            ) VALUES (?, 'default', 'Pronájem bytu 2+kk', 18000, '18000 Kč/měsíc', '2+kk', 50,
                      ?, ?, ?, ?, ?, 1, ?, 1)
            """,
            [
                (
                    i,
                    f"Praha {(i % 12) + 1} – Čtvrť {i % 12}",
                    f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{i}",
                    f"https://img.example/{i}.jpg",
                    (now - timedelta(hours=3)).isoformat(),
                    (now - timedelta(hours=1, minutes=i % 40)).isoformat(),
                    blob,
                )
                for i in range(n)
            ],
        )
        conn.commit()

    t0 = time.perf_counter()
    items = store.public_gone_fast_rentals(days=3, limit=4)
    first_ms = (time.perf_counter() - t0) * 1000
    assert first_ms < 180, f"gone-fast {first_ms:.1f}ms on {n} listings"
    assert 1 <= len(items) <= 4
    t0 = time.perf_counter()
    again = store.public_gone_fast_rentals(days=3, limit=4)
    cached_ms = (time.perf_counter() - t0) * 1000
    assert cached_ms < 8, f"gone-fast cache {cached_ms:.1f}ms"
    assert again == items


def test_catalog_new_today_skips_full_table_count(tmp_path: Path):
    store = Store(tmp_path / "today.sqlite")
    blob = json.dumps({"blob": "x" * 2000})
    now = datetime.now(timezone.utc).isoformat()
    n = 4000
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO catalog_listings(
                listing_key, id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, extras, last_seen, gone, portal
            ) VALUES (?, ?, 'Byt', 20000, '20000 Kč', '2+kk', 50, 'Praha', ?, '', ?, ?, ?, 0, 'sreality')
            """,
            [
                (
                    f"sale-{i}",
                    i,
                    f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{i}",
                    now if i < 12 else "2020-01-01T00:00:00+00:00",
                    blob,
                    now,
                )
                for i in range(n)
            ],
        )
        conn.commit()
    t0 = time.perf_counter()
    count = store.catalog_new_today_count()
    ms = (time.perf_counter() - t0) * 1000
    assert count == 12
    assert ms < 150, f"new-today count {ms:.1f}ms on {n} rows"
    t0 = time.perf_counter()
    assert store.catalog_new_today_count() == 12
    assert (time.perf_counter() - t0) * 1000 < 8


async def _asgi_get(
    app,
    path: str,
    method: str = "GET",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[bytes, bytes], bytes]:
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query_string,
            "headers": list(headers or []),
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 80),
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body") or b"" for item in sent if item["type"] == "http.response.body")
    headers = {key: value for key, value in start.get("headers") or []}
    return int(start["status"]), headers, body


def test_hry_html_bypasses_blocked_inner_app():
    hit = {"n": 0}

    async def inner(scope, receive, send):
        if scope.get("type") != "http":
            return
        hit["n"] += 1
        await asyncio.sleep(8)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner)
        for path, needle in (
            ("/hry", b"HIGHER / LOWER"),
            ("/hry/vyssi-nizsi", "KTERÝ BYT JE LEVNĚJŠÍ".encode()),
            ("/hry/najem", "KOLIK STOJÍ MĚSÍC".encode()),
            ("/", "NEJLEPŠÍ BYTY ZMIZÍ".encode()),
        ):
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert needle in body
            assert headers[b"cache-control"].startswith(b"public")
            assert ms < 40, f"{path} {ms:.1f}ms while inner would block"
        status, headers, body = await _asgi_get(app, "/hry/vyssi-nizsi", method="HEAD")
        assert status == 200
        assert body == b""
        assert int(headers[b"content-length"]) == len(site_body("hry-vyssi-nizsi.html"))
        assert hit["n"] == 0

        for path in ("/hry/vyssi-nizsi/", "/hry/najem/"):
            status, _headers, body = await _asgi_get(app, path)
            assert status == 200, path
            assert body

        css = site_asset("site/games.css")[0]
        site_css = site_asset("site/site.css")[0]
        site_js = site_asset("site/site.js")[0]
        for path, needle in (
            ("/static/site/games.css", b"@font-face"),
            ("/static/site/site.css", b"@font-face"),
            ("/static/site/site.js", b"sold-cards"),
            ("/static/t.js", b"/api/t"),
            ("/static/site/games.js", b"[^\\d]"),
            ("/static/site/fonts/archivo-black-latin.woff2", b"wOF2"),
            ("/static/site/assets/hero-apart.webp", b"WEBP"),
        ):
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert needle in body
            assert headers[b"cache-control"].startswith(b"public")
            assert headers[b"access-control-allow-origin"] == b"*"
            assert ms < 40, f"{path} {ms:.1f}ms while inner would block"
        assert css == site_asset("site/games.css")[0]
        assert site_css == site_asset("site/site.css")[0]
        assert site_js == site_asset("site/site.js")[0]
        assert b"/api/t" in site_asset("t.js")[0]

        for path in ("/api/public/games/higher-lower", "/api/public/games/rent-round"):
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            payload = json.loads(body)
            assert headers[b"content-type"].startswith(b"application/json")
            assert headers[b"cache-control"] == b"no-store"
            assert ms < 40, f"{path} {ms:.1f}ms while inner would block"
            if path.endswith("higher-lower"):
                assert payload["left"]["locality_key"] == payload["right"]["locality_key"]
                assert payload["cheaper"] in {"left", "right"}
            else:
                assert len(payload["items"]) == 5
                assert len({item.get("pair_key") for item in payload["items"]}) == 1
                assert payload.get("pair_key")
                assert "v řádu minut" not in (payload.get("vanish_label") or "")
        assert hit["n"] == 0

    asyncio.run(run())


def test_marketing_auth_html_bypasses_blocked_inner_app():
    hit = {"n": 0}

    async def inner(scope, receive, send):
        if scope.get("type") != "http":
            return
        hit["n"] += 1
        await asyncio.sleep(8)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner)
        for path, needle in (
            ("/prihlaseni", "PŘIHLÁŠENÍ".encode()),
            ("/registrace", "VYTVOŘTE SI ÚČET".encode()),
            ("/heslo", "OBNOVTE SI HESLO".encode()),
            ("/kontakt", "OZVĚTE SE NÁM".encode()),
            ("/uspechy", "NAŠLI SI VYSNĚNÉ BYDLENÍ".encode()),
            ("/uspechy/martina-tomas", b"article-page"),
            ("/obchodni-podminky", "OBCHODNÍ PODMÍNKY".encode()),
            ("/ochrana-soukromi", "OCHRANA SOUKROMÍ".encode()),
            ("/nastaveni-cookies", "NASTAVENÍ COOKIES".encode()),
            ("/byt", "Začít hlídat zdarma".encode()),
            ("/dum", "Hlídání domů v ČR".encode()),
        ):
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert needle in body, path
            assert headers[b"cache-control"].startswith(b"public")
            assert ms < 40, f"{path} {ms:.1f}ms while inner would block"
        status, headers, body = await _asgi_get(
            app, "/prihlaseni", query_string=b"next=%2Fprehled"
        )
        assert status == 200
        assert "PŘIHLÁŠENÍ".encode() in body
        assert b"fonts.googleapis" not in body
        assert int(headers[b"content-length"]) == len(site_body("prihlaseni.html"))
        status, _headers, body = await _asgi_get(app, "/registrace/")
        assert status == 200
        assert "VYTVOŘTE SI ÚČET".encode() in body
        t0 = time.perf_counter()
        status, headers, body = await _asgi_get(app, "/static/site/auth.css")
        ms = (time.perf_counter() - t0) * 1000
        assert status == 200
        assert b"text-transform: uppercase" in body
        assert b"fonts.googleapis" not in body
        assert headers[b"access-control-allow-origin"] == b"*"
        assert ms < 40, f"auth.css {ms:.1f}ms while inner would block"
        status, _headers, body = await _asgi_get(app, "/static/site/auth.js")
        assert status == 200
        assert b"/api/auth/login" in body
        leftover_assets = (
            ("/static/site/inquiries.js", b"contact-form"),
            ("/static/site/legal.js", b"legal-toc"),
            ("/static/site/cookies.js", b"rf_consent"),
            ("/static/site/stories-list.js", b"stories-grid"),
            ("/static/site/stories-articles.js", b"article-body"),
            ("/static/site/landing-search.js", b"guest-search"),
            ("/static/site/assets/room/dum.webp", b"WEBP"),
            ("/sw.js", b"push"),
            ("/manifest.webmanifest", b"standalone"),
        )
        for path, needle in leftover_assets:
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert needle in body, path
            assert ms < 40, f"{path} {ms:.1f}ms while inner would block"
            if path == "/sw.js":
                assert headers[b"cache-control"] == b"no-store, max-age=0"
                assert headers[b"service-worker-allowed"] == b"/"
            elif path == "/manifest.webmanifest":
                assert headers[b"cache-control"] == b"no-store, max-age=0"
            else:
                assert headers[b"cache-control"].startswith(b"public")
        assert hit["n"] == 0

    asyncio.run(run())


def test_auth_apis_and_app_pages_still_hit_inner_app():
    hit: list[tuple[str, str]] = []

    async def inner(scope, receive, send):
        if scope.get("type") != "http":
            return
        hit.append((str(scope.get("method")), str(scope.get("path"))))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run() -> None:
        app = InstantSiteASGI(inner)
        session = [(b"cookie", b"realitify_session=test-token")]
        status, _headers, body = await _asgi_get(app, "/prihlaseni")
        assert status == 200
        assert body
        status, _headers, body = await _asgi_get(app, "/registrace")
        assert status == 200
        status, headers, body = await _asgi_get(app, "/prehled")
        assert status == 303
        assert headers[b"location"] == b"/prihlaseni"
        assert body == b""
        status, _headers, body = await _asgi_get(app, "/prehled", headers=session)
        assert status == 200
        assert b'id="view-overview"' in body
        for method, path in (
            ("POST", "/api/auth/login"),
            ("POST", "/api/auth/register"),
            ("POST", "/api/auth/logout"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/inquiries"),
            ("GET", "/api/catalog"),
            ("GET", "/api/listings"),
            ("GET", "/api/settings"),
            ("GET", "/api/admin/me"),
            ("POST", "/prihlaseni"),
            ("POST", "/registrace"),
        ):
            if method == "GET":
                status, _headers, _body = await _asgi_get(app, path, headers=session)
            else:
                status, _headers, _body = await _asgi_post(app, path, {"email": "a@b.cz"})
            assert status == 204, path
        assert ("GET", "/prihlaseni") not in hit
        assert ("GET", "/registrace") not in hit
        assert ("GET", "/prehled") not in hit
        assert hit == [
            ("POST", "/api/auth/login"),
            ("POST", "/api/auth/register"),
            ("POST", "/api/auth/logout"),
            ("GET", "/api/auth/me"),
            ("POST", "/api/inquiries"),
            ("GET", "/api/catalog"),
            ("GET", "/api/listings"),
            ("GET", "/api/settings"),
            ("GET", "/api/admin/me"),
            ("POST", "/prihlaseni"),
            ("POST", "/registrace"),
        ]

    asyncio.run(run())


def test_dashboard_html_bypasses_blocked_inner_app():
    hit = {"n": 0}

    async def inner(scope, receive, send):
        if scope.get("type") != "http":
            return
        hit["n"] += 1
        await asyncio.sleep(8)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner)
        session = [(b"cookie", b"realitify_session=test-token")]
        guest = [(b"cookie", b"rf_guest_search=guest-token")]
        shell = web_body("index.html")
        admin = web_body("admin/index.html")
        for path in (
            "/prehled",
            "/nabidka",
            "/monitory",
            "/filtry",
            "/zprava",
            "/nastaveni",
            "/nastaveni/profily",
            "/nastaveni/predplatne",
        ):
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path, headers=session)
            ms = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert body == shell
            assert headers[b"cache-control"] == b"no-store, max-age=0"
            assert b'id="view-overview"' in body
            assert ms < 40, f"{path} {ms:.1f}ms while inner would block"
        status, headers, body = await _asgi_get(app, "/prehled/", headers=session, method="HEAD")
        assert status == 200
        assert body == b""
        assert int(headers[b"content-length"]) == len(shell)
        t0 = time.perf_counter()
        status, headers, body = await _asgi_get(app, "/prehled")
        ms = (time.perf_counter() - t0) * 1000
        assert status == 303
        assert headers[b"location"] == b"/prihlaseni"
        assert body == b""
        assert ms < 40, f"unauth /prehled {ms:.1f}ms while inner would block"
        status, headers, body = await _asgi_get(app, "/nabidka")
        assert status == 303
        assert headers[b"location"].startswith(b"/registrace?next=")
        status, headers, body = await _asgi_get(
            app, "/nabidka", query_string=b"listing_key=abc", headers=guest
        )
        assert status == 200
        assert body == shell
        t0 = time.perf_counter()
        status, headers, body = await _asgi_get(app, "/admin/prehled")
        ms = (time.perf_counter() - t0) * 1000
        assert status == 200
        assert body == admin
        assert "ADMIN PŘIHLÁŠENÍ".encode() in body
        assert headers[b"cache-control"] == b"no-store, max-age=0"
        assert ms < 40, f"/admin/prehled {ms:.1f}ms while inner would block"
        assert b"fonts.googleapis" not in shell
        assert b"fonts.gstatic" not in shell
        assert b"archivo-black-latin.woff2" in shell
        assert b"fonts.googleapis" not in admin
        assert b"archivo-black-latin.woff2" in admin
        t0 = time.perf_counter()
        status, headers, body = await _asgi_get(app, "/static/styles.css")
        ms = (time.perf_counter() - t0) * 1000
        assert status == 200
        assert b".nav" in body or b"nav" in body
        assert headers[b"access-control-allow-origin"] == b"*"
        assert ms < 40, f"styles.css {ms:.1f}ms while inner would block"
        assert hit["n"] == 0

    asyncio.run(run())


def test_store_init_does_not_wait_on_writer_lock(tmp_path: Path):
    path = tmp_path / "reload.sqlite"
    Store(path)
    locker = sqlite3.connect(path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN IMMEDIATE")
    locker.execute("UPDATE meta SET value = value")
    t0 = time.perf_counter()
    Store(path)
    ms = (time.perf_counter() - t0) * 1000
    locker.rollback()
    locker.close()
    assert ms < 1500, f"reload Store() waited {ms:.1f}ms on a live writer"


def _latency_listing(i: int):
    from app.sreality import Listing

    return Listing(
        id=20_000 + i,
        name=f"Byt {i} 2+kk Praha",
        price_czk=18000 + i,
        price_label=f"{18000 + i} Kč/měsíc",
        disposition="2+kk",
        area_m2=50,
        locality=f"Praha {(i % 8) + 1}",
        url=f"https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/{20_000 + i}",
        image_url=f"https://img.example/{i}.jpg",
        lat=50.08 + (i % 20) * 0.001,
        lon=14.42 + (i % 20) * 0.001,
        extras={"offer": "Pronájem", "estate": "Byt", "portal": "sreality"},
    )


def _hry_ttfb_listing(i: int):
    """Mixed-size flats so warm JSON still scores teaching contrast."""
    from app.sreality import Listing

    disposition = ("1+kk", "2+kk", "3+kk", "4+kk")[i % 4]
    area = 28 + (i % 7) * 8
    price = 11_500 + (i % 13) * 1700 + (i % 5) * 250
    return Listing(
        id=30_000 + i,
        name=f"Pronájem bytu {disposition}",
        price_czk=price,
        price_label=f"{price} Kč/měsíc",
        disposition=disposition,
        area_m2=area,
        locality=f"Praha {(i % 8) + 1}",
        url=f"https://www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{30_000 + i}",
        image_url=f"https://img.example/hry-{i}.jpg",
        lat=50.08 + (i % 20) * 0.001,
        lon=14.42 + (i % 20) * 0.001,
        extras={"offer": "Pronájem", "estate": "Byt", "portal": "sreality"},
    )


def test_readonly_connect_is_wal_query_only_not_mode_ro(tmp_path: Path, monkeypatch):
    seen: list[tuple[tuple, dict]] = []
    real = sqlite3.connect

    def wrapped(*args, **kwargs):
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", wrapped)
    store = Store(tmp_path / "reader.sqlite")
    seen.clear()
    conn = store.connect(readonly=True)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 80
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE meta SET value = value")
    finally:
        conn.close()
    assert not any(kwargs.get("uri") for _args, kwargs in seen)
    assert not any("mode=ro" in str(arg) for args, _kwargs in seen for arg in args)
    mode = store.connect().execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_writer_timeout_splits_request_path_from_scrape_worker(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "split.sqlite")
    result: dict[str, int] = {}

    def probe(label: str) -> None:
        conn = store.connect()
        result[label] = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        conn.close()

    probe("main")
    thread = threading.Thread(target=probe, args=("ui",), name="rf-ui-1")
    thread.start()
    thread.join()
    thread = threading.Thread(target=probe, args=("job",), name="rf-job-1")
    thread.start()
    thread.join()
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    thread = threading.Thread(target=probe, args=("web-job",), name="rf-job-2")
    thread.start()
    thread.join()
    assert result["main"] == 80
    assert result["ui"] == 80
    assert result["job"] == 30_000
    assert result["web-job"] == 800


def test_catalog_json_stays_snappy_under_scrape_writer(tmp_path: Path):
    store = Store(tmp_path / "scrape-load.sqlite")
    seed = [_latency_listing(i) for i in range(60)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", commit_every=20, fast=True)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
            VALUES ('m1', 'Praha', 'https://www.sreality.cz/hledani/pronajem/byty', '', 'default', 1, 1, ?)
            """,
            ("2020-01-01T00:00:00+00:00",),
        )
    stop = threading.Event()

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_latency_listing(300 + (n + k) % 40) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=24, fast=True)
            n += 1

    thread = threading.Thread(target=writer, name="rf-job-sim", daemon=True)
    thread.start()
    time.sleep(0.05)
    catalog_ms: list[float] = []
    pin_ms: list[float] = []
    list_ms: list[float] = []
    item_ms: list[float] = []
    search_ms: list[float] = []
    guest_ms: list[float] = []
    monitor_ms: list[float] = []
    fresh_ms: list[float] = []
    first: dict[str, float] = {}
    try:
        for i in range(10):
            t0 = time.perf_counter()
            catalog = store.catalog({"limit": 24, "include_pins": "0", "q": "Praha"})
            catalog_ms.append((time.perf_counter() - t0) * 1000)
            assert catalog["items"]
            t0 = time.perf_counter()
            pins = store.catalog(
                {
                    "pins_only": True,
                    "south": "49.90",
                    "north": "50.25",
                    "west": "14.10",
                    "east": "14.75",
                }
            )
            pin_ms.append((time.perf_counter() - t0) * 1000)
            assert pins["items"]
            t0 = time.perf_counter()
            store.recent_notified(24)
            list_ms.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            item = store.catalog_item("", None, "", seed[0].url)
            item_ms.append((time.perf_counter() - t0) * 1000)
            assert item and item.get("url")
            t0 = time.perf_counter()
            store.catalog({"q": "2+kk", "limit": 12, "include_pins": "0"})
            search_ms.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            store.guest_search_used("127.0.0.1", "visitor-1")
            guest_ms.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            monitors = store.list_monitors()
            monitor_ms.append((time.perf_counter() - t0) * 1000)
            assert monitors
            t0 = time.perf_counter()
            fresh = store.catalog_freshness()
            fresh_ms.append((time.perf_counter() - t0) * 1000)
            assert fresh.get("listing_key")
            if i == 0:
                first["catalog"] = catalog_ms[-1]
                first["pins"] = pin_ms[-1]
                first["search"] = search_ms[-1]
                first["guest"] = guest_ms[-1]
                first["monitors"] = monitor_ms[-1]
                first["fresh"] = fresh_ms[-1]
    finally:
        stop.set()
        thread.join(timeout=8)

    def p95(samples: list[float]) -> float:
        ordered = sorted(samples)
        return ordered[max(0, int(round(0.95 * (len(ordered) - 1))))]

    assert p95(catalog_ms) < 80, f"catalog p95 {p95(catalog_ms):.1f}ms {catalog_ms}"
    assert p95(pin_ms) < 50, f"pins p95 {p95(pin_ms):.1f}ms {pin_ms}"
    assert p95(list_ms) < 40, f"listings p95 {p95(list_ms):.1f}ms {list_ms}"
    assert p95(item_ms) < 40, f"item p95 {p95(item_ms):.1f}ms {item_ms}"
    assert p95(search_ms) < 80, f"search p95 {p95(search_ms):.1f}ms {search_ms}"
    assert p95(guest_ms) < 20, f"guest p95 {p95(guest_ms):.1f}ms {guest_ms}"
    assert p95(monitor_ms) < 40, f"monitors p95 {p95(monitor_ms):.1f}ms {monitor_ms}"
    assert p95(fresh_ms) < 20, f"fresh p95 {p95(fresh_ms):.1f}ms {fresh_ms}"
    assert catalog_ms[1:] and p95(catalog_ms[1:]) < 15, f"cached catalog p95 {p95(catalog_ms[1:]):.1f}ms"
    assert pin_ms[1:] and p95(pin_ms[1:]) < 10, f"cached pins p95 {p95(pin_ms[1:]):.1f}ms"
    report = [
        f"catalog first={first['catalog']:.2f}ms p95={p95(catalog_ms):.2f}ms",
        f"pins first={first['pins']:.2f}ms p95={p95(pin_ms):.2f}ms",
        f"search first={first['search']:.2f}ms p95={p95(search_ms):.2f}ms",
        f"guest first={first['guest']:.2f}ms p95={p95(guest_ms):.2f}ms",
        f"monitors first={first['monitors']:.2f}ms p95={p95(monitor_ms):.2f}ms",
        f"fresh first={first['fresh']:.2f}ms p95={p95(fresh_ms):.2f}ms",
    ]
    print("product JSON TTFB under scrape:\n  " + "\n  ".join(report))


def _seed_fat_listings(store: Store, n: int, blob_bytes: int = 2500) -> str:
    blob = "x" * blob_bytes
    now = datetime.now(timezone.utc).isoformat()
    extras = json.dumps(
        {"offer": "Pronájem", "estate": "Byt", "portal": "sreality", "flags": ["pets"], "blob": blob},
        ensure_ascii=False,
    )
    rows = []
    for i in range(n):
        disposition = ("1+kk", "2+kk", "3+kk", "4+kk")[i % 4]
        url = f"https://www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{90_000 + i}"
        key = f"www.sreality.cz/detail/pronajem/byt/{disposition}/praha/{90_000 + i}"
        rows.append(
            (
                key,
                90_000 + i,
                f"Pronájem bytu {disposition}",
                12_000 + (i % 40) * 450,
                f"{12_000 + (i % 40) * 450} Kč/měsíc",
                disposition,
                32 + (i % 9) * 6,
                f"Praha {(i % 8) + 1}",
                url,
                f"https://img.example/fat-{i}.jpg",
                now,
                extras,
                now,
                blob,
                50.08 + (i % 40) * 0.001,
                14.42 + (i % 40) * 0.001,
                1 if i % 5 == 0 else 0,
            )
        )
    with store.connect() as conn:
        conn.executemany(
            """
            INSERT INTO catalog_listings(
                listing_key, id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, extras, last_seen, gone, portal,
                description, lat, lon, canonical_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'sreality', ?, ?, ?, ?)
            """,
            [(*row[:13], row[13], row[14], row[15], row[0]) for row in rows],
        )
        conn.executemany(
            """
            INSERT INTO listings(
                id, monitor_id, name, price_czk, price_label, disposition, area_m2,
                locality, url, image_url, first_seen, extras, last_seen, gone,
                description, lat, lon, listing_key, canonical_key, notified
            ) VALUES (?, '__catalog__', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                    row[9],
                    row[10],
                    row[11],
                    row[12],
                    row[13],
                    row[14],
                    row[15],
                    row[0],
                    row[0],
                    row[16],
                )
                for row in rows
            ],
        )
        conn.execute(
            """
            INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
            VALUES ('m1', 'Praha', 'https://www.sreality.cz/hledani/pronajem/byty', '', 'default', 1, 1, ?)
            """,
            (now,),
        )
        conn.commit()
    return rows[0][8]


def _flush_hot_json(store: Store) -> None:
    store._hot_json_cache.clear()
    store._facets_cache = None
    store._facets_at = 0.0
    store._city_pin_cache.clear()
    store._listing_user_status_cache.clear()


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[max(0, int(round(0.95 * (len(ordered) - 1))))]


def _p50(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[len(ordered) // 2]


def test_catalog_item_url_uses_listings_url_index(tmp_path: Path):
    store = Store(tmp_path / "item-idx.sqlite")
    url = _seed_fat_listings(store, 80, blob_bytes=80)
    with store.read() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(listings)")}
        plan = " ".join(
            row[3]
            for row in conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT listings.id FROM listings
                WHERE listings.url = ?
                ORDER BY listings.last_seen DESC LIMIT 1
                """,
                (url,),
            )
        )
    assert "idx_listings_url" in indexes
    assert "idx_listings_id" in indexes
    assert "idx_listings_disposition" in indexes
    assert "idx_listings_geo_notified" in indexes
    assert "idx_listings_pin_cover" in indexes
    assert "idx_listings_url" in plan
    assert "SCAN listings" not in plan or "USING INDEX" in plan
    item = store.catalog_item("", None, "", url)
    assert item and item["url"] == url
    assert "pets" in (item.get("flags") or [])


def test_map_pin_gps_grid_uses_covering_lat_lon_index(tmp_path: Path):
    store = Store(tmp_path / "pin-idx.sqlite")
    _seed_fat_listings(store, 80, blob_bytes=80)
    gps_clause = (
        "listings.lat IS NOT NULL AND listings.lon IS NOT NULL "
        "AND listings.lat BETWEEN ? AND ? AND listings.lon BETWEEN ? AND ?"
    )
    sql = _pin_gps_grid_sql(gps_clause)
    with store.read() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(listings)")}
        cover_cols = [row[2] for row in conn.execute("PRAGMA index_info('idx_listings_pin_cover')")]
        plan = " ".join(
            row[3]
            for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", (49.90, 50.25, 14.10, 14.75))
        )
    assert "idx_listings_pin_cover" in indexes
    assert cover_cols == list(_PIN_COVER_INDEX_COLS)
    assert "name" in cover_cols and "url" in cover_cols and "locality" in cover_cols
    assert "INDEXED BY idx_listings_pin_cover" in sql
    assert "COALESCE" not in sql
    assert "idx_listings_pin_cover" in plan or "COVERING INDEX" in plan
    assert "CO-ROUTINE" not in plan
    assert "SCAN listings" not in plan or "USING INDEX" in plan
    pins = store.catalog(
        {
            "pins_only": True,
            "south": "49.90",
            "north": "50.25",
            "west": "14.10",
            "east": "14.75",
        }
    )
    assert pins["items"]
    assert all(item.get("lat") is not None and item.get("lon") is not None for item in pins["items"])
    # 80 rows share 40 GPS cells at 0.001°; grid must collapse them.
    assert len(pins["items"]) <= 50


def test_map_pin_tight_zoom_hydrates_labels_via_covering_index(tmp_path: Path):
    store = Store(tmp_path / "pin-tight.sqlite")
    _seed_fat_listings(store, 80, blob_bytes=4000)
    gps_clause = (
        "listings.lat IS NOT NULL AND listings.lon IS NOT NULL "
        "AND listings.lat BETWEEN ? AND ? AND listings.lon BETWEEN ? AND ?"
    )
    sql = _pin_gps_tight_sql(gps_clause, pin_cap=8000)
    with store.read() as conn:
        cover_cols = [row[2] for row in conn.execute("PRAGMA index_info('idx_listings_pin_cover')")]
        plan = " ".join(
            row[3]
            for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", (50.08, 50.12, 14.42, 14.46))
        )
    assert cover_cols == list(_PIN_COVER_INDEX_COLS)
    assert "COVERING INDEX" in plan
    assert "idx_listings_pin_cover" in plan
    assert "SCAN listings" not in plan or "USING INDEX" in plan
    pins = store.catalog(
        {
            "pins_only": True,
            "south": "50.08",
            "north": "50.12",
            "west": "14.42",
            "east": "14.46",
        }
    )
    assert pins["items"]
    assert all(item.get("name") and item.get("url") and item.get("locality") for item in pins["items"])
    assert all("extras" not in item and "description" not in item for item in pins["items"])
    assert all(item.get("lat") is not None and item.get("lon") is not None for item in pins["items"])


def test_pin_cover_index_rebuilds_when_label_columns_missing(tmp_path: Path):
    store = Store(tmp_path / "pin-rebuild.sqlite")
    with store.connect() as conn:
        conn.execute("DROP INDEX IF EXISTS idx_listings_pin_cover")
        conn.execute(
            "CREATE INDEX idx_listings_pin_cover ON listings("
            "lat, lon, notified, id, monitor_id, price_czk, listing_key, canonical_key)"
        )
        conn.commit()
    again = Store(tmp_path / "pin-rebuild.sqlite")
    with again.read() as conn:
        cover_cols = [row[2] for row in conn.execute("PRAGMA index_info('idx_listings_pin_cover')")]
    assert cover_cols == list(_PIN_COVER_INDEX_COLS)


def test_catalog_q_uses_listings_fts_not_fat_like(tmp_path: Path):
    store = Store(tmp_path / "fts-idx.sqlite")
    _seed_fat_listings(store, 80, blob_bytes=80)
    assert store._listings_fts is True
    match = listings_fts_match_query("Praha")
    assert match
    sql = f"""
        SELECT listings.id FROM listings INDEXED BY idx_listings_first_seen
        WHERE {LISTINGS_FTS_MATCH_SQL}
        ORDER BY listings.first_seen DESC LIMIT 96
    """
    with store.read() as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        }
        plan = " ".join(
            row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", (match,))
        )
        count_plan = " ".join(
            row[3]
            for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM listings_fts WHERE listings_fts MATCH ?",
                (match,),
            )
        )
        like_plan = " ".join(
            row[3]
            for row in conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT COUNT(*) FROM listings
                WHERE listings.name LIKE ? OR listings.locality LIKE ? OR listings.disposition LIKE ?
                """,
                ("%Praha%", "%Praha%", "%Praha%"),
            )
        )
    assert "listings_fts" in tables
    assert "listings_fts" in plan
    assert "idx_listings_first_seen" in plan
    assert "CORRELATED" not in plan
    assert "LIKE" not in plan
    assert "listings_fts" in count_plan
    assert "LIKE" not in count_plan
    assert "SCAN listings" in like_plan and "LIKE" not in count_plan
    page = store.catalog({"q": "Praha", "limit": 12, "include_pins": "0"})
    assert page["items"]
    assert page["total"] >= len(page["items"])
    pins = store.catalog(
        {
            "pins_only": True,
            "south": "49.90",
            "north": "50.25",
            "west": "14.10",
            "east": "14.75",
        }
    )
    assert pins["items"]
    with store.read() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(listings)")}
    assert "idx_listings_pin_cover" in indexes


def test_catalog_q_fts_city_disposition_diacritics_and_upsert(tmp_path: Path):
    from app.sreality import Listing

    store = Store(tmp_path / "fts-sem.sqlite")

    def item(i: int, *, name: str, locality: str, disposition: str) -> Listing:
        return Listing(
            id=50_000 + i,
            name=name,
            price_czk=18_000,
            price_label="18000 Kč/měsíc",
            disposition=disposition,
            area_m2=50,
            locality=locality,
            url=f"https://www.sreality.cz/detail/pronajem/byt/{disposition}/mesto/{50_000 + i}",
            image_url=f"https://img.example/fts-{i}.jpg",
            lat=50.08,
            lon=14.42,
            extras={"offer": "Pronájem", "estate": "Byt", "portal": "sreality"},
        )

    store.upsert_catalog_listings_batch(
        [
            item(1, name="Pronájem bytu 2+kk", locality="Praha 7 – Holešovice", disposition="2+kk"),
            item(2, name="Prodej domu 5+kk", locality="Brno-střed", disposition="5+kk"),
            item(3, name="Ateliér 1+kk", locality="Ostrava", disposition="1+kk"),
        ],
        kind="seeded",
        fast=True,
    )
    _flush_hot_json(store)
    praha = store.catalog({"q": "Praha", "limit": 12, "include_pins": "0"})
    assert [row["locality"] for row in praha["items"]] == ["Praha 7 – Holešovice"]
    holes = store.catalog({"q": "Holesovice", "limit": 12, "include_pins": "0"})
    assert holes["items"] and "Holešovice" in holes["items"][0]["locality"]
    prefix = store.catalog({"q": "Holešov", "limit": 12, "include_pins": "0"})
    assert prefix["items"] and "Holešovice" in prefix["items"][0]["locality"]
    kk = store.catalog({"q": "2+kk", "limit": 12, "include_pins": "0"})
    assert kk["items"] and all(
        row["disposition"] == "2+kk" or "2+kk" in (row["name"] or "") for row in kk["items"]
    )
    interior = store.catalog({"q": "rah", "limit": 12, "include_pins": "0"})
    assert interior["items"] == []

    store.upsert_catalog_listings_batch(
        [item(2, name="Prodej domu 5+kk", locality="Praha 2", disposition="5+kk")],
        kind="refresh",
        fast=True,
    )
    _flush_hot_json(store)
    moved = store.catalog({"q": "Praha", "limit": 12, "include_pins": "0"})
    localities = {row["locality"] for row in moved["items"]}
    assert "Praha 2" in localities
    assert "Praha 7 – Holešovice" in localities
    gone_brno = store.catalog({"q": "Brno", "limit": 12, "include_pins": "0"})
    assert gone_brno["items"] == []


def test_catalog_hidden_filter_stays_off_until_listing_user_exists(tmp_path: Path):
    store = Store(tmp_path / "hidden.sqlite")
    url = _seed_fat_listings(store, 12, blob_bytes=40)
    assert store._listing_user_has_status("hidden") is False
    page = store.catalog({"limit": 12, "include_pins": "0"})
    assert any(item["url"] == url for item in page["items"])
    store.set_listing_user(url, status="hidden")
    assert store._listing_user_has_status("hidden") is True
    _flush_hot_json(store)
    hidden = store.catalog({"limit": 12, "include_pins": "0"})
    assert all(item["url"] != url for item in hidden["items"])


def test_fat_catalog_json_stays_snappy_under_scrape_writer(tmp_path: Path, capsys):
    store = Store(tmp_path / "fat-scrape.sqlite")
    url = _seed_fat_listings(store, 8000, blob_bytes=2500)
    pin_filters = {
        "pins_only": True,
        "south": "49.90",
        "north": "50.25",
        "west": "14.10",
        "east": "14.75",
    }
    tight_filters = {
        "pins_only": True,
        "south": "50.08",
        "north": "50.12",
        "west": "14.42",
        "east": "14.46",
    }

    def sample(*, flush: bool) -> dict[str, float]:
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        catalog = store.catalog({"limit": 24, "include_pins": "0"})
        catalog_ms = (time.perf_counter() - t0) * 1000
        assert catalog["items"]
        assert catalog["items"][0].get("extras")
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        search = store.catalog({"q": "Praha", "limit": 24, "include_pins": "0"})
        search_ms = (time.perf_counter() - t0) * 1000
        assert search["items"]
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        pins = store.catalog(pin_filters)
        pin_ms = (time.perf_counter() - t0) * 1000
        assert pins["items"]
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        tight = store.catalog(tight_filters)
        tight_ms = (time.perf_counter() - t0) * 1000
        assert tight["items"]
        assert all(item.get("name") and item.get("url") and item.get("locality") for item in tight["items"])
        assert all("extras" not in item and "description" not in item for item in tight["items"])
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        listings = store.recent_notified(24)
        list_ms = (time.perf_counter() - t0) * 1000
        assert listings
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        monitors = store.list_monitors()
        watch_ms = (time.perf_counter() - t0) * 1000
        assert monitors
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        item = store.catalog_item("", None, "", url)
        item_ms = (time.perf_counter() - t0) * 1000
        assert item and item.get("url")
        if flush:
            _flush_hot_json(store)
        t0 = time.perf_counter()
        fresh = store.catalog_freshness()
        fresh_ms = (time.perf_counter() - t0) * 1000
        assert fresh.get("listing_key")
        return {
            "catalog": catalog_ms,
            "search": search_ms,
            "pins": pin_ms,
            "pins_tight": tight_ms,
            "listings": list_ms,
            "watch": watch_ms,
            "item": item_ms,
            "fresh": fresh_ms,
        }

    sample(flush=True)
    quiet_pins = store.catalog(pin_filters)
    assert quiet_pins["items"]
    assert len(quiet_pins["items"]) <= 80
    quiet_tight = store.catalog(tight_filters)
    assert quiet_tight["items"]
    assert all(item.get("name") and item.get("url") for item in quiet_tight["items"])
    quiet_miss = [sample(flush=True) for _ in range(8)]
    stop = threading.Event()

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_latency_listing(400 + (n + k) % 80) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=500, fast=True)
            n += 1

    thread = threading.Thread(target=writer, name="rf-job-sim", daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        writer_miss = [sample(flush=True) for _ in range(8)]
        writer_hit = [sample(flush=False) for _ in range(8)]
    finally:
        stop.set()
        thread.join(timeout=12)

    def col(rows: list[dict[str, float]], key: str) -> list[float]:
        return [row[key] for row in rows]

    report = []
    for label, rows in (("quiet", quiet_miss), ("writer", writer_miss), ("writer-cached", writer_hit)):
        for key in ("catalog", "search", "pins", "pins_tight", "listings", "watch", "item", "fresh"):
            values = col(rows, key)
            report.append(f"{label} {key} p50={_p50(values):.2f}ms p95={_p95(values):.2f}ms")
    print("fat catalog JSON under scrape:\n  " + "\n  ".join(report))

    assert _p95(col(quiet_miss, "catalog")) < 45, col(quiet_miss, "catalog")
    assert _p95(col(quiet_miss, "search")) < 25, col(quiet_miss, "search")
    assert _p95(col(quiet_miss, "pins")) < 50, col(quiet_miss, "pins")
    assert _p95(col(quiet_miss, "pins_tight")) < 25, col(quiet_miss, "pins_tight")
    assert _p95(col(quiet_miss, "listings")) < 20, col(quiet_miss, "listings")
    assert _p95(col(quiet_miss, "item")) < 12, col(quiet_miss, "item")
    assert _p95(col(quiet_miss, "watch")) < 12, col(quiet_miss, "watch")
    assert _p95(col(writer_miss, "catalog")) < 50, col(writer_miss, "catalog")
    assert _p95(col(writer_miss, "search")) < 35, col(writer_miss, "search")
    assert _p95(col(writer_miss, "pins")) < 50, col(writer_miss, "pins")
    assert _p95(col(writer_miss, "pins_tight")) < 35, col(writer_miss, "pins_tight")
    assert _p95(col(writer_miss, "listings")) < 25, col(writer_miss, "listings")
    assert _p95(col(writer_miss, "item")) < 12, col(writer_miss, "item")
    # 2s hot JSON can expire during writer_miss; first cached sample may recompute.
    assert _p95(col(writer_hit, "catalog")) < 25, col(writer_hit, "catalog")
    assert _p95(col(writer_hit, "search")) < 25, col(writer_hit, "search")
    assert _p95(col(writer_hit, "item")) < 12, col(writer_hit, "item")
    assert _p95(col(writer_hit, "listings")) < 15, col(writer_hit, "listings")


def test_catalog_serves_stale_json_when_writer_locks_sqlite(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "stale.sqlite")
    store.upsert_catalog_listings_batch([_latency_listing(1)], kind="seeded", fast=True)
    filters = {"q": "Praha", "limit": 12, "include_pins": "0"}
    first = store.catalog(filters)
    assert first["items"]

    def boom(_filters):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_catalog_query", boom)
    for key, (at, payload) in list(store._hot_json_cache.items()):
        store._hot_json_cache[key] = (at - 5.0, payload)
    t0 = time.perf_counter()
    again = store.catalog(filters)
    ms = (time.perf_counter() - t0) * 1000
    assert again.get("stale") is True
    assert again["items"] == first["items"]
    assert ms < 15, f"stale catalog {ms:.1f}ms"


def test_monitors_serve_stale_json_when_writer_locks_sqlite(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "mon-stale.sqlite")
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
            VALUES ('m1', 'Praha', 'https://www.sreality.cz/hledani/pronajem/byty', '', 'default', 1, 1, ?)
            """,
            ("2020-01-01T00:00:00+00:00",),
        )
    first = store.list_monitors()
    assert any(item["id"] == "m1" for item in first)

    def boom():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_list_monitors_query", boom)
    for key, (at, payload) in list(store._hot_json_cache.items()):
        store._hot_json_cache[key] = (at - 5.0, payload)
    t0 = time.perf_counter()
    again = store.list_monitors()
    ms = (time.perf_counter() - t0) * 1000
    assert [item["id"] for item in again] == [item["id"] for item in first]
    assert ms < 15, f"stale monitors {ms:.1f}ms"


def test_guest_search_serves_stale_used_when_writer_locks_sqlite(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "guest-stale.sqlite")
    assert store.guest_search_used("127.0.0.1", "visitor-1") is False
    assert store.grant_guest_search("127.0.0.1", "visitor-1")
    assert store.guest_search_used("127.0.0.1", "visitor-1") is True

    def boom(_ip, _visitor):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_guest_search_used_query", boom)
    for key, (at, payload) in list(store._hot_json_cache.items()):
        store._hot_json_cache[key] = (at - 5.0, payload)
    t0 = time.perf_counter()
    assert store.guest_search_used("127.0.0.1", "visitor-1") is True
    ms = (time.perf_counter() - t0) * 1000
    assert ms < 15, f"stale guest {ms:.1f}ms"


def test_catalog_freshness_tracks_newest_listing(tmp_path: Path):
    store = Store(tmp_path / "fresh.sqlite")
    store.upsert_catalog_listings_batch([_latency_listing(1)], kind="seeded", fast=True)
    first = store.catalog_freshness()
    assert first.get("listing_key")
    assert first.get("id")
    store.upsert_catalog_listings_batch([_latency_listing(2)], kind="seeded", fast=True)
    again = store.catalog_freshness()
    assert again.get("id") != first.get("id")
    assert again.get("listing_key")


def test_list_monitors_light_skips_aggregate_counts(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "light.sqlite")
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
            VALUES ('m1', 'Praha', 'https://www.sreality.cz/hledani/pronajem/byty', '', 'default', 1, 1, ?)
            """,
            ("2020-01-01T00:00:00+00:00",),
        )
    called = {"n": 0}
    monkeypatch.setattr(store, "count", lambda *_a, **_k: called.__setitem__("n", called["n"] + 1) or 0)
    monkeypatch.setattr(store, "new_today_count", lambda *_a, **_k: called.__setitem__("n", called["n"] + 1) or 0)
    items = store.list_monitors_light()
    ours = next(item for item in items if item["id"] == "m1")
    assert ours["tracked"] == 0
    assert ours["new_today"] == 0
    assert called["n"] == 0


def test_user_from_session_skips_sqlite_without_cookie(tmp_path: Path, monkeypatch):
    from app.account import user_from_session

    store = Store(tmp_path / "session.sqlite")

    def boom(*_a, **_k):
        raise AssertionError("get_meta should not run without a session cookie")

    monkeypatch.setattr(store, "get_meta", boom)
    assert user_from_session(store, None) is None
    assert user_from_session(store, "") is None


def test_app_shell_redirect_is_cookie_presence_only():
    assert app_shell_redirect_for_cookies("/prehled", {}) == "/prihlaseni"
    assert app_shell_redirect_for_cookies("/prehled", {"realitify_session": "tok"}) is None
    assert app_shell_redirect_for_cookies("/nabidka", {}) == "/registrace?next=%2Fnabidka"
    assert (
        app_shell_redirect_for_cookies("/nabidka", {}, "listing_key=abc")
        == "/registrace?next=%2Fnabidka%3Flisting_key%3Dabc"
    )
    assert app_shell_redirect_for_cookies("/nabidka", {"rf_guest_search": "guest"}) is None
    scope = {
        "headers": [(b"cookie", b"realitify_session=tok")],
        "query_string": b"",
    }
    assert app_shell_redirect("/prehled", scope) is None
    empty = {"headers": [], "query_string": b""}
    assert app_shell_redirect("/prehled", empty) == "/prihlaseni"


def test_fastapi_html_fallbacks_stay_memory_and_skip_sqlite():
    src = Path(__file__).resolve().parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
    start = src.index("async def require_account")
    end = src.index("def _extension_cors_origin")
    guard = src[start:end]
    assert "user_from_session" not in guard
    assert "guest_search_has_access" not in guard
    assert "app_shell_redirect_for_cookies" in guard
    assert "hub.store" not in guard
    assert "FileResponse(config.WEB_DIR / \"index.html\"" not in src
    assert "FileResponse(config.WEB_DIR / \"admin\" / \"index.html\"" not in src
    assert "return web_page(\"index.html\")" in src
    assert "return web_page(\"admin/index.html\")" in src
    assert "return site_page(\"prihlaseni.html\")" in src
    assert "return site_page(\"kontakt.html\")" in src
    assert "return site_page(\"uspechy.html\")" in src
    assert "return site_page(\"byt.html\")" in src
    assert "return site_page(\"dum.html\")" in src
    assert "return site_page(\"obchodni-podminky.html\")" in src
    assert "return site_page(\"ochrana-soukromi.html\")" in src
    assert "return site_page(\"nastaveni-cookies.html\")" in src
    assert "return site_page(\"heslo.html\")" in src
    assert "return site_page(\"clanek.html\")" in src
    for path in (
        "/",
        "/kontakt",
        "/kontakt/",
        "/uspechy",
        "/uspechy/martina-tomas",
        "/obchodni-podminky",
        "/ochrana-soukromi",
        "/nastaveni-cookies",
        "/byt",
        "/dum",
        "/heslo",
    ):
        assert instant_page_name(path), path
    assert instant_asset_rel("/static/site/inquiries.js") == "site/inquiries.js"
    assert instant_asset_rel("/static/site/legal.js") == "site/legal.js"
    assert instant_asset_rel("/static/site/cookies.js") == "site/cookies.js"
    assert instant_asset_rel("/static/site/stories-list.js") == "site/stories-list.js"
    assert instant_asset_rel("/static/site/landing-search.js") == "site/landing-search.js"
    assert instant_root_file("/sw.js")[0] == "sw.js"
    assert instant_root_file("/manifest.webmanifest")[0] == "manifest.webmanifest"
    assert set(INSTANT_ROUTES).issuperset(
        {
            "/",
            "/kontakt",
            "/uspechy",
            "/byt",
            "/dum",
            "/obchodni-podminky",
            "/ochrana-soukromi",
            "/nastaveni-cookies",
            "/heslo",
        }
    )
    web_body.cache_clear()
    page = web_page("admin/index.html")
    assert page.body == web_body("admin/index.html")
    assert b"fonts.googleapis" not in page.body
    assert page.headers["cache-control"] == "no-store, max-age=0"


def _percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    if not ordered:
        return 0.0
    return ordered[max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))]


async def _asgi_post(app, path: str, payload: dict) -> tuple[int, dict[bytes, bytes], bytes]:
    sent: list[dict] = []
    raw = json.dumps(payload).encode()
    received = {"done": False}

    async def receive() -> dict:
        if received["done"]:
            return {"type": "http.disconnect"}
        received["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 80),
        },
        receive,
        send,
    )
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body") or b"" for item in sent if item["type"] == "http.response.body")
    headers = {key: value for key, value in start.get("headers") or []}
    return int(start["status"]), headers, body


def test_game_json_and_hry_html_ttfb_under_scrape_writer(tmp_path: Path):
    reset_pool_cache()
    store = Store(tmp_path / "game-scrape.sqlite")
    seed = [_latency_listing(i) for i in range(80)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", commit_every=20, fast=True)
    stop = threading.Event()
    hit = {"n": 0}

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_latency_listing(400 + (n + k) % 50) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=24, fast=True)
            if n % 3 == 0:
                store.record_scrape_tick({"kind": "new_discovery", "n": n, "role": "all"})
            n += 1

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    thread = threading.Thread(target=writer, name="rf-job-sim", daemon=True)
    thread.start()
    time.sleep(0.05)

    html_paths = (
        "/",
        "/hry",
        "/hry/vyssi-nizsi",
        "/hry/najem",
        "/prihlaseni",
        "/registrace",
        "/heslo",
        "/kontakt",
        "/uspechy",
        "/uspechy/martina-tomas",
        "/obchodni-podminky",
        "/ochrana-soukromi",
        "/nastaveni-cookies",
        "/byt",
        "/dum",
    )
    app_html_paths = (
        "/prehled",
        "/nabidka",
        "/monitory",
        "/filtry",
        "/zprava",
        "/nastaveni",
        "/nastaveni/profily",
        "/admin",
        "/admin/prehled",
    )
    asset_paths = (
        "/static/site/games.css",
        "/static/site/games.js",
        "/static/site/fonts/archivo-black-latin.woff2",
        "/static/site/auth.css",
        "/static/site/site.css",
        "/static/site/site.js",
        "/static/t.js",
        "/static/styles.css",
        "/static/admin/admin.css",
    )
    json_paths = ("/api/public/games/higher-lower", "/api/public/games/rent-round")
    session = [(b"cookie", b"realitify_session=test-token")]
    samples = {
        path: []
        for path in (
            *html_paths,
            *app_html_paths,
            *asset_paths,
            *json_paths,
            "/api/public/games/rent-score",
            "/prehled unauth",
            "/nabidka unauth",
        )
    }

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        for _ in range(24):
            for path in html_paths:
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                samples[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body
            for path in app_html_paths:
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path, headers=session)
                samples[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, "/prehled")
            samples["/prehled unauth"].append((time.perf_counter() - t0) * 1000)
            assert status == 303
            assert headers[b"location"] == b"/prihlaseni"
            assert body == b""
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, "/nabidka")
            samples["/nabidka unauth"].append((time.perf_counter() - t0) * 1000)
            assert status == 303
            assert headers[b"location"].startswith(b"/registrace?next=")
            for path in asset_paths:
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                samples[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body
            for path in json_paths:
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                samples[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                payload = json.loads(body)
                if path.endswith("higher-lower"):
                    assert payload["left"]["locality_key"] == payload["right"]["locality_key"]
                else:
                    assert payload["items"]
                    assert len({item.get("pair_key") for item in payload["items"]}) == 1
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_post(
                app,
                "/api/public/games/rent-score",
                {
                    "name": "TTFB",
                    "guesses": [
                        {"id": "seed-zizkov-2kk", "guess": 16500},
                        {"id": "seed-zizkov-1kk", "guess": 18900},
                    ],
                },
            )
            samples["/api/public/games/rent-score"].append((time.perf_counter() - t0) * 1000)
            assert status == 200
            scored = json.loads(body)
            assert scored["score"] >= 0
            assert "items" in scored

        burst = await asyncio.gather(
            *[
                *[_asgi_get(app, path) for path in (*html_paths, *json_paths) * 4],
                *[_asgi_get(app, path, headers=session) for path in app_html_paths * 4],
            ]
        )
        assert all(status == 200 for status, _headers, _body in burst)

    try:
        asyncio.run(run())
    finally:
        stop.set()
        thread.join(timeout=8)

    assert hit["n"] == 0
    report = []
    for path, values in samples.items():
        cold = values[0]
        warm = values[1:] or values
        p50 = _percentile(values, 0.50)
        p95 = _percentile(values, 0.95)
        warm_p95 = _percentile(warm, 0.95)
        report.append(
            f"{path} n={len(values)} cold={cold:.2f}ms p50={p50:.2f}ms p95={p95:.2f}ms warm_p95={warm_p95:.2f}ms"
        )
        html = path in html_paths or path in app_html_paths or path in asset_paths or path.endswith("unauth")
        assert p50 < (8 if html else 15), f"{path} p50 {p50:.1f}ms {values}"
        assert p95 < (25 if html else 40), f"{path} p95 {p95:.1f}ms {values}"
        assert warm_p95 < (8 if html else 25), f"{path} warm p95 {warm_p95:.1f}ms {warm}"
    print("game TTFB under scrape:\n  " + "\n  ".join(report))


def test_hry_routes_ttfb_cold_warm_under_scrape_writer(tmp_path: Path):
    """Dedicated InstantSite /hry* audit: HTML memory path + seed/live JSON vs scrape writer."""
    reset_pool_cache()
    store = Store(tmp_path / "hry-ttfb.sqlite")
    seed = [_hry_ttfb_listing(i) for i in range(240)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", commit_every=40, fast=True)
    stop = threading.Event()
    hit = {"n": 0}

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_hry_ttfb_listing(800 + (n + k) % 80) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=24, fast=True)
            if n % 3 == 0:
                store.record_scrape_tick({"kind": "new_discovery", "n": n, "role": "all"})
            n += 1

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    html_paths = ("/hry", "/hry/vyssi-nizsi", "/hry/najem")
    json_paths = ("/api/public/games/higher-lower", "/api/public/games/rent-round")
    cold: dict[str, float] = {}
    warm: dict[str, list[float]] = {path: [] for path in (*html_paths, *json_paths)}

    thread = threading.Thread(target=writer, name="rf-hry-ttfb", daemon=True)
    thread.start()
    time.sleep(0.05)

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        for path in (*html_paths, *json_paths):
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_get(app, path)
            cold[path] = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert body
            if path in json_paths:
                payload = json.loads(body)
                if path.endswith("higher-lower"):
                    assert payload["left"]["locality_key"] == payload["right"]["locality_key"]
                    assert payload.get("seeded") is True
                else:
                    assert payload["items"]
                    assert payload.get("seeded") is True
                    assert "v řádu minut" not in (payload.get("vanish_label") or "")
            else:
                assert b"fonts.googleapis" not in body
                assert b"board-tease" in body or path != "/hry/najem"
                if path == "/hry":
                    assert "V ŘÁDU HODIN".encode() in body
                    assert "V ŘÁDU MINUT".encode() not in body
        wait_refresh(0.8)
        for _ in range(12):
            for path in (*html_paths, *json_paths):
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                warm[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body
                if path in json_paths:
                    payload = json.loads(body)
                    if path.endswith("higher-lower"):
                        assert payload["left"]["locality_key"] == payload["right"]["locality_key"]
                    else:
                        assert payload["items"]
                        assert "v řádu minut" not in (payload.get("vanish_label") or "")

    try:
        asyncio.run(run())
    finally:
        stop.set()
        thread.join(timeout=8)
        reset_pool_cache()

    assert hit["n"] == 0
    report = []
    for path in (*html_paths, *json_paths):
        values = warm[path]
        p50 = _percentile(values, 0.50)
        p95 = _percentile(values, 0.95)
        html = path in html_paths
        report.append(
            f"{path} cold={cold[path]:.2f}ms warm_n={len(values)} warm_p50={p50:.2f}ms warm_p95={p95:.2f}ms"
        )
        assert cold[path] < (8 if html else 15), f"{path} cold {cold[path]:.1f}ms"
        assert p50 < (4 if html else 12), f"{path} warm p50 {p50:.1f}ms {values}"
        assert p95 < (8 if html else 25), f"{path} warm p95 {p95:.1f}ms {values}"
    print("hry TTFB cold/warm under scrape:\n  " + "\n  ".join(report))


def test_hry_instant_path_stays_fast_under_sqlite_exclusive_lock(tmp_path: Path):
    reset_pool_cache()
    store = Store(tmp_path / "hry-lock.sqlite")
    store.upsert_catalog_listings_batch(
        [_hry_ttfb_listing(i) for i in range(40)], kind="seeded", commit_every=20, fast=True
    )
    locker = sqlite3.connect(store.path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("UPDATE meta SET value = value")
    hit = {"n": 0}

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        report = []
        for path in (
            "/hry",
            "/hry/vyssi-nizsi",
            "/hry/najem",
            "/api/public/games/higher-lower",
            "/api/public/games/rent-round",
        ):
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            report.append(f"{path} {ms:.2f}ms")
            assert status == 200, path
            assert body
            assert ms < 40, f"{path} under exclusive lock {ms:.1f}ms"
        print("hry InstantSite under exclusive lock:\n  " + "\n  ".join(report))
        assert hit["n"] == 0

    try:
        asyncio.run(run())
    finally:
        locker.rollback()
        locker.close()
        reset_pool_cache()


def test_home_auth_dashboard_ttfb_cold_warm_under_scrape_writer(tmp_path: Path):
    """Dedicated InstantSite homepage/auth/dashboard audit vs scrape writer."""
    reset_pool_cache()
    store = Store(tmp_path / "home-ttfb.sqlite")
    seed = [_hry_ttfb_listing(i) for i in range(240)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", commit_every=40, fast=True)
    stop = threading.Event()
    hit = {"n": 0}

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_hry_ttfb_listing(800 + (n + k) % 80) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=24, fast=True)
            if n % 3 == 0:
                store.record_scrape_tick({"kind": "new_discovery", "n": n, "role": "all"})
            n += 1

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    html_paths = ("/", "/prihlaseni", "/registrace", "/prehled", "/admin")
    asset_paths = (
        "/static/site/site.css",
        "/static/site/site.js",
        "/static/site/auth.css",
        "/static/t.js",
        "/static/styles.css",
        "/static/admin/admin.css",
    )
    session = [(b"cookie", b"realitify_session=test-token")]
    needles = {
        "/": "NEJLEPŠÍ BYTY ZMIZÍ".encode(),
        "/prihlaseni": "PŘIHLÁŠENÍ".encode(),
        "/registrace": "VYTVOŘTE SI ÚČET".encode(),
        "/prehled": b'id="view-overview"',
        "/admin": "ADMIN PŘIHLÁŠENÍ".encode(),
    }
    cold: dict[str, float] = {}
    warm: dict[str, list[float]] = {
        path: [] for path in (*html_paths, *asset_paths, "/prehled unauth")
    }

    thread = threading.Thread(target=writer, name="rf-home-ttfb", daemon=True)
    thread.start()
    time.sleep(0.05)

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        for path in html_paths:
            headers = session if path in {"/prehled", "/admin"} else None
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_get(app, path, headers=headers)
            cold[path] = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert needles[path] in body
            assert b"fonts.googleapis" not in body
        t0 = time.perf_counter()
        status, headers, body = await _asgi_get(app, "/prehled")
        cold["/prehled unauth"] = (time.perf_counter() - t0) * 1000
        assert status == 303
        assert headers[b"location"] == b"/prihlaseni"
        assert body == b""
        for path in asset_paths:
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_get(app, path)
            cold[path] = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert body
        for _ in range(12):
            for path in html_paths:
                req_headers = session if path in {"/prehled", "/admin"} else None
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path, headers=req_headers)
                warm[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, "/prehled")
            warm["/prehled unauth"].append((time.perf_counter() - t0) * 1000)
            assert status == 303
            assert headers[b"location"] == b"/prihlaseni"
            for path in asset_paths:
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                warm[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body

    try:
        asyncio.run(run())
    finally:
        stop.set()
        thread.join(timeout=8)
        reset_pool_cache()

    assert hit["n"] == 0
    report = []
    for path in (*html_paths, "/prehled unauth", *asset_paths):
        values = warm[path]
        p50 = _percentile(values, 0.50)
        p95 = _percentile(values, 0.95)
        report.append(
            f"{path} cold={cold[path]:.2f}ms warm_n={len(values)} warm_p50={p50:.2f}ms warm_p95={p95:.2f}ms"
        )
        assert cold[path] < 8, f"{path} cold {cold[path]:.1f}ms"
        assert p50 < 1, f"{path} warm p50 {p50:.1f}ms {values}"
        assert p95 < 4, f"{path} warm p95 {p95:.1f}ms {values}"
    print("home/auth/dashboard TTFB cold/warm under scrape:\n  " + "\n  ".join(report))


def test_home_auth_dashboard_instant_path_stays_fast_under_sqlite_exclusive_lock(tmp_path: Path):
    store = Store(tmp_path / "home-lock.sqlite")
    store.upsert_catalog_listings_batch(
        [_hry_ttfb_listing(i) for i in range(40)], kind="seeded", commit_every=20, fast=True
    )
    locker = sqlite3.connect(store.path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("UPDATE meta SET value = value")
    hit = {"n": 0}
    session = [(b"cookie", b"realitify_session=test-token")]

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        report = []
        checks = (
            ("/", None),
            ("/prihlaseni", None),
            ("/registrace", None),
            ("/prehled", session),
            ("/admin", session),
            ("/prehled", None),
            ("/static/site/site.js", None),
            ("/static/t.js", None),
        )
        for path, headers in checks:
            t0 = time.perf_counter()
            status, resp_headers, body = await _asgi_get(app, path, headers=headers)
            ms = (time.perf_counter() - t0) * 1000
            label = "/prehled unauth" if path == "/prehled" and headers is None else path
            report.append(f"{label} {ms:.2f}ms")
            if path == "/prehled" and headers is None:
                assert status == 303
                assert resp_headers[b"location"] == b"/prihlaseni"
            else:
                assert status == 200, path
                assert body
            assert ms < 40, f"{label} under exclusive lock {ms:.1f}ms"
        print("home InstantSite under exclusive lock:\n  " + "\n  ".join(report))
        assert hit["n"] == 0

    try:
        asyncio.run(run())
    finally:
        locker.rollback()
        locker.close()


def test_remaining_shells_ttfb_cold_warm_under_scrape_writer(tmp_path: Path):
    """Dedicated InstantSite leftover HTML shells + companion JS vs scrape writer."""
    reset_pool_cache()
    store = Store(tmp_path / "shell-ttfb.sqlite")
    seed = [_hry_ttfb_listing(i) for i in range(240)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", commit_every=40, fast=True)
    stop = threading.Event()
    hit = {"n": 0}

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_hry_ttfb_listing(800 + (n + k) % 80) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=24, fast=True)
            if n % 3 == 0:
                store.record_scrape_tick({"kind": "new_discovery", "n": n, "role": "all"})
            n += 1

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    html_paths = (
        "/kontakt",
        "/uspechy",
        "/uspechy/martina-tomas",
        "/obchodni-podminky",
        "/ochrana-soukromi",
        "/nastaveni-cookies",
        "/byt",
        "/dum",
        "/heslo",
    )
    asset_paths = (
        "/static/site/inquiries.js",
        "/static/site/legal.js",
        "/static/site/cookies.js",
        "/static/site/stories-list.js",
        "/static/site/stories-articles.js",
        "/static/site/landing-search.js",
        "/static/site/assets/room/dum.webp",
        "/sw.js",
        "/manifest.webmanifest",
    )
    needles = {
        "/kontakt": "OZVĚTE SE NÁM".encode(),
        "/uspechy": "NAŠLI SI VYSNĚNÉ BYDLENÍ".encode(),
        "/uspechy/martina-tomas": b"article-page",
        "/obchodni-podminky": "OBCHODNÍ PODMÍNKY".encode(),
        "/ochrana-soukromi": "OCHRANA SOUKROMÍ".encode(),
        "/nastaveni-cookies": "NASTAVENÍ COOKIES".encode(),
        "/byt": "Začít hlídat zdarma".encode(),
        "/dum": "Hlídání domů v ČR".encode(),
        "/heslo": "OBNOVTE SI HESLO".encode(),
    }
    cold: dict[str, float] = {}
    warm: dict[str, list[float]] = {path: [] for path in (*html_paths, *asset_paths)}

    thread = threading.Thread(target=writer, name="rf-shell-ttfb", daemon=True)
    thread.start()
    time.sleep(0.05)

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        for path in html_paths:
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_get(app, path)
            cold[path] = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert needles[path] in body, path
            assert b"fonts.googleapis" not in body
            assert b"Inter:" not in body
        for path in asset_paths:
            t0 = time.perf_counter()
            status, headers, body = await _asgi_get(app, path)
            cold[path] = (time.perf_counter() - t0) * 1000
            assert status == 200, path
            assert body
            if path in {"/sw.js", "/manifest.webmanifest"}:
                assert headers[b"cache-control"] == b"no-store, max-age=0"
            else:
                assert headers[b"cache-control"].startswith(b"public")
        for _ in range(12):
            for path in (*html_paths, *asset_paths):
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                warm[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body

    try:
        asyncio.run(run())
    finally:
        stop.set()
        thread.join(timeout=8)
        reset_pool_cache()

    assert hit["n"] == 0
    report = []
    for path in (*html_paths, *asset_paths):
        values = warm[path]
        p50 = _percentile(values, 0.50)
        p95 = _percentile(values, 0.95)
        report.append(
            f"{path} cold={cold[path]:.2f}ms warm_n={len(values)} warm_p50={p50:.2f}ms warm_p95={p95:.2f}ms"
        )
        html = path in html_paths
        assert cold[path] < (8 if html else 15), f"{path} cold {cold[path]:.1f}ms"
        assert p50 < (1 if html else 8), f"{path} warm p50 {p50:.1f}ms {values}"
        assert p95 < (4 if html else 15), f"{path} warm p95 {p95:.1f}ms {values}"
    print("remaining InstantSite shells TTFB cold/warm under scrape:\n  " + "\n  ".join(report))


def test_remaining_shells_instant_path_stays_fast_under_sqlite_exclusive_lock(tmp_path: Path):
    store = Store(tmp_path / "shell-lock.sqlite")
    store.upsert_catalog_listings_batch(
        [_hry_ttfb_listing(i) for i in range(40)], kind="seeded", commit_every=20, fast=True
    )
    locker = sqlite3.connect(store.path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("UPDATE meta SET value = value")
    hit = {"n": 0}

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        report = []
        for path in (
            "/kontakt",
            "/uspechy",
            "/uspechy/martina-tomas",
            "/obchodni-podminky",
            "/ochrana-soukromi",
            "/nastaveni-cookies",
            "/byt",
            "/dum",
            "/heslo",
            "/static/site/inquiries.js",
            "/static/site/legal.js",
            "/sw.js",
            "/manifest.webmanifest",
        ):
            t0 = time.perf_counter()
            status, _headers, body = await _asgi_get(app, path)
            ms = (time.perf_counter() - t0) * 1000
            report.append(f"{path} {ms:.2f}ms")
            assert status == 200, path
            assert body
            assert ms < 40, f"{path} under exclusive lock {ms:.1f}ms"
        print("remaining shells InstantSite under exclusive lock:\n  " + "\n  ".join(report))
        assert hit["n"] == 0

    try:
        asyncio.run(run())
    finally:
        locker.rollback()
        locker.close()


def test_rent_score_stays_fast_when_writer_locks_sqlite(tmp_path: Path):
    store = Store(tmp_path / "rent-lock.sqlite")
    locker = sqlite3.connect(store.path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN IMMEDIATE")
    locker.execute("UPDATE meta SET value = value")
    hit = {"n": 0}

    async def inner(scope, receive, send):
        hit["n"] += 1
        await asyncio.sleep(2)
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"slow"})

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        t0 = time.perf_counter()
        status, _headers, body = await _asgi_post(
            app,
            "/api/public/games/rent-score",
            {"name": "Eva", "guesses": [{"id": "seed-zizkov-2kk", "guess": 16500}]},
        )
        ms = (time.perf_counter() - t0) * 1000
        assert status == 200
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["score"] == 1000
        assert ms < 40, f"rent-score under lock {ms:.1f}ms"
        assert hit["n"] == 0

    try:
        asyncio.run(run())
    finally:
        locker.rollback()
        locker.close()


def _leftover_hub(store: Store):
    from app.monitor import Hub

    hub = object.__new__(Hub)
    hub.store = store
    hub.running = False
    hub.checking = False
    hub.last_error = None
    hub.catalog_running = False
    hub._status_cache = None
    hub._status_cache_at = 0.0
    return hub


def _seed_leftover_account(store: Store) -> None:
    store.set_meta(
        "account",
        json.dumps(
            {
                "email": "a@b.cz",
                "first": "Ada",
                "discord_channel_id": "1",
                "discord_channel_name": "alerts",
                "discord_webhook_url": "https://discord.com/api/webhooks/1/x",
            }
        ),
    )
    store.set_meta("auth_session", "tok-leftover")
    store.set_meta("billing", json.dumps({"plan": "start"}))


def test_leftover_json_stays_snappy_under_scrape_writer(tmp_path: Path, capsys):
    from app.account import discord_status, user_from_session
    from app.analytics import ingest
    from app.extension_score import extension_account, score_batch

    store = Store(tmp_path / "leftover-scrape.sqlite")
    seed = [_latency_listing(i) for i in range(60)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", commit_every=20, fast=True)
    with store.connect() as conn:
        conn.execute(
            """
            INSERT INTO monitors(id, name, search_url, webhook_url, template_id, enabled, seeded, created_at)
            VALUES ('m1', 'Praha', 'https://www.sreality.cz/hledani/pronajem/byty', '', 'default', 1, 1, ?)
            """,
            ("2020-01-01T00:00:00+00:00",),
        )
    _seed_leftover_account(store)
    hub = _leftover_hub(store)
    stop = threading.Event()

    def writer() -> None:
        n = 0
        while not stop.is_set():
            batch = [_latency_listing(300 + (n + k) % 40) for k in range(24)]
            store.upsert_catalog_listings_batch(batch, kind="refresh", commit_every=24, fast=True)
            n += 1

    class _Req:
        cookies = {}
        headers = {"user-agent": "pytest", "cf-ipcountry": "CZ"}
        client = type("C", (), {"host": "127.0.0.1"})()

    thread = threading.Thread(target=writer, name="rf-job-sim", daemon=True)
    thread.start()
    time.sleep(0.05)
    samples = {key: [] for key in ("status", "settings", "templates", "discord", "ext_me", "scores", "auth", "ingest")}
    first: dict[str, float] = {}
    try:
        for i in range(10):
            t0 = time.perf_counter()
            payload = hub.status()
            samples["status"].append((time.perf_counter() - t0) * 1000)
            assert payload.get("monitors")
            t0 = time.perf_counter()
            settings = store.app_settings()
            samples["settings"].append((time.perf_counter() - t0) * 1000)
            assert "notify" in settings
            t0 = time.perf_counter()
            assert store.list_templates()
            samples["templates"].append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter()
            disc = discord_status(store)
            samples["discord"].append((time.perf_counter() - t0) * 1000)
            assert disc.get("linked") is True
            t0 = time.perf_counter()
            account = extension_account(store)
            samples["ext_me"].append((time.perf_counter() - t0) * 1000)
            assert account.get("email") == "a@b.cz"
            t0 = time.perf_counter()
            scored = score_batch(store, ids=[str(seed[0].id)], urls=[seed[0].url], allow_network=False)
            samples["scores"].append((time.perf_counter() - t0) * 1000)
            assert scored["items"]
            t0 = time.perf_counter()
            user = user_from_session(store, "tok-leftover")
            samples["auth"].append((time.perf_counter() - t0) * 1000)
            assert user and user["email"] == "a@b.cz"
            t0 = time.perf_counter()
            ingest(store, _Req(), {"path": "/", "heartbeat": True, "tz": "Europe/Prague", "lang": "cs"})
            samples["ingest"].append((time.perf_counter() - t0) * 1000)
            if i == 0:
                for key, values in samples.items():
                    first[key] = values[-1]
    finally:
        stop.set()
        thread.join(timeout=8)

    def p95(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[max(0, int(round(0.95 * (len(ordered) - 1))))]

    assert p95(samples["status"]) < 50, samples["status"]
    assert p95(samples["settings"]) < 15, samples["settings"]
    assert p95(samples["templates"]) < 15, samples["templates"]
    assert p95(samples["discord"]) < 15, samples["discord"]
    assert p95(samples["ext_me"]) < 15, samples["ext_me"]
    assert p95(samples["scores"]) < 40, samples["scores"]
    assert p95(samples["auth"]) < 15, samples["auth"]
    assert samples["ingest"][0] < 120, samples["ingest"]
    assert samples["ingest"][1:] and p95(samples["ingest"][1:]) < 20, samples["ingest"]
    assert samples["status"][1:] and p95(samples["status"][1:]) < 8
    assert samples["settings"][1:] and p95(samples["settings"][1:]) < 8
    assert samples["scores"][1:] and p95(samples["scores"][1:]) < 8
    report = [
        f"{key} first={first[key]:.2f}ms p95={p95(samples[key]):.2f}ms"
        for key in samples
    ]
    print("leftover JSON TTFB under scrape:\n  " + "\n  ".join(report))


def test_status_serves_stale_when_writer_locks_sqlite(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "status-stale.sqlite")
    _seed_leftover_account(store)
    hub = _leftover_hub(store)
    first = hub.status()
    assert first.get("monitors") is not None

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "list_monitors", boom)
    hub._status_cache_at = 0.0
    t0 = time.perf_counter()
    again = hub.status()
    ms = (time.perf_counter() - t0) * 1000
    assert again.get("stale") is True
    assert again.get("running") == first.get("running")
    assert ms < 15, f"stale status {ms:.1f}ms"


def test_settings_and_discord_serve_stale_when_writer_locks(tmp_path: Path, monkeypatch):
    from app.account import discord_status

    store = Store(tmp_path / "settings-stale.sqlite")
    _seed_leftover_account(store)
    first_settings = store.app_settings()
    first_discord = discord_status(store)
    first_templates = store.list_templates()

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_app_settings_query", boom)
    monkeypatch.setattr(store, "_list_templates_query", boom)
    from app import account as user_account

    monkeypatch.setattr(user_account, "_discord_status_query", boom)
    for key, (at, payload) in list(store._hot_json_cache.items()):
        store._hot_json_cache[key] = (at - 5.0, payload)
    t0 = time.perf_counter()
    again_settings = store.app_settings()
    settings_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    again_discord = discord_status(store)
    discord_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    again_templates = store.list_templates()
    templates_ms = (time.perf_counter() - t0) * 1000
    assert again_settings.get("stale") is True
    assert again_settings["digest_hour"] == first_settings["digest_hour"]
    assert again_discord.get("stale") is True
    assert again_discord["linked"] == first_discord["linked"]
    assert [item["id"] for item in again_templates] == [item["id"] for item in first_templates]
    assert settings_ms < 15
    assert discord_ms < 15
    assert templates_ms < 15


def test_analytics_ingest_skips_when_writer_locks(tmp_path: Path):
    from app.analytics import ingest

    store = Store(tmp_path / "ingest-lock.sqlite")
    locker = sqlite3.connect(store.path, timeout=30)
    locker.execute("PRAGMA busy_timeout=30000")
    locker.execute("BEGIN EXCLUSIVE")
    locker.execute("UPDATE meta SET value = value")

    class _Req:
        cookies = {"rf_vid": "visitor-lock"}
        headers = {"user-agent": "pytest"}
        client = type("C", (), {"host": "127.0.0.1"})()

    try:
        t0 = time.perf_counter()
        visitor = ingest(store, _Req(), {"path": "/", "heartbeat": True})
        ms = (time.perf_counter() - t0) * 1000
    finally:
        locker.rollback()
        locker.close()
    assert visitor == "visitor-lock"
    assert ms < 120, f"ingest under exclusive lock {ms:.1f}ms"


def test_extension_scores_skip_network_and_serve_stale(tmp_path: Path, monkeypatch):
    from app.extension_score import score_batch

    store = Store(tmp_path / "ext-stale.sqlite")
    seed = [_latency_listing(1)]
    store.upsert_catalog_listings_batch(seed, kind="seeded", fast=True)
    first = score_batch(store, ids=[str(seed[0].id)], urls=[seed[0].url], allow_network=False)
    assert first["items"]
    from app import extension_score as ext_mod

    for key, (at, payload) in list(ext_mod._SCORE_CACHE.items()):
        ext_mod._SCORE_CACHE[key] = (at - 120.0, payload)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("app.extension_score._lookup_rows", boom)
    t0 = time.perf_counter()
    again = score_batch(store, ids=[str(seed[0].id)], urls=[seed[0].url], allow_network=True)
    ms = (time.perf_counter() - t0) * 1000
    assert again.get("stale") is True
    assert again["items"][str(seed[0].id)]["found"] is True
    assert ms < 15, f"stale scores {ms:.1f}ms"


def test_discord_link_read_does_not_write(tmp_path: Path, monkeypatch):
    from app.account import _link_payload

    store = Store(tmp_path / "discord-read.sqlite")
    store.set_meta("discord_link_code", json.dumps({"code": "ABC123", "expires_at": time.time() - 10}))

    def boom(*_a, **_k):
        raise AssertionError("expired link read must not write")

    monkeypatch.setattr(store, "set_meta", boom)
    assert _link_payload(store) is None


def test_get_meta_request_path_does_not_retry_sleep(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "meta-fast.sqlite")
    store.set_meta("account", '{"email":"a@b.cz"}')
    slept = {"n": 0}

    def no_sleep(_seconds):
        slept["n"] += 1

    monkeypatch.setattr(time, "sleep", no_sleep)

    def boom(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "read", boom)
    store._hot_json_put(store._hot_json_key("meta", extra="account"), '{"email":"a@b.cz"}')
    for key, (at, payload) in list(store._hot_json_cache.items()):
        store._hot_json_cache[key] = (at - 5.0, payload)
    t0 = time.perf_counter()
    value = store.get_meta("account")
    ms = (time.perf_counter() - t0) * 1000
    assert json.loads(value)["email"] == "a@b.cz"
    assert slept["n"] == 0
    assert ms < 15


def test_status_does_not_settle_billing_on_get(tmp_path: Path, monkeypatch):
    from app import billing as stripe_billing

    store = Store(tmp_path / "status-settle.sqlite")
    hub = _leftover_hub(store)
    called = {"n": 0}

    def boom(*_a, **_k):
        called["n"] += 1

    monkeypatch.setattr(stripe_billing, "settle_pending_if_due", boom)
    hub.status(fresh=True)
    assert called["n"] == 0

