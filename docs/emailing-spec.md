# Automatické e-maily Realitify: společný kontrakt

Zdroj textů: `Marketing emails/Realitify – automatické e-maily (wording).md` (dále "wording doc"). Rozhodnutí zadavatele:

- Dodávka celá najednou (31 šablon, admin CMS, log, hromadné odesílání, triggery v appce).
- Odesílání zůstává přes SMTP (`app/email_notify.py`, `config.SMTP_*`), 3 odesílatelé podle streamu. Žádný ESP. Otevření měříme tracking pixelem, kliky přes redirect.
- Souhlas: Obchodní podmínky (`web/site/obchodni-podminky.html`, případně `terms.html`) musí uvádět, že registrací uživatel souhlasí se zasíláním marketingových e-mailů. Checkbox "Souhlasím s obchodními podmínkami" v registraci je POVINNÝ (HTML `required` + kontrola na serveru). Odhlášení z tipů a novinek (lifecycle) přesto existuje: odkaz v patičce, hlavička List-Unsubscribe, příznak `marketing_unsub` u uživatele.
- Hromadné odesílání v adminu: segmentace, náhled, test, naplánování, textový nebo HTML režim.

## Streamy a odesílatelé

| stream | klíč | From (výchozí) | Reply-To | patička |
| --- | --- | --- | --- | --- |
| Transakční | `transactional` | `Realitify <noreply@realitify.cz>` | podpora@realitify.cz | odkaz na nastavení notifikací |
| Alerty | `alerts` | `Realitify <alerty@realitify.cz>` | podpora@realitify.cz | odkaz na nastavení notifikací |
| Lifecycle | `lifecycle` | `Realitify <tym@realitify.cz>` | podpora@realitify.cz | "Tento e-mail dostáváte, protože máte účet na Realitify.cz. Nastavení e-mailů · Odhlásit z tipů a novinek" |

Adresy se berou z env (`EMAIL_FROM_TRANSACTIONAL`, `EMAIL_FROM_ALERTS`, `EMAIL_FROM_LIFECYCLE`, `EMAIL_REPLY_TO`), fallback na `SMTP_FROM`. SMTP login zůstává jeden, From se jen mění.
Kampaně z adminu jdou jako stream `lifecycle` (patička s odhlášením).

## Šablony (klíče, soubory)

HTML soubor každé šablony: `Marketing emails/<číslo>_<klíč>.html`, např. `Marketing emails/1.1_verify_email.html`.

| č. | klíč | stream | název |
| --- | --- | --- | --- |
| 1.1 | verify_email | transactional | Ověření e-mailu |
| 1.2 | password_reset | transactional | Reset hesla |
| 1.3a | email_changed_old | transactional | Změna e-mailu: upozornění na starou adresu |
| 1.3b | email_changed_confirm | transactional | Změna e-mailu: ověření nové adresy |
| 1.4 | password_changed | transactional | Změna hesla |
| 1.5 | account_deleted | transactional | Smazání účtu |
| 2.1 | welcome | lifecycle | Vítejte |
| 2.2 | nudge_no_dog | lifecycle | Nudge: zatím nemáte hlídacího psa |
| 2.3 | first_dog_confirm | lifecycle | Potvrzení hlídacího psa |
| 2.4 | nudge_last_rent_index | lifecycle | Poslední nudge (Index nájmů) |
| 2.5 | dog_no_matches | lifecycle | Pes bez shod |
| 3.1 | upsell_missed_offers | lifecycle | Uteklo vám X nabídek |
| 3.2 | upsell_dog_limit | lifecycle | Limit hlídacích psů |
| 3.3 | upsell_speed | lifecycle | Rychlost upozornění |
| 4.1 | trial_started | transactional | Trial začal |
| 4.2 | trial_ending | transactional | Trial končí zítra |
| 4.3 | payment_ok | transactional | Platba proběhla |
| 4.4 | payment_failed | transactional | Platba selhala |
| 4.4b | payment_failed_last | transactional | Platba selhala: poslední připomínka (3. pokus) |
| 4.5 | downgraded_after_failure | transactional | Přepnutí na Zdarma po neúspěšné platbě |
| 4.6 | subscription_canceled | transactional | Zrušení předplatného |
| 4.7 | subscription_ended | transactional | Konec předplatného |
| 5.1 | alert_new_listing | alerts | Nová nabídka |
| 5.2 | alert_digest | alerts | Souhrn nabídek |
| 5.3 | dog_inactive | alerts | Pes bez aktivity |
| 5.4 | alerts_unopened | alerts | Upozornění nečtete |
| 6.1 | found_home | lifecycle | Našli jste bydlení? |
| 6.1b | found_home_followup | lifecycle | Gratulujeme (po kliknutí na "Ano") |
| 6.2 | market_monthly | lifecycle | Měsíční přehled trhu |
| 6.3 | winback | lifecycle | Win-back |
| 6.4 | pro_mcp | lifecycle | PRO: napojení na AI (MCP) |

