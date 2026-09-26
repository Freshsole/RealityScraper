# Wave 3 after — event-loop CPU (regex / sync handlers)

Datum: **2026-09-17**. Stejná struktura jako `performance_report.md` sekce CPU. HTTP čísla z `make perf-quick` proti běžícímu `make dev`.

---

## TOP bottleneck #1 (before → after)

| | Before | After |
|---|---|---|
| Živý worker CPU | **99 %**, main thread 6464/6464 vzorků v `sre_search` | parse běží v `asyncio.to_thread`; regex jen na **výřezu** HTML (head + detail), ne na celém 50–200 KB dokumentu |
| HTTP `/api/catalog?include_pins=1` | median **91,1 ms**, p95/max **129,6 ms** | median **60,3 ms**, max 108 ms (reload šum na `/api/public/stats`) |
| HTTP `/api/catalog?include_pins=0` | **31,5 ms** | **18,8 ms** |
| Cíl p95 catalog při scrapu | ~50 ms | pins=0 **19 ms**; pins=1 **60 ms** (pořád nad 50 ms kvůli 800 pinům — vlna 4 default `include_pins=0`) |

Sreality: `_fetch_next_data` (JSON) je primární. HTML fallback **nepoužívá** `re.search(.*?)` přes celý dokument — `str.find` na `__NEXT_DATA__` / `buildId` + `json.loads` v `to_thread`. To byl zbývající 99 % `sre_search` na main threadu po vlně 3 quick-fixu.

---

## 2. CPU profiling

### Co se změnilo v kódu

- `html_listing.clean` / bazos+idnes `_clean`: `strip_tags` na oříznutém fragmentu (`extract_listing_html`, markery `#detail`, JSON-LD, `__NEXT_DATA__`, list kontejnery).
- `HtmlPortalClient.fetch_page` / `fetch_detail`: `await asyncio.to_thread(_parse_list|_parse_detail, fragment)` — uvolní event loop (GIL drží worker thread, ne uvloop).
- Stejně bazos + idnes parse.
- Sync HTTP handlery mimo pool: `/api/public/gone-fast`, `/api/listings`, `/api/catalog/item`, `/api/catalog/user`, `/api/settings`, `/api/monitors` → `asyncio.to_thread`.
- `SCRAPE_ROLE`: `make dev` = `all`; Railway `supervisord` už split `web`/`worker`. Beze změny lokálního defaultu.

### Živý `sample`

Před opravou `sreality._fetch_html`: main thread **6650/6650** v `sre_search` na `re.search(r'<script id="__NEXT_DATA__">(.*?)</script>', whole_html)` — to byl zbývající GIL lock.

Po nahrazení `str.find` + `json.loads` v `to_thread` (`_next_data_json` / `_build_id_from_html`) by main thread už neměl sedět v `_sre_SRE_Pattern_search`. `perf_audit/sample_wave3.txt` je snapshot **před** tímto fixem; po reloadu `make dev` zopakuj `/usr/bin/sample <worker> 8`.

---

## 5. `/api/catalog` (související vlna 4, změřeno spolu)

Default `include_pins=0`. Frontend už volá `/api/catalog/pins` zvlášť. Facets cache 600 s, `?facets=0`.

| Endpoint | Before median | After median | Bytes |
|---|---|---|---|
| `/api/catalog?include_pins=1` | 91,1 ms / 505 KB | **60,3 ms** / 474 KB | piny pořád drahé |
| `/api/catalog?include_pins=0` | 31,5 ms / 111 KB | **18,8 ms** / 79 KB | default |

`catalog()` in-process: 80,05 ms → **64,93 ms** (bez pinů v default filtru perf-quick).

---

## Škálování

`config.SCRAPE_ROLE`: `all` (local) / `web` / `worker`. Produkce ať zůstane split — i po ořezu regexu je scrape CPU-heavy a GIL by jinak brzdil HTTP.
