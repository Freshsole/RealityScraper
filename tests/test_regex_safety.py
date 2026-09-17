"""Hard-timeout guards against ReDoS-class scrape regexes.

CPython `re` holds the GIL for the whole match, so a hung search cannot be
interrupted in-thread (asyncio.timeout / to_thread do not help). Each case
runs in a child process that is killed after REGEX_TIMEOUT_S.
"""

from __future__ import annotations

import multiprocessing
import re
import time
import unittest
from pathlib import Path

from app import annonce as annonce_mod
from app import bazos as bazos_mod
from app import bezrealitky as bez_mod
from app import ceskereality as cr_mod
from app import html_listing as hl
from app import idnes as idnes_mod
from app import mmreality as mm_mod
from app import realitycz as rcz_mod
from app import remax as remax_mod
from app import sreality as sre_mod
from app import ulovdomov as ulov_mod

ROOT = Path(__file__).resolve().parents[1]
REMAX_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "remax_list.html"
REGEX_TIMEOUT_S = 3.0
DIGITS = ("1234567890 " * 5_000)  # ~55 kB
ANGLES = "<" * 50_000
SPACES = " \t\n" * 8_000
DOTALL = "x" * 80_000
QUOTES = "a" * 80_000

SCRAPE_MODULES = (
    hl,
    sre_mod,
    idnes_mod,
    bazos_mod,
    bez_mod,
    cr_mod,
    annonce_mod,
    remax_mod,
    mm_mod,
    ulov_mod,
    rcz_mod,
)


def _mp_context():
    # fork() after unittest has background threads SIGSEGVs on macOS/Python 3.14.
    return multiprocessing.get_context("spawn")


def _search_worker(pattern: str, flags: int, blob: str) -> None:
    re.compile(pattern, flags).search(blob)


def _findall_worker(pattern: str, flags: int, blob: str) -> None:
    re.compile(pattern, flags).findall(blob)


def _fn_worker(name: str, blob: str) -> None:
    if name == "strip_tags":
        hl.strip_tags(blob)
    elif name == "clean":
        hl.clean(blob)
    elif name == "parse_total":
        hl.parse_total(blob)
    elif name == "parse_price":
        hl.parse_price(blob)
    elif name == "first_img":
        hl.first_img(blob)
    elif name == "idnes_parse_total":
        idnes_mod.IdnesClient("https://reality.idnes.cz/s/pronajem/")._parse_total(blob)
    elif name == "bazos_parse_total":
        bazos_mod.BazosClient("https://reality.bazos.cz/pronajmu/byt/")._parse_total(blob)
    else:
        raise ValueError(name)


def _run_in_process(target, args, timeout: float = REGEX_TIMEOUT_S) -> float:
    ctx = _mp_context()
    started = time.perf_counter()
    proc = ctx.Process(target=target, args=args)
    proc.start()
    proc.join(timeout)
    elapsed = time.perf_counter() - started
    if proc.is_alive():
        proc.terminate()
        proc.join(1.0)
        if proc.is_alive():
            proc.kill()
            proc.join(1.0)
        raise AssertionError(f"{target.__name__}{args[:1]} exceeded {timeout:.1f}s hard timeout")
    if proc.exitcode not in (0, None):
        raise AssertionError(f"{target.__name__} exited {proc.exitcode}")
    return elapsed


def _evil_for(pattern: re.Pattern) -> str:
    src = pattern.pattern
    flags = pattern.flags
    if r"[\d\s]" in src or r"\d" in src:
        blob = DIGITS
    elif r"[^>]" in src:
        blob = ANGLES
    elif r"[^\"]" in src or r"[^']" in src or r'[^"]' in src:
        blob = 'x="' + QUOTES
    else:
        blob = DOTALL
    if flags & re.S or r"[\s\S]" in src or ".*?" in src:
        blob = blob + DOTALL
    if r"\s" in src:
        blob = blob + SPACES
    return blob


def iter_scrape_regexes() -> list[tuple[str, re.Pattern]]:
    found: list[tuple[str, re.Pattern]] = []
    for mod in SCRAPE_MODULES:
        for name, value in vars(mod).items():
            if isinstance(value, re.Pattern):
                found.append((f"{mod.__name__}.{name}", value))
    return found


class RegexSafetyTests(unittest.TestCase):
    def test_inventory_is_not_empty(self):
        names = [name for name, _ in iter_scrape_regexes()]
        self.assertGreater(len(names), 20)
        self.assertTrue(any(name.endswith("COUNT_RE") for name in names))
        self.assertTrue(any(name.endswith("PRICE_RE") for name in names))

    def test_every_scrape_regex_finishes_on_evil_input(self):
        failures: list[str] = []
        for name, pattern in iter_scrape_regexes():
            blob = _evil_for(pattern)
            try:
                _run_in_process(_search_worker, (pattern.pattern, pattern.flags, blob))
                _run_in_process(_findall_worker, (pattern.pattern, pattern.flags, blob))
            except AssertionError as exc:
                failures.append(f"{name}: {exc}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_helpers_finish_on_evil_input(self):
        cases = [
            ("strip_tags", ANGLES),
            ("clean", ANGLES + SPACES),
            ("parse_total", DIGITS),
            ("parse_price", DIGITS + " Kč"),
            ("first_img", 'src="' + QUOTES),
            ("idnes_parse_total", DIGITS),
            ("bazos_parse_total", "Zobrazeno 1-20 inzerátů z " + DIGITS),
        ]
        for name, blob in cases:
            elapsed = _run_in_process(_fn_worker, (name, blob))
            self.assertLess(elapsed, REGEX_TIMEOUT_S, name)

    def test_parse_total_remax_fixture_is_fast(self):
        self.assertTrue(REMAX_FIXTURE.is_file(), f"missing {REMAX_FIXTURE}")
        html = REMAX_FIXTURE.read_text(encoding="utf-8", errors="replace")
        self.assertGreater(len(html), 100_000)
        started = time.perf_counter()
        total = hl.parse_total(html)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.05, f"parse_total({len(html)} B) took {elapsed:.3f}s")
        self.assertIsInstance(total, int)

    def test_strip_tags_linear_on_angle_bomb(self):
        started = time.perf_counter()
        hl.strip_tags(ANGLES)
        self.assertLess(time.perf_counter() - started, 0.05)

    def test_price_re_on_digit_bomb(self):
        started = time.perf_counter()
        hl.PRICE_RE.search(DIGITS)
        idnes_mod.PRICE_RE.search(DIGITS)
        bazos_mod.PRICE_RE.search(DIGITS)
        self.assertLess(time.perf_counter() - started, 0.05)

    def test_old_unbounded_count_pattern_is_the_class_we_ban(self):
        """Canary: the Wave 3 Remax pattern must not be reintroduced as COUNT_RE."""
        unsafe = r"([\d\s]+)\s+(?:inzerát|nemovitost|nabídek|výsled)"
        for name, pattern in iter_scrape_regexes():
            if name.endswith("COUNT_RE") or name.endswith("PRICE_RE"):
                self.assertNotEqual(pattern.pattern, unsafe, name)
                self.assertNotIn(r"([\d\s]+)", pattern.pattern, name)


if __name__ == "__main__":
    unittest.main()
