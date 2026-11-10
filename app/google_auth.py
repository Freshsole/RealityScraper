"""Google OAuth 2.0 sign-in (authorization-code flow, server side).

Flow: /api/auth/google/start -> Google -> /api/auth/google/callback -> session cookie.
Accounts are matched by e-mail; Google must report the address as verified.
"""

from __future__ import annotations

import base64
import json
import secrets
import urllib.parse
from typing import Any

import httpx

from app import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
STATE_COOKIE = "realitify_gstate"


def enabled() -> bool:
    return bool(config.GOOGLE_OAUTH_CLIENT_ID and config.GOOGLE_OAUTH_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"{config.PUBLIC_BASE_URL}/api/auth/google/callback"


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(state: str) -> str:
    q = {
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


def safe_next(value: str | None) -> str:
    v = (value or "").strip()
    if v.startswith("/") and not v.startswith("//") and "\\" not in v:
        return v
    return "/prehled"


def exchange_code(code: str) -> dict[str, Any]:
    """Swap the code for tokens and return the id_token claims (received over TLS straight from Google)."""
    resp = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise ValueError("Google přihlášení selhalo")
    id_token = str(resp.json().get("id_token") or "")
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception as exc:
        raise ValueError("Google přihlášení selhalo") from exc
    if claims.get("aud") != config.GOOGLE_OAUTH_CLIENT_ID or claims.get("iss") not in {
        "https://accounts.google.com",
        "accounts.google.com",
    }:
        raise ValueError("Google přihlášení selhalo")
    if not claims.get("email") or claims.get("email_verified") not in (True, "true"):
        raise ValueError("Google účet nemá ověřený e-mail")
    return claims