Pozn.: 4.1 až 4.7 jsou podle wording doc transakční (`noreply@`), chodí vždy. 4.4b je varianta 4.4 s předmětem "Poslední připomínka: zítra hlídání omezíme".

## Proměnné

Syntax: `{{nazev}}` = HTML-escapovaná hodnota, `{{{nazev}}}` = raw HTML (jen pro připravené bloky, např. řádky tabulky). Neznámá nebo prázdná proměnná se vykreslí jako prázdný řetězec. Žádné podmínky ve šabloně, volitelné části řeší připravená proměnná (např. `{{osloveni}}`).

Společné proměnné (dostupné vždy, doplňuje renderer):

| proměnná | význam |
| --- | --- |
| `{{osloveni}}` | "Dobrý den, Jiří," nebo "Dobrý den," když není jméno |
| `{{jmeno}}` | křestní jméno (může být prázdné) |
| `{{email}}` | e-mail příjemce |
| `{{base_url}}` | veřejná URL appky (`config.PUBLIC_BASE_URL`), pro obrázky a odkazy |
| `{{nastaveni_url}}` | odkaz na nastavení e-mailů/notifikací |
| `{{odhlasit_url}}` | podepsaný odkaz pro odhlášení z tipů a novinek |
| `{{podpora_email}}` | podpora@realitify.cz |
| `{{rok}}` | aktuální rok |
| `{{preheader}}` | preheader šablony (renderer ho vloží do skrytého spanu na začátku body) |

Šablonové proměnné: ty z wording doc (např. `{{pes_nazev}}`, `{{pes_filtr}}`, `{{lokalita}}`, `{{tarif}}`, `{{cena}}`, `{{datum_konec}}`, `{{pocet}}`, `{{odkaz}}`, ...). Pozor, wording doc je v markdown exportu s escapovanými podtržítky (`\_`), skutečné názvy jsou bez zpětného lomítka. Tlačítka mají cíl `{{odkaz}}` (hlavní CTA), případně jiný explicitně pojmenovaný odkaz (`{{odkaz_zrusit}}`, `{{odkaz_pozastavit}}` apod.). Seznamy a tabulky (např. nabídky v 3.1, souhrn v 5.2, tabulka cen) se předávají jako raw HTML řádky proměnné s příponou `_html` (např. `{{{nabidky_html}}}`, `{{{radky_html}}}`), v `catalog.py` s ukázkovou hodnotou.

## Python API (`app/emailing/`)

