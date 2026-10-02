"""Server-rendered market overview pages for AI/SEO (HTML in response, not JS)."""

from __future__ import annotations

import csv
import html
import io
from datetime import date
from typing import Any

from app.locality_normalize import _fold, normalize_locality
from app.store import PRAGUE_DISTRICTS, Store

# Cities / numbered city parts candidates (pages only if ≥20 active for that offer).
MARKET_CITIES: list[str] = (
    ["Praha"]
    + [f"Praha {n}" for n in range(1, 23)]
    + [
        "Brno",
        "Ostrava",
        "Plzeň",
        "Olomouc",
        "Liberec",
        "Hradec Králové",
        "Pardubice",
        "České Budějovice",
        "Ústí nad Labem",
        "Zlín",
        "Jihlava",
        "Karlovy Vary",
    ]
)

# Prague neighborhoods reliably present as text in listings.locality
# (typically "Praha N - Název"). Filtered with locality LIKE name AND LIKE '%Praha%'.
# Excluded after data check: Letná (almost no Praha+Letná rows; Děčín-Letná noise),
# Bubny (0 rows).
MARKET_NEIGHBORHOODS: list[str] = [
    "Smíchov",
    "Vinohrady",
    "Žižkov",
    "Karlín",
    "Holešovice",
    "Dejvice",
    "Nusle",
    "Vršovice",
    "Libeň",
    "Strašnice",
    "Stodůlky",
    "Chodov",
    "Košíře",
    "Bubeneč",
    "Břevnov",
    "Prosek",
    "Vysočany",
    "Michle",
    "Malešice",
    "Záběhlice",
    "Krč",
    "Podolí",
    "Jinonice",
    "Radlice",
    "Hlubočepy",
    "Motol",
    "Braník",
    "Kunratice",
    "Hloubětín",
    "Kbely",
    "Bohnice",
    "Kobylisy",
    "Čimice",
    "Ďáblice",
    "Střížkov",
    "Nové Město",
    "Malá Strana",
    "Staré Město",
    "Josefov",
    "Hradčany",
    "Vyšehrad",
    "Troja",
    "Veleslavín",
    "Vokovice",
    "Liboc",
    "Ruzyně",
    "Suchdol",
    "Nebušice",
    "Háje",
    "Modřany",
    "Řepy",
    "Letňany",
    "Černý Most",
    "Hostivař",
    "Zbraslav",
    "Radotín",
    "Uhříněves",
]

MARKET_LOCALITIES: list[str] = MARKET_CITIES + MARKET_NEIGHBORHOODS

# /trh page hysteresis: create at high water, keep until low water.
MIN_ACTIVE_CREATE = 20
MIN_ACTIVE_KEEP = 12
MIN_ACTIVE = MIN_ACTIVE_CREATE  # backward-compatible alias (= create threshold)

PRICE_METHODOLOGY_CS = (
    "Hlavní metrika tržních stránek Realitify je medián ceny za m² (průměr uvádíme jako doplněk). "
    "Počítáme jen byty (kategorie Byt/Podnájem nebo bez uvedené nebytové kategorie) s plochou 10–500 m²; "
    "u pronájmu s cenou 2 000–300 000 Kč měsíčně; u prodeje s cenou od 300 000 Kč. "
    "Vyřazujeme nebytové kategorie, domy a krátkodobé pronájmy rozpoznané z názvu nebo ceny za den "
    "(např. Airbnb, krátkodobý, /den). Stejný filtr platí pro ukázky nabídek a veřejné MCP vyhledávání. "
    f"Samostatná stránka /trh vznikne při ≥ {MIN_ACTIVE_CREATE} kvalitních nabídkách a zruší se "
    f"(301 na nadřazenou lokalitu) až když počet klesne pod {MIN_ACTIVE_KEEP}."
)


def _parents_for_neighborhood(name: str) -> list[str]:
    parents: list[str] = []
    for num, areas in PRAGUE_DISTRICTS.items():
        if name in areas:
            parents.append(f"Praha {num}")
    return parents


