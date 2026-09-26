# Wave 4 — audit scrape regexů (ReDoS / GIL)

Datum: **2026-09-17**. Concurrency se v téhle vlně **neladí** (`SCRAPE_CONCURRENCY_OVERRIDES` prázdné, globální strop 16 / `*global*` 24). Bod 4 (16 vs 24 vs per-portál) až po tomto baseline.

| Artefakt | Cesta |
|---|---|
| Remax list HTML (265 318 B, stejný dokument co freeze-nul Wave 3) | `tests/fixtures/remax_list.html` |
| Hard-timeout testy | `tests/test_regex_safety.py` |
| Timing skript | `scripts/perf/regex_audit.py` |
| Nový 5min baseline | `scripts/perf/scrape_baseline_v2.json` (kopie `perf_audit/scrape_baseline_v2.json`) |
| Starý kontaminovaný baseline | `scripts/perf/scrape_baseline.json` (watchdog max **88 789 ms**) |
| Wave 3 po `parse_total` (3 min) | `scripts/perf/scrape_wave3_after.json` |

---

## 1. Inventář a měření

Projité soubory: `html_listing.py`, `sreality.py`, `idnes.py`, `bazos.py`, `bezrealitky.py`, `ceskereality.py`, `annonce.py`, `remax.py`, `mmreality.py`, `ulovdomov.py`, `realitycz.py`. Každý `re.compile` / `re.search` / `re.findall` / `re.sub`.

Syntetický „zlý“ vstup: ~55 kB `[\d\s]`, 50 kB samých `<`, 80 kB bez uzavíracího tagu (DOTALL `.*?`).

### 1.1 Katastrofické (stejná třída jako Remax `COUNT_RE`)

| Pattern | Před (ms) | Po (ms) | Proč |
|---|---:|---:|---|
| `idnes.PRICE_RE` `([\d\s]+)\s*Kč` | **23 717** | 6.9 | `[\d\s]+` se překrývá s `\s*` před `Kč` → backtracking. iDNES baseline fetch p95 49 440 ms **nebyl** tenhle regex (to byl Remax GIL, který maskoval loop); po Remax fixu by tenhle landmine čekal na první velký digit-heavy blob v `price_text` / fallback celého HTML. |
| `bazos.PRICE_RE` totéž | **24 004** | 6.9 | Stejný vzor. Bazos `parse_price` ho pouští na vyčištěný label — po `clean()` na velkém fragmentu to byl stejný GIL lock. |
| `html_listing._TAG_RE` `<[^>]+>` | **818** (50 kB `<`) | — (smazáno) | O(n²): každé `<` spolkne zbytek `[^>]+` a failne, protože `>` nikde není. |
| `html_listing.clean` / `strip_tags` | **1 723** / **811** | **0.0** | `_TAG_RE` + `re.sub(r"\s+", " ")`. Nahrazeno lineárním `str.find` + `" ".join(.split())`. |
| `html_listing.COUNT_RE` | opraveno ve Wave 3 | 1.4 | Kontrolní měření: drží se. Remax fixture `parse_total` **0.89 ms** (dřív 36 s na živém HTML). |

`html_listing.PRICE_RE` už byl bounded (`\d{1,3}(?:sep\d{3})+|\d{4,8}`) — 6.9 ms, ne katastrofa. iDNES/Bazoš teď **importují ten samý** pattern.

### 1.2 Naměřené OK (neexponenciální na 50–80 kB)

DOTALL lazy `.*?` u karet (`ceskereality`, `annonce`, `remax`, `bazos`, `idnes.ARTICLE_RE` / `RESULTS_RE` / `DESC_RE`, title regexy, `ulovdomov.__NEXT_DATA__`) — vesměs **0.2–1.7 ms**. Bez uzavíracího tagu je to lineární scan, ne overlapping character class.

`bezrealitky`: jediný regex `OSM_RE = ^R\d+$` na krátkém OSM id. GraphQL/JSON, žádný embedded-JS regex. **Bez nálezu.**

`sreality`: `AREA_RE` + path `\d+`; `__NEXT_DATA__` už je `str.find` + `json.loads`. **Bez nálezu.**

### 1.3 Co se opravilo (i když timing nebyl 50 s)

U každého scrape regexu s nebounded `+`/`*`/`{n,}` nad opakující se třídou: **horní mez kvantifikátoru**. Tam, kde šel parser místo NFA:

