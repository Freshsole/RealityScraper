"""Cross-portal deduplikace: stejná nemovitost na více portálech."""

from __future__ import annotations

import re
import unicodedata


def normalize_address(addr: str) -> str:
    """Normalizuje adresu pro porovnání: lowercase, bez diakritiky, bez mezer navíc."""
    if not addr:
        return ""
    text = unicodedata.normalize("NFD", addr.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[,.]", " ", text)  # čárky/tečky -> mezera
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(ul\.|ulice)\s+", "", text)
    return text


def normalize_disposition(disp: str) -> str:
    """Normalizuje dispozici: '2+kk' == '2 + kk' == '2+KK'."""
    if not disp:
        return ""
    text = disp.lower().replace(" ", "")
    # 2+1 == 2+kk v některých případech, ale držíme odděleně
    return text


def price_close(p1: int | None, p2: int | None, tolerance_pct: float = 5.0) -> bool:
    """Ceny jsou blízké (do X % rozdílu)."""
    if not p1 or not p2:
        return False
    diff = abs(p1 - p2) / max(p1, p2) * 100
    return diff <= tolerance_pct


def area_close(a1: float | None, a2: float | None, tolerance_m2: float = 3.0) -> bool:
    """Výměry jsou blízké (do X m² rozdílu)."""
    if not a1 or not a2:
        return False
    return abs(a1 - a2) <= tolerance_m2


def dedup_key(
    locality: str,
    disposition: str = "",
    area_m2: float | None = None,
    price_czk: int | None = None,
) -> str:
    """Vytvoří klíč pro deduplikaci napříč portály.

    Formát: norm_adresa|dispozice|plocha_zaokrouhlena|cena_zaokrouhlena
    Plocha zaokrouhlená na 5 m², cena na 5 %.
    """
    addr = normalize_address(locality)
    disp = normalize_disposition(disposition)
    # Plocha zaokrouhlená na 5 m²
    area_bucket = f"{round((area_m2 or 0) / 5) * 5:.0f}" if area_m2 else ""
    # Cena zaokrouhlená na tisíce
    price_bucket = f"{round((price_czk or 0) / 1000)}k" if price_czk else ""
    return f"{addr}|{disp}|{area_bucket}|{price_bucket}"


def are_duplicates(a: dict, b: dict) -> bool:
    """Zjistí, jestli dva listingy jsou pravděpodobně stejná nemovitost.

    Konzervativní: vyžaduje shodu adresy + (dispozice nebo plocha) + blízkou cenu.
    """
    addr_a = normalize_address(a.get("locality", ""))
    addr_b = normalize_address(b.get("locality", ""))
    if not addr_a or not addr_b:
        return False
    # Adresa musí být stejná nebo jedna obsahuje druhou
    if addr_a != addr_b and addr_a not in addr_b and addr_b not in addr_a:
        return False

    disp_a = normalize_disposition(a.get("disposition", ""))
    disp_b = normalize_disposition(b.get("disposition", ""))
    disp_match = disp_a and disp_b and disp_a == disp_b

    area_match = area_close(a.get("area_m2"), b.get("area_m2"))

    if not (disp_match or area_match):
        return False

    # Cena musí být blízká (pokud obě známe)
    p1, p2 = a.get("price_czk"), b.get("price_czk")
    if p1 and p2 and not price_close(p1, p2):
        return False

    return True
