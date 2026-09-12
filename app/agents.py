from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from app import billing as stripe_billing
from app import config
from app.store import Store
from app.version import current_version

KEYS_META = "agent_api_keys"
MAX_KEYS = 8
TOKEN_PREFIX = "rf_live_"
PROTOCOL = "2025-03-26"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mcp_enabled(store: Store) -> bool:
    return bool(stripe_billing.billing_state(store).get("mcp"))


def mcp_url() -> str:
    return f"{config.PUBLIC_BASE_URL.rstrip('/')}/mcp"


def openapi_url() -> str:
    return f"{config.PUBLIC_BASE_URL.rstrip('/')}/api/agents/openapi.json"


def _read_keys(store: Store) -> list[dict[str, Any]]:
    raw = store.get_meta(KEYS_META) or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict) and item.get("id")]


def _write_keys(store: Store, rows: list[dict[str, Any]]) -> None:
    store.set_meta(KEYS_META, json.dumps(rows, ensure_ascii=False))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_key(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name") or "Agent",
        "prefix": row.get("prefix") or "",
        "created_at": row.get("created_at") or "",
        "last_used_at": row.get("last_used_at") or "",
    }


def list_keys(store: Store) -> list[dict[str, Any]]:
    return [public_key(row) for row in _read_keys(store)]


def create_key(store: Store, name: str = "") -> dict[str, Any]:
    if not mcp_enabled(store):
        raise PermissionError("MCP a AI agenti jsou v tarifu PRO")
    rows = _read_keys(store)
    if len(rows) >= MAX_KEYS:
        raise ValueError(f"Maximum je {MAX_KEYS} klíčů. Nejdřív jeden smažte.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    row = {
        "id": secrets.token_hex(8),
        "name": (name or "Agent").strip()[:80] or "Agent",
        "prefix": raw[:16],
        "hash": _hash_token(raw),
        "created_at": _now(),
        "last_used_at": "",
    }
    rows.append(row)
    _write_keys(store, rows)
    return {**public_key(row), "token": raw}


def delete_key(store: Store, key_id: str) -> dict[str, Any]:
    rows = _read_keys(store)
    kept = [row for row in rows if row.get("id") != key_id]
    if len(kept) == len(rows):
        raise KeyError("Klíč neexistuje")
    _write_keys(store, kept)
    return {"ok": True}


def authenticate(store: Store, authorization: str | None) -> dict[str, Any] | None:
    header = (authorization or "").strip()
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    else:
        token = header
    if not token:
        return None
    digest = _hash_token(token)
    rows = _read_keys(store)
    for row in rows:
        hashed = str(row.get("hash") or "")
        if hashed and hmac.compare_digest(hashed, digest):
            row["last_used_at"] = _now()
            _write_keys(store, rows)
            return row
    from app import mcp_oauth

    return mcp_oauth.authenticate_token(store, token)


def require_mcp(store: Store, authorization: str | None) -> dict[str, Any]:
    if not mcp_enabled(store):
        raise PermissionError("MCP a AI agenti jsou v tarifu PRO")
    key = authenticate(store, authorization)
    if not key:
        raise PermissionError("Neplatný nebo chybějící API klíč")
    return key


def compact_listing(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "monitor_id": item.get("monitor_id"),
        "name": item.get("name") or item.get("title") or "",
        "price_label": item.get("price_label") or "",
        "price_czk": item.get("price_czk"),
        "disposition": item.get("disposition") or "",
        "area_m2": item.get("area_m2"),
        "locality": item.get("locality") or "",
        "url": item.get("url") or "",
        "portal": item.get("portal") or "",
        "gone": bool(item.get("gone")),
        "first_seen": item.get("first_seen") or "",
    }


