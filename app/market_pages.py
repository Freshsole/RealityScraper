"""Server-rendered market overview pages for AI/SEO (HTML in response, not JS)."""

from __future__ import annotations

import csv
import html
import io
from datetime import date, datetime, timezone
from typing import Any

from app.locality_normalize import normalize_locality
from app.store import PRAGUE_DISTRICTS, Store

MARKET_LOCALITIES: list[str] = (
    [f"Praha {n}" for n in range(1, 11)]
    + ["Praha", "Brno", "Ostrava", "Plzeň", "Olomouc"]
)


def slugify_locality(label: str) -> str:
    from app.locality_normalize import _fold

    return _fold(label).replace(" ", "-")


def locality_from_slug(slug: str) -> str:
    raw = (slug or "").replace("-", " ").strip()
    return normalize_locality(raw) or raw.title()


def list_market_paths(store: Store) -> list[tuple[str, str, str]]:
    """Return (path, locality, offer) for localities with enough data."""
    paths: list[tuple[str, str, str]] = []
    for loc in MARKET_LOCALITIES:
        for offer in ("pronajem", "prodej"):
            try:
                stats = store.catalog_stats(loc, offer)
            except ValueError:
                continue
            if int(stats.get("active_count") or 0) < 20:
                continue
            paths.append((f"/trh/{slugify_locality(loc)}/{offer}", loc, offer))
    return paths


def _fmt_czk(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{int(round(float(value))):,}".replace(",", " ") + " Kč"
    except (TypeError, ValueError):
        return "—"


def _fmt_m2(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.0f}".replace(",", " ") + " Kč/m²"
    except (TypeError, ValueError):
        return "—"


def render_prehled(store: Store, locality: str, offer: str) -> str:
    report = store.catalog_locality_report(locality, offer)
    offer_label = "pronájem" if offer == "pronajem" else "prodej"
    title = f"{locality} — {offer_label} | Realitify přehled trhu"
    updated = str(report.get("updated_at") or "")[:19].replace("T", " ")
    rows = "".join(
        f"<tr><td>{html.escape(str(r.get('disposition') or ''))}</td>"
        f"<td>{int(r.get('count') or 0)}</td>"
        f"<td>{_fmt_czk(r.get('avg_price'))}</td>"
        f"<td>{_fmt_m2(r.get('avg_price_per_m2'))}</td></tr>"
        for r in (report.get("by_disposition") or [])
    )
    hist = "".join(
        f"<li>{html.escape(str(h.get('week_start') or ''))}: "
        f"{int(h.get('new_count') or 0)} nových, avg {_fmt_m2(h.get('avg_price_per_m2'))}</li>"
        for h in (report.get("history_90d") or [])[-12:]
    )
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": title,
        "description": f"Agregované ceny a počty nabídek: {locality}, {offer_label}.",
        "url": f"https://realitify.cz/trh/{slugify_locality(locality)}/{offer}",
        "creator": {"@type": "Organization", "name": "Realitify"},
        "temporalCoverage": "P90D",
        "variableMeasured": ["active_count", "avg_price", "avg_price_per_m2", "median_price"],
    }
    import json

    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <meta name="description" content="Aktuální trh: {html.escape(locality)} {offer_label}. Aktivních nabídek {report.get('active_count')}, medián {_fmt_czk(report.get('median_price'))}, průměr {_fmt_m2(report.get('avg_price_per_m2'))}." />
  <link rel="canonical" href="https://realitify.cz/trh/{slugify_locality(locality)}/{offer}" />
  <link rel="stylesheet" href="/static/site/site.css?v=4" />
  <script type="application/ld+json">{json.dumps(dataset, ensure_ascii=False)}</script>
