# Wave 1 — scrape diagnóza (5 hypotéz)

Datum: **2026-09-17**. Staging DB `data/monitor.sqlite`. Měření: `scripts/perf/scrape_bench.py --minutes 5` → `scripts/perf/scrape_baseline.json` (wall **416 s**, `SCRAPE_METRICS_DETAIL=1`, tři pipeline současně). Globální limiter: **16**.

## Baseline (KPI)

| KPI | Hodnota |
|---|---|
| pages/s celkem | **4,702** (1956 stránek) |
| listings/s celkem | **56,239** (23394) |
| unique shards v okně | 284 (z toho deep 230) |
| `daily_shards()` | **797** (sreality 382, idnes 345, bezrealitky 32, bazos 26, 6× extras po 2) |
| coverage cycle teoretický | 797 / (10 shardů / 2 s) = **159 s (0,04 h)** — nesmysl, tick netrvá 2 s |
| coverage cycle **měřený** | **1442 s ≈ 0,4 h** (230 unique deep / 416 s → extrapolace na 797) |
| deferred | 254 řádků, čekání p50=p95 **280 s** (skoro celé okno — nedorazily) |
| 403 / 429 | **0 / 0** u všech portálů |
| watchdog | n=600, p50 **1,7 ms**, p95 **2,2 ms**, max **88 789 ms**, last 40 542 ms, `over_2000ms=2` |

Deep pipeline v okně běžela **jen na Sreality** (1445 stránek). `next_deep_shards()` rotuje od začátku `daily_shards()` = samé sreality, dokud neprojede 382.

---

## 1. CPU kontence v worker loopu — **částečně potvrzeno (ocas, ne medián)**

Watchdog (`t0=monotonic(); await sleep(0.1); lag`) během plného provozu:

| | ms |
|---|---|
| p50 | 1,7 |
| p95 | 2,2 |
| max | **88 789** |
| vzorky > 50 / 500 / 2000 ms | 3 / 2 / 2 |

Typický loop je v pořádku (pod desítky ms). Dva vzorky na desítkách sekund znamenají, že **něco na event loopu / GIL drželo loop ~40–89 s**. Parse u Sreality už běží v `asyncio.to_thread` (p95 parse **6,9 ms**), takže to není čistý JSON parse na loopu — spíš GIL (thread pool parse + SQLite v `job_pool=2`) nebo dlouhý tick (`new_discovery` 54–177 s, `rolling_deep` až 148 s), během kterého `sleep(0.1)` watchdogu nedorazí.

**Wave 3 (ProcessPool / častější `sleep(0)`): ano, kvůli ocasu.** Cíl p95 už baseline plní; cíl max ≪ 2 s ne.

---

## 2. Granularita limiteru — **429 hypotéza vyvrácena, fronta potvrzena**

Žádný portál v 5min okně nedostal 403/429. Limiter se **nesnížil** (zůstalo 16). Throttle alerty v logu byly `fail` (timeout/parse), ne rate-limit.

| Portál | pages | pages/s | ok / fail / deferred | error_rate | fetch p95 ms |
|---|---|---|---|---|---|
| sreality | 1746 | **4,197** | 1603 / 0 / 143 | 0 | 726 |
| bazos | 28 | 0,067 | 20 / 0 / 8 | 0 | 110 |
| idnes | 27 | 0,065 | 7 / 0 / 20 | 0 | 49 440 |
| bezrealitky | 28 | 0,067 | 2 / **18** / 8 | **0,64** | 48 912 |
| mmreality | 28 | 0,067 | 11 / 0 / 17 | 0 | 49 507 |
| ulovdomov | 28 | 0,067 | 8 / 0 / 20 | 0 | **140 266** |
| realitycz | 28 | 0,067 | 14 / 0 / 14 | 0 | 88 901 |
| ostatní | 14–15 | ~0,03 | málo ok, 8 deferred | 0 | 1,5–91 s |

Sreality = **89 %** stránek. Ostatní portály končí v `deferred` kvůli sdílenému stropu 16 + sdílenému `fetch_shards` semaphore (`max(4, limiter.limit)`), ne kvůli 429.

Kód: `Hub._scrape_limiter` je jeden `AdaptiveLimiter` pro monitor (prio 0), discovery (1) i deep (2). Deep Sreality obsadí 16 slotů → monitor/discovery Bazosu/iDNES čeká ve stejné Condition frontě.

