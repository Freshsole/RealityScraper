# Realitify MCP — directory submission copy-paste pack

Copy texts below into Claude Connectors Directory and OpenAI ChatGPT Apps (With MCP) forms.
Replace every `[DOPLNIT: …]` before submit.

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
Realitify MCP is a read-only connector for Czech real-estate listings. Search rentals and sales by locality, price, and layout, open listing details with source links, and get locality price-per-m² stats — no login required.
```

## Long description (EN)

```
Realitify aggregates Czech apartment listings from major portals. This public MCP server exposes three read-only tools over Streamable HTTP so Claude, ChatGPT, and other MCP clients can help users find rentals or sales.

Tools:
• search_listings — filter by locality, offer type (pronajem/prodej), price, disposition, and area (max 20 results)
• get_listing — fetch one listing by id or listing_key
• locality_stats — active listing count and average CZK/m² for a locality

Every listing includes the original portal URL and a Realitify deep link. Broker names and phone numbers are stripped from responses. The connector does not create accounts, monitors, or write any data.

Server: https://mcp.realitify.cz/mcp
Docs: https://realitify.cz/mcp-docs
```

## Primary use cases (2–3)

1. **Find a rental quickly** — Ask for a 2+kk in Prague under a monthly budget and get compact results with source + Realitify links.
2. **Compare localities** — Ask for average rent per m² and active listing counts in Brno vs Prague.
3. **Inspect one listing** — Open details for a specific listing id from a previous search before visiting the portal.

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

---

## Tools and annotation rationale

### `search_listings`

- **readOnlyHint=true** — only queries the catalog API; no writes.
- **destructiveHint=false** — cannot delete or modify listings or accounts.
- **idempotentHint=true** — same filters return the same catalog snapshot (aside from live catalog changes).
- **openWorldHint=false** — reads only Realitify’s own catalog API / DB-backed data, not arbitrary external sites at call time.

### `get_listing`

- **readOnlyHint=true** — DB-only detail (`enrich=0`); no mutations.
- **destructiveHint=false** — no side effects on Realitify data.
- **idempotentHint=true** — repeated calls with the same id return the same record.
- **openWorldHint=false** — reads Realitify catalog only (no live portal scrape).

### `locality_stats`

- **readOnlyHint=true** — aggregated read from catalog stats API.
- **destructiveHint=false** — no writes.
- **idempotentHint=true** — same locality/offer returns the same aggregates until the catalog changes.
- **openWorldHint=false** — computed on Realitify’s catalog, not the open web.

---

## Starter prompts (5)

1. I'm looking for a 2-room flat in Prague under 20,000 CZK rent.
2. Show me 3+1 apartments for sale in Brno under 5,000,000 CZK.
3. What is the average rent per m² in Ostrava right now?
4. Find rentals in Praha 5 with at least 50 m², max 25,000 CZK, show 10 results.
5. Open the details for the first listing from the previous search and include both links.

---

## OpenAI test cases

### Positive (exactly 5)

| # | Prompt | Expected tool | Expected result |
| --- | --- | --- | --- |
| 1 | Find 2+kk rentals in Prague under 20000 CZK | `search_listings` | JSON with `items` (≤20), each with `source_url` and `realitify_url` |
| 2 | Get locality stats for Brno rentals | `locality_stats` | JSON with `active_count` and `avg_price_per_m2` |
| 3 | Show sales listings in Ostrava under 4000000 CZK | `search_listings` with `offer_type=prodej` | Items marked sale / matching price filter |
| 4 | Open listing id `{id from prior search}` | `get_listing` | Single listing JSON with title, price, links |
| 5 | How many active rentals are there in Praha? | `locality_stats` | `active_count` ≥ 0 for locality Praha |

### Negative (exactly 3)

| # | Prompt | Expected tool | Expected result |
| --- | --- | --- | --- |
| 1 | Create a monitor for Praha 2+kk under 20k | none / refusal | No write tool exists; assistant should explain read-only |
| 2 | Delete my Realitify account via MCP | none / refusal | No account tools; cannot delete |
| 3 | Get listing id `this-id-does-not-exist-xyz` | `get_listing` | Clear error: listing not found / Catalog API error |

---

## Demo video scenario (1–2 minutes)

1. **0:00–0:15** — Open ChatGPT or Claude; show Realitify connector enabled; show docs page `https://realitify.cz/mcp-docs` briefly.
2. **0:15–0:45** — Prompt: “I'm looking for a 2+kk flat in Prague under 20,000 CZK.” Show `search_listings` call and 3–5 compact results with prices and links.
3. **0:45–1:10** — Prompt: “Open details for the first result.” Show `get_listing` and highlight `source_url` + `realitify_url`.
4. **1:10–1:35** — Prompt: “What is the average rent per m² in Brno?” Show `locality_stats` numbers.
5. **1:35–1:50** — Optional: try a write request (“create a monitor”) and show that only read-only tools exist.
6. **End** — Flash privacy/terms URLs and support email.

---

## Manual checklist (you must do these)

### Before submit

- [ ] Fill every `[DOPLNIT: …]` in this file and on `/privacy`, `/terms`, `/ochrana-soukromi`, `/obchodni-podminky`
- [ ] Deploy main app so `/api/public/catalog*`, `/privacy`, `/terms`, `/mcp-docs`, `/public/mcp-assets/*` are live
- [ ] Deploy MCP Coolify app on `mcp.realitify.cz` with `CATALOG_BASE_URL=https://realitify.cz`
- [ ] Verify `curl https://mcp.realitify.cz/health`
- [ ] Verify one real `search_listings` call
- [ ] Optional: run `scripts/mcp_screenshots.sh` and upload screenshots
- [ ] Record the 1–2 min demo video

### Claude Connectors Directory

- [ ] Open [claude.ai/directory/manage](https://claude.ai/directory/manage)
- [ ] Create/submit connector with name, tagline, descriptions, tool list, URLs above
- [ ] MCP URL: `https://mcp.realitify.cz/mcp`
- [ ] Upload icon `public/mcp-assets/icon-512.png`
- [ ] Upload demo video

### OpenAI ChatGPT Apps (With MCP)

- [ ] OpenAI portal → create app with transport **With MCP**
- [ ] MCP server URL: `https://mcp.realitify.cz/mcp`
- [ ] Paste short/long description, use cases, starter prompts, test cases
- [ ] Privacy `https://realitify.cz/privacy`, Terms `https://realitify.cz/terms`, Docs `https://realitify.cz/mcp-docs`
- [ ] **Domain verification:** add the DNS TXT record at Active24 for `realitify.cz` (exact value from OpenAI UI)
- [ ] **Identity verification** in OpenAI dashboard
- [ ] Create/use an OpenAI project with **GLOBAL** data residency (not EU-only)
- [ ] Upload demo video and icon
- [ ] Submit for review

### Placeholders still to fill

- `[DOPLNIT: company legal name]` / `[DOPLNIT: obchodní název]`
- `[DOPLNIT: IČO]`
- `[DOPLNIT: registered address]` / `[DOPLNIT: sídlo]`
- `[DOPLNIT: soud a spisová značka]`
- `[DOPLNIT: access log retention period]` / `[DOPLNIT: doba uchování access logů]`
- `[DOPLNIT: OpenAI organization / project IDs if forms require them]`
- `[DOPLNIT: Claude publisher / contact name if forms require them]`
