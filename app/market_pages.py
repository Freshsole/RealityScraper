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


def _stat_card(label: str, value: str, sub: str, *, unit: str = "", width: float | None = None, dark: bool = False) -> str:
    bar = (
        f'<div class="stat-bar"><i data-w="{max(0.06, min(1.0, width)):.2f}"></i></div>'
        if width is not None
        else ""
    )
    unit_html = f" <small>{html.escape(unit)}</small>" if unit else ""
    cls = "stat-card is-dark" if dark else "stat-card"
    return (
        f'<article class="{cls}"><p class="stat-label">{html.escape(label)}</p>'
        f'<p class="stat-value">{html.escape(value)}{unit_html}</p>'
        f'<p class="stat-sub">{html.escape(sub)}</p>{bar}</article>'
    )


def _cite_row(text: str) -> str:
    return (
        f'<div class="cite-row"><p>{html.escape(text)}</p>'
        f'<button type="button" class="copy-btn" data-copy="{html.escape(text, quote=True)}">Kopírovat</button></div>'
    )


def render_index(store: Store) -> str:
    import json

    from app import site_chrome

    praha = store.catalog_stats("Praha", "pronajem")
    brno = store.catalog_stats("Brno", "pronajem")
    ostrava = store.catalog_stats("Ostrava", "pronajem")
    today = date.today().isoformat()
    today_cs = product_facts.format_date_cs(date.today())
    current_month = date.today().strftime("%Y-%m")
    try:
        months = store.list_index_months(completed_only=True)
    except Exception:
        months = []
    try:
        current_rows = store.index_month_city_stats(current_month, allow_incomplete=True)
    except Exception:
        current_rows = []
    med_m2 = _fmt_m2(praha.get("median_price_per_m2"))
    updated_label = product_facts.data_updated_label(date.today())

    def cite(city_gen: str, st: dict[str, Any]) -> str:
        return (
            f"K {today_cs} je medián pronájmu bytů v {city_gen} {_fmt_czk(st.get('median_price'))} "
            f"({_fmt_m2(st.get('median_price_per_m2'))}; {_fmt_int(st.get('active_count'))} nabídek)."
        )

    cite_praha, cite_brno, cite_ostrava = cite("Praze", praha), cite("Brně", brno), cite("Ostravě", ostrava)
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

    cities = [("Praha", praha), ("Brno", brno), ("Ostrava", ostrava)]
    top_m2 = max([float(st.get("median_price_per_m2") or 0) for _, st in cities] + [1.0])
    cards = "".join(
        _stat_card(
            name,
            _fmt_czk(st.get("median_price")),
            f"{_fmt_m2(st.get('median_price_per_m2'))} · {_fmt_int(st.get('active_count'))} aktivních nabídek",
            unit="medián / měsíc",
            width=float(st.get("median_price_per_m2") or 0) / top_m2,
            dark=(i == 0),
        )
        for i, (name, st) in enumerate(cities)
    )
    rows = "".join(
        f"<tr><td>{html.escape(str(r['locality']))}</td>"
        f"<td data-v=\"{r.get('new_count') or 0}\">{_fmt_int(r['new_count'])}</td>"
        f"<td data-v=\"{r.get('median_price') or 0}\">{_fmt_czk(r.get('median_price'))}</td>"
        f"<td data-v=\"{r.get('median_price_per_m2') or 0}\">{_fmt_m2(r.get('median_price_per_m2'))}</td>"
        f"<td data-v=\"{r.get('avg_price_per_m2') or 0}\">{_fmt_m2(r.get('avg_price_per_m2'))}</td></tr>"
        for r in current_rows
    )
    table = (
        '<div class="table-wrap"><table class="data-table"><thead><tr>'
        '<th>Lokalita</th><th data-sort>Nové nabídky</th><th data-sort>Medián</th>'
        '<th data-sort>Medián Kč/m²</th><th data-sort>Průměr Kč/m²</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
        if rows
        else '<div class="table-wrap"><p class="table-empty">Pro tento měsíc zatím nemáme dost dat.</p></div>'
    )
    archive = "".join(
        f'<a class="pg-chip is-link" href="/index/{html.escape(m)}">{html.escape(m)}</a>' for m in months[:24]
    ) or '<span class="note">Zatím bez ukončených měsíců.</span>'

    body = f"""      <section class="pg-hero" data-nav="Přehled" data-nav-desc="Souhrn nájmů k dnešnímu dni">
        <p class="eyebrow">INDEX NÁJMŮ · {html.escape(updated_label.upper())}</p>
        <h1 class="display">Kolik dnes stojí pronájem bytu v Česku?</h1>
        <p class="lead">{html.escape(cite_praha)} Čísla počítáme z aktivních nabídek ze všech sledovaných portálů, bez duplicit.</p>
        <div class="pg-actions">
          <a class="pill pill-lg" href="/hledat">Procházet nabídky</a>
          <a class="link-row" href="/index.csv">Stáhnout CSV →</a>
        </div>
      </section>

      <section class="pg-section" id="mesta" data-nav="Města" data-nav-desc="Medián nájmu v největších městech">
        <div class="pg-head reveal">
          <p class="eyebrow">NEJVĚTŠÍ MĚSTA</p>
          <h2 class="display">Medián nájmu podle města</h2>
          <p>Aktuální nabídky bytů k pronájmu. Medián je odolnější vůči extrémům než průměr.</p>
        </div>
        <div class="stat-grid">{cards}</div>
      </section>

      <section class="pg-section is-pale" id="mesic" data-nav="Tento měsíc" data-nav-desc="Nové nabídky a ceny v aktuálním měsíci">
        <div class="pg-head reveal">
          <p class="eyebrow">{html.escape(current_month)}</p>
          <h2 class="display">Aktuální měsíc podle lokality</h2>
          <p>Neukončený měsíc nemá trvalou archivní adresu, čísla se průběžně mění. Po skončení měsíce vznikne /index/{html.escape(current_month)}. Řadit můžete kliknutím na záhlaví sloupce.</p>
        </div>
        {table}
      </section>

      <section class="pg-section" id="archiv" data-nav="Archiv" data-nav-desc="Trvalé odkazy na ukončené měsíce">
        <div class="pg-head reveal">
          <p class="eyebrow">ARCHIV</p>
          <h2 class="display">Ukončené měsíce</h2>
          <p>Trvalé adresy vhodné pro citace a reportáže.</p>
        </div>
        <div class="pg-chips">{archive}</div>
      </section>

      <section class="pg-section is-pale" id="metodika" data-nav="Metodika" data-nav-desc="Jak počítáme Kč/m² a jak citovat">
        <div class="split">
          <div class="pg-card cite-card reveal">
            <h3 style="margin:0">Citace pro média</h3>
            {_cite_row(cite_praha)}
            {_cite_row(cite_brno)}
            {_cite_row(cite_ostrava)}
          </div>
          <div class="pg-head reveal">
            <p class="eyebrow">METODIKA</p>
            <h2 class="display">Jak počítáme Kč/m²</h2>
            <p>{html.escape(PRICE_METHODOLOGY_CS)}</p>
            <p class="note">{html.escape(DISCLAIMER_CS)}</p>
          </div>
        </div>
      </section>
{site_chrome.cta_band("Hlídejte nové byty dřív než ostatní", "Upozornění na nové nabídky podle vašich filtrů. Registrace trvá 30 sekund a nepotřebujete platební kartu.")}"""
    return site_chrome.page(
        title=f"Realitify index nájmů – průběžně k {today_cs}",
        description=cite_praha,
        canonical="https://realitify.cz/index",
        body=body,
        markdown_alt="https://realitify.cz/index.md",
        ld=[dataset],
    )


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
            "q": "Kdy vznikne a zanikne lokalitní stránka?",
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
        {
            "q": "Které portály Realitify sleduje?",
            "a": f"Agregujeme inzeráty ze {portals}. Duplicity mezi portály sjednocujeme, takže stejný byt uvidíte jen jednou.",
        },
        {
            "q": "Jak často se nabídky aktualizují?",
            "a": (
                "Nejnovější nabídky stahujeme zhruba každých 60 sekund. Úplné synchronizace katalogu "
                "běží průběžně na pozadí a zrušené inzeráty označujeme jako neaktivní."
            ),
        },
        {
            "q": "Musím se registrovat, abych viděl nabídky?",
            "a": (
                "Ne. Celý katalog s mapou a filtry otevřete na stránce Nabídky po zadání e-mailu. "
                "Účet potřebujete až pro hlídání nových bytů a notifikace."
            ),
        },
    ]


