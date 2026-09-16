# Realitify — Czech real-estate monitor

Hlídá hledání na **10 portálech** a při novém inzerátu pošle upozornění (Discord / e-mail / push) s fotkou, cenou, dispozicí, lokalitou a proklikem.

Pokryté portály: Sreality, Reality.iDNES, Bazoš, ČeskéReality, Bezrealitky, Annonce, M&M Reality, UlovDomov, RE/MAX, Reality.cz.

## Jak to pozná nový byt

Sreality (a kde to jde i další) bereme JSON / `_next/data` / veřejné API. Ostatní portály mají HTML parsery s fixture testy. Minutová fronta `NewDiscovery` tahá nejnovější shards **všech 10 portálů** paralelně (Sreality po dispozicích, ostatní nationwide newest-first). Rolling deep rotuje denní shards napříč portály. Denní katalog sync má pro každý portál vlastní hodinu (`CATALOG_SYNC_HOUR_*`).

1. Filtry z URL zůstanou. Řazení se pro detekci přepne na **nejnovější**.
2. Stabilní ID inzerátu se ukládá do SQLite (`data/monitor.sqlite`).
3. **První běh** si potichu uloží aktuální nabídku. Nic nejde na Discord.
4. Další kontroly berou první stránky nejnovějších; u neznámého ID jde detail.
5. Notifikace u **nového** inzerátu (vložen v posledních 2 dnech) a u **změny ceny**.

## Windows

Složka `dist/SrealityMonitor` je přenosný balíček. Na Windows spusť `SrealityMonitor.exe`. Vedle exáče musí zůstat `python`, `app`, `web`, `run_app.py` a `.env` s webhookem. Prohlížeč se otevře na [http://127.0.0.1:8080](http://127.0.0.1:8080). Zavřením černého okna hlídání vypneš.

Novou verzi Windows stáhne samo z GitHub Releases (`UPDATE_FEED` v `.env`). `.env` a `data/` se při aktualizaci nepřepisují.

Nová verze z Macu:

```bash
python packaging/release.py patch
```

To zvedne číslo v `VERSION`, složí zip a nahraje release. Znovu sestavit jen lokálně: `python packaging/build_windows.py`

## Spuštění

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

Lokálně default `SCRAPE_ROLE=all` (web + scrape v jednom procesu). Na Railway startuje `supervisord` (`railpack.toml`) se dvěma procesy: `web` (`SCRAPE_ROLE=web`) a `worker` (`python -m app.scrape_worker`). Pád scrapru neshodí web a naopak (`autorestart` per proces).

Dashboard: [http://127.0.0.1:8080](http://127.0.0.1:8080)

Webhook a výchozí URL hledání jsou v `.env`. Další hledání, Discord šablony a generátor filtrů jsou v dashboardu.

Výchozí interval je 60 s. Dashboard umí víc monitorů najednou, start/stop, ruční kontrolu a testovací zprávu.

Marketing: `/` landing, hry `/hry` (Higher/Lower + tip nájmu), admin žebříček `/admin/hry`.

### Scrape worker (minutové SLA)

- **NewDiscovery:** newest-first shards všech 10 portálů každou minutu (paralelní list / JSON, deadline `SCRAPE_DISCOVERY_DEADLINE_SEC`).
- **MonitorRefresh:** deduplikované URL aktivních monitorů + rolling deep mix všech portálů.
- Env knoby: `SCRAPE_CONCURRENCY` (default 16), `SCRAPE_CONCURRENCY_FLOOR` (4), `SCRAPE_RECENT_PAGES` (4), `SCRAPE_DISCOVERY_DEADLINE_SEC` (50), `SCRAPE_MONITOR_DEADLINE_SEC` (55), `SCRAPE_FULL_MARKET_DEADLINE_SEC` (70), `SCRAPE_BATCH_COMMIT` (500), `SCRAPE_ERROR_RATE_ALERT` (0.10).

Známá omezení živého webu: M&M Reality vrací Cloudflare **hard-block** 403 i z curl_cffi / headless Chrome na datacenter IP (měřeno). Challenge HTML zchladí jen ten portál a **nedeferuje** stránky 2..N. Opt-in `SCRAPE_BROWSER_FETCH=1` na `SCRAPE_ROLE=worker` (timeout `SCRAPE_BROWSER_TIMEOUT_SEC`, default 12 s) zkusí JA3/Playwright/Chrome jen u challenge, ne u hard-blocku — na `/hry*` / InstantSiteASGI se nespouští. Follow-up: rezidenční proxy + persistent Playwright na workeru. Reality.cz umí maintenance i JS shell — nationwide `/Ceska-republika/` vypis karty jdou z HTML. UlovDomov `offer/find` POST teď 500 a SSR/`_next/data` nese jen count; list jde ze `sitemap-offers.xml` (~3.3k pronájem + ~4.3k prodej, bez browseru). ČeskéReality 429 jen zchladí ten portál (Retry-After), ostatní NewDiscovery běží dál. Parsery jsou fixture-testované. Detail: `docs/listing-coverage.md`.