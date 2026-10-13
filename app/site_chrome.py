"""Shared page chrome (head, header, footer) for server-rendered public pages.

The static pages under web/site/ carry the same header and footer markup;
scripts/sync_chrome.py rewrites them from FOOTER_HTML / HEADER_HTML so every
page stays identical, and tests/test_site_chrome.py guards the invariant.
"""

from __future__ import annotations

import html
import json
from typing import Any, Iterable

ASSET_V = {"site": "20", "nav": "1", "pages": "3", "navjs": "3", "pagesjs": "1"}

FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com" />\n'
    '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
    '    <link href="https://fonts.googleapis.com/css2?family=Manrope:wght@800&family='
    'Inter:ital,wght@0,400;0,600;0,700;0,800;1,400&display=swap" rel="stylesheet" />'
)

HEADER_HTML = """<header class="navbar">
      <a class="brand" href="/">
        <span class="icon" style="width:20px;height:32.5px"><img src="/static/site/assets/logo.svg" width="20" height="32.5" alt="" /></span>
        REALITIFY
      </a>
      <nav class="menu">
        <a href="/hledat">Nabídky</a>
        <a href="/index">Index nájmů</a>
        <a href="/uspechy">Vaše úspěchy</a>
        <a href="/#cenik">Ceník</a>
        <a href="/o-nas">O nás</a>
      </nav>
      <div class="nav-cta">
        <a class="nav-login" href="/prihlaseni">Přihlásit se</a>
        <a class="pill pill-nav" href="/registrace">Vyzkoušet zdarma</a>
      </div>
    </header>"""

FOOTER_HTML = """<footer class="footer">
      <div class="footer-top">
        <div class="footer-brand">
          <div class="footer-mark">
            <span class="bell-box"><span class="icon" style="width:18px;height:18px"><img src="/static/site/assets/bell-ring.svg" width="18" height="18" alt="" /></span></span>
            <span>REALIT<span class="alert-green">IFY</span></span>
          </div>
          <p>Agregátor nabídek bytů a domů z českých realitních portálů. Sjednocujeme inzeráty a doručujeme upozornění na nové shody.</p>
        </div>
        <div class="footer-cols">
          <div class="footer-col">
            <h3>Služba</h3>
            <a href="/#jak-to-funguje">Jak to funguje</a>
            <a href="/#srovnani">Srovnání hlídačů</a>
            <a href="/#cenik">Ceník a tarify</a>
            <a href="/mcp-docs">MCP pro AI</a>
          </div>
          <div class="footer-col">
            <h3>Zdroje</h3>
            <a href="/hledat">Vyhledávání nabídek</a>
            <a href="/index">Index nájmů</a>
            <a href="/faq">Časté dotazy</a>
            <a href="/o-nas">O nás</a>
            <a href="/hry">Hry</a>
            <a href="/uspechy">Blog o bydlení</a>
            <a href="/kontakt">Kontakt</a>
          </div>
          <div class="footer-col">
            <h3>Populární vyhledávání</h3>
            <a href="/pronajem-praha">Pronájem bytu Praha</a>
            <a href="/pronajem-brno">Pronájem bytu Brno</a>
            <a href="/trh/praha/pronajem">Ceny nájmů v Praze</a>
          </div>
          <div class="footer-col">
            <h3>Právní</h3>
            <a href="/obchodni-podminky">Obchodní podmínky</a>
            <a href="/ochrana-soukromi">Ochrana soukromí</a>
            <a href="/nastaveni-cookies">Nastavení cookies</a>
          </div>
        </div>
      </div>
      <div class="footer-bot">
        <p>© 2026 Realitify. Všechna práva vyhrazena. Realitify (realitify.cz) nesouvisí se společností Realtify ani PriceHubble. Služba není oficiálně spojena s portály Sreality.cz nebo Bezrealitky.cz.</p>
        <div class="soc">
          <span class="icon" style="width:18px;height:18px"><img src="/static/site/assets/facebook.svg" width="18" height="18" alt="" /></span>
          <span class="icon" style="width:18px;height:18px"><img src="/static/site/assets/twitter.svg" width="18" height="18" alt="" /></span>
          <span class="icon" style="width:18px;height:18px"><img src="/static/site/assets/instagram.svg" width="18" height="18" alt="" /></span>
        </div>
      </div>
    </footer>"""


