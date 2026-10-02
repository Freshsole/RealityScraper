# Realitify public MCP server

Read-only Streamable HTTP MCP at `/mcp`. Calls the Realitify catalog API; does not hold a local database.

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

Connect to `http://127.0.0.1:8100/mcp` (Streamable HTTP). Check `tools/list` annotations and call `search_listings`, `get_listing`, `locality_stats`.

## Production

- URL: `https://mcp.realitify.cz/mcp`
- Env: `CATALOG_BASE_URL=https://realitify.cz`
- Deploy as a separate Coolify app with Base Directory `mcp_server` (Dockerfile).
