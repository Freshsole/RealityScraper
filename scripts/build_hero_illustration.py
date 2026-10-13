"""Generate the homepage hero skyline (inline SVG) and splice it into web/site/index.html.

Run: python scripts/build_hero_illustration.py
"""

from __future__ import annotations

import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = ROOT / "web" / "site" / "index.html"

FOREST, DEEP, GREEN, GREEN_D = "#163300", "#0e2200", "#9fe870", "#7fd04f"
MINT, MINT_D, PALE = "#e2f6d5", "#c9e8b3", "#eef7e8"
H = 210  # viewBox height; the card hides the bottom ~52px


def windows(x: float, w: float, top: float, dark: bool, rnd: random.Random) -> str:
    out = []
    cols = int((w - 18) // 17)
    rows = int((H - top - 26) // 21)
    if cols < 1:
        return ""
    gap = (w - cols * 17 + 9) / 2
    for r in range(rows):
        for c in range(cols):
            wx, wy = x + gap + c * 17, top + 16 + r * 21
            lit = rnd.random() < 0.16
            if dark:
                fill, op = (GREEN, 0.95) if lit else (MINT, 0.62)
            else:
                fill, op = (GREEN_D, 0.9) if lit else (FOREST, 0.2)
            out.append(f'<rect x="{wx:.1f}" y="{wy}" width="9" height="12" rx="2.5" fill="{fill}" opacity="{op}"/>')
    return "".join(out)


def tower(x, w, top, fill, shade, dark, rnd, roof=None) -> str:
    h = H - top
    parts = [
        f'<rect x="{x}" y="{top}" width="{w}" height="{h}" rx="10" fill="{fill}"/>',
        f'<path d="M{x + w * 0.78:.1f} {top} h{w * 0.22 - 10:.1f} a10 10 0 0 1 10 10 V{H} h-{w * 0.22:.1f} Z" fill="{shade}"/>',
        windows(x, w * 0.78, top, dark, rnd),
    ]
    if roof == "tank":
        parts.append(f'<rect x="{x + w * 0.3:.1f}" y="{top - 12}" width="{w * 0.28:.1f}" height="14" rx="4" fill="{shade}"/>')
    elif roof == "mast":
        parts.append(f'<rect x="{x + w / 2 - 2:.1f}" y="{top - 26}" width="4" height="30" rx="2" fill="{DEEP}"/><circle cx="{x + w / 2:.1f}" cy="{top - 27}" r="5.5" fill="{GREEN}"/>')
    elif roof == "crown":
        parts.append(f'<rect x="{x + 8}" y="{top - 8}" width="{w - 16}" height="10" rx="4" fill="{shade}"/>')
    return "".join(parts)


def tree(cx, base, r, rnd) -> str:
    return (
        f'<rect x="{cx - 3}" y="{base - r}" width="6" height="{r + 10}" rx="3" fill="{DEEP}"/>'
        f'<circle cx="{cx}" cy="{base - r}" r="{r}" fill="{GREEN}"/>'
        f'<circle cx="{cx + r * 0.35:.1f}" cy="{base - r * 0.9:.1f}" r="{r * 0.7:.1f}" fill="{GREEN_D}"/>'
        f'<circle cx="{cx - r * 0.4:.1f}" cy="{base - r * 1.25:.1f}" r="{r * 0.45:.1f}" fill="#b9f28f"/>'
    )


def build() -> str:
    rnd = random.Random(11)
    p: list[str] = []
    p.append(
        "<defs>"
        '<linearGradient id="hs-glass" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".85"/><stop offset="1" stop-color="#e2f6d5" stop-opacity=".35"/></linearGradient>'
        '<radialGradient id="hs-glow" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#9fe870" stop-opacity=".45"/><stop offset="1" stop-color="#9fe870" stop-opacity="0"/></radialGradient>'
        "</defs>"
    )
    # soft glow behind the scene
    p.append('<ellipse cx="540" cy="150" rx="470" ry="70" fill="url(#hs-glow)"/>')
    # back layer: pale silhouettes for depth
    for x, w, top in ((0, 70, 96), (78, 58, 70), (150, 86, 108), (262, 66, 82), (352, 80, 112), (700, 74, 100), (790, 90, 78), (894, 60, 104), (962, 80, 88), (1050, 40, 112)):
        p.append(f'<rect x="{x}" y="{top}" width="{w}" height="{H - top}" rx="9" fill="{MINT_D}" opacity=".55"/>')
    # main skyline
    spec = [
        (20, 78, 92, MINT, MINT_D, False, None),
        (108, 92, 52, GREEN, GREEN_D, False, "tank"),
        (210, 64, 100, FOREST, DEEP, True, None),
        (284, 86, 70, MINT, MINT_D, False, "crown"),
        (470, 100, 30, FOREST, DEEP, True, "mast"),
        (578, 74, 82, GREEN, GREEN_D, False, None),
        (660, 90, 58, MINT, MINT_D, False, "tank"),
        (762, 70, 96, FOREST, DEEP, True, None),
        (840, 100, 46, GREEN, GREEN_D, False, "crown"),
        (1000, 72, 84, MINT, MINT_D, False, None),
    ]
    for x, w, top, fill, shade, dark, roof in spec:
        p.append(tower(x, w, top, fill, shade, dark, rnd, roof))
    # house with a roof, door and window
    p.append(
        f'<path d="M376 122 L420 84 L464 122 Z" fill="{FOREST}"/>'
        f'<rect x="384" y="120" width="72" height="90" rx="7" fill="#fff" stroke="{FOREST}" stroke-width="4"/>'
        f'<rect x="408" y="144" width="22" height="66" rx="4" fill="{GREEN}"/>'
        f'<rect x="392" y="130" width="14" height="14" rx="3" fill="{MINT}"/><rect x="436" y="130" width="14" height="14" rx="3" fill="{MINT}"/>'
        f'<rect x="436" y="84" width="9" height="20" rx="2" fill="{DEEP}"/>'
    )
    for cx, base, r in ((366, 160, 17), (472, 168, 14), (930, 150, 19), (1010, 160, 15), (46, 158, 14)):
        p.append(tree(cx, base, r, rnd))
    # dashed search route from the pin to the lens
    p.append(f'<path class="hs-illu-route" d="M642 70 C 720 8, 820 8, 884 58" fill="none" stroke="{FOREST}" stroke-width="3" stroke-linecap="round" stroke-dasharray="2 9" opacity=".55"/>')
    # floating pin
    p.append(
        f'<g class="hs-illu-pin">'
        f'<path d="M610 28c-19 0-33 14-33 32 0 24 33 56 33 56s33-32 33-56c0-18-14-32-33-32z" fill="{FOREST}"/>'
        f'<circle cx="610" cy="60" r="14" fill="#fff"/><circle cx="610" cy="60" r="7" fill="{GREEN}"/></g>'
    )
    # magnifier
    p.append(
        '<g class="hs-illu-lens">'
        f'<circle cx="940" cy="78" r="42" fill="url(#hs-glass)" stroke="{FOREST}" stroke-width="10"/>'
        f'<circle cx="940" cy="78" r="31" fill="none" stroke="{GREEN}" stroke-width="2.5" opacity=".8"/>'
        f'<path d="M913 66 a30 30 0 0 1 24 -22" stroke="#fff" stroke-width="7" stroke-linecap="round" fill="none" opacity=".95"/>'
        f'<path d="M972 110 L1014 152" stroke="{FOREST}" stroke-width="15" stroke-linecap="round"/>'
        f'<path d="M984 122 L996 134" stroke="{GREEN}" stroke-width="15" stroke-linecap="round"/></g>'
    )
    # tiny sparkles
    for x, y, s in ((88, 54, 6), (352, 36, 5), (742, 44, 5), (1040, 60, 6)):
        p.append(f'<path d="M{x} {y - s} L{x + s / 3:.1f} {y - s / 3:.1f} L{x + s} {y} L{x + s / 3:.1f} {y + s / 3:.1f} L{x} {y + s} L{x - s / 3:.1f} {y + s / 3:.1f} L{x - s} {y} L{x - s / 3:.1f} {y - s / 3:.1f} Z" fill="{GREEN}" class="hs-illu-spark"/>')
    return (
        f'<svg class="hs-illu" viewBox="0 0 1080 {H}" preserveAspectRatio="xMidYMax meet" role="img" '
        'aria-label="Ilustrace města s lupou a špendlíkem na mapě" xmlns="http://www.w3.org/2000/svg">' + "".join(p) + "</svg>"
    )


def main() -> None:
    html = HTML.read_text(encoding="utf-8")
    new, n = re.subn(r'<svg class="hs-illu".*?</svg>', lambda _m: build(), html, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("hero svg not found in index.html")
    HTML.write_text(new, encoding="utf-8")
    print("hero illustration updated")


if __name__ == "__main__":
    main()
