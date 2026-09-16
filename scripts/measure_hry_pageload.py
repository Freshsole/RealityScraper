#!/usr/bin/env python3
"""Chromium LCP/FCP + first-interactive-guess timings for /hry* pages."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

PATHS = ("/hry", "/hry/vyssi-nizsi", "/hry/najem")

READY = {
    "/hry": "a.pill[href='/hry/vyssi-nizsi']",
    "/hry/vyssi-nizsi": "button.flat-row.pickable",
    "/hry/najem": "#rent-guess",
}

INIT = """
window.__rfPaint = { lcp: null, fcp: null };
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (e.name === "first-contentful-paint") window.__rfPaint.fcp = e.startTime;
  }
}).observe({ type: "paint", buffered: true });
new PerformanceObserver((list) => {
  const e = list.getEntries().at(-1);
  if (!e) return;
  window.__rfPaint.lcp = {
    startTime: e.startTime,
    size: e.size,
    tag: e.element ? e.element.tagName : null,
    url: e.url || "",
  };
}).observe({ type: "largest-contentful-paint", buffered: true });
"""


def _metrics(page, path: str, url: str) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=20_000)
    page.wait_for_selector(READY[path], timeout=15_000)
    guess_ms = page.evaluate("() => performance.now()")
    page.wait_for_timeout(250)
    row = page.evaluate(
        """() => {
          const nav = performance.getEntriesByType("navigation")[0];
          const paints = window.__rfPaint || {};
          const resources = performance.getEntriesByType("resource").map((e) => ({
            name: e.name,
            type: e.initiatorType,
            transferSize: e.transferSize || 0,
            encodedBodySize: e.encodedBodySize || 0,
            duration: e.duration,
          }));
          return {
            ttfb: nav ? nav.responseStart : null,
            dcl: nav ? nav.domContentLoadedEventEnd : null,
            load: nav ? nav.loadEventEnd : null,
            fcp: paints.fcp ?? null,
            lcp: paints.lcp ? paints.lcp.startTime : null,
            lcp_tag: paints.lcp ? paints.lcp.tag : null,
            lcp_url: paints.lcp ? paints.lcp.url : null,
            lcp_size: paints.lcp ? paints.lcp.size : null,
            interactive_guess_ms: performance.now(),
            transfer_bytes: resources.reduce((n, r) => n + (r.transferSize || 0), 0),
            encoded_bytes: resources.reduce((n, r) => n + (r.encodedBodySize || 0), 0),
            resource_count: resources.length,
            google_fonts: resources.filter((r) => /fonts\\.(googleapis|gstatic)\\.com/.test(r.name)).length,
            hosts: [...new Set(resources.map((r) => {
              try { return new URL(r.name).host; } catch { return ""; }
            }).filter(Boolean))],
          };
        }"""
    )
    row["interactive_guess_ms"] = guess_ms
    return row


def measure(base: str, repeats: int, warm: bool) -> dict:
    out: dict[str, list[dict]] = {path: [] for path in PATHS}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="cs-CZ",
        )
        context.add_init_script(INIT)
        page = context.new_page()
        if warm:
            for path in PATHS:
                page.goto(urljoin(base, path), wait_until="load", timeout=20_000)
                page.wait_for_selector(READY[path], timeout=15_000)
        for _ in range(repeats):
            for path in PATHS:
                out[path].append(_metrics(page, path, urljoin(base, path)))
        browser.close()
    return out


def _stat(values: list[float | None]) -> dict:
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {"n": 0}
    clean.sort()
    return {
        "n": len(clean),
        "p50": clean[len(clean) // 2],
        "mean": statistics.fmean(clean),
        "min": clean[0],
        "max": clean[-1],
    }


def summarize(raw: dict[str, list[dict]]) -> dict:
    summary = {}
    for path, rows in raw.items():
        keys = ("ttfb", "fcp", "lcp", "dcl", "load", "interactive_guess_ms", "encoded_bytes")
        summary[path] = {key: _stat([row.get(key) for row in rows]) for key in keys}
        summary[path]["google_fonts"] = max((row.get("google_fonts") or 0) for row in rows)
        summary[path]["lcp_tag"] = next((row.get("lcp_tag") for row in rows if row.get("lcp_tag")), None)
        hosts: set[str] = set()
        for row in rows:
            hosts.update(row.get("hosts") or [])
        summary[path]["hosts"] = sorted(hosts)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8091")
    parser.add_argument("--repeats", type=int, default=6)
    parser.add_argument("--label", default="run")
    parser.add_argument("--cold", action="store_true")
    args = parser.parse_args()
    raw = measure(args.base, args.repeats, warm=not args.cold)
    summary = summarize(raw)
    payload = {"label": args.label, "base": args.base, "summary": summary}
    print(json.dumps(payload, indent=2))
    print("\n=== %s (warm=%s, n=%s) ===" % (args.label, not args.cold, args.repeats), file=sys.stderr)
    for path, stats in summary.items():
        print(
            f"{path:20} FCP p50={stats['fcp'].get('p50', 0):6.0f}ms  "
            f"LCP p50={stats['lcp'].get('p50', 0):6.0f}ms  "
            f"guess p50={stats['interactive_guess_ms'].get('p50', 0):6.0f}ms  "
            f"encoded p50={stats['encoded_bytes'].get('p50', 0):7.0f}  "
            f"gfonts={stats['google_fonts']}  lcp={stats['lcp_tag']}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
