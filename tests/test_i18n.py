"""Auto-translation: cache, validation, endpoint, and script injection into HTML pages."""
import asyncio
import os
import tempfile
import unittest

os.environ["I18N_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="realitify-i18n-"), "i18n.sqlite")

from app import i18n  # noqa: E402


class TranslateTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._orig = i18n._translate_batch

        async def fake(lang, sources):
            self.calls.append((lang, list(sources)))
            return {s: f"[{lang}] {s}" for s in sources}

        i18n._translate_batch = fake

    def tearDown(self):
        i18n._translate_batch = self._orig

    def test_translates_once_then_serves_from_cache(self):
        first = asyncio.run(i18n.translate("uk", ["Přehled A", "Nalezeno {0} nabídek"]))
        self.assertEqual(first[i18n.key_for("Přehled A")], "[uk] Přehled A")
        again = asyncio.run(i18n.translate("uk", ["Přehled A"]))
        self.assertEqual(again[i18n.key_for("Přehled A")], "[uk] Přehled A")
        self.assertEqual(len(self.calls), 1)

    def test_czech_and_unknown_languages_are_noops(self):
        self.assertEqual(asyncio.run(i18n.translate("cs", ["x"])), {})
        self.assertEqual(asyncio.run(i18n.translate("pl", ["x"])), {})
        self.assertEqual(self.calls, [])

    def test_placeholders_must_survive(self):
        self.assertTrue(i18n._valid("Nalezeno {0} z {1}", "Found {1} of {0}"))
        self.assertFalse(i18n._valid("Nalezeno {0}", "Found"))
        self.assertFalse(i18n._valid("a", ""))

    def test_all_requested_languages_supported(self):
        self.assertEqual(set(i18n.LANGUAGES), {"cs", "sk", "uk", "en", "ru", "vi", "de"})


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        from app.main import app

        cls.client = TestClient(app)

    def test_script_injected_into_public_pages_but_not_admin(self):
        page = self.client.get("/kontakt")
        self.assertEqual(page.status_code, 200)
        self.assertIn("/static/site/i18n.js", page.text)
        self.assertEqual(page.text.count("/static/site/i18n.js"), 1)
        self.assertEqual(int(page.headers["content-length"]), len(page.content))
        admin = self.client.get("/admin")
        self.assertNotIn("/static/site/i18n.js", admin.text)

    def test_endpoint_validates_input(self):
        self.assertEqual(self.client.post("/api/i18n/translate", json={"lang": "pl", "strings": ["a"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/i18n/translate", json={"lang": "en", "strings": "a"}).status_code, 400)
        big = self.client.post("/api/i18n/translate", json={"lang": "en", "strings": ["x"] * 500})
        self.assertEqual(big.status_code, 413)


if __name__ == "__main__":
    unittest.main()
