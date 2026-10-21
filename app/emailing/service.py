"""Queueing and delivery of templated e-mails."""

from __future__ import annotations

import asyncio
import json
import re
import smtplib
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Any, Callable, Mapping

from app import config
from app import email_notify
from app.emailing import catalog, render, tracking
from app.emailing import store as estore

STREAMS = ("transactional", "alerts", "lifecycle")
_STREAM_LOCAL = {"transactional": "noreply", "alerts": "alerty", "lifecycle": "ahoj"}
_BACKOFF_SEC = (60, 300, 1800)
MAX_ATTEMPTS = 3
AUTH_PAUSE_SEC = 300  # after an SMTP login failure nothing is sent for 5 minutes (messages stay queued)
STALE_ALERT_SEC = 6 * 3600  # an alert about a new listing is useless after this long
STALE_KEYS = ("alert_new_listing", "alert_digest")

_pause_until = 0.0
_throttle_lock = threading.Lock()
_last_send = 0.0

# Per-template check run right before delivery of a delayed mail: returns a skip reason or None.
Guard = Callable[[dict[str, Any]], "str | None"]
GUARDS: dict[str, Guard] = {}


# ------------------------------------------------------------- senders --

def sender_for(stream: str) -> str:
    """From header for a stream: env override, else <noreply|alerty|ahoj>@ on the SMTP_FROM domain."""
    explicit = {
        "transactional": config.EMAIL_FROM_TRANSACTIONAL,
        "alerts": config.EMAIL_FROM_ALERTS,
        "lifecycle": config.EMAIL_FROM_LIFECYCLE,
    }.get(stream, "")
    if explicit:
        return explicit
    base = email_notify.from_addr()
    _, addr = parseaddr(base)
    domain = addr.rpartition("@")[2].lower()
    if not base:
        return ""
    if domain == "realitify.cz":
        return formataddr(("Realitify", f"{_STREAM_LOCAL.get(stream, 'noreply')}@{domain}"))
    return base


def senders() -> dict[str, str]:
    return {stream: sender_for(stream) for stream in STREAMS}


# ------------------------------------------------------ pause / limits --

def paused_for() -> float:
    """Seconds left of the post-auth-failure pause (0 when sending is allowed)."""
    return max(0.0, _pause_until - time.time())


def _pause_sending(seconds: float = AUTH_PAUSE_SEC) -> None:
    global _pause_until
    _pause_until = time.time() + seconds


def _throttle() -> float:
    """Honour EMAIL_MAX_PER_SEC by a short sleep; returns seconds to defer when EMAIL_MAX_PER_HOUR is reached."""
    global _last_send
    hourly = config.EMAIL_MAX_PER_HOUR
    if hourly and estore.sent_since(estore.iso_in(-3600)) >= hourly:
        return 60.0
    per_sec = config.EMAIL_MAX_PER_SEC
    if per_sec > 0:
        with _throttle_lock:
            wait = _last_send + 1.0 / per_sec - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_send = time.monotonic()
    return 0.0


def paused_stream(stream: str, meta: Mapping[str, Any] | None = None) -> bool:
    """EMAIL_ONLY_TRANSACTIONAL holds back everything except transactional mail, EMAIL_ALERTS_PAUSED only alerts (admin tests always go)."""
    if (meta or {}).get("test"):
        return False
    if config.EMAIL_ALERTS_PAUSED and stream == "alerts":
        return True
    return bool(config.EMAIL_ONLY_TRANSACTIONAL and stream != "transactional")


def _is_stale(row: dict[str, Any]) -> bool:
    if row.get("template_key") not in STALE_KEYS:
        return False
    try:
        created = datetime.fromisoformat(str(row.get("created_at") or ""))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > timedelta(seconds=STALE_ALERT_SEC)


def _defer(row: dict[str, Any], seconds: float, error: str | None = None) -> None:
    """Put a claimed row back to the queue without counting the attempt."""
    estore.log_update(
        int(row["id"]),
        status="queued",
        attempts=max(0, int(row.get("attempts") or 1) - 1),
        error=error,
        scheduled_at=estore.iso_in(seconds),
    )


# ----------------------------------------------------------- templates --

