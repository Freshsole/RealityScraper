"""Generate web/site/mcp-docs.html (EN) and mcp-docs-cs.html (CS) from one content table.

Run: python scripts/build_mcp_docs.py
"""

from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import site_chrome  # noqa: E402

URL = "https://mcp.realitify.cz/mcp"
HEALTH = "https://mcp.realitify.cz/health"

TOOLS = [
    ("search_listings", "Procházení a filtrování aktuálních nabídek: lokalita, offer_type, cena, dispozice, plocha, řazení newest | cheapest | best_value, max 20.",
     "Browse and filter current listings: locality, offer_type, price, layout, area, sort newest | cheapest | best_value, max 20.", "search"),
    ("new_listings", "Nabídky poprvé viděné za posledních X hodin.", "Listings first seen in the last N hours.", "clock"),
    ("get_listing", "Detail jedné nabídky podle id nebo listing_key z předchozího hledání.", "One listing by id or listing_key from a prior search.", "doc"),
    ("locality_stats", "Počet aktivních nabídek, průměr a medián ceny a Kč/m² pro lokalitu.", "Active count, average and median price and CZK/m² for a locality.", "chart"),
    ("price_check", "Je požadovaná cena férová oproti srovnatelným nabídkám (percentil, vs_avg_pct).", "Is an asking price fair versus comparable listings (percentile, vs_avg_pct).", "scale"),
    ("compare_localities", "Statistiky více lokalit vedle sebe.", "Statistics for two or more localities side by side.", "columns"),
]
ICONS = {
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "doc": '<path d="M7 3h7l5 5v13H7z"/><path d="M14 3v5h5M10 13h6M10 17h6"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "scale": '<path d="M12 3v18M5 7h14M5 7l-3 7a4 4 0 0 0 6 0L5 7ZM19 7l-3 7a4 4 0 0 0 6 0l-3-7Z"/>',
    "columns": '<rect x="3" y="4" width="7" height="16" rx="2"/><rect x="14" y="4" width="7" height="16" rx="2"/>',
}
PROMPTS_CS = [
    "Hledám 2+kk v Praze do 20 000 Kč měsíčně.",
    "Co nového přibylo v Praze 5 za posledních 24 hodin?",
    "Je 25 000 Kč za 2+kk 55 m² v Brně férová cena?",
    "Porovnej průměrný nájem v Praze, Brně a Ostravě.",
    "Otevři detail nabídky z předchozího hledání.",
]
PROMPTS_EN = [
    "Find a 2+kk in Prague under 20,000 CZK per month.",
    "What is new in Prague 5 in the last 24 hours?",
    "Is 25,000 CZK for a 55 m² 2+kk in Brno a fair price?",
    "Compare the average rent in Prague, Brno and Ostrava.",
    "Open the details of a listing from the previous search.",
]


def svg(name: str) -> str:
    return (
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>'
    )


def code_box(text: str, label: str, copy_label: str) -> str:
    return (
        f'<div class="code-box"><span class="code-label">{html.escape(label)}</span>'
        f'<code>{html.escape(text)}</code>'
        f'<button type="button" class="copy-btn" data-copy="{html.escape(text, quote=True)}">{html.escape(copy_label)}</button></div>'
    )


