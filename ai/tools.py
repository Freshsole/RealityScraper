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
from app.locality_normalize import normalize_disposition
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

_CATALOG_PORTALS = {"sreality", "idnes", "bazos", "ceskereality", "bezrealitky", "annonce", "mmreality", "ulovdomov", "remax", "realitycz"}
_AMENITY_FLAGS = {"balcony", "loggia", "terrace", "cellar", "lift", "parking", "garage", "garden", "barrier_free"}
_OWNERSHIP = {"Osobní", "Družstevní", "Obecní", "S.r.o.", "Podílové", "Jiné"}
_CONDITIONS = {"Novostavba", "Po rekonstrukci", "Velmi dobrý", "Dobrý", "Před rekonstrukcí", "V rekonstrukci", "Ve výstavbě", "Projekt", "Udržovaný", "Špatný", "K demolici"}
_BUILDINGS = {"cihlova", "panelova", "drevena", "kamenna", "skeletova", "montovana", "smisena", "ostatni"}
_EQUIPPED = {"vybaveny", "castecne", "nevybaveny"}
_TRI = {"s", "bez"}
_KM_PER_MIN = {"walk": 0.08, "bike": 0.2, "transit": 0.25, "drive": 0.4}  # straight-line km per minute, door to door
_CATALOG_SORTS = {"newest", "oldest", "price_asc", "price_desc", "area_desc", "area_asc", "discount_desc"}


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

    async def get_listing(self, ctx: ToolContext, listing_url: str, full_description: bool = False) -> dict[str, Any]:
        """Detail jednoho inzerátu včetně uživatelovy poznámky, stavu (uložený, skrytý), historie ceny a počtu fotek. Popis je zkrácený, celý (až 3000 znaků) vrátí full_description true. listing_url je přesně pole url z výsledku search_listings nebo new_listings."""
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
            out["description"] = description[: (3000 if full_description else MAX_DESCRIPTION)]
        photos = item.get("photos")
        if isinstance(photos, list):
            out["photo_count"] = len(photos)
        history = item.get("price_history")
        if isinstance(history, list) and history:
            out["price_history"] = [
                {k: point.get(k) for k in ("date", "seen_at", "price_czk", "price") if isinstance(point, dict) and point.get(k) is not None}
                for point in history[-8:]
            ]
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
        """Převede hledání jednoho hlídání na jiný portál (např. Sreality na Bezrealitky). Jen navrhne adresu, nic nevytváří; hlídání pak založíš nástrojem create_monitor_from_url."""
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
        self,
        ctx: ToolContext,
        monitor_id: str,
        name: str = "",
        enabled: bool | None = None,
        portal: str = "",
        locality: str = "",
        offer_type: str = "",
        disposition: list[str] | None = None,
        max_price: int | None = None,
        min_price: int | None = None,
        min_area: int | None = None,
        max_area: int | None = None,
    ) -> dict[str, Any]:
        """Navrhne úpravu hlídání (monitor_id z list_my_monitors): přejmenování, zapnutí/vypnutí, portál (all nebo jeden portál, např. sreality) nebo změnu filtrů. Při změně filtrů pošli VŠECHNY filtry hlídání znovu (locality a offer_type povinně, nahradí původní nastavení; původní hodnoty zjisti z list_my_monitors). Uživatel musí potvrdit."""
        return await self._propose(
            ctx,
            "update_monitor",
            {
                "monitor_id": monitor_id,
                "name": name,
                "enabled": enabled,
                "portal": portal,
                "locality": locality,
                "offer_type": offer_type,
                "disposition": disposition or [],
                "price_to": max_price,
                "price_from": min_price,
                "area_from": min_area,
                "area_to": max_area,
            },
        )

    async def delete_monitor(self, ctx: ToolContext, monitor_id: str) -> dict[str, Any]:
        """Navrhne smazání hlídání (monitor_id z list_my_monitors). Uživatel musí potvrdit."""
        return await self._propose(ctx, "delete_monitor", {"monitor_id": monitor_id})

    async def set_listing_status(self, ctx: ToolContext, listing_url: str, status: str) -> dict[str, Any]:
        """Navrhne uložení inzerátu mezi oblíbené (saved), skrytí (hidden) nebo vrácení (none). listing_url je pole source_url inzerátu. Uživatel musí potvrdit."""
        return await self._propose(ctx, "set_listing_status", {"listing_url": listing_url, "status": status})

    async def set_listings_status(self, ctx: ToolContext, listing_urls: list[str], status: str) -> dict[str, Any]:
        """Navrhne hromadně uložit do oblíbených (saved), skrýt (hidden) nebo vrátit (none) až 20 inzerátů najednou, jedno potvrzení. listing_urls jsou pole source_url. Uživatel musí potvrdit."""
        return await self._propose(ctx, "set_listings_status", {"listing_urls": listing_urls, "status": status})

    async def create_monitor_from_url(self, ctx: ToolContext, search_url: str, name: str = "", portal: str = "all") -> dict[str, Any]:
        """Navrhne nové hlídání z hotové adresy hledání na portálu (např. z convert_monitor nebo od uživatele). portal je all nebo jeden portál. Uživatel musí potvrdit."""
        return await self._propose(ctx, "create_monitor_from_url", {"search_url": search_url, "name": name, "portal": portal})

    async def update_watch_prefs(
        self,
        ctx: ToolContext,
        disposition: list[str] | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        min_area: int | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        """Navrhne změnu výchozích preferencí hledání (dispozice, cena, plocha), které se předvyplní v nabídce. clear true je smaže. Uživatel musí potvrdit."""
        return await self._propose(
            ctx,
            "update_watch_prefs",
            {"sizes": disposition or [], "price_from": min_price, "price_to": max_price, "area_from": min_area, "clear": clear},
        )

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

    # ---- READ: notes and side-by-side comparison ------------------------------------------------------------------

    async def get_overview(self, ctx: ToolContext) -> dict[str, Any]:
        """Přehled účtu jako na stránce Přehled: tarif, kolik hlídání je zapnutých a pro každé počet zásahů celkem a nových dnes."""
        monitors = await asyncio.to_thread(ctx.ustore.list_monitors)

        def stats() -> list[dict[str, Any]]:
            out = []
            for m in monitors:
                mid = str(m.get("id"))
                out.append(
                    {
                        "id": mid,
                        "name": str(m.get("name") or "")[:80],
                        "enabled": bool(m.get("enabled")),
                        "hits_total": ctx.ustore.count(mid),
                        "hits_today": ctx.ustore.new_today_count(mid),
                    }
                )
            return out

        plan = await self.get_my_plan(ctx)
        return {"plan": plan.get("label"), "watch_limit": plan.get("watch_limit"), "monitors": await asyncio.to_thread(stats)}

    async def list_my_notes(self, ctx: ToolContext, limit: int = 10) -> dict[str, Any]:
        """Poznámky, které si uživatel napsal k inzerátům (nejnovější první): název inzerátu, text poznámky a stav (uložený/skrytý)."""
        rows = await asyncio.to_thread(ctx.ustore.listing_notes, min(max(int(limit or 10), 1), MAX_LISTINGS))
        out = []
        for row in rows:
            item = await self._svc.catalog["item"](url=row["url"]) if row.get("url") else None
            out.append(
                {
                    "title": str((item or {}).get("name") or "")[:120] or row["url"][:100],
                    "locality": str((item or {}).get("locality") or "")[:80],
                    "note": str(row.get("note") or "")[:MAX_NOTE],
                    "status": row.get("status") or "",
                    "source_url": row["url"],
                }
            )
        return {"count": len(out), "items": out}

    async def compare_listings(self, ctx: ToolContext, listing_urls: list[str]) -> dict[str, Any]:
        """Porovná 2 až 3 inzeráty vedle sebe (cena, dispozice, plocha, cena za m², lokalita, poznámka uživatele). listing_urls jsou pole url nebo source_url z výsledků nebo z porovnání v UI."""
        urls = [str(u).strip() for u in (listing_urls or []) if str(u).strip()][:3]
        if len(urls) < 2:
            raise ToolError("K porovnání jsou potřeba aspoň 2 inzeráty.")
        rows: list[dict[str, Any]] = []
        cards: list[dict[str, Any]] = []
        for url in urls:
            params = parse_qs(urlparse(url).query)
            lookup = (
                {"listing_key": params["listing_key"][0]}
                if params.get("listing_key")
                else {"id": params["id"][0]}
                if params.get("id")
                else {"url": params.get("url", [url])[0]}
            )
            try:
                item = await self._svc.catalog["item"](**lookup)
            except Exception:
                rows.append({"url": url, "error": "Inzerát se nepodařilo načíst."})
                continue
            row = self._listing(item)
            try:
                if item.get("price_czk") and item.get("area_m2"):
                    row["price_per_m2"] = round(int(item["price_czk"]) / float(item["area_m2"]))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
            for key in ("floor", "condition", "equipped", "building", "ownership", "balcony", "terrace", "parking", "lift"):
                if item.get(key) not in (None, "", False):
                    row[key] = item[key]
            source = str(item.get("url") or "")
            if source:
                mine = await asyncio.to_thread(ctx.ustore.get_listing_user, source)
                if mine.get("note"):
                    row["my_note"] = str(mine["note"])[:MAX_NOTE]
            rows.append(row)
            cards.append(self._card(item))
        return {"items": rows, "_cards": cards}

    # ---- UI ------------------------------------------------------------------------------------------------------

    async def navigate(self, ctx: ToolContext, path: str) -> dict[str, Any]:
        """Přesune uživatele na stránku Realitify (relativní cesta, např. /monitory, /nastaveni, /prehled). Použij i pro věci, které sama nesmíš dělat (předplatné, platby, heslo, e-mail, smazání účtu, API klíče, profil a telefon): pošli uživatele na /nastaveni."""
        return {"ok": True, "_events": [{"type": "navigate", "path": safe_path(path)}]}

    async def set_catalog_filters(
        self,
        ctx: ToolContext,
        locality: str = "",
        offer_type: str = "",
        estate: str = "",
        disposition: list[str] | None = None,
        max_price: int | None = None,
        min_price: int | None = None,
        min_area: int | None = None,
        max_area: int | None = None,
        portal: str = "",
        amenities: list[str] | None = None,
        sort: str = "",
        status: str = "",
        discounted: bool | None = None,
        ownership: list[str] | None = None,
        condition: list[str] | None = None,
        building: list[str] | None = None,
        equipped: list[str] | None = None,
        roommate: str = "",
        pets: str = "",
        short_term: str = "",
        monitor_id: str = "",
        hits: str = "",
        around_address: str = "",
        radius_km: float | None = None,
        max_commute_minutes: int | None = None,
        commute_mode: str = "transit",
        keep_current: bool = False,
    ) -> dict[str, Any]:
        """Nastaví filtry v nabídce (/nabidka) přímo v UI uživatele. locality: město nebo čtvrť (hledá se v poli lokality), offer_type pronajem/prodej/drazba, estate byt/dum/pozemek, disposition např. ["2+kk"], ceny v Kč, plocha v m², portal (sreality, idnes, bazos, ceskereality, bezrealitky, annonce, mmreality, ulovdomov, remax, realitycz), amenities (balcony, loggia, terrace, cellar, lift, parking, garage, garden, barrier_free), sort (newest, oldest, price_asc, price_desc, area_desc, area_asc, discount_desc), status (prázdné = aktivní, saved = uložené, hidden = skryté, all = vše), discounted jen zlevněné. Další filtry: ownership (Osobní, Družstevní, Obecní, S.r.o., Podílové, Jiné), condition (Novostavba, Po rekonstrukci, Velmi dobrý, Dobrý, Před rekonstrukcí, V rekonstrukci, Ve výstavbě, Projekt, Udržovaný, Špatný, K demolici), building (cihlova, panelova, drevena, kamenna, skeletova, montovana, smisena, ostatni), equipped (vybaveny, castecne, nevybaveny), roommate / pets / short_term (s = jen kde to víme, bez = skrýt), monitor_id (jen nabídky jednoho hlídání z list_my_monitors), hits (today = zásahy z dneška, notified = všechny zásahy z hlídání), around_address + radius_km (nabídky v okruhu kolem adresy). Dojezd: místo radius_km můžeš dát max_commute_minutes s commute_mode (transit = MHD, walk, bike, drive); server z toho udělá přibližný okruh kolem adresy (MHD 20 min je asi 5 km vzdušnou čarou), takže jde o odhad, ne přesný dojezd. Pro „centrum“ použij around_address například „Václavské náměstí, Praha“. Ostatní dosavadní filtry se vymažou, pokud keep_current není true. Nic se nemění v datech, jen se zobrazí výsledky."""
        params: dict[str, str] = {}
        offer = offer_type.strip().casefold()
        if offer:
            if offer not in {"pronajem", "prodej", "drazba"}:
                raise ToolError("offer_type musí být pronajem, prodej nebo drazba.")
            params["offer"] = offer
        if estate.strip():
            if estate.strip().casefold() not in {"byt", "dum", "pozemek"}:
                raise ToolError("estate musí být byt, dum nebo pozemek.")
            params["estate"] = estate.strip().casefold()
        if locality.strip():
            params["q"] = locality.strip()[:80]
        sizes = [normalize_disposition(str(d)) for d in (disposition or []) if str(d).strip()]
        if any(sizes):
            params["disposition"] = ",".join(x for x in sizes if x)
        for key, value in (("price_to", max_price), ("price_from", min_price), ("area_from", min_area), ("area_to", max_area)):
            if value not in (None, "", 0):
                params[key] = str(int(value))
        if portal.strip():
            if portal.strip().casefold() not in _CATALOG_PORTALS:
                raise ToolError("Neznámý portál.")
            params["portal"] = portal.strip().casefold()
        flags = [f.strip() for f in (amenities or []) if f.strip()]
        if flags:
            if any(f not in _AMENITY_FLAGS for f in flags):
                raise ToolError("Neznámé příslušenství. Povolené: " + ", ".join(sorted(_AMENITY_FLAGS)))
            params["amenities"] = ",".join(flags)
        if sort.strip():
            if sort.strip() not in _CATALOG_SORTS:
                raise ToolError("Neznámé řazení.")
            params["sort"] = sort.strip()
        if status.strip():
            if status.strip() not in {"saved", "hidden", "all"}:
                raise ToolError("status musí být saved, hidden nebo all (prázdné = aktivní).")
            params["status"] = status.strip()
        if discounted:
            params["discounted"] = "1"
        for key, values, allowed in (
            ("ownership", ownership, _OWNERSHIP),
            ("condition", condition, _CONDITIONS),
            ("building", building, _BUILDINGS),
            ("equipped", equipped, _EQUIPPED),
        ):
            picked = [v.strip() for v in (values or []) if v.strip()]
            if picked:
                if any(v not in allowed for v in picked):
                    raise ToolError(f"Neplatná hodnota pro {key}. Povolené: " + ", ".join(sorted(allowed)))
                params[key] = ",".join(picked)
        for key, value in (("roommate", roommate), ("pets", pets), ("short_term", short_term)):
            if value.strip():
                if value.strip() not in _TRI:
                    raise ToolError(f"{key} musí být s nebo bez.")
                params[key] = value.strip()
        if monitor_id.strip():
            monitor = await asyncio.to_thread(ctx.ustore.get_monitor, monitor_id.strip())
            if not monitor:
                raise ToolError("Hlídání s tímto id neexistuje. Zavolej list_my_monitors.")
            params["monitor_id"] = str(monitor["id"])
        if hits.strip():
            if hits.strip() not in {"today", "notified"}:
                raise ToolError("hits musí být today nebo notified.")
            params["hits"] = hits.strip()
        if around_address.strip():
            found = (await self._svc.geocode(q=around_address.strip())).get("items") or []
            if not found:
                raise ToolError(f"Adresu „{around_address.strip()}“ se nepodařilo najít.")
            if radius_km is None and max_commute_minutes:
                if commute_mode not in _KM_PER_MIN:
                    raise ToolError("commute_mode musí být transit, walk, bike nebo drive.")
                radius_km = round(int(max_commute_minutes) * _KM_PER_MIN[commute_mode], 1)
            km = float(radius_km or 2)
            if not 0.2 <= km <= 50:
                raise ToolError("radius_km musí být mezi 0,2 a 50.")
            params["lat"], params["lon"], params["radius_m"] = str(found[0]["lat"]), str(found[0]["lon"]), str(int(km * 1000))
        return {
            "ok": True,
            "applied": params,
            "note": "Okruh je jen přibližný odhad dojezdu (vzdušná čára), uživateli to řekni." if "radius_m" in params else None,
            "_events": [{"type": "ui", "op": "catalog_filters", "params": params, "keep_current": bool(keep_current)}],
        }

    async def set_catalog_view(self, ctx: ToolContext, mode: str) -> dict[str, Any]:
        """Přepne zobrazení nabídky: list (seznam s mapou), grid (karty) nebo map (jen mapa)."""
        mode = mode.strip().casefold()
        if mode not in {"list", "grid", "map"}:
            raise ToolError("mode musí být list, grid nebo map.")
        return {"ok": True, "_events": [{"type": "ui", "op": "view", "mode": mode}]}

    async def clear_catalog_filters(self, ctx: ToolContext) -> dict[str, Any]:
        """Vymaže všechny filtry v nabídce (/nabidka) a vrátí výchozí zobrazení."""
        return {"ok": True, "_events": [{"type": "ui", "op": "catalog_filters", "params": {}, "keep_current": False}]}

    async def catalog_compare(self, ctx: ToolContext, action: str, listing_urls: list[str] | None = None) -> dict[str, Any]:
        """Ovládá porovnávání inzerátů v UI (max 3): action add (přidat listing_urls), remove (odebrat), clear (vyprázdnit) nebo open (otevřít porovnání). listing_urls jsou pole source_url z karet nebo get_listing."""
        action = action.strip().casefold()
        if action not in {"add", "remove", "clear", "open"}:
            raise ToolError("action musí být add, remove, clear nebo open.")
        urls = [str(u).strip() for u in (listing_urls or []) if str(u).strip()][:3]
        if action in {"add", "remove"} and not urls:
            raise ToolError("Chybí listing_urls.")
        return {"ok": True, "_events": [{"type": "ui", "op": "compare", "action": action, "urls": urls}]}

    async def open_listing(self, ctx: ToolContext, listing_url: str) -> dict[str, Any]:
        """Otevře detail inzerátu v UI uživatele. listing_url je pole url z výsledků nebo get_listing."""
        params = parse_qs(urlparse(listing_url or "").query)
        target = {
            "listing_key": (params.get("listing_key") or [""])[0],
            "id": (params.get("id") or [""])[0],
            "url": (params.get("url") or [listing_url if str(listing_url).startswith("http") else ""])[0],
        }
        if not any(target.values()):
            raise ToolError("Chybí listing_url.")
        return {"ok": True, "_events": [{"type": "ui", "op": "open_listing", **target}]}

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
    "list_my_notes",
    "compare_listings",
    "get_overview",
)
WRITE_TOOLS = tuple(actions.WRITE_SPECS)
UI_TOOLS = ("navigate", "set_catalog_filters", "clear_catalog_filters", "set_catalog_view", "catalog_compare", "open_listing")
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