def effective(key: str) -> dict[str, Any] | None:
    """Template as it is sent: admin override over the shipped default."""
    tdef = catalog.get_template(key)
    if tdef is None:
        return None
    override = estore.get_override(key)
    default_html = catalog.read_default_html(key)
    out = {
        "key": key,
        "stream": tdef.stream,
        "subject": tdef.subject,
        "preheader": tdef.preheader,
        "html": default_html,
        "text_body": "",
        "enabled": True,
        "customized": False,
    }
    if override:
        out["subject"] = override["subject"] or tdef.subject
        out["preheader"] = override["preheader"] if override["preheader"] is not None else tdef.preheader
        out["html"] = override["html"] or default_html
        out["text_body"] = override["text_body"] or ""
        out["enabled"] = bool(override["enabled"])
        out["customized"] = True
    return out


def is_enabled(key: str) -> bool:
    eff = effective(key)
    return bool(eff and eff["enabled"])


# ----------------------------------------------------------- variables --

def common_vars(user: Mapping[str, Any] | None, to: str) -> dict[str, str]:
    first = str((user or {}).get("first") or "").strip()
    if not first:
        name = str((user or {}).get("name") or "").strip()
        first = name.split(" ")[0] if name else ""
    base = tracking.base_url()
    uid = str((user or {}).get("id") or "")
    return {
        "osloveni": f"Dobrý den, {first}," if first else "Dobrý den,",
        "jmeno": first,
        "email": to,
        "base_url": base,
        "nastaveni_url": f"{base}/nastaveni",
        "odhlasit_url": tracking.unsubscribe_url(uid) if uid else f"{base}/nastaveni",
        "odhlasit_alerty_url": tracking.alerts_unsubscribe_url(uid) if uid else f"{base}/nastaveni",
        "podpora_email": "podpora@realitify.cz",
        "rok": str(estore.datetime.now().year),
    }


def _user_flags(user_id: str) -> dict[str, Any] | None:
    return tracking.get_user_flag(user_id) if user_id else None


# --------------------------------------------------------------- queue --

def _enqueue(
    *,
    template_key: str | None,
    stream: str,
    to: str,
    user_id: str,
    rendered: render.Rendered,
    campaign_id: int | None = None,
    dedupe_key: str | None = None,
    delay_sec: float = 0,
    meta: dict[str, Any] | None = None,
    status: str = "queued",
    error: str | None = None,
    plain_only: bool = False,
) -> int | None:
    meta = dict(meta or {})
    if plain_only:
        meta["plain_only"] = True
    return estore.log_insert(
        template_key=template_key,
        campaign_id=campaign_id,
        user_id=user_id or None,
        to_email=to,
        stream=stream,
        subject=rendered.subject,
        html="" if plain_only else rendered.html,
        text_body=rendered.text,
        status=status,
        error=error,
        scheduled_at=estore.iso_in(delay_sec) if delay_sec else estore.now_iso(),
        dedupe_key=None if status == "skipped" else dedupe_key,
        meta=meta,
    )


def send_template(
    key: str,
    *,
    user: Mapping[str, Any] | None = None,
    to: str | None = None,
    variables: Mapping[str, Any] | None = None,
    dedupe_key: str | None = None,
    delay_sec: float = 0,
    force: bool = False,
    meta: dict[str, Any] | None = None,
    immediate: bool | None = None,
) -> int | None:
    """Render a template and queue it. Transactional and alert mails are delivered right away.

    Returns the log id, or None when ``dedupe_key`` was already used. ``force`` ignores the enabled switch
    and the marketing opt-out (used for admin tests and explicit user requests).
    """
    tdef = catalog.get_template(key)
    if tdef is None:
        raise ValueError(f"Neznámá šablona: {key}")
    if force:
        meta = {**(meta or {}), "force": True}
    eff = effective(key)
    assert eff is not None
    recipient = (to or (user or {}).get("email") or "").strip()
    if not recipient:
        raise ValueError("Chybí příjemce")
    user_id = str((user or {}).get("id") or "")
    values = {**common_vars(user, recipient), **{k: ("" if v is None else str(v)) for k, v in (variables or {}).items()}}
    rendered = render.render(eff["html"], eff["subject"], eff["preheader"], values, text_body=eff["text_body"])
    skip: str | None = None
    if paused_stream(tdef.stream, meta):
        # Alerts would flood the log (one row per listing and user); the pause is visible in the admin status instead.
        if tdef.stream == "alerts":
            return None
        skip = "paused"
    elif not eff["enabled"] and not force:
        skip = "disabled"
    elif tdef.stream == "lifecycle" and not force:
        flags = _user_flags(user_id)
        if flags and flags.get("marketing_unsub"):
            skip = "marketing_unsub"
    if skip is None and not email_notify.configured():
        skip = "smtp_not_configured"
    if skip is None and estore.is_suppressed(recipient):
        skip = "suppressed"
    if skip:
        return _enqueue(template_key=key, stream=tdef.stream, to=recipient, user_id=user_id, rendered=rendered, status="skipped", error=skip, meta=meta)
    log_id = _enqueue(
        template_key=key,
        stream=tdef.stream,
        to=recipient,
        user_id=user_id,
        rendered=rendered,
        dedupe_key=dedupe_key,
        delay_sec=delay_sec,
        meta=meta,
    )
    now_send = immediate if immediate is not None else (tdef.stream in ("transactional", "alerts") and not delay_sec)
    if log_id and now_send:
        process_now(log_id)
    return log_id