| Místo | Změna |
|---|---|
| `html_listing.strip_tags` | `str.find('<')` / `find('>')`, cap 80 kB |
| `html_listing.clean` | `str.split()`, žádné `\s+` |
| `html_listing.parse_price` | cap label 8 kB; bounded `PRICE_RE` |
| `html_listing.parse_total` | cap 32 kB (Wave 3) |
| `html_listing.first_img` / photo `findall` / og / lat-lon | compiled + `{1,500}` / `{1,4000}` |
| `idnes` + `bazos` `PRICE_RE` | sdílený bounded pattern z `html_listing` |
| `bazos.COUNT_RE` / `VIEWS_RE` | `[\d\s]{1,24}` / `{1,16}`, cap 32 kB před `COUNT_RE` |
| `idnes` CARD/DESC/ARTICLE/FANCY | bounded `[^>]{0,n}`, `.{0,n}?` |
| `ulovdomov.__NEXT_DATA__` | `str.find` + `json.loads` (jako Sreality) |
| karty ceskereality / annonce / remax / mm / realitycz | bounded `.*?` → `.{0,80000}?`, href/src `{1,400}` |
| `sreality.AREA_RE` | `\d{1,6}` |

---

## 2. Regresní test

`tests/test_regex_safety.py`:

- Auto-inventory **všech** `re.Pattern` v 11 scrape modulech (nový portál / nový `FOO_RE` spadne do sady sám).
- Každý pattern: `search` + `findall` na syntetickém zlém vstupu v **child process** (fork). Hard timeout **3 s** — in-thread timeout GIL-bound `re.search` nepřeruší.
- `strip_tags` / `clean` / `parse_total` / `parse_price` / `first_img` / iDNES+Bazos `_parse_total` stejně.
- Remax fixture: `parse_total` musí doběhnout **< 50 ms**.
- Canary: žádný `COUNT_RE`/`PRICE_RE` nesmí znovu obsahovat `([\d\s]+)`.

`python -m unittest tests.test_regex_safety tests.test_extra_portals tests.test_bazos tests.test_idnes_photos` — **OK**.

---

## 3. Baseline v2 (5 min, po všech regex opravách)

`SCRAPE_METRICS_DETAIL=1`, vlastní Hub v `scrape_bench` (souběžně s `make dev` — stejné jako Wave 0–3).

| | kontaminovaný `scrape_baseline.json` (5 min) | Wave 3 after (3 min, jen COUNT_RE) | **v2 (5 min, celý audit)** |
|---|---:|---:|---:|
| pages/s | 4.702 | 7.848 | **7.111** |
| listings/s | 56.239 | 102.31 | **78.467** |
| watchdog max | **88 789 ms** | 62.3 ms | **83.7 ms** |
| watchdog p95 | 2.2 ms | 3.0 ms | **2.0 ms** |
| over 50 / 500 / 2000 ms | 3 / 2 / 2 | 0 / 0 / 0 | **0 / 0 / 0** |
| unique shards / 5 min | 230 | 254 / 3 min | **431** |

Listings/s je níž než Wave 3 after hlavně mixem okna (5 vs 3 min), Sreality 5.53 vs 7.23 pages/s a UlovDomov **error_rate 1.0** (API 500 → circuit breaker). To **není** regresi regexů: parse p95 HTML portálů je jednotky ms (Remax **6.96 ms**, Bazos **1.84 ms**, Annonce **6.14 ms**).

### iDNES — už to není 49 s fetch

| | starý baseline | v2 |
|---|---:|---:|
| fetch_ms p95 | **49 440** | **5 010** (HTTP connect/timeout 5 s, ne parse) |
| parse_ms p50 / p95 | 0.0 / 4.81 | 0.0 / **2 318** |
| pages / deferred | 27 / 20 | 130 / 102 |
| error_rate | 0 | 0 |

p50=0 a 102 deferred řádků natahují p95: `parse_ms` se měří přes `asyncio.to_thread`, takže čekání na **volný worker** v default executor se počítá jako parse. Stall dump během v2 viděl dlouhé `freeze=1` stacky v `places._nominatim_locality_sync` na threadech `asyncio_N` — to je **už** `to_thread` + `time.sleep(1.1)` rate limit + sync `httpx` (GIL pouští). Event loop **nezamrzl**.

### Watchdog / asyncio debug v okně v2

V `perf_audit/loop_stall/asyncio.log` po startu v2 (21:33–21:41) jen:

- `_amain` 0.121 s
- `_amain` 0.183 s (konec bench, header probe)

Žádný `gated() took 49s`. Žádný outlier **> 500 ms**. 83.7 ms max je šum pod prahem, který měl spustit Wave 3 postup.

Nominatim stack v dump threadu **není** nový GIL-ReDoS. Follow-up mimo tuhle vlnu: geocode rate-limit žere thread pool a umí nafouknout `parse_ms` iDNES. Neřešit concurrency, dokud tohle případně nepůjde do vlastního executor / cache.

---

## 4. Concurrency — ne teď

Wave 2 čísla byla proti baseline s 50s Remax freeze. `scrape_baseline_v2.json` je první čisté 5min číslo (watchdog max desítky ms, ne desítky sekund). 16 vs 24 vs per-portál override až v samostatném reportu na tomto souboru.

Cíl beze změny: Sreality (~89 % listings/s v2: 69.9 / 78.5) nemá čekat za HTML portály; žádný portál nesmí zhoršit error rate. UlovDomov 500 a Reality.cz 0.26 error_rate jsou síť/markup, ne regex.
