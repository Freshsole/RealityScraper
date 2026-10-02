"""Server-rendered market overview pages for AI/SEO (HTML in response, not JS)."""

from __future__ import annotations

import csv
import html
import io
from datetime import date
from typing import Any

from app import facts as product_facts
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
_METHOD = product_facts.get_facts().get("methodology") or {}
MIN_ACTIVE_CREATE = int(_METHOD.get("trh_create_min") or 20)
MIN_ACTIVE_KEEP = int(_METHOD.get("trh_keep_min") or 12)
MIN_ACTIVE = MIN_ACTIVE_CREATE  # backward-compatible alias (= create threshold)

PRICE_METHODOLOGY_CS = product_facts.methodology_cs()


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


# Portals currently advertised as working on public pages (from content/facts.yaml).
PUBLIC_PORTAL_LABELS: tuple[str, ...] = product_facts.portal_labels()

DISCLAIMER_CS = product_facts.disclaimer_cs()
DISCLAIMER_EN = product_facts.disclaimer_en()


def public_portals_sentence(*, oxford: bool = True) -> str:
    del oxford  # kept for call-site compatibility
    return product_facts.portals_sentence(lang="cs")


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
    today_cs = product_facts.format_date_cs(date.today())
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
    updated_label = product_facts.data_updated_label(date.today())
    cite_praha = (
        f"K {today_cs} je medián pronájmu bytů v Praze {_fmt_czk(praha.get('median_price'))} "
        f"({_fmt_m2(praha.get('median_price_per_m2'))}; {_fmt_int(praha.get('active_count'))} nabídek)."
    )
    cite_brno = (
        f"K {today_cs} je medián pronájmu bytů v Brně {_fmt_czk(brno.get('median_price'))} "
        f"({_fmt_m2(brno.get('median_price_per_m2'))}; {_fmt_int(brno.get('active_count'))} nabídek)."
    )
    cite_ostrava = (
        f"K {today_cs} je medián pronájmu bytů v Ostravě {_fmt_czk(ostrava.get('median_price'))} "
        f"({_fmt_m2(ostrava.get('median_price_per_m2'))}; {_fmt_int(ostrava.get('active_count'))} nabídek)."
    )
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": f"Realitify index nájmů {today}",
        "description": f"Souhrn mediánových nájmů z katalogu Realitify. Praha medián Kč/m² {med_m2}.",
        "url": "https://realitify.cz/index",
        "creator": product_facts.dataset_creator(),
        "dateModified": today,
        "temporalCoverage": today,
        "license": product_facts.dataset_license(),
        "distribution": product_facts.dataset_distribution(
            "https://realitify.cz/index.csv", name="Realitify rent index CSV"
        ),
    }
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>Realitify index nájmů – {today_cs}</title>
  <meta name="description" content="{html.escape(cite_praha)}" />
  <link rel="canonical" href="https://realitify.cz/index" />
  <link rel="alternate" type="text/markdown" href="https://realitify.cz/index.md" />
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
    <p>Souhrn aktivních nabídek pronájmu. {html.escape(updated_label)}. Zdroj: katalog Realitify.</p>
  </header>
  <div class="legal-body" style="max-width:900px;margin:0 auto;padding:1rem">
    <p>{html.escape(cite_praha)}</p>
    <p>{html.escape(cite_brno)}</p>
    <p>{html.escape(cite_ostrava)}</p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead><tr><th>Lokalita</th><th>Aktivní</th><th>Medián</th><th>Průměr</th><th>Medián Kč/m²</th><th>Průměr Kč/m²</th></tr></thead>
      <tbody>
        <tr><td>Praha</td><td>{praha.get('active_count')}</td><td>{_fmt_czk(praha.get('median_price'))}</td><td>{_fmt_czk(praha.get('avg_price'))}</td><td>{_fmt_m2(praha.get('median_price_per_m2'))}</td><td>{_fmt_m2(praha.get('avg_price_per_m2'))}</td></tr>
        <tr><td>Brno</td><td>{brno.get('active_count')}</td><td>{_fmt_czk(brno.get('median_price'))}</td><td>{_fmt_czk(brno.get('avg_price'))}</td><td>{_fmt_m2(brno.get('median_price_per_m2'))}</td><td>{_fmt_m2(brno.get('avg_price_per_m2'))}</td></tr>
        <tr><td>Ostrava</td><td>{ostrava.get('active_count')}</td><td>{_fmt_czk(ostrava.get('median_price'))}</td><td>{_fmt_czk(ostrava.get('avg_price'))}</td><td>{_fmt_m2(ostrava.get('median_price_per_m2'))}</td><td>{_fmt_m2(ostrava.get('avg_price_per_m2'))}</td></tr>
      </tbody>
    </table>
    <h2>Aktuální měsíc ({html.escape(current_month)}) – {html.escape(updated_label)}</h2>
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
    <p><small>{html.escape(DISCLAIMER_CS)}</small></p>
  </div>