def _children_for_district(district: str) -> list[str]:
    m = __import__("re").match(r"(?i)^praha[-\s]*(\d{1,2})$", district.strip())
    if not m:
        return []
    areas = PRAGUE_DISTRICTS.get(m.group(1)) or []
    return [a for a in areas if a in MARKET_NEIGHBORHOODS]


def slugify_locality(label: str) -> str:
    return _fold(label).replace(" ", "-")


def locality_from_slug(slug: str) -> str:
    """Resolve URL slug to a market locality label without collapsing neighborhoods."""
    raw = (slug or "").replace("-", " ").strip()
    folded = _fold(raw)
    for label in MARKET_LOCALITIES:
        if _fold(label) == folded:
            return label
    # Known neighborhood aliases that normalize to Praha N for MCP search —
    # keep the neighborhood label for /trh/ pages.
    for label in MARKET_NEIGHBORHOODS:
        if _fold(label) == folded:
            return label
    return normalize_locality(raw) or raw.title()


def decide_market_page_active(was_active: bool, quality_count: int) -> bool:
    """Hysteresis: new pages need CREATE, existing stay until below KEEP."""
    if was_active:
        return int(quality_count) >= MIN_ACTIVE_KEEP
    return int(quality_count) >= MIN_ACTIVE_CREATE


def evaluate_market_page(
    store: Store,
    locality: str,
    offer: str,
    disposition: str = "",
) -> tuple[bool, int]:
    """Update DB state for one /trh page and return (active, quality_count)."""
    try:
        count = int(store.catalog_active_count(locality, offer, disposition, quality=True))
    except ValueError:
        count = 0
    prev = store.get_market_seo_page(locality, offer, disposition)
    was_active = bool(prev and prev.get("active"))
    active = decide_market_page_active(was_active, count)
    store.set_market_seo_page(
        locality,
        offer,
        disposition,
        active=active,
        quality_count=count,
    )
    return active, count


def _active_count(store: Store, locality: str, offer: str) -> int:
    try:
        return int(store.catalog_active_count(locality, offer, quality=True))
    except ValueError:
        return 0


def market_inventory(store: Store) -> dict[str, list[dict[str, Any]]]:
    """Classify candidates with hysteresis; persist active state in DB."""
    included: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for loc in MARKET_LOCALITIES:
        kind = "neighborhood" if loc in MARKET_NEIGHBORHOODS else "city"
        for offer in ("pronajem", "prodej"):
            active, count = evaluate_market_page(store, loc, offer)
            row = {
                "locality": loc,
                "offer": offer,
                "active_count": count,
                "kind": kind,
                "path": f"/trh/{slugify_locality(loc)}/{offer}",
                "parents": _parents_for_neighborhood(loc) if kind == "neighborhood" else [],
                "active": active,
            }
            if active:
                included.append(row)
            else:
                skipped.append(row)
    return {"included": included, "skipped": skipped}


def list_market_paths(store: Store) -> list[tuple[str, str, str]]:
    """Return (path, locality, offer) for localities with enough data."""
    inv = market_inventory(store)
    return [(r["path"], r["locality"], r["offer"]) for r in inv["included"]]


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


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


# Portals currently advertised as working on public pages (UlovDomov omitted while unstable).
PUBLIC_PORTAL_LABELS: tuple[str, ...] = (
    "Sreality",
    "Reality.iDNES",
    "Bazoš",
    "ČeskéReality",
    "Bezrealitky",
    "Annonce",
    "M&M Reality",
    "RE/MAX",
    "Reality.cz",
)

DISCLAIMER_CS = (
    "Realitify (realitify.cz) nesouvisí se společností Realtify ani PriceHubble."
)
DISCLAIMER_EN = (
    "Realitify (realitify.cz) is not affiliated with Realtify or PriceHubble."
)


def public_portals_sentence(*, oxford: bool = True) -> str:
    labels = list(PUBLIC_PORTAL_LABELS)
    if len(labels) <= 1:
        return labels[0] if labels else ""
    if oxford:
        return ", ".join(labels[:-1]) + " a " + labels[-1]
    return ", ".join(labels)


