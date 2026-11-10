"""Tools exposed to the chat model.

READ tools run immediately. WRITE tools only *propose* an action (ai/actions.py): the user confirms it with a click.
`navigate` moves the browser to a whitelisted page. Schemas are built from the signatures and docstrings below.

Never available here (the model sends the user to a page with `navigate` instead): payments and subscription, account,
e-mail, password, API keys / MCP tokens, admin, backups, webhooks, push subscription, profile / phone / avatar.
`user_id` is never a tool parameter: it comes from ToolContext, which the server fills from the session.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import types
import typing
from typing import Any, Union
from urllib.parse import parse_qs, quote, urlparse

from ai import actions
from ai.context import Services, ToolContext, ToolError
from app import billing

logger = logging.getLogger("realitify.ai")

MAX_LISTINGS = 10
MAX_DESCRIPTION = 500
MAX_NOTE = 500
COMMUTE_CANDIDATES = 20
COMMUTE_PARALLEL = 6

# Client-side navigation: only these pages, only relative paths.
NAVIGATE_PREFIXES = (
    "/prehled",
    "/nabidka",
    "/monitory",
    "/filtry",
    "/zprava",
    "/nastaveni",
    "/trh",
    "/hledat",
    "/index",
    "/faq",
    "/hry",
    "/uspechy",
    "/pronajem-praha",
    "/pronajem-brno",
    "/mcp-docs-cs",
    "/kontakt",
    "/obchodni-podminky",
    "/ochrana-soukromi",
)
NAVIGATE_HOME_FRAGMENTS = {"cenik", "jak-to-funguje", "srovnani"}


def safe_path(raw: Any) -> str:
    """Normalise a navigation target or raise ToolError. Only relative paths on whitelisted pages pass."""
    path = str(raw or "").strip()
    if not path or len(path) > 300 or not path.startswith("/") or path.startswith("//"):
        raise ToolError("Neplatná cesta. Povolené jsou jen relativní cesty v rámci Realitify.")
    if "\\" in path or "://" in path or any(ord(c) < 32 for c in path):
        raise ToolError("Neplatná cesta. Povolené jsou jen relativní cesty v rámci Realitify.")
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or ".." in parsed.path.split("/"):
        raise ToolError("Neplatná cesta. Povolené jsou jen relativní cesty v rámci Realitify.")
    base = parsed.path.rstrip("/") or "/"
    if base == "/":
        if parsed.fragment not in NAVIGATE_HOME_FRAGMENTS:
            raise ToolError("Tuto stránku nelze otevřít.")
    elif not any(base == p or base.startswith(p + "/") for p in NAVIGATE_PREFIXES):
        raise ToolError("Tuto stránku nelze otevřít.")
    return path


def _s(value: Any) -> str:
    return "" if value in (None, "", 0) else str(value)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class ChatTools:
    """Tool methods. Docstrings and type hints are the tool descriptions and schemas. `ctx` is hidden from the schema."""

    def __init__(self, services: Services) -> None:
        self._svc = services
        self._base = services.public_base_url.rstrip("/")

    # ---- READ: catalog (same six functions as the MCP server) ------------------------------------------------

    async def search_listings(
        self,
        ctx: ToolContext,
        locality: str = "",
        offer_type: str = "",
        max_price: int | None = None,
        min_price: int | None = None,
        disposition: str = "",
        min_area: int | None = None,
        limit: int = 10,
        sort: str = "newest",
        commute_point_id: str = "",
        max_commute_minutes: int | None = None,
        commute_mode: str = "transit",
    ) -> dict[str, Any]:
        """Hledá aktuální inzeráty bytů. locality: město nebo čtvrť (Praha, Brno, Praha 5). offer_type: pronajem nebo prodej. max_price/min_price v Kč. disposition např. 2+kk. min_area v m². sort: newest, cheapest nebo best_value. Dojezd: commute_point_id je id bodu z get_settings, max_commute_minutes limit v minutách, commute_mode transit (MHD), walk nebo drive. Adresu bodu doplní server."""
        wanted = min(max(int(limit or 10), 1), MAX_LISTINGS)
        data = await self._svc.catalog["search"](
            district=locality,
            offer=offer_type,
            price_to=_s(max_price),
            price_from=_s(min_price),
            disposition=disposition,
            area_from=_s(min_area),
            limit=COMMUTE_CANDIDATES if commute_point_id else wanted,
            sort=sort,
            facets="0",
        )
        return await self._listings_with_commute(ctx, data, wanted, commute_point_id, max_commute_minutes, commute_mode)

    async def new_listings(
        self,
        ctx: ToolContext,
        locality: str = "",
        offer_type: str = "pronajem",
        since_hours: int = 24,
        disposition: str = "",
        max_price: int | None = None,
        min_area: int | None = None,
        limit: int = 10,
        commute_point_id: str = "",
        max_commute_minutes: int | None = None,
        commute_mode: str = "transit",
    ) -> dict[str, Any]:
        """Nové inzeráty za posledních since_hours hodin (1 až 168). Použij, když se člověk ptá, co je nového. Dojezd funguje stejně jako u search_listings."""
        wanted = min(max(int(limit or 10), 1), MAX_LISTINGS)
        data = await self._svc.catalog["search"](
            district=locality,
            offer=offer_type,
            since_hours=min(max(int(since_hours or 24), 1), 168),
            disposition=disposition,
            price_to=_s(max_price),
            area_from=_s(min_area),
            limit=COMMUTE_CANDIDATES if commute_point_id else wanted,
            sort="newest",
            facets="0",
        )
        return await self._listings_with_commute(ctx, data, wanted, commute_point_id, max_commute_minutes, commute_mode)

    async def get_listing(self, ctx: ToolContext, listing_url: str) -> dict[str, Any]:
        """Detail jednoho inzerátu včetně uživatelovy poznámky a stavu (uložený, skrytý). listing_url je přesně pole url z výsledku search_listings nebo new_listings."""
        params = parse_qs(urlparse(listing_url or "").query)
        lookup: dict[str, str] = {}
        if params.get("listing_key"):
            lookup["listing_key"] = params["listing_key"][0]
        elif params.get("id"):
            lookup["id"] = params["id"][0]
        elif listing_url:
            lookup["url"] = params.get("url", [listing_url])[0]
        else:
            return {"error": "Chybí listing_url."}
        item = await self._svc.catalog["item"](**lookup)
        out = self._listing(item)
        out["_cards"] = [self._card(item)]
        description = str(item.get("description") or "").strip()
        if description:
            out["description"] = description[:MAX_DESCRIPTION]
        source = str(item.get("url") or "")
        if source:
            mine = await asyncio.to_thread(ctx.ustore.get_listing_user, source)
            if mine.get("note"):
                out["my_note"] = str(mine["note"])[:MAX_NOTE]
            if mine.get("status"):
                out["my_status"] = mine["status"]
        return out

    async def locality_stats(self, ctx: ToolContext, locality: str, offer_type: str = "", disposition: str = "") -> dict[str, Any]:
        """Statistiky lokality: počet aktivních inzerátů, medián a průměr ceny a Kč/m². Pro dotazy typu kolik stojí nájem v Brně."""
        return await self._svc.catalog["stats"](district=locality, offer=offer_type, disposition=disposition)

    async def price_check(
        self, ctx: ToolContext, locality: str, offer_type: str, price: int, disposition: str = "", area: int | None = None
    ) -> dict[str, Any]:
        """Porovná konkrétní cenu v Kč s podobnými inzeráty v lokalitě. offer_type je povinný: pronajem nebo prodej."""
        return await self._svc.catalog["price_check"](
            district=locality, offer=offer_type, price=int(price), disposition=disposition, area=int(area or 0)
        )

    async def compare_localities(
        self, ctx: ToolContext, localities: list[str], offer_type: str = "pronajem", disposition: str = ""
    ) -> dict[str, Any]:
        """Porovná ceny ve dvou a více lokalitách, např. ["Praha", "Brno"]."""
        return await self._svc.catalog["compare"](
            localities=",".join(str(x).strip() for x in localities if str(x).strip()),
            offer=offer_type,
            disposition=disposition,
        )

    # ---- READ: the user's own data -----------------------------------------------------------------------------

    async def list_my_monitors(self, ctx: ToolContext) -> dict[str, Any]:
        """Seznam uživatelových hlídání (hlídacích psů): id, název, zapnuto, portály."""
        items = await asyncio.to_thread(ctx.ustore.list_monitors)
        return {
            "items": [
                {
                    "id": m.get("id"),
                    "name": str(m.get("name") or "")[:80],
                    "enabled": bool(m.get("enabled")),
                    "portals": m.get("portals") or "all",
                    "search_url": str(m.get("search_url") or "")[:300],
                }
                for m in items
            ]
        }

    async def preview_monitor(self, ctx: ToolContext, monitor_id: str) -> dict[str, Any]:
        """Poslední nabídky, které zachytilo jedno hlídání. monitor_id z list_my_monitors."""
        monitor = await asyncio.to_thread(ctx.ustore.get_monitor, str(monitor_id))
        if not monitor:
            raise ToolError("Hlídání s tímto id neexistuje.")
        items = await asyncio.to_thread(ctx.ustore.monitor_preview, str(monitor_id))
        return self._listings({"items": items, "total": len(items)})

    async def convert_monitor(self, ctx: ToolContext, monitor_id: str) -> dict[str, Any]:
        """Převede hledání jednoho hlídání na jiný portál (např. Sreality na Bezrealitky). Jen navrhne adresu, nic nevytváří."""
        from app.filter_bridge import convert_search_url

        monitor = await asyncio.to_thread(ctx.ustore.get_monitor, str(monitor_id))
        if not monitor:
            raise ToolError("Hlídání s tímto id neexistuje.")
        converted = convert_search_url(monitor["search_url"])
        return {"from": converted.get("source"), "to": converted.get("target"), "url": converted.get("url")}

    async def list_saved_listings(self, ctx: ToolContext, status: str = "saved", limit: int = 10) -> dict[str, Any]:
        """Uživatelovy uložené (oblíbené, status saved) nebo skryté (status hidden) inzeráty."""
        if status not in {"saved", "hidden"}:
            raise ToolError("status musí být saved nebo hidden.")
        reader = self._svc.user_catalog(ctx.ustore) or ctx.ustore
        data = await asyncio.to_thread(
            reader.catalog,
            {"status": status, "limit": min(max(int(limit or 10), 1), MAX_LISTINGS), "sort": "newest", "facets": "0"},
        )
        return self._listings(data)

    async def get_my_plan(self, ctx: ToolContext) -> dict[str, Any]:
        """Uživatelův tarif a jeho limity: název, limit hlídání, interval kontroly, kolik hlídání je zapnutých. Platby ani předplatné měnit nelze."""
        state = await asyncio.to_thread(billing.billing_state, ctx.ustore)
        enabled = await asyncio.to_thread(ctx.ustore.enabled_monitor_count)
        return {
            "plan": state.get("plan"),
            "label": state.get("label"),
            "watch_limit": state.get("watch_limit"),
            "check_interval_sec": state.get("check_interval_sec"),
            "enabled_monitors": enabled,
            "features": state.get("features"),
        }

    async def get_settings(self, ctx: ToolContext) -> dict[str, Any]:
        """Nastavení notifikací, hodina denního souhrnu a body dojezdu (jen id a název, adresa se nikdy nezobrazuje)."""
        settings = await asyncio.to_thread(ctx.ustore.app_settings)
        return {
            "notify": settings.get("notify"),
            "digest_hour": settings.get("digest_hour"),
            "watch_prefs": settings.get("watch_prefs"),
            "commute_points": [
                {"id": p.get("id"), "label": str(p.get("name") or "Bod")[:40]} for p in settings.get("commute_points") or []
            ],
        }

    async def get_discord_status(self, ctx: ToolContext) -> dict[str, Any]:
        """Je k účtu připojený Discord?"""
        from app import account as user_account

        status = await asyncio.to_thread(user_account.discord_status, ctx.ustore)
        return {"linked": bool(status.get("linked"))}

    async def get_whatsapp_status(self, ctx: ToolContext) -> dict[str, Any]:
        """Je WhatsApp dostupný v tarifu a zapnutý? Telefonní číslo se nikdy nezobrazuje."""
        from app import whatsapp as wa_notify

        status = await asyncio.to_thread(wa_notify.status, ctx.ustore)
        return {
            "allowed_in_plan": bool(status.get("allowed")),
            "enabled": bool(status.get("enabled")),
            "has_phone": bool(status.get("phone_digits")),
        }

    # ---- WRITE: only proposals, the user confirms --------------------------------------------------------------

    async def create_monitor(
        self,
        ctx: ToolContext,
        locality: str,
        offer_type: str,
        disposition: list[str] | None = None,
        max_price: int | None = None,
        min_price: int | None = None,
        min_area: int | None = None,
        max_area: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        """Navrhne nové hlídání (hlídacího psa) podle filtrů. locality např. Praha 7, offer_type pronajem nebo prodej, disposition např. ["2+kk"]. Uživatel musí potvrdit."""
        return await self._propose(
            ctx,
            "create_monitor",
            {
                "locality": locality,
                "offer_type": offer_type,
                "disposition": disposition or [],
                "price_to": max_price,
                "price_from": min_price,
                "area_from": min_area,
                "area_to": max_area,
                "name": name,
            },
        )

    async def update_monitor(
        self, ctx: ToolContext, monitor_id: str, name: str = "", enabled: bool | None = None
    ) -> dict[str, Any]:
        """Navrhne přejmenování nebo zapnutí/vypnutí hlídání (monitor_id z list_my_monitors). Uživatel musí potvrdit."""
        return await self._propose(ctx, "update_monitor", {"monitor_id": monitor_id, "name": name, "enabled": enabled})

    async def delete_monitor(self, ctx: ToolContext, monitor_id: str) -> dict[str, Any]:
        """Navrhne smazání hlídání (monitor_id z list_my_monitors). Uživatel musí potvrdit."""
        return await self._propose(ctx, "delete_monitor", {"monitor_id": monitor_id})

    async def set_listing_status(self, ctx: ToolContext, listing_url: str, status: str) -> dict[str, Any]:
        """Navrhne uložení inzerátu mezi oblíbené (saved), skrytí (hidden) nebo vrácení (none). listing_url je pole source_url inzerátu. Uživatel musí potvrdit."""
        return await self._propose(ctx, "set_listing_status", {"listing_url": listing_url, "status": status})

    async def set_listing_note(self, ctx: ToolContext, listing_url: str, note: str) -> dict[str, Any]:
        """Navrhne uložení poznámky k inzerátu (prázdná poznámka ji smaže). listing_url je pole source_url inzerátu. Uživatel musí potvrdit."""
        return await self._propose(ctx, "set_listing_note", {"listing_url": listing_url, "note": note})

    async def update_notify_settings(
        self,
        ctx: ToolContext,
        email: bool | None = None,
        discord: bool | None = None,
        push: bool | None = None,
        instant: bool | None = None,
        quiet: bool | None = None,
        quiet_from: str | None = None,
        quiet_to: str | None = None,
        new_listings: bool | None = None,
        price_changes: bool | None = None,
        expirations: bool | None = None,
        digest: bool | None = None,
        tips: bool | None = None,
        digest_hour: int | None = None,
    ) -> dict[str, Any]:
        """Navrhne změnu nastavení notifikací (kanály, tiché hodiny ve tvaru HH:MM, druhy upozornění, hodina denního souhrnu 0 až 23). Posílej jen to, co se mění. Uživatel musí potvrdit."""
        return await self._propose(
            ctx,
            "update_notify_settings",
            {
                "email": email,
                "discord": discord,
                "push": push,
                "instant": instant,
                "quiet": quiet,
                "quiet_from": quiet_from,
                "quiet_to": quiet_to,
                "new_listings": new_listings,
                "price_changes": price_changes,
                "expirations": expirations,
                "digest": digest,
                "tips": tips,
                "digest_hour": digest_hour,
            },
        )

    async def set_commute_point(self, ctx: ToolContext, name: str, address: str, point_id: str = "") -> dict[str, Any]:
        """Navrhne uložení bodu dojezdu (např. Práce) s adresou, kterou uživatel napsal. S point_id upraví existující bod. Maximálně 2 body. Uživatel musí potvrdit."""
        return await self._propose(ctx, "set_commute_point", {"name": name, "address": address, "point_id": point_id})

    async def delete_commute_point(self, ctx: ToolContext, point_id: str) -> dict[str, Any]:
        """Navrhne smazání bodu dojezdu (point_id z get_settings). Uživatel musí potvrdit."""
        return await self._propose(ctx, "delete_commute_point", {"point_id": point_id})

    async def check_monitor_now(self, ctx: ToolContext, monitor_id: str = "") -> dict[str, Any]:
        """Navrhne okamžitou kontrolu jednoho hlídání (nebo všech, když monitor_id chybí). Uživatel musí potvrdit."""
        return await self._propose(ctx, "check_monitor_now", {"monitor_id": monitor_id})

    async def send_test_notification(self, ctx: ToolContext, channel: str) -> dict[str, Any]:
        """Navrhne odeslání testovací zprávy: channel je email, discord nebo whatsapp (jen PRO). Uživatel musí potvrdit."""
        return await self._propose(ctx, "send_test_notification", {"channel": channel})

    async def unlink_discord(self, ctx: ToolContext) -> dict[str, Any]:
        """Navrhne odpojení Discordu od účtu. Uživatel musí potvrdit."""
        return await self._propose(ctx, "unlink_discord", {})

    # ---- UI ------------------------------------------------------------------------------------------------------

    async def navigate(self, ctx: ToolContext, path: str) -> dict[str, Any]:
        """Přesune uživatele na stránku Realitify (relativní cesta, např. /monitory, /nastaveni, /prehled). Použij i pro věci, které sama nesmíš dělat (předplatné, platby, heslo, e-mail, smazání účtu, API klíče, profil a telefon): pošli uživatele na /nastaveni."""
        return {"ok": True, "_events": [{"type": "navigate", "path": safe_path(path)}]}

    # ---- internals -----------------------------------------------------------------------------------------------

    async def _propose(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
        result, events = await actions.propose(self._svc, ctx, name, args)
        return {"_raw": result, "_events": events}

    async def _listings_with_commute(
        self, ctx: ToolContext, data: dict[str, Any], wanted: int, point_id: str, max_minutes: int | None, mode: str
    ) -> dict[str, Any]:
        if not point_id:
            return self._listings(data)
        if mode not in {"transit", "walk", "drive"}:
            raise ToolError("commute_mode musí být transit, walk nebo drive.")
        settings = await asyncio.to_thread(ctx.ustore.app_settings)
        point = next((p for p in settings.get("commute_points") or [] if p.get("id") == point_id), None)
        if not point:
            raise ToolError("Neznámý bod dojezdu. Zavolej get_settings a použij id bodu.")
        raw = [i for i in (data.get("items") or []) if isinstance(i, dict)]
        gate = asyncio.Semaphore(COMMUTE_PARALLEL)

        async def minutes(item: dict[str, Any]) -> int | None:
            try:
                lat, lon = float(item["lat"]), float(item["lon"])
            except (KeyError, TypeError, ValueError):
                return None
            async with gate:
                times = await self._svc.route_times(lat, lon, float(point["lat"]), float(point["lon"]))
            seconds = (times or {}).get(mode)
            return None if seconds is None else round(seconds / 60)

        found = await asyncio.gather(*(minutes(i) for i in raw))
        kept = []
        for item, mins in zip(raw, found):
            if mins is None or (max_minutes is not None and mins > int(max_minutes)):
                continue
            kept.append({**item, "_commute_min": mins})
        out = self._listings({"items": kept[:wanted], "total": len(kept)})
        out["commute_to"] = str(point.get("name") or "Bod")[:40]
        out["commute_mode"] = mode
        return out

    def _url(self, item: dict[str, Any]) -> str:
        key = str(item.get("listing_key") or item.get("canonical_key") or "").strip()
        if key:
            return f"{self._base}/nabidka?listing_key={quote(key, safe='')}"
        source = str(item.get("url") or "").strip()
        if source:
            return f"{self._base}/nabidka?url={quote(source, safe='')}"
        return f"{self._base}/nabidka?id={item.get('id')}"

    def _listing(self, item: dict[str, Any]) -> dict[str, Any]:
        out = {
            "title": str(item.get("name") or item.get("title") or "")[:160],
            "price": item.get("price_label") or item.get("price_czk"),
            "disposition": item.get("disposition") or "",
            "locality": str(item.get("locality") or "")[:120],
            "area_m2": item.get("area_m2"),
            "url": self._url(item),
        }
        if item.get("_commute_min") is not None:
            out["commute_min"] = item["_commute_min"]
        return out

    def _card(self, item: dict[str, Any]) -> dict[str, Any]:
        """Data for the UI listing card. It goes to the browser only, never into the model context."""
        photos = item.get("photos")
        image = item.get("image_url") or (photos[0] if isinstance(photos, list) and photos else "")
        return {
            **self._listing(item),
            "price_czk": item.get("price_czk"),
            "image_url": str(image or ""),
            "source_url": str(item.get("url") or ""),
            "portal": str(item.get("portal") or ""),
        }

    def _listings(self, data: dict[str, Any]) -> dict[str, Any]:
        raw = [i for i in (data.get("items") or []) if isinstance(i, dict)][:MAX_LISTINGS]
        items = [self._listing(i) for i in raw]
        out: dict[str, Any] = {"count": len(items), "total": data.get("total", len(items)), "items": items}
        if raw:
            out["_cards"] = [self._card(i) for i in raw]
        if data.get("message"):
            out["message"] = data["message"]
            out["suggestions"] = data.get("suggestions") or []
        return out

    # ---- dispatch ------------------------------------------------------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        return _build_schemas(self)

    async def call(self, name: str, arguments_json: str, ctx: ToolContext) -> tuple[str, list[dict[str, Any]]]:
        """Run one tool call: (compact JSON for the model, SSE events for the UI: cards, confirm, navigate).

        Failures become an error result for the model. `ctx` always comes from the server, never from the arguments.
        """
        if name not in TOOL_NAMES:
            return _dump({"error": f"Neznámý nástroj: {name}"}), []
        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict) or "ctx" in args:
                raise ValueError("arguments must be an object")
            result = await getattr(self, name)(ctx=ctx, **args)
        except ToolError as exc:
            return _dump({"error": str(exc)}), []
        except (TypeError, ValueError) as exc:
            logger.warning("tool_bad_arguments tool=%s error=%s", name, exc)
            return _dump({"error": "Neplatné parametry nástroje."}), []
        except Exception as exc:
            logger.warning("tool_failed tool=%s error=%s", name, type(exc).__name__)
            return _dump({"error": "Nástroj teď nefunguje."}), []
        events: list[dict[str, Any]] = []
        if isinstance(result, dict):
            cards = result.pop("_cards", [])
            if cards:
                events.append({"type": "cards", "items": cards})
            events.extend(result.pop("_events", []))
            raw = result.pop("_raw", None)
            if raw is not None:
                return raw, events
        return _dump(result), events


READ_TOOLS = (
    "search_listings",
    "new_listings",
    "get_listing",
    "locality_stats",
    "price_check",
    "compare_localities",
    "list_my_monitors",
    "preview_monitor",
    "convert_monitor",
    "list_saved_listings",
    "get_my_plan",
    "get_settings",
    "get_discord_status",
    "get_whatsapp_status",
)
WRITE_TOOLS = tuple(actions.WRITE_SPECS)
UI_TOOLS = ("navigate",)
TOOL_NAMES = READ_TOOLS + WRITE_TOOLS + UI_TOOLS


def _json_type(annotation: Any) -> dict[str, Any]:
    origin = typing.get_origin(annotation)
    if origin in (Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _json_type(inner[0])
    if origin is list:
        return {"type": "array", "items": _json_type(typing.get_args(annotation)[0])}
    return {"type": {int: "integer", str: "string", float: "number", bool: "boolean"}[annotation]}


def _build_schemas(tools: ChatTools) -> list[dict[str, Any]]:
    out = []
    for name in TOOL_NAMES:
        method = getattr(tools, name)
        hints = typing.get_type_hints(method)
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, param in inspect.signature(method).parameters.items():
            if pname == "ctx":
                continue
            props[pname] = _json_type(hints[pname])
            if param.default is inspect.Parameter.empty:
                required.append(pname)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": inspect.getdoc(method) or "",
                    "parameters": {"type": "object", "properties": props, "required": required},
                },
            }
        )
    return out
