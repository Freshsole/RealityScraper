"""Testy pro /api/watchdog endpoint (monitoring scraperů)."""
import os
import unittest

os.environ.setdefault("WATCHDOG_TOKEN", "test-token-123")


class WatchdogTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient
        import app.main as m
        return TestClient(m.app)

    def test_watchdog_requires_token(self):
        c = self._client()
        r = c.get("/api/watchdog")
        self.assertEqual(r.status_code, 403)

    def test_watchdog_rejects_bad_token(self):
        c = self._client()
        r = c.get("/api/watchdog?token=spatny")
        self.assertEqual(r.status_code, 403)

    def test_watchdog_accepts_good_token(self):
        c = self._client()
        r = c.get("/api/watchdog?token=test-token-123")
        # 200 s daty, nebo 503 když není admin store (testovací prostředí)
        self.assertIn(r.status_code, (200, 503))
        if r.status_code == 200:
            d = r.json()
            self.assertEqual(len(d["portals"]), 10)
            ids = [p["id"] for p in d["portals"]]
            self.assertIn("sreality", ids)
            self.assertIn("realitycz", ids)
            self.assertIn("ceskereality", ids)
            for p in d["portals"]:
                self.assertIn(p["status"], ("ok", "down"))
                self.assertIn("reasons", p)


if __name__ == "__main__":
    unittest.main()
