"""The only module coupled to python-telegram-bot."""
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import NetworkError, TelegramError
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from .config import Settings
from .services import MockServices
from .storage import JsonStore, StorageError
from .ui import BotUI, Screen


logger = logging.getLogger(__name__)


def keyboard(screen: Screen, revision: str | None = None) -> InlineKeyboardMarkup | None:
    if not screen.buttons: return None
    buttons = []
    for index, button in enumerate(screen.buttons):
        if button.url: buttons.append(InlineKeyboardButton(button.label, url=button.url))
        else: buttons.append(InlineKeyboardButton(button.label, callback_data=f"{revision}:{index}" if revision is not None else button.data))
    rows = [[button] for button in buttons]
    if screen.kind == "menu":
        rows = [buttons[:1]] + [buttons[index:index + 2] for index in range(1, len(buttons), 2)]
    return InlineKeyboardMarkup(rows)


def chunks(value: str, limit: int = 4000) -> list[str]:
    return [value[index:index + limit] for index in range(0, len(value), limit)] or [""]


def build_application(settings: Settings) -> Application:
    store = JsonStore(settings.data_dir)
    services = MockServices(store, tiers=settings.tiers, dev_mode=settings.dev_mode, dev_ids=settings.dev_ids, referral_rate=settings.referral_rate, validation_rules=settings.validation_rules)
    ui = BotUI(services, settings.bot_username, settings.support_links, settings.dev_mode, settings.dev_ids)
    application = (ApplicationBuilder().token(settings.token)
                   .connect_timeout(10).read_timeout(20).write_timeout(20).pool_timeout(10)
                   .get_updates_connect_timeout(10).get_updates_read_timeout(30).build())
    application.bot_data["ui"] = ui
    latest_messages: dict[int, int] = {}
    displayed_messages: dict[int, list[tuple[tuple[str, ...], list[int]]]] = {}
    try:
        for record in store.read("telegram_frames"):
            if not isinstance(record, dict):
                continue
            user_id, chat_id, ids = record.get("user_id"), record.get("chat_id"), record.get("ids")
            if (type(user_id) is int and type(chat_id) is int and user_id == chat_id
                    and isinstance(ids, list) and 0 < len(ids) <= 1000
                    and all(type(message_id) is int and message_id > 0 for message_id in ids)):
                displayed_messages[user_id] = [(('restored',), ids)]
    except StorageError as exc:
        logger.warning("Telegram frame recovery failed: %s", type(exc).__name__)
    revisions: dict[int, int] = {}
    active_actions: dict[int, dict[str, str]] = {}
    consumed_revisions: dict[int, int] = {}
    input_errors: dict[int, tuple[int, Screen]] = {}

    def private(update: Update) -> bool:
        return getattr(update.effective_chat, "type", "private") == "private"

    wizard_flows = ui.WIZARD_FLOWS

    async def send(update: Update, screen: Screen, context: ContextTypes.DEFAULT_TYPE, *, before_flow=None, action=""):
        user_id = update.effective_user.id
        revision = revisions.get(user_id, 0) + 1
        token = format(revision, "x")
        actions = {str(index): button.data for index, button in enumerate(screen.buttons) if button.data is not None}
        parts = chunks(screen.text)
        old_frames = displayed_messages.get(user_id, [])
        query = update.callback_query
        after_flow = ui._state(user_id)["flow"]
        keep_steps = before_flow in wizard_flows and after_flow in wizard_flows and action not in {"/start", "cancel"}
        ordinary = before_flow not in wizard_flows and after_flow not in wizard_flows and action not in {"/start", "text"}

        async def clean(message_ids, *, delete):
            for previous_id in message_ids:
                old_message = getattr(query, "message", None)
                same_query_message = getattr(old_message, "message_id", None) == previous_id
                try:
                    if getattr(context, "bot", None) is not None:
                        await context.bot.edit_message_reply_markup(chat_id=update.effective_chat.id, message_id=previous_id, reply_markup=None)
                    elif same_query_message and hasattr(old_message, "edit_reply_markup"):
                        await old_message.edit_reply_markup(reply_markup=None)
                except TelegramError as exc:
                    logger.warning("Telegram keyboard cleanup failed: %s", type(exc).__name__)
                if delete:
                    try:
                        if getattr(context, "bot", None) is not None:
                            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=previous_id)
                        elif same_query_message and hasattr(old_message, "delete"):
                            await old_message.delete()
                    except TelegramError as exc:
                        logger.warning("Telegram delete failed: %s", type(exc).__name__)

        async def edit(message_id, text, markup):
            if getattr(context, "bot", None) is not None and hasattr(context.bot, "edit_message_text"):
                await context.bot.edit_message_text(chat_id=update.effective_chat.id, message_id=message_id, text=text, reply_markup=markup)
            elif query is not None and getattr(getattr(query, "message", None), "message_id", None) == message_id and hasattr(query, "edit_message_text"):
                await query.edit_message_text(text=text, reply_markup=markup)
            else:
                return False
            return True

        def remember(message_id, frames):
            latest_messages[user_id] = message_id
            displayed_messages[user_id] = frames
            revisions[user_id] = revision
            active_actions[user_id] = actions
            try:
                records = [{"user_id": owner, "chat_id": owner,
                            "ids": [saved for _, ids in saved_frames for saved in ids]}
                           for owner, saved_frames in displayed_messages.items()]
                store.write("telegram_frames", records)
            except StorageError as exc:
                logger.warning("Telegram frame persistence failed: %s", type(exc).__name__)

        waiting = ui._state(user_id)["waiting"]
        frame_key = ("waiting", after_flow, waiting) if after_flow in wizard_flows and waiting else (screen.text, screen.kind)
        field_error = keep_steps and screen.kind == "error" and screen.input_prompt is not None
        previous_error = input_errors.get(user_id)
        if not field_error: input_errors.pop(user_id, None)
        if previous_error and keep_steps and not field_error and action != "back":
            error_id, prompt = previous_error
            try:
                restored = await edit(error_id, prompt.text, None)
            except TelegramError as exc:
                logger.warning("Telegram field error cleanup failed: %s", type(exc).__name__)
                restored = False
            if not restored:
                await clean([error_id], delete=True)
                old_frames = [(key, ids) for key, ids in old_frames if error_id not in ids]

        sent_ids = []
        try:
            if (ordinary or field_error) and old_frames and len(old_frames[-1][1]) == 1 and len(parts) == 1 and (field_error or len(old_frames) == 1):
                current_id = old_frames[-1][1][0]
                try:
                    edited = await edit(current_id, parts[0], keyboard(screen, token))
                except TelegramError as exc:
                    logger.warning("Telegram edit failed: %s", type(exc).__name__)
                    edited = False
                if edited:
                    remember(current_id, old_frames[:-1] + [(frame_key, [current_id])])
                    if field_error: input_errors[user_id] = (current_id, screen.input_prompt)
                    return
            # A menu that cannot be edited is retired before sending its replacement.
            if ordinary and old_frames:
                await clean([old for _, ids in old_frames for old in ids], delete=True)
                old_frames = []
            for part in parts[:-1]:
                chunk_message = await update.effective_chat.send_message(part)
                if isinstance(getattr(chunk_message, "message_id", None), int):
                    sent_ids.append(chunk_message.message_id)
            result = await update.effective_chat.send_message(parts[-1], reply_markup=keyboard(screen, token))
            message_id = getattr(result, "message_id", None)
            if isinstance(message_id, int):
                sent_ids.append(message_id)
                frame = (frame_key, sent_ids)
                retained = list(old_frames) if keep_steps else []
                to_delete = []
                if keep_steps and action == "back":
                    if retained:
                        to_delete.extend(retained.pop()[1])
                    for index in range(len(retained) - 1, -1, -1):
                        if retained[index][0] == frame[0]:
                            to_delete.extend(retained.pop(index)[1])
                            break
                elif keep_steps and screen.kind == "error" and retained:
                    to_delete.extend(retained.pop()[1])
                elif keep_steps and old_frames:
                    await clean(old_frames[-1][1][-1:], delete=False)
                if not keep_steps:
                    to_delete = [old for _, ids in old_frames for old in ids]
                remember(message_id, retained + [frame])
                if field_error: input_errors[user_id] = (message_id, screen.input_prompt)
                await clean(to_delete, delete=True)
        except TelegramError as exc:
            logger.warning("Telegram send failed: %s", type(exc).__name__)
            if getattr(context, "bot", None) is not None:
                for partial_id in sent_ids:
                    try:
                        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=partial_id)
                    except TelegramError as cleanup_exc:
                        logger.warning("Telegram partial cleanup failed: %s", type(cleanup_exc).__name__)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not private(update): return
        await send(update, ui.start(update.effective_user.id, context.args[0] if context.args else None), context, action="/start")

    async def dev(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not private(update): return
        await send(update, ui.click(update.effective_user.id, "dev:menu"), context)

    async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not private(update): return
        query = update.callback_query
        try:
            await query.answer()
        except NetworkError as exc:
            logger.warning("Telegram callback acknowledgement failed: %s", type(exc).__name__)
            return
        message_id = getattr(getattr(query, "message", None), "message_id", None)
        current_id = latest_messages.get(update.effective_user.id)
        user_id = update.effective_user.id
        if current_id is not None and not isinstance(message_id, int):
            return
        if isinstance(message_id, int):
            if current_id != message_id or consumed_revisions.get(user_id) == revisions.get(user_id, 0):
                return
            token, separator, index = (query.data or "").partition(":")
            if not separator or token != format(revisions.get(user_id, 0), "x"):
                return
            action = active_actions.get(user_id, {}).get(index)
            if action is None:
                return
            consumed_revisions[user_id] = revisions[user_id]
        else:
            action = query.data or ""
        before_flow = ui._state(user_id)["flow"]
        await send(update, ui.click(user_id, action), context, before_flow=before_flow, action=action)

    async def message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not private(update): return
        user_id = update.effective_user.id
        before_flow = ui._state(user_id)["flow"]
        await send(update, ui.text(user_id, update.message.text or ""), context, before_flow=before_flow, action="text")

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
        error = context.error
        logger.error("Telegram update failed: %s", type(error).__name__)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("dev", dev))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message))
    application.add_error_handler(on_error)
    return application
