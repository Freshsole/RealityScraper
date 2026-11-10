"""AI listing summaries: generated once per listing, stored in the DB."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

PROMPT = (Path(__file__).parent / "prompts" / "listing_summary.md").read_text(encoding="utf-8").strip()
MAX_DESCRIPTION = 3000
SUMMARY_MAX_TOKENS = 200

_locks: dict[str, asyncio.Lock] = {}


def listing_text(listing: dict[str, Any]) -> str:
    """Only these fields go to the LLM: title, price, disposition, area, locality, description."""
    area = listing.get("area_m2")
    parts = [
        f"Titulek: {listing.get('name') or listing.get('title') or ''}",
        f"Cena: {listing.get('price_label') or listing.get('price_czk') or 'neuvedena'}",
        f"Dispozice: {listing.get('disposition') or 'neuvedena'}",
        f"Plocha: {str(area) + ' m²' if area else 'neuvedena'}",
        f"Lokalita: {listing.get('locality') or 'neuvedena'}",
        f"Popis: {str(listing.get('description') or '').strip()[:MAX_DESCRIPTION] or 'bez popisu'}",
    ]
    return "\n".join(parts)


async def summarize_listing(listing: dict[str, Any], llm: Any) -> str:
    return await llm.complete(
        [{"role": "system", "content": PROMPT}, {"role": "user", "content": listing_text(listing)}],
        SUMMARY_MAX_TOKENS,
    )


async def get_or_create_summary(store: Any, listing_id: str, listing: dict[str, Any], llm: Any) -> str:
    """Stored summary, or generate + store it. The per-listing lock keeps concurrent first views to one LLM call."""
    existing = await asyncio.to_thread(store.listing_summary_get, listing_id)
    if existing:
        return existing
    lock = _locks.setdefault(listing_id, asyncio.Lock())
    async with lock:
        existing = await asyncio.to_thread(store.listing_summary_get, listing_id)
        if existing:
            return existing
        summary = await summarize_listing(listing, llm)
        await asyncio.to_thread(store.listing_summary_save, listing_id, summary, llm.LLM_MODEL)
    _locks.pop(listing_id, None)
    return summary
