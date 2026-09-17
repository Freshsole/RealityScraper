# Listing coverage: M&M Cloudflare + UlovDomov

Measured 2026-09-16 from a datacenter IP (this Cloud Agent). InstantSiteASGI / `/hry*` is unchanged — browser/JA3 code is not on that path.

## Web vs worker (`SCRAPE_ROLE`)

InstantSite TTFB stays off the scrape writer by process split, not just timeouts.

| Role | How it starts | Owns |
|---|---|---|
| `web` | `Procfile` / supervisord `web`: `uvicorn app.asgi:app` (`app/asgi.py` forces `SCRAPE_ROLE=web`) | InstantSite HTML/JSON, games `/hry*`, dashboard shells, API. Digest + Discord ping dequeue. **Never** constructs `ScrapeEngine`, never schedules NewDiscovery / rolling-deep / Ulov hydrate / catalog / dedupe / sold ticks. Admin scrape/dedupe buttons queue meta for the worker. SQLite writer timeout 800 ms. |
| `worker` | `Procfile` / supervisord `worker`: `python -m app.scrape_worker` (`main()` forces `SCRAPE_ROLE=worker`) | NewDiscovery, rolling deep, Ulov hydrate, denní katalog, pozemky shards, M&M `SCRAPE_HTTP_PROXY` / `SCRAPE_BROWSER_FETCH`. Consumes `catalog_sync_request`, `scrape_url_request`, `dedupe_request`. |
| `all` | Local `uvicorn app.main:app` (default) | Both in one process. Do not use `app.asgi:app` for this. |

Do not point the web dyno at `app.main:app` without `SCRAPE_ROLE=web` — that default `all` would start scrape engines next to InstantSite. This slice does not require a residential proxy.

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

Fixture yield: 2 rent flats + 1 sale + 1 rent house + 1 sale villa from `tests/fixtures/ulov_sitemap_offers.xml` after a mocked 500.

Live `scripts/measure_listing_yield.py` (this agent, 2026-09-16):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| UlovDomov rent | **20** | **3295** | 1.9 s (API 500 + 1.1 MB sitemap) | later pages hit the 8 min cache |
| UlovDomov sale | **20** | **4293** | cached | leading-dash sitemap slugs |
| M&M Reality | **0** | 0 | 105 ms | `blocked:cloudflare:403` hard — no fake listings |

Worker hydrate (`SCRAPE_ROLE!=web`, off InstantSiteASGI / `/hry*`): newest unpriced Ulov catalog rows mixed with newest sitemap stubs (rent + sale + houses, unpriced first), `GET /v2/offer/detail?offerId=` batched (default 32, concurrency 6, 0.12 s spacing, 18 s deadline, fail-fast on 403/429 / 2 consecutive errors). `fetch_page` never calls detail. Thin sitemap upserts do not wipe a hydrated price/photo on `catalog_listings` or on the `listings` row the catalog/map reads. List/detail overlay missing price/image/GPS from `catalog_listings` so a hydrate is visible on the next read.

Live hydrate of the same page-1 rent cards (this agent, 2026-09-16):

| attempted | priced | imaged | gone | failed | success | time |
|---|---|---|---|---|---|---|
| **20** | **19** | **20** | 0 | 0 | **95%** | 3.2 s |

Sample after detail: Olomouc 2+kk 17 900 Kč, Praha-Komořany 18 500 Kč, Sokolov 1+1 7 500 Kč — all with photos. One card had photos but no numeric rent (not invented). Sitemap list yield stayed 20/3295.

Limit: M&M still has no listings from this datacenter IP without a live residential proxy. Worker-only `SCRAPE_HTTP_PROXY` plumbing is in this slice (mocked tests; live yield only if a proxy is actually configured).

## Healthy-portal NewDiscovery throughput

Goal: more freshest listings/min from portals that already return cards (Sreality, iDNES, Bazoš, Bezrealitky, Annonce, RE/MAX, ČeskéReality, Reality.cz, Ulov sitemap+hydrate). InstantSiteASGI `/hry*`, WAL stale readers, live-catalog games, and worker-only Ulov hydrate are unchanged. M&M list fetch may use opt-in `SCRAPE_HTTP_PROXY` on the worker; without a proxy it stays 0 cards + cooldown.

Shipped:

