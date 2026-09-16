# Listing coverage: M&M Cloudflare + UlovDomov

Measured 2026-09-16 from a datacenter IP (this Cloud Agent). InstantSiteASGI / `/hry*` is unchanged — browser/JA3 code is not on that path.

## UlovDomov (yield)

| Path | Result |
|---|---|
| `POST ud.api.ulovdomov.cz/v1/offer/find` | **500** `udBe.internalServerError` (nationwide + Praha bounds, empty body) |
| `POST /v2/offer/find` | 400/422/500 depending on `parameters` shape; rent+bounds still **500** |
| `GET /v2/offer/latest` | 400/500 |
| `GET /v2/offer/detail?offerId=` | **200** (detail only — not used on the list path) |
| HTML `/pronajem/byty` | 200, 94 KB, **0** listing hrefs; `__NEXT_DATA__` has `count` only |
| `/_next/data/{buildId}/pronajem/byty.json` | 200, `count: 3293`, **0** offers |
| `GET /sitemap-offers.xml` | **200**, 7635 `<loc>` (3295 `pronajem-*`, 47 coliving, ~4293 sale / leading-dash slugs) |

Worker now: API → sitemap cards (cached 8 min, newest-by-id pages of 20) → HTML/`_next/data` only if the sitemap fetch is not valid XML. Empty offer shard after a good sitemap returns `[]` without cooling the portal.

Fixture yield: 2 rent + 1 sale cards from `tests/fixtures/ulov_sitemap_offers.xml` after a mocked 500.

Live `scripts/measure_listing_yield.py` (this agent, 2026-09-16):

| Portal | page 1 | catalog total | page-1 time | notes |
|---|---|---|---|---|
| UlovDomov rent | **20** | **3295** | 1.9 s (API 500 + 1.1 MB sitemap) | later pages hit the 8 min cache |
| UlovDomov sale | **20** | **4293** | cached | leading-dash sitemap slugs |
| M&M Reality | **0** | 0 | 52 ms | `blocked:cloudflare:403` hard — browser not launched |

Limit: sitemap cards have URL, slug locality, disposition — not price/photos until a later detail pass (`v2/offer/detail` works). Follow-up: optional worker-only detail hydrate for the newest N, still off `/hry*`.

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

### Follow-up (exact)

1. Run the scrape worker with a **residential / ISP proxy** (not this datacenter egress).
2. Persistent Playwright Chromium context on `SCRAPE_ROLE=worker` only, `SCRAPE_BROWSER_FETCH=1`, hard timeout 12s, one probe per cooldown window.
3. If the proxy gets a **challenge** (not hard-block), keep cookies and reuse the context for list pages; parse existing JSON-LD / card HTML.
4. Do not add Playwright or Chrome to the web dyno. Do not import `app.browser_fetch` from `app.site_pages`.
5. Re-measure list yield (`scripts/measure_listing_yield.py`) after proxy is in place before making the flag default-on.
