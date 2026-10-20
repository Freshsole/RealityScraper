"""Katalog automatických e-mailů Realitify: metadata, proměnné a ukázková data.

Texty (předmět, preheader, trigger, podmínka) vycházejí z wording doc v ``Marketing emails/``.
HTML šablony generuje ``scripts/build_marketing_emails.py`` do ``Marketing emails/<č>_<klíč>.html``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EMAILS_DIR = Path(__file__).resolve().parents[2] / "Marketing emails"


@dataclass(frozen=True)
class Var:
    name: str
    description: str
    sample: str
    raw: bool = False


@dataclass(frozen=True)
class TemplateDef:
    key: str
    number: str
    group: int
    group_name: str
    name: str
    stream: str
    trigger: str
    condition: str
    subject: str
    preheader: str
    html_file: str
    variables: tuple[Var, ...]


GROUPS: dict[int, str] = {
    1: "Transakční e-maily",
    2: "Onboarding (Zdarma)",
    3: "Upsell (Zdarma → Start / PRO)",
    4: "Trial a předplatné",
    5: "Alerty",
    6: "Retence a ukončení",
}

COMMON_VARS: tuple[Var, ...] = (
    Var("osloveni", "Oslovení: „Dobrý den, Jiří,“ nebo „Dobrý den,“ když chybí jméno", "Dobrý den, Jiří,"),
    Var("jmeno", "Křestní jméno (může být prázdné)", "Jiří"),
    Var("email", "E-mail příjemce", "jiri@example.cz"),
    Var("base_url", "Veřejná URL appky", "https://realitify.cz"),
    Var("nastaveni_url", "Odkaz na nastavení e-mailů a notifikací", "https://realitify.cz/nastaveni"),
    Var("odhlasit_url", "Podepsaný odkaz pro odhlášení z tipů a novinek", "https://realitify.cz/odhlasit?token=ukazka"),
    Var("podpora_email", "E-mail podpory", "podpora@realitify.cz"),
    Var("rok", "Aktuální rok", "2026"),
    Var("preheader", "Preheader šablony (vkládá renderer)", "Ukázkový preheader"),
)

_LINK = "https://realitify.cz/odkaz-ukazka"


def _v(name: str, description: str, sample: str, raw: bool = False) -> Var:
    return Var(name, description, sample, raw)


def _link(description: str = "Hlavní odkaz CTA (s UTM)") -> Var:
    return _v("odkaz", description, _LINK)


_PES = ("pes_nazev", "Název hlídacího psa", "Praha 2, 2+kk do 25 000 Kč")
_NABIDKY_3_1 = (
    '<tr><td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 15px/1.4 Inter,Arial,sans-serif;color:#163300;">'
    "2+kk, Praha 2 – Vinohrady<br><span style=\"font-weight:400;color:#555b54;\">24 500 Kč · Bezrealitky</span></td></tr>"
    '<tr><td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 15px/1.4 Inter,Arial,sans-serif;color:#163300;">'
    "2+kk, Praha 3 – Žižkov<br><span style=\"font-weight:400;color:#555b54;\">22 900 Kč · ČeskéReality</span></td></tr>"
    '<tr><td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 15px/1.4 Inter,Arial,sans-serif;color:#163300;">'
    "1+kk, Praha 2 – Nové Město<br><span style=\"font-weight:400;color:#555b54;\">19 800 Kč · RE/MAX</span></td></tr>"
)
_RADKY_5_2 = (
    '<tr><td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 14px/1.4 Inter,Arial,sans-serif;color:#163300;">2+kk, 54 m², Praha 2 – Vinohrady</td>'
    '<td style="padding:10px 8px;border-top:1px solid #e8ebe6;font:14px/1.4 Inter,Arial,sans-serif;color:#163300;white-space:nowrap;">24 500 Kč</td>'
    '<td style="padding:10px 8px;border-top:1px solid #e8ebe6;font:14px/1.4 Inter,Arial,sans-serif;color:#555b54;">Sreality</td>'
    '<td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 14px/1.4 Inter,Arial,sans-serif;"><a href="https://realitify.cz/odkaz-ukazka" style="color:#163300;">Otevřít</a></td></tr>'
    '<tr><td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 14px/1.4 Inter,Arial,sans-serif;color:#163300;">1+kk, 31 m², Praha 2 – Nové Město</td>'
    '<td style="padding:10px 8px;border-top:1px solid #e8ebe6;font:14px/1.4 Inter,Arial,sans-serif;color:#163300;white-space:nowrap;">19 800 Kč</td>'
    '<td style="padding:10px 8px;border-top:1px solid #e8ebe6;font:14px/1.4 Inter,Arial,sans-serif;color:#555b54;">Bazoš</td>'
    '<td style="padding:10px 0;border-top:1px solid #e8ebe6;font:600 14px/1.4 Inter,Arial,sans-serif;"><a href="https://realitify.cz/odkaz-ukazka" style="color:#163300;">Otevřít</a></td></tr>'
)
_UPSELL_5_1 = (
    '<p style="margin:16px 0 0;font:14px/1.5 Inter,Arial,sans-serif;color:#555b54;">Se Startem by vám tohle přišlo do 60 sekund. '
    '<a href="https://realitify.cz/odkaz-ukazka" style="color:#163300;font-weight:600;">Vyzkoušet zdarma</a></p>'
)


def _mk(number, key, group, name, stream, trigger, condition, subject, preheader, variables) -> TemplateDef:
    return TemplateDef(
        key=key,
        number=number,
        group=group,
        group_name=GROUPS[group],
        name=name,
        stream=stream,
        trigger=trigger,
        condition=condition,
        subject=subject,
        preheader=preheader,
        html_file=f"{number}_{key}.html",
        variables=tuple(Var(*v) if isinstance(v, tuple) else v for v in variables),
    )


T, L, A = "transactional", "lifecycle", "alerts"

TEMPLATES: tuple[TemplateDef, ...] = (
    # --- 1. Transakční ---
    _mk("1.1", "verify_email", 1, "Ověření e-mailu", T,
        "Registrace. Pokud uživatel neověří do 24 h, jednou znovu.", "Žádná.",
        "Potvrďte prosím svůj e-mail", "Jedno kliknutí a můžete začít hlídat nové nabídky.",
        [_link("Ověřovací odkaz (platí 24 h)")]),
    _mk("1.2", "password_reset", 1, "Reset hesla", T,
        "Žádost o reset hesla.", "Účet existuje (při neexistujícím účtu nic neposílat, na webu stejná hláška).",
        "Obnovení hesla k Realitify", "Odkaz pro nastavení nového hesla platí 1 hodinu.",
        [_link("Odkaz pro nastavení nového hesla (platí 1 h)")]),
    _mk("1.3a", "email_changed_old", 1, "Změna e-mailu: upozornění na starou adresu", T,
        "Změna e-mailu v nastavení. Posílá se na starou adresu.", "Žádná.",
        "E-mail k vašemu účtu byl změněn", "Pokud jste to nebyli vy, dejte nám vědět.",
        [_v("novy_email", "Nová e-mailová adresa účtu", "jiri.novy@example.cz")]),
    _mk("1.3b", "email_changed_confirm", 1, "Změna e-mailu: ověření nové adresy", T,
        "Změna e-mailu v nastavení. Posílá se na novou adresu.", "Žádná.",
        "Potvrďte prosím novou e-mailovou adresu", "Po potvrzení sem začnou chodit upozornění na nabídky.",
        [_link("Odkaz pro potvrzení nové adresy")]),
    _mk("1.4", "password_changed", 1, "Změna hesla", T,
        "Úspěšná změna hesla.", "Žádná.",
        "Heslo k Realitify bylo změněno", "Pokud jste to nebyli vy, ozvěte se nám.",
        [_v("datum_cas", "Datum a čas změny hesla", "20. 10. 2026 14:32"), _link("Odkaz pro obnovení hesla")]),
    _mk("1.5", "account_deleted", 1, "Smazání účtu", T,
        "Smazání účtu uživatelem.", "Žádná.",
        "Váš účet na Realitify byl smazán", "Hlídací psi jsou vypnutí a osobní údaje odstraněné.",
        []),
    # --- 2. Onboarding ---
    _mk("2.1", "welcome", 2, "Vítejte", L,
        "Ověření e-mailu.", "Žádná.",
        "Vítejte v Realitify, teď už vám žádný byt neuteče",
        "Nastavte si hlídacího psa a nové nabídky vám pošleme do 5 minut.",
        [_link("Odkaz na vytvoření hlídacího psa")]),
    _mk("2.2", "nudge_no_dog", 2, "Nudge: zatím nemáte hlídacího psa", L,
        "+1 den od ověření e-mailu.", "Uživatel nemá žádného psa. Zruší se, jakmile psa vytvoří.",
        "Byty v {{lokalita}} mizí do pár hodin", "Za včerejšek přibylo {{pocet}} nových nabídek. Hlídáte je?",
        [_v("lokalita", "Lokalita (když uživatel nic nehledal, Praha)", "Praha 2"),
         _v("pocet", "Počet nových nabídek za 24 h", "184"),
         _v("pes_filtr", "Lidsky čitelný popis předvyplněného filtru", "pronájem, Praha 2, 2+kk, do 25 000 Kč"),
         _link("Odkaz na zapnutí hlídání (předvyplněný filtr)")]),
    _mk("2.3", "first_dog_confirm", 2, "Potvrzení hlídacího psa", L,
        "Vytvoření prvního hlídacího psa.", "Žádná (u dalších psů neposílat).",
        "Hlídání je zapnuté: {{pes_nazev}}", "První shodu vám pošleme do 5 minut od zveřejnění.",
        [_v(*_PES), _v("pes_filtr", "Lidsky čitelný popis filtru", "pronájem, Praha 2, 2+kk, do 25 000 Kč"),
         _v("portaly", "Hlídané portály", "Sreality, Bezrealitky, Reality.iDNES, Bazoš"),
         _v("rychlost", "Rychlost upozornění podle tarifu", "do 5 minut"),
         _link("Odkaz na aktuální shody")]),
    _mk("2.4", "nudge_last_rent_index", 2, "Poslední nudge (Index nájmů)", L,
        "+3 dny od ověření e-mailu. Poté sekvence končí.", "Uživatel stále nemá psa.",
        "Kolik stojí nájem v {{lokalita}}?", "Aktuální přehled cen z 9 portálů a jak nepropásnout dobrou nabídku.",
        [_v("lokalita", "Lokalita", "Praha 2"), _v("pocet", "Počet nabídek, ze kterých jsou průměry", "1 240"),
         _v("cena_1kk", "Průměrný nájem 1+kk", "17 800"), _v("cena_2kk", "Průměrný nájem 2+kk", "24 300"),
         _v("cena_3kk", "Průměrný nájem 3+kk", "33 900"), _link("Odkaz na Index nájmů")]),
    _mk("2.5", "dog_no_matches", 2, "Pes bez shod", L,
        "7 dní od vytvoření psa.", "Pes má 0 shod.",
        "Za týden žádná shoda. Zkusíme filtr trochu povolit?", "S malou úpravou byste viděli {{pocet}} nabídek navíc.",
        [_v(*_PES), _v("pocet", "Celkový počet nabídek navíc po úpravě", "37"),
         _v("cena_navrh", "Navrhovaná maximální cena (+10 %)", "27 500"),
         _v("pocet_cena", "Počet nabídek po zvýšení ceny", "21"),
         _v("lokality_navrh", "Navrhované sousední lokality", "Praha 3, Praha 10"),
         _v("pocet_lokalita", "Počet nabídek po rozšíření lokality", "16"),
         _link("Odkaz na úpravu filtru")]),
    # --- 3. Upsell ---
    _mk("3.1", "upsell_missed_offers", 3, "Uteklo vám X nabídek", L,
        "1× týdně (pondělí 8:00). Max 4× za sebou, pak pauza 30 dní.",
        "Tarif Zdarma, v portálech mimo Free tarif bylo za týden ≥ 3 shody s filtrem psa. Mezi upselly aspoň 7 dní.",
        "{{pocet}} nabídek pro „{{pes_nazev}}“ jste minulý týden neviděli",
        "Objevily se na portálech, které tarif Zdarma nehlídá.",
        [_v(*_PES), _v("pocet", "Počet nabídek mimo Free portály", "5"),
         _v("nabidky_html", "Řádky tabulky s ukázkami nabídek (raw HTML)", _NABIDKY_3_1, True),
         _link("Odkaz na spuštění trialu Start")]),
    _mk("3.2", "upsell_dog_limit", 3, "Limit hlídacích psů", L,
        "Pokus vytvořit 2. psa na tarifu Zdarma. Zpoždění 1 hodina.", "Uživatel mezitím neupgradoval.",
        "Hledáte ve více lokalitách najednou?", "Se Startem máte 10 hlídacích psů na 9 portálech.",
        [_link("Odkaz na spuštění trialu Start")]),
    _mk("3.3", "upsell_speed", 3, "Rychlost upozornění", L,
        "Po 10. odeslaném alertu na tarifu Zdarma.", "Aspoň 1 z alertů je už neaktivní (inzerát stažen).",
        "{{pocet_stazeno}} z vašich {{pocet}} nabídek už zmizelo", "U dobrých bytů rozhoduje každá minuta.",
        [_v("pocet", "Počet odeslaných upozornění", "10"), _v("pocet_stazeno", "Kolik nabídek už je stažených", "4"),
         _link("Odkaz na spuštění trialu")]),
    # --- 4. Trial a předplatné ---
    _mk("4.1", "trial_started", 4, "Trial začal", T,
        "Aktivace 3denního trialu.", "Žádná.",
        "Váš {{tarif}} je aktivní, 3 dny zdarma", "Hlídáme všech 9 portálů. Zkušební doba končí {{datum_konec}}.",
        [_v("tarif", "Tarif (Start / PRO)", "Start"), _v("cena", "Cena tarifu", "149 Kč"),
         _v("datum_konec", "Datum konce trialu", "23. 10. 2026"),
         _v("rychlost", "Rychlost upozornění", "60 sekund"), _v("pocet_psu", "Počet hlídacích psů v tarifu", "10"),
         _link("Odkaz na nastavení hlídacích psů")]),
    _mk("4.2", "trial_ending", 4, "Trial končí zítra", T,
        "24 h před koncem trialu.", "Trial nebyl zrušen.",
        "Zítra končí zkušební doba {{tarif}}",
        "{{datum_konec}} strhneme {{cena}}. Pokud nechcete pokračovat, zrušte to jedním kliknutím.",
        [_v("tarif", "Tarif", "Start"), _v("cena", "Cena tarifu", "149 Kč"),
         _v("datum_konec", "Datum konce trialu", "23. 10. 2026"),
         _v("pocet", "Počet nových nabídek za poslední 2 dny", "42"),
         _v("pocet_navic", "Z toho na portálech mimo Free tarif", "17"),
         _link("Odkaz na předplatné"), _v("odkaz_zrusit", "Odkaz na zrušení předplatného", _LINK)]),
    _mk("4.3", "payment_ok", 4, "Platba proběhla", T,
        "Každá úspěšná platba (první i opakovaná).", "Žádná. Faktura v příloze.",
        "Platba za Realitify {{tarif}} – faktura {{cislo_faktury}}", "Děkujeme. Fakturu najdete v příloze.",
        [_v("tarif", "Tarif", "Start"), _v("cena", "Zaplacená částka", "149 Kč"),
         _v("obdobi_od", "Začátek období", "23. 10. 2026"), _v("obdobi_do", "Konec období", "22. 11. 2026"),
         _v("cislo_faktury", "Číslo faktury", "2026-000123"),
         _v("karta_typ", "Typ karty", "Visa"), _v("karta_posledni4", "Poslední 4 číslice karty", "4242"),
         _link("Odkaz na předplatné")]),
    _mk("4.4", "payment_failed", 4, "Platba selhala", T,
        "Neúspěšná platba. Opakování stejného e-mailu +3 dny a +7 dní, pak 4.5.", "Platba stále neprošla.",
        "Platba za Realitify neprošla, hlídání zatím běží", "Aktualizujte prosím platební kartu, ať o nic nepřijdete.",
        [_v("tarif", "Tarif", "Start"), _v("cena", "Částka platby", "149 Kč"),
         _v("datum_downgrade", "Datum přepnutí na Zdarma", "27. 10. 2026"),
         _link("Odkaz na aktualizaci platební karty")]),
    _mk("4.4b", "payment_failed_last", 4, "Platba selhala: poslední připomínka", T,
        "3. pokus po neúspěšné platbě.", "Platba stále neprošla.",
        "Poslední připomínka: zítra hlídání omezíme", "Aktualizujte prosím platební kartu, ať o nic nepřijdete.",
        [_v("tarif", "Tarif", "Start"), _v("cena", "Částka platby", "149 Kč"),
         _v("datum_downgrade", "Datum přepnutí na Zdarma", "27. 10. 2026"),
         _link("Odkaz na aktualizaci platební karty")]),
    _mk("4.5", "downgraded_after_failure", 4, "Přepnutí na Zdarma po neúspěšné platbě", T,
        "7 dní po prvním selhání platby.", "Platba stále neprošla.",
        "Váš účet jsme přepnuli na tarif Zdarma", "Hlídáme dál, ale jen 1 psa na 4 portálech.",
        [_v("tarif", "Původní tarif", "Start"), _v("pes_aktivni", "Název dál aktivního psa", "Praha 2, 2+kk do 25 000 Kč"),
         _v("pocet_pozastaveno", "Počet pozastavených psů", "3"), _link("Odkaz na obnovení tarifu")]),
    _mk("4.6", "subscription_canceled", 4, "Zrušení předplatného", T,
        "Uživatel zruší předplatné.", "Žádná.",
        "Předplatné jsme zrušili, {{tarif}} vám běží do {{datum_konec}}", "Další platby už nestrhneme.",
        [_v("tarif", "Tarif", "Start"), _v("datum_konec", "Datum konce předplatného", "22. 11. 2026"),
         _link("Odkaz na předplatné (obnovení)"),
         _v("odkaz_duvod_bydleni", "Důvod: našel/a jsem bydlení", _LINK),
         _v("odkaz_duvod_draha", "Důvod: je to drahé", _LINK),
         _v("odkaz_duvod_relevance", "Důvod: málo relevantních nabídek", _LINK),
         _v("odkaz_duvod_jiny", "Důvod: jiný", _LINK)]),
    _mk("4.7", "subscription_ended", 4, "Konec předplatného", T,
        "Den vypršení zrušeného předplatného.", "Žádná.",
        "Váš účet je teď na tarifu Zdarma", "Hlídáme dál 1 psa. Ostatní nastavení jsme uložili.",
        [_v("tarif", "Skončený tarif", "Start"), _v("pes_aktivni", "Název dál aktivního psa", "Praha 2, 2+kk do 25 000 Kč"),
         _link("Odkaz na hlídací psy")]),
    # --- 5. Alerty ---
    _mk("5.1", "alert_new_listing", 5, "Nová nabídka (okamžitý alert)", A,
        "Nová shoda s filtrem psa. 1 nabídka = 1 e-mail.", "Duplicitní inzerát z jiného portálu neposílat znovu.",
        "{{dispozice}}, {{plocha}} m², {{lokalita_detail}} – {{cena}} Kč",
        "Nová nabídka pro „{{pes_nazev}}“ na {{portal}}, zveřejněno před {{minuty}} min.",
        [_v("dispozice", "Dispozice", "2+kk"), _v("plocha", "Plocha v m²", "54"),
         _v("lokalita_detail", "Lokalita (čtvrť)", "Praha 2 – Vinohrady"), _v("cena", "Cena", "24 500"),
         _v("cena_suffix", "Přípona ceny (/měsíc u pronájmu)", "/měsíc"),
         _v(*_PES), _v("portal", "Portál inzerátu", "Sreality"), _v("minuty", "Minut od zveřejnění", "3"),
         _v("foto_url", "URL fotky nabídky", "https://realitify.cz/static/site/assets/db-1.png"),
         _v("adresa", "Adresa", "Korunní, Praha 2"), _v("poplatky", "Poplatky navíc", "+ 3 500 Kč poplatky"),
         _v("cas_zverejneni", "Čas zveřejnění", "dnes 14:29"),
         _v("rk_nebo_majitel", "Realitní kancelář nebo majitel", "Přímo od majitele"),
         _link("Odkaz na původní inzerát"), _v("odkaz_mapa", "Odkaz na mapu v Realitify", _LINK),
         _v("odkaz_upravit", "Odkaz na úpravu psa", _LINK), _v("odkaz_pozastavit", "Odkaz na pozastavení psa", _LINK),
         _v("upsell_html", "Řádek s nabídkou Startu (jen tarif Zdarma, max 1× denně; raw HTML)", _UPSELL_5_1, True)]),
    _mk("5.2", "alert_digest", 5, "Souhrn nabídek", A,
        "Uživatel si zvolil hodinový nebo denní souhrn místo okamžitých alertů.", "Aspoň 1 nová nabídka v období.",
        "{{pocet}} nových nabídek pro „{{pes_nazev}}“", "Nejlevnější: {{dispozice_min}}, {{cena_min}} Kč, {{lokalita_min}}.",
        [_v("pocet", "Počet nových nabídek", "7"), _v(*_PES),
         _v("dispozice_min", "Dispozice nejlevnější nabídky", "1+kk"), _v("cena_min", "Cena nejlevnější nabídky", "19 800"),
         _v("lokalita_min", "Lokalita nejlevnější nabídky", "Praha 2 – Nové Město"),
         _v("radky_html", "Řádky tabulky s nabídkami (raw HTML)", _RADKY_5_2, True),
         _link("Odkaz na všechny nabídky na mapě"), _v("odkaz_upravit", "Odkaz na úpravu psa", _LINK)]),
    _mk("5.3", "dog_inactive", 5, "Pes bez aktivity", A,
        "14 dní od poslední shody. Max 1× za 30 dní na psa.", "Pes je aktivní a dříve už nějaké shody měl (jinak platí 2.5).",
        "Pro „{{pes_nazev}}“ 14 dní nic nového", "Upravíme filtr, nebo hlídání pozastavíme?",
        [_v(*_PES), _v("pocet_cena", "Počet nabídek při ceně +10 %", "21"),
         _link("Odkaz na úpravu filtru"), _v("odkaz_pozastavit", "Odkaz na pozastavení psa", _LINK)]),
    _mk("5.4", "alerts_unopened", 5, "Upozornění nečtete", A,
        "10 alertů po sobě bez otevření nebo bounce.", "Při hard bounce neposílat (adresa nefunguje), jen banner v aplikaci.",
        "Dostáváte naše upozornění?", "Posledních 10 jste neotevřeli. Možná padají do spamu.",
        [_link("Odkaz na nastavení upozornění")]),
    # --- 6. Retence ---
    _mk("6.1", "found_home", 6, "Našli jste bydlení?", L,
        "30 a 60 dní od vytvoření prvního psa.", "Nějaký pes je aktivní. Končí, jakmile uživatel klikne na jakoukoli volbu.",
        "Už máte nové bydlení?", "Dejte nám vědět. Jedno kliknutí stačí.",
        [_v("pocet_dni", "Počet dní hlídání", "30"), _v("pocet", "Počet odeslaných upozornění", "58"),
         _link("Odkaz „Ano, našel/a jsem bydlení“"), _v("odkaz_hledam", "Odkaz „Ještě hledám“", _LINK),
         _v("odkaz_pozastavit", "Odkaz „Pozastavit hlídání na měsíc“", _LINK)]),
    _mk("6.1b", "found_home_followup", 6, "Gratulujeme k novému bydlení", L,
        "Po kliknutí na „Ano, našel/a jsem bydlení“ (hned).", "Žádná.",
        "Gratulujeme k novému bydlení", "Hlídací psy jsme pozastavili. Prozradíte nám, jak to proběhlo?",
        [_v("odmena", "Odměna za příběh", "3 měsíce PRO zdarma pro příští hledání"),
         _link("Odkaz na napsání příběhu (/uspechy)"), _v("odkaz_predplatne", "Odkaz na zrušení předplatného", _LINK)]),
    _mk("6.2", "market_monthly", 6, "Měsíční přehled trhu", L,
        "1. pracovní den v měsíci.", "Souhlas s tipy a novinkami. Lokalita = lokalita prvního psa (nebo Praha).",
        "Nájmy v {{lokalita}} za {{mesic}}: {{zmena_procenta}}", "Průměrný nájem 2+kk je teď {{cena_2kk}} Kč. Celý přehled uvnitř.",
        [_v("lokalita", "Lokalita", "Praha 2"), _v("mesic", "Měsíc", "září"), _v("zmena_procenta", "Změna nájmů za měsíc", "+1,8 %"),
         _v("cena_1kk", "Průměrný nájem 1+kk", "17 800"), _v("cena_2kk", "Průměrný nájem 2+kk", "24 300"),
         _v("cena_3kk", "Průměrný nájem 3+kk", "33 900"),
         _v("zmena_1kk", "Změna 1+kk", "+0,9 %"), _v("zmena_2kk", "Změna 2+kk", "+1,8 %"), _v("zmena_3kk", "Změna 3+kk", "−0,4 %"),
         _v("pocet_novych", "Počet nových nabídek za měsíc", "1 482"), _v("dny_zivotnost", "Průměrná životnost inzerátu ve dnech", "11"),
         _link("Odkaz na Index nájmů")]),
    _mk("6.3", "winback", 6, "Win-back", L,
        "30 dní po konci placeného předplatného. Druhý pokus po 90 dnech, pak už nic.",
        "Souhlas s tipy a novinkami. Důvod zrušení ≠ „Našel/a jsem bydlení“.",
        "Hledáte znovu? Vaše hlídací psy máme uložené", "Zapnete je jedním kliknutím, všech 9 portálů.",
        [_v("lokalita", "Lokalita", "Praha 2"), _v("pocet", "Počet nových odpovídajících nabídek", "126"),
         _v(*_PES), _link("Odkaz na obnovení hlídání")]),
    _mk("6.4", "pro_mcp", 6, "PRO: napojení na AI (MCP)", L,
        "Aktivace PRO (trial i placené), +1 den po 4.1.", "Uživatel MCP ještě nenapojil.",
        "Hledejte byty přímo v ChatGPT, Claude nebo Groku", "Napojení zabere 2 minuty.",
        [_link("Odkaz na návod k napojení")]),
)

_BY_KEY = {t.key: t for t in TEMPLATES}


def get_template(key: str) -> TemplateDef | None:
    return _BY_KEY.get(key)


def sample_vars(key: str) -> dict[str, str]:
    values = {v.name: v.sample for v in COMMON_VARS}
    tpl = _BY_KEY.get(key)
    if tpl:
        values.update({v.name: v.sample for v in tpl.variables})
        values["preheader"] = re.sub(r"\{\{\s*([a-z0-9_]+)\s*\}\}", lambda m: values.get(m.group(1), ""), tpl.preheader)
    return values


def read_default_html(key: str) -> str:
    tpl = _BY_KEY.get(key)
    if not tpl:
        return ""
    path = EMAILS_DIR / tpl.html_file
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
