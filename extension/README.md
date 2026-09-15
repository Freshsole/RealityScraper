# Realitify Chrome extension (Sreality)

MV3 rozšíření injektuje Realitify skóre do výpisu a detailu na [sreality.cz](https://www.sreality.cz) při aktivním tarifu Start/PRO.

## Instalace (Load unpacked)

1. Spusť Realitify backend (`make run` / lokálně `http://127.0.0.1:8080`).
2. V Chrome otevři `chrome://extensions` → zapni **Developer mode** → **Load unpacked** → vyber složku `extension/`.
3. V popup rozšíření nastav **API** na `127.0.0.1:8080` (dev) nebo `realitify.cz` (prod).
4. Přihlas se na stejném hostu (cookie `realitify_session`) a otevři Sreality.

## Co uvidíš

- Badge **Realitify Active** (+ PRO/START) v headeru Sreality
- Skóre widget na kartách ve výpisu
- Analýza panel na detailu inzerátu

## API

- `GET /api/extension/me` — účet + `active` / `pro`
- `POST /api/extension/scores` — `{ "ids": ["52351052"], "urls": ["https://…"] }`
- `POST /api/extension/ingest` — scrapne chybějící Sreality detaily do katalogu a vrátí skóre

Auth: cookie `realitify_session` nebo header `X-Realitify-Session` (service worker čte cookie přes `chrome.cookies`).

Když skóre vrátí `found: false`, extension hned zavolá `ingest` — inzerát se stáhne do Realitify katalogu a widget se obnoví.

## Poznámky

- Skóre v1 je deterministická heuristika z katalogu (cena/m² vs peers, dny na trhu, trend, realitka/soukromé).
- Inzerát mimo katalog → „zatím bez dat“, skóre se nevymýšlí.
- Selektor DOM na Sreality je best-effort (SPA); při redesignu portálu může být potřeba upravit `content/search.js` / `content/detail.js`.
