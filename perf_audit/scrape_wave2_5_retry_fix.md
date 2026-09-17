# Wave 2.5 — retry řetězec, timeout, circuit breaker

Datum: **2026-09-17**. Před další iterací `SCRAPE_CONCURRENCY_OVERRIDES`.
Prostředí stejné jako Wave 0–2: `data/monitor.sqlite`, tři pipeline, `SCRAPE_METRICS_DETAIL=1`, vedle běžícího `make dev`.

JSON:

- baseline: `scripts/perf/scrape_baseline.json`
- Wave 2b (concurrency cap 24): `scripts/perf/scrape_wave2b.json`
- Wave 2.5 první běh (timeout 5/8, ještě bez `asyncio.timeout`): `scripts/perf/scrape_wave2_5.json`
- Wave 2.5b (`asyncio.timeout(8)` + breaker, který omylem vypnul Sreality): `scripts/perf/scrape_wave2_5b.json`
- Wave 2.5c (finální, stall-aware breaker): `scripts/perf/scrape_wave2_5c.json`

---

## Gate

**FAIL na cílenou metriku (fetch p95 / watchdog max). PASS na celkový pages/s.**

| Cíl | Výsledek |
|---|---|
| fetch p95 u 5 HTML portálů = rychlý úspěch **nebo** rychlé selhání (jednotky s) | **FAIL** — pořád ~49–52 s |
| watchdog max řádově níž než 89 s, ideálně jednotky s | **FAIL** — max **49,5 s** (stejný cluster jako předtím) |
| pages/s CELKEM nahoru, protože se přestane topit kapacita do mrtvých portálů | **PASS vs Wave 2b** 4,069 → **4,794** (+18 %); vs baseline 4,702 **+2 %** |
| coverage cycle bez čekání za HTML portály | **PASS vs baseline** 0,40 h → **0,37 h** |

Fetch p95 u úspěšných requestů (Sreality p50 233 ms / p95 1,1 s, Bazos p95 115 ms, Bezrealitky p50 1,2 s) potvrzuje, že **8 s timeout je pro zdravý request až moc**. 50s hodnoty nejsou „pomalý HTTP“ — jsou to **zpožděné timeouty po zamrznutí event loopu**. Proto concurrency overrides teď **neiterovat**. Další páka není limiter, ale odblokovat loop (Wave 3 / CPU+thread pool).

---

## 1. Retry řetězec a blokující sleep

### Co bylo (před touto vlnou)

| Místo | Pokusy | Backoff | Sleep |
|---|---|---|---|
| `bezrealitky.py` GraphQL 403 | `range(3)` | `0.8*(attempt+1)` s | **`await asyncio.sleep`** (jen coroutine, ne celý loop) |
| `html_listing.py` `fetch_page` | 1 (httpx default) | žádný | žádný |
| `scrape_engine.fetch_one_page` | 1 | až 20 s | **`await asyncio.sleep` jen při 429** (403 sleep odstraněn) |
| `ulovdomov.py` | POST, při ne-200 HTML GET | žádný sleep | druhý HTTP request (p95 140 s = 2× timeout) |
| `httpx.Timeout(25.0)` | — | — | connect+read+write+pool **každá** 25 s → teoreticky ~50 s |

**`time.sleep()` v HTML `fetch_page` / `fetch_detail` NENÍ.** Prohledáno `html_listing.py`, `idnes.py`, `bezrealitky.py`, `ulovdomov.py`, `mmreality.py`, `realitycz.py`. Zbylé `time.sleep` jsou mimo fetch (SQLite `job_pool` v `store.py` / `monitor.py`, geocode v `places.py` v threadu, identity relink 8 s v daemon threadu).

### Co je teď

- `SCRAPE_HTTP_RETRIES=1` → log `n=1/1`, žádný druhý pokus stejného list requestu.
- Mezi pokusy **žádný sleep**.
- `bounded_request`: `asyncio.timeout(SCRAPE_HTTP_TIMEOUT)` navíc k httpx timeoutu.
- Bezrealitky: jeden GraphQL pokus, 403 hned raise, bez `asyncio.sleep`.
- Ulovdomov: POST 500 **raise**, žádný HTML fallback.
- Engine backoff jen na **429**.

### httpx timeout — není to 25 s, ale pořád se „sčítá“ jinak

`scrape_timeout()` = `Timeout(connect=5, read=8, write=8, pool=5)`. To **není** 25 s. Fáze jdou za sebou, strop je tedy ~13 s (connect+read), ne 50.

