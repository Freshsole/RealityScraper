#!/usr/bin/env python3
"""Vygeneruje HTML šablony automatických e-mailů do ``Marketing emails/``.

Spuštění: ``python3 scripts/build_marketing_emails.py``
Texty jsou z wording doc, design z design systému appky (web/site/site.css).
Šablony používají syntaxi ``{{x}}`` (escapované) a ``{{{x}}}`` (raw), viz docs/emailing-spec.md.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.emailing import catalog  # noqa: E402

OUT = ROOT / "Marketing emails"

FOREST, GREEN, MINT, PALE, SURFACE = "#163300", "#9fe870", "#e2f6d5", "#f7f9f6", "#e8ebe6"
INK, BODY, LINE, SOLD = "#0e0f0c", "#555b54", "#d0d4cd", "#cf2929"
UI = "Inter,Helvetica,Arial,sans-serif"
DISPLAY = "Manrope,Inter,Helvetica,Arial,sans-serif"

# ------------------------------------------------------------------ bloky --


def p(text: str, *, size: int = 16, color: str = BODY, mt: int = 0, mb: int = 16) -> str:
    return f'<p style="margin:{mt}px 0 {mb}px;font:400 {size}px/1.6 {UI};color:{color};">{text}</p>'


def small(text: str) -> str:
    return p(text, size=14, mb=12)


def h1(text: str) -> str:
    return (
        f'<h1 style="margin:0 0 20px;font:800 28px/1.15 {DISPLAY};letter-spacing:-0.02em;color:{FOREST};">{text}</h1>'
    )


def link(text: str, href: str, *, bold: bool = True) -> str:
    weight = 600 if bold else 400
    return f'<a href="{href}" style="color:{FOREST};font-weight:{weight};text-decoration:underline;">{text}</a>'


def button(label: str, href: str = "{{odkaz}}", *, outline: bool = False, mb: int = 24) -> str:
    bg = "#ffffff" if outline else GREEN
    border = f"2px solid {FOREST}" if outline else f"2px solid {GREEN}"
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 {mb}px;"><tr>'
        f'<td align="center" bgcolor="{bg}" style="border-radius:999px;border:{border};">'
        f'<a href="{href}" style="display:inline-block;padding:13px 30px;font:600 16px/20px {UI};color:{FOREST};'
        f'text-decoration:none;border-radius:999px;">{label}</a></td></tr></table>'
    )


def ul(items: list[str], *, ordered: bool = False) -> str:
    tag = "ol" if ordered else "ul"
    lis = "".join(f'<li style="margin:0 0 8px;padding-left:4px;">{i}</li>' for i in items)
    return (
        f'<{tag} style="margin:0 0 16px;padding:0 0 0 22px;font:400 16px/1.6 {UI};color:{BODY};">{lis}</{tag}>'
    )


def banner(inner: str, *, bg: str = MINT) -> str:
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px;">'
        f'<tr><td bgcolor="{bg}" style="background:{bg};border-radius:18px;padding:18px 22px;font:400 15px/1.6 {UI};color:{FOREST};">'
        f"{inner}</td></tr></table>"
    )


def table(rows: str, *, head: list[str] | None = None) -> str:
    thead = ""
    if head:
        cells = "".join(
            f'<th align="left" style="padding:0 8px 8px 0;font:800 11px/1.2 {UI};letter-spacing:1px;text-transform:uppercase;color:{BODY};">{h}</th>'
            for h in head
        )
        thead = f"<tr>{cells}</tr>"
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:0 0 20px;">'
        f"{thead}{rows}</table>"
    )


def row(*cells: str, head: bool = False) -> str:
    out = []
    for i, c in enumerate(cells):
        weight = 600 if (i == 0 or head) else 400
        color = FOREST if i == 0 else BODY
        out.append(
            f'<td style="padding:11px 8px 11px 0;border-top:1px solid {SURFACE};font:{weight} 15px/1.4 {UI};color:{color};">{c}</td>'
        )
    return "<tr>" + "".join(out) + "</tr>"


def kv(rows: list[tuple[str, str]]) -> str:
    return table("".join(row(k, v) for k, v in rows))


def rule() -> str:
    return f'<div style="height:1px;background:{SURFACE};margin:8px 0 20px;"></div>'


def sign(prefix: str = "") -> str:
    return p(f"{prefix}Tým Realitify".strip(), color=FOREST, mt=8, mb=0)


FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScjJhsLzt57s2Qa4Atc850IHKMM_WTvRtjKYBynS2ui5DjOxA/viewform?usp=dialog"

HI = p("{{osloveni}}")
HI_PLAIN = p("Dobrý den,")

# ------------------------------------------------------------------ layout --

FOOTERS = {
    "lifecycle": (
        "Tento e-mail dostáváte, protože máte účet na Realitify.cz. "
        '<a href="{{nastaveni_url}}" style="color:#555b54;text-decoration:underline;">Nastavení e-mailů</a> · '
        '<a href="{{odhlasit_url}}" style="color:#555b54;text-decoration:underline;">Odhlásit z tipů a novinek</a>'
    ),
    "transactional": (
        'Tento e-mail je součástí služby Realitify. '
        '<a href="{{nastaveni_url}}" style="color:#555b54;text-decoration:underline;">Nastavení notifikací</a>'
    ),
    "alerts": (
        'Upozornění z vašeho hlídacího psa na Realitify.cz. '
        '<a href="{{nastaveni_url}}" style="color:#555b54;text-decoration:underline;">Nastavení notifikací</a> · '
        '<a href="{{odhlasit_alerty_url}}" style="color:#555b54;text-decoration:underline;">Odhlásit upozornění</a>'
    ),
}


def layout(title: str, stream: str, body: str) -> str:
    filler = "&#847;&zwnj;&nbsp;" * 40
    return f"""<!doctype html>
