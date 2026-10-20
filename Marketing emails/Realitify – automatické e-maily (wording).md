# Realitify – automatické e-maily (wording)

Oct 20, 2026 · @Jirka

## Úvod

Dokument obsahuje hotové texty všech 29 automatických e-mailů Realitify, rozdělené do 6 skupin. Každý e-mail má trigger, podmínku odeslání, předmět, preheader, tělo a CTA.

**Tón:** vykání, krátce, věcně, bez vykřičníků v předmětu. Jeden hlavní CTA na e-mail. Podpis „Tým Realitify“.

**Odesílatelé**

| Stream | Adresa | Použití |
| --- | --- | --- |
| Transakční | noreply@realitify.cz | účet, platby, faktury |
| Alerty | alerty@realitify.cz | notifikace nových nabídek |
| Lifecycle | tym@realitify.cz | onboarding, upsell, retence (odpověď chodí na podporu) |

**Proměnné**

| Proměnná | Význam |
| --- | --- |
| `{{jmeno}}` | křestní jméno; když chybí, oslovení vynechat („Dobrý den,“) |
| `{{pes_nazev}}` | název hlídacího psa, např. „Praha 2, 2+kk do 25 000 Kč“ |
| `{{pes_filtr}}` | lidsky čitelný popis filtru |
| `{{lokalita}}` | hlavní lokalita psa |
| `{{tarif}}` | Zdarma / Start / PRO |
| `{{cena}}` | cena tarifu, např. 149 Kč |
| `{{datum_konec}}` | datum konce trialu nebo předplatného |
| `{{pocet}}` | počet nabídek v daném kontextu |
| `{{odkaz}}` | hlavní odkaz CTA (s UTM) |

**Patička (lifecycle):** „Tento e-mail dostáváte, protože máte účet na Realitify.cz. \[Nastavení e-mailů\] · \[Odhlásit z tipů a novinek\]“. Transakční e-maily a alerty mají jen odkaz na nastavení notifikací.

## 1. Transakční e-maily

Odchází vždy, bez ohledu na souhlas s marketingem. Odesílatel noreply@realitify.cz.

### 1.1 Ověření e-mailu

**Trigger:** registrace · **Podmínka:** žádná · **Opakování:** pokud neověří do 24 h, jednou znovu

**Předmět:** Potvrďte prosím svůj e-mail **Preheader:** Jedno kliknutí a můžete začít hlídat nové nabídky.

Dobrý den{{, jmeno}},

děkujeme za registraci na Realitify. Potvrďte prosím, že tahle adresa patří vám. Na ni vám budeme posílat upozornění na nové byty a domy.

**\[Tlačítko: Potvrdit e-mail\]**

Odkaz platí 24 hodin. Pokud jste se neregistrovali, e-mail klidně ignorujte.

Tým Realitify

### 1.2 Reset hesla

**Trigger:** žádost o reset · **Podmínka:** účet existuje (při neexistujícím účtu nic neposílat, na webu stejná hláška)

**Předmět:** Obnovení hesla k Realitify **Preheader:** Odkaz pro nastavení nového hesla platí 1 hodinu.

Dobrý den,

někdo požádal o obnovení hesla k vašemu účtu na Realitify. Nové heslo si nastavíte tady:

**\[Tlačítko: Nastavit nové heslo\]**

Odkaz platí 1 hodinu. Pokud jste o změnu nežádali, nic nedělejte, vaše heslo zůstává beze změny.

Tým Realitify

### 1.3 Změna e-mailu

**Trigger:** změna e-mailu v nastavení · **Posílá se:** na starou adresu (upozornění) i na novou (ověření)

**Na starou adresu**

**Předmět:** E-mail k vašemu účtu byl změněn **Preheader:** Pokud jste to nebyli vy, dejte nám vědět.

Dobrý den,

e-mail k vašemu účtu na Realitify byl právě změněn na {{novy\_email}}. Upozornění na nabídky budou chodit na novou adresu.

