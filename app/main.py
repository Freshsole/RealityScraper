from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from contextlib import asynccontextmanager
import time
from typing import Any

from fastapi import Body, Cookie, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app import config
from app.backup import export_config, export_pack, export_sqlite, import_config, replace_sqlite
from app.monitor import Hub
from app.templates import VARIABLES, default_template_config, sample_vars
from app.updater import apply_update, version_info
from app import bezrealitky_url, localities, places, url_builder
from app.filter_bridge import convert_search_url
from app.catalog_sync import monitor_search_targets
from app.commute import route_times
from app import billing as stripe_billing
from app import account as user_account
from app import push as web_push
from app import email_notify as mail_notify
from app import whatsapp as wa_notify
from app.sreality import ListingGone
from app.store import _listing_from_catalog_dict

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
    print(f"SQLite: {config.DB_PATH} persistent={config.PERSISTENT_STORAGE}", flush=True)
    await hub.start()
    if config.ON_RAILWAY and not config.PERSISTENT_STORAGE:
        hub.last_error = (
            "Databáze není na Railway Volume. Po každém deployi se smaže účet. "
            "V Railway → Volume přidej disk a připoj ho na /data."
        )
    elif not config.DISCORD_BOT_TOKEN and not config.DISCORD_WEBHOOK_URL:
        hub.last_error = "Chybí Discord bot (DISCORD_BOT_TOKEN, DISCORD_GUILD_ID) nebo DISCORD_WEBHOOK_URL"
    asyncio.create_task(maybe_auto_update())
    try:
        yield
    finally:
        await hub.close()


app = FastAPI(title="Sreality Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


def _agent_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        with open("/Users/jirka/Desktop/Folders/RealityScraper/.cursor/debug-c31723.log", "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "sessionId": "c31723",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                        "runId": "post-fix",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


@app.middleware("http")
async def no_store_ui(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    # #region agent log
    if path.startswith("/api/"):
        _agent_log(
            "E",
            "main.py:middleware",
            "api request",
            {"path": path, "ms": round((time.perf_counter() - started) * 1000, 1), "status": response.status_code},
        )
    # #endregion
    if (
        path.startswith("/static/")
        or path.startswith("/nastaveni")
        or path in {
        "/",
        "/kontakt",
        "/prihlaseni",
        "/registrace",
        "/heslo",
        "/prehled",
        "/nabidka",
        "/monitory",
        "/filtry",
        "/zprava",
        "/nastaveni",
        "/sw.js",
        "/manifest.webmanifest",
    }
    ):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _is_app_page(path: str) -> bool:
    return path in {"/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"} or path.startswith("/nastaveni/")


@app.middleware("http")
async def require_account(request: Request, call_next):
    if request.method == "GET" and _is_app_page(request.url.path):
        user = user_account.user_from_session(hub.store, request.cookies.get(user_account.SESSION_COOKIE))
        if not user:
            return RedirectResponse("/prihlaseni", status_code=303)
    return await call_next(request)


def page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


def landing() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


def contact() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "kontakt.html", headers={"Cache-Control": "no-store, max-age=0"})


def auth_login() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "prihlaseni.html", headers={"Cache-Control": "no-store, max-age=0"})


def auth_register() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "registrace.html", headers={"Cache-Control": "no-store, max-age=0"})


def auth_forgot() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "heslo.html", headers={"Cache-Control": "no-store, max-age=0"})


app.add_api_route("/", landing, methods=["GET"], include_in_schema=False)
app.add_api_route("/kontakt", contact, methods=["GET"], include_in_schema=False)
app.add_api_route("/prihlaseni", auth_login, methods=["GET"], include_in_schema=False)
app.add_api_route("/registrace", auth_register, methods=["GET"], include_in_schema=False)
app.add_api_route("/heslo", auth_forgot, methods=["GET"], include_in_schema=False)
for _path in ("/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"):
    app.add_api_route(_path, page, methods=["GET"], include_in_schema=False)
app.add_api_route("/nastaveni/{rest:path}", page, methods=["GET"], include_in_schema=False)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        user_account.SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def _current_user(session: str | None) -> dict[str, Any]:
    user = user_account.user_from_session(hub.store, session)
    if not user:
        raise HTTPException(401, "Nejste přihlášeni")
    return user


