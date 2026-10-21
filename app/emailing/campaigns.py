"""Admin campaigns: segmentation, preview of the audience, batched sending through the queue."""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app import config, email_notify
from app import guest as guest_access
from app.emailing import render, service, tracking
from app.emailing import store as estore

StoreFor = Callable[[str], Any]

_TEXT_FOOTER = "\n\n--\nTento e-mail dostáváte, protože máte účet na Realitify.cz.\nOdhlásit z tipů a novinek: {{odhlasit_url}}\n"
_HTML_FOOTER = (
    '<p style="margin:32px 0 0;font:13px/1.5 Inter,Arial,sans-serif;color:#868685;">Tento e-mail dostáváte, protože máte účet '
    'na Realitify.cz. <a href="{{odhlasit_url}}" style="color:#868685;">Odhlásit z tipů a novinek</a></p>'
)
_WRAP = (
    '<!doctype html><html lang="cs"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
    '<body style="margin:0;padding:24px 12px;background:#f7f9f6;"><div style="max-width:600px;margin:0 auto;padding:32px;background:#ffffff;'
    'border-radius:24px;font:16px/1.6 Inter,Arial,sans-serif;color:#163300;">{body}</div></body></html>'
)


def normalize_segment(raw: Any) -> dict[str, Any]:
    seg = raw if isinstance(raw, dict) else {}
    plans = [p for p in (seg.get("plans") or []) if p in ("free", "start", "pro")]
    verified = seg.get("verified") if seg.get("verified") in ("any", "yes", "no") else "yes"
    has_dog = seg.get("has_dog") if seg.get("has_dog") in ("any", "yes", "no") else "any"
    try:
        active_days = int(seg.get("active_days")) if seg.get("active_days") not in (None, "") else None
    except (TypeError, ValueError):
        active_days = None
    raw_ex = seg.get("exclude_emails") or []
    if isinstance(raw_ex, str):
        raw_ex = re.split(r"[\s,;]+", raw_ex)
    exclude: list[str] = []
    for item in raw_ex:
        addr = str(item or "").strip().lower()
        if "@" in addr and addr not in exclude:
            exclude.append(addr)
    return {
        "exclude_emails": exclude,
        "audience": seg.get("audience") if seg.get("audience") in ("users", "leads", "both") else "users",
        "plans": plans,
        "verified": verified,
        "registered_from": str(seg.get("registered_from") or ""),
        "registered_to": str(seg.get("registered_to") or ""),
        "has_dog": has_dog,
        "locality": str(seg.get("locality") or "").strip(),
        "active_days": active_days,
        "include_unsubscribed": bool(seg.get("include_unsubscribed")),
    }


