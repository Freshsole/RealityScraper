"""SSR market SEO pages: unique narratives, disposition long-tails, hub, OG, index archive."""

from __future__ import annotations

import html
import io
import json
import re
from typing import Any

from app import facts as product_facts
from app.locality_normalize import _fold, normalize_disposition
from app.market_pages import (
    DISCLAIMER_CS,
    MARKET_NEIGHBORHOODS,
    MIN_ACTIVE,
    MIN_ACTIVE_CREATE,
    MIN_ACTIVE_KEEP,
    PRICE_METHODOLOGY_CS,
    _active_count,
    _children_for_district,
    _fmt_czk,
    _fmt_int,
    _fmt_m2,
    _listings_html,
    _parents_for_neighborhood,
    _sample_listing_items,
    evaluate_market_page,
    market_inventory,
    slugify_locality,
)
from app.store import Store


CZ_MONTHS = {
    1: "leden",
    2: "únor",
    3: "březen",
    4: "duben",
    5: "květen",
    6: "červen",
    7: "červenec",
    8: "srpen",
    9: "září",
    10: "říjen",
    11: "listopad",
    12: "prosinec",
}


def czech_month_label(month: str) -> str:
    y, m = month.split("-")
    return f"{CZ_MONTHS[int(m)]} {y}"


SEO_DISPOSITIONS: tuple[str, ...] = (
    "1+kk",
    "1+1",
    "2+kk",
    "2+1",
    "3+kk",
    "3+1",
    "4+kk",
    "4+1",
    "pokoj",
)


def slugify_disposition(label: str) -> str:
    mapped = normalize_disposition(label) or (label or "").strip()
    m = re.fullmatch(r"(\d)\+kk", mapped, re.I)
    if m:
        return f"{m.group(1)}kk"
    m = re.fullmatch(r"(\d)\+(\d)", mapped)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return re.sub(r"[^a-z0-9]", "", _fold(mapped))


def disposition_from_slug(slug: str) -> str:
    raw = (slug or "").strip()
    folded = _fold(raw).replace(" ", "").replace("-", "")
    if re.fullmatch(r"\d-\d", raw):
        mapped = normalize_disposition(raw.replace("-", "+"))
        if mapped and "," not in mapped:
            return mapped
    mapped = normalize_disposition(raw)
    if mapped and "," not in mapped:
        return mapped
    m = re.fullmatch(r"(\d)kk", folded)
    if m:
        return f"{m.group(1)}+kk"
    m = re.fullmatch(r"(\d)1", folded)
    if m:
        return f"{m.group(1)}+1"
    special = {"pokoj": "pokoj", "garsoniera": "garsoniéra", "atypicky": "atypický", "atyp": "atyp"}
    if folded in special:
        return special[folded]
    return normalize_disposition(raw) or raw


def offer_label(offer: str) -> str:
    return "pronájem" if offer == "pronajem" else "prodej"


def offer_label_genitive(offer: str) -> str:
    return "pronájmu" if offer == "pronajem" else "prodeje"


def parent_locality(locality: str) -> str | None:
    parents = _parents_for_neighborhood(locality)
    if parents:
        return parents[0]
    m = re.match(r"(?i)^praha\s+(\d{1,2})$", locality.strip())
    if m:
        return "Praha"
    return None


def redirect_path_for_thin(store: Store, locality: str, offer: str, disposition: str = "") -> str | None:
    active, _count = evaluate_market_page(store, locality, offer, disposition)
    if active:
        return None
    if disposition:
        parent_active, _ = evaluate_market_page(store, locality, offer, "")
        if parent_active:
            return f"/trh/{slugify_locality(locality)}/{offer}"
    parent = parent_locality(locality)
    while parent:
        parent_active, _ = evaluate_market_page(store, parent, offer, "")
        if parent_active:
            return f"/trh/{slugify_locality(parent)}/{offer}"
        parent = parent_locality(parent)
    return "/hledat"


def list_disposition_paths(store: Store) -> list[tuple[str, str, str, str]]:
    out: list[tuple[str, str, str, str]] = []
    wanted = {d.lower(): d for d in SEO_DISPOSITIONS}
    for _path, loc, offer in [(r["path"], r["locality"], r["offer"]) for r in market_inventory(store)["included"]]:
        try:
            rows = store.catalog_disposition_counts(loc, offer)
        except ValueError:
            continue
        for disp_raw, _n in rows:
            key = (normalize_disposition(disp_raw) or disp_raw).lower()
            label = wanted.get(key)
            if not label:
                continue
            active, _count = evaluate_market_page(store, loc, offer, label)
            if not active:
                continue
            out.append(
                (
                    f"/trh/{slugify_locality(loc)}/{offer}/{slugify_disposition(label)}",
                    loc,
                    offer,
                    label,
                )
            )
    return out


def list_all_seo_paths(store: Store, *, sync_if_empty: bool = False) -> dict[str, list[str]]:
    """SEO paths for sitemap. Prefer DB state (fast); optionally sync when empty."""
    paths = seo_paths_from_db(store)
    if sync_if_empty and not paths["locality"]:
        refresh_market_seo_inventory(store)
        paths = seo_paths_from_db(store)
    return paths