Pokud jste změnu neprovedli vy, odpovězte prosím na tento e-mail nebo napište na podpora@realitify.cz. Účet hned zablokujeme.

Tým Realitify

**Na novou adresu**

**Předmět:** Potvrďte prosím novou e-mailovou adresu **Preheader:** Po potvrzení sem začnou chodit upozornění na nabídky.

Dobrý den,

potvrďte prosím, že chcete u Realitify používat tuto adresu.

**\[Tlačítko: Potvrdit novou adresu\]**

Tým Realitify

### 1.4 Změna hesla

**Trigger:** úspěšná změna hesla

**Předmět:** Heslo k Realitify bylo změněno **Preheader:** Pokud jste to nebyli vy, ozvěte se nám.

Dobrý den,

heslo k vašemu účtu na Realitify bylo právě změněno ({{datum\_cas}}).

Pokud jste to nebyli vy, obnovte si heslo přes odkaz níže a napište nám na podpora@realitify.cz.

**\[Tlačítko: Obnovit heslo\]**

Tým Realitify

### 1.5 Smazání účtu

**Trigger:** smazání účtu uživatelem

**Předmět:** Váš účet na Realitify byl smazán **Preheader:** Hlídací psi jsou vypnutí a osobní údaje odstraněné.

Dobrý den,

váš účet na Realitify jsme smazali. Všechny hlídací psy jsme vypnuli a osobní údaje odstranili. Údaje, které musíme ze zákona uchovat (například faktury), držíme jen po zákonem stanovenou dobu.

Pokud jste měli aktivní předplatné, bylo zrušeno a další platby už nestrhneme.

Děkujeme, že jste Realitify používali. Když budete znovu hledat bydlení, registrace trvá 30 sekund.

Tým Realitify

## 2. Onboarding (Zdarma)

Cíl celé sekvence: aby si uživatel do 3 dnů nastavil prvního hlídacího psa. Každý e-mail se zruší, jakmile uživatel cílovou akci udělá. Odesílatel tym@realitify.cz.

### 2.1 Vítejte

**Trigger:** ověření e-mailu · **Podmínka:** žádná

**Předmět:** Vítejte v Realitify, teď už vám žádný byt neuteče **Preheader:** Nastavte si hlídacího psa a nové nabídky vám pošleme do 5 minut.

Dobrý den{{, jmeno}},

vítejte v Realitify. Máte teď na jednom místě nabídky z 9 realitních portálů, bez duplicit a zdarma k prohlížení.

Největší výhodu ale získáte s hlídacím psem. Zadáte lokalitu, cenu a dispozici a my vám každou novou shodu pošleme e-mailem do 5 minut od zveřejnění. Nemusíte tak každý den ručně procházet Sreality, Bezrealitky ani iDNES.

Nastavení zabere minutu:

1. Vyberte lokalitu a pronájem nebo prodej.
2. Zadejte maximální cenu a dispozici.
3. Uložte. Hotovo.

**\[Tlačítko: Nastavit hlídacího psa\]**

Tip: přidejte si alerty@realitify.cz do kontaktů, ať vám upozornění nepadají do spamu nebo do Hromadné pošty.

Hodně štěstí při hledání, Tým Realitify

### 2.2 Nudge: zatím nemáte hlídacího psa

**Trigger:** +1 den od ověření · **Podmínka:** nemá žádného psa

**Předmět:** Byty v {{lokalita}} mizí do pár hodin **Preheader:** Za včerejšek přibylo {{pocet}} nových nabídek. Hlídáte je?

Dobrý den{{, jmeno}},

za posledních 24 hodin přibylo v lokalitě {{lokalita}} {{pocet}} nových nabídek. Ty nejlepší bývají zarezervované dřív, než je stihnete najít ručně.

Připravili jsme vám hlídacího psa podle toho, co jste hledali naposledy: **{{pes\_filtr}}**. Stačí ho jedním kliknutím zapnout.

**\[Tlačítko: Zapnout hlídání\]**

Podmínky si samozřejmě můžete před uložením upravit.

