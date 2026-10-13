"""Generate web/site/pronajem-praha.html and pronajem-brno.html from one content table.

Run: python scripts/build_city_landings.py
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import site_chrome  # noqa: E402

CITIES = {
    "praha": {
        "name": "Praha", "loc": "v Praze", "q": "Praha",
        "title": "Pronájem bytu Praha 2026 | Nové byty k pronájmu — REALITIFY",
        "desc": "Pronájem bytu v Praze bez stresu. Realitify hlídá nové byty k pronájmu v Praze na 10 portálech 24/7 a upozorní vás mezi prvními. Byty 1+kk až 4+1 v Praze 1-10, Vinohrady, Žižkov, Karlín. Zkuste zdarma.",
        "lead": "Nejlepší nabídky mizí během hodin. Realitify hlídá <strong>10 českých realitních portálů 24/7</strong> a pošle vám upozornění do 60 sekund od zveřejnění inzerátu, takže budete mezi prvními, kdo se o novém bytu dozví.",
        "mock": ("Praha 2 – Vinohrady", "2+kk · 54 m²", "27 500 Kč / měsíc"),
        "why_title": "Jak najít pronájem bytu v Praze rychleji než ostatní",
        "why_lead": "Pražský trh s pronájmy je jeden z nejrychlejších v Evropě. Poptávka dlouhodobě převyšuje nabídku.",
        "why": [
            ("Hodiny, někdy minuty", "Kvalitní byty za rozumnou cenu mizí z portálů během několika hodin, ty nejatraktivnější i během desítek minut od zveřejnění."),
            ("Vybírá se z prvních zájemců", "Majitelé a realitky typicky vybírají z těch, kdo se ozvou jako první. Upozornění hned po zveřejnění je rozdíl mezi bytem a dalším měsícem hledání."),
            ("Hlídání každých 60 sekund", "Místo ručního procházení Sreality, Bezrealitky, iDNES Reality a Bazoše systém všechny portály kontroluje za vás a pošle notifikaci, jakmile se objeví byt podle vašich kritérií."),
        ],
        "prices_title": "Ceny pronájmů v Praze 2026",
        "prices_lead": "Orientační měsíční nájmy bez poplatků za služby a energie.",
        "prices": [("1+kk", "od 16 000 Kč", "garsonky a malé byty"), ("2+kk", "22–28 tis. Kč", "nejčastěji hledaná dispozice"), ("3+kk / 3+1", "28–38 tis. Kč", "pro páry a rodiny"), ("4+1", "od 35 tis. Kč", "velké byty")],
        "prices_note": "Ceny výrazně závisí na lokalitě. Nejdražší jsou Praha 1 (Staré Město, Malá Strana) a Praha 2 (Vinohrady, Nové Město). Dostupnější jsou Praha 9 (Vysočany, Prosek), Praha 10 (Vršovice, Strašnice) a okraje města s dobrým spojením metrem.",
        "live_link": ("/trh/praha/pronajem", "Aktuální medián a Kč/m² v Praze"),
        "districts_title": "Nejoblíbenější čtvrti pro pronájem v Praze",
        "districts": [
            ("Vinohrady", "Praha 2", "Secesní domy, Riegrovy sady, kavárny a skvělá dostupnost do centra. Pronájem 2+kk zde typicky stojí 26–32 tis. Kč."),
            ("Žižkov", "Praha 3", "Láká mladší generaci: hospody, kultura, autentická atmosféra a o něco příznivější ceny než Vinohrady."),
            ("Karlín", "Praha 8", "Po revitalizaci jedna z nejmodernějších čtvrtí. Nové developerské projekty, kanceláře, restaurace. Nájmy vyšší, standard také."),
            ("Holešovice", "Praha 7", "Industriální genius loci s galeriemi, tržnicí a náplavkou. Oblíbené u kreativců a mladých profesionálů."),
            ("Vršovice", "Praha 10", "Dobrý poměr ceny a kvality. Klidnější rezidenční čtvrť s výbornou dostupností do centra tramvají i vlakem."),
        ],
        "portals_title": "Kde hledat byty k pronájmu v Praze",
        "portals": [("Sreality", "největší inzertní portál v ČR"), ("Bezrealitky", "pronájmy přímo od majitelů bez provize"), ("Reality.iDNES", "silný v Praze i regionech"), ("Bazoš", "inzerce zdarma, často zajímavé nabídky"), ("ČeskéReality, Annonce, Reality.cz", "další zdroje nabídek")],
        "portals_note": "Každý portál musíte jinak kontrolovat zvlášť a několikrát denně. Realitify všech 10 portálů sleduje automaticky na jednom místě, odstraňuje duplicitní inzeráty a upozorní vás, když se objeví byt podle vašich představ.",
        "faq": [
            ("Kolik stojí pronájem bytu v Praze v roce 2026?", "Průměrný nájem bytu 2+kk v Praze se v roce 2026 pohybuje kolem 22 000 až 28 000 Kč měsíčně bez poplatků. Byty 1+kk začínají na přibližně 16 000 Kč, větší byty 3+1 a 4+1 v atraktivních lokalitách přesahují 35 000 Kč. Ceny se liší podle čtvrti - nejdražší je Praha 1 a Praha 2, dostupnější jsou okrajové části jako Praha 9 nebo Praha 10."),
            ("Jak rychle zmizí dobrý byt k pronájmu v Praze?", "Atraktivní byty k pronájmu v Praze za dobrou cenu mizí z portálů během několika hodin, ty nejlepší i během desítek minut. Proto je klíčové dostat upozornění okamžitě po zveřejnění inzerátu - Realitify odesílá notifikace do 60 sekund."),
            ("Kde hledat pronájem bytu v Praze?", "Nejvíce inzerátů najdete na portálech Sreality, Bezrealitky, Reality.iDNES a Bazoš. Realitify všechny tyto portály (celkem 10) sleduje automaticky 24/7 na jednom místě, takže nemusíte každý portál procházet ručně."),
            ("Jaké čtvrti v Praze jsou nejlepší k pronájmu?", "Mezi nejoblíbenější čtvrti patří Vinohrady, Žižkov, Karlín, Holešovice a Vršovice - kombinují dobrou dostupnost do centra, občanskou vybavenost a živou atmosféru. Pro rodiny jsou vhodné Dejvice, Střešovice nebo Klánovice. Nejlevnější pronájmy najdete typicky v Praze 9, Praze 10 a na okrajích města."),
        ],
        "related": [("/pronajem-brno", "Pronájem bytu Brno"), ("/trh/praha/pronajem", "Ceny nájmů v Praze"), ("/index", "Index nájmů"), ("/uspechy", "Příběhy uživatelů, kteří našli byt"), ("/#cenik", "Ceník hlídání bytů")],
        "cta": "Nenechte si ujít další byt v Praze",
        "cta_text": "Založte si bezplatný účet a dostávejte upozornění na nové byty k pronájmu v Praze mezi prvními.",
    },
    "brno": {
        "name": "Brno", "loc": "v Brně", "q": "Brno",
        "title": "Pronájem bytu Brno 2026 | Nové byty k pronájmu — REALITIFY",
        "desc": "Pronájem bytu v Brně bez stresu. Realitify hlídá nové byty k pronájmu v Brně na 10 portálech 24/7 a upozorní vás mezi prvními. Byty 1+kk až 4+1, centrum i okrajové části. Zkuste zdarma.",
        "lead": "Realitify hlídá <strong>10 českých realitních portálů 24/7</strong> a pošle vám upozornění do 60 sekund od zveřejnění inzerátu, takže budete mezi prvními, kdo se o novém bytu dozví.",
        "mock": ("Brno – Královo Pole", "2+kk · 52 m²", "21 500 Kč / měsíc"),
        "why_title": "Jak najít pronájem bytu v Brně",
        "why_lead": "Brno je druhé největší město Česka a univerzitní centrum, poptávka po pronájmech je tu stabilně vysoká, zejména na začátku akademického roku.",
        "why": [
            ("Dny, někdy hodiny", "Dobré byty k pronájmu v Brně mizí z portálů během dnů, ty nejlepší během hodin."),
            ("Všech 10 portálů každou minutu", "Realitify sleduje všech 10 hlavních realitních portálů každých 60 sekund. Jakmile se objeví byt podle vašich kritérií, dostanete okamžitou notifikaci."),
            ("Bez ručního procházení", "Nemusíte portály procházet ručně a o žádnou nabídku nepřijdete."),
        ],
        "prices_title": "Ceny pronájmů v Brně 2026",
        "prices_lead": "Orientační měsíční nájmy bez poplatků za služby a energie.",
        "prices": [("1+kk", "od 13 000 Kč", "garsonky a malé byty"), ("2+kk", "18–23 tis. Kč", "nejčastěji hledaná dispozice"), ("3+1", "23–30 tis. Kč", "pro páry a rodiny")],
        "prices_note": "Brno je oproti Praze cenově dostupnější zhruba o 20–30 %. Nejdražší je centrum a okolí Špilberku, dostupnější jsou sídlištní lokality jako Bystrc, Líšeň nebo Vinohrady.",
        "live_link": None,
        "districts_title": "Oblíbené čtvrti pro pronájem v Brně",
        "districts": [
            ("Veveří a Staré Brno", "střed", "Blízkost centra, historická zástavba, kavárny. Ideální pro ty, kdo chtějí bydlet v srdci dění."),
            ("Královo Pole a Žabovřesky", "sever", "Výborná dostupnost do centra i k univerzitám, klidnější rezidenční charakter, dobrá občanská vybavenost."),
            ("Bystrc", "u přehrady", "Rodinná čtvrť u Brněnské přehrady. Klid, zeleň, dostupnější nájmy, dobré spojení do centra."),
        ],
        "portals_title": None,
        "portals": [],
        "portals_note": "",
        "faq": [
            ("Kolik stojí pronájem bytu v Brně v roce 2026?", "Průměrný nájem bytu 2+kk v Brně se v roce 2026 pohybuje kolem 18 000 až 23 000 Kč měsíčně bez poplatků. Byty 1+kk začínají na přibližně 13 000 Kč. Brno je oproti Praze cenově dostupnější zhruba o 20 až 30 %."),
            ("Kde hledat pronájem bytu v Brně?", "Nejvíce inzerátů najdete na Sreality, Bezrealitky a Reality.iDNES. Realitify všech 10 hlavních portálů sleduje automaticky 24/7 na jednom místě a upozorní vás do 60 sekund od zveřejnění nového inzerátu."),
            ("Jaké čtvrti v Brně jsou nejlepší k pronájmu?", "Oblíbené jsou Veveří, Staré Brno, Královo Pole a Žabovřesky s dobrou dostupností do centra. Pro studenty jsou ideální lokality kolem VUT a Masarykovy univerzity. Klidnější rodinné bydlení nabízejí Bystrc, Kohoutovice nebo Líšeň."),
            ("Kdy je nejlepší hledat byt v Brně?", "Největší nabídka bývá v létě před začátkem semestru (červenec-září), ale konkurence je tehdy také nejvyšší. S automatickým hlídáním najdete byt celoročně."),
        ],
        "related": [("/pronajem-praha", "Pronájem bytu Praha"), ("/index", "Index nájmů"), ("/uspechy", "Příběhy uživatelů, kteří našli byt"), ("/#cenik", "Ceník hlídání bytů")],
        "cta": "Nenechte si ujít další byt v Brně",
        "cta_text": "Založte si bezplatný účet a dostávejte upozornění na nové byty k pronájmu v Brně mezi prvními.",
    },
}

ICON = {
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "layers": '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z"/>',
    "users": '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5"/><path d="M16 5a3.2 3.2 0 0 1 0 6M18 14.8c1.8.7 3 2.4 3 5.2"/>',
}
CHEV = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'


def svg(name: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>'
    )


def build(slug: str, c: dict) -> str:
    e = html.escape
    why_icons = ["clock", "users", "bolt"]
    why = "".join(
        f'<article class="feature-card reveal"><span class="feature-icon">{svg(why_icons[i])}</span><h3>{e(t)}</h3><p>{e(p)}</p></article>'
        for i, (t, p) in enumerate(c["why"])
    )
    prices = "".join(
        f'<article class="stat-card{" is-dark" if i == 1 else ""}"><p class="stat-label">{e(lay)}</p><p class="stat-value">{e(val)}</p><p class="stat-sub">{e(sub)}</p></article>'
        for i, (lay, val, sub) in enumerate(c["prices"])
    )
    grid_cls = "stat-grid is-4" if len(c["prices"]) == 4 else "stat-grid"
    live = (
        f'<a class="pg-chip is-link" href="{c["live_link"][0]}">{e(c["live_link"][1])} →</a>' if c["live_link"] else ""
    )
    districts = "".join(
        f'<article class="feature-card reveal"><span class="pg-chip">{e(tag)}</span><h3>{e(name)}</h3><p>{e(text)}</p>'
        f'<a class="link-row" href="/hledat?q={e(name.split(" ")[0])}" style="padding-left:0">Nabídky v lokalitě →</a></article>'
        for name, tag, text in c["districts"]
    )
    portals_section = ""
    if c["portals_title"]:
        rows = "".join(f'<div><dt>{e(n)}</dt><dd>{e(d)}</dd></div>' for n, d in c["portals"])
        portals_section = f"""      <section class="pg-section is-pale" id="portaly" data-nav="Portály" data-nav-desc="Kde se nabídky objevují">
        <div class="split">
          <div class="pg-head reveal"><p class="eyebrow">ZDROJE NABÍDEK</p><h2 class="display">{e(c["portals_title"])}</h2><p>{e(c["portals_note"])}</p></div>
          <div class="pg-card reveal"><dl class="fact-list">{rows}</dl></div>
        </div>
      </section>