def seo_paths_from_db(store: Store) -> dict[str, list[str]]:
    locality: list[str] = []
    disposition: list[str] = []
    try:
        rows = store.list_market_seo_pages(active_only=True)
    except Exception:
        rows = []
    for row in rows:
        loc = row.get("locality") or ""
        offer = row.get("offer") or ""
        disp = (row.get("disposition") or "").strip()
        if not loc or offer not in {"pronajem", "prodej"}:
            continue
        base = f"/trh/{slugify_locality(loc)}/{offer}"
        if disp:
            disposition.append(f"{base}/{slugify_disposition(disp)}")
        else:
            locality.append(base)
    try:
        months = [f"/index/{m}" for m in store.list_index_months()]
    except Exception:
        months = []
    return {"hub": ["/hledat"], "locality": locality, "disposition": disposition, "index_archive": months}


_SITEMAP_LOCK = __import__("threading").Lock()
_SITEMAP_STATE: dict[str, Any] = {"xml": "", "at": 0.0, "refreshing": False}
_SITEMAP_TTL_SEC = 3600.0


def refresh_market_seo_inventory(store: Store) -> None:
    """Recompute locality + disposition active flags into market_seo_pages (slow)."""
    try:
        market_inventory(store)
        list_disposition_paths(store)
    except Exception:
        pass


def _schedule_seo_refresh(store: Store) -> None:
    import threading

    with _SITEMAP_LOCK:
        if _SITEMAP_STATE.get("refreshing"):
            return
        _SITEMAP_STATE["refreshing"] = True

    def _run() -> None:
        try:
            refresh_market_seo_inventory(store)
            # Invalidate sitemap cache so next hit rebuilds from DB.
            with _SITEMAP_LOCK:
                _SITEMAP_STATE["at"] = 0.0
        finally:
            with _SITEMAP_LOCK:
                _SITEMAP_STATE["refreshing"] = False

    threading.Thread(target=_run, name="seo-inventory-refresh", daemon=True).start()


def build_sitemap_xml(store: Store | None, static_pages: list[tuple[str, str, str]]) -> str:
    """Build sitemap XML quickly from DB (+ static pages). Never runs full inventory sync."""
    from datetime import date

    today = date.today().isoformat()
    paths = list(static_pages)
    seo = seo_paths_from_db(store) if store is not None else {"locality": [], "disposition": [], "index_archive": []}
    if store is not None and not seo.get("locality"):
        _schedule_seo_refresh(store)
    for path in seo.get("locality") or []:
        paths.append((path, "daily", "0.7"))
    for path in seo.get("disposition") or []:
        paths.append((path, "daily", "0.65"))
    for path in seo.get("index_archive") or []:
        paths.append((path, "monthly", "0.8"))
    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: list[tuple[str, str, str]] = []
    for path, freq, prio in paths:
        if path in seen:
            continue
        seen.add(path)
        ordered.append((path, freq, prio))
    urls = "\n".join(
        f"  <url><loc>https://realitify.cz{path}</loc>"
        f"<lastmod>{today}</lastmod>"
        f"<changefreq>{freq}</changefreq>"
        f"<priority>{prio}</priority></url>"
        for path, freq, prio in ordered
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>"
    )


def cached_sitemap_xml(store: Store | None, static_pages: list[tuple[str, str, str]]) -> str:
    import time

    now = time.time()
    with _SITEMAP_LOCK:
        xml = str(_SITEMAP_STATE.get("xml") or "")
        at = float(_SITEMAP_STATE.get("at") or 0.0)
        if xml and (now - at) < _SITEMAP_TTL_SEC:
            return xml
    xml = build_sitemap_xml(store, static_pages)
    with _SITEMAP_LOCK:
        _SITEMAP_STATE["xml"] = xml
        _SITEMAP_STATE["at"] = now
    return xml


def top_localities(store: Store, offer: str = "pronajem", limit: int = 10) -> list[dict[str, Any]]:
    rows = [r for r in market_inventory(store)["included"] if r["offer"] == offer]
    rows.sort(key=lambda r: int(r.get("active_count") or 0), reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        loc = row["locality"]
        if loc in seen:
            continue
        seen.add(loc)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _breadcrumb_items(locality: str, offer: str, disposition: str = "") -> list[dict[str, str]]:
    items = [
        {"name": "Domů", "url": "https://realitify.cz/"},
        {"name": "Nabídky", "url": "https://realitify.cz/hledat"},
    ]
    chain: list[str] = []
    p = parent_locality(locality)
    while p:
        chain.append(p)
        p = parent_locality(p)
    for loc in reversed(chain):
        items.append({"name": loc, "url": f"https://realitify.cz/trh/{slugify_locality(loc)}/{offer}"})
    items.append({"name": locality, "url": f"https://realitify.cz/trh/{slugify_locality(locality)}/{offer}"})
    if disposition:
        items.append(
            {
                "name": disposition,
                "url": f"https://realitify.cz/trh/{slugify_locality(locality)}/{offer}/{slugify_disposition(disposition)}",
            }
        )
    return items


def _related_chips(raw: str) -> str:
    """Turn the legacy related-links markup into chip rows."""
    out = raw.replace("<h2>", '<h3 class="rel-h">').replace("</h2>", "</h3>")
    out = out.replace("<p>", '<div class="pg-chips">').replace("</p>", "</div>")
    out = out.replace(" · ", "").replace("<a ", '<a class="pg-chip is-link" ')
    return out


def _breadcrumbs_html(items: list[dict[str, str]]) -> str:
    parts = []
    for i, item in enumerate(items):
        name = html.escape(item["name"])
        if i == len(items) - 1:
            parts.append(f"<span>{name}</span>")
        else:
            parts.append(f'<a href="{html.escape(item["url"])}">{name}</a>')
    return '<nav class="crumbs" aria-label="Drobečková navigace">' + " › ".join(parts) + "</nav>"


def _breadcrumb_ld(items: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": item["name"], "item": item["url"]}
            for i, item in enumerate(items)
        ],
    }