</body>
</html>"""


def render_index_md(store: Store) -> str:
    praha = store.catalog_stats("Praha", "pronajem")
    brno = store.catalog_stats("Brno", "pronajem")
    ostrava = store.catalog_stats("Ostrava", "pronajem")
    today_cs = product_facts.format_date_cs(date.today())
    updated = product_facts.data_updated_label(date.today())
    return "\n".join(
        [
            "# Realitify index nájmů",
            "",
            updated + ".",
            "",
            (
                f"K {today_cs} je medián pronájmu bytů v Praze {_fmt_czk(praha.get('median_price'))} "
                f"({_fmt_m2(praha.get('median_price_per_m2'))}; {_fmt_int(praha.get('active_count'))} nabídek)."
            ),
            (
                f"K {today_cs} je medián pronájmu bytů v Brně {_fmt_czk(brno.get('median_price'))} "
                f"({_fmt_m2(brno.get('median_price_per_m2'))}; {_fmt_int(brno.get('active_count'))} nabídek)."
            ),
            (
                f"K {today_cs} je medián pronájmu bytů v Ostravě {_fmt_czk(ostrava.get('median_price'))} "
                f"({_fmt_m2(ostrava.get('median_price_per_m2'))}; {_fmt_int(ostrava.get('active_count'))} nabídek)."
            ),
            "",
            "## Metodika",
            "",
            PRICE_METHODOLOGY_CS,
            "",
            DISCLAIMER_CS,
            "",
            "CSV: https://realitify.cz/index.csv",
            "HTML: https://realitify.cz/index",
            "",
        ]
    )


def index_csv(store: Store) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "locality",
            "offer",
            "active_count",
            "median_price",
            "avg_price",
            "median_price_per_m2",
            "avg_price_per_m2",
            "as_of",
        ]
    )
    as_of = date.today().isoformat()
    for loc in ("Praha", "Brno", "Ostrava", "Plzeň", "Olomouc"):
        try:
            s = store.catalog_stats(loc, "pronajem")
        except ValueError:
            continue
        writer.writerow(
            [
                loc,
                "pronajem",
                s.get("active_count"),
                s.get("median_price"),
                s.get("avg_price"),
                s.get("median_price_per_m2"),
                s.get("avg_price_per_m2"),
                as_of,
            ]
        )
    return buf.getvalue()


def _faq_items(store: Store) -> list[dict[str, str]]:
    praha = store.catalog_stats("Praha", "pronajem", "2+kk")
    portals = public_portals_sentence()
    today_cs = product_facts.format_date_cs(date.today())
    facts = product_facts.get_facts()
    plans = facts.get("plans") or {}
    start = plans.get("start") or {}
    pro = plans.get("pro") or {}
    return [
        {
            "q": "Co je Realitify?",
            "a": product_facts.one_liner_cs()
            + f" Agreguje inzeráty ze {portals}.",
        },
        {
            "q": "Jak rychle sehnat byt v Praze?",
            "a": (
                f"K {today_cs} je v katalogu Realitify {_fmt_int(praha.get('active_count'))} "
                f"aktivních nabídek 2+kk v Praze. "
                "Placené hlídání posílá upozornění během desítek sekund po objevení inzerátu."
            ),
        },
        {
            "q": "Jak být první u nové nabídky?",
            "a": (
                "Nastavte filtr (lokalita, dispozice, max. cena) a zapněte notifikace. "
                "Realitify agreguje portály a hlásí nové first_seen záznamy."
            ),
        },
        {
            "q": "Kolik stojí pronájem 2+kk v Praze?",
            "a": (
                f"K {today_cs} je medián pronájmu 2+kk v Praze {_fmt_czk(praha.get('median_price'))} "
                f"({_fmt_m2(praha.get('median_price_per_m2'))}; "
                f"průměr {_fmt_czk(praha.get('avg_price'))}, {_fmt_m2(praha.get('avg_price_per_m2'))}; "
                f"vzorek {_fmt_int(praha.get('sample_size_price_m2'))} nabídek po filtru kvality)."
            ),
        },
        {
            "q": "Jaké jsou tarify?",
            "a": (
                f"Prohlížení katalogu a veřejné MCP je zdarma. "
                f"Start stojí {start.get('price_czk')} Kč/měsíc ({start.get('watch_limit')} hlídacích psů), "
                f"PRO {pro.get('price_czk')} Kč/měsíc (neomezeně hlídacích psů). "
                "INDIVIDUAL je cena dohodou."
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
                f"klesne pod {MIN_ACTIVE_KEEP}."
            ),
        },
        {
            "q": "Souvisí Realitify se společností Realtify nebo PriceHubble?",
            "a": DISCLAIMER_CS,
        },
    ]


def render_faq(store: Store) -> str:
    import json

    faqs = _faq_items(store)
    updated = product_facts.data_updated_label(date.today())
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f["q"],
                "acceptedAnswer": {"@type": "Answer", "text": f["a"]},
            }
            for f in faqs
        ],
        "dateModified": date.today().isoformat(),
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
  <meta name="description" content="Co je Realitify, tarify, medián Kč/m² a metodika. {html.escape(updated)}." />
  <link rel="canonical" href="https://realitify.cz/faq" />
  <link rel="alternate" type="text/markdown" href="https://realitify.cz/faq.md" />
  <link rel="stylesheet" href="/static/site/site.css?v=8" />
  <script type="application/ld+json">{json.dumps(faq_ld, ensure_ascii=False)}</script>
</head>
<body class="legal-page">
  <header class="navbar"><a class="brand" href="/">REALITIFY</a></header>
  <header class="legal-header"><h1 class="display">FAQ</h1><p>{html.escape(updated)}</p></header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">{body}</div>
</body>
</html>"""


