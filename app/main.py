from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app import config
from app.backup import export_config, export_pack, export_sqlite, import_config, replace_sqlite
from app.monitor import Hub
from app.templates import VARIABLES, default_template_config, sample_vars
from app.updater import apply_update, version_info
from app import bezrealitky_url, url_builder
from app.filter_bridge import convert_search_url
from app.commute import route_times

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


@app.middleware("http")
async def no_store_ui(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in {"/", "/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"}:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


app.add_api_route("/", page, methods=["GET"], include_in_schema=False)
for _path in ("/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"):
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


@app.post("/api/digest/test")
async def digest_test(payload: dict[str, Any] | None = Body(None)) -> dict:
    try:
        return await hub.send_digest_test((payload or {}).get("webhook_url"))
    except Exception as exc:
        raise HTTPException(502, f"Test digestu selhal: {exc}") from exc


@app.get("/api/listings")
async def listings() -> dict:
    return {"items": hub.store.recent_notified(24)}


@app.get("/api/catalog")
async def catalog(
    portal: str = "",
    q: str = "",
    disposition: str = "",
    price_from: str = "",
    price_to: str = "",
    area_from: str = "",
    area_to: str = "",
    monitor_id: str = "",
    amenities: str = "",
    offer: str = "",
    district: str = "",
    estate: str = "",
    ownership: str = "",
    condition: str = "",
    building: str = "",
    equipped: str = "",
    roommate: str = "",
    pets: str = "",
    short_term: str = "",
    lat: str = "",
    lon: str = "",
    radius_m: str = "",
    status: str = "",
    discounted: str = "",
    sort: str = "newest",
    limit: int = 36,
    offset: int = 0,
) -> dict:
    return hub.store.catalog(
        {
            "portal": portal,
            "q": q,
            "disposition": disposition,
            "price_from": price_from,
            "price_to": price_to,
            "area_from": area_from,
            "area_to": area_to,
            "monitor_id": monitor_id,
            "amenities": amenities,
            "offer": offer,
            "district": district,
            "estate": estate,
            "ownership": ownership,
            "condition": condition,
            "building": building,
            "equipped": equipped,
            "roommate": roommate,
            "pets": pets,
            "short_term": short_term,
            "lat": lat,
            "lon": lon,
            "radius_m": radius_m,
            "status": status,
            "discounted": discounted,
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }
    )


@app.get("/api/catalog/pins")
async def catalog_pins(
    portal: str = "",
    q: str = "",
    disposition: str = "",
    price_from: str = "",
    price_to: str = "",
    area_from: str = "",
    area_to: str = "",
    monitor_id: str = "",
    amenities: str = "",
    offer: str = "",
    district: str = "",
    estate: str = "",
    ownership: str = "",
    condition: str = "",
    building: str = "",
    equipped: str = "",
    roommate: str = "",
    pets: str = "",
    short_term: str = "",
    lat: str = "",
    lon: str = "",
    radius_m: str = "",
    status: str = "",
    discounted: str = "",
) -> dict:
    return hub.store.catalog(
        {
            "portal": portal,
            "q": q,
            "disposition": disposition,
            "price_from": price_from,
            "price_to": price_to,
            "area_from": area_from,
            "area_to": area_to,
            "monitor_id": monitor_id,
            "amenities": amenities,
            "offer": offer,
            "district": district,
            "estate": estate,
            "ownership": ownership,
            "condition": condition,
            "building": building,
            "equipped": equipped,
            "roommate": roommate,
            "pets": pets,
            "short_term": short_term,
            "lat": lat,
            "lon": lon,
            "radius_m": radius_m,
            "status": status,
            "discounted": discounted,
            "pins_only": True,
        }
    )


@app.get("/api/catalog/item")
async def catalog_item(monitor_id: str, id: int) -> dict:
    item = hub.store.catalog_item(monitor_id, id)
    if not item:
        raise HTTPException(404, "Nabídka se nenašla")
    needs_detail = (not (item.get("description") or "").strip()) or len(item.get("photos") or []) < 2
    if needs_detail and item.get("search_url"):
        listing = Listing(
            id=int(item["id"]),
            name=item.get("name") or "",
            price_czk=item.get("price_czk"),
            price_label=item.get("price_label") or "",
            disposition=item.get("disposition") or "",
            area_m2=item.get("area_m2"),
            locality=item.get("locality") or "",
            url=item.get("url") or "",
            image_url=item.get("image_url"),
            photos=item.get("photos") or [],
            lat=item.get("lat"),
            lon=item.get("lon"),
            description=item.get("description"),
            extras=item.get("extras") or {},
        )
        try:
            listing = await hub.client_for(item["search_url"]).fetch_detail(listing)
            hub.store.save_listing_enrichment(monitor_id, listing)
            item = hub.store.catalog_item(monitor_id, id) or item
        except ListingGone:
            await hub.notify_sold(hub.store.get_monitor(monitor_id), id)
            item = hub.store.catalog_item(monitor_id, id) or item
        except Exception:
            pass
    return item


@app.post("/api/catalog/user")
async def save_listing_user(payload: dict[str, Any]) -> dict:
    try:
        return hub.store.set_listing_user(payload.get("url") or "", payload.get("status"), payload.get("note"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/settings")
async def get_settings() -> dict:
    return hub.store.app_settings()


@app.post("/api/settings")
async def save_settings(payload: dict[str, Any]) -> dict:
    try:
        return hub.store.save_app_settings(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/monitors")
async def list_monitors() -> dict:
    return {"items": hub.store.list_monitors()}


@app.post("/api/monitors")
async def save_monitor(payload: dict[str, Any]) -> dict:
    if not (payload.get("search_url") or "").strip():
        raise HTTPException(400, "Chybí search_url")
    return hub.store.save_monitor(payload)


@app.get("/api/monitors/{monitor_id}/preview")
async def preview_monitor(monitor_id: str) -> dict:
    monitor = hub.store.get_monitor(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor neexistuje")
    return {"items": hub.store.monitor_preview(monitor_id)}


@app.get("/api/monitors/{monitor_id}/convert")
async def convert_monitor(monitor_id: str) -> dict:
    monitor = hub.store.get_monitor(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor neexistuje")
    converted = convert_search_url(monitor["search_url"])
    base_name = monitor["name"].replace(" · Sreality", "").replace(" · Bezrealitky", "").strip()
    return {
        **converted,
        "suggested_name": f"{base_name} · {converted['target']}",
    }


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


def _filter_mod(source: str | None = None, url: str = ""):
    raw = (source or "").lower()
    if raw == "bezrealitky" or "bezrealitky.cz" in (url or "").lower():
        return bezrealitky_url
    return url_builder


@app.get("/api/filters/catalog")
async def filter_catalog() -> dict:
    return {
        "catalog": url_builder.catalog(),
        "defaults": url_builder.default_filters(),
        "sample": url_builder.sample_filters(),
        "sources": {
            "sreality": {
                "catalog": url_builder.catalog(),
                "defaults": url_builder.default_filters(),
                "sample": url_builder.sample_filters(),
            },
            "bezrealitky": {
                "catalog": bezrealitky_url.catalog(),
                "defaults": bezrealitky_url.default_filters(),
                "sample": bezrealitky_url.sample_filters(),
            },
        },
    }


@app.post("/api/filters/build")
async def filter_build(payload: dict[str, Any]) -> dict:
    filters = payload.get("filters") or {}
    mod = _filter_mod(filters.get("source"))
    built = mod.build_url(filters or mod.default_filters())
    return {"url": built, "filters": mod.parse_url(built)}


@app.post("/api/filters/parse")
async def filter_parse(payload: dict[str, Any]) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Chybí url")
    mod = _filter_mod(url=url)
    return {"url": url, "filters": mod.parse_url(url)}


@app.get("/api/filters/locality")
async def filter_locality(q: str = Query("", min_length=2)) -> dict:
    import httpx

    query = q.strip()
    if len(query) < 2:
        return {"items": []}
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "RealityScraper/1.2"}) as client:
            response = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": query,
                    "format": "json",
                    "addressdetails": 1,
                    "limit": 8,
                    "countrycodes": "cz,sk",
                },
            )
            response.raise_for_status()
            rows = response.json()
    except Exception as exc:
        raise HTTPException(502, f"Hledání lokality selhalo: {exc}") from exc
    items = []
    seen: set[str] = set()
    for row in rows:
        osm_type = row.get("osm_type")
        osm_id = row.get("osm_id")
        if osm_type != "relation" or not osm_id:
            continue
        ident = f"R{osm_id}"
        if ident in seen:
            continue
        seen.add(ident)
        items.append({"id": ident, "label": row.get("display_name") or ident})
    return {"items": items}


def _photon_label(props: dict[str, Any]) -> str:
    street = (props.get("street") or props.get("name") or "").strip()
    number = (props.get("housenumber") or "").strip()
    district = (props.get("district") or props.get("locality") or "").strip()
    city = (props.get("city") or props.get("town") or props.get("village") or "").strip()
    line = f"{street} {number}".strip()
    parts = [part for part in (line, district if district != city else "", city) if part]
    return ", ".join(parts) or (props.get("name") or "").strip()


def _nominatim_label(row: dict[str, Any]) -> str:
    addr = row.get("address") or {}
    street = (addr.get("road") or addr.get("pedestrian") or addr.get("footway") or "").strip()
    number = (addr.get("house_number") or "").strip()
    district = (addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter") or "").strip()
    city = (addr.get("city") or addr.get("town") or addr.get("village") or "").strip()
    line = f"{street} {number}".strip()
    parts = [part for part in (line, district if district != city else "", city) if part]
    return ", ".join(parts) or (row.get("display_name") or "").split(",")[0].strip()


def _add_geocode_item(items: list[dict[str, Any]], seen: set[str], label: str, lat: float, lon: float) -> None:
    label = (label or "").strip()
    if not label:
        return
    key = f"{label.casefold()}|{round(lat, 5)}|{round(lon, 5)}"
    if key in seen:
        return
    seen.add(key)
    items.append({"label": label, "lat": lat, "lon": lon})


@app.get("/api/geocode")
async def geocode(q: str = Query("", min_length=2)) -> dict:
    import httpx

    query = q.strip()
    if len(query) < 2:
        return {"items": []}
    headers = {"User-Agent": "RealityScraper/1.2"}
    queries = [query]
    if "praha" not in query.casefold() and "prague" not in query.casefold():
        queries.append(f"{query} Praha")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        for term in queries:
            try:
                response = await client.get(
                    "https://photon.komoot.io/api/",
                    params={"q": term, "limit": 8, "lat": 50.087, "lon": 14.421},
                )
                response.raise_for_status()
                for feature in (response.json() or {}).get("features") or []:
                    props = feature.get("properties") or {}
                    if (props.get("countrycode") or "").upper() not in {"CZ", "SK", ""}:
                        continue
                    coords = (feature.get("geometry") or {}).get("coordinates") or []
                    if len(coords) < 2:
                        continue
                    _add_geocode_item(items, seen, _photon_label(props), float(coords[1]), float(coords[0]))
            except Exception:
                pass
            try:
                response = await client.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={
                        "q": term,
                        "format": "json",
                        "addressdetails": 1,
                        "limit": 8,
                        "countrycodes": "cz,sk",
                        "viewbox": "14.22,50.18,14.72,49.94",
                    },
                )
                response.raise_for_status()
                for row in response.json() or []:
                    try:
                        lat = float(row.get("lat"))
                        lon = float(row.get("lon"))
                    except (TypeError, ValueError):
                        continue
                    _add_geocode_item(items, seen, _nominatim_label(row), lat, lon)
            except Exception:
                pass
            if len(items) >= 8:
                break
    return {"items": items[:8]}


@app.get("/api/commute")
async def commute_times(
    from_lat: float = Query(...),
    from_lon: float = Query(...),
    to_lat: float = Query(...),
    to_lon: float = Query(...),
    from_address: str = Query(""),
    to_address: str = Query(""),
) -> dict:
    if not (-90 <= from_lat <= 90 and -90 <= to_lat <= 90 and -180 <= from_lon <= 180 and -180 <= to_lon <= 180):
        raise HTTPException(400, "Neplatné souřadnice")
    return await route_times(
        from_lat,
        from_lon,
        to_lat,
        to_lon,
        from_address=from_address,
        to_address=to_address,
    )


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