def _related_html(store: Store, locality: str, offer: str, disposition: str = "") -> str:
    blocks: list[str] = []
    disp_links = []
    for disp in SEO_DISPOSITIONS:
        if disposition and disp == disposition:
            continue
        active, n = evaluate_market_page(store, locality, offer, disp)
        if active:
            disp_links.append(
                f'<a href="/trh/{slugify_locality(locality)}/{offer}/{slugify_disposition(disp)}">'
                f"{html.escape(disp)} ({_fmt_int(n)})</a>"
            )
    if disp_links:
        blocks.append("<h2>Související dispozice</h2><p>" + " · ".join(disp_links[:12]) + "</p>")
    parent = parent_locality(locality)
    links = []
    if parent:
        active, _ = evaluate_market_page(store, parent, offer)
        if active:
            links.append(f'<a href="/trh/{slugify_locality(parent)}/{offer}">{html.escape(parent)}</a>')
    for child in _children_for_district(locality)[:12]:
        active, _ = evaluate_market_page(store, child, offer)
        if active:
            links.append(f'<a href="/trh/{slugify_locality(child)}/{offer}">{html.escape(child)}</a>')
    if locality in MARKET_NEIGHBORHOODS and parent:
        for sib in _children_for_district(parent):
            if sib == locality:
                continue
            active, _ = evaluate_market_page(store, sib, offer)
            if active:
                links.append(f'<a href="/trh/{slugify_locality(sib)}/{offer}">{html.escape(sib)}</a>')
            if len(links) >= 16:
                break
    if links:
        blocks.append("<h2>Související lokality</h2><p>" + " · ".join(links[:16]) + "</p>")
    alt = "prodej" if offer == "pronajem" else "pronajem"
    active, _ = evaluate_market_page(store, locality, alt)
    if active:
        blocks.append(
            "<p>Stejná lokalita: "
            f'<a href="/trh/{slugify_locality(locality)}/{alt}">{offer_label(alt)}</a></p>'
        )
    return "\n".join(blocks)