def render_faq_md(store: Store) -> str:
    faqs = _faq_items(store)
    lines = ["# FAQ – Realitify", "", product_facts.data_updated_label(date.today()) + ".", ""]
    for f in faqs:
        lines.extend([f"## {f['q']}", "", f["a"], ""])
    lines.append(DISCLAIMER_CS)
    lines.append("")
    return "\n".join(lines)


def render_search(store: Store, q: str = "") -> str:
    query = (q or "").strip()[:120]
    items = (
        _sample_listing_items(store, q=query, offer="pronajem", limit=20)
        if query
        else _sample_listing_items(store, offer="pronajem", limit=20)
    )
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
  <meta name="description" content="Veřejné vyhledávání v agregovaném katalogu Realitify." />
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


def render_about_cs(store: Store | None = None) -> str:
    portals = public_portals_sentence()
    facts = product_facts.get_facts()
    op = facts.get("operator") or {}
    contact = facts.get("contact") or {}
    product = facts.get("product") or {}
    plans = facts.get("plans") or {}
    stats_line = ""
    if store is not None:
        try:
            praha = store.catalog_stats("Praha", "pronajem")
            today_cs = product_facts.format_date_cs(date.today())
            stats_line = (
                f"<p>K {html.escape(today_cs)} je medián pronájmu bytů v Praze "
                f"{html.escape(_fmt_czk(praha.get('median_price')))} "
                f"({html.escape(_fmt_m2(praha.get('median_price_per_m2')))}; "
                f"{html.escape(_fmt_int(praha.get('active_count')))} nabídek).</p>"
            )
        except Exception:
            stats_line = ""
    updated = product_facts.data_updated_label(date.today())
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>O nás – Realitify</title>
  <meta name="description" content="{html.escape(product_facts.one_liner_cs())} Provozuje {html.escape(str(op.get('name')))} ({html.escape(str(op.get('legal_form')))}), IČO {html.escape(str(op.get('ico')))}." />
  <link rel="canonical" href="https://realitify.cz/o-nas" />
  <link rel="alternate" type="text/markdown" href="https://realitify.cz/o-nas.md" />
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
    <p>{html.escape(product_facts.one_liner_cs())}</p>
    <p>{html.escape(updated)}</p>
  </header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">
    <h2>Co je Realitify</h2>
    <p>{html.escape(product_facts.one_liner_cs())} Stahuje a sjednocuje inzeráty z {html.escape(portals)}, odstraňuje duplicity a umožňuje prohlížet trh, porovnávat ceny a hlídat nové nabídky.</p>
    {stats_line}
    <h2>Kdo provozuje službu</h2>
    <p>Provozovatel: <strong>{html.escape(str(op.get('name')))}</strong>, podnikající fyzická osoba ({html.escape(str(op.get('legal_form')))}), IČO <strong>{html.escape(str(op.get('ico')))}</strong>, sídlo <strong>{html.escape(str(op.get('address')))}</strong>. Kontakt: <a href="mailto:{html.escape(str(contact.get('support')))}">{html.escape(str(contact.get('support')))}</a>.</p>
    <h2>Od kdy</h2>
    <p>Služba Realitify je v provozu od roku {html.escape(str(product.get('since_year')))}.</p>
    <h2>Tarify</h2>
    <p>Prohlížení katalogu je zdarma. Start {html.escape(str((plans.get('start') or {}).get('price_czk')))} Kč/měsíc, PRO {html.escape(str((plans.get('pro') or {}).get('price_czk')))} Kč/měsíc. Detail: <a href="/#cenik">ceník</a>.</p>
    <h2>Jak funguje agregace</h2>
    <p>Scrapery průběžně procházejí veřejné výpisy portálů, ukládají aktivní inzeráty do katalogu a aktualizují first_seen / last_seen. Veřejné stránky (/trh/…, /index, /hledat) a MCP server čtou z tohoto katalogu. Placené hlídání posílá notifikace při nových shodách s nastavenými filtry.</p>
    <p>{html.escape(DISCLAIMER_CS)}</p>
  </div>
