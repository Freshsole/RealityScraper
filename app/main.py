from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import sys
import threading
import zipfile
from contextlib import asynccontextmanager
import time
from urllib.parse import quote, urlencode
from typing import Any

from fastapi import Body, Cookie, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app import config
from app.backup import export_config, export_pack, export_sqlite, import_config, replace_sqlite
from app.monitor import Hub
from app.templates import VARIABLES, default_template_config, sample_vars
from app.updater import apply_update, version_info
from app import bazos_url, bezrealitky_url, idnes_url, localities, url_builder
from app import places as place_geo
from app.filter_bridge import convert_search_url
from app.catalog_sync import monitor_search_targets
from app.commute import route_times
from app import billing as stripe_billing
from app import account as user_account
from app import admin as admin_panel
from app import cms as stories_cms
from app import analytics as site_stats
from app import push as web_push
from app import email_notify as mail_notify
from app import whatsapp as wa_notify
from app import agents as agent_hub
from app import mcp_oauth
from app import extension_score as ext_score
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
        def _force_exit() -> None:
            time.sleep(2.5)
            os._exit(130)

        threading.Thread(target=_force_exit, name="shutdown-watchdog", daemon=True).start()
        try:
            await asyncio.wait_for(hub.close(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="Sreality Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.middleware("http")
async def static_asset_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/") and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=2592000"
    return response


@app.middleware("http")
async def record_ops_timing(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/"):
        return await call_next(request)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        admin_panel.record_api_sample((time.perf_counter() - started) * 1000, False)
        raise
    admin_panel.record_api_sample((time.perf_counter() - started) * 1000, response.status_code < 500)
    return response


@app.middleware("http")
async def no_store_ui(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if (
        path.startswith("/nastaveni")
        or path.startswith("/admin")
        or path.startswith("/uspechy")
        or path in {
        "/",
        "/kontakt",
        "/obchodni-podminky",
        "/ochrana-soukromi",
        "/nastaveni-cookies",
        "/prihlaseni",
        "/registrace",
        "/heslo",
        "/prehled",
        "/nabidka",
        "/monitory",
        "/filtry",
        "/zprava",
        "/nastaveni",
        "/admin",
        "/sw.js",
        "/manifest.webmanifest",
    }
    ):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def _is_app_page(path: str) -> bool:
    return path in {"/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"} or path.startswith("/nastaveni/")


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    return ((request.client.host if request.client else "") or "")[:64]


@app.middleware("http")
async def require_account(request: Request, call_next):
    if request.method == "GET" and _is_app_page(request.url.path):
        user = await asyncio.get_running_loop().run_in_executor(
            hub.auth_pool,
            user_account.user_from_session,
            hub.store,
            request.cookies.get(user_account.SESSION_COOKIE),
        )
        guest_ok = False
        if not user and request.url.path == "/nabidka":
            guest_ok = await asyncio.get_running_loop().run_in_executor(
                hub.auth_pool,
                hub.store.guest_search_has_access,
                request.cookies.get("rf_guest_search") or "",
            )
        if not user:
            if request.url.path == "/nabidka" and guest_ok:
                return await call_next(request)
            if request.url.path == "/nabidka":
                nxt = request.url.path
                if request.url.query:
                    nxt = f"{nxt}?{request.url.query}"
                return RedirectResponse(f"/registrace?next={quote(nxt, safe='')}", status_code=303)
            return RedirectResponse("/prihlaseni", status_code=303)
    return await call_next(request)


def _extension_cors_origin(request: Request) -> str | None:
    origin = (request.headers.get("origin") or "").strip()
    if not origin:
        return None
    if origin.startswith("chrome-extension://"):
        return origin
    allowed = {
        "https://www.sreality.cz",
        "https://sreality.cz",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    }
    base = (config.PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        allowed.add(base)
    if origin in allowed or origin.endswith(".sreality.cz"):
        return origin
    return None


@app.middleware("http")
async def agent_cors(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/extension"):
        origin = _extension_cors_origin(request)
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        else:
            response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Realitify-Session"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
        return response
    if (
        path == "/mcp"
        or path.startswith("/api/v1")
        or path.startswith("/api/agents/openapi")
        or path.startswith("/.well-known/oauth")
        or path.startswith("/oauth/")
    ):
        if request.method == "OPTIONS":
            response = Response(status_code=204)
        else:
            response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = (
            "Authorization, Content-Type, Mcp-Session-Id, MCP-Protocol-Version"
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS, HEAD"
        return response
    return await call_next(request)


def page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


def landing() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


def byt_preview() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "byt.html", headers={"Cache-Control": "no-store, max-age=0"})


def contact() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "kontakt.html", headers={"Cache-Control": "no-store, max-age=0"})


def terms() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "obchodni-podminky.html", headers={"Cache-Control": "no-store, max-age=0"})


def privacy() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "ochrana-soukromi.html", headers={"Cache-Control": "no-store, max-age=0"})


def cookies_page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "nastaveni-cookies.html", headers={"Cache-Control": "no-store, max-age=0"})


def stories() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "uspechy.html", headers={"Cache-Control": "no-store, max-age=0"})


def story_article() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "clanek.html", headers={"Cache-Control": "no-store, max-age=0"})


