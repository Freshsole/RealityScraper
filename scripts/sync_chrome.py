"""Rewrite the header and footer of every static page in web/site/ from app.site_chrome.

Run after editing HEADER_HTML / FOOTER_HTML:  python scripts/sync_chrome.py
tests/test_site_chrome.py fails when a page drifts from the shared markup.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import site_chrome  # noqa: E402

HEADER_RE = re.compile(r'<header class="navbar[^"]*">.*?</header>', re.S)
FOOTER_RE = re.compile(r'<footer class="footer">.*?</footer>', re.S)


def sync(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    new = text
    if HEADER_RE.search(new):
        new = HEADER_RE.sub(lambda _m: site_chrome.HEADER_HTML, new, count=1)
    if FOOTER_RE.search(new):
        new = FOOTER_RE.sub(lambda _m: site_chrome.FOOTER_HTML, new, count=1)
    if new != text:
        path.write_text(new, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = [p.name for p in sorted((ROOT / "web" / "site").glob("*.html")) if sync(p)]
    print("synced:", ", ".join(changed) or "nothing to do")


if __name__ == "__main__":
    main()