def _narrative(
    locality: str,
    offer: str,
    report: dict[str, Any],
    *,
    disposition: str = "",
    parent_stats: dict[str, Any] | None = None,
    praha_stats: dict[str, Any] | None = None,
    cr_stats: dict[str, Any] | None = None,
) -> list[str]:
    paras: list[str] = []
    ol = offer_label(offer)
    count = int(report.get("active_count") or 0)
    med_m2 = report.get("median_price_per_m2")
    avg_m2 = report.get("avg_price_per_m2")
    med = report.get("median_price")
    avg = report.get("avg_price")
    subject = f"{ol} {disposition}" if disposition else ol
    as_of = product_facts.format_date_cs(str(report.get("updated_at") or "")[:10] or None)
    head = (
        f"K {as_of} je v lokalitě {locality} {_fmt_int(count)} aktivních nabídek typu {subject}"
    )
    bits = []
    if med is not None:
        bits.append(f"medián {_fmt_czk(med)}")
    if med_m2 is not None:
        bits.append(f"medián {_fmt_m2(med_m2)}")
    if avg_m2 is not None:
        bits.append(f"průměr {_fmt_m2(avg_m2)}")
    elif avg is not None:
        bits.append(f"průměr {_fmt_czk(avg)}")
    if bits:
        head += ": " + ", ".join(bits[:2]) + (f", {bits[2]}" if len(bits) > 2 else "") + "."
    else:
        head += "."
    paras.append(head)

    if parent_stats and parent_stats.get("active_count"):
        p_name = parent_stats.get("locality") or parent_locality(locality) or ""
        p_m2 = parent_stats.get("median_price_per_m2")
        if p_name and med_m2 is not None and p_m2:
            diff = round((float(med_m2) - float(p_m2)) * 100.0 / float(p_m2), 1)
            direction = "vyšší" if diff > 0 else "nižší"
            paras.append(
                f"Proti nadřazené lokalitě {p_name} je tu medián ceny za m² "
                f"asi {abs(diff)} % {direction} ({_fmt_m2(med_m2)} vs {_fmt_m2(p_m2)})."
            )
        elif p_name:
            paras.append(
                f"V {p_name} je celkem {_fmt_int(parent_stats.get('active_count'))} aktivních nabídek "
                f"stejného typu nabídky."
            )

    if praha_stats and locality != "Praha" and med_m2 is not None and praha_stats.get("median_price_per_m2"):
        p_m2 = float(praha_stats["median_price_per_m2"])
        diff = round((float(med_m2) - p_m2) * 100.0 / p_m2, 1)
        direction = "nad" if diff > 0 else "pod"
        paras.append(
            f"Oproti Praze jako celku je medián Kč/m² {abs(diff)} % {direction} pražským mediánem "
            f"({_fmt_m2(med_m2)} vs {_fmt_m2(p_m2)})."
        )
    elif (
        cr_stats
        and med_m2 is not None
        and cr_stats.get("median_price_per_m2")
        and not str(locality).startswith("Praha")
    ):
        c_m2 = float(cr_stats["median_price_per_m2"])
        if c_m2:
            diff = round((float(med_m2) - c_m2) * 100.0 / c_m2, 1)
            direction = "nad" if diff > 0 else "pod"
            paras.append(
                f"Proti celorepublikovému mediánu je cena za m² {abs(diff)} % {direction} "
                f"({_fmt_m2(med_m2)} vs {_fmt_m2(c_m2)})."
            )

    by_disp = [d for d in (report.get("by_disposition") or []) if (d.get("count") or 0) >= 5]
    if by_disp and not disposition:
        with_price = [d for d in by_disp if d.get("median_price_per_m2") or d.get("avg_price")]
        if len(with_price) >= 2:
            key = lambda d: float(d.get("median_price_per_m2") or d.get("avg_price") or 0)
            cheapest = min(with_price, key=key)
            dearest = max(with_price, key=key)
            if cheapest["disposition"] != dearest["disposition"]:
                cheap_v = cheapest.get("median_price_per_m2") or cheapest.get("avg_price")
                dear_v = dearest.get("median_price_per_m2") or dearest.get("avg_price")
                fmt = _fmt_m2 if cheapest.get("median_price_per_m2") is not None else _fmt_czk
                paras.append(
                    f"Nejnižší medián mezi dispozicemi má {cheapest['disposition']} "
                    f"({fmt(cheap_v)}, {_fmt_int(cheapest['count'])} nabídek); "
                    f"nejvyšší u {dearest['disposition']} "
                    f"({fmt(dear_v)}, {_fmt_int(dearest['count'])} nabídek)."
                )

    vanish = report.get("vanish_median_days")
    if vanish is not None and (report.get("vanish_sample_size") or 0) >= 10:
        paras.append(
            f"U nabídek, které v posledních 90 dnech z katalogu zmizely, byl medián doby na trhu "
            f"asi {vanish} dne (vzorek {_fmt_int(report.get('vanish_sample_size'))})."
        )

    mom = report.get("mom_new_pct")
    if mom is not None and (report.get("new_prev_30_days") or 0) >= 5:
        if mom > 0:
            paras.append(
                f"Počet nově zaznamenaných nabídek za posledních 30 dní "
                f"({_fmt_int(report.get('new_last_30_days'))}) je asi o {abs(mom)} % vyšší "
                f"než v předchozích 30 dnech ({_fmt_int(report.get('new_prev_30_days'))})."
            )
        elif mom < 0:
            paras.append(
                f"Počet nově zaznamenaných nabídek za posledních 30 dní "
                f"({_fmt_int(report.get('new_last_30_days'))}) je asi o {abs(mom)} % nižší "
                f"než v předchozích 30 dnech ({_fmt_int(report.get('new_prev_30_days'))})."
            )

    hist = report.get("history_90d") or []
    if len(hist) >= 2:
        first, last = hist[0], hist[-1]
        a = first.get("median_price_per_m2") or first.get("avg_price_per_m2")
        b = last.get("median_price_per_m2") or last.get("avg_price_per_m2")
        if a and b:
            paras.append(
                f"V datech za poslední měsíce (od {first.get('month') or first.get('week_start')} "
                f"do {last.get('month') or last.get('week_start')}) se medián Kč/m² u nových záznamů "
                f"pohyboval od {_fmt_m2(a)} k {_fmt_m2(b)}."
            )
    return paras


def _faq_items(locality: str, offer: str, report: dict[str, Any], disposition: str = "") -> list[dict[str, str]]:
    ol = offer_label(offer)
    as_of = product_facts.format_date_cs(str(report.get("updated_at") or "")[:10] or None)
    items: list[dict[str, str]] = []
    if disposition:
        items.append(
            {
                "q": f"Kolik stojí {ol} {disposition} v lokalitě {locality}?",
                "a": (
                    f"K {as_of} je medián {_fmt_czk(report.get('median_price'))} "
                    f"({_fmt_m2(report.get('median_price_per_m2'))}; "
                    f"průměr {_fmt_m2(report.get('avg_price_per_m2'))}; "
                    f"{_fmt_int(report.get('active_count'))} aktivních nabídek) "
                    f"pro {ol} {disposition} v lokalitě {locality}."
                ),
            }
        )
    else:
        items.append(
            {
                "q": f"Kolik stojí {ol} bytů v lokalitě {locality}?",
                "a": (
                    f"K {as_of} je medián {_fmt_czk(report.get('median_price'))} "
                    f"({_fmt_m2(report.get('median_price_per_m2'))}; "
                    f"průměr {_fmt_m2(report.get('avg_price_per_m2'))}; "
                    f"{_fmt_int(report.get('active_count'))} aktivních nabídek) "
                    f"pro {ol} bytů v lokalitě {locality}."
                ),
            }
        )
    by_disp = report.get("by_disposition") or []
    top = next((d for d in by_disp if (d.get("count") or 0) >= MIN_ACTIVE), None)
    if top and not disposition:
        items.append(
            {
                "q": f"Kolik nabídek {top.get('disposition')} je teď v lokalitě {locality}?",
                "a": (
                    f"K {as_of} je v lokalitě {locality} {_fmt_int(top.get('count'))} "
                    f"aktivních nabídek dispozice {top.get('disposition')}, "
                    f"průměrná cena {_fmt_czk(top.get('avg_price'))}."
                ),
            }
        )
    items.append(
        {
            "q": f"Kolik nových nabídek {offer_label_genitive(offer)} přibylo za týden?",
            "a": (
                f"K {as_of} katalog zaznamenal za posledních 7 dní "
                f"{_fmt_int(report.get('new_last_7_days'))} nových nabídek v lokalitě {locality}."
            ),
        }
    )
    if report.get("vanish_median_days") is not None and (report.get("vanish_sample_size") or 0) >= 10:
        items.append(
            {
                "q": "Jak rychle nabídky z trhu mizí?",
                "a": (
                    f"U zmizelých nabídek za 90 dní byl medián doby v katalogu "
                    f"{report.get('vanish_median_days')} dne."
                ),
            }
        )
    else:
        items.append(
            {
                "q": "Odkud Realitify bere čísla?",
                "a": "Z agregovaného katalogu veřejných inzerátů českých realitních portálů. " + DISCLAIMER_CS,
            }
        )
    return items[:4]