def _sample_listing_items(store: Store, *, locality: str = "", offer: str = "", q: str = "", limit: int = 12) -> list[dict[str, Any]]:
    filters: dict[str, Any] = {
        "sort": "newest",
        "limit": limit,
        "offset": 0,
        "facets": "0",
        "listing_quality": "apartment",
    }
    if locality:
        filters["district"] = locality
    if offer:
        filters["offer"] = offer
    if q:
        filters["q"] = q
    try:
        data = store.catalog(filters)
    except Exception:
        return []
    items = []
    for item in data.get("items") or []:
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        price = str(item.get("price_label") or "").strip()
        if not price and item.get("price_czk") is not None:
            price = _fmt_czk(item.get("price_czk"))
        items.append(
            {
                "name": str(item.get("name") or item.get("locality") or "Nabídka").strip(),
                "locality": str(item.get("locality") or "").strip(),
                "disposition": str(item.get("disposition") or "").strip(),
                "price": price,
                "portal": str(item.get("portal") or "").strip(),
                "url": url,
            }
        )
        if len(items) >= limit:
            break
    return items


def _listings_html(items: list[dict[str, Any]], *, empty: str) -> str:
    if not items:
        return f"<p>{html.escape(empty)}</p>"
    rows = []
    for item in items:
        meta_bits = [b for b in (item.get("locality"), item.get("disposition"), item.get("portal"), item.get("price")) if b]
        meta = " · ".join(html.escape(str(b)) for b in meta_bits)
        rows.append(
            "<li>"
            f'<a href="{html.escape(item["url"])}" rel="noopener noreferrer">'
            f"{html.escape(item['name'])}</a>"
            f"<br /><span>{meta}</span>"
            "</li>"
        )
    return "<ol>" + "".join(rows) + "</ol>"


def _nav_links_html(store: Store, locality: str, offer: str) -> str:
    parts: list[str] = []
    if locality in MARKET_NEIGHBORHOODS:
        parents = _parents_for_neighborhood(locality)
        for parent in parents:
            active, _ = evaluate_market_page(store, parent, offer)
            if active:
                parts.append(
                    f'<a href="/trh/{slugify_locality(parent)}/{offer}">'
                    f"{html.escape(parent)}</a>"
                )
        if parts:
            return "<p>Městská část: " + ", ".join(parts) + "</p>"
        if parents:
            return "<p>Městská část: " + ", ".join(html.escape(p) for p in parents) + " (bez samostatné stránky)</p>"
        return ""
    children = _children_for_district(locality)
    links: list[str] = []
    for child in children:
        active, _ = evaluate_market_page(store, child, offer)
        if active:
            links.append(
                f'<a href="/trh/{slugify_locality(child)}/{offer}">'
                f"{html.escape(child)}</a>"
            )
    if links:
        return "<p>Čtvrti: " + ", ".join(links) + "</p>"
    return ""


def render_prehled(store: Store, locality: str, offer: str, disposition: str = "") -> str:
    from app import market_seo

    return market_seo.render_trh_page(store, locality, offer, disposition)