</body>
</html>"""


def render_about_cs_md(store: Store | None = None) -> str:
    facts = product_facts.get_facts()
    op = facts.get("operator") or {}
    contact = facts.get("contact") or {}
    product = facts.get("product") or {}
    plans = facts.get("plans") or {}
    lines = [
        "# O nás – Realitify",
        "",
        product_facts.data_updated_label(date.today()) + ".",
        "",
        product_facts.one_liner_cs(),
        "",
        f"Provozovatel: {op.get('name')}, {op.get('legal_form')}, IČO {op.get('ico')}, {op.get('address')}. Kontakt: {contact.get('support')}.",
        "",
        f"V provozu od roku {product.get('since_year')}.",
        "",
        (
            f"Tarify: Start {(plans.get('start') or {}).get('price_czk')} Kč/měsíc, "
            f"PRO {(plans.get('pro') or {}).get('price_czk')} Kč/měsíc; prohlížení katalogu zdarma."
        ),
        "",
        f"Portály: {public_portals_sentence()}.",
        "",
    ]
    if store is not None:
        try:
            praha = store.catalog_stats("Praha", "pronajem")
            today_cs = product_facts.format_date_cs(date.today())
            lines.append(
                f"K {today_cs} je medián pronájmu bytů v Praze {_fmt_czk(praha.get('median_price'))} "
                f"({_fmt_m2(praha.get('median_price_per_m2'))}; {_fmt_int(praha.get('active_count'))} nabídek)."
            )
            lines.append("")
        except Exception:
            pass
    lines.extend([DISCLAIMER_CS, ""])
    return "\n".join(lines)


def render_about_en() -> str:
    portals = product_facts.portals_sentence(lang="en")
    facts = product_facts.get_facts()
    op = facts.get("operator") or {}
    contact = facts.get("contact") or {}
    product = facts.get("product") or {}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
  <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
  <title>About – Realitify</title>
  <meta name="description" content="{html.escape(product_facts.one_liner_en())} Operated by {html.escape(str(op.get('name')))}, Company ID {html.escape(str(op.get('ico')))}." />
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
    <p>{html.escape(product_facts.one_liner_en())}</p>
  </header>
  <div class="legal-body" style="max-width:800px;margin:0 auto;padding:1rem">
    <h2>What Realitify is</h2>
    <p>{html.escape(product_facts.one_liner_en())} It collects and deduplicates ads from {html.escape(portals)}.</p>
    <h2>Operator</h2>
    <p>Operator: <strong>{html.escape(str(op.get('name')))}</strong>, sole trader ({html.escape(str(op.get('legal_form')))}), Company ID (IČO) <strong>{html.escape(str(op.get('ico')))}</strong>, registered office <strong>{html.escape(str(op.get('address')))}</strong>, Czech Republic. Contact: <a href="mailto:{html.escape(str(contact.get('support')))}">{html.escape(str(contact.get('support')))}</a>.</p>
    <h2>Since when</h2>
    <p>Realitify has been operating since {html.escape(str(product.get('since_year')))}.</p>
    <h2>How aggregation works</h2>
    <p>Scrapers continuously read public portal listings into a catalog (first_seen / last_seen). Public pages and the MCP server read from that catalog. Paid watches send alerts when new listings match a filter.</p>
    <p>{html.escape(DISCLAIMER_EN)}</p>
  </div>
</body>
</html>"""


