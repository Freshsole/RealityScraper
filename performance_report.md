# Performance audit — Realitify (FastAPI + SQLite)

Datum měření: **2026-09-17**. Prostředí: lokální `make dev`, Python **3.14.3**, FastAPI **0.116.1**, uvicorn **0.35.0**, httpx **0.28.1**. Entry point: `python -m app` → `app/main.py:app`. Role: `SCRAPE_ROLE=all` (web + scrape v jednom procesu).

Databáze `data/monitor.sqlite`: **215 MB** (kopie pro izolovaná měření: 228 MB), WAL. Řádky: `listings` 25 475, `catalog_listings` 25 371, `listing_links` 27 493, `events` 113 636 (všechny `kind=refresh`), `listing_photos` **401 269** (15,8 fotek / listing).

Profilery nainstalované do `.venv` (ne globálně): cProfile/pstats, pyinstrument 5.1.3, line_profiler 5.0.2, memory_profiler 0.61.0, scalene 2.3.0, snakeviz 2.2.2, py-spy 0.4.2. **py-spy na macOS vyžaduje sudo** (SIP) — namísto `flame.svg` je živý CPU profil z `/usr/bin/sample` (`perf_audit/sample.txt`).

Izolovaná DB měření běžela proti `perf_audit/monitor_copy.sqlite` (sqlite backup API), aby se neměnila živá data. HTTP bench a `sample` běžely proti už spuštěnému serveru na `:8080`.

---

## TOP 10 bottlenecků (podle naměřeného dopadu)

| # | Bottleneck | Dopad | File:line | Naměřeno |
|---|---|---|---|---|
| 1 | HTML regex na asyncio event loopu (drží GIL) | **~99 % CPU** živého workeru během scrape | `app/html_listing.py:77`, `:257`; `app/bazos.py:78`; `app/idnes.py:91` | `sample` 8 s, 6464/6464 vzorků main threadu v `_sre_SRE_Pattern_search` |
| 2 | N+1 upsert katalogu (15 SQL / 1 listing) | **7,7 ms/listing** izolovaně → **18,6 s / 2417** inzerátů; živý tick **188 s** | `app/store.py:2500`, `:2614`, `:3079` | 15 traced queries; cProfile 0,223 s / 30 listingů |
| 3 | `UPDATE listings … WHERE url=? OR listing_key=?` bez indexu na `url` | **5,84 ms / query**, 89 % času `upsert_seen` | `app/store.py:3100-3102` | line_profiler 7,5 ms / 89,4 %; EXPLAIN viz níže |
| 4 | `INSERT INTO events` při každém `kind=refresh` | **113 636** řádků, 100 % `refresh`; nafukuje DB a každý upsert | `app/store.py:3173-3178` | `SELECT kind, COUNT(*) FROM events` → `{refresh: 113636}` |
| 5 | `/api/catalog` pin query (800 řádků + JSON 505 KB) | **48,6 %** `catalog()`; HTTP median **91 ms** / 505 KB | `app/store.py:4161`, `:4335` | line_profiler 38,1 ms; SQL 56,3 ms; HTTP 91,1 ms |
| 6 | `COUNT(*)` katalogu s korelovaným `_hidden_listing_sql` | **5–11 ms** SCAN `listings` na každou stránku | `app/store.py:4115-4117`, `:214-228` | line_profiler 9,2 ms (11,8 %); SQL 4,98–10,80 ms |
| 7 | `catalog_facets` `GROUP BY disposition` | **9,8 ms** SCAN; 13,8 % `catalog()` | `app/store.py:4358-4362` | SQL 9,821 ms; line_profiler 10,8 ms volání |
| 8 | `daily_shards()` skládá 797 URL při každém ticku/statusu | **168 ms CPU** (čistý Python, bez sítě) | `app/catalog_sync.py:96`; voláno z `app/monitor.py:1025`, `app/store.py:3062` | 797 shardů / 168,0 ms |
| 9 | `public_gone_fast` full table SCAN + blokuje event loop | **5,4 ms** SCAN vs **0,004 ms** s `gone=1`; HTTP 10,1 ms | `app/store.py:4824-4836`; `app/main.py:1259` | EXPLAIN: `SCAN listings` + TEMP B-TREE |
| 10 | `catalog_listings` nemá index `(lat, lon)` | nearby lookup **4,48 ms SCAN** vs **0,014 ms** na `listings` | `app/store.py:889-898` | voláno z `_resolve_canonical` bez `fast=True` v `upsert_seen:3098` |

