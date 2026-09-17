# Wave 3b — reálné načtení stránky za plného scrape

Měřeno 17. 9. 2026 na `make dev` (`SCRAPE_ROLE=all`, worker PID 72446, `cpu_count=10`).
Živá DB `data/monitor.sqlite`, scrape běžel (`rolling_deep` / `new_discovery`).
Dočasná instrumentace: `app/perf_diag.py` → `perf_audit/live_diag.jsonl`, watchdog 100 ms, SQL wrap, `GET /api/perf/diag`.

**Závěr:** desítky sekund nejsou pomalý `/api/catalog`. Viník je **čekání na event loop** (TTFB document requestu 6–17 s, watchdog lag až **65,7 s**). Loop je real-blokovaný scrape tickem: zbývající regex/JSON na main threadu **a** GIL starvation `to_thread` workerů. SQLite `database is locked` se **neobjevilo** (`lock_n=0`). Parallelizace frontendu TTFB dokumentu nespraví. Zvětšení thread poolu GIL zhorší. `SCRAPE_ROLE` split je ta správná oprava pro `make dev`.

---

## 1. Waterfall — který request trvá desítky sekund

### 1a. `/nabidka` (nepřihlášený → 303 na `/registrace`)

DevTools `performance.getEntriesByType('navigation'|'resource')`, view `http://127.0.0.1:8080/nabidka`:

| Request | start (ms) | duration (ms) | TTFB (ms) | size |
|---|---:|---:|---:|---:|
| **document `GET /nabidka` → 303 `/registrace`** | 0 | **17 257** | **17 175** | 0 |
| static `auth.css` / `site.css` / SVG (paralelně) | 17 196 | 26–49 | 5–23 | cached |
| `GET /api/filters/catalog` | 17 233 | **6** | 5 | 22 KB |
| `GET /api/settings` | 17 233 | **12** | 11 | 621 B |
| `POST /api/t` | 17 256 | 16 | 14 | 311 B |

Souběžný curl (follow redirect vypnutý):

```
GET /nabidka  http=303  ttfb=16.918s  total=16.918s  size=0
Location: /registrace?next=%2Fnabidka
```

**Ten request je document navigation.** Handler (middleware `require_account` + 303) po uvolnění loopu doběhne v milisekundách. 17 s je fronta, než loop vůbec začne request zpracovávat (0 bajtů na drátě).

Po příchodu HTML jsou API **6–16 ms a paralelní**. To není bottleneck.

### 1b. Landing `GET /`

Stejný DevTools, `http://127.0.0.1:8080/`:

| Request | start (ms) | duration (ms) | wait/TTFB (ms) | poznámka |
|---|---:|---:|---:|---|
| **document `GET /`** | 0 | **6 307** | **6 208** | 0 redirect |
| `GET /api/public/gone-fast` | 6 242 | 2 905 | 2 904 | paralelní se stats |
| `GET /api/public/stats` | 6 242 | 2 910 | 2 908 | paralelní s gone-fast |
| `GET /api/auth/me` | 6 243 | 7 | 5 | 401, rychlé |
| `POST /api/t` | 6 267 | 2 897 | 2 895 | stejné okno jako stats |
| `POST /api/t` (později) | 26 271 | 7 220 | 7 211 | další scrape okno |

Frontend `web/site/site.js`: `renderGoneFast()` a `renderNewToday()` startují **paralelně** hned po parse (ne čekají na sebe). Oba ale čekají na stejný loop stall (~2,9 s).

Curl ve stejném okně (sekvenčně, takže se sčítá fronta):

| path | HTTP | wall (s) | bytes | když loop běží |
|---|---:|---:|---:|---|
| `/` | 200 | **5.908** | 34 038 | document TTFB |
| `/api/public/stats` | 200 | 1.999 | 17 | |
| `/api/public/gone-fast` | 200 | **7.660** | 12 | `to_thread` + GIL |
| `/api/public/landing-listings` | 200 | **8.205** | 1 596 | |
| `/api/catalog?include_pins=0` | 200 | **0.150** | 67 329 | SQL je rychlé |
| `/api/catalog/pins` | 200 | **0.079** | 394 961 | SQL je rychlé |
| `/api/status` | 200 | **8.090** | 7 321 | `hub.status` v threadu |
| `/api/perf/diag` | 200 | 0.003 | 3 668 | |

