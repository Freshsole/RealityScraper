"""Admin MCP: lets an AI client run the whole admin (every /api endpoint) with one personal key per admin.

Separate from the customer-facing MCP in app/agents.py. A key belongs to one admin account and is hashed
at rest (the plaintext token is shown exactly once). Every tool call re-checks that the owner is still an
admin, then replays the request in-process against this very app with a short-lived admin session, so the
AI has exactly the powers of the logged-in admin UI and nothing is duplicated.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app import admin_oauth, config
from app import users as user_registry
from app.store import Store
from app.version import current_version

KEYS_META = "admin_mcp_keys"
TOKEN_PREFIX = "rf_admin_"
PROTOCOL = "2025-03-26"
SESSION_TTL_SEC = 120
RATE_LIMIT = 120  # calls per minute per key
MAX_RESPONSE_CHARS = 200_000
ADMIN_COOKIE = "realitify_admin"

# The AI must not be able to mint or read its own credentials: key management stays a human action in the UI.
_BLOCKED_PREFIXES = (
    "/api/admin/mcp-key",
    "/api/admin/mcp-oauth",
    "/mcp-admin",
    "/oauth/admin",
    "/admin-oauth",
    "/api/admin/login",
    "/api/admin/logout",
)

_calls: dict[str, deque[float]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mcp_url() -> str:
    return f"{config.PUBLIC_BASE_URL.rstrip('/')}/mcp-admin"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read(store: Store) -> list[dict[str, Any]]:
    raw = store.get_meta(KEYS_META) or ""
    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []
    return [row for row in data if isinstance(row, dict) and row.get("id")]


def _write(store: Store, rows: list[dict[str, Any]]) -> None:
    store.set_meta(KEYS_META, json.dumps(rows, ensure_ascii=False))


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name") or "Admin AI",
        "prefix": row.get("prefix") or "",
        "created_at": row.get("created_at") or "",
        "last_used_at": row.get("last_used_at") or "",
        "mcp_url": mcp_url(),
    }


def get_key(store: Store, admin_id: str) -> dict[str, Any] | None:
    for row in _read(store):
        if str(row.get("admin_id")) == str(admin_id):
            return _public(row)
    return None


def create_key(store: Store, admin: dict[str, Any], name: str = "") -> dict[str, Any]:
    """One key per admin: creating while one exists is refused, rotate = delete + create."""
    admin_id = str(admin.get("id") or "")
    rows = _read(store)
    if any(str(row.get("admin_id")) == admin_id for row in rows):
        raise ValueError("Admin už má přístup. Nejdřív ho zrušte, pak lze vytvořit nový.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(40)
    row = {
        "id": secrets.token_hex(8),
        "admin_id": admin_id,
        "admin_email": str(admin.get("email") or ""),
        "name": (name or "Admin AI").strip()[:80] or "Admin AI",
        "prefix": raw[:14],
        "hash": _hash(raw),
        "created_at": _now(),
        "last_used_at": "",
    }
    rows.append(row)
    _write(store, rows)
    return {**_public(row), "token": raw}


def delete_key(store: Store, admin_id: str) -> dict[str, Any]:
    rows = _read(store)
    kept = [row for row in rows if str(row.get("admin_id")) != str(admin_id)]
    if len(kept) == len(rows):
        raise KeyError("Přístup neexistuje")
    _write(store, kept)
    return {"ok": True}


def authenticate(store: Store, authorization: str | None) -> dict[str, Any]:
    """Return the admin user row for a valid key, else raise PermissionError."""
    header = (authorization or "").strip()
    token = header[7:].strip() if header.lower().startswith("bearer ") else header
    if token.startswith(admin_oauth.ACCESS_PREFIX):
        grant = admin_oauth.authenticate_token(store, token)
        admin = user_registry.get_user_by_id(store, str(grant.get("admin_id"))) if grant else None
        if not grant or not admin or (admin.get("role") or "") != "admin":
            raise PermissionError("Neplatný nebo vypršený přístup (přihlaste konektor znovu)")
        return {**admin, "_key_id": f"oauth:{grant['id']}", "_client": str(grant.get("client_name") or "OAuth")}
    if not token.startswith(TOKEN_PREFIX):
        raise PermissionError("Neplatný nebo chybějící admin klíč")
    digest = _hash(token)
    rows = _read(store)
    for row in rows:
        if hmac.compare_digest(str(row.get("hash") or ""), digest):
            admin = user_registry.get_user_by_id(store, str(row.get("admin_id")))
            if not admin or (admin.get("role") or "") != "admin":
                raise PermissionError("Vlastník klíče už není admin")
            row["last_used_at"] = _now()
            _write(store, rows)
            return {**admin, "_key_id": row["id"]}
    raise PermissionError("Neplatný nebo chybějící admin klíč")


def rate_ok(key_id: str) -> bool:
    now = time.monotonic()
    bucket = _calls.setdefault(key_id, deque())
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    return True


# ------------------------------------------------------------------ tools --

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_endpoints",
        "description": (
            "List every HTTP endpoint of the Realitify app (method, path, summary). Admin endpoints live under /api/admin/*, "
            "user-level ones under /api/*. Use 'contains' to narrow the list, e.g. 'email', 'promo', 'users'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"contains": {"type": "string", "description": "Case-insensitive substring of the path"}},
        },
    },
    {
        "name": "describe_endpoint",
        "description": "Return the OpenAPI definition (parameters, request body schema) of one endpoint path.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path template as shown by list_endpoints"}},
            "required": ["path"],
        },
    },
    {
        "name": "call_api",
        "description": (
            "Call any endpoint of the app with full admin rights (read, create, overwrite, delete: all settings, users, plans, "
            "e-mail campaigns, CMS, promo codes, monitors, scraping...). Authenticated as the admin who owns this key. "
            "Pass as_user_id to call user-level endpoints (/api/account, /api/monitors...) as that specific user."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                "path": {"type": "string", "description": "Concrete path, e.g. /api/admin/users"},
                "query": {"type": "object", "description": "Query string parameters"},
                "body": {"description": "JSON request body"},
                "as_user_id": {"type": "string", "description": "Act as this user id instead of the admin on user endpoints"},
            },
            "required": ["method", "path"],
        },
    },
]


def _tool_text(payload: Any) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=1, default=str)
    if len(text) > MAX_RESPONSE_CHARS:
        return text[:MAX_RESPONSE_CHARS] + f"\n…[zkráceno, celkem {len(text)} znaků]"
    return text


def _list_endpoints(app: Any, contains: str) -> list[dict[str, Any]]:
    needle = contains.strip().lower()
    out: list[dict[str, Any]] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = sorted((getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"})
        if not path or not methods or not (path.startswith("/api/") or path.startswith("/oauth")):
            continue
        if needle and needle not in path.lower():
            continue
        doc = (getattr(route, "endpoint", None).__doc__ or "").strip().split("\n")[0] if getattr(route, "endpoint", None) else ""
        out.append({"methods": methods, "path": path, "name": getattr(route, "name", ""), "summary": doc})
    out.sort(key=lambda item: item["path"])
    return out


def _normalise_path(raw: str) -> str:
    parts = urlsplit(raw.strip())
    path = parts.path or "/"
    if not path.startswith("/") or "\\" in path or ".." in path.split("/"):
        raise ValueError("Neplatná cesta")
    return path


async def _call_api(app: Any, store: Store, admin: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    method = str(args.get("method") or "GET").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError("Neplatná metoda")
    path = _normalise_path(str(args.get("path") or ""))
    if any(path == p or path.startswith(p + "/") for p in _BLOCKED_PREFIXES):
        raise PermissionError("Správa klíčů a přihlášení je dostupná jen v administraci, ne přes MCP")
    query = args.get("query") if isinstance(args.get("query"), dict) else None
    admin_id = str(admin.get("id"))
    tokens = [user_registry.create_session(store, admin_id, SESSION_TTL_SEC)]
    cookies = {ADMIN_COOKIE: tokens[0], "realitify_session": tokens[0]}
    as_user = str(args.get("as_user_id") or "").strip()
    try:
        if as_user:
            if not user_registry.get_user_by_id(store, as_user):
                raise ValueError("Uživatel neexistuje")
            tokens.append(user_registry.create_session(store, as_user, SESSION_TTL_SEC))
            cookies["realitify_session"] = tokens[-1]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://admin-mcp.internal", cookies=cookies, timeout=120, follow_redirects=False
        ) as client:
            kwargs: dict[str, Any] = {"params": query}
            if method != "GET" and "body" in args and args["body"] is not None:
                kwargs["json"] = args["body"]
            response = await client.request(method, path, **kwargs)
    finally:
        for token in tokens:
            user_registry.delete_session(store, token)
    ctype = response.headers.get("content-type", "")
    if "json" in ctype:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
    elif ctype.startswith("text/") or not ctype:
        body = response.text
    else:
        body = f"<binární odpověď {ctype}, {len(response.content)} B>"
    return {"status": response.status_code, "ok": response.is_success, "body": body}


async def run_tool(app: Any, store: Store, admin: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    if name == "list_endpoints":
        return _list_endpoints(app, str(args.get("contains") or ""))
    if name == "describe_endpoint":
        path = str(args.get("path") or "")
        spec = app.openapi()
        item = (spec.get("paths") or {}).get(path)
        if not item:
            raise KeyError("Endpoint v OpenAPI neexistuje (zkuste list_endpoints)")
        return {"path": path, "operations": item, "components": spec.get("components", {}).get("schemas", {})}
    if name == "call_api":
        result = await _call_api(app, store, admin, args)
        method = str(args.get("method") or "GET").upper()
        if method != "GET":
            _audit(store, admin, f"AI (MCP): {method} {args.get('path')} → {result['status']}")
        return result
    raise KeyError(f"Neznámý nástroj: {name}")


def _audit(store: Store, admin: dict[str, Any], text: str) -> None:
    try:
        from app import admin as admin_panel

        client = str(admin.get("_client") or "")
        admin_panel._audit(store, (f"[{client}] " if client else "") + text[:300], str(admin.get("email") or "Admin"))
    except Exception:
        pass


async def handle(app: Any, store: Store, admin: dict[str, Any], message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = str(message.get("method") or "")
    msg_id = message.get("id")
    if method.startswith("notifications/") or msg_id is None:
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in {"2024-11-05", "2025-03-26", "2025-06-18"} else PROTOCOL
        return ok(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "realitify-admin", "version": current_version()},
                "instructions": (
                    "Realitify ADMIN MCP s plnými právy admina. Začni list_endpoints, detail přes describe_endpoint, "
                    "provádění přes call_api. Změny jsou ostré (produkce) a zapisují se do auditu."
                ),
            }
        )
    if method in {"ping", "logging/setLevel"}:
        return ok({})
    if method == "resources/list":
        return ok({"resources": []})
    if method == "prompts/list":
        return ok({"prompts": []})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            payload = await run_tool(app, store, admin, name, arguments)
            return ok({"content": [{"type": "text", "text": _tool_text(payload)}], "isError": False})
        except (PermissionError, KeyError, ValueError) as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # a failing endpoint must not kill the MCP session
            return ok({"content": [{"type": "text", "text": f"Chyba: {type(exc).__name__}: {exc}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def connector_snippets(token: str = "rf_admin_VÁŠ_KLÍČ") -> dict[str, str]:
    url = mcp_url()
    return {
        "claude_ai": f"Nastavení → Konektory → Přidat vlastní konektor → URL {url} → přihlásit se admin e-mailem a heslem (bez klíče).",
        "claude_code": f'claude mcp add --transport http realitify-admin {url} --header "Authorization: Bearer {token}"',
        "json": json.dumps(
            {"mcpServers": {"realitify-admin": {"url": url, "headers": {"Authorization": f"Bearer {token}"}}}},
            indent=2,
        ),
    }
