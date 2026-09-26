from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app import config
from app.annonce import AnnonceClient
from app.bazos import BazosClient
from app.bezrealitky import BezrealitkyClient
from app.ceskereality import CeskerealityClient
from app.idnes import IdnesClient
from app.mmreality import MmrealityClient
from app.portal_urls import (
    annonce_url,
    ceskereality_url,
    mmreality_url,
    realitycz_url,
    remax_url,
    ulovdomov_url,
)
from app.realitycz import RealityczClient
from app.remax import RemaxClient
from app.sreality import SrealityClient
from app.ulovdomov import UlovdomovClient
from app import bazos_url, bezrealitky_url, idnes_url, url_builder


@dataclass(frozen=True)
class PortalSpec:
    id: str
    label: str
    hosts: tuple[str, ...]
    url_likes: tuple[str, ...]
    client: type
    urls: Any
    default_search: str


def _host(url: str) -> str:
    return (urlparse((url or "").strip()).hostname or "").lower().removeprefix("www.")


PORTALS: tuple[PortalSpec, ...] = (
    PortalSpec(
        id="sreality",
        label="Sreality",
        hosts=("sreality.cz",),
        url_likes=("%sreality.cz%",),
        client=SrealityClient,
        urls=url_builder,
        default_search="https://www.sreality.cz/hledani/pronajem/byty?razeni=nejnovejsi",
    ),
    PortalSpec(
        id="idnes",
        label="Reality.iDNES",
        hosts=("reality.idnes.cz",),
        url_likes=("%idnes.cz%",),
        client=IdnesClient,
        urls=idnes_url,
        default_search="https://reality.idnes.cz/s/pronajem/byty/?s-l-rq=1",
    ),
    PortalSpec(
        id="bazos",
        label="Bazoš",
        hosts=("reality.bazos.cz", "bazos.cz"),
        url_likes=("%bazos.cz%",),
        client=BazosClient,
        urls=bazos_url,
        default_search="https://reality.bazos.cz/pronajmu/byt/?kitx=ano",
    ),
    PortalSpec(
        id="ceskereality",
        label="ČeskéReality",
        hosts=("ceskereality.cz",),
        url_likes=("%ceskereality.cz%",),
        client=CeskerealityClient,
        urls=ceskereality_url,
        default_search=ceskereality_url.build_url({"offers": ["pronajem"]}),
    ),
    PortalSpec(
        id="bezrealitky",
        label="Bezrealitky",
        hosts=("bezrealitky.cz",),
        url_likes=("%bezrealitky.cz%",),
        client=BezrealitkyClient,
        urls=bezrealitky_url,
        default_search="https://www.bezrealitky.cz/vyhledat?offerType=PRONAJEM&estateType=BYT&order=TIMEORDER_DESC",
    ),
    PortalSpec(
        id="annonce",
        label="Annonce",
        hosts=("annonce.cz",),
        url_likes=("%annonce.cz%",),
        client=AnnonceClient,
        urls=annonce_url,
        default_search=annonce_url.build_url({"offers": ["pronajem"]}),
    ),
    PortalSpec(
        id="mmreality",
        label="M&M Reality",
        hosts=("mmreality.cz",),
        url_likes=("%mmreality.cz%",),
        client=MmrealityClient,
        urls=mmreality_url,
        default_search=mmreality_url.build_url({"offers": ["pronajem"]}),
    ),
    PortalSpec(
        id="ulovdomov",
        label="UlovDomov",
        hosts=("ulovdomov.cz",),
        url_likes=("%ulovdomov.cz%",),
        client=UlovdomovClient,
        urls=ulovdomov_url,
        default_search=ulovdomov_url.build_url({"offers": ["pronajem"]}),
    ),
    PortalSpec(
        id="remax",
        label="RE/MAX",
        hosts=("remax-czech.cz", "remax.cz"),
        url_likes=("%remax-czech.cz%", "%remax.cz%"),
        client=RemaxClient,
        urls=remax_url,
        default_search=remax_url.build_url({"offers": ["pronajem"]}),
    ),
    PortalSpec(
        id="realitycz",
        label="Reality.cz",
        hosts=("reality.cz",),
        url_likes=("%://www.reality.cz%", "%://reality.cz/%", "%://m.reality.cz%"),
        client=RealityczClient,
        urls=realitycz_url,
        default_search=realitycz_url.build_url({"offers": ["pronajem"]}),
    ),
)

