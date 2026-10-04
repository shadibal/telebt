"""Conversation-level tests for the Telegram adapter's visible message timeline."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telebt.config import Settings
from telebt.core.locale import TEXT
from telebt.bot.telegram_adapter import build_application


IDFA = "12345678-1234-1234-1234-123456789abc"
IDFV = "87654321-4321-4321-4321-cba987654321"


class Timeline:
    def __init__(self, language):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), True, frozenset({7}), Path(self.tmp.name)))
        self.ui = self.app.bot_data["ui"]
        self.rows = []
        self.next_id = 1
        self.chat = SimpleNamespace(type="private", id=7, send_message=self.send)
        self.bot = SimpleNamespace(edit_message_text=self.edit, edit_message_reply_markup=self.markup, delete_message=self.delete)
        self.context = SimpleNamespace(args=[], bot=self.bot)
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=self.chat, callback_query=None, message=None)
        self.handlers = [handler.callback for handler in self.app.handlers[0]]
        self.start()
        self.press("lang:" + language)
        self.ui.s.subscriptions.dev_state(7, "active")

    def close(self):
        self.tmp.cleanup()

    async def send(self, text, reply_markup=None):
        row = dict(id=self.next_id, author="bot", text=text, markup=reply_markup, deleted=False)
        self.next_id += 1
        self.rows.append(row)
        return SimpleNamespace(message_id=row["id"])

    async def edit(self, *, chat_id, message_id, text, reply_markup=None):
        row = self.row(message_id)
        row.update(text=text, markup=reply_markup)

    async def markup(self, *, chat_id, message_id, reply_markup=None):
        self.row(message_id)["markup"] = reply_markup

    async def delete(self, *, chat_id, message_id):
        self.row(message_id)["deleted"] = True

    def row(self, message_id):
        return next(row for row in self.rows if row["id"] == message_id)

    def visible(self):
        return [row for row in self.rows if not row["deleted"]]

    def bot_rows(self):
        return [row for row in self.visible() if row["author"] == "bot"]

    def active(self):
        return [row for row in self.bot_rows() if row["markup"] and row["markup"].inline_keyboard]

    def start(self):
        self.update.callback_query = None
        asyncio.run(self.handlers[0](self.update, self.context))

    def press(self, action, *, old_row=None):
        row = old_row or self.active()[-1]
        button = next(button for group in row["markup"].inline_keyboard for button in group if button.callback_data and self._action(row, button.callback_data) == action)
        self.press_data(button.callback_data, row)

    def press_data(self, data, row):
        self.update.callback_query = SimpleNamespace(data=data, answer=self.answer, message=SimpleNamespace(message_id=row["id"]))
        asyncio.run(self.handlers[2](self.update, self.context))
        self.update.callback_query = None

    def _action(self, row, data):
        token, _, index = data.partition(":")
        if row is self.active()[-1]:
            screen = self.ui._state(7)["screen"]
            try:
                return screen.buttons[int(index)].data
            except (ValueError, IndexError):
                return None
        return None

    async def answer(self):
        pass

    def type(self, value):
        self.rows.append(dict(id=self.next_id, author="user", text=value, markup=None, deleted=False))
        self.next_id += 1
        self.update.message = SimpleNamespace(text=value)
        asyncio.run(self.handlers[3](self.update, self.context))
        self.update.message = None


class TelegramTimelineTests(unittest.TestCase):
    def make(self, language):
        timeline = Timeline(language)
        self.addCleanup(timeline.close)
        return timeline

    def test_iphone_steps_stay_visible_and_active_prompt_is_last_in_both_languages(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:devices")
                chat.press("device:add")
                chat.press("device:os:iOS")
                name_prompt = chat.active()[-1]
                chat.type("Test iPhone")
                idfa_prompt = chat.active()[-1]
                chat.type(IDFA)
                idfv_prompt = chat.active()[-1]
                self.assertIn(name_prompt, chat.bot_rows())
                self.assertIn(idfa_prompt, chat.bot_rows())
                self.assertIn(idfv_prompt, chat.bot_rows())
                self.assertEqual(chat.visible()[-1], idfv_prompt)
                self.assertEqual(chat.active(), [idfv_prompt])
                self.assertIn(TEXT[language]["idfv"], idfv_prompt["text"])

    def test_start_during_form_sends_fresh_menu_below_user_text(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:devices")
                chat.press("device:add")
                chat.press("device:os:iOS")
                chat.type("Test iPhone")
                chat.rows.append(dict(id=chat.next_id, author="user", text="/start", markup=None, deleted=False))
                chat.next_id += 1
                chat.start()
                self.assertEqual(chat.visible()[-1]["author"], "bot")
                self.assertIn(TEXT[language]["menu"], chat.visible()[-1]["text"])
                self.assertEqual(len(chat.active()), 1)
                self.assertEqual(chat.ui._state(7)["draft"], {})

    def test_back_reprompts_one_step_and_keeps_corrected_draft(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:devices")
                chat.press("device:add")
                chat.press("device:os:iOS")
                chat.type("Test iPhone")
                first_idfa_prompt = chat.active()[-1]
                chat.type(IDFA)
                chat.press("back")
                self.assertEqual(chat.visible()[-1], chat.active()[-1])
                self.assertIn(TEXT[language]["idfa"], chat.active()[-1]["text"])
                self.assertNotIn(first_idfa_prompt, chat.bot_rows())
                self.assertEqual(chat.ui._state(7)["draft"]["device_name"], "Test iPhone")
                chat.type(IDFV)
                self.assertEqual(chat.ui._state(7)["draft"]["idfa"], IDFV)
                self.assertIn(TEXT[language]["idfv"], chat.active()[-1]["text"])
                self.assertEqual(len(chat.active()), 1)

    def test_invalid_input_replaces_current_prompt_without_losing_earlier_steps(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:devices")
                chat.press("device:add")
                chat.press("device:os:iOS")
                name_prompt = chat.active()[-1]
                chat.type("Test iPhone")
                chat.type("bad idfa")
                self.assertIn(name_prompt, chat.bot_rows())
                self.assertEqual(len([row for row in chat.bot_rows() if TEXT[language]["idfa"] in row["text"]]), 1)
                chat.type(IDFA)
                self.assertEqual(len([row for row in chat.bot_rows() if TEXT[language]["idfa"] in row["text"]]), 1)
                self.assertIn(TEXT[language]["idfv"], chat.active()[-1]["text"])

    def test_proxy_add_keeps_previous_field_prompts_until_review(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:proxies")
                chat.press("proxy:add")
                chat.press("proxy:selectcountry:USA")
                name_prompt = chat.active()[-1]
                chat.type("Test proxy")
                chat.press("proxy:type:SOCKS5")
                endpoint_prompt = chat.active()[-1]
                chat.type("proxy.example:1080")
                username_prompt = chat.active()[-1]
                chat.type("dummy")
                password_prompt = chat.active()[-1]
                self.assertTrue(all(row in chat.bot_rows() for row in (name_prompt, endpoint_prompt, username_prompt, password_prompt)))
                self.assertEqual(chat.visible()[-1], password_prompt)
                self.assertEqual(chat.active(), [password_prompt])
                chat.type("placeholder")
                self.assertEqual(chat.visible()[-1], chat.active()[-1])
                self.assertTrue(all(row in chat.bot_rows() for row in (name_prompt, endpoint_prompt, username_prompt, password_prompt)))

    def test_confirmed_iphone_saves_once_and_old_review_button_is_inert(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:devices")
                chat.press("device:add")
                chat.press("device:os:iOS")
                chat.type("Test iPhone")
                chat.type(IDFA)
                chat.type(IDFV)
                chat.press("device:proxy:none")
                review = chat.active()[-1]
                save_data = review["markup"].inline_keyboard[0][0].callback_data
                chat.press("device:confirm")
                self.assertEqual(len(chat.ui.s.devices.list(7)), 1)
                self.assertEqual(chat.active(), [chat.visible()[-1]])
                self.assertNotIn(review, chat.bot_rows())
                chat.press_data(save_data, review)
                self.assertEqual(len(chat.ui.s.devices.list(7)), 1)

    def test_plan_creation_keeps_choices_and_interval_visible_until_cancel(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                device = chat.ui.s.devices.add(7, "iOS", "Plan phone", IDFA, IDFV)
                linked = chat.ui.s.devices.link_game(7, device["id"], "g2", expected_platform="Adjust")
                chat.press("open:plans")
                chat.press("plan:add")
                choose_device = chat.active()[-1]
                chat.press("plan:device:" + device["id"])
                choose_type = chat.active()[-1]
                chat.press("plan:mode:uniform")
                choose_game = chat.active()[-1]
                chat.press("plan:game:" + linked["id"])
                interval_prompt = chat.active()[-1]
                chat.type("30m")
                self.assertTrue(all(row in chat.bot_rows() for row in (choose_device, choose_type, choose_game, interval_prompt)))
                self.assertEqual(chat.visible()[-1], chat.active()[-1])
                self.assertEqual(len(chat.active()), 1)
                chat.press("cancel")
                self.assertEqual(chat.ui._state(7)["draft"], {})
                self.assertEqual(chat.ui.s.plans.list(7), [])

    def test_plan_back_preserves_entered_interval_for_correction(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                device = chat.ui.s.devices.add(7, "iOS", "Plan phone", IDFA, IDFV)
                linked = chat.ui.s.devices.link_game(7, device["id"], "g2", expected_platform="Adjust")
                chat.press("open:plans")
                chat.press("plan:add")
                chat.press("plan:device:" + device["id"])
                chat.press("plan:mode:uniform")
                chat.press("plan:game:" + linked["id"])
                chat.type("30m")
                chat.press("back")
                self.assertEqual(chat.ui._state(7)["draft"]["shared"], "30m")
                self.assertEqual(chat.visible()[-1], chat.active()[-1])
                self.assertIn(TEXT[language]["shared_interval"], chat.active()[-1]["text"])
                chat.type("45m")
                self.assertEqual(chat.ui._state(7)["draft"]["shared"], "45m")

    def test_cancel_form_discards_draft_and_stale_button_does_nothing(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:devices")
                chat.press("device:add")
                chat.press("device:os:iOS")
                chat.type("Test iPhone")
                old_prompt = chat.active()[-1]
                old_data = old_prompt["markup"].inline_keyboard[0][0].callback_data
                chat.press("cancel")
                self.assertEqual(chat.ui._state(7)["draft"], {})
                self.assertEqual(chat.ui.s.devices.list(7), [])
                self.assertEqual(chat.active(), [chat.visible()[-1]])
                before = len(chat.rows)
                chat.press_data(old_data, old_prompt)
                self.assertEqual(len(chat.rows), before)
                self.assertIn(TEXT[language]["menu"], chat.active()[-1]["text"])

    def test_variable_back_correction_keeps_one_bottom_prompt_and_all_values(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                device = chat.ui.s.devices.add(7, "iOS", "Event phone", IDFA, IDFV)
                linked = chat.ui.s.devices.link_game(7, device["id"], "g2", expected_platform="Adjust")
                for action in ("open:devices", "device:view:" + device["id"], "link:view:" + linked["id"], "event:start:" + linked["id"] + ":custom"):
                    chat.press(action)
                chat.type("stage_{x}_{name}")
                chat.type("first")
                chat.type("second")
                review = chat.active()[-1]
                chat.press("back")
                self.assertNotIn(review, chat.bot_rows())
                chat.press("back")
                self.assertEqual(chat.ui._state(7)["draft"]["values"], ["first", "second"])
                self.assertEqual(chat.active(), [chat.visible()[-1]])
                self.assertIn(": x", chat.active()[-1]["text"])
                chat.type("corrected")
                self.assertEqual(chat.ui._state(7)["draft"]["values"], ["corrected", "second"])
                chat.type("second")
                self.assertIn("stage_corrected_second", chat.active()[-1]["text"])
                chat.press("cancel")
                self.assertEqual(len(chat.bot_rows()), 1)
                self.assertEqual([r["text"] for r in chat.visible() if r["author"] == "user"],
                                 ["stage_{x}_{name}", "first", "second", "corrected", "second"])

    def test_failed_search_retry_and_proxy_menus_replace_old_screens(self):
        for language in ("en", "ar"):
            with self.subTest(language=language):
                chat = self.make(language)
                chat.press("open:search")
                chat.press("search:os:iOS")
                chat.type("unfindable game")
                self.assertIn(TEXT[language]["not_found"], chat.active()[-1]["text"])
                self.assertEqual(chat.visible()[-1], chat.active()[-1])
                chat.press("search:again")
                self.assertIn(TEXT[language]["game_name"], chat.active()[-1]["text"])
                self.assertEqual(len(chat.bot_rows()), 1)
                chat.press("cancel")
                chat.ui.s.proxies.add(7, {"country": "USA", "name": "Test proxy", "type": "SOCKS5", "endpoint": "proxy.example:1080", "username": "test", "password": "dummy"})
                chat.press("open:proxies")
                self.assertEqual(len(chat.bot_rows()), 1)
                chat.press("proxy:country:USA")
                self.assertEqual(len(chat.bot_rows()), 1)
                proxy_id = chat.ui.s.proxies.list(7)[0]["id"]
                chat.press("proxy:view:" + proxy_id)
                self.assertEqual(len(chat.bot_rows()), 1)
                chat.press("back")
                self.assertIn("USA", chat.active()[-1]["text"])
                self.assertEqual(len(chat.bot_rows()), 1)
                chat.press("back")
                self.assertIn(TEXT[language]["proxies"], chat.active()[-1]["text"])
                self.assertEqual(len(chat.bot_rows()), 1)
                self.assertEqual(chat.visible()[-1], chat.active()[-1])
