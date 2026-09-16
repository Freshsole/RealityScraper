import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.site_pages import InstantSiteASGI, site_body
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
