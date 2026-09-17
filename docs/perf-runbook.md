# Perf runbook — nejdřív stack, až pak strop

Tahle série (Wave 0–5) třikrát ukázala totéž: **watchdog / fetch p95 vypadal jako síť nebo rate-limit**, dokud dump nenašel jinou třídu chyby (SQLite N+1, CPython `re` GIL / ReDoS, Nominatim `sleep` ve sdíleném `to_thread` poolu).

## Kanonická reference

`scripts/perf/scrape_baseline_v2.json` (kopie `perf_audit/scrape_baseline_v2.json`) je **trvalá** 5min reference po opravě Remax/iDNES/Bazos regexů. Nesmazat po sprintu. Budoucí regrese měřit proti ní, ne proti `scrape_baseline.json` (ten má vestavěný 50s Remax freeze).

## Když scrape „vypadá pomalu“

1. **Nejdřív** asyncio slow-callback log + stack dump, **až potom** concurrency / timeout / rate-limit.
2. Spusť `scrape_bench.py --minutes 5 --out scripts/perf/last.json --compare scripts/perf/scrape_baseline_v2.json`.
   Bench sám zapne `slow_callback_duration=0.1` a 1s `sys._current_frames()` (`scripts/perf/stall_dump.py`).
3. Čti:
   - `perf_audit/loop_stall/asyncio.log` — `took N seconds` na `gated()` / parse
   - `perf_audit/loop_stall/stacks.txt` — repeating frame = freeze
   - watchdog v JSON: `max_ms`, `over_500ms`, `over_2000ms`
4. Interpretace:
   - `over_500ms` + stejný Python frame na main threadu → **CPU/GIL** (regex, sync parse, SQLite na loopu). `to_thread` **nepomůže**, pokud práce drží GIL.
   - fetch p95 ≈ `SCRAPE_HTTP_CONNECT_TIMEOUT` (5 s) nebo `SCRAPE_HTTP_TIMEOUT` (8 s) a parse p95 jednotky ms → **opravdu síť/timeout**.
   - parse p95 >> parse p50, watchdog v desítkách ms → fronta na **thread pool** (geocode/Nominatim), ne parse.
5. Teprve když dump ukáže čekání na limiter/HTTP a loop je zdravý, laď `SCRAPE_CONCURRENCY` / `SCRAPE_CONCURRENCY_OVERRIDES` / `SCRAPE_GLOBAL_CONCURRENCY`.

py-spy na macOS potřebuje sudo; `stall_dump.py` stačí bez něj. GIL-bound `re.search` nejde přerušit in-thread — timeout testů musí jít přes proces (`tests/test_regex_safety.py`).

## Pre-merge checklist

```bash
make ci          # py_compile + unittest (včetně test_regex_safety) + perf-quick
```

- `tests/test_regex_safety.py` — ReDoS pojistka; padne, když regex na 50 kB zlém vstupu drží > 3 s.
- `make perf-quick` — catalog/upsert/SQL smoke; HTTP část se přeskočí, pokud neběží `make dev`.
- CI: `.github/workflows/ci.yml` spouští totéž.

Nový portál / nový `FOO_RE` spadne do inventory v `test_regex_safety.py` sám (modulový `re.Pattern`). Neomezený `[\d\s]+` před `\s*` / `Kč` / `inzerát` je zakázaný vzor — viz Wave 3–4.