Tým Realitify

*Pozn.: Pokud uživatel nic nehledal, `{{lokalita}}` = Praha a filtr se nepředvyplňuje, CTA vede na prázdný formulář.*

### 2.3 Potvrzení hlídacího psa

**Trigger:** vytvoření prvního psa · **Podmínka:** žádná (u dalších psů neposílat)

**Předmět:** Hlídání je zapnuté: {{pes\_nazev}} **Preheader:** První shodu vám pošleme do 5 minut od zveřejnění.

Dobrý den{{, jmeno}},

váš hlídací pes běží. Tohle hlídáme:

**{{pes\_filtr}}** Portály: Sreality, Bezrealitky, Reality.iDNES, Bazoš Rychlost upozornění: do 5 minut

Aby vám nic neuteklo:

- Přidejte si alerty@realitify.cz do kontaktů.
- V Gmailu přetáhněte náš e-mail do složky Primární.
- Na nový inzerát reagujte hned. U dobrých bytů rozhodují minuty.

**\[Tlačítko: Zobrazit aktuální shody\]**

Tým Realitify

### 2.4 Poslední nudge

**Trigger:** +3 dny od ověření · **Podmínka:** stále nemá psa · **Poté:** sekvence končí

**Předmět:** Kolik stojí nájem v {{lokalita}}? **Preheader:** Aktuální přehled cen z 9 portálů a jak nepropásnout dobrou nabídku.

Dobrý den{{, jmeno}},

pro {{lokalita}} jsme spočítali aktuální průměrné nájmy z více než {{pocet}} nabídek:

| Dispozice | Průměrný nájem |
| --- | --- |
| 1+kk | {{cena\_1kk}} Kč |
| 2+kk | {{cena\_2kk}} Kč |
| 3+kk | {{cena\_3kk}} Kč |

Celý přehled najdete v našem Indexu nájmů.

**\[Tlačítko: Otevřít Index nájmů\]**

A až budete chtít hledat naostro, hlídacího psa nastavíte za minutu. Je zdarma a nové nabídky vám pošleme dřív, než je uvidí ostatní.

Tým Realitify

### 2.5 Pes bez shod

**Trigger:** 7 dní od vytvoření psa · **Podmínka:** pes má 0 shod

**Předmět:** Za týden žádná shoda. Zkusíme filtr trochu povolit? **Preheader:** S malou úpravou byste viděli {{pocet}} nabídek navíc.

Dobrý den{{, jmeno}},

váš hlídací pes **{{pes\_nazev}}** za posledních 7 dní nenašel žádnou nabídku. Nejspíš je filtr příliš úzký.

Kdybyste ho trochu upravili, viděli byste:

- cenu o 10 % vyšší ({{cena\_navrh}} Kč): **{{pocet\_cena}} nabídek**
- i sousední lokality ({{lokality\_navrh}}): **{{pocet\_lokalita}} nabídek**

**\[Tlačítko: Upravit filtr\]**

Pokud filtr sedí přesně tak, jak potřebujete, nic neměňte. Hlídáme dál a ozveme se hned, jak se něco objeví.

Tým Realitify

## 3. Upsell (Zdarma → Start / PRO)

Všechny upselly stojí na konkrétních číslech z dat uživatele, ne na obecných slevách. Posílají se jen uživatelům na tarifu Zdarma, kteří zatím trial nevyužili. Mezi dvěma upselly vždy aspoň 7 dní pauza.

### 3.1 Uteklo vám X nabídek

**Trigger:** 1× týdně (pondělí 8:00) · **Podmínka:** v 5 portálech mimo Free tarif bylo za týden ≥ 3 shody s filtrem psa · **Max:** 4× za sebou, pak pauza 30 dní

**Předmět:** {{pocet}} nabídek pro „{{pes\_nazev}}“ jste minulý týden neviděli **Preheader:** Objevily se na portálech, které tarif Zdarma nehlídá.

Dobrý den{{, jmeno}},

