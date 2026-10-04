import re
import string
import unicodedata
from dataclasses import dataclass
from datetime import timedelta


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationRules:
    """Versioned, local shape checks; they do not verify external IDs."""

    version: str = "phase1-v1"
    gaid_pattern: str = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    idfa_pattern: str = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    idfv_pattern: str = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    uid_pattern: str = r"[0-9]{13}-[0-9]{7}"
    singular_pattern: str = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    max_interval_minutes: int = 525600

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("VALIDATION_RULES_VERSION must be nonempty")
        for kind in ("gaid", "idfa", "idfv", "uid", "singular"):
            pattern = getattr(self, f"{kind}_pattern")
            key = f"VALIDATION_PATTERN_{kind.upper()}"
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"{key} must be a nonempty regex")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{key} is not a valid regex") from exc
        if (isinstance(self.max_interval_minutes, bool)
                or not isinstance(self.max_interval_minutes, int)
                or not 1 <= self.max_interval_minutes <= timedelta.max.days * 1440):
            raise ValueError("VALIDATION_MAX_INTERVAL_MINUTES must be a positive safe integer")


DEFAULT_VALIDATION_RULES = ValidationRules()
INTERVAL = re.compile(r"([1-9][0-9]*)([mhd])\Z")
MAX_TEXT = 128


def required(value: str, *, limit: int = MAX_TEXT) -> str:
    value = value.strip()
    if not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValidationError("required_text")
    return value


def identifier(kind: str, value: str, rules: ValidationRules = DEFAULT_VALIDATION_RULES) -> str:
    value = value.strip()
    if not value or kind not in {"gaid", "idfa", "idfv", "uid", "singular"} or not re.fullmatch(getattr(rules, f"{kind}_pattern"), value):
        raise ValidationError("invalid_" + kind)
    # Keep the historical lowercase UUID storage for the default formats.
    # A custom case-sensitive pattern must retain its accepted spelling so
    # the service can validate it again after the UI draft is confirmed.
    return value.lower() if kind != "uid" and getattr(rules, f"{kind}_pattern") == getattr(DEFAULT_VALIDATION_RULES, f"{kind}_pattern") else value


def interval(value: str, rules: ValidationRules = DEFAULT_VALIDATION_RULES) -> timedelta:
    match = INTERVAL.fullmatch(value.strip())
    if not match:
        raise ValidationError("invalid_interval")
    factor = {"m": 1, "h": 60, "d": 1440}[match.group(2)]
    max_count = rules.max_interval_minutes // factor
    count_text = match.group(1)
    if len(count_text) > len(str(max_count)) or (len(count_text) == len(str(max_count)) and count_text > str(max_count)):
        raise ValidationError("invalid_interval")
    return timedelta(minutes=int(count_text) * factor)


def template_fields(template: str) -> list[str]:
    required(template, limit=160)
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ValidationError("invalid_template") from exc
    if any(format_spec or conversion for _, name, format_spec, conversion in parsed if name is not None):
        raise ValidationError("invalid_template")
    fields = [name for _, name, _, _ in parsed if name is not None]
    if len(fields) > 8 or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", name) for name in fields):
        raise ValidationError("invalid_template")
    return fields


def render_template(template: str, values: list[str]) -> str:
    fields = template_fields(template)
    if len(values) != len(fields):
        raise ValidationError("invalid_template")
    clean = [required(v, limit=64).replace("{", "(").replace("}", ")") for v in values]
    iterator = iter(clean)
    return "".join(literal + (next(iterator) if name is not None else "") for literal, name, _, _ in string.Formatter().parse(template))


def search_key(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def endpoint(value: str) -> str:
    value = required(value, limit=255)
    if not re.fullmatch(r"(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9.-]+):[0-9]{1,5}", value):
        raise ValidationError("invalid_endpoint")
    port = int(value.rsplit(":", 1)[1])
    if not 1 <= port <= 65535:
        raise ValidationError("invalid_endpoint")
    return value