_FAQ_CATS = [
    ("all", "Vše"),
    ("obecne", "O Realitify"),
    ("ceny", "Ceny a trh"),
    ("hlidani", "Hlídání a tarify"),
    ("metodika", "Metodika"),
]
_FAQ_CAT_BY_Q = {
    "Co je Realitify?": "obecne",
    "Jak rychle sehnat byt v Praze?": "ceny",
    "Jak být první u nové nabídky?": "hlidani",
    "Kolik stojí pronájem 2+kk v Praze?": "ceny",
    "Jaké jsou tarify?": "hlidani",
    "Jak poznat předraženou nabídku?": "ceny",
    "Jak Realitify počítá cenu za m²?": "metodika",
    "Kdy vznikne a zanikne lokalitní stránka?": "metodika",
    "Souvisí Realitify se společností Realtify nebo PriceHubble?": "obecne",
    "Které portály Realitify sleduje?": "obecne",
    "Jak často se nabídky aktualizují?": "obecne",
    "Musím se registrovat, abych viděl nabídky?": "hlidani",
}


def render_faq(store: Store) -> str:
    from app import site_chrome

    faqs = _faq_items(store)
    updated = product_facts.data_updated_label(date.today())
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}}
            for f in faqs
        ],
        "dateModified": date.today().isoformat(),
    }
    chevron = (
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
    )
    qa = "".join(
        f'<div class="qa" id="q{i + 1}" data-cat="{_FAQ_CAT_BY_Q.get(f["q"], "obecne")}">'
        f'<button type="button" class="qa-q" aria-expanded="false"><span>{html.escape(f["q"])}</span>'
        f'<span class="qa-ico">{chevron}</span></button>'
        f'<div class="qa-a"><div><p>{html.escape(f["a"])}</p></div></div></div>'
        for i, f in enumerate(faqs)
    )
    cats = "".join(
        f'<button type="button" class="faq-cat{" is-on" if key == "all" else ""}" data-cat="{key}">{html.escape(label)}</button>'
        for key, label in _FAQ_CATS
    )
    search_icon = (
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" '
        'stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
    )
    body = f"""      <section class="pg-hero" data-nav="Dotazy" data-nav-desc="Hledejte v odpovědích">
        <p class="eyebrow">ČASTÉ DOTAZY · {html.escape(updated.upper())}</p>
        <h1 class="display">Odpovědi na to, co se nás lidé ptají</h1>
        <p class="lead">Co je Realitify, kolik stojí, jak počítáme ceny a jak rychle dostanete upozornění. Najdete tu {len(faqs)} odpovědí.</p>
      </section>

      <section class="pg-section" id="otazky" data-nav="Otázky" data-nav-desc="Rozbalte odpověď nebo vyhledejte">
        <div class="faq-layout">
          <div>
            <div class="faq-tools">
              <label class="faq-search">{search_icon}<input id="faq-q" type="search" placeholder="Hledat v otázkách a odpovědích…" autocomplete="off" aria-label="Hledat v otázkách" /></label>
              <div class="faq-cats" role="tablist" aria-label="Kategorie">{cats}</div>
            </div>
            <div class="qa-list">{qa}</div>
            <p class="faq-empty" id="faq-empty" hidden>Nic jsme nenašli. Zkuste jiné slovo, nebo nám napište.</p>
          </div>
          <aside class="faq-aside">
            <h3>Nenašli jste odpověď?</h3>
            <p>Napište nám, odpovíme obvykle do jednoho pracovního dne.</p>
            <a class="pill pill-lg" href="/kontakt">Napsat nám</a>
          </aside>
        </div>
      </section>
{site_chrome.cta_band("Vyzkoušejte hlídání zdarma", "Nastavte filtr, zapněte upozornění a buďte u nových bytů první.")}"""
    return site_chrome.page(
        title="FAQ – Realitify",
        description=f"Co je Realitify, tarify, medián Kč/m² a metodika. {updated}.",
        canonical="https://realitify.cz/faq",
        body=body,
        markdown_alt="https://realitify.cz/faq.md",
        ld=[faq_ld],
    )


