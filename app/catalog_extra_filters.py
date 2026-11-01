"""Filtry z /nabidka (vlastnictví, stav, konstrukce, vybavení, příslušenství, S/Bez, další dispozice)
pro hlídací psy. Pravidla zrcadlí SQL v Store.catalog, jen nad jedním inzerátem v Pythonu."""

from __future__ import annotations

import json
from typing import Any

# Klíče v URL hlídače (předpona nb_ = "nabídka"), aby se nepletly s kódy Sreality.
LIST_KEYS = ("ownership", "condition", "building", "equipped", "amenities", "disp")
TRI_KEYS = ("roommate", "pets", "short_term")
ALL_KEYS = LIST_KEYS + TRI_KEYS
PARAM_PREFIX = "nb_"

_BUILDINGS = {
    "cihlova": ["cihl"],
    "panelova": ["panel"],
    "drevena": ["dřev"],
    "kamenna": ["kamenn"],
    "skeletova": ["skelet"],
    "montovana": ["montovan"],
    "smisena": ["smíšen"],
    "ostatni": ["smíšená", "montovan", "skelet", "kamenn", "ostatní"],
}
_EQUIPPED = {
    "vybaveny": [
        '"vybavení", "value": "vybavený"',
        '"vybavení", "value": "vybaveno"',
        '"vybavení", "value": "ano"',
        '"vybavení", "value": "zařízený"',
        '"equipped": "zařízený"',
    ],
    "castecne": ['"vybavení", "value": "částečně"', '"equipped": "částečně zařízený"'],
    "nevybaveny": [
        '"vybavení", "value": "nevybaven"',
        '"vybavení", "value": "ne"',
        '"vybavení", "value": "nezařízený"',
        '"equipped": "nezařízený"',
    ],
}


def _field(listing: Any, key: str, default: Any = "") -> Any:
    if isinstance(listing, dict):
        return listing.get(key, default)
    return getattr(listing, key, default)


def _blob(listing: Any) -> str:
    extras = _field(listing, "extras", {}) or {}
    if isinstance(extras, str):
        return extras.casefold()
    try:
        return json.dumps(extras, ensure_ascii=False).casefold()
    except (TypeError, ValueError):
        return ""


def _csv(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item for item in value.split(",") if item]
    return [str(item) for item in (value or []) if item]


def _any(blob: str, patterns: list[str]) -> bool:
    return any(p.casefold() in blob for p in patterns)


def _tri(value: str, hit: bool) -> bool:
    if value == "s":
        return hit
    if value == "bez":
        return not hit
    return True


def matches(listing: Any, data: dict[str, Any]) -> bool:
    blob = _blob(listing)

    ownership = _csv(data.get("ownership"))
    if ownership and not _any(blob, [f'"vlastnictví", "value": "{x}"' for x in ownership] + [f'"ownership": "{x}"' for x in ownership]):
        return False

    conditions = _csv(data.get("condition"))
    if conditions:
        patterns: list[str] = []
        for label in conditions:
            patterns += [f'"stav", "value": "{label}"', f'"condition": "{label}"']
            if label == "Po rekonstrukci":
                patterns.append('"stav", "value": "po částečné rekonstrukci"')
            if label == "Dobrý":
                patterns.append("dobrý stav")
            if label == "Špatný":
                patterns.append("špatný stav")
            if label == "Projekt":
                patterns.append("projekt")
        if not _any(blob, patterns):
            return False

    buildings = _csv(data.get("building"))
    if buildings and not _any(blob, [p for b in buildings for p in _BUILDINGS.get(b, [])]):
        return False

    equipped = _csv(data.get("equipped"))
    if equipped and not _any(blob, [p for e in equipped for p in _EQUIPPED.get(e, [])]):
        return False

    amenities = set(_csv(data.get("amenities")))
    roommate = str(data.get("roommate") or "") or ("s" if "roommate" in amenities else "")
    pets = str(data.get("pets") or "") or ("s" if "pets" in amenities else "")
    short_term = str(data.get("short_term") or "") or ("s" if "short_term" in amenities else "")
    for flag in amenities - {"roommate", "pets", "short_term"}:
        if f'"{flag}"'.casefold() not in blob:
            return False

    if roommate in {"s", "bez"}:
        text = f"{_field(listing, 'name')} {_field(listing, 'description')}".casefold()
        hit = (
            '"roommate"' in blob
            or "spolubydl" in text
            or str(_field(listing, "disposition")).casefold() == "pokoj"
            or "/pokoj/" in str(_field(listing, "url")).casefold()
        )
        if not _tri(roommate, hit):
            return False
    if pets in {"s", "bez"} and not _tri(pets, '"pets"' in blob or '"mazlíčci", "value": "povolení"' in blob):
        return False
    if short_term in {"s", "bez"} and not _tri(short_term, '"short_term"' in blob):
        return False

    return True
