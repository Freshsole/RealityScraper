# Listing coverage: M&M Cloudflare + UlovDomov

Measured 2026-09-16 from a datacenter IP (this Cloud Agent). InstantSiteASGI / `/hry*` is unchanged — browser/JA3 code is not on that path.

## UlovDomov (yield)

| Path | Result |
|---|---|
| `POST ud.api.ulovdomov.cz/v1/offer/find` | **500** `udBe.internalServerError` (nationwide + Praha bounds, empty body) |
| `POST /v2/offer/find` | 400/422/500 depending on `parameters` shape; rent+bounds still **500** |
| `GET /v2/offer/latest` | 400/500 |
| `GET /v2/offer/detail?offerId=` | **200** — worker hydrate only (price, photos, geo). Never on `/hry*` / InstantSiteASGI. |
| HTML `/pronajem/byty` | 200, 94 KB, **0** listing hrefs; `__NEXT_DATA__` has `count` only |
| `/_next/data/{buildId}/pronajem/byty.json` | 200, `count: 3293`, **0** offers |
| `GET /sitemap-offers.xml` | **200**, 7635 `<loc>` (3295 `pronajem-*`, 47 coliving, ~4293 sale / leading-dash slugs) |

Worker now: API → sitemap cards (cached 8 min, newest-by-id pages of 20) → HTML/`_next/data` only if the sitemap fetch is not valid XML. Empty offer shard after a good sitemap returns `[]` without cooling the portal. Games read those hydrated rentals from memory when a locality has two priced cards; seed stays the InstantSiteASGI cold fallback.

Fixture yield: 2 rent + 1 sale cards from `tests/fixtures/ulov_sitemap_offers.xml` after a mocked 500.

Live `scripts/measure_listing_yield.py` (this agent, 2026-09-16):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| UlovDomov rent | **20** | **3295** | 1.9 s (API 500 + 1.1 MB sitemap) | later pages hit the 8 min cache |
| UlovDomov sale | **20** | **4293** | cached | leading-dash sitemap slugs |
| M&M Reality | **0** | 0 | 105 ms | `blocked:cloudflare:403` hard — no fake listings |

Worker hydrate (`SCRAPE_ROLE!=web`, off InstantSiteASGI / `/hry*`): newest unpriced Ulov catalog rows (fallback: newest sitemap cards), `GET /v2/offer/detail?offerId=` batched (default 20, concurrency 4, 0.12 s spacing, 15 s deadline, fail-fast on 403/429 / 2 consecutive errors). `fetch_page` never calls detail. Thin sitemap upserts do not wipe a hydrated price/photo on `catalog_listings` or on the `listings` row the catalog/map reads. List/detail overlay missing price/image/GPS from `catalog_listings` so a hydrate is visible on the next read.

Live hydrate of the same page-1 rent cards (this agent, 2026-09-16):

| attempted | priced | imaged | gone | failed | success | time |
|---|---|---|---|---|---|---|
| **20** | **19** | **20** | 0 | 0 | **95%** | 3.2 s |

Sample after detail: Olomouc 2+kk 17 900 Kč, Praha-Komořany 18 500 Kč, Sokolov 1+1 7 500 Kč — all with photos. One card had photos but no numeric rent (not invented). Sitemap list yield stayed 20/3295.

Limit: M&M still has no listings from this datacenter IP. Residential-proxy follow-up unchanged.

## Healthy-portal NewDiscovery throughput

Goal: more freshest listings/min from portals that already return cards (Sreality, iDNES, Bazoš, Bezrealitky, Annonce, RE/MAX, ČeskéReality, Reality.cz, Ulov sitemap+hydrate). InstantSiteASGI `/hry*`, WAL stale readers, live-catalog games, and worker-only Ulov hydrate are unchanged. No M&M residential proxy in this slice.

Shipped:

- Persist Hub `_discovery_engine` / `_deep_engine` so `SCRAPE_RECENT_PAGES` deferred leftovers actually run next minute.
- `prepare_discovery_shards`: nationwide extras first, skip portals already on per-portal cooldown (M&M still retried after cooldown — not permanently disabled).
- `fetch_shards(page1_across=True)`: page 1 of every ready shard before pages 2..N, so a slow HTML shard cannot hold the shard gate through 4 pages and starve later page-1s.
- Ulov `sitemap-offers.xml` single-flight + warmup once per discovery tick (rent+sale share the 8 min cache).
- Rolling deep yields while NewDiscovery is in-flight (limiter priority was not enough for work already started).
- Catalog upsert busy: keep fetched listings in `_pending_discovery` for the next tick instead of dropping them.

Measure: `scripts/measure_discovery_tick.py` (synthetic held-gate vs page-1-across; `--live` for healthy page-1 probes). M&M proxy remain the documented follow-up below.

Synthetic 42-shard minute (16 conc, extras 300 ms / Sreality 40 ms, 4 pages, **0.7 s** deadline — the contention case when deep/HTML eats the gate):

| scheduler | page-1 shards | listings | pages_ok | tick |
|---|---|---|---|---|
| held-gate (before) | **21** | **900** | 69 | 1.25 s |
| page-1-across (after) | **42** | **2100** | 168 | 1.49 s |

Unconstrained 50 s deadline: both finish 42 / 2100 in ~1.5 s. The gain is page-1 completeness when time is short.

Live page-1 from this datacenter (2026-09-17, 12 s cap, worker clients, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time |
|---|---|---|---|
| Sreality (one size shard) | **20** | 1061 | 1.9 s |
| Bazoš | **20** | 5627 | 0.5 s |
| Bezrealitky | **15** | 2278 | 1.1 s |
| UlovDomov sitemap | **20** | 3295 | 1.8 s |
| Reality.cz | **24** | 841 | 1.6 s |
| RE/MAX | **20** | 20 | list ok |
| Annonce | **10** | 10 | 0.3 s |
| iDNES | 0 | 0 | timeout 12 s |
| ČeskéReality | 0 | 0 | 0.6 s empty (no block raised) |

## M&M Reality (documented limit)

| Client | `GET /nemovitosti/?typ-nabidky=pronajem…` |
|---|---|
| httpx Chrome UA | **403** CF hard-block (`Sorry, you have been blocked`) |
| curl_cffi `chrome` / `chrome131` / `safari` | **403** same page |
| `google-chrome --headless=new --dump-dom` | **403** same page |
| `robots.txt` | 200 (sitemap URL advertised, sitemap itself 403) |

This is an IP/WAF hard-block, not a JS “Just a moment” challenge. A Playwright path on the same IP will not produce listings.

Shipped incremental:

- Stronger CF classify (`hard` vs `challenge`, extra hints).
- Portal cooldown already per-portal; **blocked shards no longer defer pages 2..N**.
- Opt-in `SCRAPE_BROWSER_FETCH=1` **only** when `SCRAPE_ROLE` is `worker` or local `all` — never `web`. Default skips hard-blocks (`SCRAPE_BROWSER_ON_HARD_CF=0`) so Chrome is not launched for a known WAF deny.
- Soft backends: `curl_cffi` → Playwright → system Chrome, each under `SCRAPE_BROWSER_TIMEOUT_SEC` (12s).
- UlovDomov worker hydrate: `v2/offer/detail` for newest unpriced cards (batch 20, fail-fast). Off on `SCRAPE_ROLE=web`. Opt out with `SCRAPE_ULOV_HYDRATE=0`.

### Follow-up (exact)

1. Run the scrape worker with a **residential / ISP proxy** (not this datacenter egress).
2. Persistent Playwright Chromium context on `SCRAPE_ROLE=worker` only, `SCRAPE_BROWSER_FETCH=1`, hard timeout 12s, one probe per cooldown window.
3. If the proxy gets a **challenge** (not hard-block), keep cookies and reuse the context for list pages; parse existing JSON-LD / card HTML.
4. Do not add Playwright or Chrome to the web dyno. Do not import `app.browser_fetch` from `app.site_pages`.
5. Re-measure list yield (`scripts/measure_listing_yield.py`) after proxy is in place before making the flag default-on.
