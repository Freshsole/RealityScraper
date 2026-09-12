from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from html import escape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app import config
from app.store import Store

CLIENTS_META = "mcp_oauth_clients"
CODES_META = "mcp_oauth_codes"
TOKENS_META = "mcp_oauth_tokens"
CODE_TTL_SEC = 10 * 60
ACCESS_TTL_SEC = 90 * 24 * 3600
REFRESH_TTL_SEC = 180 * 24 * 3600
TOKEN_PREFIX = "rf_oauth_"


def issuer() -> str:
    return config.PUBLIC_BASE_URL.rstrip("/")


def resource_url() -> str:
    return f"{issuer()}/mcp"


def canonical_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return (value or "").strip().rstrip("/")
    host = parsed.hostname or ""
    if parsed.port and not (
        (parsed.scheme == "https" and parsed.port == 443) or (parsed.scheme == "http" and parsed.port == 80)
    ):
        netloc = f"{host.lower()}:{parsed.port}"
    else:
        netloc = host.lower()
    path = parsed.path.rstrip("/") or ""
    return urlunparse((parsed.scheme.lower(), netloc, path, "", "", ""))


def is_https() -> bool:
    return issuer().lower().startswith("https://")


def metadata_url() -> str:
    return f"{issuer()}/.well-known/oauth-protected-resource/mcp"


def www_authenticate() -> str:
    return f'Bearer realm="realitify", resource_metadata="{metadata_url()}"'


def protected_resource_metadata() -> dict[str, Any]:
    return {
        "resource": resource_url(),
        "authorization_servers": [issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
    }


def authorization_server_metadata() -> dict[str, Any]:
    base = issuer()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "scopes_supported": ["mcp", "offline_access"],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
    }


def _read(store: Store, key: str) -> list[dict[str, Any]]:
    raw = store.get_meta(key) or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [item for item in data if isinstance(item, dict)]


