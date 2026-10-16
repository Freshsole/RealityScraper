"""Agent / AI discovery surface: Link headers, markdown negotiation, /.well-known/* manifests.

Everything here is derived from config + content/ so it never drifts from the real endpoints.
"""

from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app import config, mcp_oauth
from app.agents import TOOLS
from app.version import current_version

SKILLS_DIR = Path(__file__).resolve().parent.parent / "content" / "agent-skills"
DOMAIN = "realitify.cz"
_PUBLIC_HEAD_PATHS = {
    "/", "/index", "/hledat", "/faq", "/o-nas", "/about", "/mcp-docs", "/mcp-docs-cs", "/uspechy",
    "/kontakt", "/pronajem-praha", "/pronajem-brno", "/robots.txt", "/sitemap.xml", "/llms.txt",
}
_MD_PAGES = {
    "/": "home:md", "/index": "index:md", "/faq": "faq:md", "/o-nas": "about:md", "/mcp-docs": "mcp_docs:md",
}


def base() -> str:
    return mcp_oauth.issuer()


def link_header() -> str:
    b = base()
    return ", ".join([
        f'</.well-known/api-catalog>; rel="api-catalog"',
        f'</api/agents/openapi.json>; rel="service-desc"; type="application/vnd.oai.openapi+json"',
        f'</mcp-docs>; rel="service-doc"',
        f'</llms.txt>; rel="describedby"; type="text/plain"',
        f'</sitemap.xml>; rel="sitemap"; type="application/xml"',
        f'</.well-known/mcp/server-card.json>; rel="mcp-server-card"',
        f'</.well-known/ai-catalog.json>; rel="ai-catalog"',
        f'</.well-known/agent-skills/index.json>; rel="agent-skills"',
    ])


# --- manifests ---------------------------------------------------------------

def api_catalog() -> dict[str, Any]:
    b = base()
    return {
        "linkset": [
            {
                "anchor": f"{b}/api/v1",
                "service-desc": [{"href": f"{b}/api/agents/openapi.json", "type": "application/vnd.oai.openapi+json"}],
                "service-doc": [{"href": f"{b}/mcp-docs", "type": "text/html"}],
                "status": [{"href": f"{b}/health", "type": "application/json"}],
            },
            {
                "anchor": f"{b}/mcp",
                "service-desc": [{"href": f"{b}/.well-known/mcp/server-card.json", "type": "application/json"}],
                "service-doc": [{"href": f"{b}/mcp-docs", "type": "text/html"}],
                "status": [{"href": f"{b}/health", "type": "application/json"}],
            },
        ]
    }


def server_card() -> dict[str, Any]:
    b = base()
    return {
        "$schema": "https://static.modelcontextprotocol.io/schemas/mcp-server-card/v1.json",
        "serverInfo": {"name": "realitify", "title": "Realitify – Czech real-estate search", "version": current_version()},
        "description": "Search apartments and houses from Czech real-estate portals, read market stats, manage new-listing alerts.",
        "documentationUrl": f"{b}/mcp-docs",
        "transport": {"type": "streamable-http", "endpoint": f"{b}/mcp"},
        "authentication": {
            "required": True,
            "schemes": ["oauth2", "bearer"],
            "resource_metadata": mcp_oauth.metadata_url(),
        },
        "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}, "prompts": {"listChanged": False}},
        "tools": [{"name": t["name"], "description": t.get("description", "")} for t in TOOLS],
    }


def ai_catalog() -> dict[str, Any]:
    b = base()
    return {
        "specVersion": "0.1",
        "host": {"displayName": "Realitify", "identifier": DOMAIN, "documentationUrl": f"{b}/llms.txt"},
        "entries": [
            {
                "identifier": f"urn:air:{DOMAIN}:mcp:realitify",
                "displayName": "Realitify MCP – Czech real-estate search",
                "description": "Search listings from Czech portals and manage new-listing alerts.",
                "type": "application/mcp-server-card+json",
                "url": f"{b}/.well-known/mcp/server-card.json",
                "representativeQueries": [
                    "Najdi pronájem bytu 2+kk v Praze do 25 000 Kč",
                    "Find apartments for rent in Brno under 20000 CZK",
                    "Kolik stojí průměrný nájem v Ostravě?",
                    "Hlídej mi nové byty na Smíchově",
                ],
            },
            {
                "identifier": f"urn:air:{DOMAIN}:openapi:listings-api",
                "displayName": "Realitify REST API",
                "description": "Read listings, monitors and alerts over REST.",
                "type": "application/vnd.oai.openapi+json",
                "url": f"{b}/api/agents/openapi.json",
                "representativeQueries": [
                    "REST API pro vyhledávání nabídek nemovitostí v ČR",
                    "Czech real estate listings API",
                    "Get latest property alerts via API",
                ],
            },
        ],
    }