minulý týden se pro vašeho hlídacího psa **{{pes\_nazev}}** objevilo {{pocet}} nabídek na portálech, které tarif Zdarma nehlídá. Upozornění na ně vám tedy nepřišlo.

Například:

- **{{nabidka\_1\_nazev}}**, {{nabidka\_1\_cena}} Kč, {{nabidka\_1\_portal}}
- **{{nabidka\_2\_nazev}}**, {{nabidka\_2\_cena}} Kč, {{nabidka\_2\_portal}}
- **{{nabidka\_3\_nazev}}**, {{nabidka\_3\_cena}} Kč, {{nabidka\_3\_portal}}

S tarifem Start hlídáme všech 9 portálů včetně ČeskéReality, Annonce, M&M Reality, RE/MAX a Reality.cz. Upozornění pošleme do 60 sekund místo 5 minut.

**\[Tlačítko: Vyzkoušet Start 3 dny zdarma\]**

Po 3 dnech 149 Kč měsíčně. Zrušit můžete kdykoli jedním kliknutím.

Tým Realitify

### 3.2 Limit hlídacích psů

**Trigger:** pokus vytvořit 2. psa na tarifu Zdarma · **Zpoždění:** 1 hodina (pokud mezitím neupgradoval)

**Předmět:** Hledáte ve více lokalitách najednou? **Preheader:** Se Startem máte 10 hlídacích psů na 9 portálech.

Dobrý den{{, jmeno}},

viděli jsme, že jste si chtěli nastavit dalšího hlídacího psa. Tarif Zdarma má jednoho, což často nestačí. Lidé obvykle hledají ve 2–3 čtvrtích nebo zvažují víc dispozic.

Tarif Start vám dá:

- 10 hlídacích psů
- všech 9 portálů místo 4
- upozornění do 60 sekund
- pokročilé filtry: vyloučení realitních kanceláří, klíčová slova

**\[Tlačítko: Vyzkoušet Start 3 dny zdarma\]**

Po zkušební době 149 Kč měsíčně, bez závazku.

Tým Realitify

### 3.3 Rychlost upozornění

**Trigger:** po 10. odeslaném alertu na tarifu Zdarma · **Podmínka:** aspoň 1 z alertů je už neaktivní (inzerát stažen)

**Předmět:** {{pocet\_stazeno}} z vašich {{pocet}} nabídek už zmizelo **Preheader:** U dobrých bytů rozhoduje každá minuta.

Dobrý den{{, jmeno}},

poslali jsme vám už {{pocet}} upozornění na nové nabídky. {{pocet\_stazeno}} z nich už inzerent stáhl, některé do pár hodin od zveřejnění.

Na tarifu Zdarma vám upozornění chodí do 5 minut. Se Startem do 60 sekund, s PRO do 30 sekund. Majiteli tak voláte dřív než ostatní zájemci.

|  | Zdarma | Start | PRO |
| --- | --- | --- | --- |
| Rychlost | do 5 min | do 60 s | do 30 s |
| Portály | 4 | 9 | 9 |
| Hlídací psi | 1 | 10 | neomezeně |
| Kanály | e-mail | e-mail | e-mail, push, Discord, SMS |
| Cena | 0 Kč | 149 Kč/měs. | 349 Kč/měs. |

**\[Tlačítko: Vyzkoušet 3 dny zdarma\]**

Tým Realitify

## 4. Trial a předplatné

E-maily o platbách jsou transakční (noreply@realitify.cz) a chodí vždy. Text „Start“ / „PRO“ a cena se doplňují z `{{tarif}}` a `{{cena}}`.

### 4.1 Trial začal

**Trigger:** aktivace 3denního trialu

**Předmět:** Váš {{tarif}} je aktivní, 3 dny zdarma **Preheader:** Hlídáme všech 9 portálů. Zkušební doba končí {{datum\_konec}}.

Dobrý den{{, jmeno}},

vítejte v tarifu {{tarif}}. Od teď máte:

