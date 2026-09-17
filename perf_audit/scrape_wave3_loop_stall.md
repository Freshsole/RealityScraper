# Wave 3 — 50s event-loop stall: stack dump, ne inference

Datum: **2026-09-17**. Žádné `SCRAPE_CONCURRENCY_OVERRIDES`.
Dump: `PYTHONASYNCIODEBUG=1`, `loop.slow_callback_duration=0.1`, 1s `sys._current_frames()` (`scripts/perf/stall_dump.py`).

| Artefakt | Cesta |
|---|---|
| Freeze stack (první okno) | `perf_audit/loop_stall/freeze_parse_total.txt` |
| asyncio slow-callback log | `perf_audit/loop_stall/asyncio.log` |
| Bench před opravou (3 min) | `scripts/perf/scrape_wave3_stall.json` |
| Bench po opravě (3 min) | `scripts/perf/scrape_wave3_after.json` |

---

## 1. Frame v okamžiku zamrznutí

Dump thread **50 s nevzorkoval** (19:06:37 → 19:07:27) — GIL drželo i sidecaru. První vzorek po uvolnění:

```
-- asyncio_12 --
.../concurrent/futures/thread.py:73 run
app/html_listing.py:361 _parse
app/html_listing.py:291 _parse_total
app/html_listing.py:173 parse_total
```

Současně asyncio debug:

```
Executing <Task ... gated() ... portal='remax'> took 49.156 seconds
scrape watchdog lag_ms=49117.0 at=2026-09-17T19:07:27+00:00
```

Stejný pár `gated() portal=remax` ~49 s **čtyřikrát** v 3min okně (19:07:27, 19:08:29, 19:09:28, 19:10:23).

`parse_total` dělá `COUNT_RE.search` na **celém** HTML:

```python
COUNT_RE = r"([\d\s\u00a0]+)\s+(?:inzerát|nemovitost|nabídek|výsled)"
```

`[\d\s]+` overlapping s `\s+` na digit-heavy Remax JS (Cloudflare/bundle) = catastrophic backtracking. Regex drží GIL, `to_thread` nepomůže.

Izolovaný replay (živý Remax, 265 318 B, status 200):

| Volání | Před | Po opravě |
|---|---|---|
| `parse_total(full html)` | **36,048 s** (result=0) | **0,003 s** |
| `_parse_list` | 0,001 s | 0,001 s |
| celá `_parse` cesta | 36,131 s | 0,003 s |

`extract_listing_html` (180 kB) pořád trvala 11,3 s se starým regexem — nestačí oříznout na 180 kB, musí se omezit kvantifikátor.

---

## 2. Pět kandidátů z dokumentace

| Kandidát | Verdikt | Důkaz |
|---|---|---|
| **`threading.Lock` v async** (`Hub._catalog_write`) | **Vyvráceno** | `_catalog_write` je `asyncio.Lock`; SQLite retry `time.sleep` běží v `job_pool` přes `run_in_executor`. Dump freeze nebyl v `monitor.py` acquire. |
| **`Future.result()` sync z loopu** | **Vyvráceno** | Jediný `.result()` je v `location_amenities.warm_amenity_cache` uvnitř `ThreadPoolExecutor` (sync helper, bench ho nespouští). |
| **`SrealityClient._resolve_build_id()`** | **Vyvráceno** | Žádný lock, jen globál + `await bounded_request`. Slow-callback říká `portal='remax'`, ne sreality. Sreality fetch p95 před opravou 1,7 s. |
| **Synchronní `getaddrinfo` mimo httpx** | **Vedlejší, ne 50 s freeze** | V dump oknech po unfreeze občas `socket.py:getaddrinfo` na workeru. Hlavní 50s okno je `parse_total`. |
| **GC pauza** | **Vyvráceno** | Frame je `re.search`, ne `gc`. 36 s na 264 kB HTML není GC. `gc.disable()` se nespouštěl — nebylo potřeba. |
| **`sold_loop` / `backfill_missing_coords`** | **Vyvráceno pro tento freeze** | `scrape_bench` je nespouští (jen monitor + discovery + deep). |

Doplněk z dumpů **po** opravě `parse_total`: worker thread tráví sekundy v `places._nominatim_locality_sync` (`threading.Lock` + sync `httpx.Client` timeout 12 s, z `idnes._attach_coords` přes `asyncio.to_thread`). To **nedrží GIL** (síť uvolňuje), watchdog max po opravě je 62 ms. Není to 50s stall; eventual follow-up na thread-pool, ne na loop.

---

## 3. Izolace — co freeze vypnulo

Oprava v `app/html_listing.py` (stejná třída regexu i v `app/idnes.py`):

1. `COUNT_RE` má **omezený** kvantifikátor (`\d{1,3}(?:\s\d{3}){0,4}` / `\d{1,7}`), ne `[\d\s]+`.
2. `parse_total` čte max **32 kB**.
3. `_parse` volá `_parse_total(html)` (ořezaný extract), ne plný `text`.

Test: `tests.test_extra_portals.ExtraPortalTests.test_parse_total_is_bounded` — 250 kB digit HTML < 50 ms.

Po opravě v 3min běhu **žádný** `took 49s` na remax, **žádný** watchdog event ≥ 2 s, **žádný** `parse_total` ve freeze dumpu.

---

## 4. Před / po (3 min, stejný dump harness)

| KPI | Wave 2.5c (5 min) | W3 dump **před** | W3 **po** `parse_total` |
|---|---|---|---|
| watchdog max | 49 529 ms | **51 827 ms** | **62 ms** |
| watchdog p95 | 2,9 ms | 14 ms | 3,0 ms |
| watchdog ≥ 2 s | 6 eventů / 5 min | **4** | **0** |
| pages/s | 4,794 | 4,08 | **7,848** |
| listings/s | 47,385 | 43,687 | **102,3** |
| coverage cycle | 0,37 h | 0,89 h | **0,20 h** |

Fetch p95 (ms) — pět HTML portálů + Remax (viník):

| Portál | W3 před | W3 po |
|---|---|---|
| idnes | 53 096 | **5 034** |
| bezrealitky | 49 392 | **5 032** |
| mmreality | 49 633 | **5 007** |
| realitycz | 49 385 | **5 008** |
| ulovdomov | 52 481 | **807** |
| **remax** | **49 315** | **5 017** |
| sreality | 1 696 | **740** |

~5 s po opravě = `SCRAPE_HTTP_CONNECT_TIMEOUT` (5 s) co **konečně může vystřelit**, protože loop běží. To je rychlé selhání, ne 50s zombie. Ulovdomov 807 ms = POST 500 fail-fast.

---

## Gate

**PASS.** Watchdog max **62 ms** (cíl jednotky sekund). Fetch p95 u mrtvých HTML portálů **~5 s** (timeout), ne 49–140 s.

50s freeze nebyl limiter, retry ani httpx timeout. Byl to **`COUNT_RE.search` na Remax HTML v `parse_total`**, už v `to_thread`, ale s GIL. `SCRAPE_CONCURRENCY_OVERRIDES` pořád neladit kvůli tomuto — kapacita se uvolnila sama.
