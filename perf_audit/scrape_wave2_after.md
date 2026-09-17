# Wave 2 after — per-portál AdaptiveLimiter

Datum: **2026-09-17**. Baseline: `scripts/perf/scrape_baseline.json` (416 s). Stejné prostředí: `data/monitor.sqlite`, tři pipeline, `SCRAPE_METRICS_DETAIL=1`, vedle běžícího `make dev`.

Změna: `Hub._scrape_limiter` → `LimiterRegistry` (`Dict[portal, AdaptiveLimiter]`). Priorita 0/1/2 zůstává, ale fronta je per portál. `SCRAPE_CONCURRENCY_OVERRIDES` (JSON env) nastavuje strop; default 16.

---

## Pokus 1 — bez globálního stropu → **FAIL** (gate)

`scripts/perf/scrape_wave2.json`, wall **315,5 s**.

| KPI | Baseline | Wave 2a | Δ |
|---|---|---|---|
| pages/s | **4,702** | **3,610** | **−23,2 %** |
| listings/s | **56,239** | **33,809** | **−39,9 %** |
| coverage cycle | **0,40 h** | **0,47 h** | **+17,5 %** (horší) |
| watchdog p95 | 2,2 ms | 3,0 ms | +36 % |
| watchdog max | 88 789 ms | 64 026 ms | lepší max, ale `over_2000ms` 2 → **5** |
| 403 / 429 | 0 / 0 | 0 / 0 | stejné |

### Per portál (pages/s, error_rate z logu)

| Portál | pages/s before | after | error_rate before | after |
|---|---|---|---|---|
| sreality | 4,197 | **3,157** | 0 | 0 |
| bazos | 0,067 | 0,038 | 0 | 0 |
| bezrealitky | 0,067 | 0,063 | **0,64** | **0,15** |
| idnes | 0,065 | 0,060 | 0 | 0 |
| mmreality | 0,067 | 0,063 | 0 | 0 |
| ulovdomov | 0,067 | 0,063 | 0 | 0 |
| ceskereality | 0,036 | 0,044 | 0 | 0 |
| annonce | 0,034 | 0,029 | 0 | 0 |
| remax | 0,034 | 0,032 | 0 | 0 |
| realitycz | 0,067 | 0,060 | 0 | 0 |

Sreality = pořád ~87 % stránek (996 / 1139). Ostatní portály **nezrychlily** — pořád 9–20 stránek za okno, fetch p95 28–114 s (timeouty). Deep pořád jen Sreality.

Živý `ScrapeMetrics` během běhu hlásil `error_rate_5m` 0,4–1,0 na HTML portálech a krátce i na Sreality (JSON fallback storm na začátku). Do `scrape_metrics_log` se to jako `fail` skoro nepropsalo (error_rate v JSON ≈ 0) — okno limiteru počítá i výjimky, aggregát logu jen status_code.

### Proč to spadlo

`_shard_fanout` = součet stropů portálů v ticku → až **160** souběžných shard tasků. 10 HTML portálů × 16 slotů + Sreality 16. Timeouty 50–114 s obsadily loop (watchdog 64 s). Sreality na startu padala do `__NEXT_DATA__` fallbacku. Pozdější `rolling_deep` ticky byly naopak rychlé (0,7–1,5 s) — až po opadnutí timeout vlny.

Izolace 429 **funguje v testech** (`test_429_on_one_portal_does_not_shrink_another`). V 5min okně žádný 429 nebyl, takže ten benefit se nenašel. Throughput teze („portály bez 429 zrychlí“) se **nepotvrdila**, protože bottleneck nebyl strop 16, ale **deadline + pomalé HTML + unbounded fan-out**.

**Gate:** nesmí se zhoršit error rate ani pages/s. pages/s a listings/s a coverage **selhaly**. Wave 3+ nestartuju.

---

## Pokus 2 — globální strop 24 → pořád pod baseline (gate ne)

`scripts/perf/scrape_wave2b.json`, wall **317,5 s**. `SCRAPE_GLOBAL_CONCURRENCY=24`.

| KPI | Baseline | Wave 2a | Wave 2b | vs baseline |
|---|---|---|---|---|
| pages/s | **4,702** | 3,610 | **4,069** | **−13,5 %** |
| listings/s | **56,239** | 33,809 | **43,203** | **−23,2 %** |
| coverage cycle | 0,40 h | 0,47 h | **0,39 h** | −2,5 % (lepší) |
| watchdog p50 / p95 / max | 1,7 / 2,2 / 88 789 | 1,1 / 3,0 / 64 026 | **1,8 / 2,2 / 49 320** | p95 stejné, max lepší |
| watchdog over_2000ms | 2 | 5 | **3** | horší než baseline |
| deferred rows | 254 | 230 | **133** | méně |
| 403 / 429 | 0 / 0 | 0 / 0 | 0 / 0 | |

### Per portál (2b vs baseline)

| Portál | pages/s Δ | listings/s Δ | error_rate | parse p95 |
|---|---|---|---|---|
| sreality | 4,197 → **3,600 (−14 %)** | 54,7 → 41,3 (−24 %) | 0 → 0,0017 | 6,9 → 5,8 |
| bazos | 0,067 → 0,063 | 0,96 → **1,26 (+31 %)** | 0 | 5,3 → 4,7 |
| bezrealitky | 0,067 → 0,063 | 0 | **0,64 → 0,85** | — |
| ceskereality | 0,036 → **0,044 (+22 %)** | +30 % | 0 | 838 → 432 |
| idnes | 0,065 → 0,060 | +4,5 % | 0 | 4,8 → 362 (šum málovzorků) |
| mmreality / realitycz / ulovdomov | ~−6 % pages/s | 0 listings | 0 | mix |
| annonce | −35 % pages/s | +9 % listings/s | 0 | 12 → 12 |
| remax | −18 % | +7 % | 0 | ~49 s (parse=wall u timeoutu) |

Deep unique shards 180 / 317 s → cyklus **0,39 h** (hlavní KPI skoro baseline). Sreality pořád ~88 % stránek.

Bezrealitky error_rate **zhoršená** — gate „žádný portál nesmí zhoršit error rate“ **fail**.

Watchdog p95 splňuje cíl desítek ms; ocas 49 s pořád nad 2 s → Wave 3 by dávala smysl, ale **nespouštím ji**, dokud Wave 2 neprojde throughput gatem.

---

## Verdikt

Per-portál limiter je správná izolace na 429 (testy to drží). Na dnešním stagingu **žádné 429 nejsou**; bottleneck je timeout HTML portálů + Sreality JSON fallback, ne sdílená fronta. Unbounded fan-out (pokus 1) throughput zničil. Globální strop 24 vrátil coverage na baseline, pages/s a listings/s ne.

**Wave 3 / 4 / 5 nezačínám.** Další krok Wave 2, až se pages/s vrátí ≥ baseline a bezrealitky error_rate ≤ 0,64 — kandidát: `SCRAPE_CONCURRENCY_OVERRIDES` pro HTML portály na 2–4, ne další zvedání globálu.

