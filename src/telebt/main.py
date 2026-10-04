from pathlib import Path

from .config import load_settings
from .bot.telegram_adapter import build_application


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    settings = load_settings(root)
    build_application(settings).run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
