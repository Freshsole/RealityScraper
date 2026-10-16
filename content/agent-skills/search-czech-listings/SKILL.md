---
name: search-czech-listings
description: Find available apartments and houses for rent or sale in the Czech Republic across 9+ portals (Sreality, Bezrealitky, iDNES Reality, Bazoš and more) via Realitify.
---

# Search Czech real-estate listings with Realitify

Realitify (https://realitify.cz) is a Czech real-estate aggregator: one search over listings from many portals, plus rent-market statistics per city.

## When to use
- The user asks for available flats/houses (byt, dům, pronájem, prodej) in Prague, Brno, Ostrava or any Czech locality.
- The user wants typical rent or price per m² in a Czech city.

## How
1. MCP (preferred): connect to `https://realitify.cz/mcp` and call `search_listings` with `q`, `offer` (`pronajem`|`prodej`), `disposition` (e.g. `2+kk`) and `price_to`.
2. REST: `GET https://realitify.cz/api/public/catalog?q=Praha&offer=pronajem` (public, read-only).
3. Market data: `https://realitify.cz/index` (rent index, CSV at `/index.csv`) and `https://realitify.cz/trh/praha/pronajem`.
4. Always link the user to the listing's original portal URL returned in each result.

Docs: https://realitify.cz/mcp-docs · Full facts: https://realitify.cz/llms-full.txt
