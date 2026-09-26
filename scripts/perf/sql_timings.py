#!/usr/bin/env python3
"""EXPLAIN QUERY PLAN + wall time for the audit's hot SQL."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT_DIR = Path(__file__).resolve().parent / "out"


def db_path() -> Path:
    from app import config

    return Path(config.DB_PATH)


def timed_sql(conn: sqlite3.Connection, sql: str, params: tuple = (), n: int = 5, label: str = "") -> dict:
    plan = [str(tuple(row)) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
    times: list[float] = []
    rows = []
    for _ in range(n):
        t0 = time.perf_counter()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    return {
        "label": label,
        "ms_min": round(times[0], 3),
        "ms_median": round(times[len(times) // 2], 3),
        "ms_max": round(times[-1], 3),
        "rows": len(rows),
        "plan": plan,
        "scan": any("SCAN" in item and "SEARCH" not in item.split("SCAN")[0] for item in plan)
        or any(" SCAN " in item or item.endswith("SCAN") or "SCAN listings" in item or "SCAN catalog" in item for item in plan),
    }


def run(path: Path | None = None) -> dict:
    target = path or db_path()
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.OperationalError:
        pass
    queries = [
        timed_sql(
            conn,
            "SELECT id, url FROM listings WHERE url = ? OR listing_key = ?",
            ("https://www.sreality.cz/detail/x", "sreality.cz/detail/x"),
            n=20,
            label="listings url OR listing_key (#3)",
        ),
        timed_sql(
            conn,
            """
            SELECT canonical_key FROM catalog_listings
            WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?
              AND IFNULL(canonical_key, '') != ''
            LIMIT 80
            """,
            (50.1465, 50.1475, 14.1023, 14.1033),
            n=15,
            label="nearby catalog_listings (#10)",
        ),
        timed_sql(
            conn,
            """
            SELECT locality, url FROM listings
            WHERE gone = 1 AND IFNULL(image_url, '') != '' AND last_seen >= datetime('now','-3 days')
            ORDER BY last_seen DESC
            """,
            label="gone_fast gone=1 (#9)",
        ),
        timed_sql(
            conn,
            """
            SELECT locality, url FROM listings
            WHERE IFNULL(gone, 0) = 1 AND IFNULL(image_url, '') != '' AND last_seen >= datetime('now','-3 days')
            ORDER BY last_seen DESC
            """,
            label="gone_fast IFNULL(gone)=1 (legacy)",
        ),
    ]
    conn.close()
    payload = {"db": str(target), "queries": queries}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "sql_timings.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    payload = run()
    for item in payload["queries"]:
        scan = " SCAN" if item["scan"] else " ok  "
        print(f"{item['ms_median']:8.3f} ms {scan}  {item['label']}")
        for plan in item["plan"]:
            print(f"           {plan}")


if __name__ == "__main__":
    main()
