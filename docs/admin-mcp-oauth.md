# Admin MCP: přihlášení přes OAuth (`/mcp-admin`)

Konektor se přidá jen URL `https://realitify.cz/mcp-admin` a přihlášením admin účtem (např. claude.ai → Nastavení → Konektory → Přidat vlastní konektor). Statický klíč (`rf_admin_…`) funguje dál, nezávisle.

## Tok

1. Klient zavolá `/mcp-admin` bez tokenu → `401` + `WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource/mcp-admin"`.
2. Discovery: protected resource metadata → authorization server `https://realitify.cz/admin-oauth` (`/.well-known/oauth-authorization-server/admin-oauth`, také `openid-configuration`).
3. Dynamic client registration: `POST /oauth/admin/register` (redirect jen claude.ai / *.claude.ai / *.anthropic.com / loopback `/callback`).
4. `GET /oauth/admin/authorize` → souhlasová stránka s varováním o plných právech. **Vždy** vyžaduje admin e-mail + heslo (čerstvá autentizace; zároveň ochrana proti CSRF/clickjackingu, stránka má `X-Frame-Options: DENY`).
5. `POST /oauth/admin/token`: code + PKCE S256 → `rfa_at_…` (1 h) a `rfa_rt_…` (30 dní, rotuje při každém použití).

## Bezpečnostní rozhodnutí

- **Oddělené od zákaznického OAuth** (`app/mcp_oauth.py`): vlastní issuer, endpointy `/oauth/admin/*`, registr klientů i tokenů (`admin_oauth_*` v globální meta). Token zákazníka neotevře `/mcp-admin` a admin token neotevře `/mcp`.
- Hesla ověřuje `admin_oauth.authenticate_admin`: `ADMIN_EMAIL`/`ADMIN_PASSWORD` z env, nebo jiný účet s rolí admin a vlastním heslem. Jedna chybová hláška pro všechny selhání. Rate limit 10/10 min na IP a 6/10 min na e-mail.
- Vše hashované (SHA-256), code jednorázový (5 min) a vázaný na klienta, redirect a PKCE challenge.
- Refresh rotace s detekcí znovupoužití: předložení už rotovaného refresh tokenu zruší celý grant.
- Každý požadavek znovu kontroluje, že vlastník je pořád admin (degradace = okamžitá ztráta přístupu).
- AI nemůže spravovat přístupy: `call_api` blokuje `/api/admin/mcp-oauth`, `/api/admin/mcp-key`, `/oauth/admin`, `/admin-oauth`, `/mcp-admin`.
- Zápisy přes OAuth klienta jdou do auditu s názvem klienta (`[Claude] AI (MCP): POST …`), povolení přístupu také.
- Max. 10 aktivních grantů na admina (nejstarší se zahodí).

## Správa

Administrace → Nastavení → „Konektor přes URL“: URL, seznam propojených klientů, tlačítko Odpojit (`GET /api/admin/mcp-oauth`, `DELETE /api/admin/mcp-oauth/{id}`). `POST /oauth/admin/revoke` odvolá konkrétní token.

Testy: `tests/test_admin_oauth.py`.