def render_index(store: Store) -> str:
    praha = store.catalog_stats("Praha", "pronajem")
    brno = store.catalog_stats("Brno", "pronajem")
    ostrava = store.catalog_stats("Ostrava", "pronajem")
    today = date.today().isoformat()
    current_month = date.today().strftime("%Y-%m")
    import json

    try:
        months = store.list_index_months(completed_only=True)
    except Exception:
        months = []
    archive = " · ".join(f'<a href="/index/{html.escape(m)}">{html.escape(m)}</a>' for m in months[:18])
    try:
        current_rows = store.index_month_city_stats(current_month, allow_incomplete=True)
    except Exception:
        current_rows = []
    current_table = "".join(
        f"<tr><td>{html.escape(r['locality'])}</td><td>{_fmt_int(r['new_count'])}</td>"
        f"<td>{_fmt_czk(r.get('median_price'))}</td>"
        f"<td>{_fmt_m2(r.get('median_price_per_m2'))}</td>"
        f"<td>{_fmt_m2(r.get('avg_price_per_m2'))}</td></tr>"
        for r in current_rows
    )
    med_m2 = _fmt_m2(praha.get("median_price_per_m2"))
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"Realitify index nájmů {today}",
        "description": f"Souhrn mediánových nájmů z katalogu Realitify. Praha medián Kč/m² {med_m2}.",
        "url": "https://realitify.cz/index",
        "creator": {"@type": "Organization", "name": "Realitify", "url": "https://realitify.cz"},
        "dateModified": today,
    }
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>Realitify index nájmů – průběžně k {today}</title>
  <meta name="description" content="Citovatelný index nájmů Realitify: Praha medián {_fmt_czk(praha.get('median_price'))}, medián {_fmt_m2(praha.get('median_price_per_m2'))}. Průběžně k {today}." />
  <link rel="canonical" href="https://realitify.cz/index" />
  <link rel="stylesheet" href="/static/site/site.css?v=9" />
  <script type="application/ld+json">{json.dumps(dataset, ensure_ascii=False)}</script>
