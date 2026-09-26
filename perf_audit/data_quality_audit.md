# Catalog data-quality audit (2026-09-26)

Export baseline: **180 108** `catalog_listings` rows (`realitify-nabidka-last-seen_2026-09-26_14-31.csv`).

This note covers five defects that distort what the app shows as the “current market”, ordered by impact. Each section has **before** numbers, **root cause**, **fix shipped**, **regression tests**, and **one-shot cleanup** for existing data.

---

## 1. Stale `gone=0` (~60 % of catalog) — highest priority

### Before
| Metric | Value |
|--------|------:|
| `gone=0` AND `last_seen` older than 72 h | **108 418** (export) / **108 301** (live dry-run) |
| Max age observed | ~208 h |
| Correlates with | `catalog_sync.status=error` / `database is locked` |

### Root cause
`mark_catalog_stale_gone(seen_before, complete_portals)` in `app/store.py` only runs for portals whose **entire** daily shard set finished OK (`monitor.py` → `complete_portals`).

If iDNES / Bazoš sync ends `partial` / `error` (SQLite lock, 429, restart), those portals are **never** cleaned. Stale flats stay `gone=0` forever and pollute Nabídka / maps / counts.

### Fix shipped
1. Keep existing complete-portal marking.
2. Add **independent** `Store.sweep_catalog_stale_gone(max_age_hours=72)` — bulk `UPDATE … SET gone=1` for any live row with `last_seen < now-72h`, no portal gate, no per-row sold fanout (fanout locked the DB on 100k+ rows).
3. Hourly loop in `app/scrape_worker.py` (`_stale_sweep_loop`, env `CATALOG_STALE_HOURS`, default 72).

### Regression
`tests/test_data_quality.py::StaleGoneSweepTests::test_sweep_marks_ten_day_old_live_row_gone`

### Cleanup (local applied)
| Step | Result |
|------|--------|
| `sweep_catalog_stale_gone(72)` after non-apt mark | remaining stale live → **0** |
| Live after cleanup | **56 899** `gone=0` / **123 245** `gone=1` |

Production: after deploy + restart of **both** web and worker, run via admin (same process / `job_pool`), not `python -c`:

```http
GET  /api/admin/catalog-quality?max_age_hours=72
POST /api/admin/catalog-quality/run   {"dry_run": false, "max_age_hours": 72, "chunk_size": 80}
```

Orchestrator order (do **not** sweep before category mark — see Production checklist).

---

## 2. Category leak — iDNES / Bazoš scrape non-flats (~35 %)

### Before
| Metric | Value |
|--------|------:|
| Non-flat rows (houses/land/commercial/…) | **~63 608** (export analysis) |
| Share of iDNES / Bazoš | ~44 % / ~46 % of those portals |
| Live dry-run `idnes` URL not `/byt/` | 64 814 |
| Live dry-run `bazos` non-byt-ish | ~53 119 |

### Root cause
**Intentional** in `daily_shards()`:
- iDNES looped **all** `idnes_url.CATEGORIES` except projekty (`byty`, `domy`, `pozemky`, `komercni-nemovitosti`, `male-objekty-garaze`).
- Bazoš looped **all** `bazos_url.CATEGORIES` (13 categories × 2 offers = 26 shards).
- `recent_shards()` were already byt-only; deep/daily was the leak.
- Tests asserted houses/land were present (`test_daily_shards_cover_flats_and_houses`).

### Fix shipped
1. `daily_shards()`: iDNES **byty only** (regional split), Bazoš **byt only** (2 shards). Shard count **797 → 474**.
2. `is_apartment_listing(url, extras)` post-filter — reject non-flat path/estate.
3. `upsert_catalog_listing` / batch chunk **skip** new non-flats; stop refreshing `last_seen` on existing ones so sweep can retire them.
4. `Store.mark_non_apartment_catalog_gone()` — separate history (`gone=1`), do **not** delete (future multi-estate possible).

### Regression
- `ApartmentFilterTests` (shards + mixed upsert + helpers)
- Updated `tests/test_bazos_url.py`, `tests/test_idnes_url.py`

### Cleanup (local applied)
| Step | Result |
|------|--------|
| `mark_non_apartment_catalog_gone()` | **105 408** rows → `gone=1` (kept in DB) |

Decision: **keep history, mark gone** — not delete.

---

## 3. Duplicate canonical — Sreality URL slug drift

### Before
| Metric | Value |
|--------|------:|
| `(portal, id)` groups with multiple `listing_key`, both live | **342** export / **163** live groups |

Sreality rewrites path (`…/2+kk/praha-3/{id}` → `…/3+kk/praha-4/{id}`). `listing_key = host+path`, so each slug looked like a new listing. Geo/disposition fuzzy merge often missed them.

### Fix shipped
1. `_resolve_canonical`: prefer existing `listing_links` / `catalog_listings` row with same **`(portal, native_id)`** before `url:` canonical.
2. `same_listing`: same portal + same `id` → match.
3. Upsert UPDATE now refreshes `url` / `id` on the kept row.
4. `Store.merge_portal_native_duplicates()` one-shot: keep newest live row, `gone=1` + retarget canonical on older aliases.

