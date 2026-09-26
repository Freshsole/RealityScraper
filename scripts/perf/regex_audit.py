#!/usr/bin/env python3
"""Time scrape regexes against pathological inputs. Prints ms; flag >50ms."""

from __future__ import annotations

import re
import time

from app import annonce as annonce_mod
from app import bazos as bazos_mod
from app import ceskereality as cr_mod
from app import html_listing as hl
from app import idnes as idnes_mod
from app import mmreality as mm_mod
from app import realitycz as rcz_mod
from app import remax as remax_mod
from app import ulovdomov as ulov_mod


DIGITS = "1234567890 " * 5_000  # ~55 kB
SPACES = " \t\n" * 8_000
ANGLES = "<" * 50_000
DOTALL = "x" * 80_000
QUOTES = "a" * 80_000
HTMLISH = ("<div class='x'>" + "1 2 3 4 5 " * 200 + "</div>\n") * 200


def timed(name: str, fn, *args) -> float:
    t0 = time.perf_counter()
    fn(*args)
    return (time.perf_counter() - t0) * 1000.0


def main() -> None:
    cases: list[tuple[str, object, str]] = [
        ("html_listing.COUNT_RE", hl.COUNT_RE, DIGITS),
        ("html_listing.PRICE_RE", hl.PRICE_RE, DIGITS),
        ("html_listing.AREA_RE", hl.AREA_RE, DIGITS),
        ("html_listing.strip_tags", "fn:strip", ANGLES),
        ("html_listing.clean", "fn:clean", ANGLES + SPACES),
        ("html_listing.first_img", "fn:img", 'src="' + QUOTES),
        ("html_listing.photo_findall", "fn:photos", 'src="' + QUOTES + '.txt"'),
        ("idnes.COUNT_RE", idnes_mod.COUNT_RE, DIGITS),
        ("idnes.PRICE_RE", idnes_mod.PRICE_RE, DIGITS),
        ("idnes.ARTICLE_RE", idnes_mod.ARTICLE_RE, "<article>" + DOTALL),
        ("idnes.RESULTS_RE", idnes_mod.RESULTS_RE, 'id="snippet-s-result-articles">' + DOTALL),
        ("idnes.DESC_RE", idnes_mod.DESC_RE, '<div class="b-desc">' + DOTALL),
        ("idnes.DT_RE", idnes_mod.DT_RE, "<dt>" + DOTALL + "<dt>" + DOTALL),
        ("bazos.COUNT_RE", bazos_mod.COUNT_RE, "Zobrazeno 1–20 inzerátů z " + DIGITS),
        ("bazos.PRICE_RE", bazos_mod.PRICE_RE, DIGITS),
        ("bazos.VIEWS_RE", bazos_mod.VIEWS_RE, 'class="inzeratyview">' + DIGITS),
        ("bazos.CARD_RE", bazos_mod.CARD_RE, '<div class="inzeraty inzeratyflex">' + DOTALL),
        ("bazos.TITLE_RE", bazos_mod.TITLE_RE, '<h2 class="nadpis"><a>' + DOTALL),
        ("ceskereality.CARD_RE", cr_mod.CARD_RE, '<article class="i-estate">' + DOTALL),
        ("ceskereality.CARD2_RE", cr_mod.CARD2_RE, 'class="i-estate"' + DOTALL),
        ("ceskereality.TITLE_RE", cr_mod.TITLE_RE, "<h2>" + DOTALL),
        ("ceskereality.PRICE_BOX_RE", cr_mod.PRICE_BOX_RE, "price>" + DOTALL),
        ("annonce.CARD_RE", annonce_mod.CARD_RE, '<div class="box q ext-item">' + DOTALL),
        ("annonce.TITLE_RE", annonce_mod.TITLE_RE, "<h2><a>" + DOTALL),
        ("annonce.PRICE_RE", annonce_mod.PRICE_RE, 'class="price">' + DOTALL),
        ("remax.CARD_RE", remax_mod.CARD_RE, '<div class="pl-items__item">' + DOTALL),
        ("mmreality.TITLE_RE", mm_mod.TITLE_RE, "<h2>" + DOTALL),
        ("realitycz.TITLE_RE", rcz_mod.TITLE_RE, "<h2>" + DOTALL),
        ("ulovdomov.NEXT_DATA", "fn:ulov_next", '<script id="__NEXT_DATA__">' + DOTALL),
        ("html_listing.og", "fn:og", 'property="og:description" content="' + QUOTES),
        ("remax_like_html", "fn:parse_total", HTMLISH),
    ]
    print(f"{'name':40} {'ms':>10}")
    for name, target, blob in cases:
        if target == "fn:clean":
            ms = timed(name, hl.clean, blob)
        elif target == "fn:strip":
            ms = timed(name, hl.strip_tags, blob)
        elif target == "fn:img":
            ms = timed(name, hl.first_img, blob)
        elif target == "fn:photos":
            ms = timed(name, lambda s: re.findall(
                r'(?:src|href)=["\']([^"\']+\.(?:jpg|jpeg|webp)[^"\']*)["\']', s, re.I
            ), blob)
        elif target == "fn:ulov_next":
            ms = timed(name, lambda s: re.search(
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', s, re.S
            ), blob)
        elif target == "fn:og":
            ms = timed(name, lambda s: re.search(
                r'property="og:description"\s+content="([^"]+)"', s, re.I
            ), blob)
        elif target == "fn:parse_total":
            ms = timed(name, hl.parse_total, blob)
        else:
            ms = timed(name, target.search, blob)
        flag = "  **SLOW**" if ms >= 50 else ""
        print(f"{name:40} {ms:10.1f}{flag}")


if __name__ == "__main__":
    main()