- všech 9 portálů: Sreality, Bezrealitky, Reality.iDNES, Bazoš, ČeskéReality, Annonce, M&M Reality, RE/MAX a Reality.cz
- upozornění do {{rychlost}}
- {{pocet\_psu}} hlídacích psů
- pokročilé filtry: vyloučení realitních kanceláří, klíčová slova

Vaši stávající hlídací psi už automaticky hlídají všech 9 portálů. Doporučujeme ještě nastavit vyloučení realitních kanceláří, pokud hledáte bez provize.

**\[Tlačítko: Nastavit hlídací psy\]**

Zkušební doba končí {{datum\_konec}}. Pak vám strhneme {{cena}} měsíčně. Den předem vám pošleme připomínku. Zrušit můžete kdykoli v Nastavení → Předplatné.

Tým Realitify

### 4.2 Trial končí zítra

**Trigger:** 24 h před koncem trialu · **Podmínka:** trial nebyl zrušen

**Předmět:** Zítra končí zkušební doba {{tarif}} **Preheader:** {{datum\_konec}} strhneme {{cena}}. Pokud nechcete pokračovat, zrušte to jedním kliknutím.

Dobrý den{{, jmeno}},

zítra ({{datum\_konec}}) končí vaše zkušební doba tarifu {{tarif}}. Pokud nic neuděláte, strhneme z vaší karty {{cena}} a hlídání poběží dál bez přerušení.

Za poslední 2 dny jsme pro vás našli **{{pocet}} nových nabídek**, z toho {{pocet\_navic}} na portálech, které tarif Zdarma nehlídá.

**\[Tlačítko: Pokračovat s {{tarif}}\]**

Nechcete pokračovat? Zrušit předplatné. Vrátíte se na tarif Zdarma a váš první hlídací pes poběží dál.

Tým Realitify

### 4.3 Platba proběhla

**Trigger:** každá úspěšná platba (první i opakovaná)

**Předmět:** Platba za Realitify {{tarif}} – faktura {{cislo\_faktury}} **Preheader:** Děkujeme. Fakturu najdete v příloze.

Dobrý den{{, jmeno}},

děkujeme, platba proběhla v pořádku.

|  |  |
| --- | --- |
| Tarif | {{tarif}} |
| Částka | {{cena}} |
| Období | {{obdobi\_od}} – {{obdobi\_do}} |
| Faktura | {{cislo\_faktury}} (v příloze) |
| Karta | {{karta\_typ}} končící {{karta\_posledni4}} |

Všechny faktury najdete v Nastavení → Předplatné.

**\[Tlačítko: Zobrazit předplatné\]**

Tým Realitify

### 4.4 Platba selhala

**Trigger:** neúspěšná platba · **Opakování:** stejný e-mail +3 dny a +7 dní, pak 4.5 · **Exit:** platba projde

**Předmět:** Platba za Realitify neprošla, hlídání zatím běží **Preheader:** Aktualizujte prosím platební kartu, ať o nic nepřijdete.

Dobrý den{{, jmeno}},

nepodařilo se nám strhnout platbu {{cena}} za tarif {{tarif}}. Nejčastěji jde o kartu s prošlou platností nebo nedostatek prostředků.

Hlídání vám zatím necháváme běžet. Pokud platba neprojde do {{datum\_downgrade}}, přepneme vás na tarif Zdarma a hlídání omezíme na 1 psa a 4 portály.

**\[Tlačítko: Aktualizovat platební kartu\]**

Když si nevíte rady, odpovězte na tento e-mail a pomůžeme.

Tým Realitify

*Varianta pro 3. pokus: předmět „Poslední připomínka: zítra hlídání omezíme“.*

### 4.5 Přepnutí na Zdarma po neúspěšné platbě

**Trigger:** 7 dní po prvním selhání, platba stále neprošla

**Předmět:** Váš účet jsme přepnuli na tarif Zdarma **Preheader:** Hlídáme dál, ale jen 1 psa na 4 portálech.

Dobrý den{{, jmeno}},

