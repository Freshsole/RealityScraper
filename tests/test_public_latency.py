import asyncio
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.site_pages import InstantSiteASGI, site_asset, site_body
from app.store import Store, catalog_item_needs_live_fetch


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


async def _asgi_get(app, path: str, method: str = "GET") -> tuple[int, dict[bytes, bytes], bytes]:
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
            "query_string": b"",
            "headers": [],
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
        for path, needle in (
            ("/static/site/games.css", b"@font-face"),
            ("/static/site/games.js", b"[^\\d]"),
            ("/static/site/fonts/archivo-black-latin.woff2", b"wOF2"),
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
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 250
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
    try:
        for _ in range(10):
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


def test_catalog_serves_stale_json_when_writer_locks_sqlite(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "stale.sqlite")
    store.upsert_catalog_listings_batch([_latency_listing(1)], kind="seeded", fast=True)
    filters = {"q": "Praha", "limit": 12, "include_pins": "0"}
    first = store.catalog(filters)
    assert first["items"]

    def boom(_filters):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_catalog_query", boom)
    t0 = time.perf_counter()
    again = store.catalog(filters)
    ms = (time.perf_counter() - t0) * 1000
    assert again.get("stale") is True
    assert again["items"] == first["items"]
    assert ms < 15, f"stale catalog {ms:.1f}ms"


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

    html_paths = ("/", "/hry", "/hry/vyssi-nizsi", "/hry/najem")
    asset_paths = (
        "/static/site/games.css",
        "/static/site/games.js",
        "/static/site/fonts/archivo-black-latin.woff2",
    )
    json_paths = ("/api/public/games/higher-lower", "/api/public/games/rent-round")
    samples = {path: [] for path in (*html_paths, *asset_paths, *json_paths, "/api/public/games/rent-score")}

    async def run() -> None:
        app = InstantSiteASGI(inner, store=store)
        for _ in range(24):
            for path in html_paths:
                t0 = time.perf_counter()
                status, _headers, body = await _asgi_get(app, path)
                samples[path].append((time.perf_counter() - t0) * 1000)
                assert status == 200, path
                assert body
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
            *[_asgi_get(app, path) for path in (*html_paths, *json_paths) * 4]
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
        p50 = _percentile(values, 0.50)
        p95 = _percentile(values, 0.95)
        report.append(f"{path} n={len(values)} p50={p50:.2f}ms p95={p95:.2f}ms")
        html = path in html_paths or path in asset_paths
        assert p50 < (8 if html else 15), f"{path} p50 {p50:.1f}ms {values}"
        assert p95 < (25 if html else 40), f"{path} p95 {p95:.1f}ms {values}"
    print("game TTFB under scrape:\n  " + "\n  ".join(report))


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