"""
    qa = "".join(
        f'<div class="qa" data-cat="all"><button type="button" class="qa-q" aria-expanded="false"><span>{e(q)}</span><span class="qa-ico">{CHEV}</span></button>'
        f'<div class="qa-a"><div><p>{e(a)}</p></div></div></div>'
        for q, a in c["faq"]
    )
    related = "".join(f'<a class="pg-chip is-link" href="{h}">{e(t)}</a>' for h, t in c["related"])
    mock_loc, mock_spec, mock_price = c["mock"]
    body = f"""      <section class="pg-hero hero-split" data-nav="Přehled" data-nav-desc="Pronájem bytu {e(c['loc'])}">
        <div class="hero-copy">
          <p class="eyebrow">PRONÁJEM BYTU {e(c['name'].upper())}</p>
          <h1 class="display">Pronájem bytu {e(c['loc'])} bez stresu a zbytečného čekání</h1>
          <p class="lead">Hledáte byt k pronájmu {e(c['loc'])}? {c['lead']}</p>
          <div class="pg-chips"><span class="pg-chip">Upozornění do 60 sekund</span><span class="pg-chip">10 hlídaných portálů</span><span class="pg-chip">24/7</span></div>
          <div class="pg-actions">
            <a class="pill pill-lg" href="/registrace">Začít hlídat byty zdarma</a>
            <a class="link-row" href="/hledat?q={e(c['q'])}">Nabídky {e(c['loc'])} →</a>
          </div>
        </div>
        <div class="mock-card" aria-hidden="true">
          <div class="mock-top"><span class="mock-badge">NOVÁ NABÍDKA</span><span class="mock-time">před 38 sekundami</span></div>
          <picture><source type="image/avif" srcset="/static/site/assets/hero-apart.avif 1x, /static/site/assets/hero-apart@2x.avif 2x" /><source type="image/webp" srcset="/static/site/assets/hero-apart.webp 1x, /static/site/assets/hero-apart@2x.webp 2x" /><img class="mock-photo" src="/static/site/assets/hero-apart.webp" width="432" height="180" alt="" decoding="async" /></picture>
          <div class="mock-meta"><strong>{e(mock_loc)}</strong><span class="pg-chip">{e(mock_spec)}</span></div>
          <div class="mock-guess"><span>Nájem</span><b>{e(mock_price)}</b></div>
        </div>
      </section>

      <section class="pg-section" id="rychlost" data-nav="Rychlost" data-nav-desc="Proč záleží na minutách">
        <div class="pg-head reveal"><p class="eyebrow">PROČ ZÁLEŽÍ NA RYCHLOSTI</p><h2 class="display">{e(c['why_title'])}</h2><p>{e(c['why_lead'])}</p></div>
        <div class="feature-grid">{why}</div>
      </section>

      <section class="pg-section is-pale" id="ceny" data-nav="Ceny" data-nav-desc="Orientační nájmy podle dispozice">
        <div class="pg-head reveal"><p class="eyebrow">CENY</p><h2 class="display">{e(c['prices_title'])}</h2><p>{e(c['prices_lead'])}</p></div>
        <div class="{grid_cls}">{prices}</div>
        <p class="note">{e(c['prices_note'])}</p>
        <div class="pg-chips">{live}<a class="pg-chip is-link" href="/index">Index nájmů →</a></div>
      </section>

      <section class="pg-section" id="ctvrti" data-nav="Čtvrti" data-nav-desc="Kde se nejlépe bydlí">
        <div class="pg-head reveal"><p class="eyebrow">LOKALITY</p><h2 class="display">{e(c['districts_title'])}</h2></div>
        <div class="feature-grid">{districts}</div>
      </section>