def render_home_md(store: Store | None = None) -> str:
    facts = product_facts.get_facts()
    plans = facts.get("plans") or {}
    lines = [
        "# Realitify",
        "",
        product_facts.one_liner_cs(),
        "",
        f"Portály: {public_portals_sentence()}.",
        "",
        "## Tarify",
        "",
        f"- Zdarma: {(plans.get('free') or {}).get('price_czk')} Kč — 1 hlídací pes, 4 hlavní portály",
        f"- Start: {(plans.get('start') or {}).get('price_czk')} Kč/měsíc — 10 hlídacích psů, všechny agregované portály ({len(PUBLIC_PORTAL_LABELS)})",
        f"- PRO: {(plans.get('pro') or {}).get('price_czk')} Kč/měsíc — neomezeně hlídacích psů",
        "",
        f"MCP: {(facts.get('mcp') or {}).get('url')}",
        "",
        product_facts.data_updated_label(date.today()) + ".",
        "",
    ]
    if store is not None:
        try:
            praha = store.catalog_stats("Praha", "pronajem")
            today_cs = product_facts.format_date_cs(date.today())
            lines.insert(
                4,
                (
                    f"K {today_cs} je medián pronájmu bytů v Praze {_fmt_czk(praha.get('median_price'))} "
                    f"({_fmt_m2(praha.get('median_price_per_m2'))}; {_fmt_int(praha.get('active_count'))} nabídek)."
                ),
            )
            lines.insert(5, "")
        except Exception:
            pass
    lines.extend([DISCLAIMER_CS, ""])
    return "\n".join(lines)