---

## 0. Stack a entry pointy

- Web: FastAPI + uvicorn (`app/__main__.py`, `app/run_dev.py` s `--reload`).
- Persistence: surový `sqlite3`, ne ORM. Nové `sqlite3.connect()` na každé `Store.connect()` — žádný connection pool, `PRAGMA cache_size` default.
- Scrape: asyncio smyčky v `Hub.start()` (`app/monitor.py:150-178`) — monitor, new_discovery, rolling_deep, sold, coords, dedupe — **v tom samém procesu jako HTTP**.
- HTTP klienti portálů: dlouhoživé `httpx.AsyncClient` (`app/sreality.py:119`, `app/html_listing.py:236`) — v pořádku.
- Geocode/OSM/Discord: nový `AsyncClient`/`Client` na každé volání (`app/places.py:531+`, `app/main.py:1766`, `app/discord_notify.py:121`).

---

## 1. Startup / import time

| Měření | Čas |
|---|---|
| `python -X importtime -c "from app.main import app"` | **real 0,69 s** (user 0,41 / sys 0,11) |
| uvicorn `Application startup complete` (`SCRAPE_ROLE=web`, port 8099, stejná DB) | **1425 ms** |

TOP 15 importů podle **self time** (`perf_audit/importtime.raw.txt`):

| self | cum | modul |
|---|---|---|
| 180,3 ms | 601,2 ms | `app.main` (včetně `Hub()` / `Store._init()` na 215MB DB) |
| 39,0 ms | 161,5 ms | `fastapi.openapi.models` |
| 38,8 ms | 68,3 ms | `pydantic._internal._generate_schema` |
| 11,7 ms | 20,5 ms | `charset_normalizer.api` |
| 7,5 ms | 7,5 ms | `pydantic.types` |
| 7,3 ms | 7,3 ms | `annotated_types` |
| 7,1 ms | 7,1 ms | `app.mcp_oauth` |
| 6,1 ms | 6,1 ms | `_zoneinfo` |
| 5,5 ms | 5,5 ms | `pydantic.functional_validators` |
| 5,0 ms | 7,4 ms | `pydantic_core.core_schema` |
| 4,9 ms | 116,9 ms | `fastapi.exceptions` |
| 4,6 ms | 5,3 ms | `click.types` |
| 3,8 ms | 3,8 ms | `fractions` |
| 3,6 ms | 5,3 ms | `zoneinfo._tzpath` |
| 3,5 ms | 4,6 ms | `importlib.readers` |

**Závěr:** studený start není problém (~0,7–1,4 s). Runtime scrape + SQLite je.

---

## 2. CPU profiling

### Živý worker (nejdůležitější číslo v auditu)

Proces `62871` (uvicorn reload child), RSS **200,4 MB**, **99,1 % CPU**. `sample` 8 s, 1 ms interval:

- Main thread (asyncio/uvloop): **6464/6464 vzorků** v `sre_search` → `sre_ucs2_match` (Python regex engine).
- `rf-ui_0`, `rf-job_0`, default thread pool: 6464 vzorků ve `uv_cond_wait` / pthread wait — **prázdné**, protože regex drží GIL.

To je CPU-bound práce na event loopu po `await httpx`. Typický zdroj: `re.sub(r"<[^>]+>", " ", html)` v `clean()` / `_clean()` na celém HTML listingu + `re.findall` fotek přes celou stránku (`html_listing.py:257`).

### cProfile izolovaně (catalog + 30× upsert + gone_fast + count + game pool)

`perf_audit/profile.prof` — 0,313 s, 72 233 calls.

**TOP cumulative:**