def render_faq_md(store: Store) -> str:
    faqs = _faq_items(store)
    lines = ["# FAQ – Realitify", "", product_facts.data_updated_label(date.today()) + ".", ""]
    for f in faqs:
        lines.extend([f"## {f['q']}", "", f["a"], ""])
    lines.append(DISCLAIMER_CS)
    lines.append("")
    return "\n".join(lines)


def render_search(store: Store | None = None, q: str = "") -> str:
    """Public catalog entry: email gate, then the app's catalog in a frame."""
    from app import site_chrome

    del store, q  # data is loaded client-side by the guest catalog
    body = """      <section class="pg-hero hl-hero" data-nav="Nabídky" data-nav-desc="Katalog bytů s mapou a filtry">
        <p class="eyebrow">KATALOG NABÍDEK</p>
        <h1 class="display">Všechny nabídky bytů na jednom místě</h1>
        <p class="lead">Mapa, filtry, historie cen a odkazy na originální inzeráty ze všech sledovaných portálů. Stačí zadat e-mail a katalog je váš.</p>
      </section>

      <section class="pg-section" id="gate" data-nav="Přístup" hidden>
        <div class="pg-card hl-gate reveal" id="hl-gate">
          <h2>Odemkněte celý katalog</h2>
          <p>Zadejte e-mail a hned uvidíte všechny aktuální nabídky na mapě i v seznamu. Bez hesla a bez platební karty.</p>
          <form class="hl-form" id="hl-form" novalidate>
            <input type="email" id="hl-email" name="email" placeholder="vas@email.cz" autocomplete="email" required aria-label="E-mail" />
            <button class="pill" type="submit">Zobrazit nabídky</button>
          </form>
          <p class="hl-msg" id="hl-msg" role="alert"></p>
          <ul class="hl-bullets"><li>Mapa a filtry</li><li>9 portálů</li><li>Aktualizace každou minutu</li></ul>
          <p class="note">Odesláním souhlasíte se zpracováním e-mailu dle <a href="/ochrana-soukromi" style="text-decoration:underline">zásad ochrany soukromí</a>. Můžeme vám jednou za čas poslat tipy k hlídání bytů, odhlásit se můžete kdykoli.</p>
        </div>
      </section>

      <section class="hl-frame-wrap" id="katalog" data-nav="Katalog" data-nav-desc="Mapa a seznam nabídek" hidden>
        <iframe class="hl-frame" id="hl-frame" title="Katalog nabídek Realitify" loading="lazy" allow="geolocation"></iframe>
      </section>
"""
    return site_chrome.page(
        title="Nabídky bytů a domů – katalog Realitify",
        description="Katalog nabídek bytů a domů z českých realitních portálů na jednom místě. Mapa, filtry a ceny.",
        canonical="https://realitify.cz/hledat",
        body=body,
        scripts='\n    <script src="/static/site/hledat.js?v=1" defer></script>',
    )


