import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telebt.core.validation import identifier, interval, render_template, ValidationError
from telebt.data.storage import JsonStore, StorageError
from telebt.data.services import MockServices, PermissionError, add_month
from telebt.core.schedule import due_dates, resume_unattempted
from telebt.bot.ui import BotUI


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = MockServices(JsonStore(Path(self.tmp.name)), dev_mode=True, dev_ids={1})
        self.api.users.language(1, "en")

    def test_identifiers_and_intervals(self):
        self.assertEqual(identifier("gaid", " ABCDEF12-1234-1234-ABCD-123456789ABC "), "abcdef12-1234-1234-abcd-123456789abc")
        self.assertEqual(identifier("uid", "1234567890123-1234567"), "1234567890123-1234567")
        for value in ("0m", "-1h", "2w", "999999999d"):
            with self.assertRaises(ValidationError):
                interval(value)
        self.assertEqual(interval(" 2h "), timedelta(hours=2))

    def test_template_values_are_text(self):
        self.assertEqual(render_template("level_{x}_{name}", ["30", "abc"]), "level_30_abc")
        with self.assertRaises(ValidationError):
            render_template("level_{x}", [" "])
        with self.assertRaises(ValidationError):
            render_template("level_{x!r}", ["abc"])

    def test_unicode_search_normalization(self):
        names = self.api.catalog.search("  SAMPLE   QUEST  ", "Android")
        self.assertEqual([x["id"] for x in names], ["g1"])

    def test_calendar_month_grace_clamps_month_end(self):
        self.assertEqual(add_month(datetime(2026, 1, 31, tzinfo=timezone.utc)), datetime(2026, 2, 28, tzinfo=timezone.utc))

    def test_storage_missing_corrupt_and_persistence(self):
        store = JsonStore(Path(self.tmp.name))
        self.assertEqual(store.read("devices"), [])
        store.write("devices", [{"id": "sample"}])
        self.assertEqual(JsonStore(Path(self.tmp.name)).read("devices"), [{"id": "sample"}])
        (Path(self.tmp.name) / "devices.json").write_text("{bad", encoding="utf-8")
        with self.assertRaises(StorageError):
            store.read("devices")

    def test_corrupt_users_file_shows_safe_ui_error(self):
        (Path(self.tmp.name) / "users.json").write_text("{bad", encoding="utf-8")
        ui = BotUI(self.api)
        screen = ui.start(1)
        self.assertEqual(screen.kind, "error")
        self.assertNotIn("{bad", screen.text)

    def test_corrupt_operational_file_uses_selected_language(self):
        (Path(self.tmp.name) / "devices.json").write_text("{bad", encoding="utf-8")
        ui = BotUI(self.api)
        screen = ui.click(1, "open:devices")
        self.assertEqual(screen.kind, "error")
        self.assertEqual(screen.text, "Cannot read demo data. Inspect the local JSON file.")

    def test_service_permissions_and_cascade_preserve_finance(self):
        with self.assertRaises(PermissionError):
            self.api.devices.add(1, "Android", "Phone", "12345678-1234-1234-1234-123456789abc", None)
        self.api.subscriptions.dev_state(1, "active")
        device = self.api.devices.add(1, "Android", "Phone", "12345678-1234-1234-1234-123456789abc", None)
        game = self.api.catalog.search("sample", "Android", "AppsFlyer")[0]
        linked = self.api.devices.link_game(1, device["id"], game["id"], "1234567890123-1234567")
        plan = self.api.plans.add(1, device["id"], linked["id"], "uniform", "30m", [{"template": "tutorial_complete", "values": []}])
        self.api.finance.dev_balance(1, 17)
        self.api.subscriptions.dev_expire(1, datetime.now(timezone.utc) - timedelta(days=32))
        self.api.subscriptions.dev_advance(1, datetime.now(timezone.utc))
        self.assertEqual(self.api.devices.list(1), [])
        self.assertEqual(self.api.plans.list(1), [])
        self.assertEqual(self.api.finance.balance(1), 17)
        self.assertEqual(self.api.finance.ledger(1)[-1]["amount"], 17)

    def test_developer_service_methods_are_restricted(self):
        locked = MockServices(JsonStore(Path(self.tmp.name)), dev_mode=False, dev_ids={1})
        with self.assertRaises(PermissionError):
            locked.subscriptions.dev_state(1, "active")
        guarded = MockServices(JsonStore(Path(self.tmp.name)), dev_mode=True, dev_ids={2})
        with self.assertRaises(PermissionError):
            guarded.subscriptions.dev_state(1, "active")

    def test_developer_new_state_clears_operational_queue(self):
        self.api.subscriptions.dev_state(1, "active")
        self.api.subscriptions.dev_renew(1, "weekly")
        self.api.subscriptions.dev_state(1, "new")
        self.assertEqual(self.api.subscriptions.status(1)["queue"], [])

    def test_quota_window_rollover_rebases_only_pending_operations(self):
        self.api.subscriptions.dev_state(1, "active")
        device = self.api.devices.add(1, "Android", "Phone", "12345678-1234-1234-1234-123456789abc", None)
        linked = self.api.devices.link_game(1, device["id"], "g1", "1234567890123-1234567")
        plan = self.api.plans.add(1, device["id"], linked["id"], "uniform", "30m", [{"template": "tutorial_complete", "values": []}, {"template": "tutorial_complete", "values": []}])
        self.api.plans.dev_fail_first(1, plan["id"])
        self.api.subscriptions.dev_quota(1, True)
        start = datetime.fromisoformat(self.api.subscriptions.status(1)["window_start"])
        self.api.subscriptions.dev_advance(1, start + timedelta(days=1, hours=1))
        current = self.api.plans.get(1, plan["id"])
        self.assertEqual(current["operations"][0]["status"], "failed")
        expected = start + timedelta(days=1, minutes=30)
        self.assertEqual(current["operations"][1]["due_at"], expected.isoformat())

    def test_renewal_during_grace_keeps_attempted_history(self):
        self.api.subscriptions.dev_state(1, "active")
        device = self.api.devices.add(1, "Android", "Phone", "12345678-1234-1234-1234-123456789abc", None)
        linked = self.api.devices.link_game(1, device["id"], "g1", "1234567890123-1234567")
        ops = [{"template": "tutorial_complete", "values": [], "interval": "2h"}, {"template": "tutorial_complete", "values": [], "interval": "30m"}]
        plan = self.api.plans.add(1, device["id"], linked["id"], "multiple", None, ops)
        self.api.plans.dev_fail_first(1, plan["id"])
        expired_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        renewed_at = expired_at + timedelta(days=2)
        self.api.subscriptions.dev_expire(1, expired_at)
        self.api.subscriptions.dev_renew(1, "weekly", renewed_at)
        current = self.api.plans.get(1, plan["id"])
        self.assertEqual(current["operations"][0]["status"], "failed")
        self.assertEqual(sorted(x["order"] for x in current["operations"]), [1, 2])
        remaining = next(x for x in current["operations"] if x["status"] == "pending")
        self.assertEqual(remaining["interval"], "2h")
        self.assertEqual(remaining["due_at"], (renewed_at + timedelta(hours=2)).isoformat())

    def test_schedule_modes_and_resume(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ops = [{"id": "a", "interval": "2h", "status": "pending"}, {"id": "b", "interval": "30m", "status": "pending"}]
        actual = due_dates("multiple", ops, start)
        self.assertEqual([x["id"] for x in actual], ["b", "a"])
        uniform = due_dates("uniform", ops, start, "30m")
        self.assertEqual(uniform[1]["due_at"], (start + timedelta(hours=1)).isoformat())
        ops[0]["status"] = "failed"
        resumed = resume_unattempted("multiple", ops, start + timedelta(days=1))
        self.assertEqual(resumed[0]["status"], "failed")
        self.assertEqual(resumed[1]["due_at"], (start + timedelta(days=1, minutes=30)).isoformat())

    def test_queued_subscription_activates_at_prior_expiry(self):
        self.api.subscriptions.dev_state(1, "active")
        start = datetime.fromisoformat(self.api.subscriptions.status(1)["expires_at"])
        self.api.subscriptions.dev_renew(1, "weekly")
        self.api.subscriptions.dev_renew(1, "daily")
        self.api.subscriptions.dev_advance(1, start + timedelta(hours=1))
        status = self.api.subscriptions.status(1)
        self.assertEqual(status["tier"], "weekly")
        self.assertEqual(status["window_start"], start.isoformat())
        self.assertEqual(status["expires_at"], (start + timedelta(days=7)).isoformat())
        self.assertEqual([x["tier"] for x in status["queue"]], ["daily"])

    def test_subscription_prices_and_limits_are_injected(self):
        tiers = {"daily": {"price": 9, "days": 3, "limit": 11}, "weekly": {"price": 7, "days": 7, "limit": 250}, "monthly": {"price": 25, "days": 30, "limit": 300}}
        services = MockServices(JsonStore(Path(self.tmp.name)), tiers=tiers, dev_mode=True, dev_ids={1})
        ui = BotUI(services)
        ui.start(1)
        self.assertIn("$9", ui.open_tiers(1).text + " ".join(b.label for b in ui.open_tiers(1).buttons))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        services.subscriptions.dev_renew(1, "daily", start)
        self.assertEqual(services.subscriptions.status(1)["expires_at"], (start + timedelta(days=3)).isoformat())

    def test_referral_rate_is_configurable(self):
        services = MockServices(JsonStore(Path(self.tmp.name)), referral_rate=0.15)
        self.assertEqual(services.referrals.stats(1)["rate"], 0.15)

    def test_developer_balance_rejects_values_that_cannot_be_displayed(self):
        for amount in (float("nan"), float("inf"), float("-inf"), 10 ** 400):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.api.finance.dev_balance(1, amount)
        self.assertEqual(self.api.finance.ledger(1), [])

    def test_ui_language_and_main_menu(self):
        ui = BotUI(self.api, bot_username="example_bot")
        screen = ui.start(2)
        self.assertEqual([b.label for b in screen.buttons], ["العربية", "English"])
        screen = ui.click(2, "lang:en")
        self.assertEqual(len(screen.buttons), 9)
        self.assertIn("Search", screen.buttons[0].label)
        self.assertEqual(ui.click(2, "device:add").kind, "denied")


if __name__ == "__main__":
    unittest.main()