- `catalog.py` (píše designer): 
  - `@dataclass(frozen=True) class Var: name: str; description: str; sample: str; raw: bool = False`
  - `@dataclass(frozen=True) class TemplateDef: key; number; group (1-6, int); group_name; name; stream; trigger; condition; subject; preheader; html_file; variables: tuple[Var, ...]`
  - `COMMON_VARS: tuple[Var, ...]`, `GROUPS: dict[int, str]`, `TEMPLATES: tuple[TemplateDef, ...]`, `get_template(key) -> TemplateDef | None`, `sample_vars(key) -> dict[str, str]` (společné + šablonové, hodnoty jako string, `_html` hodnoty raw), `read_default_html(key) -> str` (čte soubor z `Marketing emails/`).
  - Předmět a preheader jsou doslova z wording doc (včetně proměnných).
- `render.py`: `render(template_html, subject, preheader, variables) -> Rendered(subject, preheader, html, text)`; text se odvodí z HTML (tlačítka jako "Text: URL").
- `store.py` + `service.py` + `triggers.py` + `admin_api.py` + `tracking.py` (píše backend), tabulky v globální DB (`config.DB_PATH`, přes `users.ensure_users_schema` nebo vlastní `ensure_schema`).

### Tabulky

- `email_templates(key PK, subject, preheader, html, text_body, enabled INTEGER DEFAULT 1, updated_at, updated_by)`: přepisy; chybějící řádek = výchozí z `catalog` + souborů.
- `email_log(id INTEGER PK, created_at, scheduled_at, sent_at, template_key, campaign_id, user_id, to_email, stream, subject, html, text_body, status, error, attempts, dedupe_key UNIQUE NULL, opened_at, open_count, clicked_at, click_count)`; `status` ∈ `queued | sending | sent | failed | skipped | canceled`. `skipped` nese důvod v `error` (např. "marketing_unsub", "disabled", "no_consent", "smtp_not_configured").
- `email_campaigns(id, name, mode ('text'|'html'), subject, preheader, body, segment_json, status ('draft'|'scheduled'|'sending'|'sent'|'canceled'), scheduled_at, created_at, started_at, finished_at, total, created_by)`.
- `users.marketing_unsub INTEGER DEFAULT 0`.

### Service

- `send_template(key, *, user=None, to=None, variables=None, dedupe_key=None, delay_sec=0, force=False) -> int | None` zařadí do `email_log` (`queued`), respektuje `enabled`, `marketing_unsub` (jen lifecycle), dedupe. Vrací id logu. Transakční a alerty se odesílají okamžitě (`process_now(log_id)`), lifecycle přes frontu.
- `process_queue(limit)` odešle `queued` s `scheduled_at <= now` (retry: 3 pokusy, backoff 1/5/30 min).
- Loop `run_email_loop()` (fronta každých 15 s, scheduler triggerů každých 60 s) běží v `app/scrape_worker.py` (a v režimu `SCRAPE_ROLE=all`). Zámek proti dvojímu odeslání: atomický přechod `queued -> sending`.
- Odesílání: HTML + text alternativa, `List-Unsubscribe` + `List-Unsubscribe-Post` pro lifecycle, tracking pixel (`/e/o/<token>.gif`) a přepsání odkazů (`/e/c/<token>?u=<url>`, jen http/https na vlastní doménu nebo whitelist, jinak open-redirect!). Odhlášení: `/odhlasit?token=...` (stránka + POST), token podepsaný HMAC.
- Stávající `email_notify.send_password_reset` / `send_email_verification` se nahradí voláním šablon (`password_reset`, `verify_email`); `notify_listing`/`notify_digest` posílají přes `alert_new_listing`/`alert_digest`.

## Admin API (`/api/admin/emails/...`, cookie `realitify_admin`, vše JSON)

