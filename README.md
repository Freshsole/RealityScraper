# Sreality monitor

Hlídá konkrétní hledání na [Sreality](https://www.sreality.cz) a při novém inzerátu pošle na Discord foto, cenu, dispozici, lokalitu, rozlohu a proklik.

## Jak to pozná nový byt

Sreality je Next.js aplikace. Výsledky hledání přijdou v JSON (`/_next/data/...` nebo `__NEXT_DATA__` v HTML), ne z lámání HTML karet.

1. Filtry z tvého URL zůstanou. Řazení se pro detekci přepne na **nejnovější** — u „nejlevnější“ by nový dražší byt na první stránce nebyl.
2. Stabilní ID inzerátu se ukládá do SQLite (`data/monitor.sqlite`).
3. **První běh** si potichu uloží celou aktuální nabídku. Nic nejde na Discord.
4. Další kontroly berou první dvě stránky nejnovějších a u neznámého ID stáhnou detail (`since` / `edited` / případnou starou cenu).
5. Discord jde u **nového** inzerátu (vložen v posledních 2 dnech) a u **starého, když se změní cena** (porovnání s uloženou cenou, nebo sleva ze Sreality). Holý bump bez změny ceny se neposílá.

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

Dashboard: [http://127.0.0.1:8080](http://127.0.0.1:8080)

Webhook a výchozí URL hledání jsou v `.env`. Další hledání, Discord šablony a generátor filtrů jsou v dashboardu.

Výchozí interval je 60 s. Dashboard umí víc monitorů najednou, start/stop, ruční kontrolu a testovací zprávu.
