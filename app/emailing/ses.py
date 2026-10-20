"""Amazon SES helpers: SNS webhook verification, bounce/complaint suppression."""

from __future__ import annotations

import base64
import hmac
import json
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlsplit

from app import config
from app.emailing import store as estore

_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")
_CERTS: dict[str, bytes] = {}
_CERT_LOCK = threading.Lock()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def _fetch(url: str, limit: int = 100_000) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(url, timeout=10) as resp:  # noqa: S310 - https + amazonaws.com checked by callers
        return resp.read(limit)


# Tests replace this to avoid the network.
fetch: Callable[[str], bytes] = _fetch


def _https_host(url: str) -> str | None:
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts.hostname.lower()


def cert_url_ok(url: str) -> bool:
    host = _https_host(url)
    return bool(host and _SNS_HOST.match(host) and urlsplit(url).path.endswith(".pem"))


def subscribe_url_ok(url: str) -> bool:
    host = _https_host(url)
    return bool(host and host.endswith(".amazonaws.com"))


_SIGNED = {
    "Notification": ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type"),
    "SubscriptionConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"),
    "UnsubscribeConfirmation": ("Message", "MessageId", "SubscribeURL", "Timestamp", "Token", "TopicArn", "Type"),
}


def string_to_sign(msg: dict[str, Any]) -> bytes | None:
    names = _SIGNED.get(str(msg.get("Type") or ""))
    if not names:
        return None
    out = ""
    for name in names:
        if name == "Subject" and name not in msg:
            continue
        if name not in msg:
            return None
        out += f"{name}\n{msg[name]}\n"
    return out.encode("utf-8")


def verify_signature(msg: dict[str, Any]) -> bool:
    """Check the SNS signature with the AWS signing certificate (cryptography ships with pywebpush)."""
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    url = str(msg.get("SigningCertURL") or "")
    if not cert_url_ok(url):
        return False
    data = string_to_sign(msg)
    if data is None:
        return False
    version = str(msg.get("SignatureVersion") or "")
    digest = {"1": hashes.SHA1, "2": hashes.SHA256}.get(version)  # noqa: S303 - AWS signature v1 is SHA1
    if digest is None:
        return False
    try:
        signature = base64.b64decode(str(msg.get("Signature") or ""), validate=True)
        with _CERT_LOCK:
            pem = _CERTS.get(url)
        if pem is None:
            pem = fetch(url)
            with _CERT_LOCK:
                _CERTS[url] = pem
        key = x509.load_pem_x509_certificate(pem).public_key()
        key.verify(signature, data, padding.PKCS1v15(), digest())  # type: ignore[call-arg]
        return True
    except (InvalidSignature, ValueError, OSError, urllib.error.URLError):
        return False


def topic_allowed(arn: str) -> bool:
    allowed = [a.strip() for a in config.SES_SNS_TOPIC_ARN.split(",") if a.strip()]
    return bool(arn) and arn in allowed


def webhook_configured() -> bool:
    return bool(config.SES_WEBHOOK_TOKEN or config.SES_SNS_TOPIC_ARN)


def authorize(msg: dict[str, Any], token: str) -> str | None:
    """Return None when the request may be processed, else a short refusal reason.

    A valid AWS signature alone is not enough (anybody can create an SNS topic that is signed by AWS), so the
    shared URL token and/or the topic ARN allow-list must match as well.
    """
    if not webhook_configured():
        return "not_configured"
    if config.SES_WEBHOOK_TOKEN and not hmac.compare_digest(token or "", config.SES_WEBHOOK_TOKEN):
        return "bad_token"
    if config.SES_SNS_TOPIC_ARN and not topic_allowed(str(msg.get("TopicArn") or "")):
        return "bad_topic"
    if not verify_signature(msg):
        return "bad_signature"
    return None


def confirm_subscription(msg: dict[str, Any]) -> bool:
    url = str(msg.get("SubscribeURL") or "")
    if not subscribe_url_ok(url):
        return False
    try:
        fetch(url)
        return True
    except (OSError, urllib.error.URLError):
        return False


def _mark_user(email: str) -> None:
    try:
        with estore.db() as conn:
            conn.execute("UPDATE users SET email_suppressed = 1 WHERE lower(email) = ?", (email,))
    except Exception as exc:  # the suppression list itself is the source of truth
        print(f"[ses] user flag: {exc}", flush=True)


def _log_ids(mail: dict[str, Any]) -> list[int]:
    tags = (mail or {}).get("tags") or {}
    out: list[int] = []
    for value in tags.get("log_id") or []:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            pass
    return out


def handle_notification(message: dict[str, Any]) -> dict[str, int]:
    """Process one SES event (the JSON inside an SNS Notification). Returns counters for logging/tests."""
    kind = str(message.get("notificationType") or message.get("eventType") or "")
    result = {"suppressed": 0}
    if kind == "Bounce":
        bounce = message.get("bounce") or {}
        if str(bounce.get("bounceType") or "") != "Permanent":
            return result
        reason = f"bounce:{bounce.get('bounceSubType') or 'Permanent'}"
        addresses = [str(r.get("emailAddress") or "") for r in bounce.get("bouncedRecipients") or []]
    elif kind == "Complaint":
        reason = "complaint"
        addresses = [str(r.get("emailAddress") or "") for r in (message.get("complaint") or {}).get("complainedRecipients") or []]
    else:  # Delivery and everything else: nothing to do
        return result
    for address in addresses:
        addr = address.strip().lower()
        if not addr:
            continue
        if estore.suppress(addr, reason):
            result["suppressed"] += 1
        _mark_user(addr)
    for log_id in _log_ids(message.get("mail") or {}):
        row = estore.log_get(log_id)
        if row and row["status"] == "sent":
            estore.log_update(log_id, status="failed", error=reason)
    return result


def process_request(body: bytes, token: str) -> tuple[int, dict[str, Any]]:
    """Whole webhook flow. Returns (http_status, json)."""
    try:
        msg = json.loads(body.decode("utf-8"))
        if not isinstance(msg, dict):
            raise ValueError("not an object")
    except (ValueError, UnicodeDecodeError):
        return 400, {"detail": "invalid body"}
    refusal = authorize(msg, token)
    if refusal == "not_configured":
        return 503, {"detail": "SES webhook is not configured (SES_WEBHOOK_TOKEN or SES_SNS_TOPIC_ARN)"}
    if refusal:
        return 403, {"detail": refusal}
    kind = str(msg.get("Type") or "")
    if kind == "SubscriptionConfirmation":
        return (200, {"ok": True}) if confirm_subscription(msg) else (400, {"detail": "subscribe url refused"})
    if kind == "Notification":
        try:
            inner = json.loads(str(msg.get("Message") or "{}"))
        except ValueError:
            return 400, {"detail": "invalid message"}
        if isinstance(inner, dict):
            return 200, {"ok": True, **handle_notification(inner)}
    return 200, {"ok": True}
