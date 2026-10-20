"""Admin API for e-mail templates, the send log and campaigns (mounted under /api/admin/emails)."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import APIRouter, Body, Cookie, HTTPException, Query

from app import config, email_notify
from app.emailing import campaigns, catalog, render, service
from app.emailing import store as estore

AdminUser = Callable[[str | None], dict[str, Any]]
StoreFor = Callable[[str], Any]


def _tdef(key: str) -> catalog.TemplateDef:
    tdef = catalog.get_template(key)
    if tdef is None:
        raise HTTPException(404, "Šablona neexistuje")
    return tdef


def _var(v: catalog.Var) -> dict[str, Any]:
    return {"name": v.name, "description": v.description, "sample": v.sample, "raw": v.raw}


def _summary(tdef: catalog.TemplateDef, eff: dict[str, Any], stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": tdef.key,
        "number": tdef.number,
        "group": tdef.group,
        "group_name": tdef.group_name,
        "name": tdef.name,
        "stream": tdef.stream,
        "trigger": tdef.trigger,
        "condition": tdef.condition,
        "enabled": eff["enabled"],
        "customized": eff["customized"],
        "subject": eff["subject"],
        "preheader": eff["preheader"],
        "from": service.sender_for(tdef.stream),
        "stats": stats or {"sent": 0, "failed": 0, "skipped": 0, "opened": 0, "clicked": 0, "last_sent_at": None},
    }


def _preview(key: str, body: dict[str, Any]) -> render.Rendered:
    eff = service.effective(key)
    assert eff is not None
    values = catalog.sample_vars(key)
    values.update({k: str(v) for k, v in (body.get("variables") or {}).items()})
    html = body.get("html") if body.get("html") else eff["html"]
    subject = body.get("subject") if body.get("subject") else eff["subject"]
    pre = body.get("preheader") if body.get("preheader") is not None else eff["preheader"]
    text = body.get("text_body") if body.get("text_body") is not None else eff["text_body"]
    return render.render(html, subject, pre, values, text_body=text)


def build_router(admin_user: AdminUser, store_for: StoreFor) -> APIRouter:
    router = APIRouter(prefix="/api/admin/emails")

    def auth(token: str | None) -> dict[str, Any]:
        return admin_user(token)

    # ------------------------------------------------------------ templates --
    @router.get("/templates")
    async def templates(realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)

        def build() -> dict[str, Any]:
            stats = estore.template_stats()
            out = []
            for tdef in catalog.TEMPLATES:
                out.append(_summary(tdef, service.effective(tdef.key) or {}, stats.get(tdef.key, {})))
            return {"groups": {str(k): v for k, v in catalog.GROUPS.items()}, "templates": out}

        return await asyncio.to_thread(build)

    @router.get("/templates/{key}")
    async def template_detail(key: str, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        tdef = _tdef(key)

        def build() -> dict[str, Any]:
            eff = service.effective(key) or {}
            stats = estore.template_stats().get(key, {})
            return {
                **_summary(tdef, eff, stats),
                "html": eff["html"],
                "text_body": eff["text_body"],
                "default_html": catalog.read_default_html(key),
                "default_subject": tdef.subject,
                "default_preheader": tdef.preheader,
                "variables": [_var(v) for v in tdef.variables],
                "common_variables": [_var(v) for v in catalog.COMMON_VARS],
            }

        return await asyncio.to_thread(build)

    @router.post("/templates/{key}")
    async def template_save(key: str, payload: dict[str, Any] = Body(...), realitify_admin: str | None = Cookie(default=None)) -> dict:
        admin = auth(realitify_admin)
        _tdef(key)
        if not str(payload.get("subject") or "").strip():
            raise HTTPException(400, "Předmět nesmí být prázdný")
        if not str(payload.get("html") or "").strip():
            raise HTTPException(400, "HTML nesmí být prázdné")
        await asyncio.to_thread(
            estore.save_override, key, payload, str(admin.get("email") or admin.get("name") or "")
        )
        return {"ok": True}

    @router.post("/templates/{key}/reset")
    async def template_reset(key: str, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        _tdef(key)
        await asyncio.to_thread(estore.delete_override, key)
        return {"ok": True}

    @router.post("/templates/{key}/preview")
    async def template_preview(key: str, payload: dict[str, Any] | None = Body(None), realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        _tdef(key)
        out = await asyncio.to_thread(_preview, key, payload or {})
        return {"subject": out.subject, "preheader": out.preheader, "html": out.html, "text": out.text}

    @router.post("/templates/{key}/test")
    async def template_test(key: str, payload: dict[str, Any] = Body(...), realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        tdef = _tdef(key)
        to = str(payload.get("to") or "").strip()
        if "@" not in to:
            raise HTTPException(400, "Zadejte e-mail příjemce testu")
        if not email_notify.configured():
            raise HTTPException(503, "SMTP není nastavené (SMTP_HOST / SMTP_FROM)")

        def send() -> dict[str, Any]:
            rendered = _preview(key, payload)
            rendered = render.Rendered("[TEST] " + rendered.subject, rendered.preheader, rendered.html, rendered.text)
            log_id = service._enqueue(
                template_key=key, stream=tdef.stream, to=to, user_id="", rendered=rendered, meta={"test": True, "force": True}
            )
            status = service.process_now(log_id) if log_id else "failed"
            row = estore.log_get(log_id) if log_id else None
            return {"ok": status == "sent", "id": log_id, "status": status, "error": (row or {}).get("error")}

        return await asyncio.to_thread(send)

    # ------------------------------------------------------------------ log --
    @router.get("/log")
    async def log_list(
        template: str = "",
        status: str = "",
        stream: str = "",
        campaign: int | None = None,
        q: str = "",
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        realitify_admin: str | None = Cookie(default=None),
    ) -> dict:
        auth(realitify_admin)

        def build() -> dict[str, Any]:
            data = estore.log_query(template=template, status=status, stream=stream, campaign=campaign, q=q, page=page, page_size=page_size)
            names = {t.key: t.name for t in catalog.TEMPLATES}
            for item in data["items"]:
                item["template_name"] = names.get(item["template_key"] or "", "Kampaň" if item["campaign_id"] else "")
            return data

        return await asyncio.to_thread(build)

    @router.get("/log/{log_id}")
    async def log_detail(log_id: int, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        row = await asyncio.to_thread(estore.log_get, log_id)
        if not row:
            raise HTTPException(404, "Záznam neexistuje")
        return row

    @router.post("/log/{log_id}/retry")
    async def log_retry(log_id: int, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)

        def run() -> dict[str, Any]:
            if not service.retry(log_id):
                raise HTTPException(400, "Znovu jde odeslat jen neodeslané nebo přeskočené e-maily")
            return {"ok": True, "status": service.process_now(log_id)}

        return await asyncio.to_thread(run)

    # ------------------------------------------------------------ campaigns --
    @router.get("/campaigns")
    async def campaign_list(realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        return {"campaigns": await asyncio.to_thread(estore.campaign_list)}

    def _save(payload: dict[str, Any], admin: dict[str, Any], cid: int | None = None) -> int:
        data = dict(payload)
        data["id"] = cid or payload.get("id")
        data["created_by"] = str(admin.get("email") or "")
        try:
            data["scheduled_at"] = campaigns.normalize_when(payload.get("scheduled_at"))
            data["segment"] = campaigns.normalize_segment(payload.get("segment"))
            return estore.campaign_save(data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/campaigns")
    async def campaign_create(payload: dict[str, Any] = Body(...), realitify_admin: str | None = Cookie(default=None)) -> dict:
        admin = auth(realitify_admin)

        def run() -> dict[str, Any]:
            cid = _save(payload, admin)
            return estore.campaign_get(cid) or {}

        return await asyncio.to_thread(run)

    @router.post("/campaigns/audience")
    async def campaign_audience(payload: dict[str, Any] = Body(...), realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        try:
            return await asyncio.to_thread(campaigns.audience_preview, payload.get("segment") or {}, store_for)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/campaigns/{cid}")
    async def campaign_detail(cid: int, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        data = await asyncio.to_thread(estore.campaign_get, cid)
        if not data:
            raise HTTPException(404, "Kampaň neexistuje")
        return data

    @router.post("/campaigns/{cid}/test")
    async def campaign_test(cid: int, payload: dict[str, Any] = Body(...), realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        to = str(payload.get("to") or "").strip()
        if "@" not in to:
            raise HTTPException(400, "Zadejte e-mail příjemce testu")

        def run() -> dict[str, Any]:
            camp = estore.campaign_get(cid)
            if not camp:
                raise HTTPException(404, "Kampaň neexistuje")
            try:
                log_id = campaigns.send_test(camp, to)
            except ValueError as exc:
                raise HTTPException(503, str(exc)) from exc
            row = estore.log_get(log_id) if log_id else None
            return {"ok": (row or {}).get("status") == "sent", "id": log_id, "status": (row or {}).get("status"), "error": (row or {}).get("error")}

        return await asyncio.to_thread(run)

    @router.post("/campaigns/{cid}/send")
    async def campaign_send(cid: int, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        if not email_notify.configured():
            raise HTTPException(503, "SMTP není nastavené (SMTP_HOST / SMTP_FROM)")
        try:
            return await asyncio.to_thread(campaigns.schedule_or_send, cid, store_for)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/campaigns/{cid}/cancel")
    async def campaign_cancel(cid: int, realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)
        try:
            return await asyncio.to_thread(campaigns.cancel, cid)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    # --------------------------------------------------------------- status --
    @router.get("/status")
    async def status(realitify_admin: str | None = Cookie(default=None)) -> dict:
        auth(realitify_admin)

        def build() -> dict[str, Any]:
            since = estore.iso_in(-86400)
            with estore.db() as conn:
                queued = conn.execute("SELECT COUNT(*) FROM email_log WHERE status IN ('queued','sending')").fetchone()[0]
                failed = conn.execute("SELECT COUNT(*) FROM email_log WHERE status = 'failed' AND created_at >= ?", (since,)).fetchone()[0]
            return {
                "provider": email_notify.provider(),
                "suppressed": estore.count_suppressed(),
                "paused_sec": int(service.paused_for()),
                "only_transactional": bool(config.EMAIL_ONLY_TRANSACTIONAL),
                "smtp_configured": email_notify.configured(),
                "senders": service.senders(),
                "queue": {"queued": int(queued), "failed_24h": int(failed)},
            }

        return await asyncio.to_thread(build)

    return router