def render_mcp_docs_md() -> str:
    facts = product_facts.get_facts()
    mcp = facts.get("mcp") or {}
    tools = mcp.get("tools") or []
    lines = [
        "# Realitify MCP",
        "",
        product_facts.one_liner_en(),
        "",
        f"Server URL: {mcp.get('url')} (Streamable HTTP, no login)",
        f"Health: {mcp.get('health')}",
        f"Units: {mcp.get('units', {}).get('currency')}, {mcp.get('units', {}).get('area')}. Data region: {mcp.get('data_region')}.",
        "",
        "## Tools",
        "",
    ]
    for t in tools:
        lines.append(f"- `{t}`")
    lines.extend(
        [
            "",
            mcp.get("tip_en") or "",
            "",
            DISCLAIMER_EN,
            "",
            f"Docs HTML: {mcp.get('docs')}",
            "",
        ]
    )
    return "\n".join(lines)


def llms_txt(store: Store | None = None) -> str:
    facts = product_facts.get_facts()
    product = facts.get("product") or {}
    op = facts.get("operator") or {}
    mcp = facts.get("mcp") or {}
    urls = facts.get("urls") or {}
    portals = public_portals_sentence()
    omitted = ((facts.get("portals") or {}).get("omitted") or [{}])[0]
    updated = date.today().isoformat()
    market_line = ""
    if store is not None:
        try:
            praha = store.catalog_stats("Praha", "pronajem")
            market_line = (
                f"\nAs of {updated}, Praha rent median is {_fmt_czk(praha.get('median_price'))} "
                f"({_fmt_m2(praha.get('median_price_per_m2'))}; {_fmt_int(praha.get('active_count'))} listings).\n"
            )
        except Exception:
            market_line = ""
    tools = ", ".join(mcp.get("tools") or [])
    return f"""# Realitify

> {product_facts.one_liner_en()}

Last-Updated: {updated}

Realitify aggregates active listings from {portals} ({omitted.get('id')} currently omitted: {omitted.get('reason')}). Operated by {op.get('name')} (sole trader / {op.get('legal_form')}, IČO {op.get('ico')}) since {product.get('since_year')}. {DISCLAIMER_EN}
{market_line}
## Docs

- [MCP docs]({urls.get('mcp_docs')}): Public MCP connector documentation
- [MCP docs (CS)]({mcp.get('docs_cs')}): Czech MCP documentation
- [FAQ]({urls.get('faq')}): Frequently asked questions
- [About]({urls.get('about_cs')}): Operator and product facts
- [About (EN)]({urls.get('about_en')}): English about page
- [Full facts (llms-full.txt)]({urls.get('llms_full')}): Complete factual markdown for AI systems

## Data

- [Home]({urls.get('home')}): Product landing page ([Markdown]({urls.get('home_md')}))
- [Market hub]({urls.get('trh')}): Locality market pages
- [Rent index]({urls.get('index')}): Rent index and archive ([CSV]({urls.get('index_csv')}))
- [Search]({urls.get('search')}): Public SSR listing search
- [Praha market](https://realitify.cz/trh/praha/pronajem): Praha rental market page
- [Smíchov market](https://realitify.cz/trh/smichov/pronajem): Smíchov rental market page

## MCP

- [MCP endpoint]({mcp.get('url')}): Streamable HTTP ({tools})

## Legal

- [Privacy]({urls.get('privacy')})
- [Terms]({urls.get('terms')})
"""


