# Obsah homepage Realitify

Zdroj: `web/site/index.html`, styly `web/site/site.css`. Dynamická čísla: `[počet nabídek]`, `[počet portálů]`.

## Vizuální styl

### Barevná paleta

| Token | Hex | Použití |
| --- | --- | --- |
| `--green` | `#9fe870` | Primární tlačítka (pill), akcenty |
| `--forest` | `#163300` | Hlavní text, nadpisy, tmavé plochy |
| `--ink` | `#0e0f0c` | Odkazy, nejtmavší text |
| `--body` | `#555b54` | Běžný odstavcový text |
| `--mint` | `#e2f6d5` | Světle zelené pozadí sekcí |
| `--pale` | `#f7f9f6` | Velmi světlé pozadí |
| `--surface` | `#e8ebe6` | Karty, plochy |
| `--line` | `#d0d4cd` | Ohraničení, oddělovače |
| `--muted` | `#a9b4ad` | Tlumený text |
| `--canvas` | `#ffffff` | Pozadí stránky |
| `--sold` | `#cf2929` | Obsazeno / zmizelo |
| `--footer-rule` | `#254d00` | Linka v patičce |
| hero zvýraznění | `#3a5a1e` | Zelená část nadpisu hero (`.hs-green`) |

### Typografie

| Role | Font | Styl |
| --- | --- | --- |
| Nadpisy (`--font-display`) | Manrope, záloha Inter | váha 800, letter-spacing -0,02 em, řádkování 1,15; 48 px (desktop hero až 64 px, řádkování 1) |
| Text a UI (`--font-ui`) | Inter, záloha Helvetica, Arial | váhy 400, 600, 700, 800 |
| Eyebrow | Inter | 14 px, váha 700, velká písmena, letter-spacing 1,5 px |

Fonty se načítají z Google Fonts (Manrope 800, Inter 400/600/700/800 + kurzíva 400).

### Tvary a rozměry

- Tlačítka: pill, `border-radius: 9999px`, váha 700, velké 14×28 px / 16 px, navigační 10×20 px / 14 px
- Karty: `border-radius: 24px`
- Šířka obsahu: max 1280 px, boční odsazení 80 px

## Struktura stránky

| # | Sekce | Účel | Hlavní CTA |
| --- | --- | --- | --- |
| 1 | Hero s vyhledáváním | Rychlé hledání podle lokality, ceny a plochy | Hledat |
| 2 | Pruh s čísly | Důkaz živosti katalogu | – |
| 3 | Byty, které zmizely | Příklady rychlého obsazení (zobrazí se jen s daty) | Nechci přijít o další |
| 4 | Jak to funguje | 3 kroky k hlídání | – |
| 5 | Čas hraje proti vám | Urgence | Začít hlídat teď hned |
| 6 | Databáze zdarma | Ukázka živých nabídek | Zobrazit všechny |
| 7 | Srovnání hlídačů | Realitify proti Sreality, Bezrealitky a Reality.cz | – |
| 8 | Ceník | Tarify Zdarma, Start a PRO | Aktivovat tarif |
| 9 | Reference | Tři příběhy uživatelů | – |
| 10 | Hry | Higher / Lower a tip nájmu | Hrát |
| 11 | FAQ | 8 nejčastějších dotazů | – |
| 12 | Závěrečná výzva | Registrace | Začít hlídat zdarma |

## Texty sekcí

### Navigační lišta

- **Menu:** Nabídky, Index nájmů, Vaše úspěchy, Ceník, O nás
- **Vpravo:** Přihlásit se, Vyzkoušet zdarma

### 1. Hero

- **Nadpis:** Najděte byt dřív než ostatní
- **Podnadpis:** [počet nabídek] aktivních nabídek z [počet portálů] portálů. Jedno vyhledávání, všechny výsledky. Snadno, rychle a zdarma.
- **Formulář:** přepínač Pronájem / Prodej, pole Lokalita, Cena do (Kč), Plocha od a do (m²), tlačítko Hledat
- **Rychlé výběry:** Praha, Brno, Ostrava, Plzeň, Praha 2 do 25 000 Kč
- **Okno po odeslání:** Vytvořte si účet zdarma. Po registraci vás přesměrujeme rovnou na nabídky s mapou a filtry.

