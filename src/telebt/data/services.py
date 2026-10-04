import calendar
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from ..core.schedule import due_dates, resume_unattempted, utc_now
from .storage import JsonStore
from ..core.validation import DEFAULT_VALIDATION_RULES, ValidationRules, ValidationError, endpoint, identifier, interval, render_template, required, search_key, template_fields


class PermissionError(RuntimeError):
    pass


def uid() -> str:
    return uuid4().hex[:12]


def one(store: JsonStore, collection: str, item_id: str, user_id: int | None = None) -> dict:
    for item in store.read(collection):
        if item["id"] == item_id and (user_id is None or item.get("user_id") == user_id):
            return item
    raise KeyError(item_id)


def add_month(value: datetime) -> datetime:
    year, month = value.year, value.month + 1
    if month == 13:
        year, month = year + 1, 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


DEFAULT_CATALOG = [
    {"id": "g1", "name": "Sample Quest", "os": "Android", "platform": "AppsFlyer", "normal": ["tutorial_complete", "reach_level_{x}", "stage_{x}_{name}"], "purchase": ["af_purchase", "purchase_{x}"]},
    {"id": "g2", "name": "Sample Runner", "os": "iOS", "platform": "Adjust", "normal": ["tutorial_complete", "reach_level_{x}"], "purchase": ["purchase_{x}"]},
    {"id": "g3", "name": "Sample Garden", "os": "Android", "platform": "Singular", "normal": ["tutorial_complete"], "purchase": ["purchase_{x}"]},
    {"id": "g4", "name": "Sample Castle", "os": "iOS", "platform": "AppsFlyer", "normal": ["tutorial_complete", "stage_{x}_{name}"], "purchase": ["af_purchase"]},
    {"id": "g5", "name": "Sample Empty", "os": "Android", "platform": "Adjust", "normal": [], "purchase": []},
    {"id": "g6", "name": "Sample Stars", "os": "Android", "platform": "AppsFlyer", "normal": ["tutorial_complete"], "purchase": []},
]

TIERS = {"daily": {"price": 1, "days": 1, "limit": 200}, "weekly": {"price": 7, "days": 7, "limit": 250}, "monthly": {"price": 25, "days": 30, "limit": 300}}