def fire(key: str, **kwargs: Any) -> None:
    """Fire-and-forget ``send_template`` from sync or async code (SMTP never blocks the caller)."""

    def run() -> None:
        try:
            send_template(key, **kwargs)
        except Exception as exc:  # never break the calling flow because of a mail
            print(f"[email] {key}: {exc}", flush=True)

    threading.Thread(target=run, name=f"email-{key}", daemon=True).start()


# ------------------------------------------------------------ delivery --

def deliver(msg: EmailMessage) -> None:
    """The single SMTP exit point (tests replace it)."""
    email_notify.send_mime(msg)


def _fetch_attachment(url: str) -> bytes | None:
    if not url.lower().startswith("https://"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 - https only
            data = resp.read(5_000_001)
        return data if 0 < len(data) <= 5_000_000 else None
    except Exception:
        return None


def build_message(row: dict[str, Any], meta: dict[str, Any]) -> EmailMessage:
    stream = row["stream"]
    msg = EmailMessage()
    msg["Subject"] = row["subject"]
    sender = sender_for(stream) or email_notify.from_addr()
    name, addr = parseaddr(sender)
    msg["From"] = formataddr((name or "Realitify", addr or sender))
    msg["To"] = row["to_email"]
    if config.EMAIL_REPLY_TO:
        msg["Reply-To"] = config.EMAIL_REPLY_TO
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=(addr.rpartition("@")[2] or "realitify.cz"))
    user_id = row.get("user_id") or ""
    if stream == "lifecycle" and user_id:
        msg["List-Unsubscribe"] = f"<{tracking.unsubscribe_url(user_id)}>, <mailto:{config.EMAIL_REPLY_TO}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    elif stream == "alerts" and user_id:
        msg["List-Unsubscribe"] = f"<{tracking.alerts_unsubscribe_url(user_id)}>, <mailto:{config.EMAIL_REPLY_TO}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(row.get("text_body") or render.html_to_text(row.get("html") or ""))
    config_set = (
        (config.SES_CONFIGURATION_SET_TRANSACTIONAL or config.SES_CONFIGURATION_SET)
        if stream == "transactional"
        else config.SES_CONFIGURATION_SET
    )
    if config_set:
        msg["X-SES-CONFIGURATION-SET"] = config_set
        tags = [("stream", stream), ("log_id", str(row.get("id") or ""))]
        if row.get("template_key"):
            tags.insert(0, ("template_key", str(row["template_key"])))
        msg["X-SES-MESSAGE-TAGS"] = ", ".join(f"{k}={re.sub(r'[^A-Za-z0-9_.@-]', '_', v)}" for k, v in tags if v)
    if row.get("html") and not meta.get("plain_only"):
        html_out = tracking.instrument(
            row["html"], int(row["id"]), pixel=stream in ("alerts", "lifecycle"), clicks=stream == "lifecycle"
        )
        msg.add_alternative(html_out, subtype="html")
    url = str(meta.get("attachment_url") or "")
    if url:
        data = _fetch_attachment(url)
        if data:
            msg.add_attachment(data, maintype="application", subtype="pdf", filename=str(meta.get("attachment_name") or "faktura.pdf"))
    return msg


