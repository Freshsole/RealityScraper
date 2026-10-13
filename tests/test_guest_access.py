"""E-mail gate tokens for the public catalog."""
import os
import tempfile
import time
import unittest

os.environ.setdefault("SCRAPE_ROLE", "web")


class GuestAccessTests(unittest.TestCase):
    def setUp(self):
        from app import guest

        self.guest = guest
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_dir, guest._dir = guest._dir, lambda: __import__("pathlib").Path(self.tmp.name)
        guest._secret_cache = None

    def tearDown(self):
        self.guest._dir = self._orig_dir
        self.guest._secret_cache = None
        self.tmp.cleanup()

    def test_email_validation(self):
        g = self.guest
        self.assertTrue(g.valid_email("jan.novak@email.cz"))
        for bad in ("", "jan", "jan@", "@x.cz", "a b@c.cz", "a@b", "a@b.c"):
            self.assertFalse(g.valid_email(bad), bad)

    def test_token_roundtrip_and_tamper(self):
        g = self.guest
        token = g.issue_token("a@b.cz")
        self.assertTrue(g.verify_token(token))
        self.assertFalse(g.verify_token(token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertFalse(g.verify_token(None))
        self.assertFalse(g.verify_token("garbage"))

    def test_token_expires(self):
        g = self.guest
        token = g.issue_token("a@b.cz", now=time.time() - g.MAX_AGE_S - 10)
        self.assertFalse(g.verify_token(token))

    def test_leads_are_stored_newest_first(self):
        g = self.guest
        g.add_lead("one@x.cz", ip="1.1.1.1")
        g.add_lead("Two@x.cz")
        rows = g.list_leads()
        self.assertEqual([r["email"] for r in rows], ["two@x.cz", "one@x.cz"])
        self.assertNotIn("1.1.1.1", str(rows))


if __name__ == "__main__":
    unittest.main()