def normalize_when(value: Any) -> str:
    """Admin sends local (Prague) time without zone; the DB keeps UTC ISO strings so they compare as text."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Neplatné datum odeslání") from exc
    if parsed.tzinfo is None:
        from zoneinfo import ZoneInfo

        parsed = parsed.replace(tzinfo=ZoneInfo("Europe/Prague"))
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def lead_audience(exclude: set[str] | None = None) -> list[dict[str, Any]]:
    """Guest leads (e-mails from /hledat without an account), oldest first, one per address, minus suppressed ones."""
    seen = set(exclude or ())
    out: list[dict[str, Any]] = []
    for row in reversed(guest_access.list_leads(1_000_000)):
        addr = str(row.get("email") or "").strip().lower()
        if "@" not in addr or addr in seen:
            continue
        seen.add(addr)
        if estore.is_suppressed(addr):
            continue
        out.append({"id": tracking.lead_id(addr), "email": addr, "first": "", "name": "", "plan": "", "lead": True})
    return out


def audience(segment: Any, store_for: StoreFor | None = None) -> list[dict[str, Any]]:
    """Recipients of a segment: registered users, guest leads or both (users win when an address is in both)."""
    seg = normalize_segment(segment)
    people = user_audience(seg, store_for) if seg["audience"] in ("users", "both") else []
    if seg["audience"] in ("leads", "both"):
        with estore.db() as conn:
            registered = {str(r["email"]).strip().lower() for r in conn.execute("SELECT email FROM users").fetchall()}
        people += lead_audience(registered)
    if seg["exclude_emails"]:
        skip = set(seg["exclude_emails"])
        people = [p for p in people if str(p["email"]).strip().lower() not in skip]
    return people


def user_audience(segment: Any, store_for: StoreFor | None = None) -> list[dict[str, Any]]:
    """Registered recipients: [{id, email, first, name, plan}]. Unverified users only when verified='any'."""
    seg = normalize_segment(segment)
    where, params = ["1=1"], []
    if seg["verified"] == "yes":
        where.append("email_verified = 1")
    elif seg["verified"] == "no":
        where.append("email_verified = 0")
    if not seg["include_unsubscribed"]:
        where.append("marketing_unsub = 0")
    if seg["registered_from"]:
        where.append("substr(created_at, 1, 10) >= ?")
        params.append(seg["registered_from"])
    if seg["registered_to"]:
        where.append("substr(created_at, 1, 10) <= ?")
        params.append(seg["registered_to"])
    if seg["active_days"]:
        since = (datetime.now(timezone.utc) - timedelta(days=int(seg["active_days"]))).isoformat()
        where.append("EXISTS (SELECT 1 FROM sessions s WHERE s.user_id = users.id AND s.created_at >= ?)")
        params.append(since)
    with estore.db() as conn:
        rows = conn.execute(
            f"SELECT id, email, first, last, first_vocative FROM users WHERE {' AND '.join(where)} ORDER BY created_at", params
        ).fetchall()
    needs_store = bool(seg["plans"] or seg["has_dog"] != "any" or seg["locality"])
    out: list[dict[str, Any]] = []
    for row in rows:
        item = {
            "id": row["id"],
            "email": row["email"],
            "first": row["first"] or "",
            "first_vocative": row["first_vocative"] or "",
            "name": f"{row['first'] or ''} {row['last'] or ''}".strip(),
            "plan": "",
        }
        if needs_store:
            if store_for is None:
                raise ValueError("Segment podle tarifu, psů nebo lokality potřebuje přístup k uživatelským datům")
            try:
                ustore = store_for(row["id"])
                from app import billing

                item["plan"] = billing.billing_state(ustore)["plan"]
                monitors = ustore.list_monitors_light()
            except Exception:
                continue
            if seg["plans"] and item["plan"] not in seg["plans"]:
                continue
            if seg["has_dog"] == "yes" and not monitors:
                continue
            if seg["has_dog"] == "no" and monitors:
                continue
            if seg["locality"]:
                needle = seg["locality"].lower()
                if not any(needle in f"{m.get('name', '')} {m.get('search_url', '')}".lower() for m in monitors):
                    continue
        out.append(item)
    return out


def audience_preview(segment: Any, store_for: StoreFor | None = None) -> dict[str, Any]:
    people = audience(segment, store_for)
    return {"count": len(people), "sample": [{"email": p["email"], "name": p["name"], "plan": p["plan"]} for p in people[:10]]}


# ------------------------------------------------------------- rendering --

def render_campaign(campaign: dict[str, Any], user: dict[str, Any] | None, to: str) -> tuple[render.Rendered, bool]:
    """Returns (rendered, plain_only)."""
    values = service.common_vars(user, to)
    body = campaign.get("body") or ""
    lead = bool((user or {}).get("lead"))

    def why(text: str) -> str:
        return text.replace("protože máte účet na Realitify.cz", "protože jste na Realitify.cz zadali svůj e-mail") if lead else text

    if campaign.get("mode") == "html":
        if "odhlasit_url" not in body:
            body = body + _HTML_FOOTER if "</html" not in body.lower() else re.sub(r"(?i)</body>", _HTML_FOOTER + "</body>", body, count=1)
        if "<html" not in body.lower():
            body = _WRAP.format(body=body)
        body = why(body)
        return render.render(body, campaign.get("subject") or "", campaign.get("preheader") or "", values), False
    text = why(body if "odhlasit_url" in body else body + _TEXT_FOOTER)
    text_out = render.substitute(text, values, escape=False).strip() + "\n"
    subject = render.substitute(campaign.get("subject") or "", values, escape=False).strip()
    return render.Rendered(subject, "", "", text_out), True


SAMPLE_USER = {"first": "Jiří", "first_vocative": "Jiří", "id": ""}


def preview(campaign: dict[str, Any]) -> dict[str, Any]:
    """The campaign as a recipient gets it (variables, logo and images resolved), for the admin preview."""
    rendered, plain = render_campaign(campaign, SAMPLE_USER, "jiri@example.cz")
    return {"subject": rendered.subject, "html": "" if plain else rendered.html, "text": rendered.text, "plain": plain}


def send_test(campaign: dict[str, Any], to: str) -> int | None:
    if not email_notify.configured():
        raise ValueError("SMTP není nastavené")
    rendered, plain = render_campaign(campaign, SAMPLE_USER, to)
    rendered = render.Rendered("[TEST] " + rendered.subject, rendered.preheader, rendered.html, rendered.text)
    log_id = service._enqueue(
        template_key=None, stream="lifecycle", to=to, user_id="", rendered=rendered, meta={"test": True, "force": True}, plain_only=plain
    )
    if log_id:
        service.process_now(log_id)
    return log_id


def start(cid: int, store_for: StoreFor | None = None) -> dict[str, Any]:
    """Resolve the audience and queue one mail per recipient (the queue loop delivers them in batches)."""
    campaign = estore.campaign_get(cid)
    if not campaign:
        raise ValueError("Kampaň neexistuje")
    if campaign["status"] not in ("draft", "scheduled"):
        raise ValueError("Kampaň už byla spuštěna")
    if not (campaign["subject"] or "").strip() or not (campaign["body"] or "").strip():
        raise ValueError("Kampaň musí mít předmět i obsah")
    if config.EMAIL_ONLY_TRANSACTIONAL:
        raise ValueError("Odesílání je pozastavené (EMAIL_ONLY_TRANSACTIONAL): odchází jen transakční e-maily")
    estore.campaign_update(cid, status="sending", started_at=estore.now_iso())
    queued = 0
    for person in audience(campaign["segment"], store_for):
        rendered, plain = render_campaign(campaign, person, person["email"])
        log_id = service._enqueue(
            template_key=None,
            stream="lifecycle",
            to=person["email"],
            user_id="" if person.get("lead") else person["id"],
            rendered=rendered,
            campaign_id=cid,
            dedupe_key=f"camp:{cid}:{person['id']}",
            plain_only=plain,
        )
        queued += 1 if log_id else 0
    estore.campaign_update(cid, total=queued)
    _finalize(cid)
    return estore.campaign_get(cid) or {}


def schedule_or_send(cid: int, store_for: StoreFor | None = None) -> dict[str, Any]:
    campaign = estore.campaign_get(cid)
    if not campaign:
        raise ValueError("Kampaň neexistuje")
    when = (campaign.get("scheduled_at") or "").strip()
    if when and when > estore.now_iso():
        estore.campaign_update(cid, status="scheduled")
        return estore.campaign_get(cid) or {}
    return start(cid, store_for)


def cancel(cid: int) -> dict[str, Any]:
    campaign = estore.campaign_get(cid)
    if not campaign:
        raise ValueError("Kampaň neexistuje")
    with estore.db() as conn:
        conn.execute("UPDATE email_log SET status = 'canceled' WHERE campaign_id = ? AND status = 'queued'", (cid,))
    if campaign["status"] in ("draft", "scheduled", "sending"):
        estore.campaign_update(cid, status="canceled", finished_at=estore.now_iso())
    return estore.campaign_get(cid) or {}


def _finalize(cid: int) -> None:
    campaign = estore.campaign_get(cid)
    if campaign and campaign["status"] == "sending" and campaign["stats"]["queued"] == 0:
        estore.campaign_update(cid, status="sent", finished_at=estore.now_iso())


def run_due(store_for: StoreFor | None = None) -> None:
    for cid in estore.campaigns_due():
        try:
            start(cid, store_for)
        except Exception as exc:
            print(f"[email] campaign {cid}: {exc}", flush=True)
            estore.campaign_update(cid, status="canceled", finished_at=estore.now_iso())
    with estore.db() as conn:
        sending = [r["id"] for r in conn.execute("SELECT id FROM email_campaigns WHERE status = 'sending'").fetchall()]
    for cid in sending:
        _finalize(int(cid))