</head>
<body class="legal-page">
  <header class="navbar">
    <a class="brand" href="/">REALITIFY</a>
    <nav class="menu">
      <a href="/index">Index nájmů</a>
      <a href="/faq">FAQ</a>
      <a href="/mcp-docs">MCP</a>
    </nav>
  </header>
  <header class="legal-header">
    <h1 class="display">{html.escape(locality.upper())} — {offer_label.upper()}</h1>
    <p>Data z agregovaného katalogu Realitify. Aktualizováno: {html.escape(updated)} UTC</p>
  </header>
  <div class="legal-body" style="max-width:900px;margin:0 auto;padding:1rem">
    <ul>
      <li>Aktivní nabídky: <strong>{int(report.get('active_count') or 0)}</strong></li>
      <li>Nové za 7 dní: <strong>{int(report.get('new_last_7_days') or 0)}</strong></li>
      <li>Průměrná cena: <strong>{_fmt_czk(report.get('avg_price'))}</strong></li>
      <li>Medián ceny: <strong>{_fmt_czk(report.get('median_price'))}</strong></li>
      <li>Průměr Kč/m²: <strong>{_fmt_m2(report.get('avg_price_per_m2'))}</strong></li>
      <li>Medián Kč/m²: <strong>{_fmt_m2(report.get('median_price_per_m2'))}</strong></li>
    </ul>
    <h2>Podle dispozice</h2>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Dispozice</th><th>Počet</th><th>Průměr cena</th><th>Průměr Kč/m²</th></tr></thead>
      <tbody>{rows or '<tr><td colspan="4">Bez dat</td></tr>'}</tbody>
    </table>
    <h2>Vývoj (posledních až 90 dní, po měsících)</h2>
    <ul>{hist or '<li>Bez historie</li>'}</ul>
    <p><a href="/nabidka">Otevřít aktuální nabídky v Realitify</a> ·
       <a href="https://mcp.realitify.cz/mcp">MCP server</a></p>
  </div>
</body>
</html>"""


def render_index(store: Store) -> str:
    cz = store.catalog_stats("Praha", "pronajem")  # baseline; also fetch Brno etc
    praha = cz
    brno = store.catalog_stats("Brno", "pronajem")
    ostrava = store.catalog_stats("Ostrava", "pronajem")
    today = date.today().isoformat()
    import json

    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"Realitify index nájmů {today}",
        "description": "Souhrn průměrných a mediánových nájmů z agregovaného katalogu Realitify.",
        "url": "https://realitify.cz/index",
        "creator": {"@type": "Organization", "name": "Realitify", "url": "https://realitify.cz"},
        "dateModified": today,
    }
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Realitify index nájmů — {today}</title>
  <meta name="description" content="Citovatelný index nájmů Realitify: Praha medián {_fmt_czk(praha.get('median_price'))}, Brno {_fmt_czk(brno.get('median_price'))}. Datum {today}." />
  <link rel="canonical" href="https://realitify.cz/index" />
  <link rel="stylesheet" href="/static/site/site.css?v=4" />
  <script type="application/ld+json">{json.dumps(dataset, ensure_ascii=False)}</script>
</head>
<body class="legal-page">
  <header class="navbar"><a class="brand" href="/">REALITIFY</a></header>
  <header class="legal-header">
    <h1 class="display">REALITIFY INDEX NÁJMŮ</h1>
    <p>Souhrn aktivních nabídek pronájmu. Datum: {today}. Zdroj: katalog Realitify.</p>
  </header>
  <div class="legal-body" style="max-width:900px;margin:0 auto;padding:1rem">
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Lokalita</th><th>Aktivní</th><th>Medián</th><th>Průměr</th><th>Medián Kč/m²</th></tr></thead>
      <tbody>
        <tr><td>Praha</td><td>{praha.get('active_count')}</td><td>{_fmt_czk(praha.get('median_price'))}</td><td>{_fmt_czk(praha.get('avg_price'))}</td><td>{_fmt_m2(praha.get('median_price_per_m2'))}</td></tr>
        <tr><td>Brno</td><td>{brno.get('active_count')}</td><td>{_fmt_czk(brno.get('median_price'))}</td><td>{_fmt_czk(brno.get('avg_price'))}</td><td>{_fmt_m2(brno.get('median_price_per_m2'))}</td></tr>
        <tr><td>Ostrava</td><td>{ostrava.get('active_count')}</td><td>{_fmt_czk(ostrava.get('median_price'))}</td><td>{_fmt_czk(ostrava.get('avg_price'))}</td><td>{_fmt_m2(ostrava.get('median_price_per_m2'))}</td></tr>
      </tbody>
    </table>
    <p><a href="/index.csv">Stáhnout CSV</a> · <a href="/trh/praha/pronajem">Detail Praha</a> · <a href="/faq">FAQ</a></p>
  </div>
</body>
</html>"""


