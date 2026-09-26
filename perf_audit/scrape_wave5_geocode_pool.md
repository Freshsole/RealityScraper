# Wave 5B — Nominatim mimo parse thread pool

Datum: **2026-09-17**. Gate: `scripts/perf/scrape_baseline_v2.json`. Stejný concurrency default jako v2 (portal 16, global 24). Bench: `scripts/perf/scrape_wave5_geocode.json`.

---

## Co bylo špatně

`geocode_locality` šlo přes `asyncio.to_thread` → **default executor**, stejný jako HTML/JSON parse. `_nominatim_locality_sync` drží vlákno `time.sleep(1.1)` + sync HTTP (až 12 s). Parse čeká na volné vlákno → iDNES `parse_ms` p50 **0.0** / p95 **2318 ms**. Dump ve Wave 4: `asyncio_N:places.py:791 _nominatim_locality_sync`. Watchdog přitom 83 ms — loop žil, pool ne.

`refine_listing_location` navíc volalo Nominatim **uvnitř** `to_thread` parse (Sreality `apply_detail`, iDNES detail).

---

## Změna

- `ThreadPoolExecutor(max_workers=4, thread_name_prefix="geocode")` + `loop.run_in_executor`.
- LRU cache 4096 (forward locality i reverse `lat,lon` na 5 desetinných). Cache hit **před** executorem.
- Parse path: `refine_listing_location(..., network=False)` jen cache. Síť přes `refine_listing_location_async` / `geocode_locality` na geocode poolech.
- Testy: `tests/test_geocode_pool.py`.

Dump po opravě: `geocode_3:places.py _nominatim_locality_sync` — ne `asyncio_N`.

---

## Before / after (5 min)

| KPI | v2 | po izolaci | Δ |
|---|---:|---:|---|
| pages/s | 7.111 | **8.281** | **+16,5 %** |
| listings/s | 78.467 | **98.449** | **+25,5 %** |
| watchdog max | 83.7 ms | **79.8 ms** | 0× over 500 ms |
| **idnes parse_ms p50 / p95** | 0.0 / **2318** | 0.0 / **5.71** | **p95 −99,8 %** |
| idnes pages/s | 0.275 | 0.311 | +13 % |
| idnes listings/s | 0.983 | 1.833 | +87 % |
| sreality parse_ms p95 | 3.17 | 2.81 | — |
| sreality pages/s | 5.534 | 6.724 | +22 % |
| bazos parse_ms p95 | 1.84 | 2.54 | pořád jednotky ms |

iDNES p50 zůstává 0 kvůli deferred řádkům (`parse_ms=None` → 0). p95 **5.71 ms** je skutečný parse (karty + regex), ne fronta na Nominatim. Cíl „p95 blízko reálnému parse, ne sekundám“ splněn.

Vedlejší zisk pages/s je uvolněný default pool (Sreality fetch p95 741 → 570 ms v tomhle okně), ne změna stropu.