platbu za tarif {{tarif}} se nám nepodařilo strhnout, proto jsme váš účet přepnuli na tarif Zdarma.

Co to znamená:

- Dál hlídáme psa **{{pes\_aktivni}}** na 4 portálech, upozornění do 5 minut.
- Ostatní psi ({{pocet\_pozastaveno}}) jsou pozastavení. Nastavení jsme uložili.

Jakmile předplatné obnovíte, všichni psi se hned znovu zapnou.

**\[Tlačítko: Obnovit {{tarif}}\]**

Tým Realitify

### 4.6 Zrušení předplatného

**Trigger:** uživatel zruší předplatné

**Předmět:** Předplatné jsme zrušili, {{tarif}} vám běží do {{datum\_konec}} **Preheader:** Další platby už nestrhneme.

Dobrý den{{, jmeno}},

potvrzujeme zrušení předplatného {{tarif}}. Další platby už nestrhneme. Všechny funkce můžete používat do {{datum\_konec}}, pak se účet přepne na tarif Zdarma.

Pomůže nám, když nám řeknete, proč odcházíte. Stačí jedno kliknutí:

- Našel/a jsem bydlení
- Je to pro mě drahé
- Chodilo mi málo relevantních nabídek
- Jiný důvod

Kdybyste si to rozmysleli, předplatné obnovíte v Nastavení → Předplatné.

Tým Realitify

*Pozn.: Při volbě „Našel/a jsem bydlení“ zobrazit poděkování a prosbu o příběh do /uspechy (viz 6.1).*

### 4.7 Konec předplatného

**Trigger:** den vypršení zrušeného předplatného

**Předmět:** Váš účet je teď na tarifu Zdarma **Preheader:** Hlídáme dál 1 psa. Ostatní nastavení jsme uložili.

Dobrý den{{, jmeno}},

vaše předplatné {{tarif}} dnes skončilo a účet běží na tarifu Zdarma.

Dál hlídáme psa **{{pes\_aktivni}}** na 4 portálech. Ostatní psi jsou pozastavení a jejich nastavení zůstává uložené.

**\[Tlačítko: Zobrazit hlídací psy\]**

Děkujeme, že jste s námi byli.

Tým Realitify

## 5. Alerty

Alerty jsou jádro produktu. Musí být krátké, čitelné na mobilu a hlavní informace (cena, dispozice, lokalita) musí být vidět už v předmětu a preheaderu. Odesílatel alerty@realitify.cz.

### 5.1 Nová nabídka (okamžitý alert)

**Trigger:** nová shoda s filtrem psa · **Pravidlo:** 1 nabídka = 1 e-mail; duplicitní inzerát z jiného portálu neposílat znovu

**Předmět:** {{dispozice}}, {{plocha}} m², {{lokalita\_detail}} – {{cena}} Kč **Preheader:** Nová nabídka pro „{{pes\_nazev}}“ na {{portal}}, zveřejněno před {{minuty}} min.

**\[Fotka nabídky\]**

**{{dispozice}}, {{plocha}} m²** {{adresa}} **{{cena}} Kč{{/měsíc}}** {{poplatky}}

{{portal}} · zveřejněno {{cas\_zverejneni}} {{rk\_nebo\_majitel}}

**\[Tlačítko: Zobrazit inzerát\]**

Zobrazit na mapě v Realitify

---

Hlídací pes: {{pes\_nazev}} · Upravit · Pozastavit

*Pozn.: CTA vede přímo na původní inzerát (rychlost je hodnota produktu). U tarifu Zdarma pod tlačítko přidat jeden řádek: „Se Startem by vám tohle přišlo do 60 sekund. \[Vyzkoušet zdarma\]“. Max 1× denně.*

### 5.2 Souhrn nabídek (volitelný)

**Trigger:** uživatel si v nastavení zvolí hodinový nebo denní souhrn místo okamžitých alertů · **Podmínka:** aspoň 1 nová nabídka v období

