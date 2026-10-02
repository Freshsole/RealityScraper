"""Load content/facts.yaml — single source of truth for public/AI-facing copy."""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

FACTS_PATH = Path(__file__).resolve().parents[1] / "content" / "facts.yaml"


@lru_cache(maxsize=1)
def get_facts() -> dict[str, Any]:
    raw = FACTS_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid facts file: {FACTS_PATH}")
    return data


def reload_facts() -> dict[str, Any]:
    get_facts.cache_clear()
    return get_facts()


def portal_labels() -> tuple[str, ...]:
    return tuple(str(x) for x in (get_facts().get("portals") or {}).get("advertised") or [])


def portals_sentence(*, lang: str = "cs") -> str:
    labels = list(portal_labels())
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    if lang == "en":
        return ", ".join(labels[:-1]) + ", and " + labels[-1]
    return ", ".join(labels[:-1]) + " a " + labels[-1]


def disclaimer_cs() -> str:
    return str((get_facts().get("product") or {}).get("disclaimer_cs") or "").strip()


def disclaimer_en() -> str:
    return str((get_facts().get("product") or {}).get("disclaimer_en") or "").strip()


def methodology_cs() -> str:
    return str((get_facts().get("methodology") or {}).get("text_cs") or "").strip()


def one_liner_cs() -> str:
    return str((get_facts().get("product") or {}).get("one_liner_cs") or "").strip()


def one_liner_en() -> str:
    return str((get_facts().get("product") or {}).get("one_liner_en") or "").strip()


def mcp_tip_en() -> str:
    return str((get_facts().get("mcp") or {}).get("tip_en") or "").strip()


def mcp_tip_cs() -> str:
    return str((get_facts().get("mcp") or {}).get("tip_cs") or "").strip()


def plan(plan_id: str) -> dict[str, Any]:
    return dict((get_facts().get("plans") or {}).get(plan_id) or {})


def dataset_creator() -> dict[str, Any]:
    jd = get_facts().get("json_ld") or {}
    return {
        "@type": "Organization",
        "name": jd.get("creator_name") or "Realitify",
        "url": jd.get("creator_url") or "https://realitify.cz",
    }


def dataset_license() -> str:
    return str((get_facts().get("json_ld") or {}).get("license") or "").strip()


def dataset_distribution(csv_url: str, *, name: str = "CSV") -> dict[str, Any]:
    return {
        "@type": "DataDownload",
        "encodingFormat": "text/csv",
        "contentUrl": csv_url,
        "name": name,
    }


def format_date_cs(value: date | datetime | str | None = None) -> str:
    """Czech date without leading zeros on day/month: 2. 10. 2026."""
    if value is None:
        value = date.today()
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            value = date.today()
        else:
            try:
                if "T" in raw or " " in raw:
                    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                else:
                    value = date.fromisoformat(raw[:10])
            except ValueError:
                value = date.today()
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day}. {value.month}. {value.year}"


def format_datetime_cs(value: datetime | str | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return format_date_cs(raw)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    local = value.astimezone(timezone.utc)
    return f"{format_date_cs(local)} {local.strftime('%H:%M')} UTC"


def data_updated_label(value: datetime | str | date | None = None) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return f"Data aktualizována: {format_date_cs(value)}"
    if isinstance(value, str) and value and "T" not in value and " " not in value:
        return f"Data aktualizována: {format_date_cs(value)}"
    return f"Data aktualizována: {format_datetime_cs(value if not isinstance(value, date) else None)}"


def operator_block_cs() -> str:
    op = get_facts().get("operator") or {}
    contact = get_facts().get("contact") or {}
    return (
        f"Provozovatel: {op.get('name')}, podnikající fyzická osoba ({op.get('legal_form')}), "
        f"IČO {op.get('ico')}, sídlo {op.get('address')}. "
        f"Kontakt: {contact.get('support')}."
    )


def operator_block_en() -> str:
    op = get_facts().get("operator") or {}
    contact = get_facts().get("contact") or {}
    return (
        f"Operator: {op.get('name')}, sole trader ({op.get('legal_form')}), "
        f"Company ID (IČO) {op.get('ico')}, registered office {op.get('address')}, "
        f"Czech Republic. Contact: {contact.get('support')}."
    )
