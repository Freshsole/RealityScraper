from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

from app import config
from app.catalog_sync import extra_portal_recent_shards
from app.games import reset_pool_cache
from app.monitor import Hub
from app.scrape_worker import ScrapeWorker, main as scrape_worker_main
from app.site_pages import InstantSiteASGI
from app.sreality import Listing
from app.store import Store


def _asgi_get_setup(path: str):
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 80),
    }
    return scope, receive, send, sent


def _web_hub(tmp_path: Path, monkeypatch) -> Hub:
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    monkeypatch.setattr("app.config.DB_PATH", tmp_path / "web-role.sqlite")
    return Hub()


def _worker_hub(tmp_path: Path, monkeypatch) -> Hub:
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    monkeypatch.setattr("app.config.DB_PATH", tmp_path / "worker-role.sqlite")
    return Hub()


def _listing() -> Listing:
    return Listing(
        id=1,
        name="Byt 2+kk",
        price_czk=15000,
        price_label="15 000 Kč/měsíc",
        disposition="2+kk",
        area_m2=50,
        locality="Praha 1",
        url="https://www.sreality.cz/detail/pronajem/byt/2+kk/praha/1",
        image_url=None,
    )


def test_scrape_owned_here_false_on_web(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    assert config.scrape_owned_here() is False
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    assert config.scrape_owned_here() is True
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "all")
    assert config.scrape_owned_here() is True


def test_web_hub_does_not_construct_scrape_engines(tmp_path, monkeypatch):
    hub = _web_hub(tmp_path, monkeypatch)
    assert hub._discovery_engine is None
    assert hub._deep_engine is None
    assert hub._monitor_engine is None
    assert hub._scrape_limiter is None
    assert hub._owns_scrape() is False


def test_worker_hub_constructs_scrape_engines(tmp_path, monkeypatch):
    hub = _worker_hub(tmp_path, monkeypatch)
    assert hub._discovery_engine is not None
    assert hub._deep_engine is not None
    assert hub._monitor_engine is not None
    assert hub._scrape_limiter is not None
    assert hub._owns_scrape() is True


def test_web_role_start_does_not_schedule_scrape_loops(tmp_path, monkeypatch):
    async def _run() -> None:
        hub = _web_hub(tmp_path, monkeypatch)
        await hub.start()
        try:
            assert hub._recent_catalog_task is None
            assert hub._deep_catalog_task is None
            assert hub._ulov_hydrate_task is None
            assert hub._sold_task is None
            assert hub._catalog_task is None
            assert hub._coords_task is None
            assert hub._dedupe_task is None
            assert hub._task is not None
            assert hub._task.get_name() == "sreality-hub-web"
            assert hub._ping_task is not None
        finally:
            await hub.close()

    asyncio.run(_run())


def test_web_role_ticks_do_not_record_scrape_ticks(tmp_path, monkeypatch):
    async def _run() -> None:
        hub = _web_hub(tmp_path, monkeypatch)
        ticks: list[dict] = []
        monkeypatch.setattr(hub.store, "record_scrape_tick", lambda payload: ticks.append(payload))
        await hub._recent_catalog_tick()
        await hub._deep_catalog_tick()
        await hub._ulov_hydrate_loop()
        await hub._recent_catalog_loop()
        await hub._deep_catalog_loop()
        await hub._catalog_loop()
        await hub._sold_loop()
        await hub._dedupe_loop()
        await hub.backfill_missing_coords()
        assert await hub.run_due_scrape_schedules() == 0
        assert ticks == []
        assert hub.store.get_meta("scrape_worker_tick") in {None, ""}

    asyncio.run(_run())