_ICON = {
    "layers": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    "bell": '<path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "map": '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Z"/><path d="M9 4v14M15 6v14"/>',
    "shield": '<path d="M12 3 4 6v6c0 5 3.4 8.2 8 9 4.6-.8 8-4 8-9V6l-8-3Z"/><path d="m9 12 2 2 4-4"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
}


def _icon(name: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_ICON[name]}</svg>'
    )


def _feature(icon: str, title: str, text: str) -> str:
    return (
        f'<article class="feature-card"><span class="feature-icon">{_icon(icon)}</span>'
        f"<h3>{html.escape(title)}</h3><p>{html.escape(text)}</p></article>"
    )


def _step(title: str, text: str) -> str:
    return f'<article class="step"><h3>{html.escape(title)}</h3><p>{html.escape(text)}</p></article>'


def render_about_cs(store: Store | None = None) -> str:
    from app import site_chrome

    portals = public_portals_sentence()
    facts = product_facts.get_facts()
    op = facts.get("operator") or {}
    contact = facts.get("contact") or {}
    product = facts.get("product") or {}
    plans = facts.get("plans") or {}
    active = praha_median = m2 = None
    if store is not None:
        try:
            praha = store.catalog_stats("Praha", "pronajem")
            active, praha_median, m2 = praha.get("active_count"), praha.get("median_price"), praha.get("median_price_per_m2")
        except Exception:
            pass
    updated = product_facts.data_updated_label(date.today())
    portal_count = len(PUBLIC_PORTAL_LABELS)
    since = str(product.get("since_year") or "")
    support = html.escape(str(contact.get("support") or ""))
    stats = "".join(
        [
            _stat_card("Sledovaných portálů", str(portal_count), "Sreality, Bezrealitky, iDNES a další"),
            _stat_card("Aktivních nabídek v Praze", _fmt_int(active) if active is not None else "—", "Aktualizujeme každou minutu"),
            _stat_card("Medián nájmu v Praze", _fmt_czk(praha_median), _fmt_m2(m2), dark=True),
            _stat_card("V provozu od", since or "—", "Nezávislý agregátor nabídek"),
        ]
    )
    pro, start = plans.get("pro") or {}, plans.get("start") or {}
    body = f"""      <section class="pg-hero" data-nav="O nás" data-nav-desc="Kdo jsme a co děláme">
        <p class="eyebrow">O REALITIFY</p>
        <h1 class="display">Hlídáme český trh s byty za vás</h1>
        <p class="lead">{html.escape(product_facts.one_liner_cs())} Sjednocujeme inzeráty z {html.escape(portals)} a hlásíme nové shody dřív než ostatní.</p>
        <div class="pg-actions">
          <a class="pill pill-lg" href="/hledat">Procházet nabídky</a>
          <a class="link-row" href="/kontakt">Kontaktujte nás →</a>
        </div>
      </section>

      <section class="pg-section" id="cisla" data-nav="Čísla" data-nav-desc="Realitify v číslech">
        <div class="stat-grid is-4">{stats}</div>
      </section>

      <section class="pg-section is-pale" id="co-delame" data-nav="Co děláme" data-nav-desc="Tři věci, na kterých stavíme">
        <div class="pg-head reveal">
          <p class="eyebrow">CO DĚLÁME</p>
          <h2 class="display">Jedno místo místo deseti záložek</h2>
          <p>Realitní portály jsou roztříštěné. Realitify je spojuje do jednoho katalogu a hlídá ho za vás.</p>
        </div>
        <div class="feature-grid">
          {_feature("layers", "Jeden katalog", "Inzeráty z devíti portálů bez duplicit, s mapou, filtry a historií cen.")}
          {_feature("bolt", "Rychlost", "Nové nabídky stahujeme každou minutu. Dobré byty mizí v řádu minut, my vás upozorníme první.")}
          {_feature("chart", "Data o trhu", "Medián nájmů, Kč/m² a vývoj cen podle lokality. Index nájmů je veřejný a citovatelný.")}
        </div>
      </section>

      <section class="pg-section" id="jak" data-nav="Jak funguje" data-nav-desc="Od scraperu k upozornění">
        <div class="pg-head reveal">
          <p class="eyebrow">JAK FUNGUJE AGREGACE</p>
          <h2 class="display">Od inzerátu k upozornění za minutu</h2>
          <p>Veřejné stránky a MCP server čtou ze stejného katalogu, který průběžně plní naše scrapery.</p>
        </div>
        <div class="steps">
          {_step("Sběr", "Scrapery průběžně procházejí veřejné výpisy portálů a ukládají aktivní inzeráty.")}
          {_step("Sjednocení", "Odstraňujeme duplicity, doplňujeme polohu a sjednocujeme dispozice a ceny.")}
          {_step("Shoda s filtrem", "Každý nový záznam porovnáme s vašimi hlídacími psy podle lokality, ceny a dispozice.")}
          {_step("Upozornění", "Pošleme e-mail, push nebo zprávu na Discord. Kdo je první, vyhrává.")}
        </div>
      </section>

      <section class="pg-section is-pale" id="provozovatel" data-nav="Provozovatel" data-nav-desc="Kdo službu provozuje">
        <div class="split">
          <div class="pg-head reveal">
            <p class="eyebrow">PROVOZOVATEL</p>
            <h2 class="display">Za Realitify stojí Virtuex.cz</h2>
            <p>Realitify vytváří a provozuje <a href="https://www.virtuex.cz" rel="noopener" style="text-decoration:underline;text-underline-offset:3px">Virtuex.cz</a>, které řídí OSVČ {html.escape(str(op.get('name')))}. Najdete nás na kontaktu níže a rádi odpovíme na cokoli.</p>
            <p class="note">{html.escape(DISCLAIMER_CS)}</p>
          </div>
          <div class="pg-card reveal">
            <dl class="fact-list">
              <div><dt>Za službou stojí</dt><dd><a href="https://www.virtuex.cz" rel="noopener">Virtuex.cz</a></dd></div>
              <div><dt>Řídí</dt><dd>OSVČ {html.escape(str(op.get('name')))}</dd></div>
              <div><dt>IČO</dt><dd>{html.escape(str(op.get('ico')))}</dd></div>
              <div><dt>Sídlo</dt><dd>{html.escape(str(op.get('address')))}</dd></div>
              <div><dt>Kontakt</dt><dd><a href="mailto:{support}">{support}</a></dd></div>
              <div><dt>Tarify</dt><dd>od {html.escape(str(start.get('price_czk')))} Kč/měsíc · PRO {html.escape(str(pro.get('price_czk')))} Kč</dd></div>
            </dl>
          </div>
        </div>
      </section>
{site_chrome.cta_band("Začněte hlídat byty ještě dnes", "Prohlížení katalogu je zdarma. Hlídání nových nabídek si vyzkoušíte bez platební karty.")}"""
    return site_chrome.page(
        title="O nás – Realitify",
        description=f"{product_facts.one_liner_cs()} Provozuje {op.get('name')} ({op.get('legal_form')}), IČO {op.get('ico')}.",
        canonical="https://realitify.cz/o-nas",
        body=body,
        alternates=[
            ("cs", "https://realitify.cz/o-nas"),
            ("en", "https://realitify.cz/about"),
            ("x-default", "https://realitify.cz/o-nas"),
        ],
    )


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
        f"Za službou stojí Virtuex.cz (https://www.virtuex.cz), které řídí OSVČ Jiří Kolb. Provozovatel: {op.get('name')}, {op.get('legal_form')}, IČO {op.get('ico')}, {op.get('address')}. Kontakt: {contact.get('support')}.",
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
    from app import site_chrome

    portals = product_facts.portals_sentence(lang="en")
    facts = product_facts.get_facts()
    op = facts.get("operator") or {}
    contact = facts.get("contact") or {}
    product = facts.get("product") or {}
    support = html.escape(str(contact.get("support") or ""))
    body = f"""      <section class="pg-hero" data-nav="About" data-nav-desc="Who we are">
        <p class="eyebrow">ABOUT REALITIFY</p>
        <h1 class="display">We watch the Czech property market for you</h1>
        <p class="lead">{html.escape(product_facts.one_liner_en())} It collects and deduplicates ads from {html.escape(portals)}.</p>
        <div class="pg-actions">
          <a class="pill pill-lg" href="/hledat">Browse listings</a>
          <a class="link-row" href="/o-nas">Česky →</a>
        </div>
      </section>

      <section class="pg-section is-pale" id="what" data-nav="What we do" data-nav-desc="Three things we build on">
        <div class="pg-head reveal">
          <p class="eyebrow">WHAT WE DO</p>
          <h2 class="display">One catalog instead of ten tabs</h2>
        </div>
        <div class="feature-grid">
          {_feature("layers", "One catalog", "Listings from nine portals without duplicates, with a map, filters and price history.")}
          {_feature("bolt", "Speed", "New listings are fetched every minute. Good flats disappear within minutes, we alert you first.")}
          {_feature("chart", "Market data", "Median rents, price per m² and trends by location. The rent index is public and citable.")}
        </div>
      </section>

      <section class="pg-section" id="how" data-nav="How it works" data-nav-desc="From scraper to alert">
        <div class="pg-head reveal">
          <p class="eyebrow">HOW AGGREGATION WORKS</p>
          <h2 class="display">From a listing to an alert within a minute</h2>
        </div>
        <div class="steps">
          {_step("Collect", "Scrapers continuously read public portal listings into a catalog (first_seen / last_seen).")}
          {_step("Unify", "We remove duplicates, add coordinates and normalise layouts and prices.")}
          {_step("Match", "Each new record is compared with your watches by location, price and layout.")}
          {_step("Alert", "You get an email, push or Discord message. Public pages and MCP read from the same catalog.")}
        </div>
      </section>

      <section class="pg-section is-pale" id="operator" data-nav="Operator" data-nav-desc="Who runs the service">
        <div class="split">
          <div class="pg-head reveal">
            <p class="eyebrow">OPERATOR</p>
            <h2 class="display">Realitify is built by Virtuex.cz</h2>
            <p>Realitify is created and run by <a href="https://www.virtuex.cz" rel="noopener" style="text-decoration:underline;text-underline-offset:3px">Virtuex.cz</a>, managed by sole trader {html.escape(str(op.get('name')))}. It has been operating since {html.escape(str(product.get('since_year')))}.</p>
            <p class="note">{html.escape(DISCLAIMER_EN)}</p>
          </div>
          <div class="pg-card reveal">
            <dl class="fact-list">
              <div><dt>Built by</dt><dd><a href="https://www.virtuex.cz" rel="noopener">Virtuex.cz</a></dd></div>
              <div><dt>Managed by</dt><dd>{html.escape(str(op.get('name')))} (sole trader)</dd></div>
              <div><dt>Company ID (IČO)</dt><dd>{html.escape(str(op.get('ico')))}</dd></div>
              <div><dt>Registered office</dt><dd>{html.escape(str(op.get('address')))}, Czech Republic</dd></div>
              <div><dt>Contact</dt><dd><a href="mailto:{support}">{support}</a></dd></div>
            </dl>
          </div>
        </div>
      </section>
{site_chrome.cta_band("Start watching flats today", "Browsing the catalog is free. Try new-listing alerts without a credit card.", "Try for free")}"""
    return site_chrome.page(
        title="About – Realitify",
        description=f"{product_facts.one_liner_en()} Operated by {op.get('name')}, Company ID {op.get('ico')}.",
        canonical="https://realitify.cz/about",
        body=body,
        lang="en",
        alternates=[
            ("en", "https://realitify.cz/about"),
            ("cs", "https://realitify.cz/o-nas"),
            ("x-default", "https://realitify.cz/o-nas"),
        ],
    )


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
