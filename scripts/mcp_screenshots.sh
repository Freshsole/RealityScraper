#!/usr/bin/env bash
# Optional: screenshot the MCP docs page for directory submissions.
# Requires: npx playwright (downloads Chromium on first run).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${1:-$ROOT/public/mcp-assets}"
BASE_URL="${MCP_DOCS_URL:-https://realitify.cz/mcp-docs}"
mkdir -p "$OUT_DIR"
npx --yes playwright@1.48.0 install chromium >/dev/null
npx --yes playwright@1.48.0 screenshot \
  --viewport-size=1280,800 \
  "$BASE_URL" \
  "$OUT_DIR/mcp-docs-desktop.png"
echo "Wrote $OUT_DIR/mcp-docs-desktop.png"