Dřívější okno (první `rolling_deep` po reloadu): `GET /` i `GET /api/public/stats` **timeout 15,00 s / 0 bajtů**. Až loop naskočil, stejný `/` doběhl za 145 ms (middleware), catalog 73 ms, pins 104 ms, diag 3 ms.

### 1c. Access log + korelace se scrape tickem

Uvicorn default access log **nemá duration**. Middleware `record_ops_timing` měří až **od začátku zpracování** — frontu před `call_next` nevidí. Proto `PERF req` u `/` ukáže 145 ms, zatímco curl vidí 15 s timeout.

Korelace wall-clock (UTC) z `live_diag.jsonl` + logu monitoru:

| čas UTC | událost |
|---|---|
| 14:36:58 | worker start (PID 72446), `SCRAPE_ROLE=all` |
| 14:38:02 | **watchdog sleep_lag = 62 174 ms**, `to_thread=0` |
| 14:37:17–14:38:05 | curl `GET /` a `/api/public/stats` timeout 15 s / 0 B; `/api/status` 13.75 s |
| 14:38:05 | po uvolnění: `/` 145 ms, catalog 73 ms, pins 103 ms |
| 14:39:18 | **watchdog spin = 65 716 ms**, `to_thread=9` |
| 14:39:23 | `rolling_deep listings=1261 … ms=78666` (první shard) |
| 14:40:09 | `new_discovery listings=2199 new=16 ms=122472` |
| 14:41:03 | watchdog sleep_lag **50 636 ms** — curl `/nabidka` TTFB 16.9 s, browser nav 17.3 s |
| 14:41:52 | watchdog sleep_lag **47 882 ms** |
| 14:43:xx | landing TTFB 6.2 s; `rolling_deep` cover 55–81 %, `to_thread` 20–22 in-flight |

Reprodukce **není náhodná a není „pořád“**. Je to **okno ticku**. Mezi tickama je catalog 80–150 ms. Během `rolling_deep` / `new_discovery` (hlavně první velké shardy 78–129 s a discovery 122–174 s) je TTFB dokumentu 6–17 s, watchdog 50–66 s.

Předchozí worker (PID 71480, před reloadem): `rolling_deep … ms=129053`, `new_discovery … ms=173764`. Stejný vzor.

---

## 2. Event loop lag přímo

Watchdog: každých 100 ms `t0=monotonic(); await sleep(0); log(delta)` + `sleep(0.1)`.

Z `perf_audit/live_diag.jsonl` (17 lag eventů ≥ 50 ms):

| metrika | hodnota |
|---|---|
| `watch_max_ms` | **65 715.6** |
| max `sleep_lag_ms` | **62 174.2** (`to_thread=0`) |
| max `spin_ms` (`sleep(0)`) | **65 715.6** (`to_thread=9`) |
| další stally | 50 636 ms, 47 882 ms, 2 116 ms, 1 659 ms, 1 051 ms |
| lag při `to_thread` 12–13 | 311–323 ms (GIL, ne 60 s) |
| `lock_n` | **0** |
| `SCRAPE_ROLE` | `all` |

`sleep(0)` na **65 s** = loop nespustil nic jiného. To není odvozené z HTTP — je to přímý důkaz, že Wave 3 `to_thread` **neodstranil** real-blocking.

Dva režimy:

1. **`to_thread=0`, 62 s sleep_lag** — práce běží **na main threadu** (žádný offload).
2. **`to_thread=9–22`, spin 65 s / lag 300 ms–48 s** — workery běží, ale GIL + zbývající CPU na loopu pořád drží loop.

### `/usr/bin/sample` **během** pomalého requestu

**A) PID 72446, 16:37:58, 8 s, 100 % CPU** (`perf_audit/sample_wave3b.txt`) — souběh s 15 s timeoutem `GET /`:

- Main thread 6477 samples v `uvloop` → `asyncio.task_step`.
- **4776 / 6477 (~74 %) `_sre_SRE_Pattern_search` / `sre_search` na main threadu.**
- 391 samples `pysqlite_connection_execute` na main threadu (~6 %).
- `run_in_executor` 5 samples, **žádný `ThreadPoolExecutor` frame** — parse v tom okně nebyl v threadech.
- Worker 97–100 % CPU.