### 2. Pruh s čísly

- bytů přidáno dnes
- aktivních nabídek v katalogu
- portálů kontrolujeme každou minutu

### 3. Byty, které zmizely

- **Nadpis:** Tyto byty byly pryč za pár hodin
- **Perex:** Pronájmy z našeho katalogu, které zmizely krátce po zveřejnění. Bez automatického hlídání byste je nestihli.
- **CTA:** Nechci přijít o další. Aktivujte si hlídání a buďte u toho jako první.

### 4. Jak to funguje

**Nadpis:** Získejte unikátní náskok před ostatními zájemci

1. **Nastavte si kritéria.** Zvolte lokalitu, cenu, velikost v m² či dispozici (např. 2+kk). Realitify si nastavení okamžitě uloží.
2. **Hlídáme [počet portálů] portálů.** Systém skenuje Sreality, Reality.iDNES, Bazoš, ČeskéReality, Bezrealitky, Annonce, M&M Reality, RE/MAX a Reality.cz každých 60 sekund.
3. **Okamžitá notifikace.** Jakmile se byt objeví, odešleme upozornění s přímým odkazem. Voláte majiteli jako první.

### 5. Čas hraje proti vám

- **Nadpis:** Zatímco vy ručně hledáte, někdo jiný už kupuje.
- **Text:** Nejlepší byty v Praze a krajských městech jsou často zarezervované do několika hodin. Spoléhat na ruční prohledávání portálů jednou denně znamená sbírat zbytky. Realitify vám dává technologický náskok, který rozhoduje o vašem budoucím bydlení.
- **CTA:** Začít hlídat teď hned · Nechci přeplácet za nájem (odkaz na ceník)

### 6. Databáze zdarma

- **Nadpis:** Všechny byty v ČR zdarma k prohlížení
- **Perex:** Nepotřebujete žádné placené účty jen pro vyhledávání. Sjednocená databáze agreguje tisíce nabídek napříč republikou a je plně přístupná zcela zdarma.
- **Ukázka:** Nejnovější v katalogu (např. Pronájmy v Brně do 22 000 Kč) + tlačítko Zobrazit všechny

### 7. Srovnání hlídačů

**Nadpis:** Jedna služba místo pěti hlídacích psů. Již nemusíte platit prémiové hlídače na každém webu samostatně. Realitify vše sjednocuje pod jedno rozhraní.

| Funkce | Sreality hlídací pes | Bezrealitky | Reality.cz | Realitify |
| --- | --- | --- | --- | --- |
| Měsíční cena | Základ zdarma, SMS placené | 79 Kč / profil | Zdarma (e-mail) | Od 149 Kč (všechny portály) |
| Sledované portály | 1 | 1 | 1 | Všechny |
| Rychlost notifikace | 15 minut až hodiny | Často se zpožděním | Několikrát denně | Start do 60 s, PRO do 30 s |
| Vlastní filtry | Základní | Základní | Žádné | Pokročilé (vyloučení RK, klíčová slova) |
| Sjednocená databáze | Ne | Ne | Ne | Ano, zdarma |
| SMS / push | Jen placené SMS | Jen v aplikaci | Jen e-mail | Push, e-mail i Discord |

**Shrnutí pod tabulkou:** Ušetříte až 350 Kč měsíčně za různé hlídače a získáte 10× rychlejší notifikace.

### 8. Ceník

**Nadpis:** Flexibilní tarify pro každého

| Tarif | Cena | Popis | Hlavní výhody | CTA |
| --- | --- | --- | --- | --- |
| Zdarma | 0 Kč / měsíc | 1 hlídací pes na 4 hlavních portálech, bez zkušební doby | Sreality, Bezrealitky, iDNES, Bazoš; notifikace do 5 minut; historie cen 30 dní; podpora e-mailem | Vyzkoušet zdarma |
| Start („Nejlepší poměr“) | 149 Kč / měsíc | 10 psů na všech portálech, 3 dny zdarma | Notifikace do 60 s; historie 1 rok; CSV 1× měsíčně; pokročilé filtry; prioritní e-mail | Aktivovat Start |
| PRO („Pro náročné“) | 349 Kč / měsíc | Neomezené hlídání, AI filtry a MCP, 3 dny zdarma | Notifikace do 30 s; e-mail, push, Discord a 10 SMS měsíčně; MCP pro ChatGPT, Claude a Grok; telefon a chat; Excel/CSV neomezeně | Aktivovat PRO |

