# Wave 2 after — upsert / discovery write path

Datum: **2026-09-17**. Stejné prostředí jako `performance_report.md`. Nástroj: `make perf-quick` → `scripts/perf/` (hotpaths tracer, sql_timings EXPLAIN, HTTP bench). Baseline: `scripts/perf/baseline.json`.

---

## TOP bottlenecků z auditu (before → after)

| # | Bottleneck | Before | After | File |
|---|---|---|---|---|
| 2 | N+1 upsert (15 SQL / listing) | **7,7 ms**, 15 SQL | **0,317 ms**, **3,13 SQL** | `store.py` `_upsert_catalog_chunk` |
| 3 | `UPDATE listings WHERE url=? OR listing_key=?` SCAN | **5,84 ms SCAN** | **0,008 ms SEARCH** `idx_listings_url` + `idx_listings_listing_key` | migrace `0001_perf_indexes.sql` |
| 4 | `INSERT events` na každý refresh | 113 636 řádků, 100 % `refresh` | refresh bez změny ceny/dostupnosti **neinsertuje** event | `upsert_seen` `skip_event` |
| 10 | nearby `catalog_listings` SCAN `(lat,lon)` | **4,48 ms SCAN** | **0,014 ms SEARCH** `idx_catalog_lat_lon`; u refresh se nearby nespouští | `_resolve_canonical(skip_nearby=True)` |

---

## 2. CPU / write path (cProfile + tracer)

`scripts/perf/out/hotpaths.json` — 30 existujících listingů, `kind=refresh`, rollback:

| | Before | After |
|---|---|---|
| ms / listing | 7,7 | **0,317 (−96 %)** |
| SQL / listing | 15 | **3,13 (−79 %)** |
| 30 listingů wall | 223 ms (cProfile) | **9,51 ms** |
| SQL verbs | 15× SELECT/UPDATE/INSERT per row | 1× `SELECT IN` + 30× UPDATE catalog + 30× INSERT links + 30× INSERT listings |

Rozpad 94 statements / 30 listingů ≈ 3 SQL/listing (batch SELECT + executemany UPDATE + executemany INSERT ÷ N). Nearby geo SCAN na refresh **0×**.

`REFRESH_WRITES_FULL_ROW` default **1** (catalog UI čte `listings`). `0` = jen `catalog_listings` + `listing_links`.

---

## 3. SQLite EXPLAIN (sql_timings)

`scripts/perf/out/sql_timings.json`:

| Query (audit #) | Before | After | Plan |
|---|---|---|---|
| listings `url OR listing_key` (#3) | 5,837 ms SCAN | **0,008 ms** | `SEARCH idx_listings_url` + `SEARCH idx_listings_listing_key` |
| nearby catalog (#10) | 4,475 ms SCAN | **0,014 ms** | `SEARCH idx_catalog_lat_lon (lat>? AND lat<?)` |
| `gone = 1` (#9) | 5,405 ms IFNULL SCAN | **0,003 ms** | `SEARCH idx_listings_gone_seen` |

SCAN na #3 a #10 zmizel.

---

## Invarianty (testy)

`tests/test_refresh_canonical.py`:

- refresh se stejnou geo/URL **nemění** `canonical_key`
- čistý refresh **nepíše** `events`
- změna ceny **zapíše** jeden `refresh` event

`tests/test_batch_upsert.py`, `test_listing_dedup.py`, `test_catalog_map_zoom.py` — pass.

---

## Discovery tick (cíl 188 s → <30 s)

Izolovaný zápis 2417 listingů: 18,6 s → ~0,8 s čistého SQL (0,317 ms × 2417). Živý tick pořád zahrnuje HTTP + parse; to řeší vlna 3. Staging tick znovu změřit při běžícím scrapu.

Feature flag rollback: `REFRESH_WRITES_FULL_ROW=1` (současný default) vs `0`.
