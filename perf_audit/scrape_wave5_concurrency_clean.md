# Wave 5A — concurrency na čistém `scrape_baseline_v2.json`

Datum: **2026-09-17**. Gate: `scripts/perf/scrape_baseline_v2.json` (pages/s **7.111**, listings/s **78.467**, watchdog max **83.7 ms**). `SCRAPE_CONCURRENCY=16`, žádný regex freeze. Vedle `make dev`. 5 min, `--skip-headers`.

| Run | Env | JSON |
|---|---|---|
| v2 (reference) | portal 16, global **24** | `scripts/perf/scrape_baseline_v2.json` |
| g16 | `SCRAPE_GLOBAL_CONCURRENCY=16` | `scripts/perf/scrape_wave5_g16.json` |
| g24 | `SCRAPE_GLOBAL_CONCURRENCY=24` (replika v2) | `scripts/perf/scrape_wave5_g24.json` |
| uncap | `SCRAPE_GLOBAL_CONCURRENCY=128` | `scripts/perf/scrape_wave5_uncap.json` |
| overrides | global 24, HTML 2–4, Sreality 16 | `scripts/perf/scrape_wave5_overrides.json` |

Overrides: `sreality=16 idnes/bazos/bezrealitky=4 remax/annonce/ceskereality=3 mmreality/realitycz/ulovdomov=2`.

---

## KPI vs v2

| | v2 | g16 | g24 | uncap 128 | overrides |
|---|---:|---:|---:|---:|---:|
| pages/s | **7.111** | 4.819 **−32 %** | 4.029 **−43 %** | 5.550 **−22 %** | **7.613 +7,1 %** |
| listings/s | **78.467** | 52.202 **−34 %** | 44.880 **−43 %** | 58.413 **−26 %** | **90.655 +15,5 %** |
| sreality pages/s | **5.534** | 3.987 −28 % | 3.336 −40 % | 4.461 −19 % | **6.209 +12 %** |
| sreality listings/s | **69.905** | 48.715 | 42.128 | 55.028 | **80.236** |
| sreality error_rate | 0 | 0 | 0 | 0 | 0 |
| watchdog max / p95 | 83.7 / 2.0 | 155.7 / 2.0 | 78.7 / 2.0 | 170.7 / 2.2 | 74.2 / 5.4 |
| over_500ms | 0 | 0 | 0 | 0 | 0 |

Žádný běh znovu nezamrzl loop (max 171 ms). To je ten rozdíl proti Wave 2, kde max byl 49–88 s.

---

## Per portál (pages/s, error_rate)

| Portál | v2 | g16 | g24 | uncap | ovr | error v2 → ovr |
|---|---:|---:|---:|---:|---:|---|
| sreality | 5.534 | 3.987 | 3.336 | 4.461 | **6.209** | 0 → 0 |
| bezrealitky | 0.746 | 0.079 | 0.189 | 0.251 | 0.837 | 0 → 0 |
| idnes | 0.275 | 0.337 | 0.203 | 0.346 | 0.287 | 0 → 0 |
| bazos | 0.099 | 0.084 | 0.059 | 0.103 | 0.068 | 0 → 0 |
| mmreality | 0.169 | 0.084 | 0.061 | 0.103 | 0.068 | 0 → 0 |
| realitycz | 0.169 | 0.084 | 0.061 | 0.103 | 0.068 | 0.26 → **0** |
| ulovdomov | 0.017 | 0.028 | 0.020 | 0.027 | 0.017 | **1.0 → 1.0** |
| remax / annonce / ceske | ~0.034 | ~0.045 | ~0.033 | 0.044–0.068 | **0.019** | 0 → 0 |

UlovDomov 500 a Reality.cz maintenance se vícem concurrency **nespraví**. Overrides jim sebraly sloty, Sreality je využila.

---

## Verdikt: **neměnit globální strop; nechat 16 / 24**

Čísla, která to zdůvodňují:

1. **Zvednout strop nepomůže.** g24 (stejný config jako v2) byl **−43 %** pages/s — večerní Sreality fetch p95 5 009 ms (timeout) vs 741 ms ve v2. uncap 128 **−22 %** vs v2. g16 **−32 %** a Sreality seškrtí (−28 %). Variance sítě je větší než efekt 16 vs 24 vs 128.
2. **24 ani 128 nezlepšily pages/s o víc než pár % oproti 16** v tomhle okně — g24 bylo horší než g16 (4.03 vs 4.82). Zvedání globálu je špatná páka.
3. Overrides HTML 2–4 daly **+7,1 % pages/s / +15,5 % listings/s** vs v2 a Sreality **+12 %** pages/s, error_rate žádného portálu nestoupla. To je jediný směr, který sedí na cíl „Sreality nemá čekat za HTML“. **+7 % je v řádu večerní variance** (g24 replica −43 % na identickém configu), takže to **nezapečítávám do defaultu**. Nechat `SCRAPE_CONCURRENCY=16`, `SCRAPE_GLOBAL_CONCURRENCY=24`, overrides prázdné. Kdo chce vyzkoušet HTML throttle, env z tabulky výše.

**Závěr série:** pomalý scraping nebyl o počtu paralelních requestů. Byl o N+1 upsertu a katastrofickém regexu. Škálování concurrency ty dvě chyby nemohlo opravit a na čistém baseline je strop nad 16 šum.

Wave 2 „bez stropu zničilo throughput“ byl artefakt 50s GIL freeze + timeout fan-out, ne důkaz, že 24 je málo.