def test_web_role_catalog_upsert_skips_long_writer_lock(tmp_path, monkeypatch):
    async def _run() -> None:
        hub = _web_hub(tmp_path, monkeypatch)
        acquired = {"n": 0}
        original = hub._catalog_write.acquire

        async def spy() -> None:
            acquired["n"] += 1
            return await original()

        hub._catalog_write.acquire = spy  # type: ignore[method-assign]
        stats = await hub._catalog_upsert([_listing()])
        written, note = await hub._try_catalog_upsert([_listing()])
        assert acquired["n"] == 0
        assert stats["n"] == 0
        assert stats["deferred_write"] == 1
        assert written is not None
        assert written["deferred_write"] == 1
        assert note == "write-deferred:web-role"

    asyncio.run(_run())


def test_web_role_queues_admin_scrape_jobs_without_event_loop(tmp_path, monkeypatch):
    hub = _web_hub(tmp_path, monkeypatch)
    queued = hub.start_catalog_sync(portals=["sreality"])
    assert queued.get("queued") is True
    assert hub.store.get_meta("catalog_sync_request")
    dedupe = hub.start_dedupe()
    assert dedupe.get("queued") is True
    assert hub.store.get_meta("dedupe_request") == "1"
    scan = hub.start_dedupe_scan()
    assert scan.get("queued") is True
    assert hub.store.get_meta("dedupe_scan_request") == "1"
    manual = hub.start_scrape_search_url("https://www.sreality.cz/hledani/pronajem/byty")
    assert manual.get("queued") is True
    assert hub.store.get_meta("scrape_url_request")


