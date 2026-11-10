"""Batch-generate AI summaries for the newest catalog listings that have none.

Usage:  AI_ENABLED=true LLM_API_KEY=... LLM_MODEL=... python scripts/generate_summaries.py --limit 50

Sequential, one LLM call at a time, prints progress. A failed call is printed and the script stops (no retries).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai import llm  # noqa: E402  (needs LLM_API_KEY / LLM_MODEL)
from ai import summary  # noqa: E402
from app import config  # noqa: E402
from app import users as user_registry  # noqa: E402
from app.store import CATALOG_MONITOR_ID, Store  # noqa: E402


def open_catalog_store() -> Store:
    glob = Store(config.DB_PATH)
    owner = user_registry.catalog_owner_id(glob)
    if not owner:
        return glob
    return Store(user_registry.user_store_path(owner), seed_webhook_url="")


async def run(limit: int) -> int:
    store = open_catalog_store()
    ids = store.listing_ids_without_summary(limit)
    print(f"K dogenerování: {len(ids)} inzerátů")
    for n, listing_id in enumerate(ids, 1):
        item = store.catalog_item(CATALOG_MONITOR_ID, listing_id)
        if not item:
            print(f"[{n}/{len(ids)}] {listing_id}: inzerát nenalezen, přeskakuji")
            continue
        try:
            await summary.get_or_create_summary(store, listing_id, item, llm)
        except llm.LLMError as exc:
            print(f"[{n}/{len(ids)}] {listing_id}: CHYBA {exc}")
            return 1
        print(f"[{n}/{len(ids)}] {listing_id}: ok")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, required=True)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.limit)))


if __name__ == "__main__":
    main()
