# Production verification — DB + scraping Waves 0–5

Datum: **2026-09-17**. Host: `https://www.realitify.cz` (`/data/monitor.sqlite`).  
Toto je ověření, ne oprava. **Žádný kód se nenasazoval.**

## Verdikt

**Série Wave 0–5 na Railway produkci není.** Poslední GitHub deployment do `discerning-gentleness / production` je **2026-09-15** SHA **`7b453b501f43384c682bb16df5faf3006a60fba9`** (`origin/main`, „Add continuous prioritized scraping and browser extension“).

Lokální práce žije mimo ten SHA:

| Ref | SHA | Co to je |
|---|---|---|
| Railway / `origin/main` | `7b453b501f43` | to, co běží |
| lokální `HEAD` (`cursor/game-locality-wise-ui-531b`) | `a229fc0b760b` | 10 portálů + hry, **bez** Wave 0–5 |
| working tree | uncommitted `M` / `??` | regex, geocode pool, indexes, CI, `perf_diag`, `portal_health` |

`scrape_bench` proti kopii produkční DB **neběžel** — bod 1 failnul dřív, než dávalo smysl měřit Wave čísla. Níže je jen observace live ticků ze starého kódu.

**Navrhovaný další krok (po potvrzení, nespouštět samo):** commit Wave 0–5 → merge na `main` (nebo deploy větve `a229fc0` + uncommitted waves) → Railway redeploy → znovu tento checklist. Než to potvrdíš, na produkci nic nesahej.

---

## 1. Checklist nasazení

Živý důkaz: `GET https://www.realitify.cz/api/status` (apex `https://realitify.cz/api/status` je **404**, jiný vhost). Verze v payloadu `1.2.0`.

| Položka | V gitu na prod SHA `7b453b5` | Živě na Railway | Stav |
|---|---|---|---|
| **Role split** `supervisord.conf` `[program:web]` `SCRAPE_ROLE=web` + `[program:worker]` `SCRAPE_ROLE=worker` | **ano** (blob `1c81beeaf4d6`, stejný jako working tree). `railpack.toml` `startCommand = supervisord -c supervisord.conf` | **`scrape_role: "all"`** a `scrape_worker.role: "all"` na každém vzorku. `last_error: "database is locked"`. Catalog sync i scrape píšou ze stejného procesu | **soubor v image-tree je split; runtime split neběží** |
| **Wave 1 indexy** `idx_listings_url`, `idx_catalog_lat_lon` + `ANALYZE` | **ne** — `store.py` na `7b453b5` je nevytváří (`0c18dc593b3f`). SQL `migrations/0001_perf_indexes.sql` na tom SHA **neexistuje** | SQLite volume `/data/monitor.sqlite` nešel otevřít (žádný Railway CLI, žádný sqlite backup). Indexy z nenasazeného kódu se samy nevytvoří | **chybí** (PRAGMA neověřeno přímo; kód to nedělá) |
| **Wave 2 upsert** `REFRESH_WRITES_FULL_ROW` (default 1), `skip_nearby` / `fast=True` v `upsert_seen` | `REFRESH_WRITES_FULL_ROW` **není v config**. `_resolve_canonical(..., fast=)` existuje, **`skip_nearby` ne**. `upsert_seen` volá `_resolve_canonical` **bez** `fast` → bbox `_find_canonical_nearby` na každém upsertu | env z Railway dashboardu nejde vypsat bez CLI. Default 1 by stejně neexistoval | **chybí** |
| **Wave 3+4 regex** bounded `COUNT_RE` / `PRICE_RE`, `html_listing.clean()` | iDNES: `COUNT_RE = ([\d\s]+)\s+inzerát`, `PRICE_RE = ([\d\s]+)\s*Kč` — unbounded. Bazoš: totéž u ceny. `app/html_listing.py`, `remax.py` na `7b453b5` **neexistují** (extra portály až `ada873d` / `a229fc0`) | image = ten SHA | **nenasazeno**; na prod běží katastrofický iDNES/Bazoš regex |
| **Wave 5B geocode pool** `ThreadPoolExecutor` + LRU | `geocode_locality` = `asyncio.to_thread(geocode_locality_sync, …)` (`places.py` `ac54107e7a30`) | `/api/perf/diag` → **404** `{"detail":"Not Found"}` — `perf_diag.py` na SHA není | **chybí** |
| **Concurrency 16, žádné overrides** | default `SCRAPE_CONCURRENCY=16`. `SCRAPE_CONCURRENCY_OVERRIDES` / `SCRAPE_GLOBAL_CONCURRENCY` na tom SHA **nejsou** (přišly až lokálně) | `scrape_worker.metrics.limit` 16 → 9 → 14 (AdaptiveLimiter, ne override mapa). `http_429` kumulativně **4934** | default 16 **sedí**; overrides na prod kódově neexistují (Wave 5A „neměnit“ je náhodou splněné tím, že vlna není venku) |
| **Wave 4C CI** `.github/workflows/ci.yml` → `test_regex_safety` + `make perf-quick` | na `origin/main` **žádný** `.github/`. `gh api .../actions/workflows` → `total_count: 0` | GitHub Actions na `Freshsole/RealityScraper` neběží | **chybí** (soubor je jen lokálně, untracked) |

