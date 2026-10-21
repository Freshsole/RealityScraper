"""Měsíc PRO zdarma z Google Formuláře: čtení tabulky, grant podle tarifu, lead, Discord jen jednou, expirace."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import billing, config, free_pro, users
from app.store import Store


class FreeProTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "g.sqlite")
        self.user = users.create_user(self.store, email="a@example.cz", password="Heslo12345!", first="Jan", last="Novák")
        self.leads: list[str] = []
        patches = [
            mock.patch.object(free_pro.guest_access, "list_leads", lambda limit=1000: [{"email": e} for e in self.leads]),
            mock.patch.object(free_pro.guest_access, "add_lead", lambda email, **kw: self.leads.append(email)),
            mock.patch.object(config, "FREE_PRO_DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/x"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.post = mock.patch.object(free_pro.httpx, "post").start()
        self.addCleanup(mock.patch.stopall)

    def _redeem(self, email):
        return free_pro.redeem(self.store, lambda _uid: self.store, email)["status"]

    def test_free_account_gets_pro_for_a_month_once(self):
        self.assertEqual(self._redeem(" A@Example.cz "), "granted")
        state = billing.billing_state(self.store)
        self.assertEqual((state["plan"], state["comp"]), ("pro", True))
        self.assertEqual(self._redeem("a@example.cz"), "already")
        self.assertEqual(self.post.call_count, 1)

    def test_admin_grant_of_free_counts_as_free(self):
        """Regression: "Zdarma" set by the admin is a comp_plan='free', which used to look like a paid plan."""
        self.store.save_billing_record({"plan": "free", "comp_plan": "free", "status": "active"})
        self.assertEqual(self._redeem("a@example.cz"), "granted")

    def test_start_gets_pro_on_top_and_keeps_start_underneath(self):
        self.store.save_billing_record({"plan": "start", "subscription_id": "sub_1", "status": "active"})
        self.assertEqual(self._redeem("a@example.cz"), "granted")
        self.assertEqual(billing.billing_state(self.store)["plan"], "pro")
        record = self.store.billing_record()
        self.assertEqual((record["plan"], record["subscription_id"]), ("start", "sub_1"))  # Stripe side untouched
        record["comp_until"] = "2000-01-01T00:00:00+00:00"
        self.store.save_billing_record(record)
        self.assertTrue(billing.expire_comp_if_due(self.store))
        self.assertEqual(billing.billing_state(self.store)["plan"], "start")

    def test_paid_pro_gets_next_charge_pushed_back(self):
        self.store.save_billing_record({"plan": "pro", "subscription_id": "sub_9", "status": "active"})
        sub = {"status": "active", "cancel_at_period_end": False, "schedule": None, "current_period_end": 2_000_000_000, "items": {"data": []}}
        with mock.patch.object(billing, "_configure"), mock.patch.object(billing.stripe.Subscription, "retrieve", return_value=sub), \
                mock.patch.object(billing.stripe.Subscription, "modify") as modify:
            self.assertEqual(self._redeem("a@example.cz"), "extended")
        kwargs = modify.call_args.kwargs
        self.assertEqual(kwargs["trial_end"], 2_000_000_000 + 30 * 86400)
        self.assertEqual(kwargs["proration_behavior"], "none")
        record = self.store.billing_record()
        self.assertEqual(record["plan"], "pro")
        self.assertFalse(record.get("comp_plan"))
        self.assertTrue(record["payment_shift_until"])

    def test_stripe_failure_is_reported_and_not_retried_every_poll(self):
        self.store.save_billing_record({"plan": "pro", "subscription_id": "sub_9", "status": "active"})
        with mock.patch.object(billing, "_configure"), \
                mock.patch.object(billing.stripe.Subscription, "retrieve", side_effect=RuntimeError("boom")) as retrieve:
            self.assertEqual(self._redeem("a@example.cz"), "stripe_error")
            self.assertEqual(self._redeem("a@example.cz"), "stripe_error")
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(self.post.call_count, 1)

    def test_canceling_pro_is_left_alone(self):
        self.store.save_billing_record({"plan": "pro", "subscription_id": "sub_9", "status": "active"})
        sub = {"status": "active", "cancel_at_period_end": True, "current_period_end": 2_000_000_000, "items": {"data": []}}
        with mock.patch.object(billing, "_configure"), mock.patch.object(billing.stripe.Subscription, "retrieve", return_value=sub), \
                mock.patch.object(billing.stripe.Subscription, "modify") as modify:
            self.assertEqual(self._redeem("a@example.cz"), "stripe_error")
        modify.assert_not_called()
        self.assertNotIn("payment_shift_until", self.store.billing_record())

    def test_unknown_email_becomes_a_lead_once_and_discord_speaks_once(self):
        self.assertEqual(self._redeem("nikdo@example.cz"), "no_account")
        self.assertEqual(self._redeem("nikdo@example.cz"), "no_account")
        self.assertEqual(self.leads, ["nikdo@example.cz"])
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(self._redeem("bad"), "invalid")

    def test_lead_who_registers_later_gets_the_month_and_one_more_message(self):
        self._redeem("later@example.cz")
        users.create_user(self.store, email="later@example.cz", password="Heslo12345!", first="L", last="X")
        self.assertEqual(self._redeem("later@example.cz"), "granted")
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(len(self.leads), 1)

    def test_status_change_notifies_but_repeats_do_not(self):
        self.store.save_billing_record({"plan": "individual", "status": "active"})
        self.assertEqual(self._redeem("a@example.cz"), "has_higher_plan")
        self._redeem("a@example.cz")
        self._redeem("a@example.cz")
        self.assertEqual(self.post.call_count, 1)  # the three-times-in-Discord bug
        self.store.save_billing_record({"plan": "free", "status": "active"})
        self.assertEqual(self._redeem("a@example.cz"), "granted")
        self.assertEqual(self.post.call_count, 2)

    def test_grant_expires(self):
        self._redeem("a@example.cz")
        record = self.store.billing_record()
        record["comp_until"] = "2000-01-01T00:00:00+00:00"
        self.store.save_billing_record(record)
        self.assertEqual(billing.billing_state(self.store)["plan"], "free")
        self.assertTrue(billing.expire_comp_if_due(self.store))
        self.assertEqual(self.store.billing_record()["comp_plan"], "")

    def test_sheet_sync_reads_email_column_and_grants(self):
        csv_text = "﻿Časová značka,E-mail,Poznámka\n1.1.2026,A@Example.cz,\n2.1.2026,nikdo@example.cz,\n3.1.2026,a@example.cz,\n"
        self.assertEqual(free_pro.parse_sheet_emails(csv_text), ["a@example.cz", "nikdo@example.cz"])
        with mock.patch.object(free_pro, "_fetch_csv", return_value=csv_text):
            self.assertEqual(free_pro.sync_sheet(self.store, lambda _u: self.store, "http://x"), {"granted": 1, "no_account": 1})
            self.assertEqual(free_pro.sync_sheet(self.store, lambda _u: self.store, "http://x"), {"already": 1, "no_account": 1})
        self.assertEqual(free_pro.parse_sheet_emails("a,b\n1,2\n"), [])


if __name__ == "__main__":
    unittest.main()