**B) PID 72446, 16:43:28, 8 s** (`perf_audit/sample_wave3b_during_nav.txt`) — souběh s landing TTFB 6 s / `rolling_deep` cover ~55 %:

- Main thread: většina `kevent` (GIL puštěný) + **161 samples `_json.scanner_call` / `scan_once_unicode` přes `task_step`** = `response.json()` / `json.loads` **na loopu**.
- **14× `rf-to-thread_*`** (default executor). 13 z nich skoro 100 % v `lock_PyThread_acquire_lock` (čekají na GIL/zámek). `rf-to-thread_1` 6573/6648 v `_ssl__SSLSocket_read` → `poll` (blokující HTTP v default executoru).
- `rf-ui_0` existuje, `ui` in-flight v diag = 0 (loop neschopný submitnout UI práci, nebo UI čeká).
- RSS peak 891 MB.

CPU tedy **není „99 % sre_search jen ve workerech“**. Je to mix:

- pořád **regex na main threadu** (vzorek A),
- **JSON parse na main threadu** (`SrealityClient._fetch_detail_next` / `_fetch_next_data` volá `response.json()` v korutině; HTML fallback sice dává `_next_data_json` do `to_thread`, ale `parse_search_payload` zůstává na loopu),
- **GIL** — 14 parse/HTTP threadů, fronta `to_thread` in-flight 21–22.

Zbývající on-loop regex (Wave 3 díry):

- `idnes.py`: `self._results_html(html)` = `RESULTS_RE.search` **na loopu**, pak teprve `to_thread(_parse_list, …)`. `RESULTS_RE` je `[\s\S]*?` mezi markery — bez koncového markeru klasický ReDoS na celém HTML.
- `html_listing.fetch_page`: `_parse_total(raw)` = `COUNT_RE.search` na **plném** HTML na loopu (`extract_listing_html` je `str.find`, ten je v pohodě).
- Extra portály: `CARD_RE` / `ARTICLE_RE` (`.*?`, `re.S`) v `_parse_list` (v threadu → GIL).
- `ulovdomov._parse_list`: `re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', …, re.S)` v `to_thread`.

---

## 3. DB lock kontence

| | |
|---|---|
| `lock_n` za celou relaci | **0** |
| `lock_wait_ms` | **0** |
| JSONL `kind=lock` | 0 řádků |
| `sqlite3.OperationalError: database is locked` | nepozorováno |

Hypotéza „5 requestů × `busy_timeout=8 s` = 40 s“ na této stránce **neplatí**. Catalog/pins po uvolnění loopu = 73–150 ms, bez retry.

Endpointy stránky vs. loop:

| endpoint | handler | DB |
|---|---|---|
| `GET /`, `GET /nabidka` (`def page` / `def landing`) | FastAPI sync → default/AnyIO pool, **ale loop musí request naschedulovat** | ne |
| `require_account` na `/nabidka` | async, `auth_pool` (2) | session lookup |
| `GET /api/public/stats`, `gone-fast`, `landing-listings` | async + `asyncio.to_thread` | `ui_pool` (4) až doběhne loop |
| `GET /api/catalog`, `/api/catalog/pins` | async + `ui_pool` | rychlé |
| `GET /api/status` | `to_thread(hub.status)` | |
| `GET /api/public/games/higher-lower` | **sync `higher_lower_pair(store)` na loopu** | ano, na loopu |
| `GET /api/catalog/item` | lookup v `to_thread`, pak **`fetch_detail` + `save_listing_enrichment` na loopu** | mix |

Sync hry a catalog-item enrichment **nejsou** viníkem document TTFB (landing/nabidka je), ale na loopu pořád umí přidat stall, když je uživatel otevře.

`_run_catalog_job` pořád volá `self.store.update_scrape_job(...)` **synchronně na loopu** (start jobu). To je krátké; 60 s je parse/JSON.

---

## 4. Kolik requestů frontend dělá a v jakém pořadí

### Landing `/`

Paralelně po HTML (ne čekají na sebe):

1. `GET /api/public/gone-fast`
2. `GET /api/public/stats`
3. `GET /api/auth/me` (`t.js`)
4. `POST /api/t` (telemetry)

