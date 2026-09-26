from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import account as user_account
from app.store import Store


class PasswordResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "monitor.sqlite")
        user_account.register(self.store, "Jiri Kolb", "jajirka.kolb@gmail.com", "stare-heslo-123")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reset_flow(self) -> None:
        self.assertIsNone(user_account.create_password_reset(self.store, "other@example.com"))
        token = user_account.create_password_reset(self.store, "jajirka.kolb@gmail.com")
        self.assertTrue(token)
        self.assertTrue(user_account.password_reset_ok(self.store, token or ""))
        self.assertFalse(user_account.password_reset_ok(self.store, "wrong-token"))

        user, session = user_account.reset_password_with_token(self.store, token or "", "nove-heslo-456")
        self.assertEqual(user["email"], "jajirka.kolb@gmail.com")
        self.assertTrue(session)
        self.assertFalse(user_account.password_reset_ok(self.store, token or ""))

        logged, _ = user_account.login(self.store, "jajirka.kolb@gmail.com", "nove-heslo-456")
        self.assertEqual(logged["email"], "jajirka.kolb@gmail.com")
        with self.assertRaises(ValueError):
            user_account.login(self.store, "jajirka.kolb@gmail.com", "stare-heslo-123")

    def test_expired_token(self) -> None:
        token = user_account.create_password_reset(self.store, "jajirka.kolb@gmail.com")
        with mock.patch("app.account.time.time", return_value=10**12):
            self.assertFalse(user_account.password_reset_ok(self.store, token or ""))


if __name__ == "__main__":
    unittest.main()