### Regression
`PortalNativeIdMergeTests::test_slug_drift_shares_canonical`

### Cleanup (local applied)
`merge_portal_native_duplicates` → `{groups: 163, merged: 179, marked_gone: 179}`

---

## 4. `price_czk=0` as “cena na dotaz”

### Before
| Metric | Value |
|--------|------:|
| `price_czk = 0` | **~995** (export) / **991** dry-run |

Mostly Sreality (`0 Kč/měsíc`, `0 Kč/nemovitost`) when API returns `priceCzk: 0`.

### Fix shipped
1. `listing_from_raw` / estate parser: `price_czk <= 0` → `None`.
2. `format_price(0)` → `"Cena neuvedena"`.
3. `html_listing.parse_price`: “na dotaz” / amount ≤ 0 → `None`.
4. Upsert path normalizes `price_czk <= 0` before write.
5. `Store.repair_zero_prices()` bulk UPDATE → `NULL` + label fix.

### Regression
`PriceZeroTests` (parse, sreality raw, upsert)

### Cleanup (local applied)
`repair_zero_prices` → **991** rows. Residual `price0` may reappear until workers restart onto new code; re-run repair after deploy.

---

## 5. Area parsing garbage (`area_m2=990100`)

### Before
Example: Bazoš `id=223137180` “BYDLENÍ V PRAZE”, `area_m2=990100`.

### Root cause
Description: `tel.: 721990100 Mail:…`  
Regex `(\d{1,6})\s*m` (case-insensitive) matched **`990100` + ` M`** from **Mail** — not a floor area.  
(Legitimate land `85,9 ha` → ~859 000 m² is correct and must stay allowed for land.)

### Fix shipped
1. Stricter `AREA_RE` in `html_listing.py`: require `m²` / `m2` / `m` **not** followed by a letter.
2. `HA_RE` for hectares → ×10 000 (land).
3. `sanitize_area_m2`: flats cap **10 000 m²**, land higher; drop garbage.
4. Bazoš uses shared `html_listing.parse_area`.
5. One-shot NULL for `area_m2=990100` and absurd flat areas.

### Regression
`AreaParsingTests` (phone/Mail, normal m², ha, sanitize cap)

### Cleanup (local applied)
Cleared **8** absurd flat-area rows (including 990100). Broader `area_m2 > 2000` review: most large values are land/houses now marked `gone=1` via §2; remaining live flats above 10k should stay NULL via sanitize on next upsert.

---

## Summary — local DB after cleanup

| | Before (export) | After (local) |
|--|----------------:|--------------:|
| Total rows | 180 108 | ~180 144 |
| Live `gone=0` | ~180k mostly “live” | **56 899** |
| Stale live (>72 h) | **108 418** | **0** |
| `price_czk=0` | ~995 | repaired (re-run after deploy) |
| Daily shards | 797 (multi-category) | **474** (flats-only deep) |

## Production checklist

**Prerequisite:** deploy this branch (same as PR #56 / `perf/waves-0-5-plus-portals`) and restart **both** web and worker processes before any cleanup. `repair_zero_prices` residuals return if only web restarts while workers still write `price_czk=0`.

**Do not** run `python -c "Store(...).…"` against live `/data` while the worker writes — that is a second sqlite connection and recreates Wave 2 `database is locked`. Use the running app:

| Step | How |
|------|-----|
| Dry-run counts on a **sqlite3 backup API** copy first | Confirm order-of-magnitude: ~108k stale, ~105k non-apt, ~163 merge groups, ~991 price0 (export 2026-09-26). Prod may drift. |
| Live dry-run | `GET /api/admin/catalog-quality` → hub `_job_db` → `Store.catalog_quality_dry_run` |
| Apply | `POST /api/admin/catalog-quality/run` → `Store.run_catalog_quality_cleanup` |

**Orchestrator order (fixed — order matters):**

1. `mark_non_apartment_catalog_gone(chunk_size=80)` — houses/land scraped moments before deploy still have fresh `last_seen`; must go first
2. `merge_portal_native_duplicates()`
3. `repair_zero_prices()`
4. clear `area_m2=990100`
5. `sweep_catalog_stale_gone(72)` — after category mark, so fresh non-flats are already gone and stale flats are swept

If sweep ran **before** category mark, houses with `last_seen < 72h` would stay `gone=0` until a later mark. Both steps eventually converge if mark runs later, but the safe one-shot order is mark → … → sweep.

`mark_non_apartment_catalog_gone` commits every 50–100 keys (default 80) so WAL locks stay short and the scrape worker can interleave.

After apply:

6. Confirm `daily_shards` counts: idnes≈46, bazos=2, total≈474 (Wave 0 baseline was **797** — same codebase lineage; category fix is the delta)
7. Confirm hourly stale sweep log: `catalog stale sweep gone=…`

## Tests

```bash
python -m unittest tests.test_data_quality tests.test_bazos_url tests.test_idnes_url tests.test_listing_dedup tests.test_identity tests.test_regex_safety tests.test_scrape_engine tests.test_scrape_pipeline tests.test_catalog_shards tests.test_batch_upsert tests.test_sources tests.test_geocode_pool
```

All listed suites green together (data-quality + Waves 0–5) before merge/deploy.