def page(
    *,
    title: str,
    description: str,
    canonical: str,
    body: str,
    lang: str = "cs",
    body_class: str = "pg-page",
    robots: str | None = None,
    alternates: Iterable[tuple[str, str]] = (),
    markdown_alt: str | None = None,
    ld: Iterable[dict[str, Any]] = (),
    extra_head: str = "",
    scripts: str = "",
) -> str:
    """Full HTML document wrapped in the shared header/footer."""
    robots_meta = f'\n    <meta name="robots" content="{html.escape(robots)}" />' if robots else ""
    alt_links = "".join(
        f'\n    <link rel="alternate" hreflang="{html.escape(code)}" href="{html.escape(url)}" />'
        for code, url in alternates
    )
    md_link = (
        f'\n    <link rel="alternate" type="text/markdown" href="{html.escape(markdown_alt)}" />'
        if markdown_alt
        else ""
    )
    ld_tags = "".join(
        f'\n    <script type="application/ld+json">{json.dumps(item, ensure_ascii=False)}</script>' for item in ld
    )
    return f"""<!doctype html>
<html lang="{html.escape(lang)}">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="icon" href="/favicon.svg?v=2" type="image/svg+xml" />
    <link rel="icon" href="/favicon.ico?v=2" sizes="any" />
    <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=2" />
    <title>{html.escape(title)}</title>
    <meta name="description" content="{html.escape(description)}" />{robots_meta}
    <link rel="canonical" href="{html.escape(canonical)}" />{alt_links}{md_link}
    <meta property="og:type" content="website" />
    <meta property="og:site_name" content="Realitify" />
    <meta property="og:title" content="{html.escape(title)}" />
    <meta property="og:description" content="{html.escape(description)}" />
    <meta property="og:url" content="{html.escape(canonical)}" />{ld_tags}
    {FONTS_LINK}
    <link rel="stylesheet" href="/static/site/site.css?v={ASSET_V['site']}" />
    <link rel="stylesheet" href="/static/site/nav.css?v={ASSET_V['nav']}" />
    <link rel="stylesheet" href="/static/site/pages.css?v={ASSET_V['pages']}" />{extra_head}
  </head>
  <body class="{html.escape(body_class)}">
    {HEADER_HTML}
    <main class="pg" id="top">
{body}
    </main>
    {FOOTER_HTML}
    <script src="/static/site/nav.js?v={ASSET_V['navjs']}" defer></script>
    <script src="/static/site/pages.js?v={ASSET_V['pagesjs']}" defer></script>{scripts}
  </body>
</html>"""


def cta_band(title: str, text: str, button: str = "Vyzkoušet zdarma", href: str = "/registrace") -> str:
    return f"""      <section class="final-cta" data-nav="Začít" data-nav-desc="Vyzkoušejte hlídání zdarma">
        <div class="final-copy">
          <h2 class="display">{html.escape(title)}</h2>
          <p class="lead">{html.escape(text)}</p>
        </div>
        <a class="pill pill-lg" href="{html.escape(href)}">{html.escape(button)}</a>
      </section>"""


def not_found_page() -> str:
    body = """      <section class="pg-hero" data-nav="404">
        <p class="eyebrow">CHYBA 404</p>
        <h1 class="display">Tuhle stránku jsme nenašli</h1>
        <p class="lead">Odkaz je možná zastaralý, nebo byla stránka přesunuta. Zkuste jednu z těchto cest.</p>
        <div class="pg-actions">
          <a class="pill pill-lg" href="/hledat">Procházet nabídky</a>
          <a class="link-row" href="/">Zpět na úvodní stránku →</a>
        </div>
      </section>
      <section class="pg-section">
        <div class="feature-grid">
          <a class="feature-card" href="/hledat"><h3>Nabídky bytů</h3><p>Mapa a filtry ze všech sledovaných portálů.</p></a>
          <a class="feature-card" href="/index"><h3>Index nájmů</h3><p>Medián nájmů ve městech Česka.</p></a>
          <a class="feature-card" href="/faq"><h3>Časté dotazy</h3><p>Odpovědi na to, co se nás lidé ptají.</p></a>
        </div>
      </section>"""
    return page(
        title="Stránka nenalezena – Realitify",
        description="Stránka nebyla nalezena.",
        canonical="https://realitify.cz/",
        body=body,
        robots="noindex",
    )
