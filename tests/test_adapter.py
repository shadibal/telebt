import asyncio
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telebt.config import Settings, load_settings
from telebt.ui import Button, Screen


@unittest.skipUnless(importlib.util.find_spec("telegram"), "python-telegram-bot is not installed")
class AdapterTests(unittest.TestCase):
    def test_start_after_process_restart_cleans_previous_bot_screen(self):
        from telebt.telegram_adapter import build_application

        with tempfile.TemporaryDirectory() as directory:
            settings = Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory))
            sent = 10

            async def send_message(*args, **kwargs):
                nonlocal sent
                sent += 1
                return SimpleNamespace(message_id=sent)

            bot = SimpleNamespace(edit_message_reply_markup=AsyncMock(), delete_message=AsyncMock())
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", id=7, send_message=send_message), callback_query=None, message=None)
            context = SimpleNamespace(args=[], bot=bot)

            first = build_application(settings)
            asyncio.run(first.handlers[0][0].callback(update, context))
            second = build_application(settings)
            asyncio.run(second.handlers[0][0].callback(update, context))

            self.assertEqual(sent, 12)
            bot.delete_message.assert_awaited_once_with(chat_id=7, message_id=11)

    def test_application_builds_without_network_or_real_token(self):
        from telebt.telegram_adapter import build_application, keyboard, chunks
        with tempfile.TemporaryDirectory() as directory:
            config = Settings("123456:TESTTOKEN", "example_bot", ("https://t.me/example1", "https://t.me/example2"), False, frozenset(), Path(directory))
            app = build_application(config)
            self.assertEqual(len(app.handlers[0]), 4)
            markup = keyboard(Screen("test", [Button("Next", "menu"), Button("Support", url="https://t.me/example1")]))
            self.assertEqual(markup.inline_keyboard[0][0].callback_data, "menu")
            self.assertEqual(markup.inline_keyboard[1][0].url, "https://t.me/example1")
            self.assertEqual("".join(chunks("abc\ndef", 4)), "abc\ndef")
            self.assertTrue(all(len(part) <= 4 for part in chunks("abc\ndef", 4)))

    def test_offline_telegram_handlers_drive_both_languages_and_text_input(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory))
            app = build_application(settings)
            send_message = AsyncMock()
            answer = AsyncMock()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=7),
                effective_chat=SimpleNamespace(send_message=send_message),
                callback_query=None,
                message=None,
            )
            context = SimpleNamespace(args=[])
            start, _, callback, message = app.handlers[0]
            asyncio.run(start.callback(update, context))
            self.assertIn("اختر اللغة", send_message.call_args.args[0])

            update.callback_query = SimpleNamespace(data="lang:en", answer=answer)
            asyncio.run(callback.callback(update, context))
            self.assertIn("Main menu", send_message.call_args.args[0])
            update.callback_query.data = "lang:ar"
            asyncio.run(callback.callback(update, context))
            self.assertIn("القائمة الرئيسية", send_message.call_args.args[0])

            update.callback_query.data = "search:os:Android"
            asyncio.run(callback.callback(update, context))
            update.message = SimpleNamespace(text="sample")
            asyncio.run(message.callback(update, context))
            self.assertIn("Sample Quest", send_message.call_args.args[0])
            self.assertEqual(answer.await_count, 3)

    def test_callback_timeout_does_not_execute_action_or_escape(self):
        from telegram.error import TimedOut
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            ui = app.bot_data["ui"]
            ui.start(7)
            send_message = AsyncMock()
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", send_message=send_message), callback_query=SimpleNamespace(data="lang:en", answer=AsyncMock(side_effect=TimedOut())), message=None)
            callback = app.handlers[0][2].callback
            asyncio.run(callback(update, SimpleNamespace()))
            self.assertIsNone(ui.s.users.get(7)["language"])
            send_message.assert_not_awaited()
            self.assertTrue(app.error_handlers)

    def test_send_network_error_is_handled_without_second_action(self):
        from telegram.error import NetworkError
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            ui = app.bot_data["ui"]
            ui.start(7)
            send_message = AsyncMock(return_value=SimpleNamespace(message_id=1))
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", send_message=send_message), callback_query=SimpleNamespace(data="lang:en", answer=AsyncMock(), message=SimpleNamespace(message_id=1)), message=None)
            asyncio.run(app.handlers[0][0].callback(update, SimpleNamespace(args=[])))
            update.callback_query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
            send_message.reset_mock()
            send_message.side_effect = NetworkError("secret connection detail")
            asyncio.run(app.handlers[0][2].callback(update, SimpleNamespace()))
            asyncio.run(app.handlers[0][2].callback(update, SimpleNamespace()))
            self.assertEqual(ui.s.users.get(7)["language"], "en")
            self.assertEqual(send_message.await_count, 1)

    def test_old_callback_does_not_change_language_or_navigation(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            sent = 0
            markups = []
            async def send_message(*args, **kwargs):
                nonlocal sent
                sent += 1
                markups.append(kwargs["reply_markup"])
                return SimpleNamespace(message_id=sent)
            chat = SimpleNamespace(type="private", send_message=send_message)
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=chat, callback_query=None, message=None)
            asyncio.run(app.handlers[0][0].callback(update, SimpleNamespace(args=[])))
            update.callback_query = SimpleNamespace(data=markups[0].inline_keyboard[1][0].callback_data, answer=AsyncMock(), message=SimpleNamespace(message_id=1))
            asyncio.run(app.handlers[0][2].callback(update, SimpleNamespace()))
            self.assertEqual(sent, 2)
            update.callback_query.data = "lang:ar"
            asyncio.run(app.handlers[0][2].callback(update, SimpleNamespace()))
            self.assertEqual(sent, 2)
            self.assertEqual(app.bot_data["ui"].s.users.get(7)["language"], "en")

    def test_callback_edits_same_screen_and_rejects_previous_keyboard_revision(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            send_message = AsyncMock(side_effect=[SimpleNamespace(message_id=11), SimpleNamespace(message_id=12), SimpleNamespace(message_id=13)])
            chat = SimpleNamespace(type="private", id=7, send_message=send_message)
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=chat, callback_query=None, message=None)
            start, _, callback, _ = app.handlers[0]
            asyncio.run(start.callback(update, SimpleNamespace(args=[])))
            first = send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
            query = SimpleNamespace(data=first, answer=AsyncMock(), message=SimpleNamespace(message_id=11), edit_message_text=AsyncMock())
            update.callback_query = query
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertEqual(send_message.await_count, 1)
            query.edit_message_text.assert_awaited_once()
            self.assertEqual(app.bot_data["ui"].s.users.get(7)["language"], "en")
            query.data = first
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertEqual(send_message.await_count, 1)
            self.assertEqual(query.edit_message_text.await_count, 1)
            next_data = query.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            query.data = next_data
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertEqual(send_message.await_count, 1)
            self.assertEqual(query.edit_message_text.await_count, 2)

    def test_uneditable_screen_sends_replacement_and_cleans_only_old_bot_message(self):
        from telegram.error import BadRequest
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            send_message = AsyncMock(side_effect=[SimpleNamespace(message_id=11), SimpleNamespace(message_id=12)])
            old_message = SimpleNamespace(message_id=11, edit_reply_markup=AsyncMock(), delete=AsyncMock())
            query = SimpleNamespace(data="", answer=AsyncMock(), message=old_message, edit_message_text=AsyncMock(side_effect=BadRequest("cannot edit")))
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", id=7, send_message=send_message), callback_query=query, message=None)
            start, _, callback, _ = app.handlers[0]
            asyncio.run(start.callback(update, SimpleNamespace(args=[])))
            query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertEqual(send_message.await_count, 2)
            old_message.edit_reply_markup.assert_awaited_once()
            old_message.delete.assert_awaited_once()
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertEqual(send_message.await_count, 2)

    def test_text_input_sends_active_bot_screen_below_user_message(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            send_message = AsyncMock(side_effect=[SimpleNamespace(message_id=i) for i in range(11, 16)])
            chat = SimpleNamespace(type="private", id=7, send_message=send_message)
            query = SimpleNamespace(data="", answer=AsyncMock(), message=SimpleNamespace(message_id=11))
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=chat, callback_query=None, message=None)
            start, _, callback, message = app.handlers[0]
            asyncio.run(start.callback(update, SimpleNamespace(args=[])))
            query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
            update.callback_query = query
            asyncio.run(callback.callback(update, SimpleNamespace()))
            query.message = SimpleNamespace(message_id=12)
            query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            asyncio.run(callback.callback(update, SimpleNamespace()))
            query.message = SimpleNamespace(message_id=13)
            query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            asyncio.run(callback.callback(update, SimpleNamespace()))
            update.callback_query = None
            update.message = SimpleNamespace(text="jj")
            bot = SimpleNamespace(edit_message_reply_markup=AsyncMock(), delete_message=AsyncMock())
            asyncio.run(message.callback(update, SimpleNamespace(bot=bot)))
            self.assertEqual(send_message.await_count, 5)
            self.assertIn("Game not found", send_message.call_args.args[0])

    def test_failed_keyboard_cleanup_still_replaces_screen_and_blocks_old_button(self):
        from telegram.error import BadRequest
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            send_message = AsyncMock(side_effect=[SimpleNamespace(message_id=11), SimpleNamespace(message_id=12)])
            old_message = SimpleNamespace(message_id=11, edit_reply_markup=AsyncMock(side_effect=BadRequest("too old")), delete=AsyncMock(side_effect=BadRequest("too old")))
            query = SimpleNamespace(data="", answer=AsyncMock(), message=old_message, edit_message_text=AsyncMock(side_effect=BadRequest("too old")))
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", id=7, send_message=send_message), callback_query=query, message=None)
            start, _, callback, _ = app.handlers[0]
            asyncio.run(start.callback(update, SimpleNamespace(args=[])))
            query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
            asyncio.run(callback.callback(update, SimpleNamespace()))
            old_message.delete.assert_awaited_once()
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertEqual(send_message.await_count, 2)
            old_message.edit_reply_markup.assert_awaited_once()

    def test_callback_without_message_identity_cannot_change_active_screen(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            send_message = AsyncMock(return_value=SimpleNamespace(message_id=11))
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", id=7, send_message=send_message), callback_query=None, message=None)
            start, _, callback, _ = app.handlers[0]
            asyncio.run(start.callback(update, SimpleNamespace(args=[])))
            data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[1][0].callback_data
            update.callback_query = SimpleNamespace(data=data, answer=AsyncMock(), message=None)
            asyncio.run(callback.callback(update, SimpleNamespace()))
            self.assertIsNone(app.bot_data["ui"].s.users.get(7)["language"])
            self.assertEqual(send_message.await_count, 1)

    def test_long_screen_replacement_cleans_all_previous_bot_chunks(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            ui = app.bot_data["ui"]
            send_message = AsyncMock(side_effect=[SimpleNamespace(message_id=10), SimpleNamespace(message_id=11), SimpleNamespace(message_id=12)])
            chat = SimpleNamespace(type="private", id=7, send_message=send_message)
            old_message = SimpleNamespace(message_id=11, edit_reply_markup=AsyncMock(), delete=AsyncMock())
            query = SimpleNamespace(data="", answer=AsyncMock(), message=old_message, edit_message_text=AsyncMock())
            bot = SimpleNamespace(edit_message_reply_markup=AsyncMock(), delete_message=AsyncMock())
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=chat, callback_query=None, message=None)
            start, _, callback, _ = app.handlers[0]
            with patch.object(ui, "start", return_value=Screen("x" * 5000, [Button("Menu", "menu")])):
                asyncio.run(start.callback(update, SimpleNamespace(args=[], bot=bot)))
            self.assertEqual(send_message.await_count, 2)
            query.data = send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
            update.callback_query = query
            asyncio.run(callback.callback(update, SimpleNamespace(bot=bot)))
            self.assertEqual(send_message.await_count, 3)
            query.edit_message_text.assert_not_awaited()
            self.assertEqual([call.kwargs["message_id"] for call in bot.delete_message.await_args_list], [10, 11])

    def test_failed_long_screen_send_removes_known_partial_bot_chunk(self):
        from telegram.error import NetworkError
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            ui = app.bot_data["ui"]
            send_message = AsyncMock(side_effect=[SimpleNamespace(message_id=10), NetworkError("connection")])
            bot = SimpleNamespace(delete_message=AsyncMock())
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", id=7, send_message=send_message), callback_query=None, message=None)
            with patch.object(ui, "start", return_value=Screen("x" * 5000, [Button("Menu", "menu")])):
                asyncio.run(app.handlers[0][0].callback(update, SimpleNamespace(args=[], bot=bot)))
            bot.delete_message.assert_awaited_once_with(chat_id=7, message_id=10)

    def test_group_chat_cannot_display_proxy_password(self):
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            send_message = AsyncMock()
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="group", send_message=send_message), callback_query=SimpleNamespace(data="open:proxies", answer=AsyncMock()), message=None)
            asyncio.run(app.handlers[0][2].callback(update, SimpleNamespace()))
            send_message.assert_not_awaited()
            self.assertFalse(app.bot_data["ui"].sessions)

    def test_disconnect_ack_and_error_handler_do_not_log_details(self):
        from telegram.error import NetworkError
        from telebt.telegram_adapter import build_application
        with tempfile.TemporaryDirectory() as directory:
            app = build_application(Settings("123456:TESTTOKEN", "", ("", ""), False, frozenset(), Path(directory)))
            ui = app.bot_data["ui"]
            ui.start(7)
            update = SimpleNamespace(effective_user=SimpleNamespace(id=7), effective_chat=SimpleNamespace(type="private", send_message=AsyncMock()), callback_query=SimpleNamespace(data="lang:en", answer=AsyncMock(side_effect=NetworkError("secret credentials"))), message=None)
            with self.assertLogs("telebt.telegram_adapter", level="WARNING") as captured:
                asyncio.run(app.handlers[0][2].callback(update, SimpleNamespace()))
                asyncio.run(next(iter(app.error_handlers))(update, SimpleNamespace(error=NetworkError("secret credentials"))))
            self.assertNotIn("secret credentials", "\n".join(captured.output))
            self.assertIsNone(ui.s.users.get(7)["language"])


