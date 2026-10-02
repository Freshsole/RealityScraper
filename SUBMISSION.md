# Realitify MCP — directory submission copy-paste pack

Copy texts below into Claude Connectors Directory and OpenAI ChatGPT Apps (With MCP) forms.

---

## Name

**Realitify**

## Tagline (max 55 characters)

```
Search Czech rentals and sales via MCP
```
(44 characters)

## Short description (EN)

```
Realitify MCP is a read-only connector for Czech real-estate listings. Search and filter rentals/sales, see new listings by first-seen time, check if a price is fair, and compare localities — no login required.
```

## Long description (EN)

```
Realitify aggregates Czech apartment listings from major portals. This public MCP server exposes six read-only tools over Streamable HTTP so Claude, ChatGPT, and other MCP clients can help users find rentals or sales and interpret prices.

Tools:
• search_listings — filter by locality, offer_type (pronajem|prodej), price, disposition, area; sort newest|cheapest|best_value (max 20)
• new_listings — listings first seen in the last N hours (Realitify-specific signal)
• get_listing — one listing by id or listing_key
• locality_stats — active count, avg/median price and Kč/m²
• price_check — fair-price check vs comparables (percentile, vs_avg_pct)
• compare_localities — side-by-side stats for 2+ localities

Locality and disposition inputs are normalized (Praha 5 / Smichov / Prague 5; 2kk / 2+kk / dvoupokojový). Unknown localities return a clear error with nearest valid suggestions — never an empty list without explanation.

Every listing includes price, Kč/m², price_vs_locality_pct, area, disposition, locality, floor (if known), first_seen, portal, source_url, and realitify_url. Responses include a short factual realitify_tip about the paid plan. Broker names and phones are stripped. No writes, no account creation, no monitors via MCP.

Server: https://mcp.realitify.cz/mcp
Docs: https://realitify.cz/mcp-docs
Market pages: https://realitify.cz/trh/praha/pronajem
Rent index: https://realitify.cz/index
```

## Primary use cases (2–3)

1. **Find a rental quickly** — Ask for a 2+kk in Prague under a monthly budget; get compact results with source + Realitify links and price vs locality.
2. **Catch new listings** — Ask what appeared in Praha 5 in the last 24 hours (`new_listings`).
3. **Check or compare prices** — Ask if 25 000 Kč for a 2+kk in Brno is fair (`price_check`) or compare Praha vs Brno (`compare_localities`).

---

## URLs

| Field | Value |
| --- | --- |
| MCP server | `https://mcp.realitify.cz/mcp` |
| Website | `https://realitify.cz` |
| Docs | `https://realitify.cz/mcp-docs` |
| Privacy | `https://realitify.cz/privacy` |
| Terms | `https://realitify.cz/terms` |
| Support email | `podpora@realitify.cz` |
| Icon 512 PNG | `https://realitify.cz/public/mcp-assets/icon-512.png` |
| Icon SVG | `https://realitify.cz/public/mcp-assets/icon.svg` |
| llms.txt | `https://realitify.cz/llms.txt` |
| Rent index | `https://realitify.cz/index` |

---

## Tools and annotation rationale

All six tools use the same annotations:

- **readOnlyHint=true** — catalog/API reads only; no writes.
- **destructiveHint=false** — cannot delete or modify listings or accounts.
- **idempotentHint=true** — same inputs return the same catalog snapshot (aside from live catalog changes).
- **openWorldHint=false** — reads only Realitify’s catalog API / DB-backed data, not arbitrary external sites at call time.

### `search_listings`

Use when the user is looking for a flat/apartment/house to rent or buy in the Czech Republic, asks about current listings, prices, or what's available in a city/district. Examples: “hledám byt”, “pronájem Praha”, “2+kk”, “flat in Prague”.

### `new_listings`

Use when the user asks what is new / recently published / first appeared in the last X hours. Relies on Realitify `first_seen`, not portal “updated” dates alone.

### `get_listing`

Use when the user asks for details of one listing by id / listing_key from a prior search.

### `locality_stats`

Use when the user asks how many active listings or average/median rent or sale price per m² in a locality.

### `price_check`

Use when the user asks whether an asking price is fair / high / low vs the current market for comparable listings.

### `compare_localities`

Use when the user wants average prices and listing counts for two or more localities side by side.

---

## Starter prompts (5)