def llms_full_txt(store: Store) -> str:
    facts = product_facts.get_facts()
    product = facts.get("product") or {}
    op = facts.get("operator") or {}
    contact = facts.get("contact") or {}
    plans = facts.get("plans") or {}
    mcp = facts.get("mcp") or {}
    free_vs = facts.get("free_vs_paid") or {}
    updated = date.today().isoformat()
    today_cs = product_facts.format_date_cs(date.today())
    lines: list[str] = [
        "# Realitify — full facts for AI systems",
        "",
        f"Last-Updated: {updated}",
        "",
        "## What it is",
        "",
        product_facts.one_liner_en(),
        product_facts.one_liner_cs(),
        "",
        DISCLAIMER_EN,
        DISCLAIMER_CS,
        "",
        "## Operator",
        "",
        f"- Name: {op.get('name')}",
        f"- Legal form: {op.get('legal_form')}",
        f"- IČO: {op.get('ico')}",
        f"- Address: {op.get('address')}",
        f"- Support: {contact.get('support')}",
        f"- Since: {product.get('since_year')}",
        "",
        "## Portals",
        "",
        f"Currently aggregated: {public_portals_sentence()}.",
    ]
    for row in (facts.get("portals") or {}).get("omitted") or []:
        lines.append(f"Omitted: {row.get('id')} ({row.get('reason')}).")
    lines.extend(["", "## Plans and prices", ""])
    for key in ("free", "start", "pro", "individual"):
        p = plans.get(key) or {}
        price = p.get("price_czk")
        price_s = "dohodou" if price is None else f"{price} Kč/měsíc"
        lines.append(f"### {p.get('label')} ({price_s})")
        for feat in p.get("features_cs") or []:
            lines.append(f"- {feat}")
        lines.append("")
    lines.extend(["## Free vs paid", "", "Free includes:"])
    for item in free_vs.get("free_includes_cs") or []:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Paid includes:")
    for item in free_vs.get("paid_includes_cs") or []:
        lines.append(f"- {item}")
    lines.extend(["", "## Methodology", "", PRICE_METHODOLOGY_CS, "", "## Market summary (live)", ""])
    for loc in ("Praha", "Brno", "Ostrava"):
        try:
            s = store.catalog_stats(loc, "pronajem")
        except Exception:
            continue
        lines.append(
            f"K {today_cs} je medián pronájmu bytů v lokalitě {loc} {_fmt_czk(s.get('median_price'))} "
            f"({_fmt_m2(s.get('median_price_per_m2'))}; {_fmt_int(s.get('active_count'))} nabídek)."
        )
    lines.extend(
        [
            "",
            "## FAQ",
            "",
        ]
    )
    for f in _faq_items(store):
        lines.extend([f"### {f['q']}", "", f["a"], ""])
    lines.extend(
        [
            "## MCP tools",
            "",
            f"Endpoint: {mcp.get('url')}",
            f"Transport: {mcp.get('transport')}; auth: {mcp.get('auth')}",
            f"Units: {mcp.get('units', {}).get('currency')}, area in {mcp.get('units', {}).get('area')}",
            f"Data region: {mcp.get('data_region')}",
            "",
            "### search_listings",
            "Use when the user wants current rentals/sales matching filters.",
            "Example CZ: „hledám 2+kk v Praze do 20000“ → locality=Praha, offer_type=pronajem, disposition=2+kk, max_price=20000",
            "Example EN: \"flat in Prague under 20000 CZK\"",
            "",
            "### new_listings",
            "Use when the user asks what is new in the last N hours (first_seen).",
            "Example: „co nového na pronájem v Brně“ → locality=Brno, offer_type=pronajem, since_hours=24",
            "",
            "### get_listing",
            "Use when the user wants one listing by id/listing_key from a prior result.",
            "",
            "### locality_stats",
            "Use when the user asks for median/average price or active count in a locality.",
            "",
            "### price_check",
            "Use when the user asks if a price is fair vs comparables (median primary).",
            "Example: „je 25000 za 2+kk na Vinohradech hodně?“",
            "",
            "### compare_localities",
            "Use when comparing two or more localities side by side.",
            "",
            mcp.get("tip_en") or "",
            "",
            "## Links",
            "",
            f"- Home: {(facts.get('urls') or {}).get('home')}",
            f"- FAQ: {(facts.get('urls') or {}).get('faq')}",
            f"- Index: {(facts.get('urls') or {}).get('index')}",
            f"- MCP docs: {mcp.get('docs')}",
            "",
        ]
    )
    return "\n".join(lines)
