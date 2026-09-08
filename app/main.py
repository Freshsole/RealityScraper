from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app import config
from app.backup import export_config, export_pack, export_sqlite, import_config, replace_sqlite
from app.monitor import Hub
from app.templates import VARIABLES, default_template_config, sample_vars
from app.updater import apply_update, version_info
from app.url_builder import build_url, catalog, default_filters, parse_url, sample_filters

hub = Hub()
monitor = hub


async def maybe_auto_update() -> None:
    if not config.AUTO_UPDATE or sys.platform != "win32":
        return
    try:
        info = await version_info()
        if info.get("update_available") and info.get("url"):
            print(f"Aktualizuji na {info['latest']}…")
            await apply_update(info["url"])
    except Exception as exc:
        print(f"Aktualizace se nepodařila: {exc}")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not config.DISCORD_WEBHOOK_URL:
        hub.last_error = "Chybí DISCORD_WEBHOOK_URL v .env"
    await hub.start()
    asyncio.create_task(maybe_auto_update())
    try:
        yield
    finally:
        await hub.close()


app = FastAPI(title="Sreality Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


def page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html")


app.add_api_route("/", page, methods=["GET"], include_in_schema=False)
for _path in ("/prehled", "/monitory", "/filtry", "/zprava", "/nastaveni"):
    app.add_api_route(_path, page, methods=["GET"], include_in_schema=False)


@app.get("/api/status")
async def status() -> dict:
    return hub.status()


@app.get("/api/version")
async def version() -> dict:
    return await version_info()


@app.post("/api/update")
async def install_update() -> dict:
    info = await version_info()
    if not info.get("update_available"):
        raise HTTPException(400, "Už máš nejnovější verzi")
    if not info.get("can_install"):
        raise HTTPException(400, "Aktualizace se instaluje jen z Windows balíčku")
    if not info.get("url"):
        raise HTTPException(400, "Chybí odkaz na novou verzi")
    try:
        await apply_update(info["url"])
    except Exception as exc:
        raise HTTPException(502, f"Aktualizace selhala: {exc}") from exc
    return {"ok": True, "restarting": True, "version": info.get("latest")}


@app.post("/api/monitor/start")
async def start_monitor() -> dict:
    await hub.start()
    return hub.status()


@app.post("/api/monitor/stop")
async def stop_monitor() -> dict:
    await hub.stop()
    return hub.status()


@app.post("/api/monitor/check")
async def check_now(payload: dict[str, Any] | None = Body(None)) -> dict:
    monitor_id = (payload or {}).get("monitor_id")
    result = await hub.check_once(monitor_id)
    return {"result": result, "status": hub.status()}


@app.post("/api/discord/test")
async def discord_test(payload: dict[str, Any] | None = Body(None)) -> dict:
    try:
        return await hub.send_test((payload or {}).get("monitor_id"))
    except Exception as exc:
        raise HTTPException(502, f"Discord test selhal: {exc}") from exc


@app.get("/api/listings")
async def listings() -> dict:
    return {"items": hub.store.recent_notified(24)}


@app.get("/api/monitors")
async def list_monitors() -> dict:
    return {"items": hub.store.list_monitors()}


@app.post("/api/monitors")
async def save_monitor(payload: dict[str, Any]) -> dict:
    if not (payload.get("search_url") or "").strip():
        raise HTTPException(400, "Chybí search_url")
    return hub.store.save_monitor(payload)


@app.delete("/api/monitors/{monitor_id}")
async def delete_monitor(monitor_id: str) -> dict:
    try:
        hub.store.delete_monitor(monitor_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": hub.status()}


@app.get("/api/templates")
async def list_templates() -> dict:
    return {"items": hub.store.list_templates(), "variables": VARIABLES, "sample": sample_vars()}


@app.post("/api/templates")
async def save_template(payload: dict[str, Any]) -> dict:
    if not payload.get("config"):
        payload["config"] = default_template_config()
    return hub.store.save_template(payload)


@app.delete("/api/templates/{template_id}")
async def delete_template(template_id: str) -> dict:
    try:
        hub.store.delete_template(template_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


@app.get("/api/filters/catalog")
async def filter_catalog() -> dict:
    return {
        "catalog": catalog(),
        "defaults": default_filters(),
        "sample": sample_filters(),
    }


@app.post("/api/filters/build")
async def filter_build(payload: dict[str, Any]) -> dict:
    url = build_url(payload.get("filters") or default_filters())
    return {"url": url, "filters": parse_url(url)}


@app.post("/api/filters/parse")
async def filter_parse(payload: dict[str, Any]) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Chybí url")
    return {"url": url, "filters": parse_url(url)}


@app.get("/api/backup/json")
async def backup_json() -> Response:
    payload = json.dumps(export_config(hub.store), ensure_ascii=False, indent=2)
    return Response(
        payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sreality-config.json"'},
    )


@app.get("/api/backup/sqlite")
async def backup_sqlite() -> Response:
    return Response(
        export_sqlite(hub.store),
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": 'attachment; filename="monitor.sqlite"'},
    )


@app.get("/api/backup/pack")
async def backup_pack() -> Response:
    return Response(
        export_pack(hub.store),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sreality-backup.zip"'},
    )


@app.post("/api/backup/import")
async def backup_import(file: UploadFile = File(...)) -> dict:
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Prázdný soubor")
    name = (file.filename or "").lower()
    was_running = hub.running
    await hub.stop()
    while hub.checking:
        await asyncio.sleep(0.05)
    try:
        if name.endswith(".zip") or raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = archive.namelist()
                sqlite_name = next((item for item in names if item.endswith((".sqlite", ".db"))), None)
                json_name = next((item for item in names if item.endswith(".json")), None)
                if sqlite_name:
                    hub.store = replace_sqlite(hub.store, archive.read(sqlite_name))
                if json_name:
                    payload = json.loads(archive.read(json_name))
                    import_config(hub.store, payload, reset_seeded=sqlite_name is None)
                if not sqlite_name and not json_name:
                    raise HTTPException(400, "V zipu chybí config.json nebo monitor.sqlite")
        elif name.endswith(".json") or raw[:1] in {b"{", b"["}:
            import_config(hub.store, json.loads(raw.decode("utf-8")), reset_seeded=True)
        elif name.endswith((".sqlite", ".db")) or raw[:16] == b"SQLite format 3\x00":
            hub.store = replace_sqlite(hub.store, raw)
        else:
            raise HTTPException(400, "Nahraj .zip, .json nebo .sqlite")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if was_running:
            await hub.start()
    return {"ok": True, "status": hub.status()}