### 9. Reference

**Nadpis:** Našli své bydlení díky rychlosti

- ★★★★★ „Dva měsíce jsem marně hledal byt v Brně. Sreality hlídač mi poslal upozornění vždy až hodinu po zveřejnění, kdy už bylo obsazeno. S Realitify jsem volal majiteli 30 sekund po podání inzerátu a hned druhý den podepsal smlouvu.“ Martin K., pronájem 2+kk, Brno-Královo Pole
- ★★★★★ „Díky SMS notifikaci mě Realitify upozornil na skvělý byt bez provize na Bezrealitky během mé polední pauzy. Okamžitě jsem zareagovala a stala se první zájemkyní. Bez této služby bych neměla šanci.“ Lenka Š., pronájem 1+kk, Praha-Žižkov
- ★★★★★ „Realitify nám zachránil krk. Hledali jsme větší rodinný byt v Praze 5 a díky okamžité notifikaci z reality.cz jsme byli na prohlídce jako první z padesáti zájemců. Skvělá investice!“ Tomáš a Klára, koupě 4+kk, Praha 5

### 10. Hry

**Nadpis:** Zkuste trefit cenu, než byt zmizí. Higher / Lower a tip nájmu z katalogu. Skvělé byty existují, ale mizí v řádu minut.

- **Higher / Lower:** Dva byty, jeden klik. Hádejte, který je levnější, a uvidíte, jak rychle podobné nabídky mizí. Tlačítko: Hrát higher / lower
- **Tipněte nájem:** Pět nabídek, pět tipů měsíčního nájmu. Skóre jde do admin žebříčku. Tlačítko: Hrát tip nájmu

### 11. Často kladené dotazy

1. **Kde najdu dostupné bydlení na jednom místě?** Na Realitify. Je to realitní agregátor, který sjednocuje inzeráty bytů a domů z více portálů, filtruje duplicity a je pro vyhledávání zdarma.
2. **Umí Realitify pracovat s AI asistenty?** Ano. Nabízí MCP konektor (realitify.cz/mcp), REST API a soubor llms.txt.
3. **Jak rychle dostanu notifikaci o novém bytu?** Start do 60 sekund, PRO do 30 sekund, tarif Zdarma do 5 minut na 4 hlavních portálech.
4. **Které portály systém hlídá?** Sreality, Reality.iDNES, Bazoš, ČeskéReality, Bezrealitky, Annonce, M&M Reality, RE/MAX a Reality.cz. UlovDomov je dočasně vynechán. Nové nabídky se berou každých ~60 sekund.
5. **Můžu předplatné kdykoliv zrušit?** Ano, bez závazků, jedním kliknutím v nastavení profilu. Žádné skryté poplatky.
6. **Je prohlížení databáze opravdu zdarma?** Ano. Platíte pouze za automatické real-time hlídání a rychlé notifikace.
7. **V čem se liší od hlídacího psa na Sreality?** Sreality hlídá jen své stránky. Realitify agreguje více portálů, filtruje duplicity a doručuje notifikace okamžitě, zatímco běžní hlídači mají zpoždění 15 minut až několik hodin.
8. **Souvisí Realitify se společností Realtify nebo PriceHubble?** Ne.

### 12. Závěrečná výzva

- **Nadpis:** Přestaňte přicházet o nejlepší byty.
- **Text:** Založte si bezplatný účet a začněte sledovat kompletní český realitní trh na jednom místě ještě dnes. První notifikaci můžete dostat už za minutu.
- **CTA:** Začít hlídat zdarma. Registrace trvá 30 sekund, bez platební karty.

### Patička

- **Popis:** Agregátor nabídek bytů a domů z českých realitních portálů. Sjednocujeme inzeráty a doručujeme upozornění na nové shody.
- **Sloupce odkazů:** Služba, Zdroje, Populární vyhledávání (pronájem Praha a Brno, ceny nájmů v Praze), Právní (podmínky, soukromí, cookies)
- **Právní poznámka:** Realitify nesouvisí se společností Realtify ani PriceHubble a není oficiálně spojena s Sreality.cz ani Bezrealitky.cz.