### Role split — proč soubor ≠ runtime

`supervisord.conf` na nasazeném commitu je správně. HTTP proces ale hlásí `config.SCRAPE_ROLE == "all"` (default v `app/config.py`). Tick do sqlite píše totéž. To nejde dohromady s dětmi supervisord, pokud by `environment=SCRAPE_ROLE="web"|"worker"` opravdu platilo.

Nejpravděpodobnější: Railway **Start Command v UI přebíjí** `railpack.toml`, nebo služba startuje jediný uvicorn/`SCRAPE_ROLE=all`. `database is locked` + `checking: true` na web procesu tomu sedí (scrape i HTTP v jednom procesu).

Bez Railway CLI nešly vypsat service variables. Až při deployi zkontrolovat:

1. Start Command = `supervisord -c supervisord.conf`
2. žádné service-wide `SCRAPE_ROLE=all`
3. po startu `scrape_role` v `/api/status` = **`web`**, tick `role` = **`worker`**

### Commit hash souborů (prod SHA vs working tree)

| Soubor | `7b453b5` (prod) | working tree |
|---|---|---|
| `supervisord.conf` | `1c81beeaf4d6` | stejný |
| `railpack.toml` | `103bc3a74c12` | stejný |
| `app/idnes.py` | `eb4c4fb64597` unbounded regex | `c3658b963ff3` Wave 4 |
| `app/bazos.py` | `68e0f40c0b88` | `a3b1a102dcc9` |
| `app/places.py` | `ac54107e7a3091d76d8ce4dcbe0dead922b67446` `to_thread` | `301449643b58` geocode pool |
| `app/store.py` | `0c18dc593b3f7c0a63c32d2c72116ee38395269e` bez indexů / skip_nearby | `8afd7cbc8c64` |
| `app/config.py` | `1d37fe260b66` | `59445abf7877` |
| `app/html_listing.py` | **není v tree** | `c5fbfcc36916` |
| `app/perf_diag.py` | není | `b20c968c5a7e` |
| `app/portal_health.py` | není | `5eb70f958bdb` |
| `.github/workflows/ci.yml` | není | `3f5c1c35931f` untracked |
| `migrations/0001_perf_indexes.sql` | není | `8d02b6b8cc06` |

---

## 2. Produkční zátěž vs `scrape_baseline_v2.json`

**Nespouštěl jsem** `scripts/perf/scrape_bench.py --minutes 5` proti `/data` ani proti sqlite backup kopii. Důvod: vlny na prod nejsou; Railway CLI chybí, takže `sqlite3` backup API na volume nešel. Měřit starý kód 5 minut by nebylo porovnání s v2/geocode.

Místo toho 4× `GET /api/status` ~20:55 UTC, jen čtení `scrape_worker` ticků (rolling_deep). Žádný watchdog, žádný `scrape_metrics_log` (tabulka je jen v nenasazeném `store.py`).

### Before / after

| KPI | Staging v2 (`scripts/perf/scrape_baseline_v2.json`) | Po geocode izolaci (Wave 5B, lokál) | Produkce 2026-09-17 ~20:55 UTC |
|---|---:|---:|---|
| pages/s (5 min agregát) | **7.111** | **8.281** | **nejde spočítat stejně**. Tick `pages_ok / ms`: 124/17.2s ≈ **7.2**, 83/15.3s ≈ **5.4**, 64/2.9s ≈ 22 (useknutý tick, 1265 deferred) |
| listings/s | **78.467** | **98.449** | tick hlásí 2157 / 1255 listings včetně deferred; **není** listings/s z bench pipeline |
| watchdog max | 83.7 ms (0× >500) | 79.8 ms | **neznámé** — endpoint ani asyncio slow-callback na prod kódu není |
| idnes parse_ms p95 | 2318 ms | 5.71 ms | **neznámé** (detailní per-portál log nenasazen) |
| scrape_role | web/worker (lokál `make dev`) | web/worker | **`all`** |
| `database is locked` | — | — | **ano** (`last_error` i `catalog_sync.last_error`) |
| deferred / tick | nízké ve v2 | — | **755–1557** z 10 shardů |
| AdaptiveLimiter | 16 | 16 | 16 → **9** (429/tlak) |
| `/api/perf/diag` | lokálně job/ui/geocode pooly | geocode prefix `geocode_` | **404** |

