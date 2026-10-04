import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telebt.data.services import MockServices, PermissionError
from telebt.data.storage import JsonStore
from telebt.bot.ui import BotUI
from telebt.core.validation import ValidationError


UUID = "abcdef12-1234-1234-abcd-123456789abc"


class ExtendedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = MockServices(JsonStore(Path(self.tmp.name)), dev_mode=True, dev_ids={42})
        self.ui = BotUI(self.api, bot_username="demo_bot", support_links=("https://t.me/demo1", "https://t.me/demo2"), dev_mode=True, dev_ids={42})
        self.ui.start(42)
        self.ui.click(42, "lang:ar")
        self.api.subscriptions.dev_state(42, "active")

    def test_ios_adjust_and_singular_validation(self):
        ios = self.api.devices.add(42, "iOS", "Tablet", UUID, UUID)
        adjust = self.api.devices.link_game(42, ios["id"], "g2")
        self.assertIsNone(adjust["extra_id"])
        with self.assertRaises(ValidationError):
            self.api.devices.link_game(42, ios["id"], "g3", UUID)
        android = self.api.devices.add(42, "Android", "Phone", UUID, None)
        singular = self.api.devices.link_game(42, android["id"], "g3", UUID)
        self.assertEqual(singular["extra_id"], UUID)
        with self.assertRaises(ValidationError):
            self.api.devices.link_game(42, android["id"], "g1", "1234567890123-1234567", expected_platform="Adjust")

    def test_custom_and_purchase_previews_never_send_or_count(self):
        device = self.api.devices.add(42, "Android", "Phone", UUID, None)
        linked = self.api.devices.link_game(42, device["id"], "g1", "1234567890123-1234567")
        result = self.api.events.preview(42, linked["id"], "custom_{a}_{b}", ["one", "2"], "custom")
        self.assertEqual(result["event"], "custom_one_2")
        self.assertFalse(result["sent"])
        purchase = self.api.events.preview(42, linked["id"], "af_purchase", [], "purchase")
        self.assertEqual(purchase["event"], "af_purchase")
        self.assertEqual(self.api.subscriptions.status(42)["used"], 0)

    def test_proxy_edit_invalidates_prior_check(self):
        proxy = self.api.proxies.add(42, {"country": "USA", "name": "West", "type": "SOCKS5", "endpoint": "proxy.example:1080", "username": "mock", "password": "demo"})
        proxy["check"] = {"status": "success", "at": "2026-01-01T00:00:00+00:00", "ip": "203.0.113.1"}
        self.api.store.replace("proxies", proxy)
        edited = self.api.proxies.edit(42, proxy["id"], {"endpoint": "new.example:8080"})
        self.assertEqual(edited["check"], {"status": "unchecked"})

    def test_proxy_country_type_edit_and_developer_check(self):
        proxy = self.api.proxies.add(42, {"country": "USA", "name": "West", "type": "SOCKS5", "endpoint": "proxy.example:1080", "username": "mock", "password": "demo"})
        self.ui.click(42, "proxy:editcountry:" + proxy["id"] + ":Canada")
        self.ui.click(42, "proxy:edittype:" + proxy["id"] + ":HTTP/HTTPS")
        self.assertEqual(self.api.proxies.get(42, proxy["id"])["country"], "Canada")
        self.assertEqual(self.api.proxies.get(42, proxy["id"])["type"], "HTTP/HTTPS")
        result = self.ui.click(42, "dev:proxycheck:" + proxy["id"] + ":success")
        self.assertIn("تجريبي", result.text)
        self.assertEqual(self.api.proxies.get(42, proxy["id"])["check"]["status"], "success")

    def test_timezone_change_only_affects_display(self):
        device = self.api.devices.add(42, "Android", "Phone", UUID, None)
        linked = self.api.devices.link_game(42, device["id"], "g1", "1234567890123-1234567")
        plan = self.api.plans.add(42, device["id"], linked["id"], "uniform", "1d", [{"template": "tutorial_complete", "values": []}])
        original = plan["operations"][0]["due_at"]
        self.api.users.timezone(42, "UTC")
        self.assertEqual(self.api.plans.get(42, plan["id"])["operations"][0]["due_at"], original)

    def test_start_drops_unconfirmed_draft_and_language_switch(self):
        self.ui.click(42, "device:os:Android")
        self.ui.text(42, "Unfinished")
        restarted = self.ui.start(42)
        self.assertIn("لم تُحفظ", restarted.text)
        self.assertEqual(self.api.devices.list(42), [])
        english = self.ui.click(42, "lang:en")
        self.assertIn("Main menu", english.text)
        self.assertEqual(len(english.buttons), 9)

    def test_support_and_deposit_placeholders(self):
        support = self.ui.open_support(42)
        self.assertEqual(sum(bool(b.url) for b in support.buttons), 2)
        deposit = self.ui.open_deposit(42)
        self.assertEqual(len(deposit.buttons), 4)
        for action in ("deposit:syriatel", "deposit:sham", "deposit:usdt"):
            self.assertIn("لاحقاً", self.ui.click(42, action).text)

    def test_search_results_are_names_without_selection_buttons(self):
        self.ui.click(42, "search:os:Android")
        screen = self.ui.text(42, "SAMPLE")
        self.assertIn("Sample Quest", screen.text)
        self.assertFalse(any(b.data and b.data.startswith("search:choose") for b in screen.buttons))
        self.assertFalse(any(b.label == "Sample Quest" for b in screen.buttons))

    def test_tier_view_shows_insufficient_demo_balance(self):
        screen = self.ui.click(42, "tier:view:monthly")
        self.assertIn("غير كاف", screen.text)
        self.assertIn("open:deposit", [x.data for x in screen.buttons])

    def test_referral_deep_link_registers_once_without_payment(self):
        self.ui.start(55, "ref_42")
        self.ui.start(55, "ref_42")
        self.assertEqual(self.api.referrals.stats(42)["count"], 1)
        self.assertEqual(self.api.referrals.stats(42)["earnings"], 0)
        self.ui.start(42, "ref_42")
        self.assertEqual(self.api.referrals.stats(42)["count"], 1)

    def test_expiry_purge_keeps_financial_ledger_and_referrals(self):
        self.api.finance.dev_balance(42, 100)
        self.api.store.add("referral_data", {"id": "r1", "user_id": 42, "earnings": 10})
        device = self.api.devices.add(42, "Android", "Phone", UUID, None)
        expiry = datetime.now(timezone.utc) - timedelta(days=36525)
        self.api.subscriptions.dev_expire(42, expiry)
        self.api.subscriptions.dev_advance(42, datetime.now(timezone.utc))
        self.assertEqual(self.api.devices.list(42), [])
        self.assertEqual(self.api.finance.balance(42), 100)
        self.assertEqual(self.api.referrals.stats(42)["earnings"], 10)
        with self.assertRaises(PermissionError):
            self.api.devices.add(42, "Android", "Another", UUID, None)

    def test_device_delete_cascades_plans_but_keeps_proxy(self):
        proxy = self.api.proxies.add(42, {"country": "USA", "name": "West", "type": "SOCKS5", "endpoint": "proxy.example:1080", "username": "mock", "password": "demo"})
        device = self.api.devices.add(42, "Android", "Phone", UUID, None, proxy["id"])
        linked = self.api.devices.link_game(42, device["id"], "g1", "1234567890123-1234567")
        self.api.plans.add(42, device["id"], linked["id"], "uniform", "1h", [{"template": "tutorial_complete", "values": []}])
        self.api.devices.delete(42, device["id"])
        self.assertEqual(self.api.plans.list(42), [])
        self.assertEqual(self.api.store.read("linked_games"), [])
        self.assertEqual(self.api.store.read("plan_counters"), [])
        self.assertEqual(len(self.api.proxies.list(42)), 1)

    def test_link_delete_removes_only_its_plans(self):
        device = self.api.devices.add(42, "Android", "Phone", UUID, None)
        first = self.api.devices.link_game(42, device["id"], "g1", "1234567890123-1234567")
        second = self.api.devices.link_game(42, device["id"], "g3", UUID)
        for linked in (first, second):
            self.api.plans.add(42, device["id"], linked["id"], "uniform", "1h", [{"template": "tutorial_complete", "values": []}])
        self.api.devices.delete_linked(42, first["id"])
        self.assertEqual([x["id"] for x in self.api.devices.linked(42, device["id"])], [second["id"]])
        self.assertEqual([x["linked_id"] for x in self.api.plans.list(42)], [second["id"]])

    def test_purchase_template_cannot_be_scheduled(self):
        device = self.api.devices.add(42, "Android", "Phone", UUID, None)
        linked = self.api.devices.link_game(42, device["id"], "g1", "1234567890123-1234567")
        with self.assertRaises(ValidationError):
            self.api.plans.add(42, device["id"], linked["id"], "uniform", "1h", [{"template": "af_purchase", "values": []}])


if __name__ == "__main__":
    unittest.main()