**Wave 2 (per-portál limiter): ano.** Ne proto, že by jeden 403 stáhl strop všem (to se v okně nestalo), ale proto, že Sreality drží globální frontu a ostatní nestíhají ani své 2–4 stránky/tick.

---

## 3. Sériový denní sync — **kód ano, v tomto okně bez čísel z `scrape_jobs`**

`scrape_jobs`: 16 řádků, všechny `kind=monitor_live`, `status=pending`, `started_at=NULL`. **Žádný `catalog_daily` job.** Meta `catalog_sync_*` prázdné — denní sync na tomto staging DB ještě nedoběhl (okno 5 min + sync startuje jen v `CATALOG_SYNC_HOUR_*` ± 2 h).

Co kód dělá:

- `maybe_run_catalog_sync` **spustí jen 1 portál** (`pick = bazos if bazos in due else sorted[0]`). I když `run_catalog_sync` umí `asyncio.gather` přes portály, scheduler to schválně serializuje.
- Hodiny default **1–10 UTC** (sreality…realitycz) se **překrývají v ±2h okně** (sreality 1–3, bezrealitky 2–4, …), ale catch-up je zakázaný (`hour > hour+2` skip).
- Uvnitř portálu: shardy **sériově** (`for shard: await run_shard`). Bazos: `Semaphore(1)` — stejná sériovost, důvod v komentáři: „parallel full-catalog upserts lock SQLite for minutes“.
- Catalog sync staví **vlastní** `ScrapeEngine()` — **nesdílí** `Hub._scrape_limiter` s live pipeline.
- `job_pool` = **2** thready; upsert drží `Hub._catalog_write` (asyncio.Lock, ne SQLite lock).

**Wave 4: odložit**, dokud nepoběží aspoň jeden kompletní denní cyklus a nebudou `started_at`/`finished_at`. Hypotéza „portál po portálu“ platí na úrovni scheduleru, ne `gather` uvnitř `run_catalog_sync`.

---

## 4. Zbytečné re-fetch (ETag / Last-Modified) — **potvrzeno u Sreality + Bazos**

Z reálných `scrape_metrics_log` odpovědí (ne jen HEAD probe):

| Portál | header_samples | ETag % | Last-Modified % |
|---|---|---|---|
| sreality | 1603 | **100** | 0 |
| bazos | 20 | 0 | **100** |
| annonce | 6 | 0 | 100 |
| idnes | 5 | 0 | 0 |
| bezrealitky (HTML probe) | — | ETag ano, `Cache-Control: public, max-age=600` | 0 |

Sreality JSON posílá ETag, ale HTML probe má `Cache-Control: private, no-cache, no-store, max-age=0, must-revalidate`. 304 tedy **není jistý** — API může ETag posílat a stejně vracet 200. Tohle Wave 5 musí ověřit reálným `If-None-Match` na `rolling_deep`.

iDNES: `no-store, no-cache`, bez ETag/LM → Wave 5 **ne**.

**Wave 5: ano pro Sreality (89 % traffic) a Bazos LM**, až po Wave 2 bench. Odhad úspory nelze spočítat bez 304 experimentu.

---

## 5. Detail fetch vs list crawl — **není bottleneck, limiter nesdílí**

| Portál | list pages | detail_fetches | poměr list:detail |
|---|---|---|---|
| sreality | 1746 | 3 | **582 : 1** |
| bazos | 28 | 5 | 5,6 : 1 |
| idnes | 27 | 2 | 13,5 : 1 |
| ostatní | — | 0 | — |

Kód: `_classify` → `_timed_detail` → `client.fetch_detail` **bez** `AdaptiveLimiter.acquire`. `limiter_at_call=None` v logu. Detail nesoutěží o strop list crawlu.

Upsert (pipeline `refresh`, portal prázdný): p50 **15,7 ms**, p95 **46,8 ms** — vedle Sreality fetch p95 726 ms zanedbatelné.

---

## Co stavět dál (pořadí)

1. **Wave 2 — per-portál `AdaptiveLimiter`** (největší očekávaný throughput). Sdílená fronta je změřená; 429 masking v tomto okně ne.
2. **Wave 3** jen pokud po Wave 2 watchdog max pořád > 2 s.
3. **Wave 5** až po ověření 304 na Sreality JSON (ETag je, `no-store` může 304 zabít).
4. **Wave 4** až budou čísla z `catalog_daily` jobů.

Gate: žádná další vlna, pokud Wave 2 zhorší error_rate u kteréhokoli portálu (hlavně bezrealitky 0,64).
