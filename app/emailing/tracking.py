"""Open pixel, click redirect, one-click actions and unsubscribe (all links are HMAC-signed)."""

from __future__ import annotations

import hashlib
import hmac
import html as _html
import re
from typing import Any
from urllib.parse import quote, urlsplit

from app import config
from app.emailing import store as estore

PIXEL_GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00"
    b"\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)

_HREF_RE = re.compile(r"""(<a\b[^>]*?\bhref=)(["'])(.*?)\2""", re.I | re.S)
_SKIP_MARKERS = ("/odhlasit", "/e/a/", "/e/c/", "/e/o/")


def base_url() -> str:
    return (config.PUBLIC_BASE_URL or "").rstrip("/") or "https://realitify.cz"


def _sig(message: str) -> str:
    return hmac.new(estore.secret(), message.encode(), hashlib.sha256).hexdigest()[:24]


def _ok(message: str, sig: str) -> bool:
    return bool(sig) and hmac.compare_digest(_sig(message), sig)


# ----------------------------------------------------------- log tokens --

def log_token(log_id: int) -> str:
    return f"{int(log_id)}.{_sig(f'log:{int(log_id)}')}"


def parse_log_token(token: str) -> int | None:
    head, _, sig = (token or "").partition(".")
    if not head.isdigit() or not _ok(f"log:{head}", sig):
        return None
    return int(head)


# -------------------------------------------------------- unsubscribe --

def unsubscribe_token(user_id: str) -> str:
    return f"{user_id}.{_sig(f'unsub:{user_id}')}"


def parse_unsubscribe_token(token: str) -> str | None:
    user_id, _, sig = (token or "").rpartition(".")
    if not user_id or not _ok(f"unsub:{user_id}", sig):
        return None
    return user_id


def unsubscribe_url(user_id: str) -> str:
    return f"{base_url()}/odhlasit?token={quote(unsubscribe_token(user_id))}"


# ------------------------------------------------------ one-click actions --

ACTIONS = {"found", "hledam", "pauza", "duvod_bydleni", "duvod_draha", "duvod_relevance", "duvod_jiny"}


def action_url(user_id: str, action: str) -> str:
    return f"{base_url()}/e/a/{user_id}.{_sig(f'act:{user_id}:{action}')}?a={action}"


def parse_action(token: str, action: str) -> str | None:
    if action not in ACTIONS:
        return None
    user_id, _, sig = (token or "").rpartition(".")
    if not user_id or not _ok(f"act:{user_id}:{action}", sig):
        return None
    return user_id


# ------------------------------------------------------ instrumentation --

def _hrefs(source: str) -> set[str]:
    return {_html.unescape(m.group(3)).strip() for m in _HREF_RE.finditer(source or "")}


def instrument(html_source: str, log_id: int, *, pixel: bool, clicks: bool) -> str:
    """Add the open pixel and/or route http(s) links through the click redirect."""
    token = log_token(log_id)
    out = html_source
    if clicks:

        def repl(match: re.Match[str]) -> str:
            url = _html.unescape(match.group(3)).strip()
            if not url.lower().startswith(("http://", "https://")) or any(m in url for m in _SKIP_MARKERS):
                return match.group(0)
            target = f"{base_url()}/e/c/{token}?u={quote(url, safe='')}"
            return f"{match.group(1)}{match.group(2)}{_html.escape(target, quote=True)}{match.group(2)}"

        out = _HREF_RE.sub(repl, out)
    if pixel:
        img = f'<img src="{base_url()}/e/o/{token}.gif" width="1" height="1" alt="" style="display:block;border:0;width:1px;height:1px;">'
        idx = out.lower().rfind("</body>")
        out = out[:idx] + img + out[idx:] if idx >= 0 else out + img
    return out


def record_open(token: str) -> None:
    log_id = parse_log_token(token)
    if not log_id:
        return
    with estore.db() as conn:
        conn.execute(
            "UPDATE email_log SET open_count = open_count + 1, opened_at = COALESCE(opened_at, ?) WHERE id = ?",
            (estore.now_iso(), log_id),
        )


def resolve_click(token: str, url: str) -> str | None:
    """Return the redirect target, or None. Only URLs that are in the stored e-mail body are allowed."""
    log_id = parse_log_token(token)
    if not log_id:
        return None
    url = (url or "").strip()
    if urlsplit(url).scheme not in {"http", "https"}:
        return None
    row = estore.log_get(log_id)
    if not row or url not in _hrefs(row.get("html") or ""):
        return None
    now = estore.now_iso()
    with estore.db() as conn:
        # a click also proves the mail was opened (images are often blocked)
        conn.execute(
            "UPDATE email_log SET click_count = click_count + 1, clicked_at = COALESCE(clicked_at, ?),"
            " opened_at = COALESCE(opened_at, ?) WHERE id = ?",
            (now, now, log_id),
        )
    return url


# -------------------------------------------------------------- pages --

_PAGE = """<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{title} – Realitify</title>
<style>body{{margin:0;background:#f7f9f6;color:#163300;font:16px/1.5 Inter,Arial,sans-serif}}main{{max-width:480px;margin:12vh auto;padding:36px;background:#fff;border-radius:24px;box-shadow:0 12px 32px #0e0f0c14}}
h1{{margin:0 0 12px;font:800 28px/1.15 Manrope,Inter,Arial,sans-serif;letter-spacing:-.02em}}p{{color:#555b54}}
button,a.btn{{display:inline-block;margin-top:12px;padding:13px 28px;border:0;border-radius:999px;background:#9fe870;color:#163300;font:600 16px Inter,Arial,sans-serif;cursor:pointer;text-decoration:none}}</style></head>
<body><main><h1>{title}</h1>{body}</main></body></html>"""


def page(title: str, body_html: str) -> str:
    return _PAGE.format(title=_html.escape(title), body=body_html)


def unsubscribe_page(token: str, *, done: bool = False, resubscribed: bool = False) -> str:
    t = _html.escape(token, quote=True)
    if done:
        return page(
            "Odhlášeno",
            "<p>Už vám nebudeme posílat tipy a novinky. Upozornění na nové nabídky a e-maily o účtu a platbách"
            " vám chodí dál.</p>"
            f'<form method="post" action="/odhlasit?token={t}"><input type="hidden" name="action" value="resubscribe">'
            "<button type=\"submit\">Vrátit zpět</button></form>",
        )
    if resubscribed:
        return page("Vítejte zpět", "<p>Tipy a novinky vám zase budeme posílat.</p>")
    return page(
        "Odhlásit z tipů a novinek",
        "<p>Přestaneme vám posílat tipy, novinky a přehledy trhu. Upozornění na nové nabídky a e-maily o účtu"
        " a platbách zůstanou zapnuté.</p>"
        f'<form method="post" action="/odhlasit?token={t}"><button type="submit">Odhlásit</button></form>',
    )


def get_user_flag(user_id: str) -> dict[str, Any] | None:
    with estore.db() as conn:
        row = conn.execute("SELECT id, email, marketing_unsub FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None