{portals_section}      <section class="pg-section{'' if c['portals_title'] else ' is-pale'}" id="dotazy" data-nav="Dotazy" data-nav-desc="Časté otázky k pronájmu">
        <div class="pg-head reveal"><p class="eyebrow">ČASTÉ DOTAZY</p><h2 class="display">Časté dotazy k pronájmu bytu {e(c['loc'])}</h2></div>
        <div class="qa-list">{qa}</div>
      </section>

      <section class="pg-section{' is-pale' if c['portals_title'] else ''}" id="souvisejici" data-nav="Související" data-nav-desc="Další užitečné stránky">
        <div class="pg-head reveal"><p class="eyebrow">SOUVISEJÍCÍ STRÁNKY</p><h2 class="display">Mohlo by vás zajímat</h2></div>
        <div class="pg-chips">{related}</div>
      </section>
{site_chrome.cta_band(c['cta'], c['cta_text'], 'Začít hlídat zdarma')}"""
    url = f"https://realitify.cz/pronajem-{slug}"
    ld = [
        {
            "@context": "https://schema.org", "@type": "WebPage", "name": c["title"].split(" — ")[0],
            "url": url, "description": c["desc"].split(" a upozorní")[0] + ".", "inLanguage": "cs",
            "isPartOf": {"@type": "WebSite", "name": "Realitify", "url": "https://realitify.cz/"},
            "about": {"@type": "Place", "name": c["name"], "address": {"@type": "PostalAddress", "addressLocality": c["name"], "addressCountry": "CZ"}},
        },
        {
            "@context": "https://schema.org", "@type": "FAQPage",
            "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in c["faq"]],
        },
    ]
    return site_chrome.page(title=c["title"], description=c["desc"], canonical=url, body=body, robots="index, follow", ld=ld)


def main() -> None:
    for slug, c in CITIES.items():
        (ROOT / "web" / "site" / f"pronajem-{slug}.html").write_text(build(slug, c), encoding="utf-8")
        print("wrote", slug)


if __name__ == "__main__":
    main()