def index_csv(store: Store) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["locality", "offer", "active_count", "median_price", "avg_price", "median_price_per_m2", "avg_price_per_m2", "as_of"])
    as_of = date.today().isoformat()
    for loc in ("Praha", "Brno", "Ostrava", "Plzeň", "Olomouc"):
        try:
            s = store.catalog_stats(loc, "pronajem")
        except ValueError:
            continue
        writer.writerow([
            loc, "pronajem", s.get("active_count"), s.get("median_price"), s.get("avg_price"),
            s.get("median_price_per_m2"), s.get("avg_price_per_m2"), as_of,
        ])
    return buf.getvalue()


def render_faq(store: Store) -> str:
    praha = store.catalog_stats("Praha", "pronajem", "2+kk")
    import json

    faqs = [
        {
            "q": "Jak rychle sehnat byt v Praze?",
            "a": (
                f"Sledujte nové nabídky průběžně — v katalogu Realitify je teď "
                f"{praha.get('active_count')} aktivních 2+kk v Praze. "
                "Placené hlídání posílá upozornění během desítek sekund po objevení inzerátu."
            ),
        },
        {
            "q": "Jak být první u nové nabídky?",
            "a": "Nastavte filtr (lokalita, dispozice, max. cena) a zapněte notifikace. Realitify agreguje portály a hlásí nové first_seen záznamy dřív, než je většina lidí ručně projde.",
        },
        {
            "q": "Kolik stojí pronájem 2+kk v Praze?",
            "a": (
                f"Podle aktuálních dat Realitify je medián {_fmt_czk(praha.get('median_price'))} "
                f"a průměr {_fmt_czk(praha.get('avg_price'))} "
                f"(vzorek {praha.get('sample_size_price')} nabídek, datum {date.today().isoformat()})."
            ),
        },
        {
            "q": "Jak poznat předraženou nabídku?",
            "a": "Porovnejte cenu s mediánem a průměrem za m² ve stejné lokalitě a dispozici (nástroj price_check v MCP nebo stránky /trh/…). Odchylka nad +10–15 % vůči průměru si zaslouží vysvětlení (stav, lokalita, vybavení).",
        },
    ]
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in faqs
        ],
    }
    body = "".join(
        f"<section><h2>{html.escape(f['q'])}</h2><p>{html.escape(f['a'])}</p></section>" for f in faqs
    )
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FAQ — Realitify</title>
  <meta name="description" content="Odpovědi: jak sehnat byt v Praze, být první u nabídky, kolik stojí 2+kk, jak poznat předražený nájem. S živými čísly z katalogu." />
  <link rel="canonical" href="https://realitify.cz/faq" />
  <link rel="stylesheet" href="/static/site/site.css?v=4" />
  <script type="application/ld+json">{json.dumps(faq_ld, ensure_ascii=False)}</script>
</head>
<body class="legal-page">
  <header class="navbar"><a class="brand" href="/">REALITIFY</a></header>
  <header class="legal-header"><h1 class="display">FAQ</h1></header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">{body}</div>
</body>
</html>"""


def llms_txt() -> str:
    return """# Realitify

> Czech real-estate listing aggregator and alert service.

Realitify monitors major Czech property portals and lets users watch for new rentals and sales.
Public read-only MCP server for AI assistants: https://mcp.realitify.cz/mcp
Docs: https://realitify.cz/mcp-docs

## Who it is for
People searching for flats to rent or buy in Czechia (Praha, Brno, Ostrava, and other cities).
AI assistants that need current listing data, price stats, or newly published ads.

## What it aggregates
Listings from Czech portals including Sreality, Bezrealitky, iDNES Reality, Bazoš and others tracked by Realitify scrapers.

## Plans (CZK / month)
- Free: 1 watch filter, main portals, email support
- Start: 149 CZK, up to 10 watches, faster alerts
- PRO: 349 CZK, unlimited watches, Discord/push, MCP for logged-in agents
- Individual: custom

## Public MCP tools
search_listings, new_listings, get_listing, locality_stats, price_check, compare_localities

## Key pages
- https://realitify.cz/
- https://realitify.cz/index (rent index)
- https://realitify.cz/faq
- https://realitify.cz/trh/praha/pronajem
- https://realitify.cz/privacy
- https://realitify.cz/terms

## Contact
podpora@realitify.cz
"""
