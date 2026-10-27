"""OAuth 2.1 (authorization code + PKCE, dynamic client registration) for the admin MCP at /mcp-admin.

Lets a client such as claude.ai add https://realitify.cz/mcp-admin as a connector by URL and sign in, instead of
pasting a static key. Deliberately separate from the customer-facing OAuth in app/mcp_oauth.py:

* its own issuer (<base>/admin-oauth), endpoints (/oauth/admin/*), client registry and token store, so a token
  of a normal user can never open /mcp-admin and vice versa;
* granting access needs the admin's e-mail + password on the consent page every time (fresh authentication for
  full admin power; it also makes the consent form immune to CSRF and clickjacking);
* short-lived access tokens, rotating refresh tokens with reuse detection, grants listed and revocable by the admin;
* everything is hashed at rest, only the admin id is trusted: every request re-checks that the owner is still an admin.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from html import escape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app import config
from app import mcp_oauth
from app import users as user_registry
from app.store import Store

CLIENTS_META = "admin_oauth_clients"
CODES_META = "admin_oauth_codes"
TOKENS_META = "admin_oauth_tokens"
CODE_TTL_SEC = 5 * 60
ACCESS_TTL_SEC = 60 * 60
REFRESH_TTL_SEC = 30 * 24 * 3600
ACCESS_PREFIX = "rfa_at_"
REFRESH_PREFIX = "rfa_rt_"
MAX_GRANTS_PER_ADMIN = 10
MAX_CLIENTS = 80
SCOPE = "admin"
LAST_USED_WRITE_EVERY_SEC = 300

_lock = threading.Lock()  # read-modify-write of the meta lists (several request threads)


def base_url() -> str:
    return config.PUBLIC_BASE_URL.rstrip("/")


def issuer() -> str:
    return f"{base_url()}/admin-oauth"


def resource_url() -> str:
    return f"{base_url()}/mcp-admin"


def metadata_url() -> str:
    return f"{base_url()}/.well-known/oauth-protected-resource/mcp-admin"


def www_authenticate() -> str:
    return f'Bearer realm="realitify-admin", resource_metadata="{metadata_url()}"'


def protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": resource_url(),
        "authorization_servers": [issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SCOPE],
        "resource_name": "Realitify Admin MCP",
    }


def authorization_server_metadata() -> dict[str, Any]:
    base = base_url()
    return {
        "issuer": issuer(),
        "authorization_endpoint": f"{base}/oauth/admin/authorize",
        "token_endpoint": f"{base}/oauth/admin/token",
        "registration_endpoint": f"{base}/oauth/admin/register",
        "revocation_endpoint": f"{base}/oauth/admin/revoke",
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


# ------------------------------------------------------------------ storage --


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read(store: Store, key: str) -> list[dict[str, Any]]:
    return mcp_oauth._read(store, key)


def _write(store: Store, key: str, rows: list[dict[str, Any]], keep: int = 200) -> None:
    import json

    store.set_meta(key, json.dumps(rows[-keep:], ensure_ascii=False))


# ------------------------------------------------------------------ clients --


def register_client(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    redirects = [str(item).strip() for item in (payload.get("redirect_uris") or []) if str(item).strip()]
    if not redirects:
        raise ValueError("Chybí redirect_uris")
    if len(redirects) > 5 or any(not mcp_oauth.redirect_allowed(uri) for uri in redirects):
        raise ValueError("Nepovolený redirect_uri")
    client_id = secrets.token_urlsafe(24)
    row = {
        "client_id": client_id,
        "redirect_uris": redirects,
        "client_name": str(payload.get("client_name") or "AI klient")[:120],
        "created_at": int(time.time()),
    }
    with _lock:
        rows = _read(store, CLIENTS_META)
        rows.append(row)
        _write(store, CLIENTS_META, rows, MAX_CLIENTS)
    return {
        "client_id": client_id,
        "client_id_issued_at": row["created_at"],
        "redirect_uris": redirects,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_name": row["client_name"],
        "scope": SCOPE,
    }


def _client(store: Store, client_id: str) -> dict[str, Any] | None:
    for row in _read(store, CLIENTS_META):
        if row.get("client_id") == client_id:
            return row
    return None


# ---------------------------------------------------------------- authorize --


def _redirect_matches(registered: str, actual: str) -> bool:
    if registered == actual:
        return True
    left, right = urlparse(registered), urlparse(actual)
    loopback = {"localhost", "127.0.0.1"}
    # Native clients (Claude Code) get a random loopback port per login: host + path must match, port may differ.
    return (
        left.scheme == right.scheme == "http"
        and (left.hostname or "").lower() in loopback
        and (right.hostname or "").lower() in loopback
        and (left.path or "/") == (right.path or "/")
    )


def validate_authorize(store: Store, params: dict[str, str]) -> dict[str, str]:
    client_id = (params.get("client_id") or "").strip()
    redirect_uri = (params.get("redirect_uri") or "").strip()
    challenge = (params.get("code_challenge") or "").strip()
    if (params.get("response_type") or "").strip() != "code":
        raise ValueError("Nepodporovaný response_type")
    if (params.get("code_challenge_method") or "S256").strip() != "S256" or not challenge:
        raise ValueError("Vyžaduji PKCE S256")
    client = _client(store, client_id) if client_id else None
    if not client:
        raise ValueError("Neznámý klient (nejdřív proběhne registrace)")
    if not mcp_oauth.redirect_allowed(redirect_uri) or not any(
        _redirect_matches(str(item), redirect_uri) for item in (client.get("redirect_uris") or [])
    ):
        raise ValueError("redirect_uri neodpovídá zaregistrovanému klientovi")
    resource = (params.get("resource") or "").strip()
    if resource and mcp_oauth.canonical_url(resource) != mcp_oauth.canonical_url(resource_url()):
        raise ValueError("resource musí být MCP URL administrace")
    return {
        "client_id": client_id,
        "client_name": str(client.get("client_name") or "AI klient"),
        "redirect_uri": redirect_uri,
        "state": params.get("state") or "",
        "code_challenge": challenge,
    }


def authenticate_admin(store: Store, email: str, password: str) -> dict[str, Any]:
    """Verify admin credentials without leaving a session behind. Raises ValueError (same message) on any failure."""
    from app import admin as admin_panel

    failure = ValueError("E-mail nebo heslo nesedí")
    try:
        # The ADMIN_EMAIL/ADMIN_PASSWORD account of the admin panel login.
        token = admin_panel.login_admin(store, email, password)
    except ValueError:
        token = ""
    if token:
        try:
            admin = admin_panel.admin_from_cookie(store, token)
        finally:
            user_registry.delete_session(store, token)
        if admin:
            return admin
        raise failure
    # Any other account with role admin and its own password.
    user = user_registry.verify_user_password(store, email, password)
    if not user or (user.get("role") or "") != "admin":
        raise failure
    return {"id": user.get("id"), "email": user.get("email"), "name": user.get("first") or "Administrátor"}


def complete_authorize(store: Store, params: dict[str, str], email: str, password: str) -> tuple[str, dict[str, Any]]:
    """Check the consent form; returns (redirect target with ?code=, admin). ValueError = show the form again."""
    fields = validate_authorize(store, params)
    admin = authenticate_admin(store, email, password)
    code = secrets.token_urlsafe(32)
    now = int(time.time())
    with _lock:
        rows = [row for row in _read(store, CODES_META) if int(row.get("exp") or 0) > now]
        rows.append(
            {
                "code": _hash(code),
                "exp": now + CODE_TTL_SEC,
                "client_id": fields["client_id"],
                "redirect_uri": fields["redirect_uri"],
                "code_challenge": fields["code_challenge"],
                "admin_id": str(admin.get("id")),
            }
        )
        _write(store, CODES_META, rows)
    parsed = urlparse(fields["redirect_uri"])
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["code"] = code
    if fields["state"]:
        query["state"] = fields["state"]
    target = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))
    return target, {**admin, "_client_name": fields["client_name"]}


# ------------------------------------------------------------------- tokens --


def _payload(access: str, refresh: str) -> dict[str, Any]:
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SEC,
        "refresh_token": refresh,
        "scope": SCOPE,
    }


def _new_tokens() -> tuple[str, str]:
    return ACCESS_PREFIX + secrets.token_urlsafe(32), REFRESH_PREFIX + secrets.token_urlsafe(32)


def exchange_code(store: Store, *, code: str, redirect_uri: str, client_id: str, code_verifier: str) -> dict[str, Any]:
    digest = _hash(code)
    now = int(time.time())
    with _lock:
        rows = _read(store, CODES_META)
        found = next((row for row in rows if row.get("code") == digest and int(row.get("exp") or 0) > now), None)
        # single use: gone whether or not the rest of the check passes
        _write(store, CODES_META, [row for row in rows if row is not found and int(row.get("exp") or 0) > now])
    if not found or found.get("client_id") != client_id or found.get("redirect_uri") != redirect_uri:
        raise ValueError("invalid_grant")
    if not code_verifier or mcp_oauth.pkce_s256(code_verifier) != str(found.get("code_challenge") or ""):
        raise ValueError("invalid_grant")
    admin = user_registry.get_user_by_id(store, str(found.get("admin_id")))
    if not admin or (admin.get("role") or "") != "admin":
        raise ValueError("invalid_grant")
    client = _client(store, client_id) or {}
    access, refresh = _new_tokens()
    grant = {
        "id": secrets.token_hex(8),
        "admin_id": str(found.get("admin_id")),
        "client_id": client_id,
        "client_name": str(client.get("client_name") or "AI klient"),
        "access_hash": _hash(access),
        "access_exp": now + ACCESS_TTL_SEC,
        "refresh_hash": _hash(refresh),
        "refresh_exp": now + REFRESH_TTL_SEC,
        "prev_refresh_hash": "",
        "created_at": now,
        "last_used_at": 0,
    }
    with _lock:
        rows = _read(store, TOKENS_META)
        mine = [row for row in rows if row.get("admin_id") == grant["admin_id"]]
        drop = {row["id"] for row in mine[: max(0, len(mine) - MAX_GRANTS_PER_ADMIN + 1)]}
        rows = [row for row in rows if row.get("id") not in drop]
        rows.append(grant)
        _write(store, TOKENS_META, rows)
    return _payload(access, refresh)


def refresh_tokens(store: Store, refresh_token: str, client_id: str = "") -> dict[str, Any]:
    """Rotate: the presented refresh token is replaced. Presenting an already-rotated one is a theft signal -> grant revoked."""
    digest = _hash(refresh_token)
    now = int(time.time())
    with _lock:
        rows = _read(store, TOKENS_META)
        reused = next((row for row in rows if digest and row.get("prev_refresh_hash") == digest), None)
        if reused is not None:
            _write(store, TOKENS_META, [row for row in rows if row is not reused])
            raise ValueError("invalid_grant")
        found = next((row for row in rows if row.get("refresh_hash") == digest and int(row.get("refresh_exp") or 0) > now), None)
        if found is None or (client_id and found.get("client_id") != client_id):
            raise ValueError("invalid_grant")
        admin = user_registry.get_user_by_id(store, str(found.get("admin_id")))
        if not admin or (admin.get("role") or "") != "admin":
            _write(store, TOKENS_META, [row for row in rows if row is not found])
            raise ValueError("invalid_grant")
        access, refresh = _new_tokens()
        found.update(
            access_hash=_hash(access),
            access_exp=now + ACCESS_TTL_SEC,
            prev_refresh_hash=found.get("refresh_hash") or "",
            refresh_hash=_hash(refresh),
            refresh_exp=now + REFRESH_TTL_SEC,
        )
        _write(store, TOKENS_META, rows)
    return _payload(access, refresh)


def authenticate_token(store: Store, token: str) -> dict[str, Any] | None:
    """Grant row for a valid access token, else None. The caller still checks that the owner is an admin."""
    if not token.startswith(ACCESS_PREFIX):
        return None
    digest = _hash(token)
    now = int(time.time())
    rows = _read(store, TOKENS_META)
    for row in rows:
        if row.get("access_hash") == digest and int(row.get("access_exp") or 0) > now:
            if now - int(row.get("last_used_at") or 0) >= LAST_USED_WRITE_EVERY_SEC:
                with _lock:
                    fresh = _read(store, TOKENS_META)
                    for item in fresh:
                        if item.get("id") == row.get("id"):
                            item["last_used_at"] = now
                    _write(store, TOKENS_META, fresh)
            return row
    return None


def revoke(store: Store, token: str) -> None:
    digest = _hash(token or "")
    with _lock:
        rows = _read(store, TOKENS_META)
        _write(store, TOKENS_META, [r for r in rows if r.get("access_hash") != digest and r.get("refresh_hash") != digest])


def list_grants(store: Store, admin_id: str) -> list[dict[str, Any]]:
    return [
        {
            "id": row.get("id"),
            "client_name": row.get("client_name") or "AI klient",
            "created_at": int(row.get("created_at") or 0),
            "last_used_at": int(row.get("last_used_at") or 0),
            "refresh_expires_at": int(row.get("refresh_exp") or 0),
        }
        for row in _read(store, TOKENS_META)
        if row.get("admin_id") == str(admin_id)
    ]


def revoke_grant(store: Store, admin_id: str, grant_id: str) -> None:
    with _lock:
        rows = _read(store, TOKENS_META)
        kept = [r for r in rows if not (r.get("id") == grant_id and r.get("admin_id") == str(admin_id))]
        if len(kept) == len(rows):
            raise KeyError("Přístup neexistuje")
        _write(store, TOKENS_META, kept)


# --------------------------------------------------------------------- page --


def consent_html(
    params: dict[str, str], *, client_name: str = "", error: str = "", email: str = "", show_form: bool = True
) -> str:
    names = ("client_id", "redirect_uri", "response_type", "state", "code_challenge", "code_challenge_method", "scope", "resource")
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    if show_form:
        fields = "".join(
            f'<input type="hidden" name="{name}" value="{escape(params.get(name) or "")}" />' for name in names if params.get(name)
        )
        who = escape(client_name or "AI klient")
        host = escape(urlparse(params.get("redirect_uri") or "").hostname or "")
        body = f"""<h1>Povolit přístup do administrace?</h1>
