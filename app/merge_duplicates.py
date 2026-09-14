"""Offline merge of duplicate listings across portals."""

from __future__ import annotations

from app import config
from app.store import Store


def main() -> None:
    print(f"SQLite: {config.DB_PATH}", flush=True)
    stats = Store(config.DB_PATH).merge_duplicate_listings(force=True)
    print(stats, flush=True)


if __name__ == "__main__":
    main()