Co z toho plyne: produkce **není** „zpátky na desítky sekund stránek“ v tom smyslu, že rolling_deep tick trvá jednotky až ~17 s a limiter drží ~16. **Není to ale v2/5B číslo.** Obří deferral, sqlite lock, role=`all` a nenasazený regex/geocode znamenají jiný runtime. Watchdog >500 ms **nešlo ověřit**; py-spy / asyncio slow-callback na Railway bez přístupu do kontejneru nespouštím.

`catalog_sync`: 785 jobs, 295 done, `status=error`, `listings=91017`, běží `idnes:byty:pronajem:jihocesky-kraj`, `last_error=database is locked`. To samo o sobě zkreslí jakýkoliv throughput.

Až poleze nasazená vlna: teprve pak 5min bench na **kopii** `/data/monitor.sqlite` (sqlite backup API, ne live volume) + `/api/perf/diag` pool fronty.

---

## 3. UlovDomov a Reality.cz — rozhodnutí, ne oprava

Na **aktuální produkci (`7b453b5`) se tyto dva portály vůbec nescrapují.** Klienti jsou až v `ada873d` / `a229fc0` („Cover all 10 Czech portals…“), pořád mimo `main`. Error_rate 1.0 / 0.26 z Wave 5A je ze **staging/lokálního** 10portálového bench, ne z Railway.

Ruční probe 2026-09-17:

| Portál | Endpoint | Výsledek |
|---|---|---|
| UlovDomov API (to, co klient POSTuje) | `POST https://ud.api.ulovdomov.cz/v1/offer/find` JSON `offerTypeId` + `sorting=latest` | **HTTP 500** `{"error":"udBe.internalServerError","success":false,"data":null"}` (~0.5 s) |
| UlovDomov HTML | `GET https://www.ulovdomov.cz/pronajem/byty` | **200**, ~95 kB, má `__NEXT_DATA__`, skoro žádné listing hrefy v prvním HTML |
| Reality.cz | `GET https://www.reality.cz/pronajem/byty/` a `/prodej/byty/` | **200**, ~54 kB, title „Katalog nemovitostí reality.cz“. Řetězec `údržba server` **není**. **0** hrefů `detail`/`nemovitost`/`inzerat` — prázdný katalog, ne klasická maintenance stránka |

### Doporučení

- **UlovDomov:** API je pořád mrtvé. Circuit breaker ať portál přeskakuje; ušetří to cykly. Strop cooldownu v nenasazeném `portal_health` je **60 min** (`SCRAPE_PORTAL_COOLDOWN_CAP_SEC`). Až poleze 10 portálů na prod, **zvednout cap** (např. 6–12 h) nebo permanent skip, dokud POST 500 trvá. HTML/`__NEXT_DATA__` fallback je **samostatný ticket** na klienta, ne performance série.
- **Reality.cz:** není downtime splash, ale search URL nenesou inzeráty, které parser hledá. Stejně: nechat breaker, ať nescrapuje prázdné stránky. Oprava selektorů/API = **samostatný ticket**, až bude na jejich straně zase reálný listing HTML/JSON.

---

## 4. Co udělat po potvrzení (není součástí tohoto kroku)

1. Commitnout uncommitted Wave 0–5 (`app/*` scrape/store/places, `tests/test_regex_safety.py`, `migrations/0001_*.sql`, `.github/workflows/ci.yml`, Makefile `perf-quick`) — teď to na `main` není.
2. Rozhodnout, jestli na prod jde i `a229fc0` (10 portálů + hry). Bez toho regex v `html_listing.py` / Remax na Railway stejně není.
3. Deploy na Railway. Ověřit Start Command = supervisord a `SCRAPE_ROLE` není `all`.
4. Po deployi:
   - `GET /api/status` → `scrape_role=web`, tick `role=worker`, `last_error` prázdný
   - `GET /api/perf/diag` → 200, pooly `job` / `ui` / `geocode` bez fronty
   - sqlite backup kopie → `PRAGMA index_list(listings)` / `catalog_listings` obsahuje `idx_listings_url`, `idx_catalog_lat_lon`; `meta.perf_indexes_v1`; ideálně `ANALYZE`
   - `scripts/perf/scrape_bench.py --minutes 5` **na kopii**, ne na live `/data`
5. Teprve pak porovnávat s v2 7.11 / 5B 8.28 pages/s. Odchylka sítě je OK; návrat k desítkám sekund nebo watchdog >500 ms = znovu Wave 3 dump, ne limiter.