**Předmět:** {{pocet}} nových nabídek pro „{{pes\_nazev}}“ **Preheader:** Nejlevnější: {{dispozice\_min}}, {{cena\_min}} Kč, {{lokalita\_min}}.

Dobrý den{{, jmeno}},

od posledního souhrnu přibylo {{pocet}} nových nabídek:

| Nabídka | Cena | Portál |  |
| --- | --- | --- | --- |
| {{dispozice}}, {{plocha}} m², {{lokalita\_detail}} | {{cena}} Kč | {{portal}} | Otevřít |
| … | … | … | … |

**\[Tlačítko: Zobrazit všechny na mapě\]**

Nejlepší nabídky mizí rychle. Pokud chcete reagovat jako první, přepněte si v nastavení zpět na okamžitá upozornění.

Hlídací pes: {{pes\_nazev}} · Upravit · Nastavení upozornění

### 5.3 Pes bez aktivity

**Trigger:** 14 dní od poslední shody · **Podmínka:** pes je aktivní a dříve už nějaké shody měl (jinak platí 2.5) · **Max:** 1× za 30 dní na psa

**Předmět:** Pro „{{pes\_nazev}}“ 14 dní nic nového **Preheader:** Upravíme filtr, nebo hlídání pozastavíme?

Dobrý den{{, jmeno}},

váš hlídací pes **{{pes\_nazev}}** nenašel za posledních 14 dní žádnou novou nabídku. Trh v téhle kategorii je teď slabší, nebo je filtr příliš přísný.

Co s tím:

- Upravit filtr: s cenou +10 % byste viděli {{pocet\_cena}} nabídek
- Pozastavit hlídání: když teď nehledáte
- Nechat běžet: nic nedělejte, ozveme se hned, jak se něco objeví

Tým Realitify

### 5.4 Upozornění nečtete

**Trigger:** 10 alertů po sobě bez otevření nebo bounce · **Podmínka:** při hard bounce neposílat (adresa nefunguje), jen banner v aplikaci

**Předmět:** Dostáváte naše upozornění? **Preheader:** Posledních 10 jste neotevřeli. Možná padají do spamu.

Dobrý den{{, jmeno}},

poslali jsme vám 10 upozornění na nové nabídky, ale žádné jste neotevřeli. Možná končí ve spamu, v Hromadné poště nebo v záložce Promo akce.

Co pomůže:

- Přidejte si alerty@realitify.cz do kontaktů.
- Přesuňte náš e-mail do složky Doručené / Primární.
- S tarifem PRO můžete dostávat upozornění i jako push notifikaci nebo na Discord.

Už nehledáte? Hlídání pozastavte, ať vás nezahlcujeme.

**\[Tlačítko: Upravit upozornění\]**

Tým Realitify

*Pozn.: Pokud neotevře ani tento e-mail, psa po 7 dnech automaticky pozastavit a poslat krátkou informaci. Chrání to doručitelnost celé domény.*

## 6. Retence a ukončení

Hledání bydlení přirozeně končí. Cílem není uživatele za každou cenu udržet, ale ukončit to hezky, získat příběh do /uspechy a mít ho zpátky při dalším stěhování. Odesílatel tym@realitify.cz.

### 6.1 Našli jste bydlení?

**Trigger:** 30 a 60 dní od vytvoření prvního psa · **Podmínka:** nějaký pes je aktivní · **Exit:** uživatel klikne na jakoukoli volbu

**Předmět:** Už máte nové bydlení? **Preheader:** Dejte nám vědět. Jedno kliknutí stačí.

Dobrý den{{, jmeno}},

hlídáme pro vás nabídky už {{pocet\_dni}} dní a poslali jsme vám {{pocet}} upozornění. Jak to vypadá?

**\[Tlačítko: Ano, našel/a jsem bydlení\]**

Ještě hledám · Pozastavit hlídání na měsíc

Tým Realitify

**Navazující e-mail po kliknutí na „Ano“ (posílá se hned)**

