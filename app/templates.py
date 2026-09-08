from __future__ import annotations

import re
from typing import Any

from app.sreality import Listing, format_cz_date

VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

VARIABLES = [
    ("headline", "Nadpis (Nový byt / Změna ceny)"),
    ("name", "Název inzerátu"),
    ("price_label", "Cena s jednotkou"),
    ("price_czk", "Cena číslem"),
    ("disposition", "Dispozice"),
    ("area", "Rozloha s m²"),
    ("area_m2", "Rozloha číslem"),
    ("locality", "Lokalita"),
    ("url", "Odkaz na Sreality"),
    ("maps_url", "Odkaz na Google Maps"),
    ("image_url", "URL fotky"),
    ("created_on", "Datum vložení"),
    ("edited_on", "Datum úpravy"),
    ("views", "Počet zobrazení"),
    ("kind", "Typ zásahu"),
    ("changes", "Popis změny ceny"),
    ("monitor_name", "Název monitoru"),
]


def default_template_config() -> dict[str, Any]:
    return {
        "username": "Sreality Monitor",
        "content_lines": [
            {"text": "**{{headline}}**"},
            {"text": "{{name}}"},
            {"text": ""},
            {"text": "**Cena:** {{price_label}}"},
            {"text": "**Dispozice:** {{disposition}}"},
            {"text": "**Rozloha:** {{area}}"},
            {"text": "**Lokalita:** {{locality}}"},
            {"text": ""},
            {"text": "[Otevřít inzerát]({{url}})"},
            {"text": "[Otevřít v Google Maps]({{maps_url}})"},
        ],
        "embed": {
            "title": "{{name}}",
            "url": "{{url}}",
            "color": "#9fe870",
            "show_image": True,
            "footer": "Sreality monitor",
            "fields": [
                {"name": "Cena", "value": "{{price_label}}", "inline": True},
                {"name": "Dispozice", "value": "{{disposition}}", "inline": True},
                {"name": "Rozloha", "value": "{{area}}", "inline": True},
                {"name": "Lokalita", "value": "{{locality}}", "inline": False},
            ],
        },
    }


def listing_vars(listing: Listing, monitor_name: str = "") -> dict[str, str]:
    area = f"{listing.area_m2} m²" if listing.area_m2 else "neuvedena"
    headline = {
        "changed": "Změna inzerátu na Sreality",
        "refresh": "Obnovený inzerát na Sreality",
    }.get(listing.kind, "Nový byt na Sreality")
    changes = ""
    if listing.changes:
        changes = "\n".join(f"{label}: {before} → {after}" for label, before, after in listing.changes)
    return {
        "headline": headline,
        "name": listing.name or "",
        "price_label": listing.price_label or "",
        "price_czk": "" if listing.price_czk is None else str(listing.price_czk),
        "disposition": listing.disposition or "",
        "area": area,
        "area_m2": "" if listing.area_m2 is None else str(listing.area_m2),
        "locality": listing.locality or "",
        "url": listing.url or "",
        "maps_url": listing.maps_url() or "",
        "image_url": listing.image_url or "",
        "created_on": format_cz_date(listing.created_on) if listing.created_on else "",
        "edited_on": format_cz_date(listing.edited_on) if listing.edited_on else "",
        "views": "" if listing.views is None else str(listing.views),
        "kind": listing.kind or "",
        "changes": changes,
        "monitor_name": monitor_name,
    }


def sample_vars() -> dict[str, str]:
    return {
        "headline": "Nový byt na Sreality",
        "name": "Pronájem bytu 2+kk 56 m²",
        "price_label": "24 900 Kč/měsíc",
        "price_czk": "24900",
        "disposition": "2+kk",
        "area": "56 m²",
        "area_m2": "56",
        "locality": "Kurta Konráda, Praha – Libeň",
        "url": "https://www.sreality.cz/detail/pronajem/byt/2+kk/praha-liben-kurta-konrada/52351052",
        "maps_url": "https://www.google.com/maps/search/?api=1&query=50.1048,14.4881",
        "image_url": "https://d18-a.sdn.cz/d_18/c_img_qE_D/nPYADvcUkmCbUhRDfxHlez2D/d0ed.jpeg?fl=res,800,600,3|shr,,20|jpg,80",
        "created_on": "29. 7. 2026",
        "edited_on": "8. 9. 2026",
        "views": "9109",
        "kind": "new",
        "changes": "",
        "monitor_name": "Praha pronájmy",
    }


def render_text(template: str, variables: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        return variables.get(match.group(1), "")

    return VAR_RE.sub(repl, template or "")


def hex_color(value: str | None, fallback: int = 0x9FE870) -> int:
    raw = (value or "").strip().lstrip("#")
    try:
        return int(raw, 16)
    except ValueError:
        return fallback


def render_payload(config: dict[str, Any], variables: dict[str, str], prefix: str = "") -> dict[str, Any]:
    lines = [render_text(line.get("text") or "", variables) for line in config.get("content_lines") or []]
    content = "\n".join(lines).strip()
    if prefix:
        content = f"{prefix}{content}"
    embed_cfg = config.get("embed") or {}
    fields = []
    for field in embed_cfg.get("fields") or []:
        name = render_text(field.get("name") or "", variables).strip()
        value = render_text(field.get("value") or "", variables).strip()
        if not name or not value:
            continue
        fields.append({"name": name, "value": value, "inline": bool(field.get("inline"))})
    embed = {
        "title": render_text(embed_cfg.get("title") or "{{name}}", variables) or "Inzerát",
        "url": render_text(embed_cfg.get("url") or "{{url}}", variables),
        "color": hex_color(embed_cfg.get("color")),
        "fields": fields,
        "footer": {"text": render_text(embed_cfg.get("footer") or "Sreality monitor", variables) or "Sreality monitor"},
    }
    image = variables.get("image_url") or ""
    if embed_cfg.get("show_image", True) and image:
        embed["image"] = {"url": image}
    return {
        "username": render_text(config.get("username") or "Sreality Monitor", variables) or "Sreality Monitor",
        "content": content,
        "embeds": [embed],
        "show_image": bool(embed_cfg.get("show_image", True)),
    }