def build(lang: str) -> str:
    cs = lang == "cs"
    t = (lambda c, e: c if cs else e)
    prompts = PROMPTS_CS if cs else PROMPTS_EN
    tools = "".join(
        f'<article class="feature-card tool-card"><span class="feature-icon">{svg(icon)}</span>'
        f'<h3><code>{name}</code></h3><p>{html.escape(d_cs if cs else d_en)}</p></article>'
        for name, d_cs, d_en, icon in TOOLS
    )
    prompt_rows = "".join(
        f'<div class="cite-row"><p>„{html.escape(p)}“</p>'
        f'<button type="button" class="copy-btn" data-copy="{html.escape(p, quote=True)}">{t("Kopírovat", "Copy")}</button></div>'
        for p in prompts
    )
    other = ("/mcp-docs", "English") if cs else ("/mcp-docs-cs", "Česky")
    body = f"""      <section class="pg-hero hero-split" data-nav="MCP" data-nav-desc="{t('Připojte Realitify k AI', 'Connect Realitify to AI')}">
        <div class="hero-copy">
          <p class="eyebrow">MCP CONNECTOR</p>
          <h1 class="display">{t('Realitify přímo v Claude a ChatGPT', 'Realitify inside Claude and ChatGPT')}</h1>
          <p class="lead">{t('Veřejný read-only connector s českými nabídkami bytů. Hledání, nové nabídky, kontrola férovosti ceny a srovnání lokalit přímo z konverzace. Bez přihlášení a bez API klíče.', 'A public read-only connector with Czech property listings. Search, new listings, price checks and locality comparisons straight from a conversation. No login and no API key.')}</p>
          <div class="pg-chips">
            <span class="pg-chip">read-only</span>
            <span class="pg-chip">Streamable HTTP</span>
            <span class="pg-chip">{t('bez přihlášení', 'no login')}</span>
            <span class="pg-chip">CZK · m²</span>
          </div>
          <div class="pg-actions">
            <button type="button" class="pill pill-lg copy-btn-lg" data-copy="{URL}">{t('Kopírovat URL serveru', 'Copy server URL')}</button>
            <a class="link-row" href="{other[0]}">{other[1]} →</a>
          </div>
        </div>
        <div class="mock-card terminal" aria-hidden="true">
          <div class="mock-top"><span class="mock-badge">MCP</span><span class="mock-time">tools/call</span></div>
          <pre class="term-body">// ukázka volání / sample call
{{
  "tool": "price_check",
  "locality": "Praha 5",
  "disposition": "2+kk",
  "asking_price": 25000
}}
→ vs_avg_pct, percentile, comparables …</pre>
        </div>
      </section>

      <section class="pg-section" id="pripojeni" data-nav="{t('Připojení', 'Connect')}" data-nav-desc="{t('URL serveru a návod', 'Server URL and setup')}">
        <div class="pg-head reveal">
          <p class="eyebrow">{t('PŘIPOJENÍ', 'CONNECT')}</p>
          <h2 class="display">{t('Přidejte connector za minutu', 'Add the connector in a minute')}</h2>
          <p>{t('Stačí vložit adresu serveru. Transport je Streamable HTTP, odpovědi obsahují data_as_of, units, source a source_url.', 'Just paste the server address. Transport is Streamable HTTP; every response includes data_as_of, units, source and source_url.')}</p>
        </div>
        <div class="pg-card cite-card reveal">
          {code_box(URL, t('URL serveru', 'Server URL'), t('Kopírovat', 'Copy'))}
          {code_box(HEALTH, 'Health', t('Kopírovat', 'Copy'))}
        </div>
        <div class="feature-grid is-2">
          <article class="feature-card reveal"><h3>Claude</h3><p>{t('Otevřete Connectors a přidejte vzdálené MCP URL. Veřejný connector nevyžaduje API klíč.', 'Open Connectors and add the remote MCP URL. The public connector needs no API key.')}</p></article>
          <article class="feature-card reveal"><h3>ChatGPT</h3><p>{t('V ChatGPT Apps zvolte variantu With MCP a zadejte URL serveru. Podle potřeby ověřte doménu realitify.cz.', 'In ChatGPT Apps choose With MCP and enter the server URL. Verify the realitify.cz domain if asked.')}</p></article>
        </div>
      </section>

      <section class="pg-section is-pale" id="nastroje" data-nav="{t('Nástroje', 'Tools')}" data-nav-desc="{t('Šest read-only nástrojů', 'Six read-only tools')}">
        <div class="pg-head reveal">
          <p class="eyebrow">{t('NÁSTROJE', 'TOOLS')}</p>
          <h2 class="display">{t('Šest nástrojů pro práci s trhem', 'Six tools for working with the market')}</h2>
          <p>{t('Všechny mají readOnlyHint=true, destructiveHint=false, idempotentHint=true a openWorldHint=false. Lokality a dispozice se normalizují (Praha 5 / Smíchov / Prague 5; 2kk / 2+kk).', 'All tools set readOnlyHint=true, destructiveHint=false, idempotentHint=true and openWorldHint=false. Localities and layouts are normalised (Praha 5 / Smíchov / Prague 5; 2kk / 2+kk).')}</p>
        </div>
        <div class="feature-grid">{tools}</div>
      </section>

      <section class="pg-section" id="prompty" data-nav="{t('Prompty', 'Prompts')}" data-nav-desc="{t('Příklady, co se zeptat', 'Examples to try')}">
        <div class="split">
          <div class="pg-head reveal">
            <p class="eyebrow">{t('PŘÍKLADY', 'EXAMPLES')}</p>
            <h2 class="display">{t('Co se můžete zeptat', 'What you can ask')}</h2>
            <p>{t('Použijte, když hledáte byt, dům nebo se ptáte na ceny a dostupnost v Česku. Neznámá lokalita vrací chybu s návrhy, ne prázdný seznam.', 'Use it when you look for a flat or house in Czechia or ask about prices and availability. An unknown locality returns an error with suggestions, not an empty list.')}</p>
          </div>
          <div class="pg-card cite-card reveal">{prompt_rows}</div>
        </div>
      </section>

      <section class="pg-section is-pale" id="podpora" data-nav="{t('Podpora', 'Support')}" data-nav-desc="{t('Kontakt a související odkazy', 'Contact and related links')}">
        <div class="split">
          <div class="pg-head reveal">
            <p class="eyebrow">{t('PODPORA', 'SUPPORT')}</p>
            <h2 class="display">{t('Potřebujete pomoct?', 'Need help?')}</h2>
            <p>{t('Napište nám, rádi pomůžeme s připojením i s využitím dat.', 'Write to us, we are happy to help with setup and with using the data.')}</p>
            <p class="note">{t('Realitify (realitify.cz) nesouvisí se společností Realtify ani PriceHubble. Provozovatel: Jiří Kolb (OSVČ), IČO 21527059.', 'Realitify (realitify.cz) is not affiliated with Realtify or PriceHubble. Operator: Jiří Kolb (sole trader), Company ID 21527059.')}</p>
          </div>
          <div class="pg-card reveal">
            <dl class="fact-list">
              <div><dt>E-mail</dt><dd><a href="mailto:podpora@realitify.cz">podpora@realitify.cz</a></dd></div>
              <div><dt>{t('Index nájmů', 'Rent index')}</dt><dd><a href="/index">realitify.cz/index</a></dd></div>
              <div><dt>FAQ</dt><dd><a href="/faq">realitify.cz/faq</a></dd></div>
              <div><dt>llms.txt</dt><dd><a href="/llms.txt">/llms.txt</a> · <a href="/llms-full.txt">/llms-full.txt</a></dd></div>
            </dl>
          </div>
        </div>
      </section>
{site_chrome.cta_band(t('Hlídejte byty i bez AI', 'Watch listings without AI too'), t('Upozornění na nové nabídky podle vašich filtrů, zdarma vyzkoušíte bez karty.', 'New-listing alerts based on your filters, free to try without a card.'))}"""
    path = "mcp-docs-cs" if cs else "mcp-docs"
    return site_chrome.page(
        title=t("MCP dokumentace – Realitify", "MCP docs – Realitify"),
        description=t("Veřejný read-only MCP connector Realitify pro Claude a ChatGPT: české nabídky, ceny a srovnání lokalit.", "Realitify public read-only MCP connector for Claude and ChatGPT: Czech listings, prices and locality comparisons."),
        canonical=f"https://realitify.cz/{path}",
        lang="cs" if cs else "en",
        body=body,
        alternates=[("cs", "https://realitify.cz/mcp-docs-cs"), ("en", "https://realitify.cz/mcp-docs")],
    )


def main() -> None:
    for lang, name in (("en", "mcp-docs.html"), ("cs", "mcp-docs-cs.html")):
        (ROOT / "web" / "site" / name).write_text(build(lang), encoding="utf-8")
        print("wrote", name)


if __name__ == "__main__":
    main()