def _page_shell(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    json_ld: list[dict[str, Any]],
    og_image: str = "",
    md_href: str = "",
) -> str:
    from app import site_chrome

    extra = ""
    if og_image:
        extra = (
            f'\n    <meta property="og:image" content="{html.escape(og_image)}" />'
            '\n    <meta property="og:image:width" content="1200" />'
            '\n    <meta property="og:image:height" content="630" />'
            '\n    <meta name="twitter:card" content="summary_large_image" />'
            f'\n    <meta name="twitter:image" content="{html.escape(og_image)}" />'
        )
    return site_chrome.page(
        title=title,
        description=description,
        canonical=canonical,
        body=body,
        body_class="legal-page pg-page",
        markdown_alt=md_href or None,
        ld=json_ld,
        extra_head=extra,
    )


def build_title_description(locality: str, offer: str, report: dict[str, Any], disposition: str = "") -> tuple[str, str]:
    ol = offer_label(offer)
    count = _fmt_int(report.get("active_count"))
    med_m2 = report.get("median_price_per_m2")
    avg_m2 = report.get("avg_price_per_m2")
    med = report.get("median_price")
    if med_m2 is None or med is None:
        raise ValueError("not_enough_data")
    med_m2_s = _fmt_m2(med_m2)
    avg_m2_s = _fmt_m2(avg_m2) if avg_m2 is not None else ""
    med_s = _fmt_czk(med)
    if disposition:
        title = f"{ol.capitalize()} {disposition} {locality} – medián {med_m2_s}, {count} nabídek | Realitify"
        description = (
            f"Aktuální {ol} {disposition} v lokalitě {locality}: "
            f"{count} nabídek, medián {med_s}, medián {med_m2_s}"
            + (f", průměr {avg_m2_s}" if avg_m2_s else "")
            + ". Data z katalogu Realitify."
        )
    else:
        title = f"{ol.capitalize()} bytů {locality} – medián {med_m2_s}, {count} nabídek | Realitify"
        description = (
            f"Aktuální {ol} v lokalitě {locality}: {count} aktivních nabídek, "
            f"medián {med_s}, medián {med_m2_s}"
            + (f", průměr {avg_m2_s}" if avg_m2_s else "")
            + ". Srovnání a ukázka inzerátů."
        )
    return title, description