**Předmět:** Gratulujeme k novému bydlení **Preheader:** Hlídací psy jsme pozastavili. Prozradíte nám, jak to proběhlo?

Dobrý den{{, jmeno}},

gratulujeme. Vaše hlídací psy jsme pozastavili, ať vám už nechodí upozornění. Nastavení zůstává uložené pro příští stěhování.

Máte předplatné? Zrušit ho můžete tady. Žádné další platby.

A jedna prosba: pomohlo by nám, kdybyste ve 2–3 větách napsali, jak jste byt našli. Nejlepší příběhy zveřejňujeme na webu a autory odměníme {{odmena}}.

**\[Tlačítko: Napsat svůj příběh\]**

Ať se vám v novém bydlení daří, Tým Realitify

*Pozn.: `{{odmena}}` doplnit, např. 3 měsíce PRO zdarma pro příští hledání nebo jen „zveřejníme s vaším svolením“.*

### 6.2 Měsíční přehled trhu

**Trigger:** 1. pracovní den v měsíci · **Podmínka:** souhlas s tipy a novinkami; lokalita = lokalita prvního psa (nebo Praha)

**Předmět:** Nájmy v {{lokalita}} za {{mesic}}: {{zmena\_procenta}} **Preheader:** Průměrný nájem 2+kk je teď {{cena\_2kk}} Kč. Celý přehled uvnitř.

Dobrý den{{, jmeno}},

posíláme měsíční přehled trhu pro {{lokalita}} z dat 9 realitních portálů.

| Dispozice | Průměrný nájem | Změna za měsíc |
| --- | --- | --- |
| 1+kk | {{cena\_1kk}} Kč | {{zmena\_1kk}} |
| 2+kk | {{cena\_2kk}} Kč | {{zmena\_2kk}} |
| 3+kk | {{cena\_3kk}} Kč | {{zmena\_3kk}} |

Za {{mesic}} přibylo {{pocet\_novych}} nových nabídek. Průměrný inzerát zmizel za {{dny\_zivotnost}} dní.

**\[Tlačítko: Otevřít Index nájmů\]**

Tým Realitify

### 6.3 Win-back

**Trigger:** 30 dní po konci placeného předplatného · **Podmínka:** souhlas s tipy a novinkami; důvod zrušení ≠ „Našel/a jsem bydlení“ · **Druhý pokus:** 90 dní, pak už nic

**Předmět:** Hledáte znovu? Vaše hlídací psy máme uložené **Preheader:** Zapnete je jedním kliknutím, všech 9 portálů.

Dobrý den{{, jmeno}},

od vašeho odchodu přibylo v lokalitě {{lokalita}} {{pocet}} nových nabídek, které by odpovídaly vašemu psovi **{{pes\_nazev}}**.

Vaše nastavení máme stále uložené. Pokud zase hledáte, stačí jedno kliknutí a hlídání poběží jako dřív.

**\[Tlačítko: Obnovit hlídání\]**

Tým Realitify

### 6.4 PRO: napojení na AI (MCP)

**Trigger:** aktivace PRO (trial i placené) · **Zpoždění:** +1 den po 4.1 · **Podmínka:** uživatel MCP ještě nenapojil

**Předmět:** Hledejte byty přímo v ChatGPT, Claude nebo Groku **Preheader:** Napojení zabere 2 minuty.

Dobrý den{{, jmeno}},

s tarifem PRO můžete Realitify napojit na svého AI asistenta. Pak se ho můžete zeptat třeba:

- „Najdi mi 2+kk v Praze 3 do 22 000 Kč s balkonem, bez realitky.“
- „Kolik stojí průměrně 3+kk v Brně-Žabovřeskách?“
- „Které z nabídek z tohoto týdne jsou pod průměrnou cenou?“

Asistent hledá v aktuálním katalogu z 9 portálů, ne ve starých datech.

**\[Tlačítko: Návod na napojení\]**

Návod je pro ChatGPT, Claude i Grok. Váš osobní klíč najdete v Nastavení → MCP.

Tým Realitify
