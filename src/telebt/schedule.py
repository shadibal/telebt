from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .validation import DEFAULT_VALIDATION_RULES, ValidationRules, interval, ValidationError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def due_dates(mode: str, operations: list[dict], baseline: datetime, shared: str | None = None, *, rules: ValidationRules = DEFAULT_VALIDATION_RULES) -> list[dict]:
    if baseline.tzinfo is None or mode not in {"uniform", "multiple"}:
        raise ValidationError("invalid_schedule")
    base = baseline.astimezone(timezone.utc)
    shared_delta = interval(shared, rules) if mode == "uniform" and shared else None
    if mode == "uniform" and shared_delta is None:
        raise ValidationError("invalid_interval")
    out = []
    for index, operation in enumerate(operations):
        try:
            delta = shared_delta * (index + 1) if shared_delta else interval(operation["interval"], rules)
            due_at = (base + delta).isoformat()
        except OverflowError as exc:
            raise ValidationError("invalid_interval") from exc
        out.append({**operation, "due_at": due_at})
    return sorted(out, key=lambda item: (item["due_at"], operations.index(next(x for x in operations if x["id"] == item["id"]))))


def resume_unattempted(mode: str, operations: list[dict], baseline: datetime, shared: str | None = None, *, rules: ValidationRules = DEFAULT_VALIDATION_RULES) -> list[dict]:
    remaining = [x for x in operations if x["status"] == "pending"]
    updated = due_dates(mode, remaining, baseline, shared, rules=rules)
    by_id = {x["id"]: x for x in updated}
    return [by_id.get(x["id"], x) for x in operations]


def display_time(value: str | datetime, timezone_name: str) -> str:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        if timezone_name == "UTC":
            zone = timezone.utc
        elif timezone_name == "Asia/Damascus":
            zone = timezone(timedelta(hours=3), "Asia/Damascus")
        else:
            raise ValidationError("invalid_timezone") from exc
    date = datetime.fromisoformat(value) if isinstance(value, str) else value
    return date.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