</head>
<body class="legal-page">
  <header class="navbar">
    <a class="brand" href="/">REALITIFY</a>
    <nav class="menu">
      <a href="/trh">Trh</a>
      <a href="/index">Index</a>
      <a href="/o-nas">O nás</a>
    </nav>
  </header>
  <header class="legal-header">
    <h1 class="display">REALITIFY INDEX NÁJMŮ</h1>
    <p>Souhrn aktivních nabídek pronájmu. Průběžně k {today}. Zdroj: katalog Realitify.</p>
  </header>
  <div class="legal-body" style="max-width:900px;margin:0 auto;padding:1rem">
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Lokalita</th><th>Aktivní</th><th>Medián</th><th>Průměr</th><th>Medián Kč/m²</th><th>Průměr Kč/m²</th></tr></thead>
      <tbody>
        <tr><td>Praha</td><td>{praha.get('active_count')}</td><td>{_fmt_czk(praha.get('median_price'))}</td><td>{_fmt_czk(praha.get('avg_price'))}</td><td>{_fmt_m2(praha.get('median_price_per_m2'))}</td><td>{_fmt_m2(praha.get('avg_price_per_m2'))}</td></tr>
        <tr><td>Brno</td><td>{brno.get('active_count')}</td><td>{_fmt_czk(brno.get('median_price'))}</td><td>{_fmt_czk(brno.get('avg_price'))}</td><td>{_fmt_m2(brno.get('median_price_per_m2'))}</td><td>{_fmt_m2(brno.get('avg_price_per_m2'))}</td></tr>
        <tr><td>Ostrava</td><td>{ostrava.get('active_count')}</td><td>{_fmt_czk(ostrava.get('median_price'))}</td><td>{_fmt_czk(ostrava.get('avg_price'))}</td><td>{_fmt_m2(ostrava.get('median_price_per_m2'))}</td><td>{_fmt_m2(ostrava.get('avg_price_per_m2'))}</td></tr>
      </tbody>
    </table>
    <h2>Aktuální měsíc ({html.escape(current_month)}) – průběžně k {today}</h2>
    <p>Neukončený měsíc nemá trvalou archivní URL; čísla se mění. Po skončení měsíce vznikne /index/{html.escape(current_month)}.</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Lokalita</th><th>Nové</th><th>Medián</th><th>Medián Kč/m²</th><th>Průměr Kč/m²</th></tr></thead>
      <tbody>{current_table or '<tr><td colspan="5">Bez dat</td></tr>'}</tbody>
    </table>
    <h2>Měsíční archiv (ukončené měsíce)</h2>
    <p>Trvalé URL pro citace: {archive or 'zatím bez archivních měsíců'}.</p>
    <h2>Metodika výpočtu Kč/m²</h2>
    <p>{html.escape(PRICE_METHODOLOGY_CS)}</p>
    <p><a href="/index.csv">Stáhnout CSV</a> · <a href="/trh/praha/pronajem">Detail Praha</a> · <a href="/trh">Trh</a> · <a href="/faq">FAQ</a></p>
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
    portals = public_portals_sentence()
    import json

    faqs = [
        {
            "q": "Co je Realitify?",
            "a": (
                "Realitify je agregátor nabídek bytů a domů z českých realitních portálů. "
                f"Sjednocuje inzeráty ze {portals} a dalších sledovaných zdrojů, "
                "ukazuje tržní statistiky a umí hlídat nové nabídky podle filtrů."
            ),
        },
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
                f"Podle aktuálních dat Realitify je medián {_fmt_czk(praha.get('median_price'))}, "
                f"medián {_fmt_m2(praha.get('median_price_per_m2'))} "
                f"(průměr {_fmt_czk(praha.get('avg_price'))}, {_fmt_m2(praha.get('avg_price_per_m2'))}; "
                f"vzorek {praha.get('sample_size_price_m2')} nabídek po filtru kvality, datum {date.today().isoformat()})."
            ),
        },
        {
            "q": "Jak poznat předraženou nabídku?",
            "a": (
                "Porovnejte cenu s mediánem Kč/m² ve stejné lokalitě a dispozici "
                "(nástroj price_check v MCP nebo stránky /trh/). "
                "Odchylka nad +10–15 % vůči mediánu si zaslouží vysvětlení (stav, lokalita, vybavení)."
            ),
        },
        {
            "q": "Jak Realitify počítá cenu za m²?",
            "a": PRICE_METHODOLOGY_CS,
        },
        {
            "q": "Kdy vznikne a zanikne stránka /trh?",
            "a": (
                f"Nová lokalitní stránka vznikne při ≥ {MIN_ACTIVE_CREATE} kvalitních nabídkách bytů. "
                f"Existující stránka se zruší (přesměrování 301 na nadřazenou lokalitu) až když počet "
                f"klesne pod {MIN_ACTIVE_KEEP}. Stav aktivních stránek ukládáme v databázi, aby bylo "
                "rozhodnutí deterministické."
            ),
        },
        {
            "q": "Souvisí Realitify se společností Realtify nebo PriceHubble?",
            "a": DISCLAIMER_CS,
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
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>FAQ – Realitify</title>
  <meta name="description" content="Co je Realitify, jak počítáme medián Kč/m², jak sehnat byt v Praze a jak poznat předražený nájem. S živými čísly z katalogu." />
  <link rel="canonical" href="https://realitify.cz/faq" />
  <link rel="stylesheet" href="/static/site/site.css?v=8" />
  <script type="application/ld+json">{json.dumps(faq_ld, ensure_ascii=False)}</script>
</head>
<body class="legal-page">
  <header class="navbar"><a class="brand" href="/">REALITIFY</a></header>
  <header class="legal-header"><h1 class="display">FAQ</h1></header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">{body}</div>
</body>
</html>"""


def render_search(store: Store, q: str = "") -> str:
    query = (q or "").strip()[:120]
    items = _sample_listing_items(store, q=query, offer="pronajem", limit=20) if query else _sample_listing_items(store, offer="pronajem", limit=20)
    listings = _listings_html(items, empty="Žádné nabídky pro tento dotaz.")
    title_q = html.escape(query) if query else "aktuální nabídky"
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>Hledat nabídky – Realitify</title>
  <meta name="description" content="Veřejné vyhledávání v agregovaném katalogu Realitify. Výsledky s odkazy na detaily u zdrojových portálů." />
  <meta name="robots" content="noindex, follow" />
  <link rel="canonical" href="https://realitify.cz/hledat" />
  <link rel="stylesheet" href="/static/site/site.css?v=8" />
</head>
<body class="legal-page">
  <header class="navbar">
    <a class="brand" href="/">REALITIFY</a>
    <nav class="menu">
      <a href="/trh/praha/pronajem">Praha</a>
      <a href="/index">Index</a>
      <a href="/o-nas">O nás</a>
    </nav>
  </header>
  <header class="legal-header">
    <h1 class="display">HLEDAT NABÍDKY</h1>
    <p>Realitify agreguje nabídky bytů a domů z českých realitních portálů. Výsledky: {title_q}.</p>
  </header>
  <div class="legal-body" style="max-width:900px;margin:0 auto;padding:1rem">
    <form method="get" action="/hledat" style="margin-bottom:1.5rem;display:flex;gap:8px;flex-wrap:wrap">
      <label for="q" style="flex:1;min-width:220px">
        <span class="field-label">Lokalita, dispozice nebo text</span>
        <input id="q" name="q" type="search" value="{html.escape(query)}" placeholder="např. Praha 5 2+kk" style="width:100%;padding:10px;border:1px solid #d0d4cd;border-radius:8px" />
      </label>
      <button class="pill" type="submit" style="align-self:flex-end;padding:10px 20px">Hledat</button>
    </form>
    <h2>Výsledky</h2>
    {listings}
    <p><a href="/trh/praha/pronajem">Přehled trhu Praha</a> · <a href="/o-nas">O Realitify</a></p>
    <p><small>{html.escape(DISCLAIMER_CS)}</small></p>
  </div>
</body>
</html>"""


def render_about_cs() -> str:
    portals = public_portals_sentence()
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>O nás – Realitify</title>
  <meta name="description" content="Realitify je agregátor nabídek bytů a domů z českých realitních portálů. Provozuje Jiří Kolb (OSVČ), IČO 21527059." />
  <link rel="canonical" href="https://realitify.cz/o-nas" />
  <link rel="alternate" hreflang="cs" href="https://realitify.cz/o-nas" />
  <link rel="alternate" hreflang="en" href="https://realitify.cz/about" />
  <link rel="alternate" hreflang="x-default" href="https://realitify.cz/o-nas" />
  <link rel="stylesheet" href="/static/site/site.css?v=8" />
</head>
<body class="legal-page">
  <header class="navbar">
    <a class="brand" href="/">REALITIFY</a>
    <nav class="menu">
      <a href="/trh">Trh</a>
      <a href="/hledat">Hledat</a>
      <a href="/faq">FAQ</a>
      <a href="/about">English</a>
    </nav>
  </header>
  <header class="legal-header">
    <h1 class="display">O NÁS</h1>
    <p>Realitify – všechny nabídky bytů a domů z českých realitních portálů na jednom místě.</p>
  </header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">
    <h2>Co je Realitify</h2>
    <p>Realitify je český agregátor realitních nabídek. Stahuje a sjednocuje inzeráty bytů a domů z hlavních portálů ({html.escape(portals)}), odstraňuje duplicity a umožňuje prohlížet trh, porovnávat ceny a hlídat nové nabídky podle filtrů.</p>
    <h2>Kdo provozuje službu</h2>
    <p>Provozovatel: <strong>Jiří Kolb</strong>, podnikající fyzická osoba (OSVČ), IČO <strong>21527059</strong>, sídlo <strong>Umělecká 618/7, 170 00 Praha 7 – Holešovice</strong>. Kontakt: <a href="mailto:podpora@realitify.cz">podpora@realitify.cz</a>.</p>
    <h2>Od kdy</h2>
    <p>Služba Realitify je v provozu od roku 2025.</p>
    <h2>Jak funguje agregace</h2>
    <p>Scrapery průběžně procházejí veřejné výpisy portálů, ukládají aktivní inzeráty do katalogu a aktualizují first_seen / last_seen. Veřejné stránky (/trh/…, /index, /hledat) a MCP server čtou z tohoto katalogu. Placené hlídání posílá notifikace při nových shodách s nastavenými filtry.</p>
    <p>{html.escape(DISCLAIMER_CS)}</p>
  </div>
</body>
</html>"""


def render_about_en() -> str:
    portals = ", ".join(PUBLIC_PORTAL_LABELS[:-1]) + ", and " + PUBLIC_PORTAL_LABELS[-1]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>About – Realitify</title>
  <meta name="description" content="Realitify is a Czech real-estate listings aggregator. Operated by Jiří Kolb (sole trader), Company ID 21527059." />
  <link rel="canonical" href="https://realitify.cz/about" />
  <link rel="alternate" hreflang="en" href="https://realitify.cz/about" />
  <link rel="alternate" hreflang="cs" href="https://realitify.cz/o-nas" />
  <link rel="alternate" hreflang="x-default" href="https://realitify.cz/o-nas" />
  <link rel="stylesheet" href="/static/site/site.css?v=8" />
</head>
<body class="legal-page">
  <header class="navbar">
    <a class="brand" href="/">REALITIFY</a>
    <nav class="menu">
      <a href="/trh">Market</a>
      <a href="/hledat">Search</a>
      <a href="/faq">FAQ</a>
      <a href="/o-nas">Česky</a>
    </nav>
  </header>
  <header class="legal-header">
    <h1 class="display">ABOUT</h1>
    <p>Realitify — Czech apartment and house listings from major portals, in one place.</p>
  </header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">
    <h2>What Realitify is</h2>
    <p>Realitify is a Czech real-estate listings aggregator. It collects and deduplicates ads for flats and houses from major portals ({html.escape(portals)}), exposes market stats, and can watch for new matches against user filters.</p>
    <h2>Operator</h2>
    <p>Operator: <strong>Jiří Kolb</strong>, sole trader (OSVČ), Company ID (IČO) <strong>21527059</strong>, registered office <strong>Umělecká 618/7, 170 00 Praha 7 – Holešovice</strong>, Czech Republic. Contact: <a href="mailto:podpora@realitify.cz">podpora@realitify.cz</a>.</p>
    <h2>Since when</h2>
    <p>Realitify has been operating since 2025.</p>
    <h2>How aggregation works</h2>
    <p>Scrapers continuously read public portal listings into a catalog (first_seen / last_seen). Public pages (/trh/…, /index, /hledat) and the MCP server read from that catalog. Paid watches send alerts when new listings match a filter.</p>
    <p>{html.escape(DISCLAIMER_EN)}</p>
  </div>
</body>
</html>"""


def llms_txt() -> str:
    portals = public_portals_sentence()
    return f"""# Realitify

> Czech real-estate listing aggregator for apartments and houses from major Czech portals, with alerts and a public read-only MCP connector.

Realitify aggregates active listings from {portals} (UlovDomov currently omitted while unstable). It is operated by Jiří Kolb (sole trader / OSVČ, IČO 21527059) since 2025. {DISCLAIMER_EN}

## Docs

- [MCP docs](https://realitify.cz/mcp-docs): Public MCP connector documentation
- [FAQ](https://realitify.cz/faq): Frequently asked questions about Realitify
- [About](https://realitify.cz/o-nas): What Realitify is and who operates it
- [About (EN)](https://realitify.cz/about): English about page

## Data

- [Home](https://realitify.cz/): Product landing page
- [Market hub](https://realitify.cz/trh): Locality market pages (SSR)
- [Rent index](https://realitify.cz/index): Monthly rent index and archive
- [Search](https://realitify.cz/hledat): Public SSR listing search with links to source details
- [Rent index](https://realitify.cz/index): Czech rent index overview
- [Praha market](https://realitify.cz/trh/praha/pronajem): Praha rental market page with sample listings
- [Smíchov market](https://realitify.cz/trh/smichov/pronajem): Smíchov rental market page

## MCP

- [MCP endpoint](https://mcp.realitify.cz/mcp): Streamable HTTP MCP server (search_listings, new_listings, get_listing, locality_stats, price_check, compare_localities)

## Legal

- [Privacy](https://realitify.cz/privacy): Privacy policy
- [Terms](https://realitify.cz/terms): Terms of service
"""