| metoda | cesta | popis |
| --- | --- | --- |
| GET | `/templates` | `{groups:{1:"Transakční",...}, templates:[{key,number,group,group_name,name,stream,trigger,condition,enabled,customized,subject,preheader,from,stats:{sent,failed,skipped,opened,clicked,last_sent_at}}]}` |
| GET | `/templates/{key}` | vše výše + `html, text_body, default_html, default_subject, default_preheader, variables:[{name,description,sample,raw}], common_variables:[...]` |
| POST | `/templates/{key}` | body `{subject, preheader, html, text_body, enabled}` uloží přepis |
| POST | `/templates/{key}/reset` | smaže přepis |
| POST | `/templates/{key}/preview` | body `{subject?, preheader?, html?, variables?}` → `{subject, preheader, html, text}` (ukázková data) |
| POST | `/templates/{key}/test` | body `{to, subject?, preheader?, html?}` odešle test (log s `campaign_id` NULL, `template_key` + příznak test v `dedupe_key` nenastavuj) |
| GET | `/log` | query `template, status, stream, campaign, q, page, page_size` → `{items:[{id,created_at,sent_at,template_key,template_name,campaign_id,to_email,user_id,stream,subject,status,error,attempts,opened_at,clicked_at}], total, counts:{queued,sent,failed,skipped}}` |
| GET | `/log/{id}` | položka + `html`, `text_body` |
| POST | `/log/{id}/retry` | znovu zařadí `failed`/`skipped` |
| GET | `/campaigns` | seznam + statistiky (`total,sent,failed,opened,clicked`) |
| POST | `/campaigns` | vytvoří/uloží draft `{id?, name, mode, subject, preheader, body, segment, scheduled_at}` |
| GET | `/campaigns/{id}` | detail + statistiky |
| POST | `/campaigns/audience` | body `{segment}` → `{count, sample:[{email,name,plan}]}` |
| POST | `/campaigns/{id}/test` | body `{to}` |
| POST | `/campaigns/{id}/send` | spustí hned (nebo podle `scheduled_at`) |
| POST | `/campaigns/{id}/cancel` | zruší nenaplánované/nezaslané |
| GET | `/status` | `{smtp_configured, senders:{transactional,alerts,lifecycle}, queue:{queued,failed_24h}}` |

`segment`: `{plans:[free|start|pro], verified: "any"|"yes"|"no", registered_from, registered_to (YYYY-MM-DD), has_dog: "any"|"yes"|"no", locality: str, active_days: int|null, include_unsubscribed: false}`. Kampaň vždy vynechává `marketing_unsub` (pokud tedy není explicitně transakční) a neověřené e-maily jen když `verified:"any"`.

Veřejné (bez přihlášení): `GET /e/o/{token}.gif`, `GET /e/c/{token}?u=`, `GET/POST /odhlasit`.

## Admin UI

Nová položka menu "E-maily" (`/admin/emaily`), soubor `web/admin/emails.js` jako `window.AdEmails(ctx)` (stejný vzor jako `web/admin/cms.js`), styl v `web/admin/admin.css` (prefix `em-`). Záložky: Šablony (tabulka seskupená po skupinách 1-6 + editor s HTML, předmět, preheader, živý náhled v iframe, tlačítka Uložit / Obnovit výchozí / Poslat test, vypínač enabled, seznam proměnných s kopírováním), Log odesílání (filtry, stav, detail s náhledem odeslaného těla, retry), Kampaně (editor text/HTML, segment, počet příjemců, test, odeslat/naplánovat, statistiky).

## Design e-mailů

Vychází z design systému appky (`web/site/site.css` tokens, `wise-marketing-DESIGN.md`): canvas `#ffffff`, pale `#f7f9f6`, surface `#e8ebe6`, mint `#e2f6d5`, forest `#163300` (text, tmavé bloky), ink `#0e0f0c`, body `#555b54`, line `#d0d4cd`, green `#9fe870` (jen pill tlačítka a zvýraznění), sold/negativní `#cf2929`. Display nadpisy Manrope 800 (fallback Inter, Arial), text Inter. Tlačítka: pill (border-radius 999px), green pozadí, forest text, výška 48 px, 600. E-mailově bezpečné HTML (tabulky, inline styly, 600 px, bgcolor atributy, Outlook-safe tlačítka přes VML nebo padding na `<a>`), dark-mode safe, preheader skrytý span, mobil 100 %.
