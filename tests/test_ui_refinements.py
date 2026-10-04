"""Phase 1 UI refinements, using isolated stores and an offline Telegram timeline."""
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, NetworkError

from telebt.locale import TEXT
from telebt.services import MockServices
from telebt.storage import JsonStore
from telebt.telegram_adapter import keyboard
from telebt.ui import BotUI
from telebt.validation import ValidationError
from test_telegram_timeline import Timeline, IDFA, IDFV


class PlanPresentationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.api = MockServices(JsonStore(Path(temporary.name)), dev_mode=True, dev_ids={7})
        self.ui = BotUI(self.api, dev_mode=True, dev_ids={7})
        self.ui.start(7)
        self.ui.click(7, "lang:en")
        self.api.subscriptions.dev_state(7, "active")
        self.device = self.api.devices.add(7, "Android", "Plan phone", IDFA, None)
        self.linked = self.api.devices.link_game(7, self.device["id"], "g1", "1234567890123-1234567")

    def draft(self, mode, choices):
        for action in ("open:plans", "plan:add", "plan:device:" + self.device["id"],
                       "plan:mode:" + mode, "plan:game:" + self.linked["id"]):
            self.ui.click(7, action)
        if mode == "uniform":
            self.ui.text(7, "1d")
        intervals = ["1d", "3h", "2d", "30m"]
        events = []
        for index, choice in enumerate(choices):
            if index:
                self.ui.click(7, "plan:continue")
            self.ui.click(7, "plan:template:" + str(choice))
            if choice == 1:
                self.ui.text(7, str(30 + index))
                events.append("reach_level_" + str(30 + index))
            elif choice == 2:
                self.ui.text(7, "third")
                self.ui.text(7, "value")
                events.append("stage_third_value")
            else:
                events.append("tutorial_complete")
            if mode == "multiple":
                self.ui.text(7, intervals[index])
            self.assertIn("plan:finish", [button.data for button in self.ui._state(7)["screen"].buttons])
        return events, intervals

    def assert_simple(self, screen, language, mode, events, intervals):
        text = screen.text
        self.assertIn(("الجهاز" if language == "ar" else "Device") + ": Plan phone", text)
        self.assertIn(("اللعبة" if language == "ar" else "Game") + ": Sample Quest", text)
        for technical in ("Asia/Damascus", "GMT", "UTC", "+00:00", "→", "Proxy", "البروكسي",
                          self.device["id"], self.linked["id"]):
            self.assertNotIn(technical, text)
        expected = [f"{i + 1}. {event}" + (f" — {intervals[i]}" if mode == "multiple" else "")
                    for i, event in enumerate(events)]
        self.assertEqual([line for line in text.splitlines() if line[:1].isdigit()], expected)
        if mode == "uniform":
            self.assertEqual(text.count("1d"), 1)

    def test_one_two_and_many_events_save_in_both_modes_and_languages(self):
        baseline = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
        for language in ("ar", "en"):
            for mode in ("uniform", "multiple"):
                for choices in ([0], [1], [0, 1], [0, 0, 0, 0], [0, 1, 0, 2]):
                    with self.subTest(language=language, mode=mode, choices=choices):
                        self.ui.click(7, "lang:" + language)
                        events, intervals = self.draft(mode, choices)
                        review = self.ui.click(7, "plan:finish")
                        self.assertEqual(review.kind, "plan_review")
                        self.assertIn("plan:confirm", [b.data for b in review.buttons])
                        before = len(self.api.plans.list(7))
                        with patch("telebt.services.utc_now", return_value=baseline):
                            self.ui.click(7, "plan:confirm")
                        plans = self.api.plans.list(7)
                        self.assertEqual(len(plans), before + 1)
                        operations = sorted(plans[-1]["operations"], key=lambda op: op["order"])
                        self.assertEqual([op["event"] for op in operations], events)
                        deltas = [timedelta(days=i + 1) for i in range(len(events))] if mode == "uniform" else [
                            timedelta(days=1), timedelta(hours=3), timedelta(days=2), timedelta(minutes=30)][:len(events)]
                        self.assertEqual([op["due_at"] for op in operations], [(baseline + delta).isoformat() for delta in deltas])

    def test_review_and_saved_plan_show_ordered_events_without_technical_details(self):
        for language in ("ar", "en"):
            for mode in ("uniform", "multiple"):
                with self.subTest(language=language, mode=mode):
                    self.ui.click(7, "lang:" + language)
                    events, intervals = self.draft(mode, [0, 1, 0, 2])
                    review = self.ui.click(7, "plan:finish")
                    self.assert_simple(review, language, mode, events, intervals)
                    self.ui.click(7, "plan:confirm")
                    plan = self.api.plans.list(7)[-1]
                    detail = self.ui.click(7, "plan:view:" + plan["id"])
                    self.assert_simple(detail, language, mode, events, intervals)
                    for operation in plan["operations"]:
                        self.assertNotIn(operation["id"], detail.text)
                        self.assertNotIn(operation["due_at"], detail.text)
                    self.assertNotIn(plan["id"], detail.text)

    def test_saved_edit_review_and_buttons_hide_internal_ids(self):
        for language in ("ar", "en"):
            for mode in ("uniform", "multiple"):
                with self.subTest(language=language, mode=mode):
                    self.ui.click(7, "lang:" + language)
                    self.draft(mode, [0, 1])
                    self.ui.click(7, "plan:finish")
                    self.ui.click(7, "plan:confirm")
                    plan = self.api.plans.list(7)[-1]
                    chooser = self.ui.click(7, "plan:edit:" + plan["id"])
                    operation = next(op for op in plan["operations"] if op["order"] == 2)
                    self.assertNotIn(operation["id"], "\n".join(b.label for b in chooser.buttons))
                    self.ui.click(7, f"plan:editop:{plan['id']}:{operation['id']}")
                    self.ui.click(7, "plan:template:1")
                    screen = self.ui.text(7, "90")
                    if mode == "multiple":
                        screen = self.ui.text(7, "3h")
                    self.assert_simple(screen, language, mode, ["tutorial_complete", "reach_level_90"], ["1d", "3h"])
                    self.assertNotIn(operation["id"], screen.text)

    def test_plan_menu_hides_timezone_and_preserves_stored_timestamps(self):
        plan = self.api.plans.add(7, self.device["id"], self.linked["id"], "uniform", "1d",
                                  [{"template": "tutorial_complete", "values": []}])
        for language in ("ar", "en"):
            self.ui.click(7, "lang:" + language)
            menu = self.ui.click(7, "open:plans")
            self.assertNotIn("timezone", [b.data for b in menu.buttons])
            self.assertNotIn("Asia/Damascus", menu.text)
            self.ui.click(7, "plan:view:" + plan["id"])
            self.assertEqual(self.api.plans.get(7, plan["id"]), plan)
            old_zone = self.api.users.get(7)["timezone"]
            self.assertEqual(self.ui.click(7, "timezone").kind, "error")
            self.assertEqual(self.api.users.get(7)["timezone"], old_zone)

    def test_visible_account_dates_do_not_show_timezone_names(self):
        self.api.users.timezone(7, "UTC")
        profile = self.ui.open_profile(7).text
        self.assertIn("Expires:", profile)
        self.assertIn("Next quota window:", profile)
        self.assertNotIn("UTC", profile)

        self.api.subscriptions.dev_state(7, "expired")
        for screen in (self.ui.open_profile(7), self.ui.open_devices(7),
                       self.ui.open_proxies(7), self.ui.open_plans(7)):
            self.assertNotIn("UTC", screen.text)

    def test_empty_plan_and_purchase_events_remain_invalid(self):
        for mode in ("uniform", "multiple"):
            with self.assertRaisesRegex(ValidationError, "invalid_schedule"):
                self.api.plans.add(7, self.device["id"], self.linked["id"], mode, "1d", [])
            with self.assertRaisesRegex(ValidationError, "invalid_event"):
                self.api.plans.add(7, self.device["id"], self.linked["id"], mode, "1d",
                                   [{"template": "af_purchase", "values": [], "interval": "1d"}])