def render_trh_page(store: Store, locality: str, offer: str, disposition: str = "") -> str:
    report = store.catalog_locality_report(locality, offer, disposition)
    title, description = build_title_description(locality, offer, report, disposition)
    canonical = f"https://realitify.cz/trh/{slugify_locality(locality)}/{offer}"
    if disposition:
        canonical += f"/{slugify_disposition(disposition)}"
    og_image = canonical + "/og.png"

    parent_stats = None
    parent = parent_locality(locality)
    if parent:
        try:
            parent_stats = store.catalog_stats(parent, offer, disposition)
        except Exception:
            parent_stats = None
    praha_stats = None
    cr_stats = None
    try:
        if locality != "Praha":
            praha_stats = store.catalog_stats("Praha", offer, disposition)
    except Exception:
        pass
    try:
        cr_stats = store.catalog_offer_stats(offer, disposition)
    except Exception:
        pass

    related_report = report
    if disposition:
        try:
            related_report = store.catalog_locality_report(locality, offer)
        except ValueError:
            related_report = report

    crumbs = _breadcrumb_items(locality, offer, disposition)
    narrative = _narrative(
        locality, offer, report, disposition=disposition, parent_stats=parent_stats, praha_stats=praha_stats, cr_stats=cr_stats
    )
    faqs = _faq_items(locality, offer, report, disposition)
    samples = _sample_listing_items(store, locality=locality, offer=offer, q=disposition, limit=12)
    if disposition:
        filtered = [
            s
            for s in samples
            if disposition.lower() in (s.get("disposition") or "").lower()
            or disposition.lower() in (s.get("name") or "").lower()
        ]
        if len(filtered) >= 3:
            samples = filtered

    from app.market_pages import _stat_card

    chevron = (
        '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'
    )
    qa_html = "".join(
        f'<div class="qa" data-cat="all"><button type="button" class="qa-q" aria-expanded="false">'
        f'<span>{html.escape(f["q"])}</span><span class="qa-ico">{chevron}</span></button>'
        f'<div class="qa-a"><div><p>{html.escape(f["a"])}</p></div></div></div>'
        for f in faqs
    )
    narrative_html = "".join(f"<p>{html.escape(p)}</p>" for p in narrative)
    updated_label = product_facts.data_updated_label(report.get("updated_at"))
    date_modified = str(report.get("updated_at") or "")[:10] or None
    if not date_modified:
        from datetime import date as _date

        date_modified = _date.today().isoformat()
    md_path = f"/trh/{slugify_locality(locality)}/{offer}"
    if disposition:
        md_path += f"/{slugify_disposition(disposition)}"
    md_path += ".md"

    title_h1 = (
        f"{offer_label(offer).capitalize()} {disposition} – {locality}"
        if disposition
        else f"{offer_label(offer).capitalize()} bytů – {locality}"
    )
    stats_cards = "".join(
        [
            _stat_card("Aktivní nabídky", _fmt_int(report.get("active_count")), "v katalogu právě teď", dark=True),
            _stat_card("Medián ceny", _fmt_czk(report.get("median_price")), "prostřední nabídka"),
            _stat_card("Medián Kč/m²", _fmt_m2(report.get("median_price_per_m2")), "po filtru kvality"),
            _stat_card("Nové za 7 dní", _fmt_int(report.get("new_last_7_days")), "poprvé viděné nabídky"),
            _stat_card("Průměrná cena", _fmt_czk(report.get("avg_price")), "průměr všech aktivních"),
            _stat_card("Průměr Kč/m²", _fmt_m2(report.get("avg_price_per_m2")), "doplňkový ukazatel"),
        ]
    )
    listing_rows = "".join(
        f'<a class="listing-row" href="{html.escape(it["url"])}" rel="noopener noreferrer">'
        f'<span class="listing-name">{html.escape(it["name"])}</span>'
        f'<span class="listing-meta">'
        + "".join(
            f'<span class="pg-chip">{html.escape(str(b))}</span>'
            for b in (it.get("disposition"), it.get("portal"), it.get("price"))
            if b
        )
        + "</span></a>"
        for it in samples
    ) or '<p class="note">Momentálně nejsou k dispozici ukázkové nabídky.</p>'
    disp_section = ""
    if not disposition:
        disp_rows_html = "".join(
            f"<tr><td>{html.escape(str(r.get('disposition') or ''))}</td>"
            f"<td data-v=\"{int(r.get('count') or 0)}\">{int(r.get('count') or 0)}</td>"
            f"<td data-v=\"{r.get('avg_price') or 0}\">{_fmt_czk(r.get('avg_price'))}</td>"
            f"<td data-v=\"{r.get('median_price_per_m2') or r.get('avg_price_per_m2') or 0}\">"
            f"{_fmt_m2(r.get('median_price_per_m2') or r.get('avg_price_per_m2'))}</td></tr>"
            for r in (related_report.get("by_disposition") or [])
        )
        disp_section = f"""      <section class="pg-section" id="dispozice" data-nav="Dispozice" data-nav-desc="Ceny podle velikosti bytu">
        <div class="pg-head reveal"><p class="eyebrow">DISPOZICE</p><h2 class="display">Ceny podle dispozice</h2></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Dispozice</th><th data-sort>Počet</th><th data-sort>Průměr</th><th data-sort>Medián Kč/m²</th></tr></thead>
        <tbody>{disp_rows_html or '<tr><td colspan="4">Bez dat</td></tr>'}</tbody></table></div>
      </section>
"""
    hist_rows = "".join(
        f"<tr><td>{html.escape(str(h.get('month') or h.get('week_start') or ''))}</td>"
        f"<td>{int(h.get('new_count') or 0)}</td>"
        f"<td>{_fmt_m2(h.get('median_price_per_m2') or h.get('avg_price_per_m2'))}</td></tr>"
        for h in (report.get("history_90d") or [])
    )
    hist_section = (
        f"""      <section class="pg-section is-pale" id="vyvoj" data-nav="Vývoj" data-nav-desc="Posledních 90 dní">
        <div class="pg-head reveal"><p class="eyebrow">VÝVOJ</p><h2 class="display">Vývoj za posledních 90 dní</h2></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Období</th><th>Nové nabídky</th><th>Medián Kč/m²</th></tr></thead><tbody>{hist_rows}</tbody></table></div>
      </section>
"""
        if hist_rows
        else ""
    )
    related = _related_chips(_related_html(store, locality, offer, disposition))
    q_param = html.escape(locality)
    body = f"""      <section class="pg-hero" data-nav="Přehled" data-nav-desc="{html.escape(locality)} v číslech">
        {_breadcrumbs_html(crumbs)}
        <p class="eyebrow">{html.escape(locality.upper())} · {html.escape(offer_label(offer).upper())}</p>
        <h1 class="display">{html.escape(title_h1)}</h1>
        <p class="lead">Data z agregovaného katalogu Realitify. {html.escape(updated_label)}</p>
        <div class="pg-actions">
          <a class="pill pill-lg" href="/hledat?q={q_param}">Procházet nabídky</a>
          <a class="link-row" href="/index">Index nájmů →</a>
        </div>
      </section>

      <section class="pg-section" id="cisla" data-nav="Čísla" data-nav-desc="Klíčové ukazatele trhu">
        <div class="stat-grid">{stats_cards}</div>
        <div class="pg-card reveal narrative">{narrative_html}</div>
      </section>

      <section class="pg-section is-pale" id="nabidky" data-nav="Nabídky" data-nav-desc="Ukázka aktuálních nabídek">
        <div class="pg-head reveal"><p class="eyebrow">AKTUÁLNÍ NABÍDKY</p><h2 class="display">Ukázka nabídek v lokalitě</h2></div>
        <div class="listing-list">{listing_rows}</div>
        <div class="pg-actions"><a class="pill pill-lg" href="/hledat?q={q_param}">Všechny nabídky na mapě</a></div>
      </section>

{disp_section}{hist_section}      <section class="pg-section{'' if hist_section else ' is-pale'}" id="dotazy" data-nav="Dotazy" data-nav-desc="Časté otázky k lokalitě">
        <div class="pg-head reveal"><p class="eyebrow">ČASTÉ OTÁZKY</p><h2 class="display">Co lidé k lokalitě zajímá</h2></div>
        <div class="qa-list">{qa_html}</div>
      </section>

      <section class="pg-section is-pale" id="souvisejici" data-nav="Související" data-nav-desc="Další lokality a dispozice">
        <div class="pg-head reveal"><p class="eyebrow">SOUVISEJÍCÍ</p><h2 class="display">Další lokality a dispozice</h2></div>
        {related or '<p class="note">Zatím bez souvisejících stránek.</p>'}
        <p class="note">{html.escape(PRICE_METHODOLOGY_CS)}<br />{html.escape(DISCLAIMER_CS)}</p>
      </section>
"""
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": title,
        "description": description,
        "url": canonical,
        "creator": product_facts.dataset_creator(),
        "dateModified": date_modified,
        "temporalCoverage": date_modified,
        "license": product_facts.dataset_license(),
        "distribution": product_facts.dataset_distribution(
            "https://realitify.cz/index.csv", name="Realitify rent index CSV"
        ),
    }
    faq_ld = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faqs
        ],
    }
    return _page_shell(
        title=title,
        description=description,
        canonical=canonical,
        body=body,
        json_ld=[dataset, faq_ld, _breadcrumb_ld(crumbs)],
        og_image=og_image,
        md_href=f"https://realitify.cz{md_path}",
    )