def _skills() -> list[dict[str, str]]:
    out = []
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        data = path.read_bytes()
        text = data.decode("utf-8")
        desc = re.search(r"^description:\s*(.+)$", text, re.M)
        out.append({
            "name": path.parent.name,
            "type": "skill-md",
            "description": desc.group(1).strip() if desc else path.parent.name,
            "url": f"{base()}/.well-known/agent-skills/{path.parent.name}/SKILL.md",
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        })
    return out


def skills_index() -> dict[str, Any]:
    return {"$schema": "https://schemas.agentskills.io/discovery/0.2.0/schema.json", "skills": _skills()}


def poppy() -> dict[str, Any]:
    b = base()
    return {
        "protocol_version": "0.1",
        "organization": {"name": "Realitify", "domain": DOMAIN, "url": b},
        "auth": {"issuer": b},
        "agent": {"protocols": [{"type": "mcp", "endpoint": f"{b}/mcp"}]},
        "apis": [{"type": "openapi", "url": f"{b}/api/agents/openapi.json", "description": "Realitify REST API for listings, monitors and alerts"}],
        "web": {},
    }


def auth_md() -> str:
    b = base()
    text = f"""# Realitify auth.md – agent authentication

Realitify is a Czech real-estate aggregator. Public search needs no credentials; managing monitors and
alerts over MCP/REST needs an access token from a registered user (MCP access requires the PRO plan).

## Public (no auth)
- `GET {b}/api/public/catalog?q=Praha&offer=pronajem` – search listings
- `GET {b}/llms.txt`, {b}/index.csv – market data

## Authenticated resource
- MCP (Streamable HTTP): {b}/mcp, REST: {b}/api/v1, OpenAPI: {b}/api/agents/openapi.json
- Protected resource metadata: {b}/.well-known/oauth-protected-resource
- Authorization server metadata (with `agent_auth`): {b}/.well-known/oauth-authorization-server
- Scope: `mcp`

## Register an agent (user-claimed flow)
1. `POST {b}/agent/identity` with JSON `{{"type":"service_auth","login_hint":"user@example.com","agent_name":"My agent"}}`
   (`login_hint` optional). Response: `claim_token` and `claim` (`user_code`, `verification_uri`, `verification_uri_complete`, `expires_in`, `interval`).
2. Show the user `user_code` and `verification_uri`. The user signs in at Realitify (free account: {b}/registrace) and confirms the code.
3. Poll `POST {b}/oauth/token` with `grant_type=urn:workos:agent-auth:grant-type:claim&claim_token=...` every `interval` seconds.
   - `authorization_pending` (400): keep polling
   - `expired_token` (400): `POST {b}/agent/identity/claim` with `{{"claim_token":"...","email":"..."}}` for a fresh code
   - `access_denied` (403): the account's plan does not include MCP
   - 200: `access_token` + `refresh_token` (Bearer, scope `mcp`)
4. Call {b}/mcp with `Authorization: Bearer <access_token>`; refresh via `grant_type=refresh_token`.

## Alternatives
- Interactive OAuth 2.1 + PKCE with dynamic client registration: `POST {b}/oauth/register`, authorize at {b}/oauth/authorize.

## Revocation
`POST {b}/oauth/revoke` with `token=...`; users can also revoke in Settings → AI agents.
"""
    # Scanners extract bare URLs from this file; backticks would stick to them.
    return text.replace("`", "")


# --- HTML -> markdown --------------------------------------------------------