def auth_login() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "prihlaseni.html", headers={"Cache-Control": "no-store, max-age=0"})


def auth_register() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "registrace.html", headers={"Cache-Control": "no-store, max-age=0"})


def auth_forgot() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "heslo.html", headers={"Cache-Control": "no-store, max-age=0"})


def admin_page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "admin" / "index.html", headers={"Cache-Control": "no-store, max-age=0"})


app.add_api_route("/", landing, methods=["GET"], include_in_schema=False)
app.add_api_route("/byt", byt_preview, methods=["GET"], include_in_schema=False)
app.add_api_route("/kontakt", contact, methods=["GET"], include_in_schema=False)
app.add_api_route("/obchodni-podminky", terms, methods=["GET"], include_in_schema=False)
app.add_api_route("/ochrana-soukromi", privacy, methods=["GET"], include_in_schema=False)
app.add_api_route("/nastaveni-cookies", cookies_page, methods=["GET"], include_in_schema=False)
app.add_api_route("/uspechy", stories, methods=["GET"], include_in_schema=False)
app.add_api_route("/uspechy/{slug}", story_article, methods=["GET"], include_in_schema=False)
app.add_api_route("/prihlaseni", auth_login, methods=["GET"], include_in_schema=False)
app.add_api_route("/registrace", auth_register, methods=["GET"], include_in_schema=False)
app.add_api_route("/heslo", auth_forgot, methods=["GET"], include_in_schema=False)
for _path in ("/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"):
    app.add_api_route(_path, page, methods=["GET"], include_in_schema=False)
app.add_api_route("/nastaveni/{rest:path}", page, methods=["GET"], include_in_schema=False)


@app.get("/app", include_in_schema=False)
async def app_alias(request: Request):
    """Legacy /app links from extension → catalog deep-link."""
    query = request.url.query
    target = "/nabidka" + (f"?{query}" if query else "")
    return RedirectResponse(target, status_code=302)
app.add_api_route("/admin", admin_page, methods=["GET"], include_in_schema=False)
app.add_api_route("/admin/{rest:path}", admin_page, methods=["GET"], include_in_schema=False)


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