def process_now(log_id: int) -> str:
    """Deliver one queued row. Returns the resulting status ('sent', 'queued' (retry), 'failed', 'skipped')."""
    if not estore.claim(log_id):
        row = estore.log_get(log_id)
        return str((row or {}).get("status") or "missing")
    row = estore.log_get(log_id)
    assert row is not None
    try:
        meta = json.loads(row.get("meta") or "{}")
    except json.JSONDecodeError:
        meta = {}
    if paused_stream(str(row.get("stream") or ""), meta):
        estore.log_update(log_id, status="skipped", error="paused")
        return "skipped"
    guard = GUARDS.get(row.get("template_key") or "")
    if guard:
        try:
            reason = guard(row)
        except Exception as exc:
            print(f"[email] guard {row.get('template_key')}: {exc}", flush=True)
            reason = None
        if reason:
            estore.log_update(log_id, status="skipped", error=reason)
            return "skipped"
    if row["stream"] == "lifecycle" and not meta.get("force") and row.get("user_id"):
        flags = _user_flags(row["user_id"])
        if flags and flags.get("marketing_unsub"):
            estore.log_update(log_id, status="skipped", error="marketing_unsub")
            return "skipped"
    if estore.is_suppressed(row["to_email"]):
        estore.log_update(log_id, status="skipped", error="suppressed")
        return "skipped"
    if _is_stale(row):
        estore.log_update(log_id, status="skipped", error="stale")
        return "skipped"
    remaining = paused_for()
    if remaining > 0:
        _defer(row, remaining + 1, error=row.get("error") or "smtp_auth_paused")
        return "queued"
    wait = _throttle()
    if wait > 0:
        _defer(row, wait, error="rate_limited")
        return "queued"
    try:
        deliver(build_message(row, meta))
    except smtplib.SMTPAuthenticationError as exc:
        # Bad SMTP credentials hit every message: stop for a while instead of burning the attempts of the queue.
        _pause_sending()
        if _is_stale(row):
            estore.log_update(log_id, status="skipped", error="stale")
            return "skipped"
        _defer(row, AUTH_PAUSE_SEC + 1, error=f"auth: {exc}"[:500])
        return "queued"
    except smtplib.SMTPRecipientsRefused as exc:
        estore.log_update(log_id, status="failed", error=f"bounce: {exc}"[:500])
        return "failed"
    except Exception as exc:
        attempts = int(row.get("attempts") or 1)
        if attempts >= MAX_ATTEMPTS:
            estore.log_update(log_id, status="failed", error=str(exc)[:500])
            return "failed"
        estore.log_update(log_id, status="queued", error=str(exc)[:500], scheduled_at=estore.iso_in(_BACKOFF_SEC[min(attempts, 3) - 1]))
        return "queued"
    estore.log_update(log_id, status="sent", sent_at=estore.now_iso(), error=None)
    return "sent"


def process_queue(limit: int = 50) -> int:
    if paused_for() > 0:
        return 0
    done = 0
    for log_id in estore.due_ids(limit):
        process_now(log_id)
        done += 1
    return done


def retry(log_id: int) -> bool:
    row = estore.log_get(log_id)
    if not row or row["status"] not in ("failed", "skipped"):
        return False
    estore.log_update(log_id, status="queued", error=None, attempts=0, scheduled_at=estore.now_iso())
    return True


# ---------------------------------------------------------------- loop --

async def run_email_loop(store_for: Callable[[str], Any] | None = None, global_store: Any = None) -> None:
    """Queue every 15 s, lifecycle scheduler every 60 s. Runs in the scrape worker (and in SCRAPE_ROLE=all)."""
    if not config.EMAIL_AUTOMATION:
        print("[email] automation off (EMAIL_AUTOMATION=0)", flush=True)
        return
    if global_store is not None:
        estore.bind(global_store)
    from app.emailing import campaigns, triggers

    last_sched = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            for _ in range(5):  # keep draining while the queue is full (the rate limit still applies)
                if await asyncio.to_thread(process_queue, 50) < 50:
                    break
            if not config.EMAIL_ONLY_TRANSACTIONAL:
                await asyncio.to_thread(campaigns.run_due, store_for)
            if loop.time() - last_sched >= 60:
                last_sched = loop.time()
                await asyncio.to_thread(triggers.run_scheduler, store_for)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[email] loop error: {exc}", flush=True)
        await asyncio.sleep(15)