class _MD(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "header", "footer", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.href: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        a = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4"}:
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "ul", "ol", "table", "tr"}:
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag == "br":
            self.out.append("\n")
        elif tag == "a" and a.get("href"):
            self.href = a["href"]
            self.out.append("[")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag == "a" and self.href:
            href = self.href
            if href.startswith("/"):
                href = base() + href
            self.out.append(f"]({href})")
            self.href = None
        elif tag in {"h1", "h2", "h3", "h4", "p", "section"}:
            self.out.append("\n")
        elif tag in {"td", "th"}:
            self.out.append(" | ")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(re.sub(r"\s+", " ", data))


def html_to_markdown(html: str) -> str:
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    parser = _MD()
    parser.feed(html)
    body = re.sub(r"\n{3,}", "\n\n", "".join(parser.out)).strip()
    return (f"# {title.group(1).strip()}\n\n" if title else "") + body + "\n"


def _wants_markdown(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "text/markdown" not in accept:
        return False
    md_q, html_q = 0.0, 0.0
    for part in accept.split(","):
        mime, _, params = part.strip().partition(";")
        q = 1.0
        m = re.search(r"q=([\d.]+)", params)
        if m:
            try:
                q = float(m.group(1))
            except ValueError:
                pass
        if mime.strip() == "text/markdown":
            md_q = max(md_q, q)
        elif mime.strip() in {"text/html", "*/*", "text/*"}:
            html_q = max(html_q, q if mime.strip() == "text/html" else q * 0.5)
    return md_q > 0 and md_q >= html_q


# --- wiring ------------------------------------------------------------------

def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def agent_discovery(request: Request, call_next):
        path = request.url.path
        is_head = request.method == "HEAD" and path in _PUBLIC_HEAD_PATHS
        if is_head:
            request.scope["method"] = "GET"
        response = await call_next(request)
        ctype = (response.headers.get("content-type") or "").lower()
        is_html = ctype.startswith("text/html")
        if is_html and response.status_code == 200:
            response.headers["Link"] = link_header()
            response.headers.append("Vary", "Accept")
            if request.method in {"GET"} and _wants_markdown(request) and not path.startswith(("/api", "/admin")):
                body = b"".join([chunk async for chunk in response.body_iterator])
                md = None
                key = _MD_PAGES.get(path)
                if key:
                    from app import ssr_cache

                    page = ssr_cache.get_page(key)
                    md = (page or {}).get("body")
                if not md:
                    md = html_to_markdown(body.decode("utf-8", "replace"))
                headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
                headers["x-markdown-tokens"] = str(max(1, len(md) // 4))
                return Response(md, media_type="text/markdown; charset=utf-8", headers=headers)
        if is_head:
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return Response(status_code=response.status_code, headers=headers)
        return response

    def _json(data: dict, media_type: str = "application/json", cache: str = "public, max-age=3600") -> JSONResponse:
        return JSONResponse(data, media_type=media_type, headers={"Cache-Control": cache, "Access-Control-Allow-Origin": "*"})

    @app.get("/.well-known/api-catalog", include_in_schema=False)
    def _api_catalog():
        return _json(api_catalog(), 'application/linkset+json; profile="https://www.rfc-editor.org/info/rfc9727"')

    @app.get("/.well-known/mcp/server-card.json", include_in_schema=False)
    def _server_card():
        return _json(server_card())

    @app.get("/.well-known/mcp.json", include_in_schema=False)
    def _server_card_alias():
        return _json(server_card())

    @app.get("/.well-known/ai-catalog.json", include_in_schema=False)
    def _ai_catalog():
        return _json(ai_catalog())

    @app.get("/.well-known/agent-skills/index.json", include_in_schema=False)
    def _skills_index():
        return _json(skills_index())

    @app.get("/.well-known/agent-skills/{name}/SKILL.md", include_in_schema=False)
    def _skill(name: str):
        path = SKILLS_DIR / name / "SKILL.md"
        if not re.fullmatch(r"[a-z0-9-]+", name) or not path.is_file():
            return PlainTextResponse("Not found", status_code=404)
        return Response(path.read_bytes(), media_type="text/markdown; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=3600", "Access-Control-Allow-Origin": "*"})

    @app.get("/.well-known/poppy.json", include_in_schema=False)
    def _poppy():
        return _json(poppy())

    @app.get("/auth.md", include_in_schema=False)
    def _auth_md():
        return Response(auth_md(), media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