def _write(store: Store, key: str, rows: list[dict[str, Any]]) -> None:
    store.set_meta(key, json.dumps(rows[-80:], ensure_ascii=False))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def redirect_allowed(uri: str) -> bool:
    parsed = urlparse((uri or "").strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1"}:
        path = parsed.path or "/"
        return path == "/callback" or path.endswith("/callback")
    if parsed.scheme != "https":
        return False
    if host in {"claude.ai", "www.claude.ai", "claude.com", "www.claude.com"}:
        return True
    if host.endswith(".claude.ai") or host.endswith(".anthropic.com"):
        return True
    return False


def register_client(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    redirects = [str(item).strip() for item in (payload.get("redirect_uris") or []) if str(item).strip()]
    if not redirects:
        raise ValueError("Chybí redirect_uris")
    client_id = secrets.token_urlsafe(24)
    row = {
        "client_id": client_id,
        "redirect_uris": redirects,
        "client_name": str(payload.get("client_name") or "Claude")[:120],
        "token_endpoint_auth_method": "none",
        "created_at": int(time.time()),
    }
    rows = _read(store, CLIENTS_META)
    rows.append(row)
    _write(store, CLIENTS_META, rows)
    return {
        "client_id": client_id,
        "client_id_issued_at": row["created_at"],
        "redirect_uris": redirects,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "client_name": row["client_name"],
    }


def _client(store: Store, client_id: str) -> dict[str, Any] | None:
    for row in _read(store, CLIENTS_META):
        if row.get("client_id") == client_id:
            return row
    return None


def save_code(store: Store, payload: dict[str, Any]) -> str:
    code = secrets.token_urlsafe(32)
    rows = [row for row in _read(store, CODES_META) if int(row.get("exp") or 0) > time.time()]
    rows.append({**payload, "code": _hash(code), "exp": int(time.time()) + CODE_TTL_SEC})
    _write(store, CODES_META, rows)
    return code


def consume_code(store: Store, code: str) -> dict[str, Any] | None:
    digest = _hash(code)
    now = time.time()
    kept: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    for row in _read(store, CODES_META):
        if int(row.get("exp") or 0) <= now:
            continue
        if row.get("code") == digest and found is None:
            found = row
            continue
        kept.append(row)
    _write(store, CODES_META, kept)
    return found


def authenticate_token(store: Store, token: str) -> dict[str, Any] | None:
    digest = _hash(token)
    now = time.time()
    for row in _read(store, TOKENS_META):
        if row.get("access_hash") == digest and int(row.get("access_exp") or 0) > now:
            return {"id": row.get("id") or "oauth", "name": row.get("name") or "Claude"}
    return None


def _issue_tokens(store: Store, name: str = "Claude") -> dict[str, str]:
    access = TOKEN_PREFIX + secrets.token_urlsafe(32)
    refresh = "rf_rt_" + secrets.token_urlsafe(32)
    row = {
        "id": secrets.token_hex(8),
        "name": name,
        "access_hash": _hash(access),
        "refresh_hash": _hash(refresh),
        "access_exp": int(time.time()) + ACCESS_TTL_SEC,
        "refresh_exp": int(time.time()) + REFRESH_TTL_SEC,
    }
    rows = _read(store, TOKENS_META)
    rows.append(row)
    _write(store, TOKENS_META, rows)
    return {"access_token": access, "refresh_token": refresh}


def token_payload(tokens: dict[str, str]) -> dict[str, Any]:
    return {
        "access_token": tokens["access_token"],
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SEC,
        "refresh_token": tokens["refresh_token"],
        "scope": "mcp",
    }


def exchange_code(store: Store, *, code: str, redirect_uri: str, client_id: str, code_verifier: str) -> dict[str, Any]:
    row = consume_code(store, code)
    if not row:
        raise ValueError("invalid_grant")
    if row.get("client_id") != client_id or row.get("redirect_uri") != redirect_uri:
        raise ValueError("invalid_grant")
    challenge = str(row.get("code_challenge") or "")
    if challenge and pkce_s256(code_verifier) != challenge:
        raise ValueError("invalid_grant")
    return token_payload(_issue_tokens(store, str(row.get("name") or "Claude")))


def refresh_tokens(store: Store, refresh_token: str) -> dict[str, Any]:
    digest = _hash(refresh_token)
    now = time.time()
    rows = _read(store, TOKENS_META)
    kept: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    for row in rows:
        if row.get("refresh_hash") == digest and int(row.get("refresh_exp") or 0) > now and found is None:
            found = row
            continue
        kept.append(row)
    if not found:
        _write(store, TOKENS_META, kept)
        raise ValueError("invalid_grant")
    issued = _issue_tokens(store, str(found.get("name") or "Claude"))
    return token_payload(issued)


def revoke(store: Store, token: str) -> None:
    digest = _hash(token)
    rows = [
        row
        for row in _read(store, TOKENS_META)
        if row.get("access_hash") != digest and row.get("refresh_hash") != digest
    ]
    _write(store, TOKENS_META, rows)


def validate_authorize(store: Store, params: dict[str, str]) -> dict[str, str]:
    client_id = (params.get("client_id") or "").strip()
    redirect_uri = (params.get("redirect_uri") or "").strip()
    response_type = (params.get("response_type") or "").strip()
    challenge = (params.get("code_challenge") or "").strip()
    method = (params.get("code_challenge_method") or "S256").strip()
    if response_type != "code":
        raise ValueError("Nepodporovaný response_type")
    if method != "S256" or not challenge:
        raise ValueError("Claude vyžaduje PKCE S256")
    if not redirect_allowed(redirect_uri):
        raise ValueError("Nepovolený redirect_uri")
    def _redirect_matches(registered: str, actual: str) -> bool:
        if registered == actual:
            return True
        left, right = urlparse(registered), urlparse(actual)
        left_host = (left.hostname or "").lower()
        right_host = (right.hostname or "").lower()
        if left.scheme != right.scheme:
            return False
        if left_host in {"localhost", "127.0.0.1"} and right_host in {"localhost", "127.0.0.1"}:
            return (left.path or "/callback") == (right.path or "/callback")
        return False

    client = _client(store, client_id)
    if client:
        allowed = [str(item) for item in (client.get("redirect_uris") or [])]
        if not any(_redirect_matches(item, redirect_uri) for item in allowed):
            raise ValueError("redirect_uri neodpovídá zaregistrovanému klientovi")
    elif not client_id:
        raise ValueError("Chybí client_id")
    resource = canonical_url(params.get("resource") or resource_url())
    if resource and resource != canonical_url(resource_url()):
        raise ValueError("resource musí být MCP URL tohoto účtu")
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": params.get("state") or "",
        "code_challenge": challenge,
        "scope": params.get("scope") or "mcp",
        "resource": params.get("resource") or resource_url(),
    }


def complete_authorize(store: Store, params: dict[str, str]) -> str:
    from app.agents import mcp_enabled

    if not mcp_enabled(store):
        raise PermissionError("MCP je v tarifu PRO")
    fields = validate_authorize(store, params)
    code = save_code(
        store,
        {
            "client_id": fields["client_id"],
            "redirect_uri": fields["redirect_uri"],
            "code_challenge": fields["code_challenge"],
            "name": "Claude",
        },
    )
    query = dict(parse_qsl(urlparse(fields["redirect_uri"]).query, keep_blank_values=True))
    query["code"] = code
    if fields["state"]:
        query["state"] = fields["state"]
    parsed = urlparse(fields["redirect_uri"])
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))


def consent_html(params: dict[str, str], *, error: str = "") -> str:
    fields = "".join(
        f'<input type="hidden" name="{escape(key)}" value="{escape(value)}" />'
        for key, value in params.items()
        if value
    )
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return f"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Připojit Claude — Realitify</title>
  <style>
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:#f7f9f6; font-family:Inter,system-ui,sans-serif; color:#163300; }}
    main {{ width:min(440px, calc(100% - 32px)); background:#fff; border-radius:24px; padding:32px; box-shadow:0 16px 16px rgba(14,15,12,.04); }}
    h1 {{ font-size:22px; margin:0 0 8px; }}
    p {{ margin:0 0 16px; line-height:1.45; color:#3d4a33; }}
    .err {{ color:#9b1c1c; }}
    button {{ appearance:none; border:0; background:#9fe870; color:#163300; font-weight:700; border-radius:999px; padding:12px 18px; cursor:pointer; width:100%; }}
    a {{ color:#163300; }}
  </style>
</head>
<body>
  <main>
    <h1>Povolit Claude?</h1>
    <p>Claude dostane přístup k vašemu katalogu inzerátů a hlídacím psům v Realitify. Přístup kdykoli zrušíte v Nastavení → AI agenti a MCP.</p>
    {err}
    <form method="post" action="/oauth/authorize">
      {fields}
      <button type="submit">Povolit Claude</button>
    </form>
    <p style="margin-top:16px;font-size:13px"><a href="/nastaveni/agenti">Zpět do Realitify</a></p>
  </main>
</body>
</html>"""