- Persist Hub `_discovery_engine` / `_deep_engine` so `SCRAPE_RECENT_PAGES` deferred leftovers actually run next minute.
- `prepare_discovery_shards`: nationwide extras first, skip portals already on per-portal cooldown (M&M still retried after cooldown — not permanently disabled).
- `fetch_shards(page1_across=True)`: page 1 of every ready shard before pages 2..N, so a slow HTML shard cannot hold the shard gate through 4 pages and starve later page-1s.
- Ulov `sitemap-offers.xml` single-flight + warmup once per discovery tick (rent+sale share the 8 min cache).
- Rolling deep yields while NewDiscovery is in-flight (limiter priority was not enough for work already started).
- Catalog upsert busy: keep fetched listings in `_pending_discovery` for the next tick instead of dropping them.

Measure: `scripts/measure_discovery_tick.py` (synthetic held-gate vs page-1-across; `--live` for healthy page-1 probes). M&M proxy enablement is documented below.

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
| Annonce | **10** | 10 | 0.3 s | `CARD_RE` ate the next card opener (20 live boxes → 10); empty locality / no GPS |
| iDNES | 0 | 0 | timeout 12 s — list HTML was fine (~26 cards / 1.3 s); `fetch_page` then geocoded every locality via Photon/Nominatim |
| ČeskéReality | 0 | 0 | 0.6 s empty in PR #13; live `/nejnovejsi/` now ships cards without `id-nemovitosti` (IDs live in `*.html`) |

After PR #14 (same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time |
|---|---|---|---|
| iDNES | **26** | **8519** | 1.3 s |
| ČeskéReality | **20** | **4782** | 0.9 s |

After this branch (Annonce parser + `nabidkovy=1` + local city pins, same 12 s cap):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| Annonce rent | **20** | **20** | 0.4 s | was **10** / 10 / 0.3 s; 20/20 locality, 19/20 city-center coords |
| Annonce houses | **24** | **24** | 0.3 s | new newest shard; 24/24 locality, 22/24 price, 24/24 city pins |
| Annonce bare `/byty-k-pronajmu.html` | **19** | 19 | 0.3 s | one `Poptávka` skipped |

iDNES list fetch uses local city pins only (same as Bazoš). ČeskéReality prefers `-NNNNN.html` IDs over firm image folders, keeps `.html` hrefs even when the path contains `nejnovejsi/`, and retries nationwide `/byty/` if `/nejnovejsi/` is empty. Annonce no longer skips every other slideshow card, defaults to offer-only list URLs, paginates with `?page=`, and HTML list clients pin local city centers (no Photon on page-1). Catalog writes still clear `_city_pin_cache` and now also drop landing / new-today snapshots; `_hot_json_cache` stays as the WAL-busy fallback. Synthetic page-1-across with two extra Annonce house shards: **21→44** page-1 shards / **900→2200** listings under a 0.7 s contention deadline (was 42/2100 before the house shards). InstantSiteASGI `/hry*`, WAL readers, games, Ulov hydrate, and the scheduler are unchanged.

