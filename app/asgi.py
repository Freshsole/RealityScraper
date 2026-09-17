"""Web-dyno ASGI entry: InstantSite + API, never scrape engines.

Uvicorn / Procfile / supervisord `web` must load this module (not `app.main:app`
alone) so a shared `SCRAPE_ROLE=all` service env cannot start NewDiscovery,
rolling-deep, or Ulov hydrate on the InstantSite process. Local all-in-one stays
`uvicorn app.main:app` with default SCRAPE_ROLE=all.

Worker entry is `python -m app.scrape_worker` (forces SCRAPE_ROLE=worker).
"""

from __future__ import annotations

import os

os.environ["SCRAPE_ROLE"] = "web"

from app.main import app  # noqa: E402

__all__ = ["app"]
