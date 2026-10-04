import tempfile
import unittest
from pathlib import Path

from telebt.services import MockServices
from telebt.storage import JsonStore
from telebt.ui import BotUI
from telebt.locale import TEXT


UUID = "12345678-1234-1234-1234-123456789abc"
UID = "1234567890123-1234567"


class UIFlows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.api = MockServices(JsonStore(Path(self.tmp.name)), dev_mode=True, dev_ids={7})
        self.ui = BotUI(self.api, dev_mode=True, dev_ids={7})
        self.ui.start(7)
        self.ui.click(7, "lang:en")
        self.ui.click(7, "dev:active")

    def device_and_game(self):
        self.ui.click(7, "device:add")
        self.ui.click(7, "device:os:Android")
        self.ui.text(7, "My phone")
        self.ui.text(7, UUID)
        self.ui.click(7, "device:proxy:none")
        self.ui.click(7, "device:confirm")
        device = self.api.devices.list(7)[0]
        self.ui.click(7, "link:add:" + device["id"])
        self.ui.click(7, "link:platform:AppsFlyer")
        self.ui.text(7, "sample quest")
        self.ui.click(7, "link:choose:g1")
        self.ui.text(7, UID)
        self.ui.click(7, "link:confirm")
        return device, self.api.devices.linked(7, device["id"])[0]

    def test_search_retry_keeps_os_and_back_walks_once_in_both_languages(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                self.ui.click(7, "lang:" + language)
                self.ui.click(7, "open:search")
                self.ui.click(7, "search:os:Android")
                missing = self.ui.text(7, "jj")
                self.assertIn(TEXT[language]["not_found"], missing.text)
                self.assertEqual(self.ui.sessions[7]["waiting"], "game_name")
                self.assertIn("cancel", [b.data for b in missing.buttons])
                retry = self.ui.click(7, "search:again")
                self.assertIn(TEXT[language]["game_name"], retry.text)
                self.assertEqual(self.ui.sessions[7]["draft"]["os"], "Android")
                self.assertEqual(self.ui.sessions[7]["waiting"], "game_name")
                found = self.ui.text(7, "sample")
                self.assertIn("Sample Quest", found.text)
                self.assertIn(TEXT[language]["game_name"], self.ui.click(7, "back").text)
                self.assertIn(TEXT[language]["choose_os"], self.ui.click(7, "back").text)
                self.assertIn(TEXT[language]["menu"], self.ui.click(7, "back").text)
                self.ui.click(7, "open:search")
                self.ui.click(7, "search:os:iOS")
                self.ui.text(7, "jj")
                self.assertIn(TEXT[language]["menu"], self.ui.click(7, "cancel").text)

    def test_proxy_save_back_skips_confirmation_and_old_form(self):
        self.ui.click(7, "open:proxies")
        self.ui.click(7, "proxy:add")
        self.ui.click(7, "proxy:selectcountry:USA")
        self.ui.text(7, "P")
        self.ui.click(7, "proxy:type:SOCKS5")
        for value in ("proxy.example:1080", "u", "p"):
            self.ui.text(7, value)
        saved = self.ui.click(7, "proxy:save")
        self.assertIn(TEXT["en"]["proxy_saved"], saved.text)
        self.assertEqual(saved.kind, "menu")
        self.assertIn(TEXT["en"]["menu"], saved.text)
        home = self.ui.click(7, "open:proxies")
        self.assertIn(TEXT["en"]["proxies"], home.text)
        self.assertNotIn(TEXT["en"]["proxy_saved"], home.text)
        self.assertIn("USA", [b.label for b in home.buttons])
        country = self.ui.click(7, "proxy:country:USA")
        proxy_id = self.api.proxies.list(7)[0]["id"]
        for _ in range(2):
            self.ui.click(7, "proxy:view:" + proxy_id)
            self.assertIn("USA", self.ui.click(7, "back").text)
        self.assertIn(TEXT["en"]["proxies"], self.ui.click(7, "back").text)

    def test_saved_device_returns_to_main_menu_without_reopening_save(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                self.ui.click(7, "lang:" + language)
                self.ui.click(7, "open:devices")
                self.ui.click(7, "device:add")
                self.ui.click(7, "device:os:iOS")
                self.ui.text(7, "QA " + language)
                self.ui.text(7, "11111111-1111-1111-1111-111111111111")
                self.ui.text(7, "22222222-2222-2222-2222-222222222222")
                self.ui.click(7, "device:proxy:none")
                completed = self.ui.click(7, "device:confirm")
                count = len(self.api.devices.list(7))
                self.assertEqual(completed.kind, "menu")
                self.assertIn(TEXT[language]["device_saved"], completed.text)
                self.assertIn(TEXT[language]["menu"], completed.text)
                listing = self.ui.click(7, "open:devices")
                self.assertIn(TEXT[language]["devices"], listing.text)
                self.assertNotIn("device:confirm", [button.data for button in listing.buttons])
                self.assertIsNone(self.ui.sessions[7]["flow"])
                self.assertEqual(len(self.api.devices.list(7)), count)
                menu = self.ui.click(7, "back")
                self.assertIn(TEXT[language]["menu"], menu.text)
                self.assertNotIn(TEXT[language]["draft_discarded"], menu.text)

    def test_proxy_save_and_browse_back_does_not_restore_draft(self):
        self.ui.click(7, "menu")
        self.ui.click(7, "open:search")
        self.ui.click(7, "search:os:iOS")
        self.ui.text(7, "missing-game")
        self.ui.click(7, "cancel")
        self.ui.click(7, "open:proxies")
        self.ui.click(7, "proxy:add")
        self.ui.click(7, "proxy:selectcountry:USA")
        self.ui.text(7, "QA proxy")
        self.ui.click(7, "proxy:type:SOCKS5")
        for value in ("proxy.example:1080", "u", "p"):
            self.ui.text(7, value)
        self.ui.click(7, "proxy:save")
        self.ui.click(7, "open:proxies")
        self.ui.click(7, "proxy:country:USA")
        proxy_id = self.api.proxies.list(7)[0]["id"]
        self.ui.click(7, "proxy:view:" + proxy_id)
        self.ui.click(7, "back")
        self.ui.click(7, "back")
        menu = self.ui.click(7, "back")
        self.assertIn(TEXT["en"]["menu"], menu.text)
        self.assertNotIn(TEXT["en"]["draft_discarded"], menu.text)
        self.assertIsNone(self.ui.sessions[7]["flow"])

    def test_missing_search_accepts_new_text_without_crossing_into_device_flow(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                self.ui.click(7, "lang:" + language)
                self.ui.click(7, "open:search")
                self.ui.click(7, "search:os:Android")
                self.ui.text(7, "jj")
                found = self.ui.text(7, "sample")
                self.assertIn("Sample Quest", found.text)
                self.assertEqual(self.ui.sessions[7]["flow"], "search")
                self.assertEqual(self.ui.sessions[7]["draft"]["os"], "Android")
                self.assertIn(TEXT[language]["game_name"], self.ui.click(7, "back").text)

    def test_proxy_back_across_countries_keeps_one_step_per_visit(self):
        for country in ("USA", "UK"):
            self.api.proxies.add(7, {"country": country, "name": country + " proxy", "type": "SOCKS5", "endpoint": "proxy.example:1080", "username": "u", "password": "p"})
        self.ui.click(7, "menu")
        self.ui.click(7, "open:proxies")
        for country in ("USA", "UK", "USA"):
            self.ui.click(7, "proxy:country:" + country)
            proxy = next(p for p in self.api.proxies.list(7) if p["country"] == country)
            self.ui.click(7, "proxy:view:" + proxy["id"])
            self.assertIn(country, self.ui.click(7, "back").text)
            self.assertIn(TEXT["en"]["proxies"], self.ui.click(7, "back").text)
        self.assertIn(TEXT["en"]["menu"], self.ui.click(7, "back").text)

    def test_proxy_saved_confirmation_is_not_replayed_in_arabic(self):
        self.ui.click(7, "lang:ar")
        self.ui.click(7, "open:proxies")
        self.ui.click(7, "proxy:add")
        self.ui.click(7, "proxy:selectcountry:UK")
        self.ui.text(7, "تجريبي")
        self.ui.click(7, "proxy:type:SOCKS5")
        for value in ("proxy.example:1080", "u", "p"):
            self.ui.text(7, value)
        saved = self.ui.click(7, "proxy:save")
        self.assertIn(TEXT["ar"]["proxy_saved"], saved.text)
        self.assertEqual(saved.kind, "menu")
        self.assertIn(TEXT["ar"]["proxies"], self.ui.click(7, "open:proxies").text)
        self.assertIn(TEXT["ar"]["menu"], self.ui.click(7, "back").text)

    def test_ios_back_from_idfv_preserves_entered_idfa_and_name(self):
        original = "12345678-1234-1234-1234-123456789abc"
        corrected = "87654321-4321-4321-4321-cba987654321"
        self.ui.click(7, "menu")
        self.ui.click(7, "open:devices")
        self.ui.click(7, "device:add")
        self.ui.click(7, "device:os:iOS")
        self.ui.text(7, "iPhone")
        self.ui.text(7, original)
        self.assertIn("IDFV", self.ui.sessions[7]["screen"].text)
        self.assertIn("IDFA", self.ui.click(7, "back").text)
        self.assertEqual(self.ui.sessions[7]["draft"]["device_name"], "iPhone")
        self.assertEqual(self.ui.sessions[7]["draft"]["idfa"], original)
        self.ui.text(7, corrected)
        self.assertEqual(self.ui.sessions[7]["draft"]["idfa"], corrected)

    def test_identical_prompt_keeps_distinct_logical_steps_without_duplicate_entries(self):
        state = self.ui.sessions[7]
        self.ui._show(7, "Same prompt", [])
        depth = len(state["history"])
        self.ui._show(7, "Same prompt", [])
        self.assertEqual(len(state["history"]), depth)
        state["waiting"] = "second_field"
        self.ui._show(7, "Same prompt", [])
        self.assertEqual(len(state["history"]), depth + 1)
        self.ui.click(7, "back")
        self.assertIsNone(state["waiting"])

    def test_device_link_and_event_preview_do_not_consume_quota(self):
        device, linked = self.device_and_game()
        self.assertEqual(device["proxy_id"], None)
        self.ui.click(7, "event:start:" + linked["id"] + ":normal")
        self.ui.click(7, "event:template:1")
        self.ui.text(7, "advanced")
        result = self.ui.click(7, "event:execute")
        self.assertIn("reach_level_advanced", result.text)
        self.assertIn("no request was sent", result.text)
        self.assertEqual(self.api.subscriptions.status(7)["used"], 0)

    def test_proxy_save_is_unchecked_and_delete_detaches(self):
        device, _ = self.device_and_game()
        self.ui.click(7, "proxy:add")
        self.ui.click(7, "proxy:selectcountry:USA")
        self.ui.text(7, "California")
        self.ui.click(7, "proxy:type:SOCKS5")
        self.ui.text(7, "proxy.example:1080")
        self.ui.text(7, "demo-user")
        review = self.ui.text(7, "demo-secret")
        self.assertIn("demo-secret", review.text)
        self.ui.click(7, "proxy:save")
        proxy = self.api.proxies.list(7)[0]
        self.assertIn("demo-secret", self.ui.click(7, "proxy:view:" + proxy["id"]).text)
        self.assertNotIn("demo-secret", self.ui.click(8, "proxy:view:" + proxy["id"]).text)
        self.assertEqual(proxy["check"]["status"], "unchecked")
        self.ui.click(7, f"device:setproxy:{device['id']}:{proxy['id']}")
        self.ui.click(7, "proxy:deleteyes:" + proxy["id"])
        self.assertIsNone(self.api.devices.get(7, device["id"])["proxy_id"])

    def test_plan_creation_and_expired_read_only(self):
        device, linked = self.device_and_game()
        self.ui.click(7, "plan:add")
        self.ui.click(7, "plan:device:" + device["id"])
        self.ui.click(7, "plan:mode:multiple")
        self.ui.click(7, "plan:game:" + linked["id"])
        self.ui.click(7, "plan:template:0")
        self.ui.text(7, "2h")
        self.ui.click(7, "plan:continue")
        self.ui.click(7, "plan:template:1")
        self.ui.text(7, "55")
        self.ui.text(7, "30m")
        preview = self.ui.click(7, "plan:finish")
        self.assertLess(preview.text.index("tutorial_complete"), preview.text.index("reach_level_55"))
        self.assertNotIn("+00:00", preview.text)
        self.assertIn("1. tutorial_complete — 2h", preview.text)
        self.assertIn("2. reach_level_55 — 30m", preview.text)
        completed = self.ui.click(7, "plan:confirm")
        self.assertEqual(completed.kind, "menu")
        self.assertIn(TEXT["en"]["menu"], completed.text)
        self.assertIn(TEXT["en"]["plan_saved"], completed.text)
        plan = self.api.plans.list(7)[0]
        self.assertEqual(len(plan["operations"]), 2)
        self.api.subscriptions.dev_state(7, "expired")
        detail = self.ui.click(7, "plan:view:" + plan["id"])
        self.assertIn("Frozen: subscription", detail.text)
        self.assertNotIn("plan:edit:" + plan["id"], [x.data for x in detail.buttons])
        self.assertEqual(self.ui.click(7, "plan:deleteyes:" + plan["id"]).kind, "denied")

    def test_developer_gate_denies_guessed_callback(self):
        public = BotUI(self.api, dev_mode=False, dev_ids={7})
        self.assertEqual(public.click(7, "dev:active").kind, "denied")
        self.assertEqual(public.click(8, "dev:active").kind, "denied")

    def test_back_corrects_one_device_field_without_losing_name(self):
        self.ui.click(7, "device:os:iOS")
        self.ui.text(7, "Tablet")
        self.ui.text(7, UUID)
        back = self.ui.click(7, "back")
        self.assertIn("IDFA", back.text)
        self.ui.text(7, UUID)
        self.ui.text(7, UUID)
        self.ui.click(7, "device:proxy:none")
        self.ui.click(7, "device:confirm")
        self.assertEqual(self.api.devices.list(7)[0]["name"], "Tablet")

    def test_sequential_back_through_ios_fields_and_no_repeated_menu(self):
        self.ui.click(7, "menu")
        self.ui.click(7, "open:devices")
        self.ui.click(7, "device:add")
        self.ui.click(7, "device:os:iOS")
        self.ui.text(7, "Tablet")
        self.ui.text(7, UUID)
        self.assertIn("IDFA", self.ui.click(7, "back").text)
        self.assertIn("device name", self.ui.click(7, "back").text.lower())
        self.assertEqual(self.ui.sessions[7]["draft"]["device_name"], "Tablet")
        self.assertIn("operating system", self.ui.click(7, "back").text.lower())
        self.assertIn("My devices", self.ui.click(7, "back").text)
        self.assertIn("Main menu", self.ui.click(7, "back").text)
        self.assertIn("Main menu", self.ui.click(7, "back").text)
        self.assertEqual(self.ui.sessions[7]["history"], [])

    def test_reopening_same_list_or_page_does_not_add_back_step(self):
        self.ui.click(7, "menu")
        self.ui.click(7, "open:devices")
        depth = len(self.ui.sessions[7]["history"])
        self.ui.click(7, "open:devices")
        self.ui.click(7, "device:page:0")
        self.assertEqual(len(self.ui.sessions[7]["history"]), depth)
        self.assertIn("Main menu", self.ui.click(7, "back").text)

    def test_cancel_discards_form_independently_of_back(self):
        self.ui.click(7, "device:os:iOS")
        self.ui.text(7, "Tablet")
        self.assertIn("device name", self.ui.click(7, "back").text.lower())
        screen = self.ui.click(7, "cancel")
        self.assertIn("Main menu", screen.text)
        self.assertEqual(self.ui.sessions[7]["draft"], {})

    def test_proxy_unsaved_path_absent_and_old_callback_safe(self):
        screen = self.ui.click(7, "open:proxies")
        self.assertNotIn("proxy:check", [b.data for b in screen.buttons])
        self.assertEqual(self.ui.click(7, "proxy:check").kind, "error")

    def test_proxy_detach_preserves_device_game_and_plan(self):
        device, linked = self.device_and_game()
        proxy = self.api.proxies.add(7, {"country": "USA", "name": "P", "type": "SOCKS5", "endpoint": "proxy.example:1080", "username": "u", "password": "secret"})
        self.api.devices.proxy(7, device["id"], proxy["id"])
        plan = self.api.plans.add(7, device["id"], linked["id"], "uniform", "30m", [{"template": "tutorial_complete", "values": []}])
        screen = self.ui.click(7, "device:manageproxy:" + device["id"])
        self.assertIn("P", screen.text)
        self.assertIn("device:setproxy:" + device["id"] + ":none", [b.data for b in screen.buttons])
        self.ui.click(7, "device:setproxy:" + device["id"] + ":none")
        self.assertIsNone(self.api.devices.get(7, device["id"])["proxy_id"])
        self.assertEqual(self.api.devices.linked(7, device["id"])[0]["id"], linked["id"])
        self.assertEqual(self.api.plans.get(7, plan["id"])["id"], plan["id"])

    def test_profile_quota_remaining_and_device_legend_both_languages(self):
        self.api.devices.add(7, "iOS", "Tablet", UUID, UUID)
        for language, quota, legend in (("en", "Daily operations remaining", "🍎 = iPhone / iOS"), ("ar", "العمليات اليومية المتبقية", "🍎 = iPhone / iOS")):
            self.ui.click(7, "lang:" + language)
            self.assertIn(quota + ": 300/300", self.ui.open_profile(7).text)
            self.assertIn(legend, self.ui.open_devices(7).text)
        status = self.api.subscriptions.status(7)
        for used, remaining in ((1, 299), (50, 250), (300, 0)):
            status["used"] = used
            self.api.subscriptions._save(status)
            self.assertIn(f"{remaining}/300", self.ui.open_profile(7).text)

    def test_invalid_identifier_reprompts_same_field(self):
        self.ui.click(7, "device:os:Android")
        self.ui.text(7, "Phone")
        bad = self.ui.text(7, "not-a-gaid")
        self.assertIn("GAID", bad.text)
        self.assertEqual(self.ui.sessions[7]["waiting"], "gaid")
        self.ui.text(7, UUID)
        self.assertEqual(self.ui.sessions[7]["draft"]["device_name"], "Phone")

    def test_plan_number_does_not_reuse_deleted_number(self):
        device, linked = self.device_and_game()
        op = [{"template": "tutorial_complete", "values": []}]
        first = self.api.plans.add(7, device["id"], linked["id"], "uniform", "30m", op)
        self.api.plans.delete(7, first["id"])
        second = self.api.plans.add(7, device["id"], linked["id"], "uniform", "30m", op)
        self.assertEqual(second["number"], 2)

    def test_old_callbacks_are_rejected_without_crashing(self):
        screen = self.ui.click(7, "open:nonexistent")
        self.assertEqual(screen.kind, "error")

    def test_malformed_page_callback_returns_safe_error(self):
        screen = self.ui.click(7, "profile:queue:not-a-page")
        self.assertEqual(screen.kind, "error")
        self.assertIn("Old or invalid button", screen.text)

    def test_saved_plan_edit_preserves_failed_operation(self):
        device, linked = self.device_and_game()
        ops = [{"template": "tutorial_complete", "values": []}, {"template": "reach_level_{x}", "values": ["5"]}]
        plan = self.api.plans.add(7, device["id"], linked["id"], "uniform", "30m", ops)
        self.api.plans.dev_fail_first(7, plan["id"])
        self.assertIn("Simulated failure", self.ui.click(7, "plan:view:" + plan["id"]).text)
        second = next(x for x in self.api.plans.get(7, plan["id"])["operations"] if x["status"] == "pending")
        self.ui.click(7, "plan:edit:" + plan["id"])
        self.ui.click(7, f"plan:editop:{plan['id']}:{second['id']}")
        self.ui.click(7, "plan:template:1")
        self.ui.text(7, "updated")
        review = self.ui.click(7, "plan:editsave")
        self.assertEqual(review.kind, "menu")
        self.assertIn(TEXT["en"]["changes_saved"], review.text)
        current = self.api.plans.get(7, plan["id"])
        self.assertEqual(current["operations"][0]["status"], "failed")
        self.assertEqual(current["operations"][1]["event"], "reach_level_updated")

    def test_queue_is_fifo_and_profile_has_paginated_view(self):
        self.api.subscriptions.dev_renew(7, "weekly")
        self.api.subscriptions.dev_renew(7, "daily")
        status = self.api.subscriptions.status(7)
        self.assertEqual([x["tier"] for x in status["queue"]], ["weekly", "daily"])
        profile = self.ui.open_profile(7)
        self.assertIn("profile:queue:0", [b.data for b in profile.buttons])
        queue = self.ui.click(7, "profile:queue:0")
        self.assertLess(queue.text.index("Weekly"), queue.text.index("Daily"))

    def test_plan_device_picker_paginates(self):
        for index in range(7):
            self.api.devices.add(7, "Android", f"Phone {index}", UUID, None)
        first = self.ui.click(7, "plan:add")
        self.assertIn("plan:devices:1", [b.data for b in first.buttons])
        second = self.ui.click(7, "plan:devices:1")
        self.assertIn("Phone 6", [b.label for b in second.buttons])

    def test_single_normal_event_skips_template_picker(self):
        device = self.api.devices.add(7, "Android", "Phone", UUID, None)
        linked = self.api.devices.link_game(7, device["id"], "g3", UUID)
        self.ui.click(7, "plan:add")
        self.ui.click(7, "plan:device:" + device["id"])
        self.ui.click(7, "plan:mode:multiple")
        screen = self.ui.click(7, "plan:game:" + linked["id"])
        self.assertIn("this operation's interval", screen.text)
        self.assertFalse(any(b.data and b.data.startswith("plan:template:") for b in screen.buttons))

    def test_draft_plan_operation_can_be_corrected_before_save(self):
        device, linked = self.device_and_game()
        for action in ("plan:add", "plan:device:" + device["id"], "plan:mode:uniform", "plan:game:" + linked["id"]):
            self.ui.click(7, action)
        self.ui.text(7, "30m")
        self.ui.click(7, "plan:template:1")
        self.ui.text(7, "wrong")
        self.ui.click(7, "plan:finish")
        self.ui.click(7, "plan:draftlist")
        self.ui.click(7, "plan:draftop:0")
        self.ui.click(7, "plan:template:1")
        self.ui.text(7, "right")
        review = self.ui.click(7, "plan:draftsave")
        self.assertIn("reach_level_right", review.text)
        self.ui.click(7, "plan:confirm")
        self.assertEqual(self.api.plans.list(7)[0]["operations"][0]["event"], "reach_level_right")

    def test_event_variable_can_be_corrected_without_changing_other_values(self):
        _, linked = self.device_and_game()
        self.ui.click(7, "event:start:" + linked["id"] + ":normal")
        self.ui.click(7, "event:template:2")
        self.ui.text(7, "one")
        self.ui.text(7, "two")
        self.ui.click(7, "event:editvar:0")
        result = self.ui.text(7, "updated")
        self.assertIn("stage_updated_two", result.text)

    def test_back_from_second_event_variable_reprompts_first(self):
        _, linked = self.device_and_game()
        self.ui.click(7, "event:start:" + linked["id"] + ":normal")
        self.ui.click(7, "event:template:2")
        self.ui.text(7, "first")
        back = self.ui.click(7, "back")
        self.assertIn("variable: x", back.text)
        self.assertEqual(self.ui.sessions[7]["draft"]["values"], ["first"])
        review_step = self.ui.text(7, "fixed")
        self.assertIn("variable: name", review_step.text)

    def test_variable_back_preserves_values_in_events_and_plans_in_both_languages(self):
        device, linked = self.device_and_game()
        for language in ("en", "ar"):
            for flow in ("event", "plan"):
                with self.subTest(language=language, flow=flow):
                    self.ui.click(7, "lang:" + language)
                    if flow == "event":
                        self.ui.click(7, "event:start:" + linked["id"] + ":normal")
                        self.ui.click(7, "event:template:2")
                    else:
                        for action in ("plan:add", "plan:device:" + device["id"], "plan:mode:multiple", "plan:game:" + linked["id"], "plan:template:2"):
                            self.ui.click(7, action)
                    self.ui.text(7, "first")
                    self.ui.text(7, "second")
                    self.ui.click(7, "back")
                    self.assertEqual(self.ui.sessions[7]["draft"]["values"], ["first", "second"])
                    self.ui.click(7, "back")
                    self.assertEqual(self.ui.sessions[7]["draft"]["values"], ["first", "second"])
                    self.ui.text(7, "corrected")
                    self.assertEqual(self.ui.sessions[7]["draft"]["values"], ["corrected", "second"])
                    result = self.ui.text(7, "second")
                    self.assertIn("stage_corrected_second" if flow == "event" else TEXT[language]["operation_interval"], result.text)
                    self.ui.click(7, "back")
                    self.ui.click(7, "back")
                    picker = self.ui.click(7, "back")
                    self.assertTrue(any(b.data and b.data.startswith(flow + ":template:") for b in picker.buttons))

    def test_completed_edits_clear_draft_and_return_home_in_both_languages(self):
        device, linked = self.device_and_game()
        proxy = self.api.proxies.add(7, dict(country="USA", name="P", type="SOCKS5", endpoint="proxy.example:1080", username="u", password="p"))
        plan = self.api.plans.add(7, device["id"], linked["id"], "uniform", "30m", [{"template": "tutorial_complete", "values": []}])
        for language in ("en", "ar"):
            self.ui.click(7, "lang:" + language)
            for action, value, save in (("link:edit:" + linked["id"], UID, None),
                                        ("proxy:editfield:" + proxy["id"] + ":proxy_name", "Edited", None),
                                        ("plan:editshared:" + plan["id"], "45m", "plan:editsave")):
                with self.subTest(language=language, action=action):
                    self.ui.click(7, action)
                    screen = self.ui.text(7, value)
                    if save:
                        screen = self.ui.click(7, save)
                    self.assertEqual(screen.kind, "menu")
                    self.assertIn(TEXT[language]["menu"], screen.text)
                    self.assertEqual(self.ui.sessions[7]["draft"], {})
                    self.assertIsNone(self.ui.sessions[7]["flow"])
        self.assertEqual(self.api.proxies.get(7, proxy["id"])["name"], "Edited")
        self.assertEqual(self.api.plans.get(7, plan["id"])["shared_interval"], "45m")

    def test_back_after_draft_operation_opens_editable_draft(self):
        device, linked = self.device_and_game()
        self.ui.click(7, "plan:add")
        self.ui.click(7, "plan:device:" + device["id"])
        self.ui.click(7, "plan:mode:uniform")
        self.ui.click(7, "plan:game:" + linked["id"])
        self.ui.text(7, "30m")
        self.ui.click(7, "plan:template:0")
        screen = self.ui.click(7, "back")
        self.assertIn("plan:draftop:0", [b.data for b in screen.buttons])

    def test_exhausted_quota_blocks_manual_demo_action(self):
        _, linked = self.device_and_game()
        self.api.subscriptions.dev_quota(7, True)
        screen = self.ui.click(7, "event:start:" + linked["id"] + ":normal")
        self.assertIn("quota", screen.text.lower())
        self.assertFalse(any(b.data and b.data.startswith("event:template") for b in screen.buttons))
        screen = self.ui.click(7, "event:template:999")
        self.assertEqual(screen.kind, "error")

    def test_expired_data_sections_show_grace_deadline_in_both_languages(self):
        self.api.subscriptions.dev_state(7, "expired")
        for language, label in (("en", "Operational data grace deadline"), ("ar", "نهاية مهلة البيانات التشغيلية")):
            with self.subTest(language=language):
                self.ui.click(7, "lang:" + language)
                self.assertIn(label, self.ui.click(7, "open:devices").text)
                self.assertIn(label, self.ui.click(7, "open:proxies").text)

    def test_identifier_error_is_localized_and_keeps_draft_in_both_languages(self):
        for language, error in (("en", "Invalid GAID"), ("ar", "GAID غير صالح")):
            with self.subTest(language=language):
                self.ui.click(7, "lang:" + language)
                self.ui.click(7, "device:os:Android")
                self.ui.text(7, "Phone")
                screen = self.ui.text(7, "bad-id")
                self.assertIn(error, screen.text)
                self.assertEqual(self.ui.sessions[7]["draft"]["device_name"], "Phone")
                self.ui.click(7, "cancel")

    def test_all_main_sections_open_with_selected_language(self):
        sections = {
            "search": "choose_os", "deposit": "deposit", "devices": "devices",
            "plans": "plans", "profile": "profile", "proxies": "proxies",
            "tiers": "tiers", "support": "support_missing", "language": "choose_language",
        }
        for language, other in (("en", "ar"), ("ar", "en")):
            self.ui.click(7, "lang:" + language)
            menu = self.ui.menu(7)
            self.assertEqual(len(menu.buttons), 9)
            for section, title_key in sections.items():
                with self.subTest(language=language, section=section):
                    screen = self.ui.click(7, "open:" + section)
                    self.assertIn(TEXT[language][title_key], screen.text)
                    self.assertNotIn(TEXT[other][title_key], screen.text)
                    self.assertIn(TEXT[language]["back"], [button.label for button in screen.buttons])

    def test_empty_purchase_list_names_purchase_events_in_both_languages(self):
        device = self.api.devices.add(7, "Android", "Phone", UUID, None)
        linked = self.api.devices.link_game(7, device["id"], "g6", UID)
        for language, expected in (("en", "no purchase events"), ("ar", "لا توجد أحداث شراء")):
            with self.subTest(language=language):
                self.ui.click(7, "lang:" + language)
                screen = self.ui.click(7, f"event:start:{linked['id']}:purchase")
                self.assertIn(expected, screen.text)


if __name__ == "__main__":
    unittest.main()