def render_trh_page_md(store: Store, locality: str, offer: str, disposition: str = "") -> str:
    report = store.catalog_locality_report(locality, offer, disposition)
    updated_label = product_facts.data_updated_label(report.get("updated_at"))
    narrative = _narrative(locality, offer, report, disposition=disposition)
    faqs = _faq_items(locality, offer, report, disposition)
    lines = [
        f"# {locality} – {offer_label(offer)}" + (f" {disposition}" if disposition else ""),
        "",
        updated_label + ".",
        "",
    ]
    lines.extend(narrative)
    lines.append("")
    for f in faqs:
        lines.extend([f"## {f['q']}", "", f["a"], ""])
    lines.extend([PRICE_METHODOLOGY_CS, "", DISCLAIMER_CS, ""])
    return "\n".join(lines)


def render_index_month(store: Store, month: str) -> str:
    rows = store.index_month_city_stats(month)
    if not any(int(r.get("new_count") or 0) >= 20 for r in rows):
        raise ValueError("not_enough_data")
    month_label = czech_month_label(month)
    months = store.list_index_months(completed_only=True)
    from app.market_pages import _stat_card

    praha_row = next((r for r in rows if r["locality"] == "Praha"), rows[0])
    table = "".join(
        f"<tr><td>{html.escape(r['locality'])}</td>"
        f"<td data-v=\"{r['new_count'] or 0}\">{_fmt_int(r['new_count'])}</td>"
        f"<td data-v=\"{r.get('median_price') or 0}\">{_fmt_czk(r.get('median_price'))}</td>"
        f"<td data-v=\"{r.get('avg_price') or 0}\">{_fmt_czk(r.get('avg_price'))}</td>"
        f"<td data-v=\"{r.get('median_price_per_m2') or 0}\">{_fmt_m2(r.get('median_price_per_m2'))}</td>"
        f"<td data-v=\"{r.get('avg_price_per_m2') or 0}\">{_fmt_m2(r.get('avg_price_per_m2'))}</td></tr>"
        for r in rows
    )
    archive = "".join(
        f'<a class="pg-chip is-link" href="/index/{html.escape(m)}">{html.escape(czech_month_label(m))}</a>'
        for m in months[:18]
    )
    cards = "".join(
        [
            _stat_card("Nové nabídky v Praze", _fmt_int(praha_row.get("new_count")), month_label, dark=True),
            _stat_card("Medián nájmu", _fmt_czk(praha_row.get("median_price")), "Praha"),
            _stat_card("Medián Kč/m²", _fmt_m2(praha_row.get("median_price_per_m2")), "Praha"),
        ]
    )
    body = f"""      <section class="pg-hero" data-nav="Přehled" data-nav-desc="Souhrn měsíce">
        <nav class="crumbs" aria-label="Drobečková navigace"><a href="/index">Index nájmů</a> › <span>{html.escape(month_label)}</span></nav>
        <p class="eyebrow">ARCHIV INDEXU · {html.escape(month_label.upper())}</p>
        <h1 class="display">Index nájmů – {html.escape(month_label)}</h1>
        <p class="lead">Nabídky poprvé zaznamenané v katalogu Realitify v měsíci {html.escape(month_label)}. Trvalá adresa vhodná pro citace.</p>
        <div class="pg-actions"><a class="pill pill-lg" href="/hledat">Procházet nabídky</a><a class="link-row" href="/index">Aktuální index →</a></div>
      </section>

      <section class="pg-section" id="prague" data-nav="Praha" data-nav-desc="Klíčová čísla pro Prahu">
        <div class="stat-grid">{cards}</div>
      </section>

      <section class="pg-section is-pale" id="mesta" data-nav="Města" data-nav-desc="Všechny sledované lokality">
        <div class="pg-head reveal"><p class="eyebrow">PODLE LOKALITY</p><h2 class="display">Čísla za {html.escape(month_label)}</h2><p>Řadit můžete kliknutím na záhlaví sloupce.</p></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Lokalita</th><th data-sort>Nové</th><th data-sort>Medián</th><th data-sort>Průměr</th><th data-sort>Medián Kč/m²</th><th data-sort>Průměr Kč/m²</th></tr></thead><tbody>{table}</tbody></table></div>
      </section>

      <section class="pg-section" id="archiv" data-nav="Archiv" data-nav-desc="Další ukončené měsíce">
        <div class="pg-head reveal"><p class="eyebrow">ARCHIV</p><h2 class="display">Další měsíce</h2></div>
        <div class="pg-chips">{archive}</div>
        <p class="note">{html.escape(PRICE_METHODOLOGY_CS)}<br />Čísla vycházejí z first_seen v katalogu, ne z oficiální statistiky ČSÚ. {html.escape(DISCLAIMER_CS)}</p>
      </section>
"""
    praha = praha_row
    new_count = _fmt_int(praha.get("new_count"))
    med = _fmt_czk(praha.get("median_price"))
    med_m2 = _fmt_m2(praha.get("median_price_per_m2"))
    if praha.get("median_price") is None or praha.get("median_price_per_m2") is None:
        raise ValueError("not_enough_data")
    title = f"Realitify index nájmů – {month_label}"
    description = (
        f"Měsíční archiv indexu nájmů Realitify za {month_label}. "
        f"Praha: {new_count} nových, medián {med}, medián {med_m2}."
    )
    return _page_shell(
        title=title,
        description=description,
        canonical=f"https://realitify.cz/index/{month}",
        body=body,
        json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": title,
                "description": description,
                "url": f"https://realitify.cz/index/{month}",
                "creator": product_facts.dataset_creator(),
                "temporalCoverage": month,
                "dateModified": f"{month}-28",
                "license": product_facts.dataset_license(),
                "distribution": product_facts.dataset_distribution(
                    "https://realitify.cz/index.csv", name="Realitify rent index CSV"
                ),
            }
        ],
    )