Naměřených **49–52 s** ale sedí na `SCRAPE_DISCOVERY_DEADLINE_SEC=50`, ne na 8 s read timeout. `asyncio.timeout(8)` v testu (`Slow.request` + `asyncio.sleep(5)`) zruší request do 0,2 s — **když loop běží**. Když loop 50 s neběží, timer se naplánuje a vystřelí až po unfreeze. Proto log říká `ReadTimeout: wall-clock 8.0s` s `ms=49500`.

### Časová korelace watchdog ↔ attempt (bod 1) — **potvrzeno**

Wave 2.5c, první stall:

| Událost | UTC | ms |
|---|---|---|
| pokusy start (mmreality / ulovdomov / remax) | 2026-09-17T18:50:17 | — |
| `scrape watchdog lag_ms=2422.6` | 18:50:17 | začátek zamrznutí |
| `scrape watchdog lag_ms=49230.4` | **18:51:06** | |
| `mmreality error ConnectTimeout ms=49329` | **18:51:06.598** | |
| `remax error ReadTimeout: wall-clock 8.0s ms=52461` | **18:51:06.601** | |
| `realitycz` / `ulovdomov` error ~49520 ms | **18:51:06.601–602** | |

Stejný vzor ve Wave 2.5 (18:31–18:35, 6× ~49,5 s) a 2.5b (18:41:02 / 18:41:52). Watchdog outlier a attempt-error sdílejí timestamp na milisekundu — timeout se nevyhodnotil v 8. sekundě, ale ve chvíli, kdy se loop znovu rozběhl.

Úspěšné requesty ve stejném běhu před freeze: annonce 251 ms, bezrealitky 558–716 ms, mmreality 1464 ms, idnes 3861 ms. Tedy: **zdravý fetch je pod 5 s; 50 s je stall.**

---

## 2. Circuit breaker

In-memory `app/portal_health.py` (ne `meta`). Po N po sobě jdoucích 403/500/timeout → `disabled_until=now+cooldown`, skip fetch, log jednou `portal X disabled: reason; retry at Y`. Cooldown 15 → 30 → 60 min cap. **Není** AdaptiveLimiter (ten řeší 429/403 rate limit).

Doplněno po 2.5b (viz níže):

- in-flight fail **přestane retrippovat** už disabled portál (cooldown se nezdvojnásobí 12× za 1 ms)
- in-flight 200 **nepovolí** disabled portál zpět
- timeouty ze **3+ portálů v okně 2 s** = loop stall, ne smrt portálu (Sreality se nevypíná)
- 403/500 se počítají vždy

### Stav po 5 min (Wave 2.5c)

| Portál | disabled | reason | disable_count | skips | poznámka |
|---|---|---|---|---|---|
| **ulovdomov** | **ano** do 19:08:56 | timeout | 1 | 3 | první trip 18:53:56; POST 500 teď fail-fast (~3 s) |
| mmreality | ne | — | 0 | 0 | 8× HTTP 200 (prázdný parse) + timeouty ve stallu → consecutive se resetuje |
| realitycz | ne | — | 0 | 0 | totéž: 200 maintenance HTML ≠ 403 |
| idnes / bezrealitky | ne | — | 0 | 0 | živé; p50 úspěchu 1–4 s |
| sreality | ne | — | 0 | 0 | 1187 ok / 33 fail |
| ostatní | ne | — | 0 | 0 | |

README má pravdu, že mmreality/realitycz/ulovdomov jsou dlouhodobě mrtvé, ale **ne vždy to poznáme z 403/500**: mmreality v tomhle okně vrací i **200** (Cloudflare HTML, 0 listings). Breaker na 200 neshodí. Ulovdomov 500 breaker vidí a po sérii timeoutů ho vypne.

### Wave 2.5b — anti-pattern (nepoužívat ta čísla jako „after“)

Breaker trefil **Sreality** (disable_count **12**, skips **400**, cooldown 3600 s). Příčina: 5+ in-flight timeoutů ve stejné ms → trip → reset consecutive → další in-flight jako „probe fail“ → cooldown 15→30→60 min v jednom burstu; zároveň `record_ok` z doletěných 200 Sreality portál znovu zapnul. pages/s spadly na **1,48** (−68 %). Opraveno před 2.5c.

---

## 3. Timeout a retry budget