@app.post("/api/t")
async def telemetry(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    if not site_stats.analytics_allowed(request):
        response = JSONResponse({"ok": True, "skipped": True})
        response.delete_cookie(site_stats.VISITOR_COOKIE, path="/")
        return response
    visitor = await asyncio.to_thread(site_stats.ingest, hub.store, request, payload or {})
    response = JSONResponse({"ok": True})
    site_stats.attach_cookie(response, visitor)
    return response


@app.post("/api/auth/register")
async def auth_register_api(payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        user, token = user_account.register(
            hub.store,
            str(body.get("name") or ""),
            str(body.get("email") or ""),
            str(body.get("password") or ""),
            str(body.get("promo") or body.get("promo_code") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse(user)
    _set_session_cookie(response, token)
    try:
        site_stats.track(hub.store, site_stats.KIND_SIGNUP, path="/registrace")
    except Exception:
        pass
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


def _extension_session(request: Request, cookie: str | None) -> str | None:
    header = (request.headers.get("x-realitify-session") or "").strip()
    return header or cookie


@app.get("/api/extension/me")
async def extension_me(
    request: Request,
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    session = _extension_session(request, realitify_session)
    user = user_account.user_from_session(hub.store, session)
    account = ext_score.extension_account(hub.store)
    if not user:
        account["authenticated"] = False
        account["active"] = False
        account["pro"] = False
        return account
    account["authenticated"] = True
    return account


@app.post("/api/extension/scores")
async def extension_scores(
    request: Request,
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    session = _extension_session(request, realitify_session)
    user = user_account.user_from_session(hub.store, session)
    account = ext_score.extension_account(hub.store)
    if not user:
        raise HTTPException(401, "Nejste přihlášeni")
    if not account.get("active"):
        raise HTTPException(403, "Aktivní předplatné Start nebo PRO je povinné")
    body = payload or {}
    ids = body.get("ids") if isinstance(body.get("ids"), list) else []
    urls = body.get("urls") if isinstance(body.get("urls"), list) else []
    result = ext_score.score_batch(hub.store, ids=[str(x) for x in ids], urls=[str(x) for x in urls])
    result["account"] = {
        "plan": account["plan"],
        "label": account["label"],
        "active": account["active"],
        "pro": account["pro"],
    }
    return result


@app.post("/api/extension/ingest")
async def extension_ingest(
    request: Request,
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    """Scrape unknown Sreality listings into catalog and return fresh scores."""
    session = _extension_session(request, realitify_session)
    user = user_account.user_from_session(hub.store, session)
    account = ext_score.extension_account(hub.store)
    if not user:
        raise HTTPException(401, "Nejste přihlášeni")
    if not account.get("active"):
        raise HTTPException(403, "Aktivní předplatné Start nebo PRO je povinné")

    body = payload or {}
    raw_urls = body.get("urls") if isinstance(body.get("urls"), list) else []
    raw_ids = body.get("ids") if isinstance(body.get("ids"), list) else []
    urls: list[str] = []
    for item in raw_urls:
        text = str(item or "").strip()
        if text:
            urls.append(text)
    for item in raw_ids:
        native = ext_score.extract_sreality_id(item)
        if native and not any(native in u for u in urls):
            # Best-effort URL; scrape client needs a path — skip bare ids without URL
            continue

    # Deduplicate + keep only sreality
    seen: set[str] = set()
    cleaned: list[str] = []
    for url in urls:
        norm = ext_score.normalize_url(url)
        if "sreality.cz" not in norm.lower():
            continue
        if "/detail/" not in norm.lower():
            continue
        if norm in seen:
            continue
        seen.add(norm)
        cleaned.append(norm)
    cleaned = cleaned[:12]  # hard cap per request
    if not cleaned:
        raise HTTPException(400, "Chybí platná Sreality detail URL")

    from app.sreality import ListingGone, SrealityClient

    client = hub.client_for("https://www.sreality.cz/")
    if not isinstance(client, SrealityClient):
        client = SrealityClient("https://www.sreality.cz/")

    ingested: list[str] = []
    errors: dict[str, str] = {}

    async def _one(url: str) -> None:
        native = ext_score.extract_sreality_id(url)
        try:
            listing = await client.fetch_listing_url(url)
            from app.places import refine_listing_location

            refine_listing_location(listing)
            await asyncio.to_thread(hub.store.upsert_catalog_listing, listing, kind="extension")
            if listing.id:
                ext_score.invalidate_scores(str(listing.id))
                ingested.append(str(listing.id))
            elif native:
                ext_score.invalidate_scores(native)
                ingested.append(native)
        except ListingGone:
            errors[native or url] = "gone"
        except Exception as exc:
            errors[native or url] = str(exc)[:200]

    await asyncio.gather(*[_one(url) for url in cleaned])

    score_ids = list(dict.fromkeys(ingested + [ext_score.extract_sreality_id(u) for u in cleaned if ext_score.extract_sreality_id(u)]))
    result = ext_score.score_batch(hub.store, ids=score_ids, urls=cleaned)
    result["ingested"] = ingested
    result["errors"] = errors
    result["account"] = {
        "plan": account["plan"],
        "label": account["label"],
        "active": account["active"],
        "pro": account["pro"],
    }
    return result


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


def _admin_user(token: str | None) -> dict[str, Any]:
    user = admin_panel.admin_from_cookie(hub.store, token)
    if not user:
        raise HTTPException(401, "Nejste přihlášeni do administrace")
    return user


@app.post("/api/admin/login")
async def admin_login_api(payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        token = admin_panel.login_admin(hub.store, str(body.get("email") or ""), str(body.get("password") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    user = admin_panel.admin_from_cookie(hub.store, token) or {}
    response = JSONResponse(user)
    response.set_cookie(
        admin_panel.ADMIN_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return response


@app.post("/api/admin/logout")
async def admin_logout_api() -> dict:
    admin_panel.clear_admin_session(hub.store)
    response = JSONResponse({"ok": True})
    response.delete_cookie(admin_panel.ADMIN_COOKIE, path="/")
    return response


@app.get("/api/admin/me")
async def admin_me(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    return await asyncio.to_thread(_admin_user, realitify_admin)


@app.get("/api/admin/overview")
async def admin_overview(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(admin_panel.overview_payload, hub.store, hub)


@app.get("/api/admin/users")
async def admin_users(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(admin_panel.users_payload, hub.store)


@app.get("/api/admin/users/{user_id}")
async def admin_user_detail(
    user_id: str,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    if user_id not in {"local", "USR-LOCAL"}:
        raise HTTPException(404, "Uživatel neexistuje")
    try:
        return await asyncio.to_thread(admin_panel.user_detail_payload, hub.store)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/admin/monitors")
async def admin_monitors(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(hub.auth_pool, _admin_user, realitify_admin)
    return await loop.run_in_executor(hub.ui_pool, admin_panel.monitors_payload, hub.store)


@app.get("/api/admin/notifications")
async def admin_notifications(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(admin_panel.notifications_payload, hub.store)


@app.post("/api/admin/broadcast")
async def admin_broadcast(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = _admin_user(realitify_admin)
    return await admin_panel.send_broadcast(hub.store, payload or {}, user.get("name") or "Admin")


@app.get("/api/admin/ops")
async def admin_ops(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await asyncio.to_thread(_admin_user, realitify_admin)
    return await asyncio.to_thread(admin_panel.ops_payload, hub.store, hub)


@app.post("/api/admin/scrape-url")
async def admin_scrape_url(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    body = payload or {}
    scope = str(body.get("scope") or "url").strip().lower()
    when = str(body.get("when") or "now").strip().lower()
    try:
        max_pages = int(body.get("max_pages") or 40)
    except (TypeError, ValueError):
        max_pages = 40

    if when == "schedule":
        result = hub.schedule_manual_scrape(
            {
                "scope": scope,
                "url": body.get("url"),
                "portal": body.get("portal"),
                "max_pages": max_pages,
                "run_at": body.get("run_at") or body.get("schedule_at") or "",
            }
        )
        if not result.get("ok"):
            raise HTTPException(400, str(result.get("error") or "Naplánování selhalo"))
        return {"result": result, "ops": await asyncio.to_thread(admin_panel.ops_payload, hub.store, hub)}

    if scope == "portal":
        portal = str(body.get("portal") or "").strip().lower()
        if portal not in config.CATALOG_SYNC_HOURS:
            raise HTTPException(400, "Neznámý portál")
        result = hub.start_catalog_sync(portals=[portal])
        if not result.get("ok") and result.get("reason") != "already-running":
            raise HTTPException(409, str(result.get("reason") or "Katalog sync selhal"))
        return {"result": result, "ops": await asyncio.to_thread(admin_panel.ops_payload, hub.store, hub)}

    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Chybí URL hledání")
    result = hub.start_scrape_search_url(url, max_pages=max_pages)
    if not result.get("ok"):
        raise HTTPException(409 if "locked" in str(result.get("error") or "").lower() else 400, str(result.get("error") or "Scrape selhal"))
    return {"result": result, "ops": await asyncio.to_thread(admin_panel.ops_payload, hub.store, hub)}


@app.delete("/api/admin/scrape-schedule/{job_id}")
async def admin_scrape_schedule_delete(
    job_id: str,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    ok = await asyncio.to_thread(hub.store.remove_scrape_schedule, job_id)
    if not ok:
        raise HTTPException(404, "Naplánovaný scrape nenalezen")
    return {"ok": True, "ops": await asyncio.to_thread(admin_panel.ops_payload, hub.store, hub)}


@app.get("/api/admin/dedupe")
async def admin_dedupe(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(admin_panel.dedupe_payload, hub.store, hub)


@app.post("/api/admin/dedupe/schedule")
async def admin_dedupe_schedule(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    body = payload or {}
    try:
        hour = int(body.get("hour", 3))
    except (TypeError, ValueError):
        hour = 3
    hub.store.save_dedupe_schedule(enabled=bool(body.get("enabled")), hour=hour)
    return await asyncio.to_thread(admin_panel.dedupe_payload, hub.store, hub)


@app.post("/api/admin/dedupe/run")
async def admin_dedupe_run(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    result = hub.start_dedupe()
    if not result.get("ok"):
        raise HTTPException(409, "Deduplikace už běží")
    return await asyncio.to_thread(admin_panel.dedupe_payload, hub.store, hub)


@app.post("/api/admin/dedupe/scan")
async def admin_dedupe_scan(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    result = hub.start_dedupe_scan()
    if not result.get("ok"):
        raise HTTPException(409, "Deduplikace už běží")
    return await asyncio.to_thread(admin_panel.dedupe_payload, hub.store, hub)


@app.get("/api/admin/billing")
async def admin_billing(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(admin_panel.billing_payload, hub.store)


@app.get("/api/admin/promo")
async def admin_promo(
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
    month: str | None = Query(default=None),
) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(admin_panel.promo_payload, hub.store, month or "")


@app.post("/api/admin/promo/toggle")
async def admin_promo_toggle(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = _admin_user(realitify_admin)
    body = payload or {}
    try:
        return await asyncio.to_thread(admin_panel.toggle_promo_code, hub.store, str(body.get("code") or ""), bool(body.get("on")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/promo/create")
async def admin_promo_create(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = _admin_user(realitify_admin)
    try:
        return await asyncio.to_thread(admin_panel.create_promo_code, hub.store, payload or {}, user.get("name") or "Admin")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/promo/payout")
async def admin_promo_payout(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    body = payload or {}
    try:
        return await asyncio.to_thread(
            admin_panel.mark_promo_payout,
            hub.store,
            str(body.get("code") or ""),
            str(body.get("month") or ""),
            bool(body.get("paid")),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/action")
async def admin_action(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = _admin_user(realitify_admin)
    body = payload or {}
    try:
        return await asyncio.to_thread(
            admin_panel.apply_action,
            hub.store,
            str(body.get("action") or ""),
            body,
            user.get("name") or "Admin",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/stories")
async def stories_index() -> dict:
    return await asyncio.to_thread(stories_cms.public_listing, hub.store)


@app.get("/api/stories/{slug}")
async def stories_detail(slug: str, view: int = 0) -> dict:
    try:
        return await asyncio.to_thread(stories_cms.public_by_slug, hub.store, slug, bool(view))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/inquiries")
async def public_inquiry(payload: dict[str, Any] | None = Body(None)) -> dict:
    try:
        return await asyncio.to_thread(stories_cms.create_inquiry, hub.store, payload or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/admin/cms")
async def admin_cms_list(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    return await asyncio.to_thread(stories_cms.list_articles, hub.store)


@app.post("/api/admin/cms-inquiry/{inquiry_id}")
async def admin_cms_inquiry_save(
    inquiry_id: str,
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.update_inquiry, hub.store, inquiry_id, payload or {})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/cms-inquiry/{inquiry_id}/delete")
async def admin_cms_inquiry_delete(
    inquiry_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")
) -> dict:
    _admin_user(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.delete_inquiry, hub.store, inquiry_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/admin/cms/{article_id}")
async def admin_cms_one(article_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.get_article, hub.store, article_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/cms")
async def admin_cms_create(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = _admin_user(realitify_admin)
    return await asyncio.to_thread(stories_cms.save_article, hub.store, None, payload or {}, user.get("name") or "Admin")


@app.post("/api/admin/cms/{article_id}")
async def admin_cms_save(
    article_id: str,
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = _admin_user(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.save_article, hub.store, article_id, payload or {}, user.get("name") or "Admin")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/cms/{article_id}/delete")
async def admin_cms_delete(article_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _admin_user(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.delete_article, hub.store, article_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/cms-upload")
async def admin_cms_upload(
    file: UploadFile = File(...),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _admin_user(realitify_admin)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "Soubor je větší než 5 MB")
    url = await asyncio.to_thread(stories_cms.save_upload, file.filename or "image.jpg", raw)
    return {"url": url}


@app.get("/media/cms/{name}")
async def cms_media(name: str) -> FileResponse:
    try:
        path = stories_cms.media_path(name)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path)


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
async def catalog_sync_now(payload: dict[str, Any] | None = None) -> dict:
    raw = (payload or {}).get("portals") if isinstance(payload, dict) else None
    portals = None
    if isinstance(raw, str) and raw and raw != "all":
        portals = [raw]
    elif isinstance(raw, list):
        portals = [str(item) for item in raw if item]
    return {"result": hub.start_catalog_sync(portals=portals), "status": hub.status(fresh=True)}


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
    ua = request.headers.get("user-agent") or ""

    def _save() -> dict[str, Any]:
        hub.store.save_push_subscription(body, ua)
        prefs = hub.store.notify_prefs()
        prefs["push"] = True
        hub.store.save_notify_prefs(prefs)
        return {"ok": True, "devices": hub.store.push_subscription_count(), "notify": hub.store.notify_prefs()}

    try:
        return await asyncio.to_thread(_save)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        # Non-critical: UI can retry later; don't 500 the nabidka boot
        if "locked" in str(exc).lower():
            return {"ok": False, "skipped": True, "reason": "database_locked"}
        raise


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


@app.get("/api/public/stats")
async def public_stats() -> dict:
    return {"new_today": await asyncio.to_thread(hub.store.catalog_new_today_count)}


@app.get("/api/public/landing-listings")
async def public_landing_listings() -> dict:
    items = await asyncio.to_thread(hub.store.landing_preview_listings)
    return {"items": items}


def _guest_search_payload(request: Request, *, consume: bool) -> tuple[dict[str, Any], str | None]:
    user = user_account.user_from_session(hub.store, request.cookies.get(user_account.SESSION_COOKIE))
    if user:
        return {"ok": True, "logged_in": True, "allowed": True}, None
    token = request.cookies.get("rf_guest_search") or ""
    ip = _client_ip(request)
    visitor = request.cookies.get(site_stats.VISITOR_COOKIE) or ""
    if hub.store.guest_search_has_access(token):
        if consume:
            return {"ok": False, "logged_in": False, "allowed": False, "need_register": True}, None
        return {"ok": False, "logged_in": False, "allowed": False, "need_register": True, "used": True}, token
    if hub.store.guest_search_used(ip, visitor):
        return {"ok": False, "logged_in": False, "allowed": False, "need_register": True}, None
    if not consume:
        return {"ok": True, "logged_in": False, "allowed": True, "remaining": 1}, None
    granted = hub.store.grant_guest_search(ip, visitor)
    if not granted:
        return {"ok": False, "logged_in": False, "allowed": False, "need_register": True}, None
    return {"ok": True, "logged_in": False, "allowed": True}, granted


@app.get("/api/public/guest-search")
async def public_guest_search_status(request: Request) -> dict:
    payload, _token = _guest_search_payload(request, consume=False)
    return payload


@app.post("/api/public/guest-search")
async def public_guest_search_start(request: Request) -> JSONResponse:
    payload, token = _guest_search_payload(request, consume=True)
    response = JSONResponse(payload)
    if token and payload.get("ok"):
        response.set_cookie("rf_guest_search", token, max_age=60 * 60 * 24 * 400, samesite="lax", path="/")
        visitor = request.cookies.get(site_stats.VISITOR_COOKIE)
        if visitor:
            site_stats.attach_cookie(response, visitor)
    return response


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
    south: str = "",
    north: str = "",
    west: str = "",
    east: str = "",
    sort: str = "newest",
    limit: int = 36,
    offset: int = 0,
    pins_only: bool = False,
    include_pins: str = "1",
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
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "sort": sort,
        "limit": limit,
        "offset": offset,
        "include_pins": include_pins,
    }
    if pins_only:
        payload["pins_only"] = True
    return payload


async def _run_catalog_query(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = await asyncio.get_running_loop().run_in_executor(hub.ui_pool, hub.store.catalog, payload)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail="catalog-busy") from exc
    return result


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
    south: str = "",
    north: str = "",
    west: str = "",
    east: str = "",
    sort: str = "newest",
    limit: int = 36,
    offset: int = 0,
    include_pins: str = "1",
) -> dict:
    payload = await place_geo.attach_geoms(
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
            south=south,
            north=north,
            west=west,
            east=east,
            sort=sort,
            limit=limit,
            offset=offset,
            include_pins=include_pins,
        )
    )
    return await _run_catalog_query(payload)


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
    south: str = "",
    north: str = "",
    west: str = "",
    east: str = "",
) -> dict:
    payload = await place_geo.attach_geoms(
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
            south=south,
            north=north,
            west=west,
            east=east,
            pins_only=True,
        )
    )
    return await _run_catalog_query(payload)


@app.get("/api/catalog/item")
async def catalog_item(
    monitor_id: str = "",
    id: str = "",
    listing_key: str = "",
    url: str = "",
) -> dict:
    item = hub.store.catalog_item(monitor_id, id, listing_key=listing_key, url=url)
    if not item:
        raise HTTPException(404, "Nabídka se nenašla")
    source_url = item.get("url") or url
    if source_url:
        try:
            listing = _listing_from_catalog_dict(item)
            listing.photos = []
            listing = await hub.client_for(source_url).fetch_detail(listing)
            from app.places import refine_listing_location

            refine_listing_location(listing)
            hub.store.save_listing_enrichment(item["monitor_id"], listing)
            item = hub.store.catalog_item(item["monitor_id"], item["id"], listing_key=item.get("listing_key") or "", url=source_url) or item
        except ListingGone:
            await hub.notify_sold(hub.store.get_monitor(item["monitor_id"]), item["id"])
            item = hub.store.catalog_item(item["monitor_id"], item["id"], url=source_url) or item
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
    existing = hub.store.get_monitor(str(payload.get("id") or "")) if payload.get("id") else None
    try:
        saved = hub.store.save_monitor(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if existing is None:
        try:
            site_stats.track(hub.store, site_stats.KIND_MONITOR, path="/monitory")
        except Exception:
            pass
    return saved


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
    lowered = (url or "").lower()
    if raw == "idnes" or "idnes.cz" in lowered:
        return idnes_url
    if raw == "bazos" or "bazos" in lowered:
        return bazos_url
    if raw == "bezrealitky" or "bezrealitky.cz" in lowered:
        return bezrealitky_url
    return url_builder


@app.get("/api/filters/catalog")
async def filter_catalog() -> dict:
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
            "idnes": {
                "catalog": idnes_url.catalog(),
                "defaults": idnes_url.default_filters(),
                "sample": idnes_url.sample_filters(),
            },
            "bazos": {
                "catalog": bazos_url.catalog(),
                "defaults": bazos_url.default_filters(),
                "sample": bazos_url.sample_filters(),
            },
        },
    }
    return payload


@app.post("/api/filters/build")
async def filter_build(payload: dict[str, Any]) -> dict:
    filters = payload.get("filters") or {}
    mod = _filter_mod(filters.get("source"))
    if not filters.get("source"):
        filters = {
            **filters,
            "source": "idnes"
            if mod is idnes_url
            else "bazos"
            if mod is bazos_url
            else "bezrealitky"
            if mod is bezrealitky_url
            else "sreality",
        }
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
    return {"items": await place_geo.search_places(q)}


@app.get("/api/places/geometry")
async def places_geometry(ids: str = "") -> dict:
    ident = [item.strip() for item in ids.split(",") if item.strip()]
    try:
        items = await asyncio.wait_for(place_geo.geometries(ident), 20.0)
    except Exception:
        items = await place_geo.geometries(ident, network=False)
    if ident and not items:
        items = await place_geo.geometries(ident, network=False)
    return {"items": place_geo.public_geoms(items)}


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
            str(body.get("promo") or body.get("promo_code") or ""),
        )
        hub._status_cache = None
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.get("/api/billing/promo")
async def billing_promo_lookup(code: str = Query("")) -> dict:
    try:
        raw = (code or "").strip()
        if not raw:
            pending = (hub.store.billing_record() or {}).get("pending_promo_code") or ""
            if not pending:
                return {"ok": True, "code": "", "percent": 0, "amount_czk": 0, "first_order": True}
            raw = str(pending)
        return stripe_billing.lookup_promotion_code(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Stripe: {exc}") from exc


@app.post("/api/billing/promo")
async def billing_promo_save(payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    try:
        return stripe_billing.save_pending_promo(hub.store, str(body.get("code") or body.get("promo") or ""))
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


def _agent_key(request: Request) -> dict[str, Any]:
    try:
        key = agent_hub.require_mcp(hub.store, request.headers.get("authorization"))
    except PermissionError as exc:
        raise HTTPException(
            status_code=401,
            detail=str(exc),
            headers={"WWW-Authenticate": mcp_oauth.www_authenticate()},
        ) from exc
    if not agent_hub.rate_ok(str(key.get("id") or "")):
        raise HTTPException(429, "Příliš mnoho požadavků. Zkuste to za chvíli.")
    return key


def _mcp_http(request: Request, payload: Any):
    accept = (request.headers.get("accept") or "").lower()
    if payload is None:
        return Response(status_code=202)
    if "text/event-stream" in accept and "application/json" not in accept:
        return Response(
            f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n",
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    return JSONResponse(payload)


@app.get("/.well-known/oauth-protected-resource")
@app.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource() -> dict:
    return mcp_oauth.protected_resource_metadata()


@app.get("/.well-known/oauth-authorization-server")
@app.get("/.well-known/oauth-authorization-server/mcp")
async def oauth_authorization_server() -> dict:
    return mcp_oauth.authorization_server_metadata()


@app.post("/oauth/register")
async def oauth_register(payload: dict[str, Any] | None = Body(None)):
    try:
        created = mcp_oauth.register_client(hub.store, payload or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return JSONResponse(created, status_code=201)


@app.get("/oauth/authorize")
async def oauth_authorize_get(request: Request):
    params = {key: str(value) for key, value in request.query_params.items()}
    user = user_account.user_from_session(hub.store, request.cookies.get(user_account.SESSION_COOKIE))
    if not user:
        nxt = "/oauth/authorize?" + urlencode(list(request.query_params.multi_items()))
        return RedirectResponse("/prihlaseni?next=" + quote(nxt, safe=""), status_code=303)
    try:
        mcp_oauth.validate_authorize(hub.store, params)
    except ValueError as exc:
        return HTMLResponse(mcp_oauth.consent_html(params, error=str(exc)), status_code=400)
    return HTMLResponse(mcp_oauth.consent_html(params))


@app.post("/oauth/authorize")
async def oauth_authorize_post(request: Request):
    user = user_account.user_from_session(hub.store, request.cookies.get(user_account.SESSION_COOKIE))
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    if not user:
        nxt = "/oauth/authorize?" + urlencode(form)
        return RedirectResponse("/prihlaseni?next=" + quote(nxt, safe=""), status_code=303)
    try:
        target = mcp_oauth.complete_authorize(hub.store, form)
    except PermissionError as exc:
        return HTMLResponse(mcp_oauth.consent_html(form, error=str(exc)), status_code=403)
    except ValueError as exc:
        return HTMLResponse(mcp_oauth.consent_html(form, error=str(exc)), status_code=400)
    return RedirectResponse(target, status_code=303)


@app.post("/oauth/token")
async def oauth_token(request: Request):
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    grant = str(form.get("grant_type") or "")
    try:
        if grant == "authorization_code":
            data = mcp_oauth.exchange_code(
                hub.store,
                code=str(form.get("code") or ""),
                redirect_uri=str(form.get("redirect_uri") or ""),
                client_id=str(form.get("client_id") or ""),
                code_verifier=str(form.get("code_verifier") or ""),
            )
        elif grant == "refresh_token":
            data = mcp_oauth.refresh_tokens(hub.store, str(form.get("refresh_token") or ""))
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    except ValueError:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse(data)


@app.post("/oauth/revoke")
async def oauth_revoke(request: Request) -> dict:
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    mcp_oauth.revoke(hub.store, str(form.get("token") or ""))
    return {"ok": True}


@app.get("/api/agents")
async def agents_dashboard() -> dict:
    return agent_hub.dashboard_payload(hub.store)


@app.post("/api/agents/keys")
async def agents_create_key(payload: dict[str, Any] | None = Body(None)) -> dict:
    try:
        created = agent_hub.create_key(hub.store, str((payload or {}).get("name") or ""))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**created, "snippets": agent_hub.connector_snippets(created["token"])}


@app.delete("/api/agents/keys/{key_id}")
async def agents_delete_key(key_id: str) -> dict:
    try:
        return agent_hub.delete_key(hub.store, key_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/agents/openapi.json")
async def agents_openapi() -> dict:
    return agent_hub.openapi_spec()


@app.api_route("/mcp", methods=["GET", "POST", "HEAD"])
async def mcp_endpoint(request: Request):
    _agent_key(request)
    if request.method == "GET":
        return {
            "name": "realitify",
            "protocolVersion": agent_hub.PROTOCOL,
            "mcp_url": agent_hub.mcp_url(),
        }
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Očekávám JSON-RPC") from exc
    if isinstance(payload, list):
        out = []
        for msg in payload:
            item = agent_hub.handle_mcp(hub.store, msg if isinstance(msg, dict) else {})
            if item is not None:
                out.append(item)
        return _mcp_http(request, out)
    result = agent_hub.handle_mcp(hub.store, payload if isinstance(payload, dict) else {})
    return _mcp_http(request, result)


@app.get("/api/v1/listings")
async def agent_search_listings(request: Request) -> dict:
    _agent_key(request)
    args = dict(request.query_params)
    return agent_hub.run_tool(hub.store, "search_listings", args)


@app.get("/api/v1/listings/{monitor_id}/{listing_id}")
async def agent_get_listing(request: Request, monitor_id: str, listing_id: int) -> dict:
    _agent_key(request)
    try:
        return agent_hub.run_tool(hub.store, "get_listing", {"monitor_id": monitor_id, "listing_id": listing_id})
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/v1/monitors")
async def agent_list_monitors(request: Request) -> dict:
    _agent_key(request)
    return agent_hub.run_tool(hub.store, "list_monitors", {})


@app.post("/api/v1/monitors")
async def agent_create_monitor(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _agent_key(request)
    try:
        return agent_hub.run_tool(hub.store, "create_monitor", payload or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/v1/monitors/{monitor_id}")
async def agent_delete_monitor(request: Request, monitor_id: str) -> dict:
    _agent_key(request)
    try:
        return agent_hub.run_tool(hub.store, "delete_monitor", {"id": monitor_id})
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/alerts")
async def agent_list_alerts(request: Request) -> dict:
    _agent_key(request)
    return agent_hub.run_tool(hub.store, "list_alerts", dict(request.query_params))


@app.get("/api/v1/account")
async def agent_get_account(request: Request) -> dict:
    _agent_key(request)
    return agent_hub.run_tool(hub.store, "get_account", {})