| cumtime | ncalls | funkce |
|---|---|---|
| 0,282 s | 414 | `sqlite3.Connection.execute` |
| 0,225 s | 1 | `Store.upsert_catalog_listings_batch` `:2614` |
| 0,223 s | 30 | `Store.upsert_catalog_listing` `:2500` |
| 0,218 s | 30 | `Store.upsert_seen` `:3079` |
| 0,076 s | 1 | `Store.catalog` `:3724` |
| 0,036 s | 1 | `Store._catalog_pins` `:4168` |
| 0,015 s | 1 | `Store._attach_catalog_extras` `:4997` |
| 0,012 s | 1 | `Store.catalog_facets` `:4353` |

**TOP tottime:** `sqlite3.execute` **0,282 s (90 %)**; zbytek Pythonu je šum (`urlparse` 0,002 s, `_pin_item` 0,001 s). CPU v tomto scénáři = SQLite.

### pyinstrument — jen `catalog()`

`perf_audit/pyinstrument_catalog.html` (0,079 s):

- `_catalog_pins` 0,039 s (49 %)
- `_attach_catalog_extras` 0,015 s
- `catalog_facets` 0,012 s
- COUNT/SELECT execute 0,011 s

Otevři HTML v prohlížeči. `profile.prof` ve snakeviz: ` .venv/bin/snakeviz perf_audit/profile.prof`.

---

## 3. Line-level profiling

`perf_audit/line_profiler.txt` (jednotka = ms).

**`Store.catalog` `:3724`**

| Line | % | ms | kód |
|---|---|---|---|
| 4161 | 48,6 % | 38,1 | `payload["pins"] = self._catalog_pins(...)` |
| 4145 | 18,7 % | 14,7 | `self._attach_catalog_extras(...)` |
| 4146 | 13,8 % | 10,8 | `facets = self.catalog_facets()` |
| 4117 | 11,8 % | 9,2 | `COUNT(*) FROM listings WHERE {clause}` |

**`Store._catalog_pins` `:4168`:** L4335 `conn.execute(sql).fetchall()` **87,7 % / 33,1 ms**.

**`Store.upsert_catalog_listing` `:2500`:** L2611 `self.upsert_seen(...)` **79,2 % / 8,4 ms**.

**`Store.upsert_seen` `:3079`:** L3100 `UPDATE listings SET canonical_key=? WHERE url=? OR listing_key=?` **89,4 % / 7,5 ms**.

**`Store.public_gone_fast_rentals`:** L4824 execute **98,4 % / 9,9 ms**.

**`Store.catalog_new_today_count`:** L2360 `SELECT COUNT(*) FROM catalog_listings` **95,5 % / 5,8 ms** (zbytečný full count „je katalog neprázdný?“).

---

## 4. Memory

| Nástroj | Výsledek |
|---|---|
| Živý worker (`sample` physical footprint) | **200,4 MB** (peak stejný) |
| `memory_profiler` catalog+gone_fast+landing | min **90,8 MiB** → max **140,8 MiB** (Δ **50,0 MiB**), 5 vzorků |
| `tracemalloc` (Python alokace téhož) | peak **1,88 MB** — bloat je SQLite page cache + HTML, ne Python objekty |
| scalene (`perf_audit/scalene.json`) | max **132,4 MB**, growth rate 90 % na krátkém catalog loopu |
| Graf | `perf_audit/mprof.png` |

Leak na jednom requestu nevidím (RSS po práci neklesá kvůli SQLite cache, ne kvůli Python cyklu). Riziko růstu: `events` + `listing_photos` bez TTL.

---

## 5. I/O a síť

### HTTP endpointy proti živému `:8080` (5 requestů, median)

| path | median ms | max ms | body |
|---|---|---|---|
| `/` | 2,5 | 6,3 | 34 KB |
| `/api/public/games/higher-lower` | 1,3 | 3,0 | 1,6 KB (cache) |
| `/api/public/stats` | 4,5 | 7,6 | 17 B |
| `/api/public/gone-fast` | **10,1** | 22,9 | 12 B (`{"items":[]}`) |
| `/api/listings` | 2,1 | 4,3 | 12 B |
| `/api/status` | 1,3 | **31,8** (cold cache) | 7,3 KB |
| `/api/catalog?include_pins=1` | **91,1** | 129,6 | **505 KB** |
| `/api/catalog?include_pins=0` | **31,5** | 57,7 | 111 KB |
| `/api/catalog?offer=pronajem&estate=byt&include_pins=1` | 69,6 | 94,6 | 504 KB |
| `/api/catalog/pins?south=49.1&north=49.3&…` | 27,2 | 72,6 | 408 KB |

