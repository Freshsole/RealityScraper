"""Pre-warm the translation cache by loading pages in headless Chrome in every language.

Needs the app running (make dev) with LLM_API_KEY/LLM_MODEL set. Chrome runs the real
web/site/i18n.js, so exactly the strings real visitors would request get translated and cached.

    python scripts/i18n_warm.py [--base http://127.0.0.1:8080] [--trh-sample 24]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
LANGS = ["sk", "uk", "en", "ru", "vi", "de"]
EXTRA = ["/", "/hledat", "/nabidka", "/prihlaseni", "/registrace", "/heslo", "/overeni", "/nonexistent-404"]


def pages(base: str, trh_sample: int) -> list[str]:
    xml = urllib.request.urlopen(base + "/sitemap.xml", timeout=30).read().decode()
    paths = [re.sub(r"^https?://[^/]+", "", u) or "/" for u in re.findall(r"<loc>([^<]+)</loc>", xml)]
    trh = [p for p in paths if p.startswith("/trh")]
    rest = [p for p in paths if not p.startswith("/trh")]
    # market pages share templates (numbers become placeholders), a spread of them is enough
    step = max(1, len(trh) // max(1, trh_sample))
    return list(dict.fromkeys(EXTRA + rest + trh[::step][:trh_sample]))


def load(args: tuple[str, str, str]) -> None:
    base, lang, path = args
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={profile}", f"--lang={lang}",
             f"--accept-lang={lang}", "--virtual-time-budget=25000", "--dump-dom", base + path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False,
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--trh-sample", type=int, default=24)
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if not Path(CHROME).exists():
        sys.exit("Chrome not found")
    todo = pages(a.base, a.trh_sample)
    jobs = [(a.base, lang, p) for p in todo for lang in LANGS]
    print(f"{len(todo)} pages x {len(LANGS)} languages = {len(jobs)} loads")
    with ThreadPoolExecutor(a.workers) as pool:
        for i, _ in enumerate(pool.map(load, jobs), 1):
            if i % 20 == 0:
                print(f"{i}/{len(jobs)}", flush=True)
    print("done")


if __name__ == "__main__":
    main()
