"""E-mail verification: tokens, one-time grandfathering of old accounts, reset proves ownership."""
import tempfile
import time
import unittest
from pathlib import Path

from app import users
from app.store import Store


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.store = Store(Path(self.tmp.name))

    def _new_user(self, email="a@example.cz"):
        return users.create_user(self.store, email=email, password="Heslo12345!", first="Jan", last="Novák")

    def test_new_accounts_start_unverified(self):
        self.assertFalse(self._new_user()["email_verified"])

    def test_token_verifies_once(self):
        user = self._new_user()
        token = users.create_email_verification(self.store, user["id"])
        verified = users.verify_email_with_token(self.store, token)
        self.assertTrue(verified["email_verified"])
        self.assertIsNone(users.verify_email_with_token(self.store, token))
        self.assertIsNone(users.verify_email_with_token(self.store, "garbage"))

    def test_new_token_replaces_the_old_one(self):
        user = self._new_user()
        first = users.create_email_verification(self.store, user["id"])
        second = users.create_email_verification(self.store, user["id"])
        self.assertIsNone(users.verify_email_with_token(self.store, first))
        self.assertIsNotNone(users.verify_email_with_token(self.store, second))

    def test_expired_token_is_rejected(self):
        user = self._new_user()
        token = users.create_email_verification(self.store, user["id"])
        with self.store.connect() as conn:
            conn.execute("UPDATE email_verifications SET expires_at = ?", (time.time() - 5,))
        self.assertIsNone(users.verify_email_with_token(self.store, token))

    def test_existing_accounts_are_grandfathered_exactly_once(self):
        old = self._new_user("old@example.cz")
        with self.store.connect() as conn:
            conn.execute("DELETE FROM meta WHERE key = 'email_verify_enforced_at'")
            conn.execute("UPDATE users SET email_verified = 0")
            users.ensure_users_schema(conn)
        self.assertTrue(users.get_user_by_id(self.store, old["id"])["email_verified"])
        newcomer = self._new_user("new@example.cz")
        with self.store.connect() as conn:
            users.ensure_users_schema(conn)
        self.assertFalse(users.get_user_by_id(self.store, newcomer["id"])["email_verified"])

    def test_mark_verified_clears_pending_tokens(self):
        user = self._new_user()
        token = users.create_email_verification(self.store, user["id"])
        users.mark_email_verified(self.store, user["id"])
        self.assertTrue(users.get_user_by_id(self.store, user["id"])["email_verified"])
        self.assertIsNone(users.verify_email_with_token(self.store, token))


if __name__ == "__main__":
    unittest.main()