<p><strong>{who}</strong> ({host}) žádá o přístup k administraci Realitify.</p>
<p class="warn"><strong>Plná admin práva:</strong> čtení všech dat a volání libovolného API včetně vytváření, přepisování a mazání v produkci. Změny se zapisují do auditu. Povolte jen klienta, který jste sami spustili.</p>
{err}
<form method="post" action="/oauth/admin/authorize" autocomplete="on">
{fields}
<label for="email">Admin e-mail</label><input id="email" name="email" type="email" value="{escape(email)}" autocomplete="username" required />
<label for="password">Heslo</label><input id="password" name="password" type="password" autocomplete="current-password" required />
<button type="submit">Přihlásit a povolit</button>
</form>
<p style="margin-top:16px"><small>Přístup můžete kdykoli zrušit v administraci (Nastavení → Admin MCP). Bez použití vyprší po 30 dnech.</small></p>"""
    else:
        body = f"<h1>Požadavek nelze dokončit</h1>{err}<p>Spusťte připojení konektoru znovu z aplikace klienta.</p>"
    return f"""<!doctype html>
<html lang="cs"><head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex" /><title>Připojit administraci — Realitify</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f9f6;font-family:Inter,system-ui,sans-serif;color:#163300}}
main{{width:min(460px,calc(100% - 32px));background:#fff;border-radius:24px;padding:32px;box-shadow:0 16px 16px rgba(14,15,12,.04)}}
h1{{font-size:22px;margin:0 0 8px}}p{{margin:0 0 14px;line-height:1.45;color:#3d4a33}}
.warn{{background:#fff3cd;border-radius:12px;padding:12px 14px;color:#5c4400;font-size:14px}}
.err{{color:#9b1c1c;font-weight:600}}label{{display:block;font-size:13px;font-weight:600;margin:12px 0 4px}}
input[type=email],input[type=password]{{width:100%;box-sizing:border-box;padding:11px 14px;border:1px solid #c8d1bf;border-radius:12px;font:inherit}}
button{{margin-top:18px;appearance:none;border:0;background:#9fe870;color:#163300;font-weight:700;border-radius:999px;padding:12px 18px;cursor:pointer;width:100%}}
small{{color:#6b775f}}
</style></head><body><main>
{body}
</main></body></html>"""
