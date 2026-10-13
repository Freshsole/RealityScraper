"""E-mail gated guest access to the public catalog (/hledat).

A visitor leaves an e-mail and gets a signed, HttpOnly cookie that lets the
catalog read endpoints answer from the public store. No account, no password,
and no write access: every other endpoint still requires a real session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from app import config

COOKIE = "realitify_guest"
MAX_AGE_S = 60 * 60 * 24 * 30
_EMAIL_RE = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@.]{2,}$")
_lock = threading.Lock()
_secret_cache: bytes | None = None


def _dir() -> Path:
    path = Path(config.DATA_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    with _lock:
        file = _dir() / "guest_secret"
        if not file.exists():
            tmp = file.with_suffix(".tmp")
            tmp.write_text(secrets.token_hex(32), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(file)
        _secret_cache = file.read_text(encoding="utf-8").strip().encode()
    return _secret_cache


def valid_email(email: str) -> bool:
    email = (email or "").strip()
    return len(email) <= 254 and bool(_EMAIL_RE.match(email))


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue_token(email: str, *, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + MAX_AGE_S)
    mail_id = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
    payload = f"{mail_id}.{exp}"
    return f"{payload}.{_sign(payload)}"


def verify_token(token: str | None, *, now: float | None = None) -> bool:
    if not token:
        return False
    try:
        mail_id, exp, sig = token.split(".")
        payload = f"{mail_id}.{exp}"
        if not hmac.compare_digest(sig, _sign(payload)):
            return False
        return int(exp) > (now if now is not None else time.time())
    except (ValueError, OSError):
        return False


def add_lead(email: str, *, ip: str = "", source: str = "hledat", user_agent: str = "") -> None:
    row = {
        "email": email.strip().lower(),
        "ts": int(time.time()),
        "source": source,
        "ip_hash": hashlib.sha256(f"{ip}{_secret().hex()}".encode()).hexdigest()[:16] if ip else "",
        "ua": (user_agent or "")[:160],
    }
    with _lock:
        with open(_dir() / "guest_leads.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def list_leads(limit: int = 1000) -> list[dict]:
    file = _dir() / "guest_leads.jsonl"
    if not file.exists():
        return []
    rows: list[dict] = []
    for line in file.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[::-1]