def compact_monitor(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "name": item.get("name") or "",
        "search_url": item.get("search_url") or "",
        "enabled": bool(item.get("enabled")),
        "tracked": item.get("tracked") or 0,
        "new_today": item.get("new_today") or 0,
        "portals": item.get("portals") or "all",
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def run_tool(store: Store, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments if isinstance(arguments, dict) else {}
    if name == "get_account":
        bill = stripe_billing.billing_state(store)
        return {
            "plan": bill.get("plan"),
            "label": bill.get("label"),
            "watch_limit": bill.get("watch_limit"),
            "mcp": True,
            "monitors": len(store.list_monitors()),
        }
    if name == "search_listings":
        filters = {
            "q": str(args.get("q") or ""),
            "disposition": str(args.get("disposition") or ""),
            "price_from": str(args.get("price_from") or args.get("price_min") or ""),
            "price_to": str(args.get("price_to") or args.get("price_max") or ""),
            "area_from": str(args.get("area_from") or ""),
            "area_to": str(args.get("area_to") or ""),
            "offer": str(args.get("offer") or ""),
            "portal": str(args.get("portal") or ""),
            "locality": str(args.get("locality") or args.get("district") or ""),
            "district": str(args.get("district") or args.get("locality") or ""),
            "status": str(args.get("status") or ""),
            "sort": str(args.get("sort") or "newest"),
            "limit": min(max(_as_int(args.get("limit"), 12), 1), 25),
            "offset": max(_as_int(args.get("offset"), 0), 0),
        }
        data = store.catalog(filters)
        items = [compact_listing(item) for item in (data.get("items") or [])]
        return {"count": len(items), "total": data.get("total") or len(items), "items": items}
    if name == "get_listing":
        monitor_id = str(args.get("monitor_id") or "")
        listing_id = args.get("listing_id") or args.get("id")
        try:
            listing_id_n = int(listing_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Chybí listing_id") from exc
        item = store.catalog_item(monitor_id, listing_id_n)
        if not item:
            raise KeyError("Inzerát neexistuje")
        return compact_listing(item)
    if name == "list_monitors":
        return {"items": [compact_monitor(item) for item in store.list_monitors()]}
    if name == "create_monitor":
        url = str(args.get("search_url") or args.get("url") or "").strip()
        if not url:
            raise ValueError("Chybí search_url")
        saved = store.save_monitor(
            {
                "name": str(args.get("name") or "MCP monitor").strip()[:120],
                "search_url": url,
                "enabled": bool(args.get("enabled", True)),
            }
        )
        return compact_monitor(saved)
    if name == "delete_monitor":
        monitor_id = str(args.get("id") or args.get("monitor_id") or "")
        if not monitor_id:
            raise ValueError("Chybí id monitoru")
        store.delete_monitor(monitor_id)
        return {"ok": True, "id": monitor_id}
    if name == "preview_monitor":
        monitor_id = str(args.get("id") or args.get("monitor_id") or "")
        monitor = store.get_monitor(monitor_id)
        if not monitor:
            raise KeyError("Monitor neexistuje")
        items = store.monitor_preview(monitor_id)
        return {"monitor": compact_monitor(monitor), "items": [compact_listing(item) for item in items[:15]]}
    if name == "list_alerts":
        limit = min(max(_as_int(args.get("limit"), 12), 1), 25)
        monitor_id = str(args.get("monitor_id") or "") or None
        items = store.recent_notified(limit, monitor_id)
        return {"items": [compact_listing(item) for item in items[:limit]]}
    raise KeyError(f"Neznámý nástroj: {name}")


TOOLS = [
    {
        "name": "search_listings",
        "description": "Vyhledá inzeráty v katalogu Realitify (Sreality, Bezrealitky a další portály).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Volný text (ulice, lokalita, slova)"},
                "disposition": {"type": "string", "description": "Např. 2+kk, 3+1"},
                "price_from": {"type": "string"},
                "price_to": {"type": "string"},
                "area_from": {"type": "string"},
                "area_to": {"type": "string"},
                "offer": {"type": "string", "description": "pronajem nebo prodej"},
                "portal": {"type": "string"},
                "district": {"type": "string"},
                "sort": {"type": "string", "enum": ["newest", "price_asc", "price_desc"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            },
        },
    },
    {
        "name": "get_listing",
        "description": "Detail jednoho inzerátu podle monitor_id a listing_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "monitor_id": {"type": "string"},
                "listing_id": {"type": "integer"},
            },
            "required": ["monitor_id", "listing_id"],
        },
    },
    {
        "name": "list_monitors",
        "description": "Seznam hlídacích profilů (psů) na účtu.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_monitor",
        "description": "Založí hlídacího psa z URL hledání (Sreality nebo Bezrealitky).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "search_url": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "required": ["search_url"],
        },
    },
    {
        "name": "delete_monitor",
        "description": "Smaže hlídacího psa.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "preview_monitor",
        "description": "Náhled aktuálních zásahů hlídacího psa.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_alerts",
        "description": "Poslední notifikované nové nebo zlevněné byty.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "monitor_id": {"type": "string"},
            },
        },
    },
    {
        "name": "get_account",
        "description": "Tarif, limity a počet hlídacích psů.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def tool_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def handle_mcp(store: Store, message: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = str(message.get("method") or "")
    msg_id = message.get("id")
    if method.startswith("notifications/") or msg_id is None:
        return None
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in {"2024-11-05", "2025-03-26", "2025-06-18"} else PROTOCOL
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": "realitify", "version": current_version()},
                "instructions": "Realitify MCP: hledej inzeráty, spravuj hlídací psy a čti poslední upozornění.",
            },
        }
    if method in {"ping", "logging/setLevel"}:
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            payload = run_tool(store, name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": tool_text(payload)}], "isError": False},
            }
        except PermissionError as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
        except (KeyError, ValueError) as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            }
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def connector_snippets(token: str = "rf_live_VÁŠ_KLÍČ") -> dict[str, str]:
    url = mcp_url()
    cursor = {
        "mcpServers": {
            "realitify": {
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }
    claude = {
        "mcpServers": {
            "realitify": {
                "command": "npx",
                "args": ["-y", "mcp-remote", url, "--header", f"Authorization: Bearer {token}"],
            }
        }
    }
    https_ok = url.lower().startswith("https://")
    claude_web = (
        f"Název: Realitify\n"
        f"URL: {url}\n"
        "OAuth Client ID: nechte prázdné\n"
        "OAuth Client Secret: nechte prázdné\n"
        "Claude vás přesměruje na Realitify. Přihlaste se a klikněte Povolit Claude."
    )
    if not https_ok:
        claude_web = (
            "Claude.ai vyžaduje veřejné https:// na portu 443. Localhost ani http:// nevezme.\n"
            "1) Nasadťe Realitify (Railway) a v .env nastavte PUBLIC_BASE_URL=https://vaše-doména\n"
            "2) Do Claude zadejte https://vaše-doména/mcp\n"
            "3) Client ID i secret nechte prázdné — Claude se přihlásí přes OAuth.\n\n"
            "Teď máte jen HTTP, proto URL z tohoto počítače Claude.ai nepřijme.\n"
            "Pro zkoušení na Macu použijte Claude Desktop (blok níže) nebo HTTPS tunel na port 443."
        )
    return {
        "cursor": json.dumps(cursor, ensure_ascii=False, indent=2),
        "claude": json.dumps(claude, ensure_ascii=False, indent=2),
        "claude_web": claude_web,
        "chatgpt": f"OpenAPI: {openapi_url()}\nAutentizace: Bearer {token}",
        "grok": f"Remote MCP\nURL: {url}\nHeader: Authorization: Bearer {token}",
    }


def dashboard_payload(store: Store) -> dict[str, Any]:
    enabled = mcp_enabled(store)
    token_placeholder = "rf_live_VÁŠ_KLÍČ"
    return {
        "enabled": enabled,
        "plan": stripe_billing.billing_state(store).get("plan"),
        "mcp_url": mcp_url(),
        "https": mcp_url().lower().startswith("https://"),
        "openapi_url": openapi_url(),
        "keys": list_keys(store) if enabled else [],
        "snippets": connector_snippets(token_placeholder),
        "max_keys": MAX_KEYS,
    }


def openapi_spec() -> dict[str, Any]:
    base = config.PUBLIC_BASE_URL.rstrip("/")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Realitify Agents API",
            "version": current_version(),
            "description": "REST pro AI agenty (ChatGPT, Claude, Grok). Vyžaduje tarif PRO a Bearer API klíč.",
        },
        "servers": [{"url": base}],
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/api/v1/listings": {
                "get": {
                    "operationId": "searchListings",
                    "summary": "Vyhledat inzeráty",
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "disposition", "in": "query", "schema": {"type": "string"}},
                        {"name": "price_to", "in": "query", "schema": {"type": "string"}},
                        {"name": "offer", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                    ],
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/monitors": {
                "get": {
                    "operationId": "listMonitors",
                    "summary": "Seznam hlídacích psů",
                    "responses": {"200": {"description": "OK"}},
                },
                "post": {
                    "operationId": "createMonitor",
                    "summary": "Založit hlídacího psa",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "search_url": {"type": "string"},
                                    },
                                    "required": ["search_url"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "OK"}},
                },
            },
            "/api/v1/alerts": {
                "get": {
                    "operationId": "listAlerts",
                    "summary": "Poslední upozornění",
                    "responses": {"200": {"description": "OK"}},
                }
            },
            "/api/v1/account": {
                "get": {
                    "operationId": "getAccount",
                    "summary": "Tarif účtu",
                    "responses": {"200": {"description": "OK"}},
                }
            },
        },
    }


_hits: dict[str, list[float]] = {}


def rate_ok(key_id: str, limit: int = 90, window: float = 60.0) -> bool:
    now = time.monotonic()
    bucket = [stamp for stamp in _hits.get(key_id, []) if now - stamp < window]
    if len(bucket) >= limit:
        _hits[key_id] = bucket
        return False
    bucket.append(now)
    _hits[key_id] = bucket
    return True