Piny = **+60 ms a +394 KB** oproti `include_pins=0`.

### Blocking I/O / CPU na event loopu

Tyto handlery volají `hub.store.*` **synchronně** (ne `to_thread` / `ui_pool`):

- `GET /api/public/gone-fast` → `app/main.py:1259`
- `GET /api/listings` → `:1313`
- `GET /api/catalog/item` → `:1558` (navíc synchronní `catalog_item` + později HTTP detail)
- `GET /api/settings`, `GET /api/monitors`, `GET /api/templates` → `:1590+`

Katalog je správně v `hub.ui_pool` (`:1396`). Scrape parse po `await client.get()` běží regex na loopu — to zabíjí concurrency (viz #1).

### HTTP pooling

| Místo | Stav |
|---|---|
| `SrealityClient`, `HtmlPortalClient`, `IdnesClient`, `BezrealitkyClient`, `BazosClient` | 1× `AsyncClient` na instanci, keepalive OK |
| `app/places.py` `_nominatim_*`, Photon, Overpass | **nový client na request** |
| `app/main.py:1766` Nominatim v `/api/filters/locality` | **nový client na request** |
| `app/discord_notify.py:121` | nový client na notifikaci |
| `app/location_amenities.py:257` | **sync** `httpx.Client` |

Sync `httpx` + `time.sleep` v `places.py:779` / `:868` je v `to_thread` přes `geocode_locality` — OK. Když by se zavolalo z async bez threadu, zasekne loop.

---

## 6. Databáze

### EXPLAIN QUERY PLAN + čas (kopie DB, `query_only=ON`)

| Query | median | plan |
|---|---|---|
| pins `LIMIT 800` lat/lon NOT NULL, `ORDER BY notified DESC, rowid` | **56,268 ms** | `SCAN listings USING INDEX idx_listings_notified_seen` + TEMP B-TREE |
| `COUNT listings` + reálné `_hidden_listing_sql` | **4,984 ms** | `SCAN listings` + correlated `listing_user` |
| `COUNT listings` + jen `listing_user.url` EXISTS | 10,798 ms | totéž SCAN |
| facets `GROUP BY disposition` | **9,821 ms** | `SCAN listings` + 2× TEMP B-TREE |
| gone_fast `IFNULL(gone,0)=1` | **5,405 ms** / 0 řádků | **`SCAN listings`** (index se nepoužije) |
| gone_fast `gone=1` | **0,004 ms** | indexovatelný predikát |
| `listings WHERE url=? OR listing_key=?` | **5,837 ms** | není index na `listings.url` |
| nearby `catalog_listings` lat/lon box | **4,475 ms** | **`SCAN catalog_listings`** |
| nearby `listings` stejný box | **0,014 ms** | `SEARCH idx_listings_lat_lon` |
| `listing_links url OR url_key` | 0,004 ms | MULTI-INDEX OR (OK) |
| `COUNT catalog_listings` | 0,013 ms | covering `idx_catalog_first_seen` |
| game pool `ORDER BY last_seen LIMIT 240` | 0,501 ms | `idx_catalog_gone_last_seen` |

### N+1 upsert (traced SQL, 1 listing, `fast=True`)

15 statements na 1 inzerát (`perf_audit/hotpaths.json`):

1. `SELECT canonical_key FROM listing_links WHERE url=? OR url_key=?`
2. `SELECT * FROM catalog_listings WHERE canonical_key=? OR listing_key=?`
3. `INSERT listing_links … ON CONFLICT`
4. `UPDATE catalog_listings …`
5. **znovu** `SELECT listing_links` (`upsert_seen` → `_resolve_canonical` **bez** `fast=True`)
6. **geo SCAN** `listings` nearby + `catalog_listings` nearby (`_find_canonical_nearby`)
7. `UPDATE listings SET canonical_key=? WHERE url=? OR listing_key=?` ← 5,8 ms
8. `SELECT listings WHERE monitor_id=? AND canonical_key=?`
9. znovu `INSERT listing_links`
10. `INSERT listings … ON CONFLICT DO UPDATE` (plný řádek včetně `description`/`extras`)
11. `INSERT INTO events … kind='refresh'`
12. `SELECT price_history … LIMIT 1`
13. `SELECT COUNT(*) FROM listing_photos …`

`fast=True` na `upsert_catalog_listing` **nepomůže**, protože `upsert_seen:3098` volá `_resolve_canonical(conn, listing)` bez `fast=True`.

Projekce: 2417 listingů × 7,706 ms = **18,6 s** bez locků. Živý log `new_discovery listings=2417 new=0 ms=188799` = **188,8 s** — zbytek je HTTP + regex (#1) + `database is locked` retry (`busy_timeout` 8 s v batchi, 80 ms na request threadu).

### Chybějící indexy (konkrétně)

```sql
CREATE INDEX idx_listings_url ON listings(url);
CREATE INDEX idx_catalog_lat_lon ON catalog_listings(lat, lon);
-- gone_fast: predikát gone = 1 (ne IFNULL(gone,0)=1), index idx_listings_gone_seen už existuje
```

`listing_photos` 401 269 řádků / 25 315 distinct listingů. PK `(monitor_id, listing_id, url)` existuje; COUNT jedné položky je 0,003 ms — problém je **velikost tabulky** (každý refresh zkouší COUNT + případný INSERT).

---

## 7. Concurrency / async

| Položka | Stav | Měření |
|---|---|---|
| GIL | Regex C-API drží GIL → UI/job thready čekají | sample: thread pool 100 % ve wait, main 100 % v `sre_search` |
| Event loop | CPU regex + sync SQLite na loopu (gone-fast, settings, …) | HTTP p95 catalog 130 ms i při 99 % CPU scrape |
| `ui_pool` | 4 thready (`monitor.py:56`) | během sample idle |
| `job_pool` | **2** thready (`:58`) na všechny catalog upserty | serializace zápisů; lock `Hub._catalog_write` |
| `auth_pool` | 2 | OK pro login |
| `SCRAPE_CONCURRENCY` | default 16 | HTTP fan-out je v pořádku; bottleneck je parse+SQL po response |
| `daily_shards` | 797 URL, z toho sreality 382 | 168 ms čistě skládat URL **pokaždé** v `_deep_catalog_tick` |
| `recent_shards` | **44** shardů × `SCRAPE_RECENT_PAGES=4` | až 176 HTTP GET / discovery tick, deadline 50 s |
| Deep | 10 shardů × 20 stránek, loop 2 s | log `rolling_deep … ms=126259` (první), pak 6–15 s |

Async HTTP je správně (`await client.fetch_page`). Špatně je **synchronní CPU/SQL hned za tím** na stejném loopu.

Žádný klasický „requests v async def“ — používá se httpx. Výjimky: sync `httpx.Client` v geocode/overpass (většinou v threadu).

---

## 8. Bottlenecky — root cause a oprava

### 1. HTML regex na event loopu — `html_listing.py:77`, `:257`

**Čísla:** 99,1 % CPU, 8 s sample = 100 % main thread v `sre_search`.  
**Příčina:** `clean()` dělá `re.sub(r"<[^>]+>", " ", html)` na celém dokumentu; `_parse_detail` `re.findall` všech `src|href` s příponou obrázku na celém HTML. Totéž `_clean` v `bazos.py:78` a `idnes.py:91`. Regex drží GIL.  
**Oprava:** (a) parse jen výřezu karty/JSON, ne celé stránky; (b) tag-strip nahradit `html.parser` / `selectolax` / `lxml` na omezeném fragmentu; (c) `await asyncio.to_thread(parse, html)` nebo process pool, ať loop obslouží HTTP. Konkrétně smaž `re.findall(..., html)` v `:257` a hledej fotky v `<img>` uvnitř known containeru.

### 2–3. Upsert 15 query + UPDATE bez indexu — `store.py:3100`, `:2611`, `:3098`

**Čísla:** 7,43 ms / 15 SQL / listing; 50 listingů = 385 ms; 2417 listingů = 18,6 s izolovaně.  
**Příčina:** `upsert_catalog_listing` volá `upsert_seen`, který znovu resolvuje identitu (geo SCAN), zapisuje do `listings` **a** `catalog_listings`, a dělá `UPDATE … WHERE url=? OR listing_key=?` (OR + chybí index na `url` → 5,84 ms).  
**Oprava:**

1. `CREATE INDEX idx_listings_url ON listings(url);` a split OR na dva lookupy.
2. V `upsert_seen` předat `fast=True` a **přeskočit** `_find_canonical_nearby` na refresh path.
3. Refresh z discovery **nezapisovat** do `listings` + `events` + `listing_photos` — jen `catalog_listings` + `listing_links`. `upsert_seen` nechat pro monitor hits.
4. Batch: jeden `SELECT listing_key IN (…)` + `executemany` UPDATE/INSERT místo 15 roundtripů.

### 4. Events table — `store.py:3173`

**Čísla:** 113 636 řádků, 100 % `refresh`. Každý discovery upsert insertuje event i když `new=0`.  
**Oprava:** `if event_kind not in {"seeded", "refresh"}:` (nebo jen `new`/`changed`). TTL `DELETE FROM events WHERE created_at < …`. To zmenší DB a zrychlí INSERT.

### 5. Catalog piny — `store.py:4161` / `:4335`

**Čísla:** 56 ms SQL + 38 ms v Pythonu; HTTP 91 ms / 505 KB vs 32 ms / 111 KB bez pinů. Default `include_pins=1`.  
**Příčina:** `SELECT … FROM listings WHERE lat/lon NOT NULL ORDER BY notified DESC, rowid LIMIT 800` nepoužije dobře `idx_listings_notified_seen`.  
**Oprava:** default `include_pins=0` a piny jen z `/api/catalog/pins` (ten endpoint už existuje). Index `(lat, lon)` covering. Široký zoom: cluster SQL (`GROUP BY round(lat,2), round(lon,2)`), ne 800 bodů.

### 6. COUNT + hidden EXISTS — `store.py:4117`, `:214`

**Čísla:** 5–10 ms SCAN 25k řádků na každou stránku.  
**Oprava:** `WHERE IFNULL(gone,0)=0` bez korelovaného `listing_user`, pokud je `listing_user` prázdný (teď skoro je). Jinak denormalizovat `hidden` flag. Approximate total: `total = offset + len(items) + (limit if full page)` — kód na `:4164` to už částečně dělá, ale COUNT se stejně spouští dřív.

### 7. Facets SCAN — `store.py:4358`

**Čísla:** 9,8 ms; cache 30 s (`:4355`) pomáhá až po prvním zásahu. `catalog()` volá facets vždy.  
**Oprava:** cache 5–15 min; nebo `SELECT DISTINCT disposition` z menší tabulky / materialized meta. Přeskočit facets, když je klient neposílá (`?facets=0`).

### 8. `daily_shards()` 168 ms — `catalog_sync.py:96`

**Čísla:** 797 shardů, 168 ms. Volá se v `_deep_catalog_tick:1025` jen kvůli `len(...)` a v `catalog_sync_status:3062`.  
**Oprava:** `@lru_cache` / konstanta `DAILY_SHARD_COUNT` a `SREALITY_DEEP_COUNT`. Nesestavovat 797 URL, když potřebuješ číslo.

### 9. gone-fast SCAN + sync handler — `store.py:4824`, `main.py:1259`

**Čísla:** 5,4 ms SCAN (0 gone řádků!) vs 0,004 ms s `gone=1`; HTTP 10 ms na prázdnou odpověď.  
**Oprava:** `WHERE gone = 1 AND last_seen >= ? AND image_url != ''` (index `idx_listings_gone_seen`). `LIMIT 64` v SQL, zbytek filtru v Pythonu. Obalit `asyncio.to_thread`.

### 10. Nearby catalog SCAN — `store.py:889`

**Čísla:** 4,48 ms vs 0,014 ms. Volá se z `upsert_seen` na každém refresh listingu s GPS.  
**Oprava:** `CREATE INDEX idx_catalog_lat_lon ON catalog_listings(lat, lon);` a na refresh path nearby **vypnout** (`fast=True`).

---

## Quick wins (≤ 30 min, velký dopad)

1. **`upsert_seen(...,)` → `_resolve_canonical(..., fast=True)`** a na `kind=="refresh"` **neinsertovat** `events` / nespouštět nearby. Očekávaný efekt: ~7,5 ms → ~1–2 ms / listing (zmizí 5,8 ms UPDATE scan + geo SCAN + event). Discovery 2417 listingů: 18 s → ~3–5 s čistého zápisu.
2. **`CREATE INDEX idx_listings_url ON listings(url);`** + **`CREATE INDEX idx_catalog_lat_lon ON catalog_listings(lat, lon);`**
3. **`gone = 1` místo `IFNULL(gone,0)=1`** v `public_gone_fast_rentals`; handler do `to_thread`.
4. **`include_pins=0` default** v `_catalog_filters` / `/api/catalog`. HTTP 91 ms / 505 KB → ~32 ms / 111 KB (už změřeno).
5. **`functools.lru_cache` na `daily_shards` / `len(daily_shards())`.** −168 ms z každého deep ticku a admin/status cold path.
6. **`catalog_new_today_count`:** pryč `SELECT COUNT(*) FROM catalog_listings` bez WHERE (`store.py:2360`); stačí count s `first_seen >= today` (index už je).

## Bigger refactors (větší zásah, větší dopad)

1. **HTML parse pryč z event loopu** + přestat čistit celé HTML regexem (`clean` / photo findall). To je #1 podle live CPU. ProcessPoolExecutor na parse, nebo JSON API (Sreality `_next/data` už existuje — nenechat padat do HTML fallbacku tiše).
2. **Oddělit catalog write path od `listings`/`events`.** Discovery má zapisovat jen `catalog_listings`. Dnes se duplicitně drží 25k + 25k řádků a každý refresh sahá do obou.
3. **`executemany` batch upsert** (1 SELECT IN + 1 INSERT OR REPLACE) místo 15 query / řádek.
4. **Supervisord split `SCRAPE_ROLE=web|worker`** i lokálně — 99 % CPU scrape dnes sdílí GIL s HTTP. Konfig už existuje (`config.py:61-64`).
5. **TTL / cap `listing_photos` a `events`.** 401k fotek a 114k eventů na 25k inzerátů.
6. **Sdílený `httpx.AsyncClient` pro Nominatim/Photon/Overpass.**

---

## Co není bottleneck

- Import/startup (0,7–1,4 s).
- Marketing games (`/api/public/games/*` ~1 ms, seed cache).
- `game_listing_pool` SQL 0,5 ms (progress handler + `quick` connect už je).
- Keepalive u portálových `AsyncClient`.
- `listing_links` PK/indexy (0,004 ms).

---

## Přiložené soubory (`perf_audit/`)

| Soubor | Obsah |
|---|---|
| `profile.prof` | cProfile (snakeviz: `.venv/bin/snakeviz perf_audit/profile.prof`) |
| `pyinstrument_catalog.html` | call-stack HTML `Store.catalog` |
| `cprofile_cumulative.txt` / `cprofile_tottime.txt` | TOP 40 |
| `line_profiler.txt` | řádkový profil 8 funkcí |
| `sample.txt` | 8s live CPU (náhrada `flame.svg`; py-spy potřebuje sudo) |
| `mprof.png` / `mprof.dat` | RSS v čase |
| `scalene.json` | scalene CPU+memory (`scalene view perf_audit/scalene.json`) |
| `http_bench.json` | HTTP median/max |
| `sql_timings.json` | EXPLAIN + ms u konkrétních SQL |
| `hotpaths.json` | 15 traced upsert SQL + časy funkcí |
| `explain_plans.json` / `sqlite_stats.json` / `shards.json` | schéma, indexy, 797/44 shardů |
| `importtime.raw.txt` / `uvicorn_ready.txt` | startup |

`flame.svg` z py-spy se nepodařilo zapsat: `This program requires root on OSX`. Ekvivalent je `sample.txt` (živý proces, 99 % CPU v regexu) + `pyinstrument_catalog.html` (katalog).
