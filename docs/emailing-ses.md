# Odesílání e-mailů přes Amazon SES

Appka posílá přes SMTP rozhraní SES (region `eu-central-1`). Žádná další knihovna, mění se jen env.

## 1. SMTP přihlášení

1. SES console → **SMTP settings** → **Create SMTP credentials** (vytvoří IAM uživatele s právem `ses:SendRawEmail`).
2. Zkopíruj **SMTP username** a **SMTP password**. Jsou jiné než IAM access key a secret key, heslo se zobrazí jen jednou.
3. V Coolify (web i worker):
   ```
   SMTP_HOST=email-smtp.eu-central-1.amazonaws.com
   SMTP_PORT=587
   SMTP_STARTTLS=1
   SMTP_USER=<SMTP username>
   SMTP_PASSWORD=<SMTP password>
   SMTP_FROM=Realitify <noreply@realitify.cz>
   ```
   `From` smí být libovolná adresa na ověřené doméně (`noreply@`, `alerty@`, `ahoj@`), SMTP_USER s ní nesouvisí.
   Port 465 (SSL) také funguje: `SMTP_PORT=465`, `SMTP_STARTTLS=0`.

## 2. Sandbox vs production

Nový SES účet je v sandboxu: posílá jen na ověřené adresy, max 200 e-mailů za 24 h a 1 za sekundu.
**Account dashboard → Request production access** (use case: transakční a notifikační e-maily, odhlášení je v patičce, bounce/complaint zpracováváme přes SNS). Bez toho dostanou běžní uživatelé chybu `554 Message rejected: Email address is not verified`.

Limity odesílání nastav podle kvóty: `EMAIL_MAX_PER_SEC` (výchozí 10) a `EMAIL_MAX_PER_HOUR` (0 = bez omezení). Po překročení se zprávy odloží, nejsou označené jako chyba.

## 3. Configuration set a bounce/complaint (SNS)

1. SES → **Configuration sets** → vytvoř např. `realitify`. Nastav `SES_CONFIGURATION_SET=realitify`. Appka pak přidá hlavičky `X-SES-CONFIGURATION-SET` a `X-SES-MESSAGE-TAGS` (`template_key`, `stream`, `log_id`).
2. SNS → **Topics** → vytvoř standardní topic `realitify-ses-events` (region eu-central-1).
3. Configuration set → **Event destinations** → Add destination → typ **Amazon SNS**, události **Hard bounces** a **Complaints** (volitelně Deliveries), topic z bodu 2.
   (Alternativa bez configuration setu: SES → Verified identities → doména → **Notifications** → Bounce a Complaint feedback → stejný topic.)
4. V `.env` zvol ochranu webhooku, aspoň jedno z:
   - `SES_WEBHOOK_TOKEN=<náhodný řetězec>`
   - `SES_SNS_TOPIC_ARN=arn:aws:sns:eu-central-1:<účet>:realitify-ses-events`
   Samotný podpis AWS nestačí, protože AWS podepíše i cizí topic.
5. SNS topic → **Create subscription**: protokol **HTTPS**, endpoint
   `https://realitify.cz/api/ses/webhook?token=<SES_WEBHOOK_TOKEN>`.
   Appka potvrdí odběr sama (ověří podpis, `SubscribeURL` musí být https na `*.amazonaws.com`). Stav musí přejít na **Confirmed**.

Co se děje: trvalý bounce a stížnost uloží adresu do tabulky `email_suppressions` a nastaví `users.email_suppressed`. Na takové adresy se už neposílá (log: `skipped`, chyba `suppressed`). Počet vidí admin API `/api/admin/emails/status` (`suppressed`, `provider`).

## 4. Chování při chybě

- Chyba přihlášení (535) pozastaví odesílání na 5 minut. Zprávy zůstanou ve frontě. Alerty starší než 6 hodin se označí `skipped` (`stale`), aby se po opravě neodeslala záplava neaktuálních nabídek.
- Dočasné chyby se opakují 3× (1, 5, 30 minut), pak `failed`.

## Transakční configuration set a odhlášení z alertů

- `SES_CONFIGURATION_SET_TRANSACTIONAL` (např. `realitify-transactional`): stream `transactional` (1.x, 4.x) posílá hlavičku s tímto setem, `alerts` a `lifecycle` zůstávají na `SES_CONFIGURATION_SET`. Prázdné = vše na `SES_CONFIGURATION_SET`. Nový set vytvoř stejně jako `realitify-alerts`: event destination Bounce + Complaint → stejný SNS topic, VDM engagement tracking vypnutý (Override account level settings → Disabled).
- Alerty (5.x) mají `List-Unsubscribe` + `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058). Odkaz `/odhlasit?kind=alerts&token=...` vypne u uživatele e-mailová upozornění (`notify_prefs.email=false`), psi zůstanou. Zapnout zpět jde na stránce odhlášení nebo v nastavení notifikací. Patička alertů má viditelný odkaz "Odhlásit upozornění" (`{{odhlasit_alerty_url}}`).
- Lifecycle e-maily dál používají `marketing_unsub` (`/odhlasit?token=...`).
- SNS Delivery události webhook přijme a jen potvrdí (nic neukládá).
- Pravidlo 6 h pro zastaralé alerty (`skipped`/`stale`) platí dál.
