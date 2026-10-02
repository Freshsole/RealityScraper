# Realitify public MCP server

Read-only Streamable HTTP MCP at `/mcp`. Calls the Realitify catalog API; does not hold a local database.

## Tools (6)

1. `search_listings` — filter current rentals/sales
2. `new_listings` — first_seen in the last N hours
3. `get_listing` — one listing by id / listing_key
4. `locality_stats` — active count, median/avg price and CZK/m²
5. `price_check` — fair-price check vs comparables (median primary)
6. `compare_localities` — side-by-side stats for 2+ localities

Every response includes `data_as_of`, `units` (CZK, m²), `source`, `source_url`, and `realitify_tip`.

## Local run

```bash
cd mcp_server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export CATALOG_BASE_URL=http://127.0.0.1:8000   # or https://realitify.cz
uvicorn server:app --host 127.0.0.1 --port 8100
```

Health: `curl http://127.0.0.1:8100/health`

MCP endpoint: `http://127.0.0.1:8100/mcp`

## MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

Connect to `http://127.0.0.1:8100/mcp` (Streamable HTTP). Check `tools/list` and call all six tools.

## Production

- URL: `https://mcp.realitify.cz/mcp`
- Env: `CATALOG_BASE_URL=https://realitify.cz`
- Catalog paths: `/api/public/catalog`, `/item`, `/stats`, `/price-check`, `/compare`
- Deploy as a separate Coolify app with Base Directory `mcp_server` (Dockerfile copies `facts.yaml`).