class RefinementTimelineTests(unittest.TestCase):
    def make(self, language):
        chat = Timeline(language)
        self.addCleanup(chat.close)
        return chat

    def identifier_prompt(self, chat, field):
        if field in {"gaid", "idfa", "idfv"}:
            chat.press("open:devices")
            chat.press("device:add")
            chat.press("device:os:" + ("Android" if field == "gaid" else "iOS"))
            chat.type("Identifier phone")
            if field == "idfv":
                chat.type(IDFA)
        else:
            device = chat.ui.s.devices.add(7, "Android", "Identifier phone", IDFA, None)
            chat.press("open:devices")
            chat.press("device:view:" + device["id"])
            chat.press("link:add:" + device["id"])
            chat.press("link:platform:" + ("AppsFlyer" if field == "uid" else "Singular"))
            chat.type("sample")
            chat.press("link:choose:" + ("g1" if field == "uid" else "g3"))

    def test_main_keyboard_has_five_rows_with_stable_actions(self):
        expected = [["open:search"], ["open:profile", "open:devices"], ["open:plans", "open:proxies"],
                    ["open:deposit", "open:tiers"], ["open:support", "open:language"]]
        for language in ("ar", "en"):
            chat = self.make(language)
            screen = chat.ui._state(7)["screen"]
            markup = keyboard(screen)
            self.assertEqual([[b.callback_data for b in row] for row in markup.inline_keyboard], expected)
            markup = chat.active()[-1]["markup"]
            self.assertEqual([len(row) for row in markup.inline_keyboard], [1, 2, 2, 2, 2])
            self.assertEqual([b.callback_data.split(":")[1] for row in markup.inline_keyboard for b in row],
                             [str(i) for i in range(9)])

    def test_repeated_identifier_errors_stay_single_and_clear_after_valid_input(self):
        for language in ("ar", "en"):
            for field in ("gaid", "idfa", "idfv", "uid", "singular"):
                with self.subTest(language=language, field=field):
                    chat = self.make(language)
                    self.identifier_prompt(chat, field)
                    draft = deepcopy(chat.ui._state(7)["draft"])
                    original_prompt = chat.active()[-1]["text"]
                    prompt_id = chat.active()[-1]["id"]
                    error = TEXT[language]["invalid_" + field]
                    for bad in ("bad-one", "bad-two", "bad-three"):
                        chat.type(bad)
                        self.assertEqual(chat.ui._state(7)["waiting"], field)
                        self.assertEqual(chat.ui._state(7)["draft"], draft)
                        self.assertEqual(chat.active()[-1]["text"].count(error), 1)
                        self.assertEqual(chat.active()[-1]["id"], prompt_id)
                        self.assertNotIn(bad, chat.active()[-1]["text"])
                        self.assertEqual(sum(error in row["text"] for row in chat.bot_rows()), 1)
                    chat.type("1234567890123-1234567" if field == "uid" else IDFV)
                    self.assertFalse(any(error in row["text"] for row in chat.bot_rows()))
                    self.assertEqual(chat.row(prompt_id)["text"], original_prompt)
                    self.assertEqual(chat.active(), [chat.visible()[-1]])
                    self.assertTrue(all(error not in screen.text for screen, _ in chat.ui._state(7)["history"]))

    def test_back_after_invalid_input_retains_valid_previous_values(self):
        for language in ("ar", "en"):
            chat = self.make(language)
            self.identifier_prompt(chat, "idfv")
            chat.type("bad-one")
            chat.type("bad-two")
            chat.press("back")
            self.assertEqual(chat.ui._state(7)["waiting"], "idfa")
            self.assertEqual(chat.ui._state(7)["draft"]["idfa"], IDFA)
            self.assertEqual(chat.ui._state(7)["draft"]["device_name"], "Identifier phone")
            self.assertFalse(any(TEXT[language]["invalid_idfv"] in row["text"] for row in chat.bot_rows()))

    def test_single_event_finishes_saves_once_and_returns_fresh_home(self):
        for language in ("ar", "en"):
            for mode in ("uniform", "multiple"):
                with self.subTest(language=language, mode=mode):
                    chat = self.make(language)
                    device = chat.ui.s.devices.add(7, "Android", "One event phone", IDFA, None)
                    linked = chat.ui.s.devices.link_game(7, device["id"], "g3", IDFA)
                    for action in ("open:plans", "plan:add", "plan:device:" + device["id"],
                                   "plan:mode:" + mode, "plan:game:" + linked["id"]):
                        chat.press(action)
                    chat.type("1d")
                    count = chat.active()[-1]
                    chat.press("plan:finish")
                    review = chat.active()[-1]
                    self.assertIn(count, chat.bot_rows())
                    self.assertIn("1. tutorial_complete", review["text"])
                    save = next(b.callback_data for row in review["markup"].inline_keyboard for b in row
                                if chat._action(review, b.callback_data) == "plan:confirm")
                    chat.press_data(save, review)
                    chat.press_data(save, review)
                    plans = chat.ui.s.plans.list(7)
                    self.assertEqual(len(plans), 1)
                    self.assertEqual(len(plans[0]["operations"]), 1)
                    self.assertEqual(plans[0]["operations"][0]["status"], "pending")
                    self.assertEqual(len(chat.bot_rows()), 1)
                    self.assertEqual(chat.active(), [chat.visible()[-1]])
                    self.assertIn(TEXT[language]["plan_saved"], chat.active()[-1]["text"])

    def test_other_validated_fields_also_replace_error_and_clear_it(self):
        for language in ("ar", "en"):
            chat = self.make(language)
            chat.press("open:proxies")
            chat.press("proxy:add")
            chat.press("proxy:selectcountry:USA")
            chat.type("Proxy")
            chat.press("proxy:type:SOCKS5")
            prompt = chat.active()[-1]
            for value in ("invalid-endpoint", "also-invalid"):
                chat.type(value)
                self.assertEqual(chat.active()[-1]["id"], prompt["id"])
                self.assertEqual(chat.active()[-1]["text"].count(TEXT[language]["invalid_endpoint"]), 1)
            chat.type("proxy.example:1080")
            self.assertFalse(any(TEXT[language]["invalid_endpoint"] in row["text"] for row in chat.bot_rows()))
            self.assertEqual(chat.ui._state(7)["draft"]["proxy_name"], "Proxy")

    def test_menus_edit_same_message_and_reject_old_revision(self):
        for language in ("ar", "en"):
            chat = self.make(language)
            original = chat.active()[-1]
            old_data = original["markup"].inline_keyboard[0][0].callback_data
            initial_count = len(chat.rows)
            for action in ("open:deposit", "deposit:syriatel", "back", "back", "open:profile", "back",
                           "open:plans", "plan:saved", "back", "back", "open:proxies", "back"):
                chat.press(action)
                self.assertEqual(chat.active()[-1]["id"], original["id"])
                self.assertEqual(len(chat.rows), initial_count)
                self.assertEqual(len(chat.bot_rows()), 1)
            current = chat.active()[-1]["text"]
            chat.press_data(old_data, original)
            self.assertEqual(chat.active()[-1]["text"], current)
            self.assertEqual(len(chat.rows), initial_count)

    def test_uneditable_menu_cleans_before_fallback_and_old_callback_is_disabled(self):
        for language in ("ar", "en"):
            chat = self.make(language)
            original = chat.active()[-1]
            old_data = original["markup"].inline_keyboard[0][0].callback_data
            chat.bot.edit_message_text = AsyncMock(side_effect=BadRequest("message can't be edited"))
            events = []
            original_send, original_delete = chat.chat.send_message, chat.bot.delete_message

            async def send(*args, **kwargs):
                events.append("send")
                return await original_send(*args, **kwargs)

            async def delete(**kwargs):
                events.append("delete")
                return await original_delete(**kwargs)

            chat.chat.send_message = send
            chat.bot.delete_message = delete
            chat.press("open:profile")
            chat.bot.edit_message_text.assert_awaited_once()
            self.assertEqual(events, ["delete", "send"])
            self.assertEqual(len(chat.bot_rows()), 1)
            self.assertIsNone(original["markup"])
            before = len(chat.rows)
            chat.press_data(old_data, original)
            self.assertEqual(len(chat.rows), before)

    def test_identifier_error_edit_failure_still_replaces_one_error_and_removes_it_on_success(self):
        chat = self.make("en")
        self.identifier_prompt(chat, "idfa")
        chat.bot.edit_message_text = AsyncMock(side_effect=BadRequest("message can't be edited"))
        for value in ("bad-one", "bad-two"):
            chat.type(value)
            self.assertEqual(sum(TEXT["en"]["invalid_idfa"] in row["text"] for row in chat.bot_rows()), 1)
        chat.type(IDFA)
        self.assertFalse(any(TEXT["en"]["invalid_idfa"] in row["text"] for row in chat.bot_rows()))
        self.assertEqual(chat.active(), [chat.visible()[-1]])

    def test_failed_error_replacement_is_cleared_by_next_valid_input(self):
        chat = self.make("en")
        self.identifier_prompt(chat, "idfa")
        chat.type("bad-one")
        chat.bot.edit_message_text = AsyncMock(side_effect=BadRequest("message can't be edited"))
        send = chat.chat.send_message
        chat.chat.send_message = AsyncMock(side_effect=NetworkError("connection"))
        chat.type("bad-two")
        self.assertEqual(chat.ui._state(7)["waiting"], "idfa")
        chat.chat.send_message = send
        chat.type(IDFA)
        self.assertFalse(any(TEXT["en"]["invalid_idfa"] in row["text"] for row in chat.bot_rows()))