After this branch (Reality.cz newest/`g=` pagination + house shards + Bezrealitky `limit: 20`, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| Reality.cz rent | **25** | **841** | 1.2 s | was **24** on best-match `/Ceska-republika/` (letter-only codes dropped); now `?s=2`, 25/25 price+image, 24/25 locality, 22/25 GPS or city pin, page 2 via `g=1-2` is 25 cards / 1 overlap |
| Reality.cz houses | **25** | **41** | 0.8 s | new newest shard `/pronajem/domy/Ceska-republika/?s=2`; 25/25 locality, 24/25 price, 23/25 coords |
| Reality.cz sale | **25** | **1000** cap | 1.0 s | nationwide hits “prvních 1.000 nabídek”; page 2 is 25 / 0 overlap |
| Bezrealitky | **20** | **4369** | 1.1 s | was **15** / 2278; GraphQL `limit` 20 matches peers (API also accepts 30; kept 20) |

List path still does not call Photon/Nominatim: card `gpsx`/`gpsy` when present, otherwise local city pins. Reality.cz pagination is `g={page-1}-2` (not `strana`). Synthetic page-1-across with two extra Reality.cz house shards: **21→46** page-1 shards / **900→2300** listings under a 0.7 s contention deadline (was 44/2200 before the house shards). InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, and map freshness polling are unchanged.

After this branch (RE/MAX newest + DMS GPS + house shards, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| RE/MAX rent | **21** | **1097** | 0.9 s | was **20** / 20, 0 GPS, `order_by_price=0` (nejdražší), 20/21 cards; now `order_by_published_date=0`, 21/21 price+image+locality+DMS GPS |
| RE/MAX houses | **21** | **58** | 0.6 s | new newest shard `/domy-a-vily/pronajem/`; 21/21 price+image+locality+GPS |
| RE/MAX sale | **21** | **1891** | 0.6 s | same parser; 21/21 fields |
| Annonce rent | **20** | 20 | 0.4 s | 20/20 price+image+locality, 19/20 city pins (already at peers) |
| iDNES rent | **26** | **8469** | 1.3 s | 26/26 price+image+locality; 24/26 GPS (two Croatia localities have no local pin) |

List path still does not call Photon/Nominatim: RE/MAX card `data-gps` DMS when present, otherwise local city pins. Synthetic page-1-across with two extra RE/MAX house shards: **21→48** page-1 shards / **900→2400** listings under a 0.7 s contention deadline (was 46/2300 before the house shards). InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and Reality.cz/Bezrealitky page-1 wins are unchanged.

After this branch (Sreality sale/house detail URLs + newest house shards, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| Sreality rent byty | **22** | **11307** | ~1.2 s | already 22/22 price+image+locality+GPS, `razeni=nejnovejsi`; sale/house **detail URLs** were hardcoded `/pronajem/byt/` (houses **404**, sales 301) |
| Sreality sale byty | **22** | **20003** | ~0.7 s | `/detail/prodej/byt/…`; price `0` is `Cena neuvedena` (was `0 Kč/nemovitost`) |
| Sreality houses | **22** | **774** rent / **21285** sale | ~0.7–1.0 s | new newest shards `/hledani/{pronajem\|prodej}/domy?razeni=nejnovejsi`; `/detail/…/dum/rodinny/…` 200 (was `/byt/Rodinný/` 404) |

List path still does not call Photon/Nominatim: Sreality JSON `locality.latitude/longitude` when present, otherwise local city pins. Synthetic page-1-across with two extra Sreality house shards: **21→50** page-1 shards / **900→2500** listings under a 0.7 s contention deadline (was 48/2400 before the house shards). InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and Reality.cz/Bezrealitky/REMAX/Annonce page-1 wins are unchanged.

After this branch (Bazoš page-1 audit: canonical category URLs, house shards, hyphenated okres pins, path pagination, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| Bazoš rent byty | **20** | **5632** | 0.5 s | already 20 cards; `/pronajem/byty/` **404** mixed catalog now rewritten to `/pronajmu/byt/`; 15/20 priced (Nabídněte/V textu/Zdarma → Cena neuvedena), 18/20 image (`empty.gif` skipped), 20/20 locality, **19/20 GPS** (was 18; `Frýdek - Místek` pins; `Zahraničí` has no local pin) |
| Bazoš sale byty | **20** | **8247** | 0.5 s | `/prodam/byt/`; 19/20 price+GPS, 20/20 image+locality; detail URLs **200** |
| Bazoš houses | **20** | **461** rent / **8493** sale | 0.5 s | new newest shards `/pronajmu/dum/` + `/prodam/dum/` (were daily-only); 20/20 locality+GPS, path page 2 `/dum/20/` |

List path still does not call Photon/Nominatim: card `maps/place` GPS when present, otherwise local city pins (`Frýdek - Místek` matches `Frýdek-Místek`). Pagination is native `/pronajmu/byt/20/` (not `crp=`). Synthetic page-1-across with two extra Bazoš house shards: **21→52** page-1 shards / **900→2600** listings under a 0.7 s contention deadline (was 50/2500 before the house shards). InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and Sreality/REMAX/Reality.cz/Bezrealitky/Annonce page-1 wins are unchanged.

After this branch (iDNES houses/sale page-1 audit: canonical category URLs, house shards, Croatia GPS pin aliases, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| iDNES rent byty | **26** | **8472** | 1.2 s | already 26/26 price+image+locality; GPS **26/26** (was 24; Rijeka/Opatija pin locally). `/s/pronajem/byt/` and `/s/pronajem/dum/` **404** now rewritten to `/byty/` / `/domy/` |
| iDNES sale byty | **26** | **27891** | 1.2 s | 26/26 price+image+locality; GPS **26/26** (was 15; Croatia cities + Chorvatsko fallback). Nationwide parse no longer injects `/praha/` |
| iDNES houses | **26** | **608** rent / **28597** sale | 1.3–1.4 s | new newest shards `/s/{pronajem\|prodej}/domy/` (were daily-only); detail `/detail/…/dum/…` **200**; rent 25/26 price (`Cena na vyžádání` kept); sale 26/26 price+image+locality+GPS |

List path still does not call Photon/Nominatim: local city pins plus Croatian GPS pin aliases (`Rijeka` stays Rijeka, unknown `…, Chorvatsko` falls back to the country pin). Synthetic page-1-across with two extra iDNES house shards: **21→54** page-1 shards / **900→2700** listings under a 0.7 s contention deadline (was 52/2600 before the house shards). InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and Bazoš/Sreality/REMAX/Reality.cz/Bezrealitky/Annonce page-1 wins are unchanged.

After this branch (ČeskéReality houses/sale page-1 audit: `/rodinne-domy/` shards, agency `/domy/` rewrite, local city pins, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| ČeskéReality rent byty | **20** | **4782** | 1.2 s | already 20/20 price+image; locality 19/20 (one title has no city); GPS **19/20** (was 16; Mariánské Lázně / Police nad Metují pin locally) |
| ČeskéReality sale byty | **20** | **9289** | 1.0 s | 20/20 price+image+locality; GPS **20/20** (was 16; Napajedla, Lanškroun, Hostivice, …) |
| ČeskéReality houses | **20** | **331** rent / **11271** sale | 1.0–1.1 s | new newest shards `/pronajem\|prodej/rodinne-domy/nejnovejsi/` (were missing). Live `/domy/` is the agency *Domy, spol. s r.o.* (0 cards) and used to fall back onto `/byty/`; now rewritten to `/rodinne-domy/`. Detail `…-id.html` **200**. Rent 20/20 price+image+locality, GPS 17/20. Sale 19/20 price (`Cena na dotaz` kept), 20/20 image+locality, GPS 12/20 (small municipalities have no local pin) |

List path still does not call Photon/Nominatim: local city pins only. Empty house `/nejnovejsi/` falls back to `/rodinne-domy/`, not apartments. Synthetic page-1-across with two extra ČeskéReality house shards: **21→56** page-1 shards / **900→2800** listings under a 0.7 s contention deadline (was 54/2700 before the house shards). InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and iDNES/Bazoš/Sreality/REMAX/Reality.cz/Bezrealitky/Annonce page-1 wins are unchanged.

After this branch (UlovDomov hydrate depth: sale+houses, larger fail-fast batch, unpriced first, same 12 s list cap, worker `fetch_page` + worker hydrate, not `/hry*`):

| Shard / hydrate | page 1 or attempted | priced | imaged | notes |
|---|---|---|---|---|
| Ulov rent byty sitemap | **20** | 0 on list | 0 on list | catalog total **3292** (3 houses split off; was 3295 mixed) |
| Ulov sale byty sitemap | **20** | 0 on list | 0 on list | catalog total **4291** (2 villas split off; was 4293 mixed) |
| Ulov rent houses `/pronajem/domy` | **3** | 0 on list | 0 on list | live sitemap has 3 `-dum` rents; HTML `/pronajem/domy` is empty SSR |
| Ulov sale houses `/prodej/domy` | **2** | 0 on list | 0 on list | Senohraby villas (`ve-vilach`); `-housing` slugs are **land**, not houses |
| Hydrate rent 20 (before=after) | **20** | **19** | **20** | 95%, 2.6–3.4 s. One card photos-only (price not invented) |
| Hydrate sale 20 | **20** | **19** | **19** | 95%, 2.6–3.6 s. One newest sale **410 DELETED** (gone, not a block) |
| Hydrate houses 5 | **5** | **5** | **4** | 100% priced. One rent house has no photos |
| Hydrate mixed 32 (new tick) | **32** | **30** | **30** | **94%**, 3.4 s. Mix: 14 rent + 13 sale + 3 rent houses + 2 sale villas. 1 gone, 1 photos-only. 429/403 abort still fail-fast |

Worker tick now round-robins rent / sale / houses, prefers catalog rows missing price, backfills newest sitemap stubs, default batch **32** / concurrency **6** / 18 s deadline, fail-fast on rate limits. `fetch_page` still never calls `offer/detail`. InstantSiteASGI `/hry*`, games live teaching, page-1-across, other portal page-1 wins, and M&M `SCRAPE_HTTP_PROXY` plumbing are unchanged.

Synthetic page-1-across with two extra UlovDomov house shards: **21→58** page-1 shards / **900→2900** listings under a 0.7 s contention deadline (was 56/2800 after ČeskéReality houses).

After this branch (Bezrealitky houses/sale page-1: newest `estateType=DUM` shards, list `surfaceLand` + extras, canonical listing URLs, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| Bezrealitky rent byty | **20** | **2280** CZ OSM | 1.1 s | already 20/20 price+image+locality+GPS. GraphQL `limit` 15/20/30/50 all work (50 cards in 0.3 s); kept **20** to match peers. Unscoped nationwide rent is 4292 |
| Bezrealitky sale byty | **20** | **879** | 1.0 s | 20/20 price+image+locality+GPS; detail `/nemovitosti-byty-domy/{uri}` **200** |
| Bezrealitky houses | **20** | **50** rent / **342** sale | 1.1 s | new newest shards `estateType=DUM` (were missing — only BYT recent/daily). Path `/vyhledat/prodej/dum` **404**; query-string search is live. List now keeps `surfaceLand` (Pozemek) + condition/ownership/equipped; `houseType` is GraphQL access-denied so it stays off the list query. `UNDEFINED` disposition is blank, not the token. Name fallback uses *domu*, not *nemovitosti*. Absolute/`nemovitosti-byty-domy/` `uri` values are not double-prefixed |

List path still does not call Photon/Nominatim: GraphQL `gps` on every live card. InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and ČeskéReality/iDNES/Bazoš/Sreality/REMAX/Reality.cz/Annonce page-1 wins are unchanged.

Synthetic page-1-across with two extra Bezrealitky house shards: **21→60** page-1 shards / **900→3000** listings under a 0.7 s contention deadline (was 58/2900 after UlovDomov houses).

Measure: `scripts/measure_page1_yield.py` (healthy portals, rent/sale + houses, 12 s cap, field fill).

After this branch (Annonce sale/houses page-1 audit: newest `sort=ageasc`, house estate, Frýdek Místek pin, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| Annonce rent byty | **20** | page-size (no list count) | 0.3 s | already 20/20 price+image+locality; GPS **20/20** (was 19; `Frýdek Místek` pins). `sort=ageasc` is the live “od nejnovějšího” control |
| Annonce sale byty | **20** | page-size | 0.4 s | 19/20 price (`Cena neuvedena` kept), 19/20 image (one card is icon-only), 20/20 locality+GPS. Detail `/inzerat/…-id-….html` **200**. One newest sale is a house mixed into `/byty-na-prodej.html` (estate from title) |
| Annonce houses | **24** rent / **20** sale | page-size | 0.3–0.4 s | newest shards already present (`/domy-k-pronajmu.html` + `/domy-na-prodej.html`). Estate **Dům** (was **Byt**). Dummy `-` / `ostatní` disposition blanked so title `5+kk` fills. Sale 20/20 price+image+locality, GPS **20/20**. Bare `/rodinne-domy.html` is the live sale-house index (offer used to be tagged Pronájem) and `/rodinne-domy-na-prodej.html` is empty — both rewrite to `/domy-na-prodej.html`. `?page=2` is 20–24 cards / 0 overlap |

List path still does not call Photon/Nominatim: local city pins only (`Frýdek Místek` matches `Frýdek-Místek`). Newest shards send `nabidkovy=1&sort=ageasc` so a price-sorted saved URL cannot starve NewDiscovery. InstantSiteASGI `/hry*`, games mobile 390px, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and Bezrealitky/ČeskéReality/iDNES/Bazoš/Sreality/REMAX/Reality.cz page-1 wins are unchanged.

Synthetic page-1-across stays **21→60** page-1 shards / **900→3000** listings under a 0.7 s contention deadline (Annonce house shards were already counted).

After this branch (nationwide newest pozemky page-1 shards, same 12 s cap, worker `fetch_page`, not `/hry*`):

| Portal | page 1 | catalog total | price / image / locality / GPS | estate | time |
|---|---|---|---|---|---|
| Sreality rent | **22** | **488** | 19 / 22 / 22 / 22 | Pozemky ×22 | 2.3 s |
| Sreality sale | **22** | **21060** | 22 / 22 / 22 / 22 | Pozemky ×22 | 0.9 s |
| Bazoš rent | **20** | **421** | 11 / 18 / 20 / 20 | Pozemek ×20 | 0.5 s |
| Bazoš sale | **20** | **13712** | 17 / 20 / 20 / 20 | Pozemek ×20 | 0.5 s |
| Bezrealitky rent | **7** | **7** | 7 / 7 / 7 / 7 | Pozemek ×7 | 0.7 s |
| Bezrealitky sale | **20** | **1647** | 20 / 20 / 20 / 20 | Pozemek ×20 | 1.0 s |
| Reality.cz rent | **25** | **46** | 16 / 25 / 25 / 22 | Pozemek ×25 | 1.1 s |
| Reality.cz sale | **25** | **1000** | 23 / 25 / 25 / 22 | Pozemek ×25 | 0.9 s |
| ČeskéReality rent | **20** | **246** | 19 / 20 / 20 / 11 | Pozemek ×20 | 1.1 s |
| ČeskéReality sale | **20** | **10946** | 20 / 20 / 20 / 4 | Pozemek ×20 | 1.2 s |
| iDNES rent | **26** | **424** | 20 / 26 / 26 / 25 | Pozemek ×26 | 1.6 s |
| iDNES sale | **26** | **26707** | 24 / 26 / 26 / 25 | Pozemek ×26 | 1.4 s |
| Annonce rent | **20** | page-size | 17 / 20 / 19 / 19 | Pozemek ×20 | 0.4 s |
| Annonce sale | **20** | page-size | 20 / 20 / 20 / 20 | Pozemek ×20 | 0.5 s |
| RE/MAX rent | **21** | **36** | 19 / 21 / 21 / 21 | Pozemek ×21 | 0.9 s |
| RE/MAX sale | **21** | **1614** | 18 / 21 / 21 / 21 | Pozemek ×21 | 0.6 s |
| Ulov rent | **0** | **0** | — | sitemap has no rent `housing` slugs | 2.1 s |
| Ulov sale | **20** | **103** | 0 / 0 / 20 / 0 | Pozemek ×20 sitemap stubs; hydrate fills price/photo | 0.9 s |

Before: newest minute shards were apartments + houses only (0 `:pozemky` recent keys). iDNES/Bazoš already crawled land on the daily regional path.

Live land index notes: Annonce `/pozemky-na-prodej.html` **404**; `/pozemky.html?business_type=130|131` is live. Sreality extras estate stays the portal `categoryMainCb` name **Pozemky** (same plural as house **Domy**). ČeskéReality sale GPS 4/20 is small municipalities with no local pin (same as houses). Ulov rent land is empty on the sitemap; sale `housing` slugs moved out of byty so they are no longer tagged Byt. Hydrate round-robin still puts land in the sale/rent bucket, not a new house slot.

List path still does not call Photon/Nominatim. InstantSiteASGI `/hry*`, games, page-1-across scheduler, Ulov hydrate, M&M `SCRAPE_HTTP_PROXY` plumbing, map freshness polling, and house/flat page-1 wins are unchanged. Smoke after this change: Annonce sale houses **20/20 Dům**, Bezrealitky rent houses **20/20 Dům**, Sreality sale houses **22 Domy**, Sreality rent byty **20 Byty**.

Healthy recent set is **78** shards (was 60). Unconstrained page-1-across finishes **78 / 1560**. Under a 0.7 s contention deadline (extras 300 ms): held-gate still **21 / 900**; page-1-across **48 / 960** — the extra 16 land HTML shards no longer all fit in 0.7 s at 16 concurrency.

## Scrape writer overlap (`SCRAPE_BATCH_COMMIT` / `SCRAPE_WRITE_CHUNK`)

Catalog/search/pins JSON shares SQLite WAL with NewDiscovery upserts. Two knobs, two layers:

| Knob | Default | Where | What it does |
|---|---|---|---|
| `SCRAPE_BATCH_COMMIT` | **500** | `Store.upsert_catalog_listings_batch` | Commit every N rows inside one writer connection. Floor 50. |
| `SCRAPE_WRITE_CHUNK` | **100** | `Hub._catalog_upsert` | Lock-scoped NewDiscovery / rolling-deep / catalog-sync / Ulov hydrate chunk. Floor 50, never above `SCRAPE_BATCH_COMMIT`. Releases `_catalog_write` between chunks so minute ticks can interleave. |

Live Hub already chunks at 100 even when Store `commit_every` is 500 — a 500-row transaction historically blocked admin/auth/tick writes on a ~1GB catalog. InstantSiteASGI / `SCRAPE_ROLE=web` never takes this lock (`write-deferred:web-role`). Measure before changing defaults: `python scripts/measure_scrape_commit.py` (15k fat listings + 2.5KB blobs, no residential proxy). Leave Store default 500 unless a shorter size clearly wins catalog/pins/search p95 without missing the 12s NewDiscovery write deadline. FTS `q=`, covering pin index, games, pozemky shards, Ulov hydrate, and M&M `SCRAPE_HTTP_PROXY` are unchanged.

Measured 2026-09-17 on this agent, 15k fat fixture, uncached catalog/search/pins under a NewDiscovery-shaped writer (400-row ticks, Hub chunks). Quiet first-hit is cold; writer rows are the overlap case. `#51` harness = 24-row refresh `commit_every=500` (batch ends first). Yield = 1500 existing-row refresh (steady-state minute cards already in catalog).

| writer | catalog p95 | search p95 | pins p95 | tight p95 | 1500-row yield | listings/s | 12s write deadline |
|---|---|---|---|---|---|---|---|
| quiet (no writer) | 21.5 ms | 33.3 ms | 37.7 ms | 13.2 ms | — | — | — |
| #51 harness batch=24 | 12.8 ms | 14.9 ms | **28.5 ms** | 18.8 ms | — | — | — |
| Hub chunk **50** | 10.8 ms | 14.9 ms | 26.0 ms | 18.8 ms | 5611 ms | **267** | ok |
| Hub chunk **100** (live default) | 13.0 ms | 15.1 ms | 26.9 ms | 18.0 ms | 2081 ms | **721** | ok |
| Hub chunk 250 | 13.2 ms | 15.7 ms | 26.7 ms | 17.6 ms | 1855 ms | 809 | ok |
| Hub chunk 500 | 12.8 ms | 16.1 ms | 27.0 ms | 19.1 ms | 1726 ms | 869 | ok |

Chunk 50 is **not** a clear win: city-pin p95 only ~1 ms better than live 100, while write throughput drops 2.7× (still inside 12s, but it starves listing yield). Chunk 250/500 do not improve p95. **Leave `SCRAPE_BATCH_COMMIT=500` and `SCRAPE_WRITE_CHUNK=100`.** EXPLAIN still covering `idx_listings_pin_cover` for pins and `listings_fts` LIST SUBQUERY for `q=Praha`.

### City-wide pin grid (span ≥ 0.35)

Leftover after the chunk sweep was the city-grid SQL, not a shorter commit. Inner `GROUP BY` listing identity built a second temp B-tree over every GPS row (~21 ms SQL / **~27–29 ms** writer p95 on 15k). City pins now `GROUP BY` ~100m integer buckets on `idx_listings_pin_cover` (no identity subquery, `INDEXED BY`). Holešovice 0.0012° cells stay unique (`test_16`). Tight zoom is still one pin per identity with covering-index labels. Defaults stay **500 / 100**.

Measured 2026-09-17 on this agent, same 15k fat fixture / `measure_scrape_commit.py --chunks 100` (no residential proxy). Before = tip of #52; after = covering-index grid.

| writer | catalog p95 | search p95 | pins p95 | tight p95 | 1500-row yield | listings/s |
|---|---|---|---|---|---|---|
| quiet before → after | 20.6 → 21.1 ms | 23.2 → 25.7 ms | **24.1 → 11.9 ms** | 12.8 → 14.6 ms | — | — |
| #51 harness before → after | 12.9 → 13.4 ms | 14.7 → 15.2 ms | **28.8 → 13.6 ms** | 17.2 → 18.1 ms | — | — |
| Hub chunk **100** before → after | 11.0 → 11.4 ms | 13.4 → 14.4 ms | **24.2 → 12.0 ms** | 13.5 → 13.2 ms | 5161 → 5196 ms | 291 → 289 |

City-pin writer p95 is clearly under 20 ms. Tight-zoom stays ~13–18 ms (well under the ~24 ms leftover). Catalog/`q=` FTS plans unchanged (`idx_listings_first_seen` + `listings_fts` LIST SUBQUERY). EXPLAIN city pins: covering `idx_listings_pin_cover` range scan + one `GROUP BY` temp B-tree (no `CO-ROUTINE`).

### Catalog list / FTS search covering index

Leftover after the city-grid slice was catalog list and `q=` writer heat. Newest/`q=` over-fetch used `idx_listings_first_seen` (first_seen only) then looked up fat `listings` rows (2.5KB extras + 2.5KB description). Card hydrate pulled those blobs again for the page.

`idx_listings_first_seen` is now a covering identity+card index (`INDEXED BY`, no extras/description, no last_seen so last_seen-only refreshes do not rewrite it). Over-fetch LIMIT walks that covering index. List/search cards use lean offer/estate from covering columns (no extras blob); amenity flags stay on `catalog_item`. `q=` stays FTS `IN` + LIST SUBQUERY (correlated EXISTS probed FTS per row and could not push LIMIT: Hub-100 search p95 ~52 ms on `q=Praha`). Token/prefix + diacritics-folded MATCH is unchanged (no interior substring). Defaults stay **500 / 100**. Pin covering index is unchanged.

Measured 2026-09-17 on this agent, same 15k fat fixture / `measure_scrape_commit.py --chunks 100` (no residential proxy). Before = tip of #53; after = covering catalog index.

| writer | catalog p95 | search p95 | pins p95 | tight p95 | 1500-row yield | listings/s |
|---|---|---|---|---|---|---|
| quiet before → after | 20.5 → 19.4 ms | 22.8 → 22.2 ms | 11.1 → 11.4 ms | 11.9 → 13.4 ms | — | — |
| #51 harness before → after | 13.6 → 11.3 ms | 15.2 → 14.0 ms | 14.0 → 13.9 ms | 18.5 → 18.6 ms | — | — |
| Hub chunk **100** before → after | **13.2 → 9.8 ms** | **15.5 → 12.2 ms** | 12.5 → 11.7 ms | 14.6 → 15.2 ms | 5258 → 5645 ms | 285 → 266 |

Catalog/search writer p95 dropped ~3 ms each. City pins stay ~12 ms; tight-zoom ~15 ms (same leftover band as #53). Yield still inside the 12s NewDiscovery write deadline. EXPLAIN catalog newest/`q=Praha`: covering `idx_listings_first_seen` (search still `listings_fts` LIST SUBQUERY, not CORRELATED). Pins still covering `idx_listings_pin_cover`.

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
- Soft backends: `curl_cffi` → Playwright → system Chrome, each under `SCRAPE_BROWSER_TIMEOUT_SEC` (12s). Worker proxy URL is forwarded to those backends when set.
- UlovDomov worker hydrate: `v2/offer/detail` for newest unpriced cards (batch 32, rent+sale+houses, fail-fast). Off on `SCRAPE_ROLE=web`. Opt out with `SCRAPE_ULOV_HYDRATE=0`.
- Opt-in `SCRAPE_HTTP_PROXY` / `SCRAPE_HTTPS_PROXY` on the scrape worker only (see below).

### Enable residential proxy (worker only)

InstantSiteASGI / `/hry*` / `SCRAPE_ROLE=web` never send traffic through this proxy. Set these **on the scrape worker** (Railway `worker` process, or local `SCRAPE_ROLE=all`):

```
SCRAPE_HTTP_PROXY=http://user:pass@residential.example:8080
SCRAPE_HTTPS_PROXY=          # optional; defaults to SCRAPE_HTTP_PROXY
SCRAPE_PROXY_PORTALS=mmreality
```

A host:port value without a scheme is treated as `http://`. SOCKS URLs (`socks5://`) are accepted by the plumbing but not required.

Do **not** use generic `HTTP_PROXY` / `HTTPS_PROXY` to “turn on M&M” — those are ignored by `app.scrape_proxy` on purpose so a shared dyno env cannot leak residential egress onto InstantSite, games, or other web httpx clients.

When **unset**: M&M keeps the current hard-block classify + per-portal cooldown (0 fake listings). Challenge/block still does not defer pages 2..N.

When **set** and `SCRAPE_ROLE` is `worker` or `all`: M&M list `fetch_page` (httpx) uses that HTTP(S) proxy. Other HTML portals stay on datacenter egress unless added to `SCRAPE_PROXY_PORTALS`. Worker-only `SCRAPE_BROWSER_FETCH` backends get the same URL.

Live list yield is still **0** from this datacenter without a real residential egress. Re-measure with `scripts/measure_listing_yield.py` after a proxy is in place before expanding the allowlist or making anything default-on. This merge does not require a live proxy.

### Follow-up (exact)

1. ~~Run the scrape worker with a residential / ISP proxy~~ — **plumbing shipped** (`SCRAPE_HTTP_PROXY` on worker; mocked transport tests). Live M&M cards still need a real proxy.
2. Persistent Playwright Chromium context on `SCRAPE_ROLE=worker` only, `SCRAPE_BROWSER_FETCH=1`, hard timeout 12s, one probe per cooldown window.
3. If the proxy gets a **challenge** (not hard-block), keep cookies and reuse the context for list pages; parse existing JSON-LD / card HTML.
4. Do not add Playwright or Chrome to the web dyno. Do not import `app.browser_fetch` or `app.scrape_proxy` from `app.site_pages`.
5. Re-measure list yield (`scripts/measure_listing_yield.py`) after a real proxy is in place before making the flag default-on.
