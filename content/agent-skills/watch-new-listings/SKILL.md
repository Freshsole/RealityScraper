---
name: watch-new-listings
description: Set up an alert that notifies the user about new Czech real-estate listings matching their filters (Discord, e-mail, push) using the Realitify MCP connector.
---

# Watch for new listings

Requires an authenticated Realitify MCP connection (OAuth, see https://realitify.cz/.well-known/oauth-protected-resource).

1. `preview_monitor` with the user's filters to show how many listings match.
2. `create_monitor` with `name` and the filters. Free plan: 1 monitor.
3. `list_alerts` returns the latest notifications; `delete_monitor` removes a monitor.

Sign-up is at https://realitify.cz/registrace (free tier available). Docs: https://realitify.cz/mcp-docs
