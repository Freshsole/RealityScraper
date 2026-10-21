"""Měsíc PRO zdarma z Google Formuláře: webhook, grant na účet podle e-mailu, expirace."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import billing, config, free_pro, users
from app.store import Store


class FreeProTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        self.store = Store(Path(tmp.name))
        self.user = users.create_user(self.store, email="a@example.cz", password="Heslo12345!", first="Jan", last="Novák")

    def _redeem(self, email):
        return free_pro.redeem(self.store, lambda _uid: self.store, email)["status"]

    def test_grants_pro_for_matching_account_once(self):
        self.assertEqual(self._redeem(" A@Example.cz "), "granted")
        state = billing.billing_state(self.store)
        self.assertEqual(state["plan"], "pro")
        self.assertTrue(state["comp"])
        self.assertEqual(self._redeem("a@example.cz"), "already")

    def test_unknown_email_is_recorded_not_granted(self):
        self.assertEqual(self._redeem("nikdo@example.cz"), "no_account")
        self.assertEqual(free_pro.list_requests(self.store)[0]["status"], "no_account")
        self.assertEqual(self._redeem("bad"), "invalid")

    def test_discord_notified_once_per_new_outcome(self):
        with mock.patch.object(config, "FREE_PRO_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x"), \
                mock.patch.object(free_pro.httpx, "post") as post:
            self._redeem("nikdo@example.cz")
            self._redeem("nikdo@example.cz")
            self.assertEqual(post.call_count, 1)
            self._redeem("a@example.cz")
            self.assertEqual(post.call_count, 2)
            self.assertIn("a@example.cz", post.call_args.kwargs["json"]["content"])

    def test_paid_account_is_not_overridden(self):
        self.store.save_billing_record({"plan": "start", "subscription_id": "sub_1", "status": "active"})
        self.assertEqual(self._redeem("a@example.cz"), "has_paid_plan")
        self.assertEqual(billing.billing_state(self.store)["plan"], "start")

    def test_grant_expires(self):
        self._redeem("a@example.cz")
        record = self.store.billing_record()
        record["comp_until"] = "2000-01-01T00:00:00+00:00"
        self.store.save_billing_record(record)
        self.assertEqual(billing.billing_state(self.store)["plan"], "free")
        self.assertTrue(billing.expire_comp_if_due(self.store))
        self.assertEqual(self.store.billing_record()["comp_plan"], "")

    def test_sheet_sync_reads_email_column_and_grants(self):
        csv_text = "\ufeffČasová značka,E-mail,Poznámka\n1.1.2026,A@Example.cz,\n2.1.2026,nikdo@example.cz,\n3.1.2026,a@example.cz,\n"
        self.assertEqual(free_pro.parse_sheet_emails(csv_text), ["a@example.cz", "nikdo@example.cz"])
        with mock.patch.object(free_pro, "_fetch_csv", return_value=csv_text):
            counts = free_pro.sync_sheet(self.store, lambda _u: self.store, "http://x")
            self.assertEqual(counts, {"granted": 1, "no_account": 1})
            again = free_pro.sync_sheet(self.store, lambda _u: self.store, "http://x")
            self.assertEqual(again, {"already": 1, "no_account": 1})
        self.assertEqual(free_pro.parse_sheet_emails("a,b\n1,2\n"), [])


if __name__ == "__main__":
    unittest.main()