PORTAL_BY_ID: dict[str, PortalSpec] = {item.id: item for item in PORTALS}
PORTAL_IDS: tuple[str, ...] = tuple(item.id for item in PORTALS)
PORTAL_LABELS: dict[str, str] = {item.id: item.label for item in PORTALS}
# Screenshot / product order.
PORTAL_ORDER: tuple[str, ...] = (
    "sreality",
    "idnes",
    "bazos",
    "ceskereality",
    "bezrealitky",
    "annonce",
    "mmreality",
    "ulovdomov",
    "remax",
    "realitycz",
)


def portal_spec(portal_id: str) -> PortalSpec | None:
    return PORTAL_BY_ID.get((portal_id or "").strip().lower())


def portal_of(url: str) -> str:
    host = _host(url)
    raw = (url or "").lower()
    if "bezrealitky.cz" in host or "bezrealitky" in raw:
        return "bezrealitky"
    if "reality.idnes.cz" in host or "idnes.cz/s/" in raw or "idnes.cz/detail/" in raw:
        return "idnes"
    if "bazos" in host or "bazos" in raw:
        return "bazos"
    if "ceskereality.cz" in host:
        return "ceskereality"
    if host.endswith("annonce.cz") or host == "annonce.cz":
        return "annonce"
    if "mmreality.cz" in host:
        return "mmreality"
    if "ulovdomov.cz" in host:
        return "ulovdomov"
    if "remax" in host:
        return "remax"
    if host == "sreality.cz" or host.endswith(".sreality.cz") or "sreality.cz" in raw:
        return "sreality"
    if host == "reality.cz" or host.endswith(".reality.cz"):
        return "realitycz"
    return "sreality"


def is_bezrealitky(url: str) -> bool:
    return portal_of(url) == "bezrealitky"


def is_idnes(url: str) -> bool:
    return portal_of(url) == "idnes"


def is_bazos(url: str) -> bool:
    return portal_of(url) == "bazos"


def client_for(search_url: str):
    spec = PORTAL_BY_ID.get(portal_of(search_url), PORTAL_BY_ID["sreality"])
    return spec.client(search_url)


def source_name(url: str) -> str:
    return PORTAL_LABELS.get(portal_of(url), "Sreality")


def portal_label(portal: str) -> str:
    return PORTAL_LABELS.get((portal or "").lower(), (portal or "Web").title())


def url_mod_for(source: str | None = None, url: str = ""):
    key = (source or "").lower() or portal_of(url)
    spec = PORTAL_BY_ID.get(key)
    return spec.urls if spec else url_builder


def url_likes(portal: str) -> tuple[str, ...]:
    spec = PORTAL_BY_ID.get((portal or "").lower())
    return spec.url_likes if spec else ()


def registered_portals() -> list[dict[str, str]]:
    return [{"id": item.id, "label": item.label, "search_url": item.default_search} for item in PORTALS]


def is_discord_webhook(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and "/api/webhooks/" in (parsed.path or "")
        and (host == "discord.com" or host == "discordapp.com" or host.endswith(".discord.com"))
    )


def usable_discord_webhook(url: str) -> str:
    raw = (url or "").strip()
    if not raw or not is_discord_webhook(raw):
        return ""
    lowered = raw.lower()
    if "webhooks/id/token" in lowered or "/webhooks/id/" in lowered:
        return ""
    parts = [part for part in urlparse(raw).path.split("/") if part]
    try:
        index = parts.index("webhooks")
        hook_id = parts[index + 1]
        token = parts[index + 2]
    except (ValueError, IndexError):
        return ""
    if not hook_id.isdigit() or token.lower() in {"token", "id"} or len(token) < 20:
        return ""
    return raw


def webhook_for(search_url: str, monitor_webhook: str | None = None) -> str:
    if (monitor_webhook or "").strip():
        return usable_discord_webhook(monitor_webhook)
    portal = portal_of(search_url)
    if portal == "idnes":
        return usable_discord_webhook(getattr(config, "IDNES_WEBHOOK_URL", "") or config.DISCORD_WEBHOOK_URL)
    if portal == "bezrealitky":
        return usable_discord_webhook(config.BEZREALITKY_WEBHOOK_URL)
    if portal == "bazos":
        return usable_discord_webhook(getattr(config, "BAZOS_WEBHOOK_URL", "") or config.DISCORD_WEBHOOK_URL)
    return usable_discord_webhook(config.DISCORD_WEBHOOK_URL)
