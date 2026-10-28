from __future__ import annotations

import asyncio
import re
import hashlib
import io
import json
import logging
import os
import sqlite3
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import time
from urllib.parse import quote, urlencode
from typing import Any

from fastapi import Body, Cookie, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from app import config
from app.backup import export_config, export_pack, export_sqlite, import_config, replace_sqlite
from app.monitor import Hub
from app.scrape_console import install_stdout_tee
from app.templates import VARIABLES, default_template_config, sample_vars
from app.updater import apply_update, version_info
from app import bazos_url, bezrealitky_url, idnes_url, localities, url_builder
from app import places as place_geo
from app.filter_bridge import convert_search_url
from app.catalog_sync import monitor_search_targets
from app.commute import route_times
from app import billing as stripe_billing
from app import account as user_account
from app import guest as guest_access
from app import users as user_registry
from app import admin as admin_panel
from app import cms as stories_cms
from app import rate_limit
from app import analytics as site_stats
from app import push as web_push
from app import email_notify as mail_notify
from app.emailing import admin_api as email_admin_api
from app.emailing import ses as email_ses
from app.emailing import store as email_store
from app.emailing import tracking as email_tracking
from app.emailing import triggers as email_triggers
from app import whatsapp as wa_notify
from app import agent_discovery
from app import agents as agent_hub
from app import mcp_oauth
from app import admin_mcp
from app import admin_oauth
from app import free_pro

install_stdout_tee()
from app import extension_score as ext_score
from app.sreality import ListingGone
from app.store import CATALOG_MONITOR_ID, _listing_from_catalog_dict, Store

hub = Hub()

logger = logging.getLogger("realitify")
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


async def _background_startup() -> None:
    """Heavy init after lifespan yield so Coolify /health can pass immediately."""
    from app import perf_diag

    perf_diag.install_default_executor()
    if perf_diag.ENABLED:
        asyncio.create_task(perf_diag.watchdog(), name="perf-watchdog")
    await hub.start()
    email_store.bind(hub.store)
    email_triggers._STATE["store_for"] = hub.store_for
    if config.SCRAPE_ROLE != "web" and config.EMAIL_AUTOMATION:
        # Split deployments run the queue in the scrape worker (one place sends the lifecycle mails).
        from app.emailing import service as email_service

        asyncio.create_task(email_service.run_email_loop(hub.store_for, hub.store), name="email-loop")
    if config.FREE_PRO_SHEET_CSV_URL and config.SCRAPE_ROLE != "worker":
        asyncio.create_task(free_pro.run_sheet_loop(hub.store, hub.store_for), name="free-pro-sheet")
    from app import games as marketing_games

    marketing_games.schedule_pool_refresh(hub.store)
    if config.ON_RAILWAY and not config.PERSISTENT_STORAGE:
        hub.last_error = (
            "Databáze není na Railway Volume. Po každém deployi se smaže účet. "
            "V Railway → Volume přidej disk a připoj ho na /data."
        )
    elif not config.DISCORD_BOT_TOKEN and not config.DISCORD_WEBHOOK_URL:
        print(
            "Discord not configured — email/push still work. "
            "Set DISCORD_BOT_TOKEN+DISCORD_GUILD_ID or DISCORD_WEBHOOK_URL for Discord alerts.",
            flush=True,
        )
    asyncio.create_task(maybe_auto_update())
    asyncio.create_task(_catalog_warm_loop(), name="catalog-warm")

    def _warm_seo_sitemap() -> None:
        try:
            from app import market_seo

            store = hub.store
            market_seo.refresh_market_seo_inventory(store)
            market_seo.cached_sitemap_xml(store, SEO_PAGES)
        except Exception as exc:
            print(f"seo sitemap warm failed: {exc}", flush=True)

    threading.Thread(target=hub.store.ensure_background_indexes, name="store-indexes", daemon=True).start()
    threading.Thread(target=_warm_seo_sitemap, name="seo-sitemap-warm", daemon=True).start()
    threading.Thread(target=_diag_localhost_health, name="localhost-diag", daemon=True).start()
    try:
        from app import ssr_cache

        if config.SCRAPE_ROLE == "web":
            # The split deployment rebuilds in the worker (app.ssr_rebuild); the web only reads the
            # persisted cache. Build it here once only if the volume has none yet (fresh install).
            def _first_build() -> None:
                time.sleep(120)
                if ssr_cache.get_page("home") is None:
                    ssr_cache.ensure_fresh(_public_store() or hub.store, reason="empty_cache")

            threading.Thread(target=_first_build, name="ssr-first-build", daemon=True).start()
        else:
            ssr_cache.schedule_loop(lambda: _public_store() or hub.store)
    except Exception:
        pass
    print("lifespan: background startup complete", flush=True)