<html lang="cs" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>{html.escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@800&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  body{{margin:0;padding:0;background:{PALE};}}
  table{{border-collapse:separate;border-spacing:0;}}
  img{{border:0;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;}}
  a{{color:{FOREST};}}
  @media (max-width:620px){{
    .em-wrap{{width:100%!important;}}
    .em-pad{{padding:28px 22px!important;}}
    .em-outer{{padding:0!important;}}
    .em-card{{border-radius:0!important;border-left:0!important;border-right:0!important;}}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{PALE};">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PALE};font-size:1px;line-height:1px;">{{{{preheader}}}}{filler}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{PALE}" style="background:{PALE};">
<tr><td align="center" class="em-outer" style="padding:32px 12px;">
  <table role="presentation" class="em-wrap" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
    <tr><td style="padding:0 4px 18px;">
      <a href="{{{{base_url}}}}" style="text-decoration:none;">
        <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
          <td style="vertical-align:middle;"><img src="{{{{base_url}}}}/static/site/assets/email-logo.png" width="40" height="40" alt="Realitify" style="display:block;border-radius:12px;"></td>
          <td style="vertical-align:middle;padding-left:10px;font:800 18px/1 {DISPLAY};letter-spacing:0.04em;color:{FOREST};">REALITIFY</td>
        </tr></table>
      </a>
    </td></tr>
    <tr><td class="em-card" bgcolor="#ffffff" style="background:#ffffff;border:1px solid {SURFACE};border-radius:24px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        <td class="em-pad" style="padding:36px 40px 32px;">
{body}
        </td></tr></table>
    </td></tr>
    <tr><td style="padding:22px 8px 0;font:400 12px/1.6 {UI};color:#868685;text-align:center;">
      {FOOTERS[stream]}<br>
      <span style="color:#a9b4ad;">© {{{{rok}}}} Realitify · {{{{podpora_email}}}}</span>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>
"""


# ------------------------------------------------------------------ obsah --
Body = str


def bodies() -> dict[str, Body]:
    B: dict[str, Body] = {}

    # 1. Transakční
    B["verify_email"] = (
        h1("Potvrďte svůj e-mail")
        + p("{{osloveni}}")
        + p("děkujeme za registraci na Realitify. Potvrďte prosím, že tahle adresa patří vám. Na ni vám budeme posílat upozornění na nové byty a domy.")
        + button("Potvrdit e-mail")
        + small("Odkaz platí 24 hodin. Pokud jste se neregistrovali, e-mail klidně ignorujte.")
        + sign()
    )
    B["password_reset"] = (
        h1("Obnovení hesla")
        + HI_PLAIN
        + p("někdo požádal o obnovení hesla k vašemu účtu na Realitify. Nové heslo si nastavíte tady:")
        + button("Nastavit nové heslo")
        + small("Odkaz platí 1 hodinu. Pokud jste o změnu nežádali, nic nedělejte, vaše heslo zůstává beze změny.")
        + sign()
    )
    B["email_changed_old"] = (
        h1("E-mail k účtu byl změněn")
        + HI_PLAIN
        + p("e-mail k vašemu účtu na Realitify byl právě změněn na <strong>{{novy_email}}</strong>. Upozornění na nabídky budou chodit na novou adresu.")
        + p('Pokud jste změnu neprovedli vy, odpovězte prosím na tento e-mail nebo napište na ' + link("{{podpora_email}}", "mailto:{{podpora_email}}") + ". Účet hned zablokujeme.")
        + sign()
    )
    B["email_changed_confirm"] = (
        h1("Potvrďte novou adresu")
        + HI_PLAIN
        + p("potvrďte prosím, že chcete u Realitify používat tuto adresu.")
        + button("Potvrdit novou adresu")
        + sign()
    )
    B["password_changed"] = (
        h1("Heslo bylo změněno")
        + HI_PLAIN
        + p("heslo k vašemu účtu na Realitify bylo právě změněno ({{datum_cas}}).")
        + p("Pokud jste to nebyli vy, obnovte si heslo přes odkaz níže a napište nám na " + link("{{podpora_email}}", "mailto:{{podpora_email}}") + ".")
        + button("Obnovit heslo")
        + sign()
    )
    B["account_deleted"] = (
        h1("Váš účet byl smazán")
        + HI_PLAIN
        + p("váš účet na Realitify jsme smazali. Všechny hlídací psy jsme vypnuli a osobní údaje odstranili. Údaje, které musíme ze zákona uchovat (například faktury), držíme jen po zákonem stanovenou dobu.")
        + p("Pokud jste měli aktivní předplatné, bylo zrušeno a další platby už nestrhneme.")
        + p("Děkujeme, že jste Realitify používali. Když budete znovu hledat bydlení, registrace trvá 30 sekund.")
        + sign()
    )

    # 2. Onboarding
    B["welcome"] = (
        h1("Vítejte v Realitify")
        + HI
        + p("vítejte v Realitify. Máte teď na jednom místě nabídky z 9 realitních portálů, bez duplicit a zdarma k prohlížení.")
        + p("Největší výhodu ale získáte s hlídacím psem. Zadáte lokalitu, cenu a dispozici a my vám každou novou shodu pošleme e-mailem do 5 minut od zveřejnění. Nemusíte tak každý den ručně procházet Sreality, Bezrealitky ani iDNES.")
        + p("Nastavení zabere minutu:", mb=8)
        + ul(["Vyberte lokalitu a pronájem nebo prodej.", "Zadejte maximální cenu a dispozici.", "Uložte. Hotovo."], ordered=True)
        + button("Nastavit hlídacího psa")
        + banner("<strong>Tip:</strong> přidejte si alerty@realitify.cz do kontaktů, ať vám upozornění nepadají do spamu nebo do Hromadné pošty.")
        + sign("Hodně štěstí při hledání,<br>")
    )
    B["nudge_no_dog"] = (
        h1("Byty mizí do pár hodin")
        + HI
        + p("za posledních 24 hodin přibylo v lokalitě <strong>{{lokalita}}</strong> {{pocet}} nových nabídek. Ty nejlepší bývají zarezervované dřív, než je stihnete najít ručně.")
        + p("Připravili jsme vám hlídacího psa podle toho, co jste hledali naposledy:")
        + banner("<strong>{{pes_filtr}}</strong>")
        + p("Stačí ho jedním kliknutím zapnout.")
        + button("Zapnout hlídání")
        + small("Podmínky si samozřejmě můžete před uložením upravit.")
        + sign()
    )
    B["first_dog_confirm"] = (
        h1("Váš hlídací pes běží")
        + HI
        + p("váš hlídací pes běží. Tohle hlídáme:")
        + banner("<strong>{{pes_filtr}}</strong><br>Portály: {{portaly}}<br>Rychlost upozornění: {{rychlost}}")
        + p("Aby vám nic neuteklo:", mb=8)
        + ul(["Přidejte si alerty@realitify.cz do kontaktů.", "V Gmailu přetáhněte náš e-mail do složky Primární.", "Na nový inzerát reagujte hned. U dobrých bytů rozhodují minuty."])
        + button("Zobrazit aktuální shody")
        + sign()
    )
    B["nudge_last_rent_index"] = (
        h1("Kolik stojí nájem v {{lokalita}}?")
        + HI
        + p("pro <strong>{{lokalita}}</strong> jsme spočítali aktuální průměrné nájmy z více než {{pocet}} nabídek:")
        + table(row("1+kk", "{{cena_1kk}} Kč") + row("2+kk", "{{cena_2kk}} Kč") + row("3+kk", "{{cena_3kk}} Kč"), head=["Dispozice", "Průměrný nájem"])
        + p("Celý přehled najdete v našem Indexu nájmů.")
        + button("Otevřít Index nájmů")
        + p("A až budete chtít hledat naostro, hlídacího psa nastavíte za minutu. Je zdarma a nové nabídky vám pošleme dřív, než je uvidí ostatní.")
        + sign()
    )
    B["dog_no_matches"] = (
        h1("Za týden žádná shoda")
        + HI
        + p("váš hlídací pes <strong>{{pes_nazev}}</strong> za posledních 7 dní nenašel žádnou nabídku. Nejspíš je filtr příliš úzký.")
        + p("Kdybyste ho trochu upravili, viděli byste:", mb=8)
        + ul(["cenu o 10 % vyšší ({{cena_navrh}} Kč): <strong>{{pocet_cena}} nabídek</strong>", "i sousední lokality ({{lokality_navrh}}): <strong>{{pocet_lokalita}} nabídek</strong>"])
        + button("Upravit filtr")
        + small("Pokud filtr sedí přesně tak, jak potřebujete, nic neměňte. Hlídáme dál a ozveme se hned, jak se něco objeví.")
        + sign()
    )

    # 3. Upsell
    B["upsell_missed_offers"] = (
        h1("Minulý týden vám něco uteklo")
        + HI
        + p("minulý týden se pro vašeho hlídacího psa <strong>{{pes_nazev}}</strong> objevilo {{pocet}} nabídek na portálech, které tarif Zdarma nehlídá. Upozornění na ně vám tedy nepřišlo.")
        + p("Například:", mb=4)
        + table("{{{nabidky_html}}}")
        + p("S tarifem Start hlídáme všech 9 portálů včetně ČeskéReality, Annonce, M&amp;M Reality, RE/MAX a Reality.cz. Upozornění pošleme do 60 sekund místo 5 minut.")
        + button("Vyzkoušet Start 3 dny zdarma")
        + small("Po 3 dnech 149 Kč měsíčně. Zrušit můžete kdykoli jedním kliknutím.")
        + sign()
    )
    B["upsell_dog_limit"] = (
        h1("Hledáte ve více lokalitách?")
        + HI
        + p("viděli jsme, že jste si chtěli nastavit dalšího hlídacího psa. Tarif Zdarma má jednoho, což často nestačí. Lidé obvykle hledají ve 2–3 čtvrtích nebo zvažují víc dispozic.")
        + p("Tarif Start vám dá:", mb=8)
        + ul(["10 hlídacích psů", "všech 9 portálů místo 4", "upozornění do 60 sekund", "pokročilé filtry: vyloučení realitních kanceláří, klíčová slova"])
        + button("Vyzkoušet Start 3 dny zdarma")
        + small("Po zkušební době 149 Kč měsíčně, bez závazku.")
        + sign()
    )
    cmp_head = ["", "Zdarma", "Start", "PRO"]
    cmp_rows = (
        row("Rychlost", "do 5 min", "do 60 s", "do 30 s")
        + row("Portály", "4", "9", "9")
        + row("Hlídací psi", "1", "10", "neomezeně")
        + row("Kanály", "e-mail", "e-mail", "e-mail, push, Discord, SMS")
        + row("Cena", "0 Kč", "149 Kč/měs.", "349 Kč/měs.")
    )
    B["upsell_speed"] = (
        h1("Některé nabídky už zmizely")
        + HI
        + p("poslali jsme vám už {{pocet}} upozornění na nové nabídky. {{pocet_stazeno}} z nich už inzerent stáhl, některé do pár hodin od zveřejnění.")
        + p("Na tarifu Zdarma vám upozornění chodí do 5 minut. Se Startem do 60 sekund, s PRO do 30 sekund. Majiteli tak voláte dřív než ostatní zájemci.")
        + table(cmp_rows, head=cmp_head)
        + button("Vyzkoušet 3 dny zdarma")
        + sign()
    )

    # 4. Trial a předplatné
    B["trial_started"] = (
        h1("Váš {{tarif}} je aktivní")
        + HI
        + p("vítejte v tarifu {{tarif}}. Od teď máte:", mb=8)
        + ul(["všech 9 portálů: Sreality, Bezrealitky, Reality.iDNES, Bazoš, ČeskéReality, Annonce, M&amp;M Reality, RE/MAX a Reality.cz", "upozornění do {{rychlost}}", "{{pocet_psu}} hlídacích psů", "pokročilé filtry: vyloučení realitních kanceláří, klíčová slova"])
        + p("Vaši stávající hlídací psi už automaticky hlídají všech 9 portálů. Doporučujeme ještě nastavit vyloučení realitních kanceláří, pokud hledáte bez provize.")
        + button("Nastavit hlídací psy")
        + banner("Zkušební doba končí <strong>{{datum_konec}}</strong>. Pak vám strhneme {{cena}} měsíčně. Den předem vám pošleme připomínku. Zrušit můžete kdykoli v Nastavení → Předplatné.")
        + sign()
    )
    B["trial_ending"] = (
        h1("Zítra končí zkušební doba")
        + HI
        + p("zítra (<strong>{{datum_konec}}</strong>) končí vaše zkušební doba tarifu {{tarif}}. Pokud nic neuděláte, strhneme z vaší karty {{cena}} a hlídání poběží dál bez přerušení.")
        + p("Za poslední 2 dny jsme pro vás našli <strong>{{pocet}} nových nabídek</strong>, z toho {{pocet_navic}} na portálech, které tarif Zdarma nehlídá.")
        + button("Pokračovat s {{tarif}}")
        + small("Nechcete pokračovat? " + link("Zrušit předplatné", "{{odkaz_zrusit}}") + ". Vrátíte se na tarif Zdarma a váš první hlídací pes poběží dál.")
        + sign()
    )
    B["payment_ok"] = (
        h1("Platba proběhla")
        + HI
        + p("děkujeme, platba proběhla v pořádku.")
        + kv([("Tarif", "{{tarif}}"), ("Částka", "{{cena}}"), ("Období", "{{obdobi_od}} – {{obdobi_do}}"), ("Faktura", "{{cislo_faktury}} (v příloze)"), ("Karta", "{{karta_typ}} končící {{karta_posledni4}}")])
        + p("Všechny faktury najdete v Nastavení → Předplatné.")
        + button("Zobrazit předplatné")
        + sign()
    )
    failed = lambda lead: (  # noqa: E731
        lead
        + HI
        + p("nepodařilo se nám strhnout platbu {{cena}} za tarif {{tarif}}. Nejčastěji jde o kartu s prošlou platností nebo nedostatek prostředků.")
        + p("Hlídání vám zatím necháváme běžet. Pokud platba neprojde do <strong>{{datum_downgrade}}</strong>, přepneme vás na tarif Zdarma a hlídání omezíme na 1 psa a 4 portály.")
        + button("Aktualizovat platební kartu")
        + small("Když si nevíte rady, odpovězte na tento e-mail a pomůžeme.")
        + sign()
    )
    B["payment_failed"] = failed(h1("Platba neprošla"))
    B["payment_failed_last"] = failed(h1("Poslední připomínka"))
    B["downgraded_after_failure"] = (
        h1("Přepnuli jsme vás na Zdarma")
        + HI
        + p("platbu za tarif {{tarif}} se nám nepodařilo strhnout, proto jsme váš účet přepnuli na tarif Zdarma.")
        + p("Co to znamená:", mb=8)
        + ul(["Dál hlídáme psa <strong>{{pes_aktivni}}</strong> na 4 portálech, upozornění do 5 minut.", "Ostatní psi ({{pocet_pozastaveno}}) jsou pozastavení. Nastavení jsme uložili."])
        + p("Jakmile předplatné obnovíte, všichni psi se hned znovu zapnou.")
        + button("Obnovit {{tarif}}")
        + sign()
    )
    B["subscription_canceled"] = (
        h1("Předplatné jsme zrušili")
        + HI
        + p("potvrzujeme zrušení předplatného {{tarif}}. Další platby už nestrhneme. Všechny funkce můžete používat do <strong>{{datum_konec}}</strong>, pak se účet přepne na tarif Zdarma.")
        + p("Pomůže nám, když nám řeknete, proč odcházíte. Stačí jedno kliknutí:", mb=10)
        + "".join(button(t, h, outline=True, mb=6) for t, h in [("Našel/a jsem bydlení", "{{odkaz_duvod_bydleni}}"), ("Je to pro mě drahé", "{{odkaz_duvod_draha}}"), ("Chodilo mi málo relevantních nabídek", "{{odkaz_duvod_relevance}}"), ("Jiný důvod", "{{odkaz_duvod_jiny}}")])
        + p("Kdybyste si to rozmysleli, předplatné obnovíte v " + link("Nastavení → Předplatné", "{{odkaz}}") + ".", mt=18)
        + sign()
    )
    B["subscription_ended"] = (
        h1("Váš účet je na tarifu Zdarma")
        + HI
        + p("vaše předplatné {{tarif}} dnes skončilo a účet běží na tarifu Zdarma.")
        + p("Dál hlídáme psa <strong>{{pes_aktivni}}</strong> na 4 portálech. Ostatní psi jsou pozastavení a jejich nastavení zůstává uložené.")
        + button("Zobrazit hlídací psy")
        + p("Děkujeme, že jste s námi byli.")
        + sign()
    )

    # 5. Alerty
    B["alert_new_listing"] = (
        '<img src="{{foto_url}}" width="520" alt="" style="display:block;width:100%;max-width:520px;height:auto;border-radius:18px;margin:0 0 20px;background:#e8ebe6;">'
        + f'<h1 style="margin:0 0 4px;font:800 26px/1.15 {DISPLAY};letter-spacing:-0.02em;color:{FOREST};">{{{{dispozice}}}}, {{{{plocha}}}} m²</h1>'
        + p("{{adresa}}", mb=14)
        + f'<p style="margin:0 0 2px;font:800 28px/1.2 {DISPLAY};color:{FOREST};">{{{{cena}}}} Kč{{{{cena_suffix}}}}</p>'
        + p("{{poplatky}}", size=14, mb=14)
        + p("{{portal}} · zveřejněno {{cas_zverejneni}}<br>{{rk_nebo_majitel}}", size=14, mb=6)
        + button("Zobrazit inzerát", mb=10)
        + p(link("Zobrazit na mapě v Realitify", "{{odkaz_mapa}}"), size=14, mb=0)
        + "{{{upsell_html}}}"
        + rule().replace("margin:8px 0 20px", "margin:24px 0 14px")
        + p("Hlídací pes: {{pes_nazev}} · " + link("Upravit", "{{odkaz_upravit}}", bold=False) + " · " + link("Pozastavit", "{{odkaz_pozastavit}}", bold=False), size=13, mb=0)
    )
    B["alert_digest"] = (
        h1("{{pocet}} nových nabídek")
        + HI
        + p("od posledního souhrnu přibylo {{pocet}} nových nabídek:")
        + table("{{{radky_html}}}", head=["Nabídka", "Cena", "Portál", ""])
        + button("Zobrazit všechny na mapě")
        + p("Nejlepší nabídky mizí rychle. Pokud chcete reagovat jako první, přepněte si v nastavení zpět na okamžitá upozornění.")
        + rule()
        + p("Hlídací pes: {{pes_nazev}} · " + link("Upravit", "{{odkaz_upravit}}", bold=False) + " · " + link("Nastavení upozornění", "{{nastaveni_url}}", bold=False), size=13, mb=0)
    )
    B["dog_inactive"] = (
        h1("14 dní nic nového")
        + HI
        + p("váš hlídací pes <strong>{{pes_nazev}}</strong> nenašel za posledních 14 dní žádnou novou nabídku. Trh v téhle kategorii je teď slabší, nebo je filtr příliš přísný.")
        + p("Co s tím:", mb=8)
        + ul(["<strong>Upravit filtr:</strong> s cenou +10 % byste viděli {{pocet_cena}} nabídek", "<strong>Pozastavit hlídání:</strong> když teď nehledáte", "<strong>Nechat běžet:</strong> nic nedělejte, ozveme se hned, jak se něco objeví"])
        + button("Upravit filtr", mb=10)
        + p(link("Pozastavit hlídání", "{{odkaz_pozastavit}}"), size=14, mb=16)
        + sign()
    )
    B["alerts_unopened"] = (
        h1("Dostáváte naše upozornění?")
        + HI
        + p("poslali jsme vám 10 upozornění na nové nabídky, ale žádné jste neotevřeli. Možná končí ve spamu, v Hromadné poště nebo v záložce Promo akce.")
        + p("Co pomůže:", mb=8)
        + ul(["Přidejte si alerty@realitify.cz do kontaktů.", "Přesuňte náš e-mail do složky Doručené / Primární.", "S tarifem PRO můžete dostávat upozornění i jako push notifikaci nebo na Discord."])
        + p("Už nehledáte? Hlídání pozastavte, ať vás nezahlcujeme.")
        + button("Upravit upozornění")
        + sign()
    )

    B["dog_auto_paused"] = (
        h1("Hlídání jsme pozastavili")
        + HI
        + p("pozastavili jsme hlídání <strong>{{pes_nazev}}</strong>, protože jste dlouho neotevřeli žádné naše upozornění. Chrání to doručitelnost e-mailů a nezahlcujeme vás zbytečně.")
        + p("Nastavení máte pořád uložené. Jakmile budete znovu hledat, stačí hlídání jedním kliknutím zapnout.")
        + button("Znovu zapnout hlídání")
        + sign()
    )

    # 6. Retence
    B["found_home"] = (
        h1("Už máte nové bydlení?")
        + HI
        + p("hlídáme pro vás nabídky už {{pocet_dni}} dní a poslali jsme vám {{pocet}} upozornění. Jak to vypadá?")
        + button("Ano, našel/a jsem bydlení", mb=14)
        + p(link("Ještě hledám", "{{odkaz_hledam}}") + " · " + link("Pozastavit hlídání na měsíc", "{{odkaz_pozastavit}}"), size=15, mb=16)
        + sign()
    )
    B["found_home_followup"] = (
        h1("Gratulujeme k novému bydlení")
        + HI
        + p("gratulujeme. Vaše hlídací psy jsme pozastavili, ať vám už nechodí upozornění. Nastavení zůstává uložené pro příští stěhování.")
        + p("Máte předplatné? " + link("Zrušit ho můžete tady", "{{odkaz_predplatne}}") + ". Žádné další platby.")
        + p("A jedna prosba: pomohlo by nám, kdybyste ve 2–3 větách napsali, jak jste byt našli. Nejlepší příběhy zveřejňujeme na webu a autory odměníme {{odmena}}.")
        + button("Napsat svůj příběh")
        + sign("Ať se vám v novém bydlení daří,<br>")
    )
    B["market_monthly"] = (
        h1("Nájmy v {{lokalita}} za {{mesic}}")
        + HI
        + p("posíláme měsíční přehled trhu pro <strong>{{lokalita}}</strong> z dat 9 realitních portálů.")
        + table(row("1+kk", "{{cena_1kk}} Kč", "{{zmena_1kk}}") + row("2+kk", "{{cena_2kk}} Kč", "{{zmena_2kk}}") + row("3+kk", "{{cena_3kk}} Kč", "{{zmena_3kk}}"), head=["Dispozice", "Průměrný nájem", "Změna za měsíc"])
        + p("Za {{mesic}} přibylo <strong>{{pocet_novych}}</strong> nových nabídek. Průměrný inzerát zmizel za {{dny_zivotnost}} dní.")
        + button("Otevřít Index nájmů")
        + sign()
    )
    B["winback"] = (
        h1("Hledáte znovu?")
        + HI
        + p("od vašeho odchodu přibylo v lokalitě {{lokalita}} {{pocet}} nových nabídek, které by odpovídaly vašemu psovi <strong>{{pes_nazev}}</strong>.")
        + p("Vaše nastavení máme stále uložené. Pokud zase hledáte, stačí jedno kliknutí a hlídání poběží jako dřív.")
        + button("Obnovit hlídání")
        + sign()
    )
    B["pro_mcp"] = (
        h1("Realitify ve vašem AI asistentovi")
        + HI
        + p("s tarifem PRO můžete Realitify napojit na svého AI asistenta. Pak se ho můžete zeptat třeba:", mb=12)
        + banner("„Najdi mi 2+kk v Praze 3 do 22 000 Kč s balkonem, bez realitky.“<br><br>„Kolik stojí průměrně 3+kk v Brně-Žabovřeskách?“<br><br>„Které z nabídek z tohoto týdne jsou pod průměrnou cenou?“")
        + p("Asistent hledá v aktuálním katalogu z 9 portálů, ne ve starých datech.")
        + button("Návod na napojení")
        + small("Návod je pro ChatGPT, Claude i Grok. Váš osobní klíč najdete v Nastavení → MCP.")
        + sign()
    )
    B["free_pro_form"] = (
        '<img src="{{base_url}}/static/site/assets/email-pro-free.png" width="520" alt="Realitify PRO zdarma" '
        'style="display:block;width:100%;max-width:520px;height:auto;margin:0 auto 20px;">'
        + h1("Měsíc Realitify PRO zdarma")
        + HI
        + p("kvůli nečekanému nárůstu uživatelů nám v poslední době nefungovaly e-maily tak, jak měly. Jako omluvu vám dáme <strong>Realitify PRO na měsíc zdarma</strong>.")
        + p("Stačí vyplnit krátký formulář a tarif PRO vám aktivujeme.")
        + p("Do doby, než e-maily znovu rozjedeme, doporučujeme zapnout si <strong>upozornění v prohlížeči</strong>. Nové nabídky vám tak dorazí hned.")
        + button("Vyplnit formulář", FORM_URL)
        + sign()
        + rule()
        + small("Omlouváme se. Dodavatel automatických e-mailových upozornění má momentálně problémy, "
                "a proto jsme museli dočasně vypnout automatické e-maily s novými nemovitostmi. "
                "V nejbližších dnech by měly e-maily opět fungovat jako dřív.")
    )
    return B


# ------------------------------------------------------------------ build --

_RAW = re.compile(r"\{\{\{\s*([a-z0-9_]+)\s*\}\}\}")
_ESC = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")


def render_sample(text: str, values: dict[str, str]) -> str:
    text = _RAW.sub(lambda m: values.get(m.group(1), ""), text)
    return _ESC.sub(lambda m: html.escape(values.get(m.group(1), "")), text)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    B = bodies()
    missing = [t.key for t in catalog.TEMPLATES if t.key not in B]
    if missing:
        raise SystemExit(f"Chybí obsah pro: {missing}")
    cards = []
    for t in catalog.TEMPLATES:
        page = layout(t.subject, t.stream, B[t.key])
        (OUT / t.html_file).write_text(page, encoding="utf-8")
        vals = catalog.sample_vars(t.key)
        rendered = render_sample(page, vals)
        subj = render_sample(t.subject, vals)
        cards.append(
            f'<section id="{t.key}"><header><span class="n">{t.number}</span><div><h2>{html.escape(t.name)}</h2>'
            f'<p><b>Předmět:</b> {html.escape(subj)}<br><b>Stream:</b> {t.stream} · <b>Trigger:</b> {html.escape(t.trigger)}'
            f'<br><b>Soubor:</b> {html.escape(t.html_file)}</p></div></header>'
            f'<iframe loading="lazy" srcdoc="{html.escape(rendered, quote=True)}"></iframe></section>'
        )
    gallery = f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Realitify e-maily: galerie</title>
<style>
body{{margin:0;background:{PALE};font:15px/1.5 {UI};color:{FOREST}}}
main{{max-width:1100px;margin:0 auto;padding:40px 20px}}
h1{{font:800 36px/1.1 {DISPLAY};margin:0 0 8px}}
.lead{{color:{BODY};margin:0 0 32px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(480px,1fr));gap:28px}}
section{{background:#fff;border:1px solid {SURFACE};border-radius:24px;overflow:hidden}}
header{{display:flex;gap:14px;padding:18px 20px;border-bottom:1px solid {SURFACE}}}
.n{{flex:none;height:36px;min-width:36px;padding:0 8px;border-radius:999px;background:{GREEN};display:flex;align-items:center;justify-content:center;font-weight:800}}
h2{{margin:0 0 4px;font:800 18px/1.2 {DISPLAY}}}
header p{{margin:0;font-size:13px;color:{BODY}}}
iframe{{width:100%;height:760px;border:0;background:{PALE}}}
</style></head><body><main>
<h1>Automatické e-maily Realitify</h1>
<p class="lead">{len(catalog.TEMPLATES)} šablon vykreslených s ukázkovými daty. Vygenerováno skriptem scripts/build_marketing_emails.py.</p>
<div class="grid">{''.join(cards)}</div>
</main></body></html>"""
    (OUT / "index.html").write_text(gallery, encoding="utf-8")
    print(f"OK: {len(catalog.TEMPLATES)} šablon + index.html v {OUT}")


if __name__ == "__main__":
    main()
