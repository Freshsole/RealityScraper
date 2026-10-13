"""Every public page must carry the same header and footer."""
import re
import unittest
from pathlib import Path

from app import site_chrome

SITE = Path(__file__).resolve().parent.parent / "web" / "site"
HEADER_RE = re.compile(r'<header class="navbar[^"]*">.*?</header>', re.S)
FOOTER_RE = re.compile(r'<footer class="footer">.*?</footer>', re.S)
AUTH_PAGES = {"prihlaseni.html", "registrace.html", "heslo.html"}


class SiteChromeTests(unittest.TestCase):
    def test_static_pages_share_header_and_footer(self):
        checked = 0
        for path in sorted(SITE.glob("*.html")):
            if path.name in AUTH_PAGES:
                continue
            text = path.read_text(encoding="utf-8")
            header, footer = HEADER_RE.search(text), FOOTER_RE.search(text)
            self.assertIsNotNone(header, f"{path.name}: missing header")
            self.assertIsNotNone(footer, f"{path.name}: missing footer")
            self.assertEqual(header.group(0), site_chrome.HEADER_HTML, f"{path.name}: header drifted")
            self.assertEqual(footer.group(0), site_chrome.FOOTER_HTML, f"{path.name}: footer drifted")
            checked += 1
        self.assertGreater(checked, 10)

    def test_server_pages_use_shared_chrome(self):
        html = site_chrome.page(title="t", description="d", canonical="https://realitify.cz/x", body="<section></section>")
        self.assertIn(site_chrome.HEADER_HTML, html)
        self.assertIn(site_chrome.FOOTER_HTML, html)
        self.assertIn("/static/site/nav.js", html)

    def test_no_dead_trh_hub_links(self):
        self.assertNotIn('href="/trh"', site_chrome.HEADER_HTML + site_chrome.FOOTER_HTML)


if __name__ == "__main__":
    unittest.main()
