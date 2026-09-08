from __future__ import annotations

import json
from typing import Any

import httpx

from app.sreality import Listing, download_image
from app.templates import listing_vars, render_payload


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


async def send_text(webhook_url: str, content: str) -> None:
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is empty")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            webhook_url,
            json={"username": "Sreality Monitor", "content": content},
        )
        response.raise_for_status()
