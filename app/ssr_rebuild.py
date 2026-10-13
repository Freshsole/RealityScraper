"""One-shot SSR cache rebuild, run as a subprocess of the scrape worker.

A rebuild renders hundreds of market pages (minutes of CPU, a lot of memory). Doing it inside the
web process starved requests and pushed the container to its memory limit, so the worker runs it
in its own process (own GIL, own memory) at low priority.

    python -m app.ssr_rebuild [--if-stale]
"""

from __future__ import annotations

import os
import sys

from app import config, ssr_cache
from app import users as user_registry
from app.store import Store


def catalog_store() -> Store:
    """The store the public pages are built from: the catalog owner's, else the global one."""
    global_store = Store(config.DB_PATH)
    owner_id = None
    try:
        owner_id = user_registry.catalog_owner_id(global_store)
    except Exception:
        owner_id = None
    if not owner_id:
        return global_store
    return Store(user_registry.user_store_path(owner_id), seed_webhook_url="")


def main(argv: list[str]) -> int:
    try:
        os.nice(10)
    except OSError:
        pass
    if "--if-stale" in argv and ssr_cache.get_page("home") is not None and ssr_cache._persisted_cache_fresh():
        print("ssr_rebuild: persisted cache is fresh, nothing to do", flush=True)
        return 0
    result = ssr_cache.rebuild(catalog_store(), reason="worker")
    print(f"ssr_rebuild: done {result.get('ok')} {result.get('reason') or ''}", flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
