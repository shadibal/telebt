"""Local-only environment settings. No token or password is logged."""
import os
from dataclasses import dataclass
from pathlib import Path

from .validation import DEFAULT_VALIDATION_RULES, ValidationRules


@dataclass(frozen=True)
class Settings:
    token: str
    bot_username: str
    support_links: tuple[str, str]
    dev_mode: bool
    dev_ids: frozenset[int]
    data_dir: Path
    tiers: dict | None = None
    referral_rate: float = 0.1
    validation_rules: ValidationRules = DEFAULT_VALIDATION_RULES


def _dotenv(path: Path) -> dict[str, str]:
    if not path.exists(): return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def load_settings(project_root: Path | None = None) -> Settings:
    root = project_root or Path.cwd()
    env_file = _dotenv(root / ".env")
    get = lambda key, default="": os.environ.get(key, env_file.get(key, default))
    token = get("TELEGRAM_BOT_TOKEN")
    if not token: raise ValueError("TELEGRAM_BOT_TOKEN is required in .env or environment")
    dev_ids = frozenset(int(x.strip()) for x in get("DEV_TELEGRAM_USER_IDS").split(",") if x.strip().isdigit())
    support = (get("SUPPORT_LINK_1"), get("SUPPORT_LINK_2"))
    for link in support:
        if link and not link.startswith("https://t.me/"): raise ValueError("Support links must be https://t.me/ links")
    support = tuple("" if "/example_support_" in link else link for link in support)
    from .services import TIERS
    tiers = {key: dict(value) for key, value in TIERS.items()}
    for tier in tiers:
        for field in ("price", "days", "limit"):
            raw = get(f"PLAN_{tier.upper()}_{field.upper()}")
            if raw:
                try: value = int(raw)
                except ValueError as exc: raise ValueError(f"Invalid PLAN_{tier.upper()}_{field.upper()}") from exc
                if value <= 0: raise ValueError(f"PLAN_{tier.upper()}_{field.upper()} must be positive")
                tiers[tier][field] = value
    rate_percent = float(get("REFERRAL_RATE_PERCENT", "10"))
    if not 0 <= rate_percent <= 100: raise ValueError("REFERRAL_RATE_PERCENT must be between 0 and 100")
    username = get("BOT_USERNAME").lstrip("@")
    if username == "example_bot": username = ""
    pattern_keys = ("gaid", "idfa", "idfv", "uid", "singular")
    patterns = {f"{kind}_pattern": get(f"VALIDATION_PATTERN_{kind.upper()}", getattr(DEFAULT_VALIDATION_RULES, f"{kind}_pattern")) for kind in pattern_keys}
    raw_max = get("VALIDATION_MAX_INTERVAL_MINUTES", str(DEFAULT_VALIDATION_RULES.max_interval_minutes))
    try:
        max_minutes = int(raw_max)
    except ValueError as exc:
        raise ValueError("VALIDATION_MAX_INTERVAL_MINUTES must be an integer") from exc
    rules = ValidationRules(
        version=get("VALIDATION_RULES_VERSION", DEFAULT_VALIDATION_RULES.version),
        max_interval_minutes=max_minutes,
        **patterns,
    )
    return Settings(token, username, support, get("DEV_MODE").lower() == "true", dev_ids, root / "mock_data", tiers, rate_percent / 100, rules)