Waterfall: 1+2 start ve stejném ms. **Není to sekvence 6–8 endpointů.** Součet 2,9 s je společný loop lag, ne 2× SQL.

### Katalog `/nabidka` (`web/catalog.js`)

```
fetch("/api/settings")  ─────────────────────────────┐
fetch("/api/monitors")  (paralelní, jen views)        │
settingsReady.finally(() => loadCatalog())            ▼
  GET /api/catalog?...&include_pins=0
    then await refreshCatalogPins()
      GET /api/catalog/pins?...
```

Plus případně `GET /api/places/geometry` po výběru lokality, `GET /api/filters/locality` při typeahead (Nominatim), `GET /api/catalog/item` až při otevření karty.

**Sekvenční settings → catalog → pins je pravda**, ale když loop běží: 12 + 73 + 104 ms ≈ **190 ms**. Catalog má 25 s `AbortController`. Při 17 s TTFB dokumentu se k catalog fetch vůbec nedostane, dokud HTML nepřijde; při dalším 60 s stallu by catalog abortnul.

Sekvenční fetch **není** důvod desítek sekund. Je to jen drobný násobič stovek ms, až loop ožije.

---

## 5. Thread pool saturace

| pool | size | měření |
|---|---|---|
| default `to_thread` | `min(32, cpu_count+4)` = **14** (10 CPU) | in-flight **13, 21, 22** (CountedPool počítá i frontu) |
| `ui_pool` | **4** | během stallu **0** in-flight |
| `auth_pool` | 2 | 0 během stallu |
| `job_pool` | 2 | 0–1 |
| `SCRAPE_CONCURRENCY` | default **16** | `asyncio.gather` oken stránek v `ScrapeEngine` |

22 in-flight na 14 workerech = **8 úloh ve frontě default executoru**. UI requesty jdou do `ui_pool` (jiné 4 thready), ale:

- loop je zaseknutý → `run_in_executor(ui_pool, …)` se nenascheduluje,
- i po naschedulování GIL drží scrape `to_thread` (regex/json) → `gone-fast` 7,7 s, `status` 8,1 s.

Zvětšit default executor na 32 **nepomůže**: víc threadů = horší GIL. `to_thread` v jednom procesu GIL starvation **neřeší**.

---

## Verdikt

| hypotéza | výsledek |
|---|---|
| Jeden pomalý catalog/pins SQL | **Ne.** 73–150 ms, až loop běží. |
| SQLite busy_timeout skládání | **Ne.** `lock_n=0`. |
| Sekvenční frontend 6–8× 1–2 s | **Ne jako primární.** Document TTFB 6–17 s *před* API. Po HTML jsou API paralelní nebo ~190 ms sekvence. |
| GIL starvation `to_thread` | **Ano, reálné.** Watchdog spin 65 s při `to_thread=9`; 14 workerů, 22 in-flight, sample: thready čekají na GIL. |
| Loop real-block i po Wave 3 | **Ano.** 74 % `sre_search` na main; `response.json()` na main; `idnes._results_html` / `_parse_total` na loopu. |

**Viník requestu:** `GET /` resp. `GET /nabidka` (document). Ne `/api/catalog`.

**Příčina:** scrape tick v tom samém procesu (`SCRAPE_ROLE=all`) sežere loop i GIL na desítky sekund. `perf-quick` to nevidí, protože běží proti kopii DB bez souběžného scrape.

### Co z toho plyne pro další krok

1. **`SCRAPE_ROLE` split (web vs worker) je správná oprava pro `make dev`.** `to_thread` v jednom procesu GIL neoddělí. Isolated upsert 0,3 ms a catalog 19–60 ms z Wave 2/3 zůstanou platné — reálný page load je jiná osa.
2. Doladění thread poolu / Promise.all na katalogu **nestačí** na 17 s TTFB dokumentu.
3. I se splitem stojí za to sundat z loopu: `response.json()` v `sreality.fetch_detail` / `fetch_page`, `idnes._results_html` + `_parse_total`, sync `higher_lower_pair`, `catalog_item` enrichment. To jsou zbývající Wave 3 díry, ne náhrada splitu.

Artefakty: `perf_audit/live_diag.jsonl`, `perf_audit/sample_wave3b.txt`, `perf_audit/sample_wave3b_during_nav.txt`.
