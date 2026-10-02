"""Normalize Czech locality strings for catalog search (no hidden fallbacks)."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.store import PRAGUE_DISTRICTS

KNOWN_CITIES = [
    "Praha",
    "Brno",
    "Ostrava",
    "Plzeň",
    "Olomouc",
    "Liberec",
    "České Budějovice",
    "Hradec Králové",
    "Pardubice",
    "Ústí nad Labem",
    "Zlín",
    "Havířov",
    "Kladno",
    "Most",
    "Opava",
    "Frýdek-Místek",
    "Karviná",
    "Jihlava",
    "Teplice",
    "Děčín",
    "Karlovy Vary",
]


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_NEIGHBORHOOD_TO_PRAHA: dict[str, str] = {}
for num, areas in PRAGUE_DISTRICTS.items():
    for area in areas:
        _NEIGHBORHOOD_TO_PRAHA[_fold(area)] = f"Praha {num}"


_ALIASES: dict[str, str] = {
    "prague": "Praha",
    "praha": "Praha",
    "brno": "Brno",
    "ostrava": "Ostrava",
    "plzen": "Plzeň",
    "pilsen": "Plzeň",
    "olomouc": "Olomouc",
    "liberec": "Liberec",
    "hradec kralove": "Hradec Králové",
    "pardubice": "Pardubice",
    "ceske budejovice": "České Budějovice",
    "usti nad labem": "Ústí nad Labem",
    "zlin": "Zlín",
    "jihlava": "Jihlava",
    "karlovy vary": "Karlovy Vary",
    "smichov": "Praha 5",
    "kosire": "Praha 5",
    "stodulky": "Praha 5",
    "vinohrady": "Praha 2",
    "zizkov": "Praha 3",
    "karlin": "Praha 8",
    "holesovice": "Praha 7",
    "dejvice": "Praha 6",
    "vrsovice": "Praha 10",
    "nusle": "Praha 4",
    "krc": "Praha 4",
    "chodov": "Praha 4",
    "liben": "Praha 8",
    "strasnice": "Praha 10",
}
for n in range(1, 23):
    _ALIASES[f"prague {n}"] = f"Praha {n}"
    _ALIASES[f"praha {n}"] = f"Praha {n}"
    _ALIASES[f"praha{n}"] = f"Praha {n}"


def normalize_locality(raw: str) -> str:
    """Return a canonical locality label for district filters, or the cleaned input."""
    original = (raw or "").strip()
    if not original:
        return ""
    folded = _fold(original)
    if folded in _ALIASES:
        return _ALIASES[folded]
    m = re.match(r"praha[\s\-]*(\d{1,2})$", folded)
    if m and 1 <= int(m.group(1)) <= 22:
        return f"Praha {int(m.group(1))}"
    if folded in _NEIGHBORHOOD_TO_PRAHA:
        return _NEIGHBORHOOD_TO_PRAHA[folded]
    return original


def suggest_localities(raw: str, limit: int = 8) -> list[str]:
    """Nearest known localities for an unknown query (explicit suggestions)."""
    folded = _fold(raw)
    candidates = [f"Praha {n}" for n in range(1, 11)] + KNOWN_CITIES
    if not folded:
        return candidates[:limit]
    scored: list[tuple[int, str]] = []
    for label in candidates:
        lf = _fold(label)
        if folded == lf:
            scored.append((0, label))
        elif folded in lf or lf in folded:
            scored.append((1, label))
        elif any(tok and tok in lf for tok in folded.split()):
            scored.append((2, label))
    scored.sort(key=lambda item: (item[0], item[1]))
    out: list[str] = []
    for _, label in scored:
        if label not in out:
            out.append(label)
        if len(out) >= limit:
            break
    return out or candidates[:limit]


_DISPOSITION_MAP = {
    "1kk": "1+kk",
    "1+kk": "1+kk",
    "1+1": "1+1",
    "11": "1+1",
    "2kk": "2+kk",
    "2+kk": "2+kk",
    "2+1": "2+1",
    "21": "2+1",
    "3kk": "3+kk",
    "3+kk": "3+kk",
    "3+1": "3+1",
    "31": "3+1",
    "4kk": "4+kk",
    "4+kk": "4+kk",
    "4+1": "4+1",
    "41": "4+1",
    "5kk": "5+kk",
    "5+kk": "5+kk",
    "5+1": "5+1",
    "pokoj": "pokoj",
    "studio": "1+kk",
    "garsonka": "1+kk",
    "garsoniera": "1+kk",
    "jednopokojovy": "1+1",
    "dvoupokojovy": "2+1",
    "dvoupokojovy byt": "2+1",
    "tripokojovy": "3+1",
    "tripokojovy byt": "3+1",
}


def normalize_disposition(raw: str) -> str:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        return ""
    out: list[str] = []
    for part in parts:
        key = _fold(part).replace(" ", "")
        alt = _fold(part)
        mapped = _DISPOSITION_MAP.get(key) or _DISPOSITION_MAP.get(alt)
        if mapped:
            out.append(mapped)
            continue
        m = re.match(r"^(\d)\s*\+?\s*(kk|1)$", alt)
        if m:
            out.append(f"{m.group(1)}+{'kk' if m.group(2) == 'kk' else '1'}")
            continue
        out.append(part)
    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return ",".join(uniq)


def normalize_offer(raw: str) -> str:
    value = _fold(raw)
    if value in {"", "any", "all"}:
        return ""
    if value in {"pronajem", "rent", "rental", "lease", "najmout", "najem"}:
        return "pronajem"
    if value in {"prodej", "sale", "buy", "koupit", "sell"}:
        return "prodej"
    raise ValueError("offer_type must be 'pronajem' or 'prodej'")


def floor_from_extras(extras: Any) -> str:
    if isinstance(extras, str):
        try:
            extras = json.loads(extras) if extras else {}
        except json.JSONDecodeError:
            return ""
    if not isinstance(extras, dict):
        return ""
    for spec in extras.get("specs") or []:
        if not isinstance(spec, dict):
            continue
        label = str(spec.get("label") or "").casefold()
        if "podla" in label or label in {"floor", "patro"}:
            return str(spec.get("value") or "").strip()
    return str(extras.get("floor") or extras.get("patro") or "").strip()