class Users:
    def __init__(self, store): self.store = store
    def get(self, user_id):
        found = next((x for x in self.store.read("users") if x["id"] == str(user_id)), None)
        if found: return found
        return self.store.add("users", {"id": str(user_id), "language": None, "timezone": "Asia/Damascus"})
    def language(self, user_id, value):
        if value not in {"ar", "en"}: raise ValidationError("invalid_language")
        user = self.get(user_id); user["language"] = value
        return self.store.replace("users", user)
    def timezone(self, user_id, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try: ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            if value not in {"Asia/Damascus", "UTC"}: raise ValidationError("invalid_timezone") from exc
        user = self.get(user_id); user["timezone"] = value
        return self.store.replace("users", user)


class Catalog:
    def __init__(self, store):
        self.store = store
        if not self.store.read("catalog_games"): self.store.write("catalog_games", DEFAULT_CATALOG)
    def search(self, query, os, platform=None):
        if os not in {"Android", "iOS"}: raise ValidationError("invalid_os")
        if platform and platform not in {"AppsFlyer", "Adjust", "Singular"}: raise ValidationError("invalid_platform")
        key = search_key(required(query))
        return [x for x in self.store.read("catalog_games") if x["os"] == os and (not platform or x["platform"] == platform) and key in search_key(x["name"])]
    def get(self, game_id): return one(self.store, "catalog_games", game_id)


class Subscriptions:
    def __init__(self, services): self.s = services
    def status(self, user_id):
        return next((x for x in self.s.store.read("subscriptions") if x["id"] == str(user_id)), {"id": str(user_id), "state": "new", "tier": None, "expires_at": None, "grace_until": None, "queue": [], "used": 0, "window_start": None})
    def _save(self, data):
        if any(x["id"] == data["id"] for x in self.s.store.read("subscriptions")): return self.s.store.replace("subscriptions", data)
        return self.s.store.add("subscriptions", data)
    def require_active(self, user_id):
        if self.status(user_id)["state"] != "active": raise PermissionError("subscription_required")
    def require_quota(self, user_id):
        self.require_active(user_id)
        data = self.status(user_id)
        if data["used"] >= self.s.tiers[data["tier"]]["limit"]: raise PermissionError("quota_exhausted")
    def dev_state(self, user_id, state):
        self.s.require_developer(user_id)
        if state not in {"new", "active", "expired"}: raise ValidationError("invalid_state")
        if state == "new": self.s.purge_operational(user_id)
        data = self.status(user_id)
        now = utc_now()
        data.update(state=state, tier="monthly" if state != "new" else None, expires_at=(now + timedelta(days=self.s.tiers["monthly"]["days"])).isoformat() if state == "active" else now.isoformat() if state == "expired" else None, grace_until=add_month(now).isoformat() if state == "expired" else None, window_start=now.isoformat() if state == "active" else None, used=0, queue=[])
        self._save(data)
        return data
    def dev_expire(self, user_id, when):
        self.s.require_developer(user_id)
        if when.tzinfo is None: raise ValidationError("invalid_time")
        data = self.status(user_id); data.update(state="expired", expires_at=when.isoformat(), grace_until=add_month(when).isoformat())
        self._save(data)
        return data
    def dev_advance(self, user_id, now):
        self.s.require_developer(user_id)
        data = self.status(user_id)
        if now.tzinfo is None: raise ValidationError("invalid_time")
        while data["state"] == "active" and data["expires_at"] and now >= datetime.fromisoformat(data["expires_at"]):
            transition = datetime.fromisoformat(data["expires_at"])
            if data["queue"]:
                next_tier = data["queue"].pop(0)["tier"]
                data.update(tier=next_tier, expires_at=(transition + timedelta(days=self.s.tiers[next_tier]["days"])).isoformat(), window_start=transition.isoformat(), used=0)
                self._save(data)
            else:
                data = self.dev_expire(user_id, transition)
        if data["state"] == "active" and data["window_start"]:
            window = datetime.fromisoformat(data["window_start"])
            elapsed = (now - window) // timedelta(hours=24)
            if elapsed > 0:
                was_exhausted = data["used"] >= self.s.tiers[data["tier"]]["limit"]
                next_window = window + timedelta(hours=24) * elapsed
                data.update(window_start=(window + timedelta(hours=24) * elapsed).isoformat(), used=0)
                self._save(data)
                if was_exhausted: self.s.rebase_pending(user_id, next_window)
        if data["state"] == "expired" and now >= datetime.fromisoformat(data["grace_until"]):
            self.s.purge_operational(user_id)
            data.update(state="new", tier=None, expires_at=None, grace_until=None, used=0)
            self._save(data)
        return self.status(user_id)
    def dev_renew(self, user_id, tier, now=None):
        self.s.require_developer(user_id)
        if tier not in self.s.tiers: raise ValidationError("invalid_tier")
        now = now or utc_now(); data = self.status(user_id)
        if data["state"] == "expired" and now >= datetime.fromisoformat(data["grace_until"]):
            self.s.purge_operational(user_id)
            data.update(state="new", tier=None, expires_at=None, grace_until=None)
        if data["state"] == "active":
            if data["tier"] == tier:
                data["expires_at"] = (datetime.fromisoformat(data["expires_at"]) + timedelta(days=self.s.tiers[tier]["days"])).isoformat()
            else: data["queue"].append({"tier": tier, **self.s.tiers[tier]})
        else:
            was_expired = data["state"] == "expired" and now < datetime.fromisoformat(data["grace_until"])
            data.update(state="active", tier=tier, expires_at=(now + timedelta(days=self.s.tiers[tier]["days"])).isoformat(), grace_until=None, window_start=now.isoformat(), used=0)
            if was_expired:
                self.s.rebase_pending(user_id, now)
        self._save(data)
        return data
    def dev_quota(self, user_id, exhausted):
        self.s.require_developer(user_id)
        data = self.status(user_id)
        self.require_active(user_id)
        was_exhausted = data["used"] >= self.s.tiers[data["tier"]]["limit"]
        data["used"] = self.s.tiers[data["tier"]]["limit"] if exhausted else 0
        if not exhausted: data["window_start"] = utc_now().isoformat()
        self._save(data)
        if was_exhausted and not exhausted: self.s.rebase_pending(user_id, datetime.fromisoformat(data["window_start"]))
        return data


class Devices:
    def __init__(self, services): self.s = services
    def list(self, user_id): return [x for x in self.s.store.read("devices") if x["user_id"] == user_id]
    def get(self, user_id, device_id): return one(self.s.store, "devices", device_id, user_id)
    def add(self, user_id, os, name, primary_id, idfv=None, proxy_id=None):
        self.s.subscriptions.require_active(user_id)
        if os not in {"Android", "iOS"}: raise ValidationError("invalid_os")
        if proxy_id: self.s.proxies.get(user_id, proxy_id)
        ids = {"gaid": identifier("gaid", primary_id, self.s.validation_rules)} if os == "Android" else {"idfa": identifier("idfa", primary_id, self.s.validation_rules), "idfv": identifier("idfv", idfv or "", self.s.validation_rules)}
        return self.s.store.add("devices", {"id": uid(), "user_id": user_id, "os": os, "name": required(name), "identifiers": ids, "proxy_id": proxy_id})
    def update_ids(self, user_id, device_id, ids):
        self.s.subscriptions.require_active(user_id)
        device = self.get(user_id, device_id)
        expected = {"gaid"} if device["os"] == "Android" else {"idfa", "idfv"}
        for key in ids:
            if key not in expected: raise ValidationError("invalid_identifier")
            device["identifiers"][key] = identifier(key, ids[key], self.s.validation_rules)
        return self.s.store.replace("devices", device)
    def proxy(self, user_id, device_id, proxy_id):
        self.s.subscriptions.require_active(user_id)
        if proxy_id: self.s.proxies.get(user_id, proxy_id)
        device = self.get(user_id, device_id); device["proxy_id"] = proxy_id
        return self.s.store.replace("devices", device)
    def delete(self, user_id, device_id):
        self.s.subscriptions.require_active(user_id); self.get(user_id, device_id)
        linked_ids = {x["id"] for x in self.linked(user_id, device_id)}
        self.s.store.remove_where("scheduled_plans", lambda x: x["user_id"] == user_id and x["device_id"] == device_id)
        self.s.store.remove_where("plan_counters", lambda x: x["user_id"] == user_id and f":{device_id}:" in x["id"])
        self.s.store.remove_where("linked_games", lambda x: x["id"] in linked_ids)
        self.s.store.remove_where("devices", lambda x: x["id"] == device_id)
    def linked(self, user_id, device_id):
        self.get(user_id, device_id)
        return [x for x in self.s.store.read("linked_games") if x["user_id"] == user_id and x["device_id"] == device_id]
    def get_linked(self, user_id, linked_id): return one(self.s.store, "linked_games", linked_id, user_id)
    def link_game(self, user_id, device_id, game_id, extra_id=None, expected_platform=None):
        self.s.subscriptions.require_active(user_id)
        device = self.get(user_id, device_id); game = self.s.catalog.get(game_id)
        if game["os"] != device["os"] or (expected_platform and game["platform"] != expected_platform): raise ValidationError("incompatible_game")
        existing = next((x for x in self.linked(user_id, device_id) if x["game_id"] == game_id), None)
        if existing: return existing
        if game["platform"] in {"AppsFlyer", "Singular"}: extra_id = identifier("uid" if game["platform"] == "AppsFlyer" else "singular", extra_id or "", self.s.validation_rules)
        else: extra_id = None
        return self.s.store.add("linked_games", {"id": uid(), "user_id": user_id, "device_id": device_id, "game_id": game_id, "extra_id": extra_id})
    def edit_linked_id(self, user_id, linked_id, value):
        self.s.subscriptions.require_active(user_id); linked = self.get_linked(user_id, linked_id)
        platform = self.s.catalog.get(linked["game_id"])["platform"]
        if platform == "Adjust": raise ValidationError("invalid_identifier")
        linked["extra_id"] = identifier("uid" if platform == "AppsFlyer" else "singular", value, self.s.validation_rules)
        return self.s.store.replace("linked_games", linked)
    def delete_linked(self, user_id, linked_id):
        self.s.subscriptions.require_active(user_id); self.get_linked(user_id, linked_id)
        self.s.store.remove_where("scheduled_plans", lambda x: x["user_id"] == user_id and x["linked_id"] == linked_id)
        self.s.store.remove_where("plan_counters", lambda x: x["user_id"] == user_id and x["id"].endswith(":" + linked_id))
        self.s.store.remove_where("linked_games", lambda x: x["id"] == linked_id)


class Proxies:
    def __init__(self, services): self.s = services
    def list(self, user_id): return [x for x in self.s.store.read("proxies") if x["user_id"] == user_id]
    def get(self, user_id, proxy_id): return one(self.s.store, "proxies", proxy_id, user_id)
    def _validate(self, data):
        if data["type"] not in {"HTTP/HTTPS", "SOCKS5"}: raise ValidationError("invalid_proxy_type")
        return {"country": required(data["country"]), "name": required(data["name"]), "type": data["type"], "endpoint": endpoint(data["endpoint"]), "username": required(data["username"]), "password": required(data["password"])}
    def add(self, user_id, data):
        self.s.subscriptions.require_active(user_id)
        return self.s.store.add("proxies", {"id": uid(), "user_id": user_id, **self._validate(data), "check": {"status": "unchecked"}})
    def edit(self, user_id, proxy_id, data):
        self.s.subscriptions.require_active(user_id); proxy = self.get(user_id, proxy_id)
        proxy.update(self._validate({**proxy, **data})); proxy["check"] = {"status": "unchecked"}
        return self.s.store.replace("proxies", proxy)
    def delete(self, user_id, proxy_id):
        self.s.subscriptions.require_active(user_id); self.get(user_id, proxy_id)
        for device in self.s.devices.list(user_id):
            if device["proxy_id"] == proxy_id:
                device["proxy_id"] = None; self.s.store.replace("devices", device)
        self.s.store.remove_where("proxies", lambda x: x["id"] == proxy_id)
    def dev_check(self, user_id, proxy_id, outcome):
        self.s.require_developer(user_id)
        if outcome not in {"success", "failure"}: raise ValidationError("invalid_check")
        proxy = self.get(user_id, proxy_id)
        proxy["check"] = {"status": outcome, "simulation": True, "at": utc_now().isoformat()}
        if outcome == "success": proxy["check"].update(ip="203.0.113.10", country=proxy["country"], latency_ms=42)
        else: proxy["check"]["reason"] = "simulated_timeout"
        return self.s.store.replace("proxies", proxy)


class Events:
    def __init__(self, services): self.s = services
    def preview(self, user_id, linked_id, template, values, kind):
        self.s.subscriptions.require_quota(user_id)
        linked = self.s.devices.get_linked(user_id, linked_id); game = self.s.catalog.get(linked["game_id"])
        if kind not in {"normal", "purchase", "custom"}: raise ValidationError("invalid_event")
        if kind != "custom" and template not in game[kind]: raise ValidationError("invalid_event")
        return {"simulation": True, "event": render_template(template, values), "kind": kind, "sent": False}


class Plans:
    def __init__(self, services): self.s = services
    def list(self, user_id): return [x for x in self.s.store.read("scheduled_plans") if x["user_id"] == user_id]
    def get(self, user_id, plan_id): return one(self.s.store, "scheduled_plans", plan_id, user_id)
    def add(self, user_id, device_id, linked_id, mode, shared, operations):
        self.s.subscriptions.require_active(user_id)
        self.s.devices.get(user_id, device_id); linked = self.s.devices.get_linked(user_id, linked_id)
        if linked["device_id"] != device_id: raise ValidationError("incompatible_game")
        if mode not in {"uniform", "multiple"} or not operations: raise ValidationError("invalid_schedule")
        if len(operations) > 20: raise ValidationError("too_many_operations")
        game = self.s.catalog.get(linked["game_id"])
        if mode == "uniform": interval(shared or "", self.s.validation_rules)
        clean = []
        for position, operation in enumerate(operations, start=1):
            template = operation["template"]
            if template not in game["normal"]: raise ValidationError("invalid_event")
            values = operation.get("values", [])
            event = render_template(template, values)
            op_interval = operation.get("interval")
            if mode == "multiple": interval(op_interval or "", self.s.validation_rules)
            clean.append({"id": uid(), "order": position, "template": template, "values": values, "event": event, "interval": op_interval if mode == "multiple" else None, "status": "pending"})
        due = due_dates(mode, clean, utc_now(), shared, rules=self.s.validation_rules)
        counter_id = f"{user_id}:{device_id}:{linked_id}"
        counter = next((x for x in self.s.store.read("plan_counters") if x["id"] == counter_id), None)
        number = 1 if counter is None else counter["number"] + 1
        if counter is None: self.s.store.add("plan_counters", {"id": counter_id, "user_id": user_id, "number": number})
        else:
            counter["number"] = number
            self.s.store.replace("plan_counters", counter)
        return self.s.store.add("scheduled_plans", {"id": uid(), "user_id": user_id, "device_id": device_id, "linked_id": linked_id, "number": number, "mode": mode, "shared_interval": shared, "operations": due})
    def edit_pending(self, user_id, plan_id, operations, shared=None):
        self.s.subscriptions.require_active(user_id); plan = self.get(user_id, plan_id)
        game = self.s.catalog.get(self.s.devices.get_linked(user_id, plan["linked_id"])["game_id"])
        pending_ids = {x["id"] for x in plan["operations"] if x["status"] == "pending"}
        if {x["id"] for x in operations} != pending_ids: raise ValidationError("invalid_operation")
        for operation in operations:
            if operation["template"] not in game["normal"]: raise ValidationError("invalid_event")
            operation["event"] = render_template(operation["template"], operation["values"])
            if plan["mode"] == "multiple": interval(operation["interval"], self.s.validation_rules)
        if plan["mode"] == "uniform": interval(shared or plan["shared_interval"], self.s.validation_rules)
        plan["shared_interval"] = shared or plan["shared_interval"]
        revised = resume_unattempted(plan["mode"], operations, utc_now(), plan["shared_interval"], rules=self.s.validation_rules)
        by_id = {x["id"]: x for x in revised}
        plan["operations"] = [by_id.get(x["id"], x) for x in plan["operations"]]
        return self.s.store.replace("scheduled_plans", plan)
    def delete(self, user_id, plan_id):
        self.s.subscriptions.require_active(user_id); self.get(user_id, plan_id)
        self.s.store.remove_where("scheduled_plans", lambda x: x["id"] == plan_id)
    def dev_fail_first(self, user_id, plan_id):
        self.s.require_developer(user_id)
        plan = self.get(user_id, plan_id)
        pending = next((x for x in plan["operations"] if x["status"] == "pending"), None)
        if pending: pending.update(status="failed", detail="simulated_failure", attempted_at=utc_now().isoformat())
        return self.s.store.replace("scheduled_plans", plan)


class Finance:
    def __init__(self, services): self.s = services
    def ledger(self, user_id): return [x for x in self.s.store.read("financial_ledger") if x["user_id"] == user_id]
    def balance(self, user_id): return sum(x["amount"] for x in self.ledger(user_id))
    def dev_balance(self, user_id, amount):
        self.s.require_developer(user_id)
        if not isinstance(amount, (int, float)): raise ValidationError("invalid_amount")
        try:
            if not math.isfinite(amount): raise ValidationError("invalid_amount")
        except OverflowError as exc:
            raise ValidationError("invalid_amount") from exc
        adjustment = amount - self.balance(user_id)
        return self.s.store.add("financial_ledger", {"id": uid(), "user_id": user_id, "amount": adjustment, "kind": "developer_mock_adjustment", "simulation": True})


class Referrals:
    def __init__(self, services): self.s = services
    def register(self, referrer_id, referred_id):
        if referrer_id == referred_id or referrer_id <= 0 or referred_id <= 0: return False
        if not any(x["id"] == str(referrer_id) for x in self.s.store.read("users")): return False
        if any(x.get("referred_id") == referred_id for x in self.s.store.read("referral_data")): return False
        self.s.store.add("referral_data", {"id": uid(), "user_id": referrer_id, "referred_id": referred_id, "earnings": 0, "simulation": True})
        return True
    def stats(self, user_id):
        rows = [x for x in self.s.store.read("referral_data") if x["user_id"] == user_id]
        return {"count": len(rows), "earnings": sum(x.get("earnings", 0) for x in rows), "rate": self.s.referral_rate, "simulation": True}


class MockServices:
    def __init__(self, store: JsonStore, tiers: dict | None = None, dev_mode: bool = False, dev_ids=(), referral_rate: float = 0.1, validation_rules: ValidationRules = DEFAULT_VALIDATION_RULES):
        self.store = store
        self.tiers = tiers or TIERS
        self.referral_rate = referral_rate
        self.validation_rules = validation_rules
        self.dev_mode = dev_mode
        self.dev_ids = set(dev_ids)
        self.users = Users(store); self.catalog = Catalog(store); self.subscriptions = Subscriptions(self)
        self.devices = Devices(self); self.proxies = Proxies(self); self.events = Events(self)
        self.plans = Plans(self); self.finance = Finance(self); self.referrals = Referrals(self)
    def require_developer(self, user_id):
        if not self.dev_mode or user_id not in self.dev_ids: raise PermissionError("developer_required")
    def rebase_pending(self, user_id, baseline):
        for plan in self.plans.list(user_id):
            plan["operations"] = resume_unattempted(plan["mode"], plan["operations"], baseline, plan["shared_interval"], rules=self.validation_rules)
            self.store.replace("scheduled_plans", plan)
    def purge_operational(self, user_id):
        for name in ("devices", "linked_games", "proxies", "scheduled_plans", "plan_counters"):
            self.store.remove_where(name, lambda x: x["user_id"] == user_id)