class ConfigTests(unittest.TestCase):
    def test_missing_token_fails_with_clear_configuration_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
                    load_settings(Path(directory))

    def test_local_dev_ids_are_loaded_from_env_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ".env").write_text("TELEGRAM_BOT_TOKEN=123456:TEST\nDEV_MODE=true\nDEV_TELEGRAM_USER_IDS=42, 43\nPLAN_DAILY_LIMIT=17\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(path)
            self.assertTrue(settings.dev_mode)
            self.assertEqual(settings.dev_ids, frozenset({42, 43}))
            self.assertEqual(settings.tiers["daily"]["limit"], 17)

    def test_example_support_links_are_not_exposed_as_live(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / ".env").write_text("TELEGRAM_BOT_TOKEN=123456:TEST\nBOT_USERNAME=example_bot\nSUPPORT_LINK_1=https://t.me/example_support_one\nSUPPORT_LINK_2=https://t.me/example_support_two\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                settings = load_settings(path)
            self.assertEqual(settings.support_links, ("", ""))
            self.assertEqual(settings.bot_username, "")

    def test_nonfinite_referral_rate_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in ("nan", "inf", "-inf"):
                with self.subTest(value=value), patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:TEST", "REFERRAL_RATE_PERCENT": value}, clear=True):
                    with self.assertRaisesRegex(ValueError, "REFERRAL_RATE_PERCENT"):
                        load_settings(Path(directory))


if __name__ == "__main__":
    unittest.main()