def render_og_png(locality: str, offer: str, report: dict[str, Any], disposition: str = "") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1200, 630
    img = Image.new("RGB", (w, h), "#0e0f0c")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, w, 12], fill="#9fe870")
    draw.rectangle([0, h - 80, w, h], fill="#163300")
    try:
        font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 54)
        font_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except OSError:
        try:
            font_lg = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 54)
            font_md = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 36)
            font_sm = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 28)
        except OSError:
            font_lg = ImageFont.load_default()
            font_md = font_lg
            font_sm = font_lg
    ol = offer_label(offer).capitalize()
    draw.text((64, 80), "REALITIFY", fill="#9fe870", font=font_sm)
    draw.text((64, 140), locality[:40], fill="#ffffff", font=font_lg)
    draw.text((64, 220), (f"{ol} · {disposition}" if disposition else ol)[:50], fill="#c5cdc7", font=font_md)
    y = 320
    for line in (
        f"{_fmt_int(report.get('active_count'))} nabídek",
        f"medián {_fmt_m2(report.get('median_price_per_m2'))}",
        f"průměr {_fmt_m2(report.get('avg_price_per_m2'))}",
    ):
        draw.text((64, y), line, fill="#ffffff", font=font_md)
        y += 56
    draw.text((64, h - 52), "realitify.cz/trh", fill="#e2f6d5", font=font_sm)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def top_localities_html(store: Store, limit: int = 10) -> str:
    rows = top_localities(store, "pronajem", limit)
    if not rows:
        return ""
    links = " · ".join(f'<a href="{html.escape(r["path"])}">{html.escape(r["locality"])}</a>' for r in rows)
    return (
        '<section class="market-top" style="padding:32px var(--gutter);background:#f7f9f6">'
        '<p class="eyebrow">PŘEHLED TRHU</p>'
        '<h2 class="display" style="font-size:28px">TOP LOKALITY</h2>'
        f"<p>{links}</p>"
        '<p><a href="/hledat">Procházet všechny nabídky →</a></p>'
        "</section>"
    )