def test_web_role_background_writer_timeout_is_not_30s(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    store = Store(tmp_path / "web-writer.sqlite")
    result: dict[str, int] = {}

    def probe(label: str) -> None:
        conn = store.connect()
        result[label] = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        conn.close()

    probe("main")
    thread = threading.Thread(target=probe, args=("job",), name="rf-job-web")
    thread.start()
    thread.join()
    assert result["main"] == 80
    assert result["job"] == 800
    assert result["job"] < 30_000


def test_worker_role_background_writer_timeout_is_30s(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    store = Store(tmp_path / "worker-writer.sqlite")
    result: dict[str, int] = {}

    def probe() -> None:
        conn = store.connect()
        result["job"] = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        conn.close()

    thread = threading.Thread(target=probe, name="rf-job-worker")
    thread.start()
    thread.join()
    assert result["job"] == 30_000


def test_scrape_worker_run_refuses_web_role(tmp_path, monkeypatch):
    async def _run() -> None:
        monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
        monkeypatch.setattr("app.config.DB_PATH", tmp_path / "refuse.sqlite")
        worker = ScrapeWorker()
        await worker.run()
        assert worker.running is False
        assert worker.hub._recent_catalog_task is None
        assert worker.hub._deep_catalog_task is None
        assert worker.hub._ulov_hydrate_task is None

    asyncio.run(_run())


def test_scrape_worker_starts_discovery_deep_and_hydrate(tmp_path, monkeypatch):
    async def _run() -> None:
        monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
        monkeypatch.setattr("app.config.SCRAPE_ULOV_HYDRATE", False)
        monkeypatch.setattr("app.config.DB_PATH", tmp_path / "worker-run.sqlite")
        worker = ScrapeWorker()
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        try:
            assert worker.hub._discovery_engine is not None
            names = {
                t.get_name()
                for t in (
                    worker.hub._task,
                    worker.hub._recent_catalog_task,
                    worker.hub._deep_catalog_task,
                    worker.hub._ulov_hydrate_task,
                    worker.hub._sold_task,
                    worker.hub._coords_task,
                    worker.hub._dedupe_task,
                )
                if t is not None
            }
            assert "worker-new-discovery" in names
            assert "worker-rolling-deep" in names
            assert "worker-ulov-hydrate" in names
            assert "worker-monitor-priority" in names
        finally:
            worker.running = False
            await worker.hub.close()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


def test_scrape_worker_main_forces_worker_role(monkeypatch):
    monkeypatch.setenv("SCRAPE_ROLE", "web")
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    seen: list[str] = []

    def fake_run(coro):
        seen.append(config.SCRAPE_ROLE)
        coro.close()

    monkeypatch.setattr("app.scrape_worker.asyncio.run", fake_run)
    scrape_worker_main()
    assert seen == ["worker"]
    assert config.SCRAPE_ROLE == "worker"


def test_scrape_worker_consumes_web_queued_jobs():
    src = Path("app/scrape_worker.py").read_text()
    assert "dedupe_request" in src
    assert "dedupe_scan_request" in src
    assert "catalog_sync_request" in src
    assert "scrape_url_request" in src
    assert "worker-new-discovery" in src
    assert "worker-rolling-deep" in src
    assert "worker-ulov-hydrate" in src


def test_procfile_and_supervisord_document_role_split():
    proc = Path("Procfile").read_text()
    assert "SCRAPE_ROLE=web" in proc
    assert "SCRAPE_ROLE=worker" in proc
    assert "app.asgi:app" in proc
    assert "app.scrape_worker" in proc
    assert "NewDiscovery" in proc or "InstantSite" in proc
    conf = Path("supervisord.conf").read_text()
    assert 'SCRAPE_ROLE="web"' in conf
    assert 'SCRAPE_ROLE="worker"' in conf
    assert "app.asgi:app" in conf
    assert "app.scrape_worker" in conf
    asgi = Path("app/asgi.py").read_text()
    assert 'os.environ["SCRAPE_ROLE"] = "web"' in asgi
    assert "app.main import app" in asgi
    assert "scrape_engine" not in asgi


def test_instant_site_stays_off_scrape_engines():
    src = Path("app/site_pages.py").read_text()
    assert "scrape_engine" not in src
    assert "scrape_worker" not in src
    assert "ulov_hydrate" not in src
    assert "NewDiscovery" not in src
    assert "rolling_deep" not in src
    assert "scrape_proxy" not in src
    assert "browser_fetch" not in src


def test_instant_site_html_and_games_json_on_web_role(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    reset_pool_cache()
    store = Store(tmp_path / "instant-web.sqlite")

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"no-fastapi"})

    async def _run() -> None:
        app = InstantSiteASGI(inner, store=store)
        for path in (
            "/hry",
            "/hry/vyssi-nizsi",
            "/hry/najem",
            "/",
            "/kontakt",
            "/uspechy",
            "/byt",
            "/dum",
            "/obchodni-podminky",
            "/ochrana-soukromi",
            "/nastaveni-cookies",
            "/heslo",
            "/sw.js",
            "/manifest.webmanifest",
        ):
            scope, receive, send, sent = _asgi_get_setup(path)
            await app(scope, receive, send)
            start = next(item for item in sent if item["type"] == "http.response.start")
            assert start["status"] == 200, path
        for path in ("/api/public/games/higher-lower", "/api/public/games/rent-round"):
            scope, receive, send, sent = _asgi_get_setup(path)
            await app(scope, receive, send)
            start = next(item for item in sent if item["type"] == "http.response.start")
            body = b"".join(item.get("body") or b"" for item in sent if item["type"] == "http.response.body")
            assert start["status"] == 200, path
            payload = json.loads(body)
            assert payload.get("seeded") is True

    asyncio.run(_run())


def test_pozemky_shards_still_scheduled_on_worker():
    keys = {item["shard_key"] for item in extra_portal_recent_shards()}
    assert any(key.endswith(":pozemky") for key in keys)


def test_mm_proxy_plumbing_stays_off_web(monkeypatch):
    monkeypatch.setattr("app.config.SCRAPE_HTTP_PROXY", "http://user:pass@proxy.test:8080")
    monkeypatch.setattr("app.config.SCRAPE_HTTPS_PROXY", "")
    monkeypatch.setattr("app.config.SCRAPE_PROXY_PORTALS", frozenset({"mmreality"}))
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "web")
    from app.scrape_proxy import allowed, url_for

    assert allowed("mmreality") is False
    assert url_for("mmreality") == ""
    monkeypatch.setattr("app.config.SCRAPE_ROLE", "worker")
    assert allowed("mmreality") is True
    assert url_for("mmreality") == "http://user:pass@proxy.test:8080"