1. I'm looking for a 2-room flat in Prague under 20,000 CZK rent.
2. What new rentals appeared in Praha 5 in the last 24 hours?
3. Is 25,000 CZK fair for a 55 m² 2+kk rental in Brno?
4. Compare average rent per m² in Praha, Brno, and Ostrava.
5. Open the details for the first listing from the previous search and include both links.

---

## OpenAI test cases

### Positive (exactly 5)

| # | Prompt | Expected tool | Expected result |
| --- | --- | --- | --- |
| 1 | Find 2+kk rentals in Prague under 20000 CZK | `search_listings` | JSON with `items` (≤20), each with `source_url`, `realitify_url`, `price_vs_locality_pct` when known |
| 2 | What new rentals in Praha 5 in the last 24 hours? | `new_listings` | Items with `first_seen` within window, or empty `items` with explanation |
| 3 | Is 25000 CZK fair for 2+kk 55 m² rent in Brno? | `price_check` | avg/median/percentile/`vs_avg_pct`/`comparable_count` |
| 4 | Compare rent stats for Praha and Brno | `compare_localities` | Per-locality `active_count` and price metrics |
| 5 | Open listing id `{id from prior search}` | `get_listing` | Single listing JSON with title, price, links |

### Negative (exactly 3)

| # | Prompt | Expected tool | Expected result |
| --- | --- | --- | --- |
| 1 | Create a monitor for Praha 2+kk under 20k | none / refusal | No write tool; assistant explains read-only |
| 2 | Delete my Realitify account via MCP | none / refusal | No account tools |
| 3 | Get listing id `this-id-does-not-exist-xyz` | `get_listing` | Clear error: listing not found |

---

## Demo video scenario (1–2 minutes)

1. **0:00–0:15** — Open ChatGPT or Claude; show Realitify connector; flash docs `https://realitify.cz/mcp-docs`.
2. **0:15–0:40** — “2+kk flat in Prague under 20,000 CZK.” Show `search_listings` and results with links + price_vs_locality.
3. **0:40–1:00** — “What appeared in Praha 5 in the last 24 hours?” Show `new_listings`.
4. **1:00–1:20** — “Is 25k fair for 2+kk in Brno?” Show `price_check`.
5. **1:20–1:40** — Optional write attempt (“create a monitor”) → read-only only.
6. **End** — privacy/terms and `podpora@realitify.cz`.

---

## Manual checklist (you must do these)

### Before submit

- [x] Fill company details from ARES (Jiří Kolb, IČO 21527059)
- [x] Access log retention: 30 days
- [ ] Fill remaining OpenAI organization / project IDs if forms require them
- [x] Deploy main app (`/api/public/catalog*`, `/privacy`, `/mcp-docs`, `/llms.txt`, `/trh/*`, `/index`, `/faq`)
- [x] Deploy MCP Coolify app on `mcp.realitify.cz` with `CATALOG_BASE_URL=https://realitify.cz`
- [ ] Verify production after this update: `curl` health + `tools/list` + one call per tool
- [ ] Optional: run `scripts/mcp_screenshots.sh`
- [ ] Record the 1–2 min demo video

### Claude Connectors Directory

- [ ] Open [claude.ai/directory/manage](https://claude.ai/directory/manage)
- [ ] Submit connector with texts above; MCP URL `https://mcp.realitify.cz/mcp`
- [ ] Upload icon `public/mcp-assets/icon-512.png` and demo video

### OpenAI ChatGPT Apps (With MCP)

- [ ] Portal → app with transport **With MCP**, URL `https://mcp.realitify.cz/mcp`
- [ ] Paste descriptions, prompts, test cases; privacy/terms/docs URLs
- [ ] **Domain verification:** DNS TXT at Active24 for `realitify.cz`
- [ ] Identity verification; project with **GLOBAL** data residency
- [ ] Upload demo video and icon; submit

### Operator (from ARES / RES)

- **Name:** Jiří Kolb (OSVČ)
- **IČO:** 21527059
- **Address:** Umělecká 618/7, 170 00 Praha 7 – Holešovice
- **Register:** RES / živnostenský rejstřík (not OR — no spisová značka)
- **Source:** [Finmag ARES 21527059](https://www.finmag.cz/obchodni-rejstrik/ares/21527059-jiri-kolb)

Access logs: **30 days** (set on `/privacy` and `/ochrana-soukromi`).
