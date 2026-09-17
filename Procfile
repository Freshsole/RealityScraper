# InstantSite + API only. Never NewDiscovery / rolling-deep / Ulov hydrate / scrape engines.
# Railway uses supervisord (railpack.toml) with the same split; this Procfile is the
# documented fallback (Heroku-style, local honcho, etc.).
#
#   SCRAPE_ROLE=web     uvicorn app.asgi:app     InstantSite, games, dashboard, API
#   SCRAPE_ROLE=worker  python -m app.scrape_worker
#                       NewDiscovery, rolling deep, Ulov hydrate, catalog, M&M proxy
#
# Local all-in-one (do not use app.asgi):  uvicorn app.main:app   (default SCRAPE_ROLE=all)
web: env SCRAPE_ROLE=web uvicorn app.asgi:app --host 0.0.0.0 --port $PORT
worker: env SCRAPE_ROLE=worker python -m app.scrape_worker
