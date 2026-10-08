"""Coolify liveness: /health must answer without waiting for hub.start()."""
import os
import unittest

os.environ.setdefault("SCRAPE_ROLE", "web")


class HealthLivenessTests(unittest.TestCase):
    def test_health_ok(self):
        from fastapi.testclient import TestClient
        import app.main as m

        with TestClient(m.app) as client:
            r = client.get("/health")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {"ok": True})

if __name__ == "__main__":
    unittest.main()
