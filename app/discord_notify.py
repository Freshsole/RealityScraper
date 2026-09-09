from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.sreality import Listing, download_image, format_cz_date, format_price
from app.templates import listing_vars, render_payload


def _parse_when(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if "T" in text or " " in text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            parsed = datetime.fromisoformat(text[:10])
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _cs_plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return f"{n} {one}"
    if 2 <= n <= 4:
        return f"{n} {few}"
    return f"{n} {many}"


def format_listed_duration(row: dict[str, Any], ended_at: datetime | None = None) -> tuple[str, str, str]:
    ended = ended_at or datetime.now(timezone.utc)
    start = _parse_when(row.get("created_on")) or _parse_when(row.get("first_seen"))
    started = format_cz_date(start.date().isoformat() if start else None)
    ended_label = format_cz_date(ended.date().isoformat())
    if not start:
        return "nezjištěno", started, ended_label
    seconds = max(0, int((ended - start).total_seconds()))
    if seconds < 60:
        return "méně než minutu", started, ended_label
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts: list[str] = []
    if days:
        parts.append(_cs_plural(days, "den", "dny", "dní"))
    if hours:
        parts.append(_cs_plural(hours, "hodina", "hodiny", "hodin"))
    if not days and minutes:
        parts.append(_cs_plural(minutes, "minuta", "minuty", "minut"))
    return " ".join(parts) or "méně než minutu", started, ended_label


async def send_sold(webhook_url: str, row: dict[str, Any], monitor_name: str = "") -> None:
    if not webhook_url:
        raise RuntimeError("SOLD_WEBHOOK_URL is empty")
    duration, started, ended = format_listed_duration(row)
    name = row.get("name") or row.get("locality") or "Inzerát"
    price = row.get("price_label") or format_price(row.get("price_czk"), "měsíc")
    locality = row.get("locality") or ""
    disposition = row.get("disposition") or ""
    area = f"{row['area_m2']} m²" if row.get("area_m2") else "neuvedena"
    url = row.get("url") or ""
    monitor = monitor_name or row.get("monitor_name") or ""
    payload = {
        "username": "Sales Hunter",
        "content": f"**Prodáno** · v nabídce **{duration}**",
        "embeds": [
            {
                "title": name,
                "url": url or None,
                "color": 0xC45C26,
                "fields": [
                    {"name": "Jak dlouho", "value": duration, "inline": True},
                    {"name": "Cena", "value": price or "neuvedena", "inline": True},
                    {"name": "Dispozice", "value": disposition or "—", "inline": True},
                    {"name": "Rozloha", "value": area, "inline": True},
                    {"name": "Lokalita", "value": locality or "—", "inline": False},
                    {"name": "Na inzerci", "value": f"{started} → {ended}", "inline": False},
                ],
                "footer": {"text": monitor or "Sreality monitor"},
            }
        ],
    }
    fields = payload["embeds"][0]["fields"]
    payload["embeds"][0]["fields"] = [field for field in fields if field["value"] and field["value"] != "—"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        image = None
        if row.get("image_url"):
            image = await download_image(client, row["image_url"])
        if image:
            data, content_type = image
            filename = "listing.jpg" if "jpeg" in content_type or "jpg" in content_type else "listing.webp"
            payload["embeds"][0]["image"] = {"url": f"attachment://{filename}"}
            response = await client.post(
                webhook_url,
                data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                files={"files[0]": (filename, data, content_type)},
            )
        else:
            response = await client.post(webhook_url, json=payload)
        response.raise_for_status()


async def send_listing(
    webhook_url: str,
    listing: Listing,
    template_config: dict[str, Any] | None = None,
    monitor_name: str = "",
    prefix: str = "",
) -> None:
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is empty")
    from app.templates import default_template_config

    config = template_config or default_template_config()
    variables = listing_vars(listing, monitor_name)
    payload = render_payload(config, variables, prefix=prefix)
    show_image = payload.pop("show_image", True)

    async with httpx.AsyncClient(timeout=30.0) as client:
        image = None
        if show_image and listing.image_url:
            image = await download_image(client, listing.image_url)
        if image:
            data, content_type = image
            filename = "listing.jpg" if "jpeg" in content_type or "jpg" in content_type else "listing.webp"
            if payload["embeds"]:
                payload["embeds"][0]["image"] = {"url": f"attachment://{filename}"}
            response = await client.post(
                webhook_url,
                data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                files={"files[0]": (filename, data, content_type)},
            )
        else:
            response = await client.post(webhook_url, json=payload)
        response.raise_for_status()


DISCORD_CONTENT_LIMIT = 1900


def _split_discord(content: str, limit: int = DISCORD_CONTENT_LIMIT) -> list[str]:
    text = (content or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < 80:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    return chunks


async def send_digest(webhook_url: str, items: list[dict[str, Any]], *, test: bool = False) -> None:
    title = "**Ranní souhrn (test)**" if test else "**Ranní souhrn**"
    if not items:
        await send_text(webhook_url, f"{title} · žádné nové zásahy")
        return
    shown = items[:15]
    lines = [f"{title} · {len(items)} zásahů"]
    for item in shown:
        kind = "sleva" if item.get("kind") == "changed" or item.get("discount_czk") else "nový"
        price = item.get("price_label") or ""
        name = item.get("name") or item.get("locality") or "Inzerát"
        extra = f" (−{item['discount_czk']} Kč)" if item.get("discount_czk") else ""
        url = item.get("url") or ""
        line = f"• **{kind}** {name} — {price}{extra}"
        if url:
            line += f"\n  <{url}>"
        lines.append(line)
    if len(items) > len(shown):
        lines.append(f"…a dalších {len(items) - len(shown)}")
    await send_text(webhook_url, "\n".join(lines))


async def send_text(webhook_url: str, content: str) -> None:
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is empty")
    chunks = _split_discord(content)
    if not chunks:
        raise RuntimeError("Prázdná Discord zpráva")
    async with httpx.AsyncClient(timeout=20.0) as client:
        for chunk in chunks:
            response = await client.post(
                webhook_url,
                json={"username": "Sreality Monitor", "content": chunk},
            )
            if response.is_error:
                detail = (response.text or response.reason_phrase or "")[:240]
                raise RuntimeError(f"Discord {response.status_code}: {detail}")
            if len(chunks) > 1:
                await asyncio.sleep(0.35)