@app.post("/api/auth/register")
async def auth_register_api(payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        user, token = user_account.register(
            hub.store,
            str(body.get("name") or ""),
            str(body.get("email") or ""),
            str(body.get("password") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse(user)
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/login")
async def auth_login_api(payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        user, token = user_account.login(hub.store, str(body.get("email") or ""), str(body.get("password") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse(user)
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/logout")
async def auth_logout_api() -> dict:
    user_account.clear_session(hub.store)
    response = JSONResponse({"ok": True})
    response.delete_cookie(user_account.SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    return _current_user(realitify_session)


@app.post("/api/auth/profile")
async def auth_profile(payload: dict[str, Any] | None = Body(None), realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    body = payload or {}
    try:
        return user_account.update_profile(
            hub.store,
            str(body.get("first") or ""),
            str(body.get("last") or ""),
            str(body.get("phone") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/auth/password")
async def auth_password(payload: dict[str, Any] | None = Body(None), realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    body = payload or {}
    try:
        token = user_account.change_password(hub.store, str(body.get("current") or ""), str(body.get("new") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, token)
    return response


@app.get("/api/status")
async def status() -> dict:
    return await asyncio.to_thread(hub.status)


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
    return hub.status(fresh=True)


@app.post("/api/monitor/stop")
async def stop_monitor() -> dict:
    await hub.stop()
    return hub.status(fresh=True)


@app.post("/api/monitor/check")
async def check_now(payload: dict[str, Any] | None = Body(None)) -> dict:
    monitor_id = (payload or {}).get("monitor_id")
    result = await hub.check_once(monitor_id)
    return {"result": result, "status": hub.status(fresh=True)}


@app.post("/api/catalog/sync")
async def catalog_sync_now() -> dict:
    return {"result": hub.start_catalog_sync(), "status": hub.status(fresh=True)}


@app.post("/api/discord/test")
async def discord_test(payload: dict[str, Any] | None = Body(None)) -> dict:
    try:
        return await hub.send_test((payload or {}).get("monitor_id"))
    except Exception as exc:
        raise HTTPException(502, f"Discord test selhal: {exc}") from exc


@app.get("/api/discord/status")
async def discord_link_status(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    return user_account.discord_status(hub.store)


@app.post("/api/discord/link-code")
async def discord_link_code(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    try:
        return user_account.create_discord_link_code(hub.store)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/discord/unlink")
async def discord_unlink(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    return user_account.unlink_discord(hub.store)


@app.post("/api/email/test")
async def email_test(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    try:
        sent = await mail_notify.notify_listing(
            hub.store,
            {"name": "Testovací zpráva z Realitify"},
            "test",
            ignore_quiet=True,
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if not sent:
        raise HTTPException(400, "Zapněte e-mail a na serveru nastavte SMTP_HOST a SMTP_FROM")
    return {"ok": True, "email": mail_notify.account_email(hub.store)}


@app.get("/api/whatsapp/status")
async def whatsapp_status(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    return wa_notify.status(hub.store)


@app.post("/api/whatsapp/phone")
async def whatsapp_phone(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _current_user(realitify_session)
    if not wa_notify.plan_allows(hub.store):
        raise HTTPException(403, "WhatsApp notifikace jsou jen v tarifu PRO")
    try:
        wa_notify.save_phone(hub.store, str((payload or {}).get("phone") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return wa_notify.status(hub.store)


@app.post("/api/whatsapp/test")
async def whatsapp_test(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _current_user(realitify_session)
    if not wa_notify.plan_allows(hub.store):
        raise HTTPException(403, "WhatsApp notifikace jsou jen v tarifu PRO")
    try:
        sent = await wa_notify.notify_listing(
            hub.store,
            {"name": "Testovací zpráva z Realitify"},
            "test",
            ignore_quiet=True,
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if not sent:
        raise HTTPException(400, "Zapněte WhatsApp, vyplňte číslo a na serveru nastavte WHATSAPP_TOKEN")
    return {"ok": True, **wa_notify.status(hub.store)}


@app.get("/api/whatsapp/webhook")
async def whatsapp_webhook_verify(request: Request) -> Response:
    mode = request.query_params.get("hub.mode") or ""
    token = request.query_params.get("hub.verify_token") or ""
    challenge = request.query_params.get("hub.challenge") or ""
    if mode == "subscribe" and config.WHATSAPP_VERIFY_TOKEN and token == config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    raise HTTPException(403, "WhatsApp verify selhal")


@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook_event(payload: dict[str, Any] | None = Body(None)) -> dict:
    return {"ok": True}


@app.post("/api/digest/test")
async def digest_test(payload: dict[str, Any] | None = Body(None)) -> dict:
    try:
        return await hub.send_digest_test((payload or {}).get("webhook_url"))
    except Exception as exc:
        raise HTTPException(502, f"Test digestu selhal: {exc}") from exc


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(
        config.WEB_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store, max-age=0", "Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.webmanifest")
def web_manifest() -> FileResponse:
    return FileResponse(
        config.WEB_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/push/vapid")
def push_vapid() -> dict:
    return {
        "publicKey": web_push.public_key(),
        "supported": True,
        "devices": hub.store.push_subscription_count(),
        "enabled": bool(hub.store.notify_prefs().get("push")),
    }


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        hub.store.save_push_subscription(body, request.headers.get("user-agent") or "")
        prefs = hub.store.notify_prefs()
        prefs["push"] = True
        hub.store.save_notify_prefs(prefs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "devices": hub.store.push_subscription_count(), "notify": hub.store.notify_prefs()}


@app.post("/api/push/unsubscribe")
async def push_unsubscribe(payload: dict[str, Any] | None = Body(None)) -> dict:
    hub.store.delete_push_subscription(str((payload or {}).get("endpoint") or ""))
    return {"ok": True, "devices": hub.store.push_subscription_count()}


@app.post("/api/push/test")
async def push_test() -> dict:
    sent = await asyncio.to_thread(web_push.notify_test, hub.store)
    if not sent:
        raise HTTPException(400, "Na tomto zařízení ještě není aktivní odběr push notifikací")
    return {"ok": True, "sent": sent}


@app.get("/api/public/gone-fast")
async def public_gone_fast() -> dict:
    return {"items": hub.store.public_gone_fast_rentals(days=3, limit=4)}


@app.get("/api/listings")
async def listings() -> dict:
    return {"items": hub.store.recent_notified(24)}


def _catalog_filters(
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
    hits: str = "",
    places: str = "",
    sort: str = "newest",
    limit: int = 36,
    offset: int = 0,
    pins_only: bool = False,
) -> dict[str, Any]:
    payload = {
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
        "hits": hits,
        "places": places,
        "sort": sort,
        "limit": limit,
        "offset": offset,
    }
    if pins_only:
        payload["pins_only"] = True
    return payload


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
    hits: str = "",
    places: str = "",
    sort: str = "newest",
    limit: int = 36,
    offset: int = 0,
) -> dict:
    payload = await places.attach_geoms(
        _catalog_filters(
            portal=portal,
            q=q,
            disposition=disposition,
            price_from=price_from,
            price_to=price_to,
            area_from=area_from,
            area_to=area_to,
            monitor_id=monitor_id,
            amenities=amenities,
            offer=offer,
            district=district,
            estate=estate,
            ownership=ownership,
            condition=condition,
            building=building,
            equipped=equipped,
            roommate=roommate,
            pets=pets,
            short_term=short_term,
            lat=lat,
            lon=lon,
            radius_m=radius_m,
            status=status,
            discounted=discounted,
            hits=hits,
            places=places,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    )
    return await asyncio.to_thread(hub.store.catalog, payload)


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
    hits: str = "",
    places: str = "",
) -> dict:
    payload = await places.attach_geoms(
        _catalog_filters(
            portal=portal,
            q=q,
            disposition=disposition,
            price_from=price_from,
            price_to=price_to,
            area_from=area_from,
            area_to=area_to,
            monitor_id=monitor_id,
            amenities=amenities,
            offer=offer,
            district=district,
            estate=estate,
            ownership=ownership,
            condition=condition,
            building=building,
            equipped=equipped,
            roommate=roommate,
            pets=pets,
            short_term=short_term,
            lat=lat,
            lon=lon,
            radius_m=radius_m,
            status=status,
            discounted=discounted,
            hits=hits,
            places=places,
            pins_only=True,
        )
    )
    return await asyncio.to_thread(hub.store.catalog, payload)


@app.get("/api/catalog/item")
async def catalog_item(monitor_id: str, id: int) -> dict:
    item = hub.store.catalog_item(monitor_id, id)
    if not item:
        raise HTTPException(404, "Nabídka se nenašla")
    if item.get("search_url"):
        try:
            listing = _listing_from_catalog_dict(item)
            listing.photos = []
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
    try:
        return hub.store.save_monitor(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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
    return {"ok": True, "status": hub.status(fresh=True)}


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
    started = time.perf_counter()
    payload = {
        "locality_map": localities.catalog_map(),
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
    # #region agent log
    _agent_log("C", "main.py:filter_catalog", "catalog built", {"ms": round((time.perf_counter() - started) * 1000, 1)})
    # #endregion
    return payload


@app.post("/api/filters/build")
async def filter_build(payload: dict[str, Any]) -> dict:
    filters = payload.get("filters") or {}
    mod = _filter_mod(filters.get("source"))
    if not filters.get("source"):
        filters = {**filters, "source": "bezrealitky" if mod is bezrealitky_url else "sreality"}
    filters = localities.normalize_filters(filters)
    built = mod.build_url(filters or mod.default_filters())
    portals = str(payload.get("portals") or "all")
    targets = monitor_search_targets({"search_url": built, "portals": portals})
    return {"url": built, "filters": mod.parse_url(built), "targets": targets}


@app.post("/api/filters/parse")
async def filter_parse(payload: dict[str, Any]) -> dict:
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Chybí url")
    mod = _filter_mod(url=url)
    return {"url": url, "filters": mod.parse_url(url)}


@app.get("/api/places/search")
async def places_search(q: str = Query("", min_length=2)) -> dict:
    return {"items": await places.search_places(q)}


@app.get("/api/places/geometry")
async def places_geometry(ids: str = "") -> dict:
    ident = [item.strip() for item in ids.split(",") if item.strip()]
    try:
        items = await asyncio.wait_for(places.geometries(ident), 8.0)
    except Exception:
        items = await places.geometries(ident, network=False)
    return {"items": places.public_geoms(items)}


@app.get("/api/filters/locality")
async def filter_locality(q: str = Query("", min_length=2)) -> dict:
    import httpx

    query = q.strip()
    if len(query) < 2:
        return {"items": []}
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    needle = query.casefold()
    for ident, label in bezrealitky_url.DISTRICTS:
        if needle not in label.casefold():
            continue
        seen.add(ident)
        items.append(localities.locality_item(ident, label))
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
    except Exception:
        return {"items": items}
    for row in rows:
        osm_type = row.get("osm_type")
        osm_id = row.get("osm_id")
        if osm_type != "relation" or not osm_id:
            continue
        ident = f"R{osm_id}"
        if ident in seen:
            continue
        seen.add(ident)
        items.append(
            localities.locality_item(
                ident,
                row.get("display_name") or ident,
                row.get("address") if isinstance(row.get("address"), dict) else None,
            )
        )
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
    return {"ok": True, "status": hub.status(fresh=True)}


@app.get("/api/billing")
async def billing_status() -> dict:
    try:
        stripe_billing.settle_pending_if_due(hub.store)
        state = stripe_billing.billing_state(hub.store)
        record = hub.store.billing_record() or {}
        if record.get("customer_id") and config.STRIPE_SECRET_KEY and (
            state.get("plan") == "free" or record.get("pending_plan") or record.get("cancel_at_period_end")
        ):
            state = stripe_billing.recover_from_stripe(hub.store)
            stripe_billing.apply_watch_limit(hub.store)
            hub._status_cache = None
            state = stripe_billing.billing_state(hub.store)
        return {**state, "invoices": stripe_billing.list_invoices(hub.store)}
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.post("/api/billing/checkout")
async def billing_checkout(payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        result = stripe_billing.create_checkout(
            hub.store,
            str(body.get("plan") or ""),
            user_account.public_account(hub.store).get("email") or str(body.get("email") or ""),
        )
        hub._status_cache = None
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.post("/api/billing/portal")
async def billing_portal() -> dict:
    try:
        return {"url": stripe_billing.create_portal(hub.store)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.post("/api/billing/cancel")
async def billing_cancel() -> dict:
    try:
        result = stripe_billing.cancel_subscription(hub.store)
        hub._status_cache = None
        return result
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.post("/api/billing/sync")
async def billing_sync(payload: dict[str, Any] | None = Body(None)) -> dict:
    session_id = str((payload or {}).get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(400, "Chybí session_id")
    try:
        result = stripe_billing.sync_checkout_session(hub.store, session_id)
        hub._status_cache = None
        return result
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request) -> dict:
    payload = await request.body()
    try:
        result = stripe_billing.handle_webhook(hub.store, payload, request.headers.get("stripe-signature"))
        hub._status_cache = None
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