| Knob | Dřív | Teď | Proč |
|---|---|---|---|
| httpx | `Timeout(25)` všude | connect 5 / read-write 8 / pool 5 | 25+25 ≈ naměřených 50 s |
| wall-clock | žádný | `asyncio.timeout(8)` | httpx timer je loop-scheduled |
| list retry | Bezrealitky 3× | **1** | 403/500 se neopraví stejným requestem |
| Ulovdomov fallback | POST 500 → HTML GET | **raise** | druhý request zdvojnásobil p95 na 140 s |

Historické úspěšné p95 (Sreality 0,2–0,7 s, Bazos 0,02–0,1 s) → 8 s je ~10×. Kratší by nic nezískalo, dokud loop 50 s nenechá timer doběhnout.

---

## 4. Before / after

### KPI

| KPI | Baseline | Wave 2b | W2.5 první | W2.5b (bug) | **W2.5c** | vs baseline |
|---|---|---|---|---|---|---|
| pages/s | 4,702 | 4,069 | 3,796 | 1,48 | **4,794** | **+2,0 %** |
| listings/s | 56,239 | 43,203 | 43,991 | 12,153 | **47,385** | −15,7 % |
| coverage cycle h | 0,40 | 0,39 | 0,96 | 4,26 | **0,37** | **lepší** |
| watchdog p95 ms | 2,2 | ~3 | 4,4 | 2,2 | 2,9 | stejný řád |
| watchdog max ms | 88 789 | 49 320 | 49 705 | 49 949 | **49 529** | pořád ~50 s |
| unique shards / 5 min | 230 | — | 80 | 18 | **200** | skoro baseline |

listings/s pořád pod baseline (Sreality 54,7 → 44,2). pages/s Sreality 4,197 → **4,211**. HTML portály pořád žerou sloty na 50s zombie, ale Sreality už za nimi ve frontě nečeká tolik, co ve Wave 2b — globální cap 24 + skip ulovdomov po trip.

### Fetch p95 / p99 u pěti podezřelých (ms)

| Portál | Baseline p95 | W2b p95 | W2.5 p95 (p99) | **W2.5c p95 (p99)** |
|---|---|---|---|---|
| idnes | 49 440 | 50 289 | 50 618 (50 650) | **50 429 (50 560)** |
| bezrealitky | 48 912 | 49 854 | 50 105 (50 129) | **50 043 (50 049)** — p50 úspěchu **1 207** |
| mmreality | 49 507 | 49 898 | 50 133 (50 202) | **49 866 (50 062)** — p50 **49 333** (skoro samé timeouty) |
| ulovdomov | **140 266** | **133 413** | 1 612 (**50 966**) | **49 763 (52 446)** |
| realitycz | 88 901 | 49 531 | 49 932 (49 946) | **51 594 (52 428)** |

Ulovdomov 140 s → 50 s = pryč POST+HTML řetězec. Zbylých 50 s = jeden zamrzlý pokus, ne dva. Cíl „jednotky sekund“ **nesplněn**.

---

## Závěr

1. Hypotéza „2× client timeout + retry + blokující sleep“ je **napůl pravda**:
   - `Timeout(25)` *mohl* skládat fáze na ~50 s — **opraveno** (5+8).
   - Bezrealitky 3× retry + sleep — **opraveno** (1 pokus, žádný sleep).
   - Ulovdomov POST→GET — **opraveno** (p95 140 s → 50 s).
   - **Blokující `time.sleep` v fetch NENÍ.** 50 s je **frozen event loop**; httpx i `asyncio.timeout` čekají, až loop zase tikne. Watchdog a attempt-error se kryjí na ms.
2. Circuit breaker je správný nástroj na 500 (ulovdomov), ale **mmreality/realitycz vrací 200**, takže je N po sobě jdoucích „hard fail“ nenakrmí. Timeouty jsou korelovaný stall, ne smrt jednoho portálu — proto je neshazujeme (jinak znovu vypneme Sreality, viz 2.5b).
3. pages/s **nad baseline** bez změny limiteru: přestat topit kapacitu do retry/fallback a do falešně disabled Sreality stačilo na +18 % vs Wave 2b. Zbytek 50s watchdogů limiterem nezmizí.

**Neiterovat `SCRAPE_CONCURRENCY_OVERRIDES`.** Další práce = najít, co drží loop ~50 s (discovery deadline, thread-pool/DNS, sync parse mimo `to_thread`, SQLite na loop threadu) — to je kvalitativně Wave 3, ne Wave 2.