def _diag_localhost_health() -> None:
    """Diagnose Docker healthcheck connectivity from inside the container.

    Waits for uvicorn to finish binding, then polls http://localhost:<port>/
    health via every resolved address family and dumps the actual listening
    sockets from /proc. Definitive diagnosis for the Coolify healthcheck
    failures (probes fail fast with exit 1 while the app reports ready).
    """
    import socket as _socket
    import time as _time

    try:
        port = int(os.environ.get("PORT") or config.PORT)
    except Exception:
        port = 8000

    def _listening_sockets() -> list[str]:
        out: list[str] = []
        for path, fam in (("/proc/net/tcp", "v4"), ("/proc/net/tcp6", "v6")):
            try:
                with open(path) as fh:
                    lines = fh.read().splitlines()[1:]
                for ln in lines:
                    parts = ln.split()
                    if len(parts) >= 4 and parts[3] == "0A":  # LISTEN
                        out.append(f"{fam}:{parts[1]}")
            except Exception as exc:
                out.append(f"{fam}:err:{exc}")
        return out

    print(f"diag: PORT={port} listening={_listening_sockets()}", flush=True)
    try:
        infos = _socket.getaddrinfo("localhost", port, type=_socket.SOCK_STREAM)
        print(f"diag: localhost -> {[i[4] for i in infos]}", flush=True)
    except Exception as exc:
        print(f"diag: getaddrinfo failed: {exc}", flush=True)
        return
    deadline = _time.monotonic() + 90
    attempt = 0
    while _time.monotonic() < deadline:
        attempt += 1
        for fam, _typ, _proto, _canon, sockaddr in infos:
            s = _socket.socket(fam, _socket.SOCK_STREAM)
            s.settimeout(3)
            try:
                s.connect(sockaddr)
                s.sendall(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
                resp = s.recv(60)
                print(f"diag: attempt {attempt} {sockaddr} -> {resp[:40]!r} OK", flush=True)
                return
            except Exception as exc:
                print(
                    f"diag: attempt {attempt} {sockaddr} FAIL "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            finally:
                s.close()
        _time.sleep(5)
    print(
        f"diag: GAVE UP after {attempt} attempts; listening={_listening_sockets()}",
        flush=True,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    install_stdout_tee()
    print(
        f"SQLite: {config.DB_PATH} persistent={config.PERSISTENT_STORAGE} scrape_role={config.SCRAPE_ROLE}",
        flush=True,
    )
    startup_task = asyncio.create_task(_background_startup(), name="app-background-startup")
    try:
        yield
    finally:
        if not startup_task.done():
            startup_task.cancel()
            try:
                await startup_task
            except asyncio.CancelledError:
                pass
        elif startup_task.exception() is not None:
            print(f"lifespan: background startup failed: {startup_task.exception()!r}", flush=True)
        def _force_exit() -> None:
            time.sleep(2.5)
            os._exit(130)

        threading.Thread(target=_force_exit, name="shutdown-watchdog", daemon=True).start()
        try:
            from app import discord_notify, places

            await asyncio.wait_for(places.aclose(), timeout=1.0)
            await asyncio.wait_for(discord_notify.aclose(), timeout=1.0)
        except Exception:
            pass
        try:
            await asyncio.wait_for(hub.close(), timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="Sreality Monitor", lifespan=lifespan, redirect_slashes=False)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")
_public_dir = config.ROOT / "public"
if _public_dir.is_dir():
    app.mount("/public", StaticFiles(directory=_public_dir), name="public")


def public_error(exc: BaseException, fallback: str = "Něco se nepovedlo. Zkuste to prosím znovu.") -> str:
    """Převede technickou výjimku na lidskou českou hlášku pro uživatele.

    Kurátorské ValueError hlášky z doménové vrstvy (česky, psané pro uživatele)
    se přes tento helper neposílají — ten je jen pro neočekávané technické chyby,
    které by jinak unikly klientovi i s detaily (tracebacky, názvy služeb, …).
    Skutečnou chybu musí volající vždy zalogovat přes logger.exception.
    """
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    blob = f"{name} {msg}"
    if isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in blob or "timed out" in blob:
        return "Spojení vypršelo. Zkontrolujte připojení k internetu a zkuste to znovu."
    if "connection" in blob or "network" in blob or "dns" in blob or "name resolution" in blob:
        return "Nepodařilo se spojit se službou. Zkuste to prosím za chvíli znovu."
    if "locked" in blob:
        return "Systém je právě zaneprázdněný. Zkuste to prosím za chvíli znovu."
    if "stripe" in blob or "payment" in blob or "invoice" in blob or "checkout" in blob:
        return "Platbu se nepodařilo zpracovat. Zkuste to prosím znovu, případně nám napište na podpora@realitify.cz."
    if "discord" in blob or "webhook" in blob:
        return "Zprávu se nepodařilo odeslat. Zkontrolujte nastavení Discordu a zkuste to znovu."
    if "smtp" in blob or "email" in name or "mail" in name:
        return "E-mail se nepodařilo odeslat. Zkuste to prosím znovu."
    if "http" in name or "status code" in blob or " 429" in blob or " 502" in blob or " 503" in blob:
        return "Externí služba je momentálně nedostupná. Zkuste to prosím později."
    if "unauthorized" in blob or " 401" in blob or " 403" in blob:
        return "Přístup byl zamítnut. Zkuste se prosím znovu přihlásit."
    if "not found" in blob or " 404" in blob:
        return "Požadovaná věc nebyla nalezena. Možná už neexistuje."
    return fallback


@app.exception_handler(StarletteHTTPException)
async def http_exception_with_404_page(request: Request, exc: StarletteHTTPException):
    path = request.url.path
    if (
        exc.status_code == 404
        and "text/html" in request.headers.get("accept", "")
        and not path.startswith(("/api/", "/static/"))
    ):
        from app import site_chrome

        return HTMLResponse(site_chrome.not_found_page(), status_code=404, headers={"Cache-Control": "no-store"})
    from fastapi.exception_handlers import http_exception_handler

    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Záchranná síť: neošetřená výjimka nikdy nesmí ukázat technický detail."""
    if isinstance(exc, HTTPException):
        # HTTPException mají vlastní kurátorské hlášky — ty necháváme projít.
        raise exc
    logger.exception("Neošetřená výjimka %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Na naší straně se něco pokazilo. Zkuste to prosím za chvíli znovu."},
    )


_STATIC_CACHE = "public, max-age=31536000, immutable"
_PUBLIC_HTML_CACHE = "no-cache"
_PRIVATE_HTML_CACHE = "no-store, max-age=0"


@app.middleware("http")
async def strip_trailing_slash(request: Request, call_next):
    path = request.url.path
    if len(path) > 1 and path.endswith("/"):
        target = path.rstrip("/") or "/"
        url = request.url.replace(path=target)
        return RedirectResponse(str(url), status_code=301)
    return await call_next(request)


@app.middleware("http")
async def static_asset_cache(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if response.status_code == 200 and (
        path.startswith("/static/")
        or path.startswith("/public/")
        or path.startswith("/site/")
        or path.startswith("/assets/")
        or path.endswith((".png", ".webp", ".avif", ".jpg", ".jpeg", ".gif", ".svg", ".woff2", ".css", ".js"))
    ):
        # HTML routes keep their own Cache-Control; only stamp hashed/static assets.
        if not (response.headers.get("content-type") or "").startswith("text/html"):
            response.headers["Cache-Control"] = _STATIC_CACHE
    return response


@app.middleware("http")
async def record_ops_timing(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/") or path == "/health":
        return await call_next(request)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        ms = (time.perf_counter() - started) * 1000
        admin_panel.record_api_sample(ms, False)
        from app import perf_diag

        perf_diag.record_request(path, request.method, 500, ms, thread=threading.current_thread().name)
        raise
    ms = (time.perf_counter() - started) * 1000
    admin_panel.record_api_sample(ms, response.status_code < 500)
    from app import perf_diag

    perf_diag.record_request(path, request.method, response.status_code, ms, thread=threading.current_thread().name)
    if ms >= 200:
        response.headers["X-Perf-Ms"] = f"{ms:.0f}"
    return response


_PRIVATE_UI_PATHS = {
    "/prihlaseni",
    "/registrace",
    "/heslo",
    "/overeni",
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
_PUBLIC_UI_PATHS = {
    "/",
    "/kontakt",
    "/obchodni-podminky",
    "/ochrana-soukromi",
    "/nastaveni-cookies",
    "/privacy",
    "/terms",
    "/mcp-docs",
    "/mcp-docs-cs",
    "/uspechy",
    "/byt",
    "/pronajem-praha",
    "/pronajem-brno",
    "/hry",
    "/hry/vyssi-nizsi",
    "/hry/najem",
    "/o-nas",
    "/about",
    "/hledat",
    "/index",
    "/faq",
}


@app.middleware("http")
async def html_cache_policy(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/nastaveni") or path.startswith("/admin") or path in _PRIVATE_UI_PATHS:
        response.headers["Cache-Control"] = _PRIVATE_HTML_CACHE
    elif path.startswith("/uspechy/") or path in _PUBLIC_UI_PATHS:
        response.headers["Cache-Control"] = _PUBLIC_HTML_CACHE
    return response


def _is_app_page(path: str) -> bool:
    return path in {"/prehled", "/nabidka", "/monitory", "/filtry", "/zprava", "/nastaveni"} or path.startswith("/nastaveni/")


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    return ((request.client.host if request.client else "") or "")[:64]


async def _auth_db(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(hub.auth_pool, fn, *args)


def _site_base(request: Request) -> str:
    """Public base URL for links in e-mails (config first, else what the request used)."""
    configured = (config.PUBLIC_BASE_URL or "").rstrip("/")
    if configured:
        return configured
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}".rstrip("/")


async def _send_verification_email(request: Request, user: dict[str, Any]) -> bool:
    """Create a token and mail the link. Returns False (and logs the link in dev) when mail is not set up."""
    from app import email_notify

    token = await _auth_db(user_registry.create_email_verification, hub.store, user["id"])
    base = _site_base(request)
    verify_url = f"{base}/overeni?token={token}"
    local_dev = any(host in base for host in ("localhost", "127.0.0.1"))
    if not email_notify.configured():
        if local_dev:
            print(f"[verify] SMTP není nastavené; ověřovací odkaz pro {user['email']}: {verify_url}", flush=True)
        else:
            print("[verify] SMTP není nastavené, ověřovací e-mail nelze odeslat", flush=True)
        return False
    try:
        log_id = await asyncio.to_thread(email_triggers.on_verification_requested, user, verify_url)
        row = await asyncio.to_thread(email_store.log_get, log_id) if log_id else None
        if row and row["status"] == "sent":
            return True
        print(f"[verify] e-mail se nepodařilo odeslat: {(row or {}).get('error')}", flush=True)
    except Exception as exc:
        print(f"[verify] e-mail se nepodařilo odeslat: {exc}", flush=True)
    if local_dev:
        print(f"[verify] ověřovací odkaz pro {user['email']}: {verify_url}", flush=True)
    return False


_VERIFY_EXEMPT = ("/api/auth/", "/api/admin/login", "/api/guest/", "/api/public/", "/api/places/", "/api/t", "/api/inquiries")


@app.middleware("http")
async def require_verified_email_for_writes(request: Request, call_next):
    """Accounts must confirm their e-mail before they can change anything (reads stay open)."""
    path = request.url.path
    if (
        request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and path.startswith("/api/")
        and not path.startswith(_VERIFY_EXEMPT)
    ):
        token = request.cookies.get(user_account.SESSION_COOKIE)
        if token:
            user = await asyncio.get_running_loop().run_in_executor(
                hub.auth_pool, user_account.user_from_session, hub.store, token
            )
            if (
                user
                and not user.get("email_verified")
                and user.get("role") != "admin"
                and await asyncio.get_running_loop().run_in_executor(hub.auth_pool, email_store.verification_required)
            ):
                return JSONResponse(
                    {"detail": "Nejdřív potvrďte svůj e-mail.", "verification_required": True},
                    status_code=403,
                )
    return await call_next(request)


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
        # Povol jen oficiální extension ID (CSV v EXTENSION_IDS), ne každou extenzi.
        ext_id = origin[len("chrome-extension://"):].split("/")[0]
        allowed_ids = {e.strip() for e in (config.EXTENSION_IDS or "").split(",") if e.strip()}
        return origin if ext_id and ext_id in allowed_ids else None
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
        or path.startswith("/.well-known/openid-configuration/admin-oauth")
        or path.startswith("/admin-oauth/.well-known")
        or path.startswith("/oauth/")
        or path.startswith("/agent/identity")
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


def _asset_version() -> str:
    """Content hash statických assetů pro cache-busting — mění se vždy,
    když se změní obsah JS/CSS, nezávisle na git metadatech v buildu."""
    try:
        digest = hashlib.sha256()
        for name in ("app.js", "catalog.js", "styles.css", "mobile-app.css", "mobile-app.js", "icons.js"):
            path = config.WEB_DIR / name
            if path.exists():
                digest.update(path.read_bytes())
        return digest.hexdigest()[:8]
    except Exception:
        return "dev"


def page() -> HTMLResponse:
    html = (config.WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__ASSET_VERSION__", _asset_version())
    return HTMLResponse(html, headers={"Cache-Control": _PRIVATE_HTML_CACHE})


_SSR_CACHE_NOT_READY = "Public market data cache is not ready yet. Retry shortly."
_SSR_RETRY_AFTER = "120"


def _ssr_cache_not_ready() -> HTTPException:
    return HTTPException(
        503,
        _SSR_CACHE_NOT_READY,
        headers={"Retry-After": _SSR_RETRY_AFTER},
    )


def landing() -> HTMLResponse:
    from app import market_pages, ssr_cache
    import html as html_lib

    home = ssr_cache.get_json("home")
    if not home:
        raise _ssr_cache_not_ready()
    html = (config.WEB_DIR / "site" / "index.html").read_text(encoding="utf-8")
    active = int(home.get("active_count") or 0)
    portals = str(home.get("portals_list") or market_pages.public_portals_sentence())
    portal_n = str(home.get("portals_count") or len(market_pages.PUBLIC_PORTAL_LABELS))
    top_html = str(home.get("top_localities_html") or "")
    data_as_of = str(home.get("data_as_of") or ssr_cache.last_success_as_of() or "")
    html = (
        html.replace("__ACTIVE_COUNT__", market_pages._fmt_int(active))
        .replace("__PORTALS_LIST__", html_lib.escape(portals))
        .replace("__PORTALS_COUNT__", portal_n)
        .replace("__TOP_LOCALITIES__", top_html)
    )
    if 'rel="alternate" type="text/markdown"' not in html:
        html = html.replace(
            "</head>",
            '  <link rel="alternate" type="text/markdown" href="https://realitify.cz/home.md" />\n</head>',
            1,
        )
    headers = {"Cache-Control": _PUBLIC_HTML_CACHE}
    if data_as_of:
        headers["X-Data-As-Of"] = data_as_of
    return HTMLResponse(html, headers=headers)


def _ssr_response(page_key: str, *, media_type: str, cache_control: str) -> Response:
    from app import ssr_cache

    page = ssr_cache.get_page(page_key)
    if not page or not page.get("body"):
        raise _ssr_cache_not_ready()
    headers = {
        "Cache-Control": cache_control,
        "X-Data-As-Of": page.get("data_as_of") or "",
    }
    return Response(content=page["body"], media_type=media_type, headers=headers)


def byt_preview() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "byt.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def contact() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "kontakt.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def terms() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "obchodni-podminky.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def privacy() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "ochrana-soukromi.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def privacy_en() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "privacy.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def terms_en() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "terms.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def mcp_docs() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "mcp-docs.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def mcp_docs_cs() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "mcp-docs-cs.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


async def market_index() -> Response:
    return _ssr_response("index:html", media_type="text/html; charset=utf-8", cache_control="public, max-age=1800")


async def market_index_month(month: str) -> Response:
    from app import market_seo

    store = _public_store() or hub.store
    try:
        html = await asyncio.to_thread(market_seo.render_index_month, store, month)
    except ValueError:
        return RedirectResponse("/index", status_code=301)
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=86400"})


async def market_index_csv() -> Response:
    from app import ssr_cache

    page = ssr_cache.get_page("index:csv")
    if not page:
        raise _ssr_cache_not_ready()
    return Response(
        content=page["body"],
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="realitify-index.csv"',
            "Cache-Control": "public, max-age=1800",
            "X-Data-As-Of": page.get("data_as_of") or "",
        },
    )


async def market_faq() -> Response:
    return _ssr_response("faq:html", media_type="text/html; charset=utf-8", cache_control="public, max-age=1800")


async def market_search() -> HTMLResponse:
    from app import market_pages

    return HTMLResponse(market_pages.render_search(), headers={"Cache-Control": "public, max-age=300"})


def about_cs() -> Response:
    return _ssr_response("about:html", media_type="text/html; charset=utf-8", cache_control=_PUBLIC_HTML_CACHE)


def about_en() -> Response:
    return _ssr_response("about_en:html", media_type="text/html; charset=utf-8", cache_control=_PUBLIC_HTML_CACHE)


def llms_txt_route() -> Response:
    return _ssr_response("llms", media_type="text/plain; charset=utf-8", cache_control="public, max-age=3600")


async def llms_full_txt_route() -> Response:
    return _ssr_response("llms_full", media_type="text/plain; charset=utf-8", cache_control="public, max-age=3600")


async def home_md_route() -> Response:
    return _ssr_response("home:md", media_type="text/markdown; charset=utf-8", cache_control="public, max-age=1800")


async def index_md_route() -> Response:
    return _ssr_response("index:md", media_type="text/markdown; charset=utf-8", cache_control="public, max-age=1800")


async def faq_md_route() -> Response:
    return _ssr_response("faq:md", media_type="text/markdown; charset=utf-8", cache_control="public, max-age=1800")


async def about_md_route() -> Response:
    return _ssr_response("about:md", media_type="text/markdown; charset=utf-8", cache_control="public, max-age=1800")


def mcp_docs_md_route() -> Response:
    return _ssr_response("mcp_docs:md", media_type="text/markdown; charset=utf-8", cache_control="public, max-age=3600")


async def trh_hub_md_route() -> Response:
    return RedirectResponse("/hledat", status_code=301)


async def trh_page_md_route(locality: str, offer: str, disposition: str = "") -> Response:
    from app import market_pages, market_seo, ssr_cache

    if offer not in {"pronajem", "prodej"}:
        raise HTTPException(404, "Unknown offer type")
    loc = market_pages.locality_from_slug(locality)
    disp = market_seo.disposition_from_slug(disposition) if disposition else ""
    key = ssr_cache.trh_key(loc, offer, disp) + ":md"
    page = ssr_cache.get_page(key)
    if page and page.get("body"):
        return Response(
            content=page["body"],
            media_type="text/markdown; charset=utf-8",
            headers={"Cache-Control": "public, max-age=1800", "X-Data-As-Of": page.get("data_as_of") or ""},
        )
    raise HTTPException(404, "Not enough data")


async def market_trh_hub() -> Response:
    """The /trh hub was removed; keep old links and bookmarks working."""
    return RedirectResponse("/hledat", status_code=301)


async def market_trh(
    locality: str, offer: str, disposition: str = ""
) -> Response:
    from app import market_pages, market_seo, ssr_cache

    if offer not in {"pronajem", "prodej"}:
        raise HTTPException(404, "Unknown offer type")
    loc = market_pages.locality_from_slug(locality)
    disp = market_seo.disposition_from_slug(disposition) if disposition else ""
    key = ssr_cache.trh_key(loc, offer, disp) + ":html"
    page = ssr_cache.get_page(key)
    if page and page.get("body"):
        return HTMLResponse(
            page["body"],
            headers={"Cache-Control": "public, max-age=1800", "X-Data-As-Of": page.get("data_as_of") or ""},
        )
    redirects = ssr_cache.get_json("redirects") or {}
    path = f"/trh/{market_pages.slugify_locality(loc)}/{offer}"
    if disp:
        path += f"/{market_seo.slugify_disposition(disp)}"
    target = redirects.get(path) or "/hledat"
    return RedirectResponse(target, status_code=301)


async def market_trh_og(locality: str, offer: str, disposition: str = "") -> Response:
    from app import market_pages, market_seo, ssr_cache

    if offer not in {"pronajem", "prodej"}:
        raise HTTPException(404, "Unknown offer type")
    loc = market_pages.locality_from_slug(locality)
    disp = market_seo.disposition_from_slug(disposition) if disposition else ""
    report = ssr_cache.get_json(ssr_cache.trh_key(loc, offer, disp) + ":report")
    if not isinstance(report, dict):
        redirects = ssr_cache.get_json("redirects") or {}
        path = f"/trh/{market_pages.slugify_locality(loc)}/{offer}"
        if disp:
            path += f"/{market_seo.slugify_disposition(disp)}"
        target = redirects.get(path)
        if not target or target == "/hledat":
            raise HTTPException(404, "Not enough data")
        return RedirectResponse(f"{target}/og.png", status_code=301)
    png = await asyncio.to_thread(market_seo.render_og_png, loc, offer, report, disp)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


async def market_trh_page(locality: str, offer: str) -> Response:
    return await market_trh(locality, offer, "")


async def market_trh_disp(locality: str, offer: str, disposition: str) -> Response:
    if disposition == "og.png":
        raise HTTPException(404)
    return await market_trh(locality, offer, disposition)


async def market_trh_og_page(locality: str, offer: str) -> Response:
    return await market_trh_og(locality, offer, "")


async def market_trh_og_disp(locality: str, offer: str, disposition: str) -> Response:
    return await market_trh_og(locality, offer, disposition)


def cookies_page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "nastaveni-cookies.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def stories() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "uspechy.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def games_hub() -> HTMLResponse:
    from app.site_pages import site_page

    return site_page("hry.html")


def game_higher() -> HTMLResponse:
    from app.site_pages import site_page

    return site_page("hry-vyssi-nizsi.html")


def game_rent() -> HTMLResponse:
    from app.site_pages import site_page

    return site_page("hry-najem.html")


def story_article() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "clanek.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def auth_login() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "prihlaseni.html", headers={"Cache-Control": _PRIVATE_HTML_CACHE})


def auth_register() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "registrace.html", headers={"Cache-Control": _PRIVATE_HTML_CACHE})


def auth_verify_page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "overeni.html", headers={"Cache-Control": _PRIVATE_HTML_CACHE})


def auth_forgot() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "heslo.html", headers={"Cache-Control": _PRIVATE_HTML_CACHE})


def seo_landing_pronajem_praha() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "pronajem-praha.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def seo_landing_pronajem_brno() -> FileResponse:
    return FileResponse(config.WEB_DIR / "site" / "pronajem-brno.html", headers={"Cache-Control": _PUBLIC_HTML_CACHE})


def admin_page() -> FileResponse:
    return FileResponse(config.WEB_DIR / "admin" / "index.html", headers={"Cache-Control": _PRIVATE_HTML_CACHE})


# --- SEO: sitemap.xml a robots.txt ---
SEO_PAGES = [
    ("/", "daily", "1.0"),
    ("/index", "daily", "0.9"),
    ("/hledat", "daily", "0.9"),
    ("/faq", "weekly", "0.8"),
    ("/o-nas", "monthly", "0.7"),
    ("/about", "monthly", "0.6"),
    ("/mcp-docs", "weekly", "0.8"),
    ("/mcp-docs-cs", "weekly", "0.7"),
    ("/llms.txt", "monthly", "0.5"),
    ("/pronajem-praha", "weekly", "0.9"),
    ("/pronajem-brno", "weekly", "0.8"),
    ("/byt", "daily", "0.8"),
    ("/registrace", "weekly", "0.9"),
    ("/uspechy", "weekly", "0.7"),
    ("/hry", "weekly", "0.6"),
    ("/hry/vyssi-nizsi", "weekly", "0.5"),
    ("/hry/najem", "weekly", "0.5"),
    ("/kontakt", "monthly", "0.5"),
    ("/privacy", "monthly", "0.4"),
    ("/terms", "monthly", "0.4"),
]


def sitemap_xml() -> Response:
    from app import market_seo

    try:
        store = _public_store() or hub.store
    except Exception:
        store = None
    xml = market_seo.cached_sitemap_xml(store, SEO_PAGES)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def robots_txt() -> Response:
    txt = (
        "User-agent: GPTBot\nAllow: /\n\n"
        "User-agent: OAI-SearchBot\nAllow: /\n\n"
        "User-agent: ChatGPT-User\nAllow: /\n\n"
        "User-agent: ClaudeBot\nAllow: /\n\n"
        "User-agent: Claude-User\nAllow: /\n\n"
        "User-agent: Claude-SearchBot\nAllow: /\n\n"
        "User-agent: PerplexityBot\nAllow: /\n\n"
        "User-agent: Bingbot\nAllow: /\n\n"
        "User-agent: Googlebot\nAllow: /\n\n"
        "User-agent: SeznamBot\nAllow: /\n\n"
        "User-agent: Google-Extended\nAllow: /\n\n"
        "User-agent: Applebot\nAllow: /\n\n"
        "User-agent: Applebot-Extended\nAllow: /\n\n"
        "User-agent: DuckAssistBot\nAllow: /\n\n"
        "User-agent: MistralAI-User\nAllow: /\n\n"
        "User-agent: Meta-ExternalAgent\nAllow: /\n\n"
        "User-agent: Amazonbot\nAllow: /\n\n"
        "User-agent: CCBot\nAllow: /\n\n"
        "User-agent: *\n"
        "Content-Signal: ai-train=yes, search=yes, ai-input=yes\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /e/\n"
        "Disallow: /odhlasit\n"
        "Disallow: /api/\n"
        "Disallow: /prihlaseni\n"
        "Disallow: /heslo\n"
        "Disallow: /nastaveni\n"
        "Disallow: /prehled\n"
        "Disallow: /nabidka\n"
        "Disallow: /monitory\n"
        "Disallow: /filtry\n"
        "Disallow: /zprava\n"
        "Disallow: /nastaveni-cookies\n"
        "\n"
        "Sitemap: https://realitify.cz/sitemap.xml\n"
    )
    # Short TTL: crawlers and scanners cache robots.txt for as long as we allow.
    return Response(content=txt, media_type="text/plain",
                    headers={"Cache-Control": "public, max-age=900"})


def seznam_wmt_verify() -> Response:
    return Response(
        content="sL6S4B2gcz1qTvH5DrKNiusOIprxIMsB\n",
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# --- Watchdog: stav scraperů pro externí monitoring ---
# Volá se jako /api/watchdog?token=... (token = env WATCHDOG_TOKEN).
# Vrací stav všech 10 portálů. Cron na pozadí to kontroluje a hlásí pád.
WATCHDOG_STALE_HOURS = 26  # catalog_daily běží 1x denně + rezerva


async def _watchdog_live_test(portal: str) -> dict:
    """Spustí jeden rychlý scrape test daného portálu a vrátí výsledek."""
    from app.sources import PORTAL_LABELS
    pid = portal.strip().lower()
    if pid not in PORTAL_LABELS:
        return {"ok": False, "error": f"neznámý portál: {portal}"}
    try:
        from app import portal_urls
        mod = portal_urls.MODULES.get(pid)
        if not mod:
            return {"ok": False, "error": "není URL modul"}
        import importlib
        client_map = {
            "annonce": ("app.annonce", "AnnonceClient"),
            "mmreality": ("app.mmreality", "MmrealityClient"),
            "remax": ("app.remax", "RemaxClient"),
            "ulovdomov": ("app.ulovdomov", "UlovdomovClient"),
            "sreality": ("app.sreality", "SrealityClient"),
            "eurobydleni": ("app.eurobydleni", "EurobydleniClient"),
            "realitymix": ("app.realitymix", "RealitymixClient"),
            "realingo": ("app.realingo", "RealingoClient"),
            "espolubydleni": ("app.espolubydleni", "EspolubydleniClient"),
        }
        cm = client_map.get(pid)
        if not cm:
            return {"ok": False, "error": "test není pro tento portál implementován"}
        test_url = None
        try:
            df = mod.default_filters() if hasattr(mod, "default_filters") else {}
            test_url = mod.build_url(df) if hasattr(mod, "build_url") else None
        except Exception:
            pass
        if not test_url:
            return {"ok": False, "error": "nenašlo se testovací URL"}

        m = importlib.import_module(cm[0])
        cls = getattr(m, cm[1])
        c = cls(test_url)
        try:
            # Nejprv surové HTML pro diagnostiku
            html_info = {}
            try:
                resp = await c._client.get(test_url)
                html = resp.text
                html_info = {
                    "http_status": resp.status_code,
                    "html_len": len(html),
                    "box_q_count": html.count("box q"),
                    "has_captcha": "captcha" in html.lower(),
                    "title": (html.split("<title>")[1].split("</title>")[0][:80]
                              if "<title>" in html else "?"),
                }
            except Exception as e:
                html_info = {"html_error": f"{type(e).__name__}: {str(e)[:100]}"}
            listings, total = await c.fetch_page(page=1)
            return {"ok": True, "portal": pid, "url": test_url[:120],
                    "listings": len(listings), "total": total,
                    "html": html_info}
        finally:
            await c.aclose()
    except Exception as e:
        return {"ok": False, "portal": pid,
                "error": f"{type(e).__name__}: {str(e)[:300]}"}

# "Chyby", které nejsou pádem scraperu (očekávané stavy)
WATCHDOG_IGNORED_ERRORS = (
    "portal-disabled",      # circuit breaker - dočasná ochrana, ne pád
    "Incomplete crawl",     # varování o neúplnosti, ne fatální chyba
)


async def watchdog_status(token: str = Query(default=""), detail: str = Query(default=""),
                   test: str = Query(default=""),
                   authorization: str | None = Header(default=None)) -> JSONResponse:
    import os
    expected = os.getenv("WATCHDOG_TOKEN", "").strip()
    # Token v Authorization hlavičce (preferováno) nebo query parametru
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif token:
        provided = token
    if not expected or provided != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    # Živý test jednoho portálu z produkčního serveru
    if test:
        return JSONResponse(await _watchdog_live_test(test))
    from datetime import datetime, timedelta, timezone
    from app.sources import PORTAL_LABELS, PORTAL_ORDER

    now = datetime.now(timezone.utc)
    stale_after = now - timedelta(hours=WATCHDOG_STALE_HOURS)
    portals = []
    down = []
    # Joby a katalog žijí ve storu admin uživatele (per-user DB), ne v globálním hub.store
    astore = _public_store()
    if astore is None:
        raise HTTPException(status_code=503, detail="Store unavailable")
    with astore.connect() as conn:
        for pid in PORTAL_ORDER:
            row = conn.execute(
                "SELECT COUNT(*) n FROM catalog_listings "
                "WHERE portal = ? AND IFNULL(gone, 0) = 0",
                (pid,),
            ).fetchone()
            n = int(row["n"] or 0)
            err_row = conn.execute(
                "SELECT last_error, finished_at, started_at, upserts FROM scrape_jobs "
                "WHERE portal = ? AND kind IN ('catalog_daily', 'catalog_recent') "
                "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 1",
                (pid,),
            ).fetchone()
            last_error = (err_row["last_error"] or "").strip() if err_row else ""
            finished_at = (err_row["finished_at"] or err_row["started_at"]) if err_row else None
            # Listings refreshed by the high-priority discovery tick (catalog_recent shards)
            # don't write scrape_jobs rows, so also check last_seen in catalog_listings.
            seen_row = conn.execute(
                "SELECT MAX(last_seen) m FROM catalog_listings "
                "WHERE portal = ? AND IFNULL(gone, 0) = 0",
                (pid,),
            ).fetchone()
            last_seen = seen_row["m"] if seen_row else None
            if last_seen and (not finished_at or str(last_seen) > str(finished_at)):
                finished_at = last_seen
            try:
                fin_dt = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00")) if finished_at else None
                if fin_dt is not None and fin_dt.tzinfo is None:
                    fin_dt = fin_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                fin_dt = None
            # DOWN pokud: skutečná chyba v posledním jobu, nebo job neběžel > limit
            # Ignorujeme očekávané stavy (circuit breaker, neúplný crawl)
            reasons = []
            is_ignored = any(ign in last_error for ign in WATCHDOG_IGNORED_ERRORS)
            if last_error and not is_ignored:
                reasons.append(f"chyba: {last_error[:120]}")
            if fin_dt is None:
                reasons.append("job nikdy neběžel")
            elif fin_dt < stale_after:
                reasons.append(f"poslední běh: {finished_at}")
            if n == 0 and fin_dt is None:
                reasons.append("0 nabídek v katalogu")
            elif n == 0 and fin_dt is not None and not last_error:
                # Portal ran successfully but has zero active listings —
                # parser is likely broken (site changed HTML structure).
                reasons.append("0 aktivních nabídek po úspěšném běhu (rozbitý parser?)")
            status = "down" if reasons else "ok"
            label = PORTAL_LABELS.get(pid, pid)
            entry = {"id": pid, "name": label, "status": status,
                     "offers": n, "last_run": finished_at, "reasons": reasons}
            if detail == "1":
                shards = conn.execute(
                    "SELECT shard_key, status, last_error, upserts, finished_at FROM scrape_jobs "
                    "WHERE portal = ? AND kind IN ('catalog_daily', 'catalog_recent') "
                    "ORDER BY COALESCE(finished_at, started_at) DESC LIMIT 20",
                    (pid,),
                ).fetchall()
                entry["shards"] = [
                    {"shard": s["shard_key"], "status": s["status"],
                     "error": (s["last_error"] or "")[:200], "upserts": s["upserts"],
                     "finished_at": s["finished_at"]}
                    for s in shards
                ]
            if detail == "errors":
                errs = conn.execute(
                    "SELECT last_error, COUNT(*) AS n, MAX(finished_at) AS last_at "
                    "FROM scrape_jobs WHERE portal = ? AND kind IN ('catalog_daily', 'catalog_recent') "
                    "AND status = 'error' AND last_error IS NOT NULL AND last_error != '' "
                    "AND COALESCE(finished_at, started_at) > datetime('now', '-24 hours') "
                    "GROUP BY last_error ORDER BY n DESC LIMIT 10",
                    (pid,),
                ).fetchall()
                entry["error_breakdown"] = [
                    {"error": (e[0] or "")[:200], "count": e[1],
                     "last_at": e[2]}
                    for e in errs
                ]
            # Důvody circuit breakeru (in-memory stav) pro diagnostiku
            if detail in ("1", "errors"):
                try:
                    from app import portal_health
                    snap = portal_health.snapshot()
                    b = snap.get(pid) or snap.get(pid.lower())
                    if b and b.get("disabled"):
                        entry["breaker"] = {
                            "reason": b.get("reason", ""),
                            "disabled_until": b.get("disabled_until"),
                            "disable_count": b.get("disable_count", 0),
                        }
                except Exception:
                    pass
            # Poslední console řádky pro tento portál (z produkčního logu)
            if detail == "console":
                try:
                    from app import scrape_console
                    data = scrape_console.read_since(0, limit=2000)
                    lines = data.get("lines", [])
                    # lines jsou (offset, text) nebo dict?
                    matched = []
                    for ln in lines:
                        if isinstance(ln, (list, tuple)) and len(ln) >= 2:
                            text = ln[1]
                        elif isinstance(ln, dict):
                            text = ln.get("text", "")
                        else:
                            text = str(ln)
                        if pid in text.lower():
                            matched.append(text[:300])
                    entry["console"] = matched[-15:]
                except Exception as e:
                    entry["console"] = [f"console error: {e}"]
            portals.append(entry)
            if reasons:
                down.append(label)
    return JSONResponse({"ok": not down, "down": down, "portals": portals,
                         "checked_at": now.isoformat()})


def favicon_ico() -> FileResponse:
    return FileResponse(
        config.WEB_DIR / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=604800"},
    )


def favicon_svg() -> FileResponse:
    return FileResponse(
        config.WEB_DIR / "site" / "assets" / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800"},
    )


def apple_touch_icon() -> FileResponse:
    return FileResponse(
        config.WEB_DIR / "site" / "assets" / "apple-touch-icon.png",
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800"},
    )


app.add_api_route("/favicon.ico", favicon_ico, methods=["GET"], include_in_schema=False)
app.add_api_route("/favicon.svg", favicon_svg, methods=["GET"], include_in_schema=False)
app.add_api_route("/apple-touch-icon.png", apple_touch_icon, methods=["GET"], include_in_schema=False)
agent_discovery.install(app)
app.add_api_route("/sitemap.xml", sitemap_xml, methods=["GET"], include_in_schema=False)
app.add_api_route("/robots.txt", robots_txt, methods=["GET"], include_in_schema=False)
app.add_api_route("/llms.txt", llms_txt_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/llms-full.txt", llms_full_txt_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/home.md", home_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/index.md", index_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/faq.md", faq_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/o-nas.md", about_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/mcp-docs.md", mcp_docs_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/trh.md", trh_hub_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route("/trh/{locality}/{offer}.md", trh_page_md_route, methods=["GET"], include_in_schema=False)
app.add_api_route(
    "/trh/{locality}/{offer}/{disposition}.md",
    trh_page_md_route,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route(
    "/seznam-wmt-sL6S4B2gcz1qTvH5DrKNiusOIprxIMsB.txt",
    seznam_wmt_verify,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route("/api/watchdog", watchdog_status, methods=["GET"], include_in_schema=False)
app.add_api_route("/pronajem-praha", seo_landing_pronajem_praha, methods=["GET"], include_in_schema=False)
app.add_api_route("/pronajem-brno", seo_landing_pronajem_brno, methods=["GET"], include_in_schema=False)
app.add_api_route("/", landing, methods=["GET"], include_in_schema=False)
app.add_api_route("/byt", byt_preview, methods=["GET"], include_in_schema=False)
app.add_api_route("/kontakt", contact, methods=["GET"], include_in_schema=False)
app.add_api_route("/obchodni-podminky", terms, methods=["GET"], include_in_schema=False)
app.add_api_route("/ochrana-soukromi", privacy, methods=["GET"], include_in_schema=False)
app.add_api_route("/privacy", privacy_en, methods=["GET"], include_in_schema=False)
app.add_api_route("/terms", terms_en, methods=["GET"], include_in_schema=False)
app.add_api_route("/mcp-docs", mcp_docs, methods=["GET"], include_in_schema=False)
app.add_api_route("/mcp-docs-cs", mcp_docs_cs, methods=["GET"], include_in_schema=False)
app.add_api_route("/index", market_index, methods=["GET"], include_in_schema=False)
app.add_api_route("/index.csv", market_index_csv, methods=["GET"], include_in_schema=False)
app.add_api_route("/index/{month}", market_index_month, methods=["GET"], include_in_schema=False)
app.add_api_route("/faq", market_faq, methods=["GET"], include_in_schema=False)
app.add_api_route("/hledat", market_search, methods=["GET"], include_in_schema=False)
app.add_api_route("/o-nas", about_cs, methods=["GET"], include_in_schema=False)
app.add_api_route("/about", about_en, methods=["GET"], include_in_schema=False)
app.add_api_route("/trh", market_trh_hub, methods=["GET"], include_in_schema=False)
app.add_api_route("/trh/{locality}/{offer}/og.png", market_trh_og_page, methods=["GET"], include_in_schema=False)
app.add_api_route(
    "/trh/{locality}/{offer}/{disposition}/og.png",
    market_trh_og_disp,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route("/trh/{locality}/{offer}", market_trh_page, methods=["GET"], include_in_schema=False)
app.add_api_route(
    "/trh/{locality}/{offer}/{disposition}",
    market_trh_disp,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route("/nastaveni-cookies", cookies_page, methods=["GET"], include_in_schema=False)
app.add_api_route("/uspechy", stories, methods=["GET"], include_in_schema=False)
app.add_api_route("/uspechy/{slug}", story_article, methods=["GET"], include_in_schema=False)
app.add_api_route("/hry", games_hub, methods=["GET"], include_in_schema=False)
app.add_api_route("/hry/vyssi-nizsi", game_higher, methods=["GET"], include_in_schema=False)
app.add_api_route("/hry/najem", game_rent, methods=["GET"], include_in_schema=False)
app.add_api_route("/prihlaseni", auth_login, methods=["GET"], include_in_schema=False)
app.add_api_route("/registrace", auth_register, methods=["GET"], include_in_schema=False)
app.add_api_route("/heslo", auth_forgot, methods=["GET"], include_in_schema=False)
app.add_api_route("/overeni", auth_verify_page, methods=["GET"], include_in_schema=False)
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


def _cookie_secure() -> bool:
    # Secure flag jen přes HTTPS (produkce); lokální HTTP dev bez něj.
    return (config.PUBLIC_BASE_URL or "").startswith("https://")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        user_account.SESSION_COOKIE,
        token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def _client_ip(request: Request) -> str:
    # Za reverzní proxy (Coolify/Traefik) bereme X-Forwarded-For.
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_or_429(request: Request, scope: str, key: str, limit: int, window_s: float) -> None:
    if not rate_limit.check(scope, f"{_client_ip(request)}:{key}", limit, window_s):
        raise HTTPException(429, "Příliš mnoho pokusů, zkuste to prosím později")


def _current_user(session: str | None) -> dict[str, Any]:
    user = user_account.user_from_session(hub.store, session)
    if not user:
        raise HTTPException(401, "Nejste přihlášeni")
    return user


_admin_id_cache: dict[str, Any] = {"id": None, "at": 0.0}


def _admin_user_id() -> str | None:
    """ID of the admin user (cached 60 s). Used for public data + admin panel."""
    now = time.monotonic()
    cached = _admin_id_cache.get("id")
    if cached and now - float(_admin_id_cache.get("at") or 0) < 60:
        return str(cached)
    user_id: str | None
    try:
        user_id = user_registry.catalog_owner_id(hub.store)
    except Exception:
        user_id = None
    _admin_id_cache["id"] = user_id
    _admin_id_cache["at"] = now
    return user_id


def _public_store() -> Store | None:
    """Store backing public pages: the admin's data (listing previews, games)."""
    admin_id = _admin_user_id()
    return hub.store_for(admin_id) if admin_id else None


def _user_store(session: str | None) -> tuple[dict[str, Any], Store]:
    """Resolve (user, private store) for a session cookie; 401 when not logged in."""
    user = _current_user(session)
    return user, hub.store_for(str(user.get("id")))


def _store_for_session(session: str | None) -> tuple[dict[str, Any] | None, Store | None]:
    """(user|None, store): logged-in user's store, else the public (admin) store."""
    user = user_account.user_from_session(hub.store, session)
    if user:
        return user, hub.store_for(str(user.get("id")))
    return None, _public_store()

def _catalog_reader(user: dict[str, Any] | None, ustore: Store, payload: dict[str, Any] | None = None) -> Store:
    """Store to run a catalog read on.

    Logged-in users read the shared catalog of the owner (admin) store with their own hidden/saved/
    hit state (`Store.scoped_for`). Queries about the user's own monitor hits stay on their store.
    """
    if user is None or not config.SHARED_CATALOG:
        return ustore
    owner = _public_store()
    if owner is None or str(owner.path) == str(ustore.path):
        return ustore
    p = payload or {}
    if str(p.get("monitor_id") or "").strip() or str(p.get("hits") or "").strip():
        return ustore
    return _shared_catalog_for(ustore) or ustore


def _shared_catalog_for(ustore: Store) -> Store | None:
    """Owner catalog scoped to this user's store (None when the user's own store is the catalog)."""
    if not config.SHARED_CATALOG:
        return None
    owner = _public_store()
    if owner is None or str(owner.path) == str(ustore.path):
        return None
    return owner.scoped_for(ustore)


def _catalog_store(session: str | None, guest: str | None) -> tuple[dict[str, Any] | None, Store]:
    """Store for read-only catalog endpoints: the user's own, or the public store for e-mail guests."""
    user = user_account.user_from_session(hub.store, session)
    if user:
        return user, hub.store_for(str(user.get("id")))
    if guest_access.verify_token(guest):
        return None, _public_store() or hub.store
    raise HTTPException(401, "Nejste přihlášeni")

def _fire_discord_ping(webhook_url: str, content: str) -> None:
    """Odešle Discord zprávu na pozadí — nikdy neblokuje request ani nevyhazuje výjimku ven."""
    from app import discord_notify

    async def _runner() -> None:
        try:
            await discord_notify.send_simple(webhook_url, content)
        except Exception as exc:
            print(f"[discord-ping] selhalo: {exc}")

    asyncio.create_task(_runner())


@app.post("/api/guest/access")
async def guest_access_grant(request: Request, payload: dict[str, Any] = Body(...)) -> JSONResponse:
    email = str(payload.get("email") or "").strip()
    if not guest_access.valid_email(email):
        raise HTTPException(400, "Zadejte platný e-mail")
    _rate_limit_or_429(request, "guest", "access", 12, 3600.0)
    email_norm = email.strip().lower()
    existing = await asyncio.to_thread(guest_access.list_leads, 10000)
    is_new = email_norm not in {str(r.get("email") or "").strip().lower() for r in existing}
    await asyncio.to_thread(
        guest_access.add_lead,
        email,
        ip=_client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    if is_new and config.DISCORD_LEADS_WEBHOOK_URL:
        _fire_discord_ping(config.DISCORD_LEADS_WEBHOOK_URL, f"📧 Nový lead: {email_norm} (zdroj: /hledat)")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        guest_access.COOKIE,
        guest_access.issue_token(email),
        httponly=True,
        secure=_cookie_secure(),
        samesite="lax",
        max_age=guest_access.MAX_AGE_S,
        path="/",
    )
    return response


@app.get("/api/guest/status")
async def guest_status(
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
) -> JSONResponse:
    user = user_account.user_from_session(hub.store, realitify_session)
    unlocked = bool(user) or guest_access.verify_token(realitify_guest)
    return JSONResponse({"unlocked": unlocked, "member": bool(user)}, headers={"Cache-Control": "no-store"})


@app.get("/api/admin/guest-leads")
async def admin_guest_leads(
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    await _admin_user_async(realitify_admin)
    rows = await asyncio.to_thread(guest_access.list_leads)
    return {"count": len(rows), "items": rows}


async def guest_catalog_page(
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
) -> Response:
    user = user_account.user_from_session(hub.store, realitify_session)
    if not user and not guest_access.verify_token(realitify_guest):
        return RedirectResponse("/hledat", status_code=302)
    html = (config.WEB_DIR / "guest-catalog.html").read_text(encoding="utf-8")
    html = html.replace("__ASSET_VERSION__", _asset_version())
    return HTMLResponse(html, headers={"Cache-Control": "private, no-store"})


app.add_api_route("/hledat/katalog", guest_catalog_page, methods=["GET"], include_in_schema=False)


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
async def auth_register_api(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _rate_limit_or_429(request, "auth_register", "ip", config.REGISTER_LIMIT_PER_HOUR, 3600)
    body = payload or {}
    if body.get("terms") is not True:
        raise HTTPException(400, "Pro registraci je nutný souhlas s obchodními podmínkami.")
    try:
        user, token = await _auth_db(
            user_account.register,
            hub.store,
            str(body.get("name") or ""),
            str(body.get("email") or ""),
            str(body.get("password") or ""),
            str(body.get("promo") or body.get("promo_code") or ""),
            hub.store_for,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not user.get("email_verified"):
        # Do not make the sign-up wait for SMTP; "send again" reports failures explicitly.
        asyncio.create_task(_send_verification_email(request, user), name="send-verification")
    required = await _auth_db(email_store.verification_required)
    response = JSONResponse({**user, "verification_sent": True, "email_verification_required": required})
    _set_session_cookie(response, token)
    try:
        site_stats.track(hub.store, site_stats.KIND_SIGNUP, path="/registrace")
    except Exception:
        pass
    if config.DISCORD_SIGNUPS_WEBHOOK_URL:
        _name = str(user.get("name") or "").strip()
        _mail = str(user.get("email") or "").strip()
        _who = f"{_name} <{_mail}>" if _name else _mail
        _fire_discord_ping(config.DISCORD_SIGNUPS_WEBHOOK_URL, f"👤 Nová registrace: {_who}")
    return response


@app.post("/api/auth/login")
async def auth_login_api(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    email_key = str(body.get("email") or "").strip().lower()
    _rate_limit_or_429(request, "auth_login_ip", "ip", 30, 60)
    if email_key:
        _rate_limit_or_429(request, "auth_login_email", email_key, 10, 60)
    try:
        user, token = await _auth_db(
            user_account.login,
            hub.store,
            str(body.get("email") or ""),
            str(body.get("password") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    required = await _auth_db(email_store.verification_required)
    response = JSONResponse({**user, "email_verification_required": required})
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/forgot")
async def auth_forgot_api(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _rate_limit_or_429(request, "auth_forgot", "ip", 5, 3600)
    body = payload or {}
    email = str(body.get("email") or "").strip().lower()
    token = await _auth_db(user_account.create_password_reset, hub.store, email)
    if token:
        if not mail_notify.configured():
            raise HTTPException(503, "Odesílání e-mailů není nastavené (SMTP_HOST / SMTP_FROM)")
        base = (config.PUBLIC_BASE_URL or "").rstrip("/") or "https://realitify.cz"
        reset_url = f"{base}/heslo?token={token}"
        target = await _auth_db(user_registry.get_user_by_email, hub.store, email)
        try:
            log_id = await asyncio.to_thread(email_triggers.on_password_reset_requested, target or email, reset_url)
            row = await asyncio.to_thread(email_store.log_get, log_id) if log_id else None
        except Exception as exc:
            raise HTTPException(502, f"E-mail se nepodařilo odeslat: {exc}") from exc
        if not row or row["status"] != "sent":
            raise HTTPException(502, f"E-mail se nepodařilo odeslat: {(row or {}).get('error') or 'neznámá chyba'}")
    # Stejná odpověď i když účet neexistuje — neprozrazujeme e-maily.
    return {"ok": True}


@app.get("/api/auth/reset")
async def auth_reset_check(token: str = "") -> dict:
    ok = await _auth_db(user_account.password_reset_ok, hub.store, token)
    if not ok:
        raise HTTPException(400, "Odkaz pro obnovení hesla je neplatný nebo vypršel")
    return {"ok": True}


@app.post("/api/auth/verify/resend")
async def auth_verify_resend(request: Request, realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    """Send the confirmation e-mail again to the signed-in, still unverified user."""
    user = _current_user(realitify_session)
    _rate_limit_or_429(request, "verify_resend", f"{_client_ip(request)}:{user['id']}", 5, 3600)
    if user.get("email_verified"):
        return {"ok": True, "already": True}
    sent = await _send_verification_email(request, user)
    if not sent:
        raise HTTPException(503, "E-mail se teď nepodařilo odeslat. Zkuste to prosím za chvíli.")
    return {"ok": True}


@app.get("/api/auth/verify")
async def auth_verify_email(
    token: str = "",
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    if config.DEV_VERIFY_CODE and token.strip() == config.DEV_VERIFY_CODE:
        current = await _auth_db(user_account.user_from_session, hub.store, realitify_session)
        if not current:
            raise HTTPException(400, "Nejdřív se přihlaste, pak zadejte kód.")
        await _auth_db(user_registry.mark_email_verified, hub.store, str(current["id"]))
        return {"ok": True}
    user = await _auth_db(user_registry.verify_email_with_token, hub.store, token)
    if not user:
        raise HTTPException(400, "Ověřovací odkaz je neplatný nebo vypršel")
    asyncio.create_task(asyncio.to_thread(email_triggers.on_verified, user), name="email-on-verified")
    return {"ok": True}


@app.post("/api/auth/reset")
async def auth_reset_api(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _rate_limit_or_429(request, "auth_reset", "ip", 20, 60)
    body = payload or {}
    try:
        user, session = await _auth_db(
            user_account.reset_password_with_token,
            hub.store,
            str(body.get("token") or ""),
            str(body.get("password") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = JSONResponse(user)
    _set_session_cookie(response, session)
    return response


@app.post("/api/auth/logout")
async def auth_logout_api(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    await _auth_db(user_account.clear_session, hub.store, realitify_session)
    response = JSONResponse({"ok": True})
    response.delete_cookie(user_account.SESSION_COOKIE, path="/")
    return response


@app.get("/api/auth/me")
async def auth_me(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    user = await _auth_db(_current_user, realitify_session)
    required = await _auth_db(email_store.verification_required)
    return {
        **user,
        "email_alerts_paused": bool(config.EMAIL_ONLY_TRANSACTIONAL or config.EMAIL_ALERTS_PAUSED),
        "email_verification_required": required,
        "dev_verify_code": bool(config.DEV_VERIFY_CODE),
    }


@app.get("/api/admin/settings/email-verification")
async def admin_email_verification_get(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
    return await asyncio.to_thread(email_store.verification_setting)


@app.post("/api/admin/settings/email-verification")
async def admin_email_verification_set(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    admin = await _admin_user_async(realitify_admin)
    body = payload or {}
    if not isinstance(body.get("required"), bool):
        raise HTTPException(400, "Chybí pole required (true/false)")
    by = str(admin.get("email") or admin.get("name") or "admin")
    return await asyncio.to_thread(email_store.set_verification_required, body["required"], by)


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
    ustore = hub.store_for(str(user.get("id"))) if user else None
    account = ext_score.extension_account(ustore or hub.store)
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
    if not user:
        raise HTTPException(401, "Nejste přihlášeni")
    ustore = hub.store_for(str(user.get("id")))
    account = ext_score.extension_account(ustore)
    if not account.get("active"):
        raise HTTPException(403, "Aktivní předplatné Start nebo PRO je povinné")
    body = payload or {}
    ids = body.get("ids") if isinstance(body.get("ids"), list) else []
    urls = body.get("urls") if isinstance(body.get("urls"), list) else []
    result = ext_score.score_batch(ustore, ids=[str(x) for x in ids], urls=[str(x) for x in urls])
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
    if not user:
        raise HTTPException(401, "Nejste přihlášeni")
    ustore = hub.store_for(str(user.get("id")))
    account = ext_score.extension_account(ustore)
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
            from app.places import refine_listing_location_async

            await refine_listing_location_async(listing)
            await asyncio.to_thread(ustore.upsert_catalog_listing, listing, kind="extension")
            if listing.id:
                ext_score.invalidate_scores(str(listing.id))
                ingested.append(str(listing.id))
            elif native:
                ext_score.invalidate_scores(native)
                ingested.append(native)
        except ListingGone:
            errors[native or url] = "gone"
        except Exception as exc:
            logger.warning("Extension ingest selhal pro %s: %s", native or url, exc)
            errors[native or url] = public_error(exc, "Nepodařilo se načíst nabídku.")[:200]

    await asyncio.gather(*[_one(url) for url in cleaned])

    score_ids = list(dict.fromkeys(ingested + [ext_score.extract_sreality_id(u) for u in cleaned if ext_score.extract_sreality_id(u)]))
    result = ext_score.score_batch(ustore, ids=score_ids, urls=cleaned)
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
    user, ustore = _user_store(realitify_session)
    body = payload or {}
    try:
        return user_account.update_profile(
            hub.store,
            ustore,
            str(user.get("id")),
            str(body.get("first") or ""),
            str(body.get("last") or ""),
            str(body.get("phone") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/auth/password")
async def auth_password(request: Request, payload: dict[str, Any] | None = Body(None), realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    user = _current_user(realitify_session)
    body = payload or {}
    try:
        token = user_account.change_password(hub.store, str(user.get("id")), str(body.get("current") or ""), str(body.get("new") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    email_triggers.on_password_changed(user, f"{_site_base(request)}/heslo")
    response = JSONResponse({"ok": True})
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/email")
async def auth_change_email(request: Request, payload: dict[str, Any] | None = Body(None), realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    """Change the login e-mail (password required). The new address has to be confirmed."""
    user = _current_user(realitify_session)
    body = payload or {}
    _rate_limit_or_429(request, "auth_email", f"{_client_ip(request)}:{user['id']}", 5, 3600)
    old_email = str(user.get("email") or "")
    if not await _auth_db(user_registry.verify_user_password, hub.store, old_email, str(body.get("password") or "")):
        raise HTTPException(400, "Heslo nesedí")
    try:
        changed = await _auth_db(user_registry.change_email, hub.store, str(user["id"]), str(body.get("email") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ustore = hub.store_for(str(user["id"]))
    record = user_account.account_record(ustore)
    record.update({"email": changed["email"], "email_verified": False})
    user_account.save_account(ustore, record)
    token = await _auth_db(user_registry.create_email_verification, hub.store, str(user["id"]))
    verify_url = f"{_site_base(request)}/overeni?token={token}"
    email_triggers.on_email_changed(changed, old_email, changed["email"], verify_url)
    return {"ok": True, "email": changed["email"], "email_verified": False}


@app.post("/api/auth/delete")
async def auth_delete_account(request: Request, payload: dict[str, Any] | None = Body(None), realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    """Delete the account for good (password required): cancels billing, removes data, sends the confirmation."""
    user = _current_user(realitify_session)
    body = payload or {}
    _rate_limit_or_429(request, "auth_delete", f"{_client_ip(request)}:{user['id']}", 5, 3600)
    if not await _auth_db(user_registry.verify_user_password, hub.store, str(user.get("email") or ""), str(body.get("password") or "")):
        raise HTTPException(400, "Heslo nesedí")
    if user.get("role") == "admin" or user_registry.is_admin(user):
        raise HTTPException(400, "Účet administrátora nelze smazat")
    uid = str(user["id"])
    ustore = hub.store_for(uid)
    try:
        stripe_billing._cancel_stripe_now(ustore, ustore.billing_record() or {})
    except Exception:
        logger.exception("Zrušení předplatného při mazání účtu selhalo")
    await _auth_db(user_registry.delete_user, hub.store, uid)
    hub.drop_store_cache(uid)
    asyncio.create_task(asyncio.to_thread(email_triggers.on_account_deleted, user), name="email-account-deleted")
    response = JSONResponse({"ok": True})
    response.delete_cookie(user_account.SESSION_COOKIE, path="/")
    return response


def _admin_user(token: str | None) -> dict[str, Any]:
    user = admin_panel.admin_from_cookie(hub.store, token)
    if not user:
        raise HTTPException(401, "Nejste přihlášeni do administrace")
    return user


def _admin_store(token: str | None) -> tuple[dict[str, Any], Store]:
    """Admin user + their private store (the store the admin panel operates on)."""
    user = _admin_user(token)
    return user, hub.store_for(str(user.get("id")))


# Admin auth reads the (write-contended) global DB and the first hub.store_for() builds the big catalog Store:
# never do that on the event loop, and keep it off the pools that serve users (login, catalog).
_ADMIN_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="rf-admin")


async def _admin_user_async(token: str | None) -> dict[str, Any]:
    return await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, _admin_user, token)


async def _admin_store_async(token: str | None) -> tuple[dict[str, Any], Store]:
    return await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, _admin_store, token)


app.include_router(email_admin_api.build_router(_admin_user, hub.store_for))


@app.post("/api/ses/webhook", include_in_schema=False)
async def ses_webhook(request: Request, token: str = "") -> JSONResponse:
    """Amazon SNS -> hard bounces and complaints go to the suppression list."""
    body = await request.body()
    if len(body) > 1_000_000:
        return JSONResponse({"detail": "too large"}, status_code=413)
    status, payload = await asyncio.to_thread(email_ses.process_request, body, token)
    return JSONResponse(payload, status_code=status)


@app.get("/e/o/{token}.gif", include_in_schema=False)
async def email_open_pixel(token: str) -> Response:
    await asyncio.to_thread(email_tracking.record_open, token)
    return Response(email_tracking.PIXEL_GIF, media_type="image/gif", headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/e/c/{token}", include_in_schema=False)
async def email_click_redirect(token: str, u: str = "") -> Response:
    target = await asyncio.to_thread(email_tracking.resolve_click, token, u)
    return RedirectResponse(target or "/", status_code=302)


@app.get("/e/a/{token}", include_in_schema=False)
async def email_action_link(token: str, a: str = "", m: str = "") -> Response:
    user_id = email_tracking.parse_action(token, a, m)
    if not user_id:
        return HTMLResponse(email_tracking.page("Odkaz je neplatný", "<p>Tenhle odkaz už neplatí.</p>"), status_code=400)
    message = await asyncio.to_thread(email_triggers.handle_action, user_id, a, hub.store_for, m)
    import html as _html

    return HTMLResponse(email_tracking.page("Hotovo", f"<p>{_html.escape(message)}</p>"), headers={"Cache-Control": "no-store"})


@app.get("/odhlasit", include_in_schema=False)
async def email_unsubscribe_page(token: str = "", kind: str = "") -> Response:
    if kind == "alerts":
        if not email_tracking.parse_alerts_unsubscribe_token(token):
            return HTMLResponse(email_tracking.page("Odkaz je neplatný", "<p>Tenhle odkaz už neplatí.</p>"), status_code=400)
        return HTMLResponse(email_tracking.alerts_unsubscribe_page(token), headers={"Cache-Control": "no-store"})
    if not email_tracking.parse_unsubscribe_token(token):
        return HTMLResponse(email_tracking.page("Odkaz je neplatný", "<p>Tenhle odkaz už neplatí.</p>"), status_code=400)
    return HTMLResponse(email_tracking.unsubscribe_page(token), headers={"Cache-Control": "no-store"})


@app.post("/odhlasit", include_in_schema=False)
async def email_unsubscribe_submit(request: Request, token: str = "", kind: str = "") -> Response:
    if kind == "alerts":
        alerts_uid = email_tracking.parse_alerts_unsubscribe_token(token)
        if not alerts_uid:
            return HTMLResponse(email_tracking.page("Odkaz je neplatný", "<p>Tenhle odkaz už neplatí.</p>"), status_code=400)
        form = await request.form()
        back = str(form.get("action") or "") == "resubscribe"
        await asyncio.to_thread(lambda: hub.store_for(alerts_uid).save_notify_prefs({"email": back}))
        return HTMLResponse(email_tracking.alerts_unsubscribe_page(token, done=not back, resubscribed=back))
    user_id = email_tracking.parse_unsubscribe_token(token)
    if not user_id:
        return HTMLResponse(email_tracking.page("Odkaz je neplatný", "<p>Tenhle odkaz už neplatí.</p>"), status_code=400)
    form = await request.form()
    resubscribe = str(form.get("action") or "") == "resubscribe"
    lead = email_tracking.lead_email(user_id)
    if lead:
        await asyncio.to_thread(email_store.unsuppress if resubscribe else (lambda a: email_store.suppress(a, "unsubscribed")), lead)
    else:
        await _auth_db(user_registry.set_marketing_unsub, hub.store, user_id, not resubscribe)
    return HTMLResponse(email_tracking.unsubscribe_page(token, done=not resubscribe, resubscribed=resubscribe))


@app.post("/api/admin/login")
async def admin_login_api(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _rate_limit_or_429(request, "admin_login", "ip", 10, 60)
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
        secure=_cookie_secure(),
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )
    return response


@app.post("/api/admin/logout")
async def admin_logout_api(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    user_registry.delete_session(hub.store, realitify_admin)
    response = JSONResponse({"ok": True})
    response.delete_cookie(admin_panel.ADMIN_COOKIE, path="/")
    return response


_ADMIN_WARM = {"at": 0.0, "busy": False}


def _warm_admin_pages() -> None:
    """The admin opens the panel -> compute the heavy pages once in the background (one thread, at most every 5 min),
    so the first click on Users / Overview / Operations is already served from the cache."""
    now = time.time()
    if _ADMIN_WARM["busy"] or now - float(_ADMIN_WARM["at"]) < 300:
        return
    _ADMIN_WARM.update(busy=True, at=now)

    def run() -> None:
        try:
            admin_id = _admin_user_id()
            astore = hub.store_for(admin_id) if admin_id else None
            steps = [("users", lambda: admin_panel.users_payload(hub.store, hub.store_for))]
            if astore is not None:
                steps += [
                    ("overview", lambda: admin_panel.overview_payload(astore, hub)),
                    ("monitors", lambda: admin_panel.monitors_payload(astore, hub.store, hub.store_for)),
                ]
            for key, loader in steps:
                try:
                    admin_panel.cached_payload(key, loader)
                except Exception as exc:
                    print(f"[admin] warm {key}: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(0.2)  # one page at a time, never a burst
            if astore is not None:
                try:
                    admin_panel.ops_payload_cached(astore, hub)
                except Exception as exc:
                    print(f"[admin] warm ops: {type(exc).__name__}: {exc}", flush=True)
        finally:
            _ADMIN_WARM["busy"] = False

    threading.Thread(target=run, name="admin-warm", daemon=True).start()


@app.get("/api/admin/me")
async def admin_me(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    user = await _admin_user_async(realitify_admin)
    _warm_admin_pages()
    return user


@app.get("/api/admin/overview")
async def admin_overview(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    return await asyncio.to_thread(admin_panel.cached_payload, "overview", lambda: admin_panel.overview_payload(astore, hub))


@app.get("/api/admin/users")
async def admin_users(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
    return await asyncio.to_thread(admin_panel.cached_payload, "users", lambda: admin_panel.users_payload(hub.store, hub.store_for))


@app.post("/api/admin/users/vocatives")
async def admin_set_vocatives(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    """Hand-entered 5th case of first names (greeting in e-mails): {"items": [{"id", "vocative"}]}."""
    await _admin_user_async(realitify_admin)
    items = (payload or {}).get("items") or []
    values = {str(i.get("id") or ""): str(i.get("vocative") or "") for i in items if isinstance(i, dict) and i.get("id")}
    changed = await asyncio.to_thread(user_registry.set_vocatives, hub.store, values)
    admin_panel.invalidate_payloads()
    return {"ok": True, "changed": changed}


@app.get("/api/admin/users/{user_id}")
async def admin_user_detail(
    user_id: str,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    await _admin_user_async(realitify_admin)
    target = await asyncio.to_thread(user_registry.get_user_by_id, hub.store, user_id)
    if not target:
        raise HTTPException(404, "Uživatel neexistuje")
    try:
        return await asyncio.to_thread(
            lambda: admin_panel.user_detail_payload(hub.store_for(str(target.get("id"))), target, hub.store)
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/admin/monitors")
async def admin_monitors(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    loop = asyncio.get_running_loop()
    _, astore = await loop.run_in_executor(hub.auth_pool, _admin_store, realitify_admin)
    return await loop.run_in_executor(
        _ADMIN_POOL, admin_panel.cached_payload, "monitors", lambda: admin_panel.monitors_payload(astore, hub.store, hub.store_for)
    )


@app.get("/api/admin/notifications")
async def admin_notifications(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    return await asyncio.to_thread(
        admin_panel.cached_payload, "notifications", lambda: admin_panel.notifications_payload(astore, hub.store, hub.store_for)
    )


@app.post("/api/admin/broadcast")
async def admin_broadcast(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user, astore = await _admin_store_async(realitify_admin)
    targets = await asyncio.to_thread(
        lambda: [(str(u.get("id")), hub.store_for(str(u.get("id")))) for u in user_registry.list_users(hub.store) if u.get("id")]
    )
    result = await admin_panel.send_broadcast(astore, payload or {}, user.get("name") or "Admin", user_stores=targets)
    admin_panel.invalidate_payloads()
    return result


@app.get("/api/admin/ops")
async def admin_ops(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await asyncio.to_thread(_admin_store, realitify_admin)
    return await asyncio.to_thread(admin_panel.ops_payload_cached, astore, hub)


@app.get("/api/admin/scrape-progress")
async def admin_scrape_progress(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await asyncio.to_thread(_admin_store, realitify_admin)
    return await asyncio.to_thread(admin_panel.catalog_progress_payload, astore, hub)


@app.get("/api/admin/scrape-console")
async def admin_scrape_console(
    after: int = 0,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    from app import scrape_console

    await asyncio.to_thread(_admin_user, realitify_admin)
    return await asyncio.to_thread(scrape_console.read_since, max(0, int(after or 0)))


@app.post("/api/admin/scrape-console/clear")
async def admin_scrape_console_clear(
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    from app import scrape_console

    await asyncio.to_thread(_admin_user, realitify_admin)
    await asyncio.to_thread(scrape_console.clear)
    return {"ok": True, "offset": 0}


@app.get("/api/admin/games")
async def admin_games(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    from app import games as marketing_games

    await asyncio.to_thread(_admin_user, realitify_admin)
    # Rounds are saved by public_game_rent_score() into the public (admin) store, not the global one.
    return await asyncio.to_thread(marketing_games.leaderboard, _public_store() or hub.store)


@app.post("/api/admin/scrape-url")
async def admin_scrape_url(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
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
        return {"result": result, "ops": await asyncio.to_thread(admin_panel.ops_payload, astore, hub)}

    if scope == "portal":
        portal = str(body.get("portal") or "").strip().lower()
        if portal in {"", "all", "*"}:
            result = hub.start_catalog_sync()
        elif portal not in config.CATALOG_SYNC_HOURS:
            raise HTTPException(400, "Neznámý portál")
        else:
            result = hub.start_catalog_sync(portals=[portal])
        if not result.get("ok") and result.get("reason") != "already-running":
            raise HTTPException(409, str(result.get("reason") or "Katalog sync selhal"))
        return {"result": result, "ops": await asyncio.to_thread(admin_panel.ops_payload, astore, hub)}

    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "Chybí URL hledání")
    result = hub.start_scrape_search_url(url, max_pages=max_pages)
    if not result.get("ok"):
        raise HTTPException(409 if "locked" in str(result.get("error") or "").lower() else 400, str(result.get("error") or "Scrape selhal"))
    return {"result": result, "ops": await asyncio.to_thread(admin_panel.ops_payload, astore, hub)}


@app.delete("/api/admin/scrape-schedule/{job_id}")
async def admin_scrape_schedule_delete(
    job_id: str,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    ok = await asyncio.to_thread(hub.store.remove_scrape_schedule, job_id)
    if not ok:
        raise HTTPException(404, "Naplánovaný scrape nenalezen")
    return {"ok": True, "ops": await asyncio.to_thread(admin_panel.ops_payload, astore, hub)}


@app.get("/api/admin/dedupe")
async def admin_dedupe(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    return await asyncio.to_thread(admin_panel.dedupe_payload, astore, hub)


@app.post("/api/admin/dedupe/schedule")
async def admin_dedupe_schedule(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    body = payload or {}
    try:
        hour = int(body.get("hour", 3))
    except (TypeError, ValueError):
        hour = 3
    hub.store.save_dedupe_schedule(enabled=bool(body.get("enabled")), hour=hour)
    return await asyncio.to_thread(admin_panel.dedupe_payload, astore, hub)


@app.post("/api/admin/dedupe/run")
async def admin_dedupe_run(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    result = hub.start_dedupe()
    if not result.get("ok"):
        raise HTTPException(409, "Deduplikace už běží")
    return await asyncio.to_thread(admin_panel.dedupe_payload, astore, hub)


@app.post("/api/admin/dedupe/scan")
async def admin_dedupe_scan(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    result = hub.start_dedupe_scan()
    if not result.get("ok"):
        raise HTTPException(409, "Deduplikace už běží")
    return await asyncio.to_thread(admin_panel.dedupe_payload, astore, hub)


_CATALOG_QUALITY_CACHE: dict[int, dict[str, Any]] = {}
_CATALOG_QUALITY_TTL = 300.0


@app.get("/api/admin/catalog-quality")
async def admin_catalog_quality_dry_run(
    max_age_hours: int = 72,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    """Dry-run counts via hub job_pool Store connection (not a side-process sqlite)."""
    await _admin_user_async(realitify_admin)
    hours = max(24, min(720, int(max_age_hours or 72)))
    # The dry run json-decodes every live catalog row (seconds locally, far more in
    # production), so serve a cached result and recompute in the background.
    entry = _CATALOG_QUALITY_CACHE.setdefault(hours, {"at": 0.0, "data": None, "task": None})
    if entry["data"] is not None and time.time() - entry["at"] < _CATALOG_QUALITY_TTL:
        return {**entry["data"], "cached": True}
    task = entry["task"]
    if task is None or task.done():
        async def _refresh() -> dict:
            data = await hub.catalog_quality_dry_run(hours)
            entry.update(at=time.time(), data=data)
            return data

        task = entry["task"] = asyncio.create_task(_refresh())
    if entry["data"] is not None:
        return {**entry["data"], "cached": True, "stale": True}
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=20)
    except asyncio.TimeoutError:
        return {"pending": True, "detail": "Kontrola katalogu se počítá na pozadí, zkus to za chvíli."}


@app.post("/api/admin/catalog-quality/run")
async def admin_catalog_quality_run(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    """Ordered one-shot cleanup through the same Store + job_pool as the worker."""
    await _admin_user_async(realitify_admin)
    body = payload or {}
    dry_run = bool(body.get("dry_run", False))
    try:
        hours = max(24, min(720, int(body.get("max_age_hours", 72))))
    except (TypeError, ValueError):
        hours = 72
    try:
        chunk = max(20, min(200, int(body.get("chunk_size", 80))))
    except (TypeError, ValueError):
        chunk = 80
    result = await hub.run_catalog_quality_cleanup(
        max_age_hours=hours, dry_run=dry_run, chunk_size=chunk
    )
    if not dry_run:
        _CATALOG_QUALITY_CACHE.clear()
    if not result.get("ok"):
        reason = result.get("reason") or result.get("error") or "cleanup failed"
        raise HTTPException(409 if result.get("reason") == "already-running" else 500, str(reason))
    return result


@app.get("/api/admin/billing")
async def admin_billing(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    return await asyncio.to_thread(admin_panel.cached_payload, "billing", lambda: admin_panel.billing_payload(astore, hub), 60.0)


@app.get("/api/admin/promo")
async def admin_promo(
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
    month: str | None = Query(default=None),
) -> dict:
    await _admin_user_async(realitify_admin)
    return await asyncio.to_thread(admin_panel.promo_payload, hub.store, month or "")


@app.post("/api/admin/promo/toggle")
async def admin_promo_toggle(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user = await _admin_user_async(realitify_admin)
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
    user = await _admin_user_async(realitify_admin)
    try:
        return await asyncio.to_thread(admin_panel.create_promo_code, hub.store, payload or {}, user.get("name") or "Admin")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/promo/payout")
async def admin_promo_payout(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    await _admin_user_async(realitify_admin)
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
    user = await _admin_user_async(realitify_admin)
    body = payload or {}
    target_id = str(body.get("user_id") or "").strip()
    target = await asyncio.to_thread(user_registry.get_user_by_id, hub.store, target_id) if target_id else None
    if target is not None:
        tstore = await asyncio.to_thread(hub.store_for, str(target.get("id")))
        tuid: str | None = str(target.get("id"))
    else:
        _, tstore = await _admin_store_async(realitify_admin)
        tuid = None
    try:
        result = await asyncio.to_thread(
            admin_panel.apply_action,
            tstore,
            str(body.get("action") or ""),
            body,
            user.get("name") or "Admin",
            hub.store,
            tuid,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    admin_panel.invalidate_payloads()
    return result


@app.get("/api/stories")
async def stories_index() -> dict:
    store = _public_store() or hub.store
    return await asyncio.to_thread(stories_cms.public_listing, store)


@app.get("/api/stories/{slug}")
async def stories_detail(slug: str, view: int = 0) -> dict:
    store = _public_store() or hub.store
    try:
        return await asyncio.to_thread(stories_cms.public_by_slug, store, slug, bool(view))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/inquiries")
async def public_inquiry(payload: dict[str, Any] | None = Body(None)) -> dict:
    store = _public_store() or hub.store
    try:
        return await asyncio.to_thread(stories_cms.create_inquiry, store, payload or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/admin/cms")
async def admin_cms_list(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    return await asyncio.to_thread(stories_cms.list_articles, astore, hub.store)


@app.post("/api/admin/cms-inquiry/{inquiry_id}")
async def admin_cms_inquiry_save(
    inquiry_id: str,
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.update_inquiry, astore, inquiry_id, payload or {})
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/cms-inquiry/{inquiry_id}/delete")
async def admin_cms_inquiry_delete(
    inquiry_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")
) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.delete_inquiry, astore, inquiry_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/admin/cms/{article_id}")
async def admin_cms_one(article_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.get_article, astore, article_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/cms")
async def admin_cms_create(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user, astore = await _admin_store_async(realitify_admin)
    return await asyncio.to_thread(stories_cms.save_article, astore, None, payload or {}, user.get("name") or "Admin")


@app.post("/api/admin/cms/{article_id}")
async def admin_cms_save(
    article_id: str,
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    user, astore = await _admin_store_async(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.save_article, astore, article_id, payload or {}, user.get("name") or "Admin")
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/cms/{article_id}/delete")
async def admin_cms_delete(article_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    _, astore = await _admin_store_async(realitify_admin)
    try:
        return await asyncio.to_thread(stories_cms.delete_article, astore, article_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/admin/cms-upload")
async def admin_cms_upload(
    file: UploadFile = File(...),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    await _admin_user_async(realitify_admin)
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
async def status(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    user, store = _user_store(realitify_session)
    child = hub.hub_for(str(user.get("id")))
    return await asyncio.to_thread(child.status)


@app.get("/api/admin/stacks")
async def admin_stacks(
    seconds: float = 3.0,
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    """Where is the web process spending CPU? Samples every thread's Python stack ~20x/s."""
    await _admin_user_async(realitify_admin)
    import collections
    import sys
    import threading
    import traceback

    seconds = min(max(float(seconds), 0.5), 15.0)

    def sample() -> dict[str, Any]:
        counts: collections.Counter[str] = collections.Counter()
        names = {t.ident: t.name for t in threading.enumerate()}
        n = 0
        end = time.monotonic() + seconds
        me = threading.get_ident()
        while time.monotonic() < end:
            for ident, frame in sys._current_frames().items():
                if ident == me:
                    continue
                stack = traceback.extract_stack(frame)
                if not stack:
                    continue
                top = stack[-1]
                # Idle threads sit in waits/selects: keep them out of the hot list.
                if top.name in {"wait", "_worker", "select", "accept", "get", "run_forever", "_run_once"} and top.filename.endswith(
                    ("threading.py", "queue.py", "selectors.py", "thread.py", "base_events.py")
                ):
                    continue
                key = f"{names.get(ident, ident)} | {top.filename.split('/app/')[-1]}:{top.lineno} {top.name} <- " + " < ".join(
                    f"{fr.filename.split('/app/')[-1]}:{fr.name}" for fr in reversed(stack[-5:-1])
                )
                counts[key] += 1
            n += 1
            time.sleep(0.05)
        return {"samples": n, "top": [{"n": c, "where": k} for k, c in counts.most_common(15)]}

    return await asyncio.to_thread(sample)


@app.get("/api/perf/diag")
async def perf_diag_snapshot(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
    from app import perf_diag

    payload = perf_diag.snapshot()
    try:
        payload["scrape_tick"] = hub.store.get_meta("scrape_worker_tick")
    except Exception:
        payload["scrape_tick"] = None
    payload["scrape_role"] = config.SCRAPE_ROLE
    return payload


@app.api_route("/health", methods=["GET", "HEAD"])
async def health() -> dict:
    """Liveness for Coolify — must not touch ssr_cache or catalog SQLite."""
    return {"ok": True}


@app.get("/api/version")
async def version() -> dict:
    return await version_info()


@app.post("/api/update")
async def install_update(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
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
        logger.exception("Aktualizace selhala")
        raise HTTPException(502, public_error(exc, "Aktualizaci se nepodařilo spustit. Zkuste to prosím později.")) from exc
    return {"ok": True, "restarting": True, "version": info.get("latest")}


@app.post("/api/monitor/start")
async def start_monitor(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
    await hub.start()
    return hub.status(fresh=True)


@app.post("/api/monitor/stop")
async def stop_monitor(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
    await hub.stop()
    return hub.status(fresh=True)


@app.post("/api/monitor/check")
async def check_now(payload: dict[str, Any] | None = Body(None), realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    user, store = _user_store(realitify_session)
    monitor_id = (payload or {}).get("monitor_id")
    # User-scoped check: run against the user's own store, not the global hub.
    child = hub.hub_for(str(user.get("id"))) if hasattr(hub, "hub_for") else None
    if child is not None:
        result = await child.check_once(monitor_id)
    else:
        result = await hub.check_once(monitor_id)
    return {"result": result, "status": hub.status(fresh=True)}


@app.post("/api/catalog/sync")
async def catalog_sync_now(payload: dict[str, Any] | None = None, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    await _admin_user_async(realitify_admin)
    raw = (payload or {}).get("portals") if isinstance(payload, dict) else None
    portals = None
    if isinstance(raw, str) and raw and raw != "all":
        portals = [raw]
    elif isinstance(raw, list):
        portals = [str(item) for item in raw if item]
    return {"result": hub.start_catalog_sync(portals=portals), "status": hub.status(fresh=True)}


@app.post("/api/discord/test")
async def discord_test(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    user = _current_user(realitify_session)
    try:
        return await hub.hub_for(str(user.get("id"))).send_test((payload or {}).get("monitor_id"))
    except Exception as exc:
        logger.exception("Discord test selhal")
        raise HTTPException(502, public_error(exc, "Testovací zprávu se nepodařilo odeslat. Zkontrolujte webhook v nastavení.")) from exc


@app.get("/api/discord/status")
async def discord_link_status(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    return user_account.discord_status(ustore)


@app.post("/api/discord/link-code")
async def discord_link_code(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        return user_account.create_discord_link_code(ustore)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/discord/unlink")
async def discord_unlink(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    return user_account.unlink_discord(ustore)


@app.post("/api/email/test")
async def email_test(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        sent = await mail_notify.notify_listing(
            ustore,
            {"name": "Testovací zpráva z Realitify"},
            "test",
            ignore_quiet=True,
        )
    except Exception as exc:
        logger.warning("test email failed: %s", exc)
        raise HTTPException(502, "Testovací e-mail se nepodařilo odeslat. Zkuste to později.") from exc
    if not sent:
        raise HTTPException(400, "Zapněte e-mail a na serveru nastavte SMTP_HOST a SMTP_FROM")
    return {"ok": True, "email": mail_notify.account_email(ustore)}


@app.get("/api/whatsapp/status")
async def whatsapp_status(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    return wa_notify.status(ustore)


@app.post("/api/whatsapp/phone")
async def whatsapp_phone(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    if not wa_notify.plan_allows(ustore):
        raise HTTPException(403, "WhatsApp notifikace jsou jen v tarifu PRO")
    try:
        wa_notify.save_phone(ustore, str((payload or {}).get("phone") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return wa_notify.status(ustore)


@app.post("/api/whatsapp/test")
async def whatsapp_test(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    if not wa_notify.plan_allows(ustore):
        raise HTTPException(403, "WhatsApp notifikace jsou jen v tarifu PRO")
    try:
        sent = await wa_notify.notify_listing(
            ustore,
            {"name": "Testovací zpráva z Realitify"},
            "test",
            ignore_quiet=True,
        )
    except Exception as exc:
        logger.warning("whatsapp test failed: %s", exc)
        raise HTTPException(502, "Testovací zprávu se nepodařilo odeslat. Zkuste to později.") from exc
    if not sent:
        raise HTTPException(400, "Zapněte WhatsApp a vyplňte telefonní číslo")
    return {"ok": True, **wa_notify.status(ustore)}


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
async def digest_test(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    user = _current_user(realitify_session)
    try:
        return await hub.hub_for(str(user.get("id"))).send_digest_test((payload or {}).get("webhook_url"))
    except Exception as exc:
        logger.exception("Test digestu selhal")
        raise HTTPException(502, public_error(exc, "Testovací zprávu se nepodařilo odeslat. Zkontrolujte webhook v nastavení.")) from exc


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
def push_vapid(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, store = _user_store(realitify_session)
    return {
        "publicKey": web_push.public_key(),
        "supported": True,
        "devices": store.push_subscription_count(),
        "enabled": bool(store.notify_prefs().get("push")),
    }


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    body = payload or {}
    ua = request.headers.get("user-agent") or ""
    _, store = _user_store(request.cookies.get(user_account.SESSION_COOKIE))

    def _save() -> dict[str, Any]:
        store.save_push_subscription(body, ua)
        prefs = store.notify_prefs()
        prefs["push"] = True
        store.save_notify_prefs(prefs)
        return {"ok": True, "devices": store.push_subscription_count(), "notify": store.notify_prefs()}

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
async def push_unsubscribe(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _, store = _user_store(request.cookies.get(user_account.SESSION_COOKIE))
    store.delete_push_subscription(str((payload or {}).get("endpoint") or ""))
    return {"ok": True, "devices": store.push_subscription_count()}


@app.post("/api/push/test")
async def push_test(request: Request) -> dict:
    _, store = _user_store(request.cookies.get(user_account.SESSION_COOKIE))
    sent = await asyncio.to_thread(web_push.notify_test, store)
    if not sent:
        raise HTTPException(400, "Na tomto zařízení ještě není aktivní odběr push notifikací")
    return {"ok": True, "sent": sent}


@app.get("/api/public/stats")
async def public_stats() -> dict:
    store = _public_store() or hub.store
    return {"new_today": await asyncio.to_thread(store.catalog_new_today_count)}


_IMG_OK_TTL_S = 6 * 3600
_img_ok_cache: dict[str, tuple[float, bool]] = {}
_CITY_LOCATIVE = {
    "Praha": "v Praze",
    "Brno": "v Brně",
    "Ostrava": "v Ostravě",
    "Plzeň": "v Plzni",
    "Liberec": "v Liberci",
    "Olomouc": "v Olomouci",
    "České Budějovice": "v Českých Budějovicích",
    "Hradec Králové": "v Hradci Králové",
    "Pardubice": "v Pardubicích",
    "Ústí nad Labem": "v Ústí nad Labem",
}


async def _image_loads(client: Any, url: str) -> bool:
    hit = _img_ok_cache.get(url)
    if hit and time.time() - hit[0] < _IMG_OK_TTL_S:
        return hit[1]
    ok = False
    try:
        async with client.stream("GET", url, headers={"Range": "bytes=0-1023"}) as r:
            ctype = (r.headers.get("content-type") or "").lower()
            ok = r.status_code in (200, 206) and ctype.startswith("image/")
    except Exception:
        ok = False
    if len(_img_ok_cache) > 5000:
        _img_ok_cache.clear()
    _img_ok_cache[url] = (time.time(), ok)
    return ok


async def _loadable_cards(store: Any, city: str, need: int) -> list[dict[str, Any]]:
    import httpx

    cards = await asyncio.to_thread(store.landing_preview_listings, city, 12)
    cards = [c for c in cards if str(c.get("image") or "").startswith("https://")]
    if not cards:
        return []
    async with httpx.AsyncClient(timeout=3.0, follow_redirects=True) as client:
        flags = await asyncio.gather(*(_image_loads(client, c["image"]) for c in cards))
    return [c for c, ok in zip(cards, flags) if ok][:need]


@app.get("/api/public/landing-listings")
async def public_landing_listings(request: Request) -> dict:
    from app import geo_city

    store = _public_store() or hub.store
    city = await geo_city.city_for_request(request.headers, _client_ip(request))
    used = "Brno"
    items: list[dict[str, Any]] = []
    if city and city != "Brno":
        items = await _loadable_cards(store, city, 3)
        if len(items) >= 3:
            used = city
    if len(items) < 3:
        used = "Brno"
        items = await _loadable_cards(store, "Brno", 3)
    label = _CITY_LOCATIVE.get(used)
    title = f"Pronájmy {label} do 22 000 Kč" if label else f"Pronájmy – {used} do 22 000 Kč"
    return {"items": items, "city": used, "title": title}


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
    store = _public_store() or hub.store
    items = await asyncio.to_thread(store.public_gone_fast_rentals, days=3, limit=4)
    return {"items": items}


@app.get("/api/public/catalog")
async def public_catalog(
    portal: str = "",
    q: str = "",
    disposition: str = "",
    price_from: str = "",
    price_to: str = "",
    area_from: str = "",
    area_to: str = "",
    offer: str = "",
    district: str = "",
    estate: str = "",
    listing_quality: str = "apartment",
    sort: str = "newest",
    limit: int = 20,
    offset: int = 0,
    facets: str = "0",
    first_seen_from: str = "",
    since_hours: int = 0,
) -> dict:
    """Unauthenticated catalog search for the public MCP connector (server-side filters)."""
    from datetime import datetime, timedelta, timezone

    from app.locality_normalize import normalize_disposition, normalize_locality, normalize_offer, suggest_localities

    store = _public_store() or hub.store
    capped = min(max(int(limit or 20), 1), 20)
    try:
        offer_n = normalize_offer(offer) if offer else ""
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    district_n = normalize_locality(district) if district else ""
    disposition_n = normalize_disposition(disposition) if disposition else ""
    sort_n = (sort or "newest").strip()
    if sort_n == "cheapest":
        sort_n = "cheapest"
    elif sort_n == "best_value":
        sort_n = "best_value"
    elif sort_n not in {"newest", "oldest", "price_asc", "price_desc", "price_m2_asc"}:
        sort_n = "newest"
    seen_from = (first_seen_from or "").strip()
    if since_hours and int(since_hours) > 0 and not seen_from:
        seen_from = (datetime.now(timezone.utc) - timedelta(hours=int(since_hours))).isoformat()
    # Default = apartments only. Non-residential when estate is explicitly non-byt or listing_quality=any.
    quality_n = (listing_quality or "apartment").strip().lower() or "apartment"
    estate_n = (estate or "").strip().lower()
    non_apt = {"komercni", "pozemek", "ostatni", "projekt", "dum"}
    if any(part in non_apt for part in estate_n.replace(";", ",").split(",") if part):
        quality_n = "any"
    payload = await place_geo.attach_geoms(
        _catalog_filters(
            portal=portal,
            q=q,
            disposition=disposition_n,
            price_from=price_from,
            price_to=price_to,
            area_from=area_from,
            area_to=area_to,
            offer=offer_n,
            district=district_n,
            estate=estate,
            sort=sort_n,
            limit=capped,
            offset=offset,
            include_pins="0",
            facets=facets,
            first_seen_from=seen_from,
        )
    )
    payload["listing_quality"] = quality_n
    result = await _run_catalog_query_cached(payload, store)
    result = dict(result)
    total = int(result.get("total") or 0)
    if district_n and total == 0 and not (result.get("items") or []):
        return {
            "items": [],
            "total": 0,
            "count": 0,
            "locality": district_n,
            "error": "no_listings_for_locality",
            "suggestions": suggest_localities(district or district_n),
            "message": f"No active listings for '{district_n}'. Try one of: {', '.join(suggest_localities(district or district_n)[:5])}",
        }
    # Attach locality avg/median for price_vs_locality when district filter is set
    avg_m2 = None
    median_m2 = None
    if district_n:
        try:
            stats = await asyncio.to_thread(store.catalog_stats, district_n, offer_n, disposition_n)
            avg_m2 = stats.get("avg_price_per_m2")
            median_m2 = stats.get("median_price_per_m2")
        except ValueError:
            avg_m2 = None
            median_m2 = None
    result["locality"] = district_n
    result["offer"] = offer_n
    result["listing_quality"] = quality_n
    result["avg_price_per_m2_locality"] = avg_m2
    result["median_price_per_m2_locality"] = median_m2
    try:
        result["data_as_of"] = await asyncio.to_thread(store.catalog_data_as_of)
    except Exception:
        result["data_as_of"] = ""
    result["units"] = {"currency": "CZK", "area": "m2", "price_per_area": "CZK/m2"}
    result["source"] = "Realitify catalog"
    result["source_url"] = "https://realitify.cz"
    return result


@app.get("/api/public/catalog/stats")
async def public_catalog_stats(district: str = "", offer: str = "", disposition: str = "") -> dict:
    """Unauthenticated locality stats for the public MCP connector."""
    from app.locality_normalize import normalize_disposition, normalize_locality, normalize_offer, suggest_localities

    store = _public_store() or hub.store
    try:
        offer_n = normalize_offer(offer) if offer else ""
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    district_n = normalize_locality(district)
    if not district_n:
        raise HTTPException(
            400,
            "district is required. Pass a Czech locality such as Praha, Brno, or Praha 5.",
        )
    try:
        data = await asyncio.to_thread(
            store.catalog_stats, district_n, offer_n, normalize_disposition(disposition)
        )
    except ValueError as exc:
        suggestions = suggest_localities(district or district_n)
        raise HTTPException(
            400,
            f"{exc}. Try one of: {', '.join(suggestions[:5])}" if suggestions else str(exc),
        ) from exc
    try:
        data["data_as_of"] = await asyncio.to_thread(store.catalog_data_as_of)
    except Exception:
        data["data_as_of"] = ""
    data["units"] = {"currency": "CZK", "area": "m2", "price_per_area": "CZK/m2"}
    data["source"] = "Realitify catalog"
    data["source_url"] = "https://realitify.cz"
    return data


@app.get("/api/public/catalog/price-check")
async def public_catalog_price_check(
    district: str = "",
    offer: str = "pronajem",
    price: int = 0,
    disposition: str = "",
    area: int = 0,
) -> dict:
    from app.locality_normalize import normalize_disposition, normalize_locality, normalize_offer, suggest_localities

    store = _public_store() or hub.store
    try:
        offer_n = normalize_offer(offer)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    district_n = normalize_locality(district)
    if not district_n:
        raise HTTPException(400, "district is required. Example: Praha, Brno, Vinohrady.")
    try:
        data = await asyncio.to_thread(
            store.catalog_price_check,
            district_n,
            offer_n,
            int(price),
            normalize_disposition(disposition),
            int(area) if area else None,
        )
    except ValueError as exc:
        msg = str(exc)
        if "no comparable" in msg.lower():
            raise HTTPException(
                400,
                f"{msg}. Relax filters: drop disposition or area, or try a parent locality "
                f"(suggestions: {', '.join(suggest_localities(district_n)[:5])}).",
            ) from exc
        raise HTTPException(400, msg) from exc
    try:
        data["data_as_of"] = await asyncio.to_thread(store.catalog_data_as_of)
    except Exception:
        data["data_as_of"] = ""
    data["units"] = {"currency": "CZK", "area": "m2"}
    data["source"] = "Realitify catalog"
    data["source_url"] = "https://realitify.cz"
    return data


@app.get("/api/public/catalog/compare")
async def public_catalog_compare(
    localities: str = "",
    offer: str = "pronajem",
    disposition: str = "",
) -> dict:
    from app.locality_normalize import normalize_disposition, normalize_locality, normalize_offer

    store = _public_store() or hub.store
    try:
        offer_n = normalize_offer(offer) if offer else ""
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    locs = [normalize_locality(x) for x in localities.split(",") if x.strip()]
    locs = [x for x in locs if x]
    try:
        data = await asyncio.to_thread(
            store.catalog_compare_localities, locs, offer_n, normalize_disposition(disposition)
        )
    except ValueError as exc:
        raise HTTPException(
            400,
            f"{exc}. Provide at least two valid Czech localities, e.g. Praha,Brno.",
        ) from exc
    try:
        data["data_as_of"] = await asyncio.to_thread(store.catalog_data_as_of)
    except Exception:
        data["data_as_of"] = ""
    data["units"] = {"currency": "CZK", "area": "m2", "price_per_area": "CZK/m2"}
    data["source"] = "Realitify catalog"
    data["source_url"] = "https://realitify.cz"
    return data


@app.get("/api/public/catalog/report")
async def public_catalog_report(district: str = "", offer: str = "pronajem") -> dict:
    from app.locality_normalize import normalize_locality, normalize_offer

    store = _public_store() or hub.store
    try:
        offer_n = normalize_offer(offer) if offer else "pronajem"
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    district_n = normalize_locality(district)
    if not district_n:
        raise HTTPException(400, "district is required")
    try:
        return await asyncio.to_thread(store.catalog_locality_report, district_n, offer_n)
    except ValueError as exc:
        if str(exc) == "not_enough_data":
            raise HTTPException(404, "Not enough listings for this locality (min 20)") from exc
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/public/catalog/item")
async def public_catalog_item(
    monitor_id: str = "",
    id: str = "",
    listing_key: str = "",
    url: str = "",
) -> dict:
    """Unauthenticated listing detail (DB only, no live enrich) for the public MCP connector."""
    store = _public_store() or hub.store
    item = await asyncio.to_thread(store.catalog_item, monitor_id, id, listing_key, url)
    if not item:
        raise HTTPException(404, "Nabídka se nenašla")
    return item


@app.get("/api/public/games/higher-lower")
async def public_game_higher_lower() -> dict:
    from app import games as marketing_games

    # Memory/seed only — catalog refresh is background and must not delay the response.
    store = _public_store() or hub.store
    marketing_games.schedule_pool_refresh(store)
    return await asyncio.to_thread(marketing_games.higher_lower_pair, store)


@app.get("/api/public/games/rent-round")
async def public_game_rent_round() -> dict:
    from app import games as marketing_games

    store = _public_store() or hub.store
    marketing_games.schedule_pool_refresh(store)
    payload = await asyncio.to_thread(marketing_games.rent_round, store)
    return {"round_id": payload["round_id"], "items": payload["items"]}


@app.post("/api/public/games/rent-score")
async def public_game_rent_score(payload: dict[str, Any] | None = Body(None)) -> dict:
    from app import games as marketing_games

    body = payload or {}
    guesses = body.get("guesses") or []
    if not isinstance(guesses, list) or len(guesses) < 1:
        raise HTTPException(400, "Chybí tipy")
    ids = [str(item.get("id") or "") for item in guesses if isinstance(item, dict)]
    store = _public_store() or hub.store
    found = await asyncio.to_thread(marketing_games.lookup_prices, store, ids)
    items = [found[key] for key in ids if key in found]
    if len(items) < 1:
        raise HTTPException(400, "Neznámé byty")
    scored = marketing_games.score_round(items, [item for item in guesses if isinstance(item, dict)])
    saved = await asyncio.to_thread(
        marketing_games.save_rent_round,
        store,
        player_name=str(body.get("name") or ""),
        scored=scored,
    )
    return {
        "ok": True,
        "id": saved["id"],
        "player_name": saved["player_name"],
        "score": scored["score"],
        "max_score": scored["max_score"],
        "accuracy": scored["accuracy"],
        "items": scored["items"],
    }


@app.get("/api/listings")
async def listings(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    items = await asyncio.to_thread(ustore.recent_notified, 24)
    return {"items": items}


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
    include_pins: str = "0",
    facets: str = "1",
    first_seen_from: str = "",
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
        "facets": facets,
        "first_seen_from": first_seen_from,
    }
    if pins_only:
        payload["pins_only"] = True
    return payload


async def _run_catalog_query(payload: dict[str, Any], store: Store) -> dict[str, Any]:
    try:
        result = await asyncio.get_running_loop().run_in_executor(hub.ui_pool, store.catalog, payload)
    except sqlite3.OperationalError as exc:
        raise HTTPException(status_code=503, detail="catalog-busy") from exc
    return result


# Guest/public catalog results are identical for every visitor, but a query scans the whole
# listings table (seconds locally, tens of seconds under scrape load in production). Serve them
# from a stale-while-revalidate cache and let identical concurrent requests share one query.
_CATALOG_CACHE: dict[str, dict[str, Any]] = {}
_CATALOG_FRESH_S = 90.0
_CATALOG_STALE_S = 3600.0
_CATALOG_CACHE_MAX = 400


def _drop_user_catalog_cache(ustore: Store) -> None:
    prefix = f"{ustore.path}|"
    for key in [k for k in _CATALOG_CACHE if k.startswith(prefix)]:
        _CATALOG_CACHE.pop(key, None)


async def _refresh_catalog_cache(key: str, payload: dict[str, Any], store: Store) -> dict[str, Any]:
    data = await _run_catalog_query(payload, store)
    if len(_CATALOG_CACHE) >= _CATALOG_CACHE_MAX:
        oldest = sorted(_CATALOG_CACHE.items(), key=lambda kv: kv[1].get("at") or 0)[: _CATALOG_CACHE_MAX // 4]
        for old_key, _ in oldest:
            _CATALOG_CACHE.pop(old_key, None)
    entry = _CATALOG_CACHE.setdefault(key, {"at": 0.0, "data": None, "task": None})
    entry["at"] = time.time()
    entry["data"] = data
    return data


async def _run_catalog_query_cached(payload: dict[str, Any], store: Store, scope: str = "") -> dict[str, Any]:
    key = f"{scope}|{store.path}|{json.dumps(payload, sort_keys=True, default=str)}"
    entry = _CATALOG_CACHE.get(key)
    now = time.time()
    if entry and entry.get("data") is not None:
        age = now - float(entry.get("at") or 0)
        if age < _CATALOG_FRESH_S:
            return entry["data"]
        if age < _CATALOG_STALE_S:
            task = entry.get("task")
            if task is None or task.done():
                entry["task"] = asyncio.create_task(_refresh_catalog_cache(key, payload, store))
                entry["task"].add_done_callback(lambda t: t.cancelled() or t.exception())
            return entry["data"]
    entry = _CATALOG_CACHE.setdefault(key, {"at": 0.0, "data": None, "task": None})
    task = entry.get("task")
    if task is None or task.done():
        task = entry["task"] = asyncio.create_task(_refresh_catalog_cache(key, payload, store))
    return await asyncio.shield(task)


async def _catalog_warm_loop() -> None:
    """Keep the default (unfiltered) catalog hot so a restart or a quiet hour never leaves visitors
    waiting for a cold full-table query. Errors are swallowed; the next round retries."""
    await asyncio.sleep(15)
    while True:
        try:
            store = _public_store() or hub.store
            for limit, pins in ((36, "1"), (20, "0")):
                payload = await place_geo.attach_geoms(
                    _catalog_filters(limit=limit, offset=0, include_pins=pins, facets="1", sort="newest")
                )
                key = f"|{store.path}|{json.dumps(payload, sort_keys=True, default=str)}"
                entry = _CATALOG_CACHE.setdefault(key, {"at": 0.0, "data": None, "task": None})
                task = entry.get("task")
                if task is None or task.done():
                    task = entry["task"] = asyncio.create_task(_refresh_catalog_cache(key, payload, store))
                await asyncio.shield(task)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"catalog warm failed: {exc!r}", flush=True)
        await asyncio.sleep(75)


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
    include_pins: str = "0",
    facets: str = "1",
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
) -> dict:
    cuser, ustore = _catalog_store(realitify_session, realitify_guest)
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
            facets=facets,
        )
    )
    reader = _catalog_reader(cuser, ustore, payload)
    return await _run_catalog_query_cached(payload, reader, scope="" if cuser is None else str(ustore.path))


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
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
) -> dict:
    cuser, ustore = _catalog_store(realitify_session, realitify_guest)
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
    reader = _catalog_reader(cuser, ustore, payload)
    return await _run_catalog_query_cached(payload, reader, scope="" if cuser is None else str(ustore.path))


@app.get("/api/catalog/stats")
async def catalog_stats(
    district: str = "",
    offer: str = "",
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
) -> dict:
    cuser, ustore = _catalog_store(realitify_session, realitify_guest)
    ustore = _catalog_reader(cuser, ustore, {})
    try:
        return await asyncio.to_thread(ustore.catalog_stats, district, offer)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/catalog/item")
async def catalog_item(
    monitor_id: str = "",
    id: str = "",
    listing_key: str = "",
    url: str = "",
    enrich: str = "1",
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
    realitify_guest: str | None = Cookie(default=None, alias="realitify_guest"),
) -> dict:
    user, ustore = _catalog_store(realitify_session, realitify_guest)
    if user is None:
        enrich = "0"
    # Catalog rows live in the shared owner store; a user's own monitor-hit rows live in theirs.
    reader = _catalog_reader(user, ustore, {}) if monitor_id in ("", CATALOG_MONITOR_ID) else ustore
    item = await asyncio.to_thread(reader.catalog_item, monitor_id, id, listing_key, url)
    if not item and reader is not ustore:
        reader = ustore
        item = await asyncio.to_thread(ustore.catalog_item, monitor_id, id, listing_key, url)
    if not item:
        raise HTTPException(404, "Nabídka se nenašla")
    # Enrichment of a shared catalog row is written once, to the owner store.
    write_store = _public_store() or ustore if getattr(reader, "_scope_user_path", None) else ustore
    do_enrich = str(enrich or "1").strip().lower() not in {"0", "false", "no"}
    if not do_enrich:
        return item
    source_url = item.get("url") or url
    if source_url:
        try:
            listing = _listing_from_catalog_dict(item)
            listing.photos = []
            listing = await hub.client_for(source_url).fetch_detail(listing)
            from app.places import refine_listing_location_async

            await refine_listing_location_async(listing)

            def _enrich() -> dict:
                write_store.save_listing_enrichment(item["monitor_id"], listing)
                return (
                    reader.catalog_item(
                        item["monitor_id"],
                        item["id"],
                        listing_key=item.get("listing_key") or "",
                        url=source_url,
                    )
                    or item
                )

            item = await asyncio.to_thread(_enrich)
        except ListingGone:
            await hub.hub_for(str(user.get("id"))).notify_sold(ustore.get_monitor(item["monitor_id"]), item["id"])
            item = await asyncio.to_thread(
                reader.catalog_item, item["monitor_id"], item["id"], url=source_url
            ) or item
        except Exception:
            pass
    return item


@app.post("/api/catalog/user")
async def save_listing_user(
    payload: dict[str, Any],
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        result = await asyncio.to_thread(
            ustore.set_listing_user, payload.get("url") or "", payload.get("status"), payload.get("note")
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _drop_user_catalog_cache(ustore)
    return result


@app.get("/api/settings")
async def get_settings(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    return await asyncio.to_thread(ustore.app_settings)


@app.post("/api/settings")
async def save_settings(
    payload: dict[str, Any],
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        return await asyncio.to_thread(ustore.save_app_settings, payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/monitors")
async def list_monitors(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    items = await asyncio.to_thread(ustore.list_monitors)
    return {"items": items}


@app.post("/api/monitors")
async def save_monitor(
    payload: dict[str, Any],
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    if not (payload.get("search_url") or "").strip():
        raise HTTPException(400, "Chybí URL hledání. Vyplňte ho tlačítkem „Použít URL v profilu“.")
    existing = ustore.get_monitor(str(payload.get("id") or "")) if payload.get("id") else None
    try:
        try:
            saved = ustore.save_monitor(payload)
        except ValueError as exc:
            if "Limit tarifu" in str(exc):
                asyncio.create_task(asyncio.to_thread(email_triggers.on_dog_limit_hit, ustore), name="email-dog-limit")
            raise
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if existing is None:
        try:
            site_stats.track(hub.store, site_stats.KIND_MONITOR, path="/monitory")
        except Exception:
            pass
    return saved


@app.get("/api/monitors/{monitor_id}/preview")
async def preview_monitor(
    monitor_id: str,
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    monitor = ustore.get_monitor(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor neexistuje")
    return {"items": ustore.monitor_preview(monitor_id)}


@app.get("/api/monitors/{monitor_id}/convert")
async def convert_monitor(
    monitor_id: str,
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    monitor = ustore.get_monitor(monitor_id)
    if not monitor:
        raise HTTPException(404, "Monitor neexistuje")
    converted = convert_search_url(monitor["search_url"])
    base_name = monitor["name"].replace(" · Sreality", "").replace(" · Bezrealitky", "").strip()
    return {
        **converted,
        "suggested_name": f"{base_name} · {converted['target']}",
    }


@app.delete("/api/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: str,
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    user, ustore = _user_store(realitify_session)
    try:
        ustore.delete_monitor(monitor_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": hub.hub_for(str(user.get("id"))).status(fresh=True)}


@app.get("/api/templates")
async def list_templates(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    return {"items": ustore.list_templates(), "variables": VARIABLES, "sample": sample_vars()}


@app.post("/api/templates")
async def save_template(
    payload: dict[str, Any],
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    if not payload.get("config"):
        payload["config"] = default_template_config()
    return ustore.save_template(payload)


@app.delete("/api/templates/{template_id}")
async def delete_template(
    template_id: str,
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        ustore.delete_template(template_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


def _filter_mod(source: str | None = None, url: str = ""):
    from app.sources import url_mod_for

    return url_mod_for(source, url or "")


@app.get("/api/filters/catalog")
async def filter_catalog() -> dict:
    from app.sources import PORTALS

    sources = {}
    for spec in PORTALS:
        sources[spec.id] = {
            "catalog": spec.urls.catalog(),
            "defaults": spec.urls.default_filters(),
            "sample": spec.urls.sample_filters(),
        }
    payload = {
        "locality_map": localities.catalog_map(),
        "catalog": url_builder.catalog(),
        "defaults": url_builder.default_filters(),
        "sample": url_builder.sample_filters(),
        "sources": sources,
        "portals": [{"id": spec.id, "label": spec.label} for spec in PORTALS],
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
async def backup_json(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> Response:
    _, ustore = _user_store(realitify_session)
    payload = json.dumps(export_config(ustore), ensure_ascii=False, indent=2)
    return Response(
        payload,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="sreality-config.json"'},
    )


@app.get("/api/backup/sqlite")
async def backup_sqlite(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> Response:
    _, ustore = _user_store(realitify_session)
    return Response(
        export_sqlite(ustore),
        media_type="application/vnd.sqlite3",
        headers={"Content-Disposition": 'attachment; filename="monitor.sqlite"'},
    )


@app.get("/api/backup/pack")
async def backup_pack(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> Response:
    _, ustore = _user_store(realitify_session)
    return Response(
        export_pack(ustore),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sreality-backup.zip"'},
    )


@app.post("/api/backup/import")
async def backup_import(
    file: UploadFile = File(...),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    user, ustore = _user_store(realitify_session)
    user_id = str(user.get("id"))
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Prázdný soubor")
    name = (file.filename or "").lower()

    def _do_import() -> None:
        nonlocal ustore
        if name.endswith(".zip") or raw[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                names = archive.namelist()
                sqlite_name = next((item for item in names if item.endswith((".sqlite", ".db"))), None)
                json_name = next((item for item in names if item.endswith((".json"))), None)
                if sqlite_name:
                    replace_sqlite(ustore, archive.read(sqlite_name))
                    hub.drop_store_cache(user_id)
                    ustore = hub.store_for(user_id)
                if json_name:
                    payload = json.loads(archive.read(json_name))
                    import_config(ustore, payload, reset_seeded=sqlite_name is None)
                if not sqlite_name and not json_name:
                    raise HTTPException(400, "V zipu chybí config.json nebo monitor.sqlite")
        elif name.endswith(".json") or raw[:1] in {b"{", b"["}:
            import_config(ustore, json.loads(raw.decode("utf-8")), reset_seeded=True)
        elif name.endswith((".sqlite", ".db")) or raw[:16] == b"SQLite format 3\x00":
            replace_sqlite(ustore, raw)
            hub.drop_store_cache(user_id)
            ustore = hub.store_for(user_id)
        else:
            raise HTTPException(400, "Nahraj .zip, .json nebo .sqlite")

    try:
        await asyncio.to_thread(_do_import)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "status": hub.hub_for(user_id).status(fresh=True)}
@app.get("/api/billing")
async def billing_status(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        stripe_billing.settle_pending_if_due(ustore)
        state = stripe_billing.billing_state(ustore)
        record = ustore.billing_record() or {}
        if record.get("customer_id") and config.STRIPE_SECRET_KEY:
            # Re-sync ze Stripe i když lokální záznam tvrdí "pro" — chrání před
            # ztraceným webhookem (zrušení přes Dashboard by jinak nechalo PRO napořád).
            period_end = record.get("current_period_end") or 0
            period_past = isinstance(period_end, (int, float)) and period_end > 0 and period_end < time.time()
            if (
                state.get("plan") == "free"
                or record.get("pending_plan")
                or record.get("cancel_at_period_end")
                or period_past
            ):
                state = stripe_billing.recover_from_stripe(ustore)
                stripe_billing.apply_watch_limit(ustore)
                hub._status_cache = None
                state = stripe_billing.billing_state(ustore)
        return {**state, "invoices": stripe_billing.list_invoices(ustore)}
    except Exception as extra:
        if not config.STRIPE_SECRET_KEY or "Invalid API Key" in str(extra):
            state = stripe_billing.billing_state(ustore)
            state["configured"] = False
            return {**state, "invoices": []}
        raise HTTPException(502, f"Stripe: {extra}") from extra


@app.post("/api/billing/checkout")
async def billing_checkout(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    user, ustore = _user_store(realitify_session)
    body = payload or {}
    try:
        result = stripe_billing.create_checkout(
            ustore,
            str(body.get("plan") or ""),
            user.get("email") or str(body.get("email") or ""),
            str(body.get("promo") or body.get("promo_code") or ""),
            str(user.get("id") or ""),
        )
        hub._status_cache = None
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Stripe operace selhala")
        raise HTTPException(502, public_error(exc)) from exc


@app.get("/api/billing/promo")
async def billing_promo_lookup(
    code: str = Query(""),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        raw = (code or "").strip()
        if not raw:
            pending = (ustore.billing_record() or {}).get("pending_promo_code") or ""
            if not pending:
                return {"ok": True, "code": "", "percent": 0, "amount_czk": 0, "first_order": True}
            raw = str(pending)
        return stripe_billing.resolve_promo_code(raw)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Stripe operace selhala")
        raise HTTPException(502, public_error(exc)) from exc


@app.post("/api/billing/promo")
async def billing_promo_save(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    body = payload or {}
    try:
        return stripe_billing.save_pending_promo(ustore, str(body.get("code") or body.get("promo") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Stripe operace selhala")
        raise HTTPException(502, public_error(exc)) from exc


@app.post("/api/billing/portal")
async def billing_portal(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        return {"url": stripe_billing.create_portal(ustore)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Stripe operace selhala")
        raise HTTPException(502, public_error(exc)) from exc


@app.post("/api/billing/cancel")
async def billing_cancel(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        result = stripe_billing.cancel_subscription(ustore)
        hub._status_cache = None
        return result
    except Exception as exc:
        logger.exception("Stripe operace selhala")
        raise HTTPException(502, public_error(exc)) from exc


@app.post("/api/billing/sync")
async def billing_sync(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    session_id = str((payload or {}).get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(400, "Chybí session_id")
    try:
        result = stripe_billing.sync_checkout_session(ustore, session_id)
        hub._status_cache = None
        return result
    except Exception as exc:
        logger.exception("Stripe operace selhala")
        raise HTTPException(502, public_error(exc)) from exc


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request) -> dict:
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    # Route the event to the owning user's store: first via Stripe metadata
    # user_id, then via customer_id lookup across user stores (legacy events).
    target_store = None
    try:
        webhook_user_id = await asyncio.to_thread(
            stripe_billing.extract_webhook_user_id, payload, signature
        )
    except Exception:
        webhook_user_id = None
    if webhook_user_id and user_registry.get_user_by_id(hub.store, webhook_user_id):
        target_store = hub.store_for(webhook_user_id)
    if target_store is None:
        try:
            webhook_customer_id = await asyncio.to_thread(
                stripe_billing.extract_webhook_customer_id, payload, signature
            )
        except Exception:
            webhook_customer_id = None
        if webhook_customer_id:
            target_store = await asyncio.to_thread(
                _store_for_stripe_customer, webhook_customer_id
            )
    if target_store is None:
        # Fail loudly instead of writing billing state into the central DB black hole.
        raise HTTPException(400, "Webhook event nelze přiřadit žádnému uživateli")
    try:
        result = stripe_billing.handle_webhook(target_store, payload, signature)
        hub._status_cache = None
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("Neočekávaná chyba")
        raise HTTPException(400, public_error(exc)) from exc


def _store_for_stripe_customer(customer_id: str) -> Store | None:
    """Find the user store whose billing record matches the Stripe customer_id."""
    for user in user_registry.list_users(hub.store):
        uid = str(user.get("id") or "")
        if not uid:
            continue
        try:
            ustore = hub.store_for(uid)
            record = ustore.billing_record() or {}
        except Exception:
            continue
        if str(record.get("customer_id") or "") == customer_id:
            return ustore
    return None


def _agent_key(request: Request) -> tuple[dict[str, Any], Store]:
    """Authenticate an MCP/API key against all user stores. Returns (key, owner_store)."""
    authorization = request.headers.get("authorization")
    last_error: PermissionError | None = None
    for candidate in user_registry.list_users(hub.store):
        uid = str(candidate.get("id") or "")
        if not uid:
            continue
        ustore = hub.store_for(uid)
        try:
            key = agent_hub.require_mcp(ustore, authorization)
        except PermissionError as exc:
            last_error = exc
            continue
        if not agent_hub.rate_ok(str(key.get("id") or "")):
            raise HTTPException(429, "Příliš mnoho požadavků. Zkuste to za chvíli.")
        return key, ustore
    detail = str(last_error) if last_error else "Neplatný nebo chybějící API klíč"
    raise HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": mcp_oauth.www_authenticate()},
    )


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


@app.get("/.well-known/oauth-protected-resource/mcp")
async def oauth_protected_resource() -> dict:
    return mcp_oauth.protected_resource_metadata()


@app.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource_root(request: Request) -> dict:
    # RFC 9728: metadata at the origin root describes the origin itself (resource == requested origin).
    data = dict(mcp_oauth.protected_resource_metadata())
    host = (request.headers.get("host") or "").split(":")[0].lower()
    base = mcp_oauth.issuer()
    if host in {"realitify.cz", "www.realitify.cz"}:
        base = f"https://{host}"
    data["resource"] = base
    return data


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
        # client lives in the global store; the auth code belongs to the consenting user
        target = mcp_oauth.complete_authorize(hub.store_for(str(user.get("id"))), form, client_store=hub.store)
    except PermissionError as exc:
        return HTMLResponse(mcp_oauth.consent_html(form, error=str(exc)), status_code=403)
    except ValueError as exc:
        return HTMLResponse(mcp_oauth.consent_html(form, error=str(exc)), status_code=400)
    return RedirectResponse(target, status_code=303)


def _oauth_store_for_token(token_value: str) -> Store:
    """Find the user store holding an OAuth code/refresh token."""
    for candidate in user_registry.list_users(hub.store):
        uid = str(candidate.get("id") or "")
        if not uid:
            continue
        ustore = hub.store_for(uid)
        try:
            if mcp_oauth.find_token_store_hint(ustore, token_value):
                return ustore
        except Exception:
            continue
    return hub.store


@app.post("/agent/identity")
async def agent_identity(request: Request):
    """auth.md: start agent registration. The user confirms a code (claim ceremony)."""
    _rate_limit_or_429(request, "agent_identity", "", 10, 3600)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    kind = str(body.get("type") or "service_auth")
    if kind not in {"service_auth", "identity_assertion"}:
        return JSONResponse({"error": "unsupported_identity_type"}, status_code=400)
    if kind == "identity_assertion" and str(body.get("assertion_type") or "verified_email") != "verified_email":
        return JSONResponse({"error": "unsupported_assertion_type"}, status_code=400)
    email = str(body.get("login_hint") or body.get("email") or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return JSONResponse({"error": "invalid_request", "error_description": "invalid email"}, status_code=400)
    return JSONResponse(mcp_oauth.create_claim(hub.store, email=email, agent_name=str(body.get("agent_name") or "")))


@app.post("/agent/identity/claim")
async def agent_identity_claim(request: Request):
    _rate_limit_or_429(request, "agent_claim", "", 20, 3600)
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        data = mcp_oauth.refresh_claim(hub.store, str(body.get("claim_token") or ""), str(body.get("email") or ""))
    except ValueError:
        return JSONResponse({"error": "expired_token"}, status_code=400)
    return JSONResponse(data)


@app.get("/agent/claim")
async def agent_claim_page(request: Request):
    code = str(request.query_params.get("user_code") or "")
    user = user_account.user_from_session(hub.store, request.cookies.get(user_account.SESSION_COOKIE))
    if not user:
        nxt = "/agent/claim?" + urlencode({"user_code": code})
        return RedirectResponse("/prihlaseni?next=" + quote(nxt, safe=""), status_code=303)
    if not mcp_oauth.find_claim_by_code(hub.store, code):
        return HTMLResponse(mcp_oauth.claim_page_html(user_code=code, email="", error="Kód je neplatný nebo vypršel."), status_code=400)
    return HTMLResponse(mcp_oauth.claim_page_html(user_code=code, email=str(user.get("email") or "")), headers={"Cache-Control": "no-store"})


@app.post("/agent/claim")
async def agent_claim_submit(request: Request):
    form = {str(k): str(v) for k, v in (await request.form()).items()}
    code = form.get("user_code", "")
    user = user_account.user_from_session(hub.store, request.cookies.get(user_account.SESSION_COOKIE))
    if not user:
        return RedirectResponse("/prihlaseni?next=" + quote("/agent/claim?" + urlencode({"user_code": code}), safe=""), status_code=303)
    _rate_limit_or_429(request, "agent_claim_submit", str(user.get("id")), 10, 600)
    try:
        mcp_oauth.approve_claim(hub.store, code, user)
    except ValueError as exc:
        return HTMLResponse(mcp_oauth.claim_page_html(user_code=code, email="", error=str(exc)), status_code=400)
    return HTMLResponse(mcp_oauth.claim_page_html(user_code=code, email="", done=True), headers={"Cache-Control": "no-store"})


@app.post("/oauth/token")
async def oauth_token(request: Request):
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    grant = str(form.get("grant_type") or "")
    if grant == mcp_oauth.CLAIM_GRANT:
        _rate_limit_or_429(request, "agent_claim_poll", "", 120, 600)
        state, row = mcp_oauth.poll_claim(hub.store, str(form.get("claim_token") or ""))
        if state == "pending":
            return JSONResponse({"error": "authorization_pending"}, status_code=400)
        if state != "approved" or not row:
            return JSONResponse({"error": "expired_token"}, status_code=400)
        try:
            return JSONResponse(mcp_oauth.issue_agent_tokens(hub.store_for(str(row["user_id"])), str(row.get("name") or "")))
        except PermissionError as exc:
            return JSONResponse({"error": "access_denied", "error_description": str(exc)}, status_code=403)
    token_value = str(form.get("code") or form.get("refresh_token") or "")
    ustore = _oauth_store_for_token(token_value)
    try:
        if grant == "authorization_code":
            data = mcp_oauth.exchange_code(
                ustore,
                code=str(form.get("code") or ""),
                redirect_uri=str(form.get("redirect_uri") or ""),
                client_id=str(form.get("client_id") or ""),
                code_verifier=str(form.get("code_verifier") or ""),
            )
        elif grant == "refresh_token":
            data = mcp_oauth.refresh_tokens(ustore, str(form.get("refresh_token") or ""))
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    except ValueError:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    return JSONResponse(data)


@app.post("/oauth/revoke")
async def oauth_revoke(request: Request) -> dict:
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    token_value = str(form.get("token") or "")
    ustore = _oauth_store_for_token(token_value)
    mcp_oauth.revoke(ustore, token_value)
    return {"ok": True}


@app.get("/api/agents")
async def agents_dashboard(realitify_session: str | None = Cookie(default=None, alias="realitify_session")) -> dict:
    _, ustore = _user_store(realitify_session)
    return agent_hub.dashboard_payload(ustore)


@app.post("/api/agents/keys")
async def agents_create_key(
    payload: dict[str, Any] | None = Body(None),
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        created = agent_hub.create_key(ustore, str((payload or {}).get("name") or ""))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**created, "snippets": agent_hub.connector_snippets(created["token"])}


@app.delete("/api/agents/keys/{key_id}")
async def agents_delete_key(
    key_id: str,
    realitify_session: str | None = Cookie(default=None, alias="realitify_session"),
) -> dict:
    _, ustore = _user_store(realitify_session)
    try:
        return agent_hub.delete_key(ustore, key_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/agents/openapi.json")
async def agents_openapi() -> dict:
    return agent_hub.openapi_spec()


@app.api_route("/mcp", methods=["GET", "POST", "HEAD"])
async def mcp_endpoint(request: Request):
    _, mcp_store = _agent_key(request)
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
            item = agent_hub.handle_mcp(mcp_store, msg if isinstance(msg, dict) else {}, _shared_catalog_for(mcp_store))
            if item is not None:
                out.append(item)
        return _mcp_http(request, out)
    result = agent_hub.handle_mcp(mcp_store, payload if isinstance(payload, dict) else {}, _shared_catalog_for(mcp_store))
    return _mcp_http(request, result)


def _require_https(request: Request) -> None:
    """Admin MCP carries full admin power: refuse plaintext transport when the site is served over HTTPS."""
    if config.PUBLIC_BASE_URL.startswith("https://"):
        proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
        if proto != "https":
            raise HTTPException(403, "Admin MCP je dostupné jen přes HTTPS")


@app.get("/api/admin/mcp-key")
async def admin_mcp_key_get(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    admin = await _admin_user_async(realitify_admin)
    key = await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, admin_mcp.get_key, hub.store, str(admin.get("id")))
    return {"key": key, "mcp_url": admin_mcp.mcp_url(), "snippets": admin_mcp.connector_snippets()}


@app.post("/api/admin/mcp-key")
async def admin_mcp_key_create(
    payload: dict[str, Any] | None = Body(None),
    realitify_admin: str | None = Cookie(default=None, alias="realitify_admin"),
) -> dict:
    admin = await _admin_user_async(realitify_admin)
    try:
        created = await asyncio.get_running_loop().run_in_executor(
            _ADMIN_POOL, admin_mcp.create_key, hub.store, admin, str((payload or {}).get("name") or "")
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"key": created, "snippets": admin_mcp.connector_snippets(created["token"])}


@app.delete("/api/admin/mcp-key")
async def admin_mcp_key_delete(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    admin = await _admin_user_async(realitify_admin)
    try:
        return await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, admin_mcp.delete_key, hub.store, str(admin.get("id")))
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/.well-known/oauth-protected-resource/mcp-admin")
async def admin_oauth_protected_resource() -> dict:
    return admin_oauth.protected_resource_metadata()


@app.get("/.well-known/oauth-authorization-server/admin-oauth")
@app.get("/.well-known/openid-configuration/admin-oauth")
@app.get("/admin-oauth/.well-known/oauth-authorization-server")
@app.get("/admin-oauth/.well-known/openid-configuration")
async def admin_oauth_authorization_server() -> dict:
    return admin_oauth.authorization_server_metadata()


@app.post("/oauth/admin/register")
async def admin_oauth_register(request: Request, payload: dict[str, Any] | None = Body(None)):
    _require_https(request)
    _rate_limit_or_429(request, "admin_oauth_register", "", 20, 3600)
    try:
        created = await asyncio.get_running_loop().run_in_executor(
            _ADMIN_POOL, admin_oauth.register_client, hub.store, payload or {}
        )
    except ValueError as exc:
        return JSONResponse({"error": "invalid_redirect_uri", "error_description": str(exc)}, status_code=400)
    return JSONResponse(created, status_code=201)


_ADMIN_CONSENT_HEADERS = {
    "Cache-Control": "no-store",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


async def _admin_consent_page(params: dict[str, str], *, error: str = "", status: int = 200, email: str = "") -> HTMLResponse:
    name = ""
    try:
        name = (await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, admin_oauth.validate_authorize, hub.store, params))["client_name"]
    except ValueError as exc:
        error, status = error or str(exc), 400
    # An invalid request (unknown client, bad redirect_uri) gets an error page, never a password form.
    page = admin_oauth.consent_html(params, client_name=name, error=error, email=email, show_form=bool(name))
    return HTMLResponse(page, status_code=status, headers=_ADMIN_CONSENT_HEADERS)


@app.get("/oauth/admin/authorize")
async def admin_oauth_authorize_get(request: Request):
    _require_https(request)
    return await _admin_consent_page({key: str(value) for key, value in request.query_params.items()})


@app.post("/oauth/admin/authorize")
async def admin_oauth_authorize_post(request: Request):
    _require_https(request)
    # Password guessing: per IP, and per e-mail so a botnet cannot spread over addresses either.
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    email = str(form.get("email") or "").strip().lower()
    _rate_limit_or_429(request, "admin_oauth_authorize", "", 10, 600)
    if email:
        _rate_limit_or_429(request, "admin_oauth_authorize_email", email, 6, 600)
    try:
        target, admin = await asyncio.get_running_loop().run_in_executor(
            _ADMIN_POOL, admin_oauth.complete_authorize, hub.store, form, email, str(form.get("password") or "")
        )
    except ValueError as exc:
        return await _admin_consent_page(form, error=str(exc), status=403, email=email)
    admin_mcp._audit(hub.store, admin, f"OAuth přístup do Admin MCP povolen klientovi {admin.get('_client_name')}")
    return RedirectResponse(target, status_code=303, headers=_ADMIN_CONSENT_HEADERS)


@app.post("/oauth/admin/token")
async def admin_oauth_token(request: Request):
    _require_https(request)
    _rate_limit_or_429(request, "admin_oauth_token", "", 120, 600)
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    grant = str(form.get("grant_type") or "")
    loop = asyncio.get_running_loop()
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    try:
        if grant == "authorization_code":
            data = await loop.run_in_executor(
                _ADMIN_POOL,
                lambda: admin_oauth.exchange_code(
                    hub.store,
                    code=str(form.get("code") or ""),
                    redirect_uri=str(form.get("redirect_uri") or ""),
                    client_id=str(form.get("client_id") or ""),
                    code_verifier=str(form.get("code_verifier") or ""),
                ),
            )
        elif grant == "refresh_token":
            data = await loop.run_in_executor(
                _ADMIN_POOL,
                admin_oauth.refresh_tokens,
                hub.store,
                str(form.get("refresh_token") or ""),
                str(form.get("client_id") or ""),
            )
        else:
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400, headers=headers)
    except ValueError:
        return JSONResponse({"error": "invalid_grant"}, status_code=400, headers=headers)
    return JSONResponse(data, headers=headers)


@app.post("/oauth/admin/revoke")
async def admin_oauth_revoke(request: Request) -> dict:
    _require_https(request)
    _rate_limit_or_429(request, "admin_oauth_revoke", "", 60, 600)
    form = {str(key): str(value) for key, value in (await request.form()).items()}
    await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, admin_oauth.revoke, hub.store, str(form.get("token") or ""))
    return {"ok": True}


@app.get("/api/admin/mcp-oauth")
async def admin_mcp_oauth_list(realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")) -> dict:
    admin = await _admin_user_async(realitify_admin)
    grants = await asyncio.get_running_loop().run_in_executor(_ADMIN_POOL, admin_oauth.list_grants, hub.store, str(admin.get("id")))
    return {"grants": grants, "mcp_url": admin_mcp.mcp_url()}


@app.delete("/api/admin/mcp-oauth/{grant_id}")
async def admin_mcp_oauth_revoke(
    grant_id: str, realitify_admin: str | None = Cookie(default=None, alias="realitify_admin")
) -> dict:
    admin = await _admin_user_async(realitify_admin)
    try:
        await asyncio.get_running_loop().run_in_executor(
            _ADMIN_POOL, admin_oauth.revoke_grant, hub.store, str(admin.get("id")), grant_id
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.api_route("/mcp-admin", methods=["GET", "POST", "HEAD"])
async def admin_mcp_endpoint(request: Request):
    _require_https(request)
    try:
        admin = await asyncio.get_running_loop().run_in_executor(
            _ADMIN_POOL, admin_mcp.authenticate, hub.store, request.headers.get("authorization")
        )
    except PermissionError as exc:
        # The WWW-Authenticate header is what lets a client (claude.ai) discover the OAuth login for this URL.
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": admin_oauth.www_authenticate()}) from exc
    if not admin_mcp.rate_ok(str(admin.get("_key_id"))):
        raise HTTPException(429, "Příliš mnoho požadavků. Zkuste to za chvíli.")
    if request.method != "POST":
        return {"name": "realitify-admin", "protocolVersion": admin_mcp.PROTOCOL, "mcp_url": admin_mcp.mcp_url()}
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(400, "Očekávám JSON-RPC") from exc
    if isinstance(payload, list):
        out = []
        for msg in payload:
            item = await admin_mcp.handle(app, hub.store, admin, msg if isinstance(msg, dict) else {})
            if item is not None:
                out.append(item)
        return _mcp_http(request, out)
    return _mcp_http(request, await admin_mcp.handle(app, hub.store, admin, payload if isinstance(payload, dict) else {}))


@app.get("/api/v1/listings")
async def agent_search_listings(request: Request) -> dict:
    _, mcp_store = _agent_key(request)
    args = dict(request.query_params)
    return agent_hub.run_tool(mcp_store, "search_listings", args, _shared_catalog_for(mcp_store))


@app.get("/api/v1/listings/{monitor_id}/{listing_id}")
async def agent_get_listing(request: Request, monitor_id: str, listing_id: int) -> dict:
    _, mcp_store = _agent_key(request)
    try:
        return agent_hub.run_tool(mcp_store, "get_listing", {"monitor_id": monitor_id, "listing_id": listing_id}, _shared_catalog_for(mcp_store))
    except (KeyError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/v1/monitors")
async def agent_list_monitors(request: Request) -> dict:
    _, mcp_store = _agent_key(request)
    return agent_hub.run_tool(mcp_store, "list_monitors", {})


@app.post("/api/v1/monitors")
async def agent_create_monitor(request: Request, payload: dict[str, Any] | None = Body(None)) -> dict:
    _, mcp_store = _agent_key(request)
    try:
        return agent_hub.run_tool(mcp_store, "create_monitor", payload or {})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/v1/monitors/{monitor_id}")
async def agent_delete_monitor(request: Request, monitor_id: str) -> dict:
    _, mcp_store = _agent_key(request)
    try:
        return agent_hub.run_tool(mcp_store, "delete_monitor", {"id": monitor_id})
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/alerts")
async def agent_list_alerts(request: Request) -> dict:
    _, mcp_store = _agent_key(request)
    return agent_hub.run_tool(mcp_store, "list_alerts", dict(request.query_params))


@app.get("/api/v1/account")
async def agent_get_account(request: Request) -> dict:
    _, mcp_store = _agent_key(request)
    return agent_hub.run_tool(mcp_store, "get_account", {})
