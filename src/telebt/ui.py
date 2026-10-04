"""Framework independent Telegram conversation engine; callback IDs never depend on labels."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .locale import t
from .interfaces import ServiceBundle
from .schedule import display_time, due_dates, utc_now
from .services import PermissionError
from .storage import StorageError
from .validation import ValidationError, endpoint, identifier, interval, render_template, required, template_fields


@dataclass
class Button:
    label: str
    data: str | None = None
    url: str | None = None


@dataclass
class Screen:
    text: str
    buttons: list[Button] = field(default_factory=list)
    kind: str = "view"
    input_prompt: "Screen | None" = None


class BotUI:
    PAGE_SIZE = 6
    WIZARD_FLOWS = frozenset({"device_add", "device_edit", "proxy_add", "proxy_edit", "link_add", "link_edit", "event", "plan_add", "plan_edit", "plan_draft_edit", "plan_draft_edit_shared"})

    def __init__(self, services: ServiceBundle, bot_username: str = "", support_links=(), dev_mode=False, dev_ids=()):
        self.s = services
        self.bot_username = bot_username.lstrip("@")
        self.support_links = support_links
        self.dev_mode = dev_mode
        self.dev_ids = set(dev_ids)
        self.sessions: dict[int, dict] = {}

    def _state(self, user_id):
        return self.sessions.setdefault(user_id, {"screen": None, "screen_state": None, "history": [], "flow": None, "draft": {}, "waiting": None, "step": 0})

    def _l(self, user_id, key): return t(self.s.users.get(user_id)["language"], key)
    def _b(self, user_id, key, data): return Button(self._l(user_id, key), data)

    def _show(self, user_id, text, buttons=None, *, kind="view", push=True):
        state = self._state(user_id)
        next_screen = Screen(text, buttons or [], kind)
        next_state = {key: deepcopy(state[key]) for key in ("flow", "draft", "waiting", "step")}
        if push and state["screen"] is not None and (state["screen"] != next_screen or state["screen_state"] != next_state):
            state["history"].append((state["screen"], state["screen_state"]))
        state["screen"] = next_screen
        state["screen_state"] = next_state
        return state["screen"]

    def _safe(self, user_id, operation):
        try: return operation()
        except PermissionError as exc:
            key = "quota_exhausted" if str(exc) == "quota_exhausted" else "denied"
            return self._show(user_id, self._l(user_id, key), [self._b(user_id, "back", "back")], kind="denied", push=False)
        except (ValueError, KeyError, IndexError, AttributeError, TypeError, StopIteration) as exc:
            key = str(exc) if isinstance(exc, ValidationError) else "stale"
            screen = self._state(user_id)["screen"]
            if screen:
                return self._input_error(user_id, key)
            return self.menu(user_id)
        except StorageError:
            try:
                message = self._l(user_id, "storage_error")
            except StorageError:
                message = "تعذر قراءة بيانات التجربة. افحص ملف JSON المحلي. / Cannot read demo data; inspect the local JSON file."
            return self._show(user_id, message, kind="error", push=False)

    def _input_error(self, user_id, key):
        screen = self._state(user_id)["screen"]
        prompt = screen.input_prompt or screen
        error = self._show(user_id, self._l(user_id, key) + "\n\n" + prompt.text,
                           prompt.buttons, kind="error", push=False)
        error.input_prompt = prompt
        return error

    def start(self, user_id, referral_code=None):
        return self._safe(user_id, lambda: self._start(user_id, referral_code))

    def _start(self, user_id, referral_code=None):
        state = self._state(user_id)
        discarded = bool(state["flow"])
        state.update(history=[], flow=None, draft={}, waiting=None, step=0, screen=None, screen_state=None)
        profile = self.s.users.get(user_id)
        if not profile["language"]:
            if referral_code and referral_code.startswith("ref_") and referral_code[4:].isdigit():
                self.s.referrals.register(int(referral_code[4:]), user_id)
            screen = self._show(user_id, self._l(user_id, "choose_language"), [Button("العربية", "lang:ar"), Button("English", "lang:en")], push=False)
            if discarded: screen.text = self._l(user_id, "draft_discarded") + "\n\n" + screen.text
            return screen
        screen = self.menu(user_id)
        if discarded: screen.text = self._l(user_id, "draft_discarded") + "\n\n" + screen.text
        return screen

    def menu(self, user_id):
        state = self._state(user_id); state.update(flow=None, draft={}, waiting=None, step=0, history=[])
        keys = ["search", "profile", "devices", "plans", "proxies", "deposit", "tiers", "support", "language"]
        return self._show(user_id, self._l(user_id, "menu") + "\n" + self._l(user_id, "demo"), [self._b(user_id, key, "open:" + key) for key in keys], kind="menu", push=False)

    def _finish(self, user_id, confirmation):
        screen = self.menu(user_id)
        screen.text = confirmation + "\n\n" + screen.text
        return screen

    def _back(self, user_id):
        state = self._state(user_id)
        screen_kind = state["screen"].kind if state["screen"] else ""
        if state["flow"] == "plan_add" and screen_kind in {"plan_count", "plan_review"}:
            return self._plan_action(user_id, "plan:draftlist")
        if state["flow"] == "plan_add" and screen_kind == "plan_draftlist":
            return self._plan_review(user_id)
        if state["flow"] == "event" and state["waiting"] == "variable_edit":
            state["draft"].pop("edit_index", None)
            state["waiting"] = None
            return self._variables_or_next(user_id)
        if state["history"]:
            current_flow, current_draft = state["flow"], state["draft"]
            state["screen"], snapshot = state["history"].pop()
            if snapshot:
                state.update({key: deepcopy(value) for key, value in snapshot.items()})
                if snapshot["flow"] == current_flow and current_flow in self.WIZARD_FLOWS:
                    state["draft"].update({key: deepcopy(value) for key, value in current_draft.items() if key not in {"steps", "value_index"}})
            state["screen_state"] = {key: deepcopy(state[key]) for key in ("flow", "draft", "waiting", "step")}
            if state["screen"].kind == "menu":
                return self.menu(user_id)
            return state["screen"]
        return self.menu(user_id)

    def _list(self, user_id, title, items, button_fn, page, prefix, extras=()):
        page = max(0, min(page, max(0, (len(items) - 1) // self.PAGE_SIZE)))
        visible = items[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]
        buttons = [button_fn(item) for item in visible]
        if page: buttons.append(Button("◀", f"{prefix}:{page-1}"))
        if (page + 1) * self.PAGE_SIZE < len(items): buttons.append(Button("▶", f"{prefix}:{page+1}"))
        buttons.extend(extras)
        buttons.append(self._b(user_id, "back", "back"))
        return self._show(user_id, title + ("\n" + self._l(user_id, "empty") if not items else f"\n{page+1}/{max(1,(len(items)-1)//self.PAGE_SIZE+1)}"), buttons)

    def click(self, user_id: int, action: str):
        return self._safe(user_id, lambda: self._click(user_id, action))

    def _click(self, user_id, action):
        state = self._state(user_id)
        if action == "menu": return self.menu(user_id)
        if action == "back": return self._back(user_id)
        if action == "cancel":
            discarded = bool(state["flow"])
            state.update(flow=None, draft={}, waiting=None, step=0)
            screen = self.menu(user_id)
            if discarded: screen.text = self._l(user_id, "draft_discarded") + "\n\n" + screen.text
            return screen
        if action.startswith("lang:"):
            discarded = bool(state["flow"])
            self.s.users.language(user_id, action.split(":", 1)[1]); screen = self.menu(user_id)
            if discarded: screen.text = self._l(user_id, "draft_discarded") + "\n\n" + screen.text
            return screen
        if action.startswith("open:"):
            section = action.split(":", 1)[1]
            if section not in {"search", "deposit", "devices", "plans", "profile", "proxies", "tiers", "support", "language"}:
                raise KeyError(section)
            return getattr(self, "open_" + section)(user_id)
        if action.startswith("search:"): return self._search_action(user_id, action)
        if action.startswith("device:"): return self._device_action(user_id, action)
        if action.startswith("link:"): return self._link_action(user_id, action)
        if action.startswith("event:"): return self._event_action(user_id, action)
        if action.startswith("proxy:"): return self._proxy_action(user_id, action)
        if action.startswith("plan:"): return self._plan_action(user_id, action)
        if action.startswith("deposit:"): return self._deposit_action(user_id, action)
        if action.startswith("tier:"): return self._tier_action(user_id, action)
        if action.startswith("profile:queue:"): return self._profile_queue(user_id, int(action.split(":")[2]))
        if action == "referrals": return self.open_referrals(user_id)
        if action.startswith("dev:"): return self._dev_action(user_id, action)
        raise KeyError(action)

    def text(self, user_id: int, value: str):
        return self._safe(user_id, lambda: self._text(user_id, value))

    def _text(self, user_id, value):
        state = self._state(user_id)
        if state["screen"] and state["screen"].input_prompt:
            state["screen"] = state["screen"].input_prompt
        flow, waiting = state["flow"], state["waiting"]
        if not waiting: return self._show(user_id, self._l(user_id, "input"), [self._b(user_id, "back", "back")], push=False)
        try:
            if waiting in {"gaid", "idfa", "idfv", "uid", "singular"}: clean = identifier(waiting, value, self.s.validation_rules)
            elif waiting in {"shared_interval", "operation_interval"}: interval(value, self.s.validation_rules); clean = value.strip()
            elif waiting == "endpoint": clean = endpoint(value)
            elif waiting == "game_name": return self._search_results(user_id, value)
            elif waiting == "link_name": return self._link_results(user_id, value)
            elif waiting == "custom_template": template_fields(value); return self._event_template(user_id, value.strip(), "custom")
            else: clean = required(value, limit=64 if waiting in {"variable", "variable_edit"} else 128)
        except ValidationError as exc:
            return self._input_error(user_id, str(exc))

        if flow in {"device_add", "device_edit", "proxy_add"}:
            state["draft"][waiting] = clean; state["step"] += 1
            return self._prompt(user_id)
        if flow == "link_add":
            state["draft"]["extra_id"] = clean; state["waiting"] = None
            return self._link_review(user_id)
        if flow == "link_edit":
            linked_id = state["draft"]["linked_id"]
            self.s.devices.edit_linked_id(user_id, linked_id, clean)
            return self._finish(user_id, self._l(user_id, "changes_saved"))
        if flow == "proxy_edit":
            self.s.proxies.edit(user_id, state["draft"]["proxy_id"], {waiting: clean})
            return self._finish(user_id, self._l(user_id, "changes_saved"))
        if flow in {"event", "plan_add", "plan_edit", "plan_draft_edit"} and waiting == "variable":
            draft = state["draft"]
            values = draft.setdefault("values", [])
            index = draft.get("value_index", len(values))
            if index < len(values): values[index] = clean
            else: values.append(clean)
            draft["value_index"] = index + 1
            return self._variables_or_next(user_id)
        if flow == "event" and waiting == "variable_edit":
            state["draft"]["values"][state["draft"].pop("edit_index")] = clean
            state["waiting"] = None
            return self._variables_or_next(user_id)
        if flow == "plan_add" and waiting == "shared_interval":
            state["draft"]["shared"] = clean; state["waiting"] = None
            return self._plan_choose_event(user_id)
        if flow == "plan_edit" and waiting == "shared_interval":
            state["draft"]["shared"] = clean; state["waiting"] = None
            return self._plan_edit_review(user_id)
        if flow == "plan_draft_edit_shared" and waiting == "shared_interval":
            state["draft"]["shared"] = clean; state.update(flow="plan_add", waiting=None)
            return self._plan_review(user_id)
        if flow == "plan_add" and waiting == "operation_interval":
            state["draft"]["current_interval"] = clean; state["waiting"] = None
            return self._plan_add_operation(user_id)
        if flow == "plan_edit" and waiting == "operation_interval":
            state["draft"]["interval"] = clean; state["waiting"] = None
            return self._plan_edit_review(user_id)
        if flow == "plan_draft_edit" and waiting == "operation_interval":
            state["draft"]["current_interval"] = clean; state["waiting"] = None
            return self._plan_draft_edit_review(user_id)
        raise KeyError(waiting)

    def _start_form(self, user_id, flow, draft, steps):
        state = self._state(user_id); state.update(flow=flow, draft={**draft, "steps": steps}, step=0)
        return self._prompt(user_id)

    def _prompt(self, user_id, push=True):
        state = self._state(user_id); flow = state["flow"]; steps = state["draft"]["steps"]; index = state["step"]
        if index >= len(steps):
            state["waiting"] = None
            return {"device_add": self._device_proxy_choice, "device_edit": self._device_review, "proxy_add": self._proxy_review}[flow](user_id)
        key = steps[index]; state["waiting"] = key
        prompt = self._l(user_id, key)
        if flow == "proxy_add" and key in {"username", "password"}: prompt += "\n" + self._l(user_id, "demo_credentials")
        return self._show(user_id, prompt, [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")], push=push)

    def open_language(self, user_id):
        return self._show(user_id, self._l(user_id, "choose_language"), [Button("العربية", "lang:ar"), Button("English", "lang:en"), self._b(user_id, "back", "back")])

    def open_search(self, user_id):
        return self._show(user_id, self._l(user_id, "choose_os") + "\n" + self._l(user_id, "mock_catalog"), [Button("Android", "search:os:Android"), Button("iOS", "search:os:iOS"), self._b(user_id, "back", "back")])

    def _search_action(self, user_id, action):
        parts = action.split(":")
        if parts[1] == "os":
            self._state(user_id).update(flow="search", draft={"os": parts[2]}, waiting="game_name")
            return self._show(user_id, self._l(user_id, "game_name"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if parts[1] == "page": return self._render_search(user_id, int(parts[2]))
        if parts[1] == "again":
            state = self._state(user_id)
            if state["flow"] != "search" or "os" not in state["draft"]:
                raise ValidationError("stale")
            while state["history"] and state["history"][-1][1]["waiting"] != "game_name":
                state["history"].pop()
            if state["history"]:
                return self._back(user_id)
            state["draft"].pop("query", None)
            state["waiting"] = "game_name"
            return self._show(user_id, self._l(user_id, "game_name"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")], push=False)
        raise KeyError(action)

    def _search_results(self, user_id, query):
        state = self._state(user_id); state["draft"]["query"] = required(query)
        state["waiting"] = None
        return self._render_search(user_id, 0)

    def _render_search(self, user_id, page):
        draft = self._state(user_id)["draft"]
        games = self.s.catalog.search(draft["query"], draft["os"])
        replace_failed = self._state(user_id)["screen"] is not None and self._state(user_id)["screen"].kind == "search_missing"
        if not games:
            self._state(user_id)["waiting"] = "game_name"
            return self._show(user_id, self._l(user_id, "not_found"), [self._b(user_id, "retry", "search:again"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")], kind="search_missing", push=not replace_failed)
        page = max(0, min(page, (len(games) - 1) // self.PAGE_SIZE))
        visible = games[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]
        buttons = []
        if page: buttons.append(Button("◀", f"search:page:{page-1}"))
        if (page + 1) * self.PAGE_SIZE < len(games): buttons.append(Button("▶", f"search:page:{page+1}"))
        buttons += [self._b(user_id, "retry", "search:again"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")]
        text = self._l(user_id, "mock_catalog") + f"\n{page+1}/{(len(games)-1)//self.PAGE_SIZE+1}\n" + "\n".join(g["name"] for g in visible)
        return self._show(user_id, text, buttons, kind="search_results", push=not replace_failed)

    def open_devices(self, user_id, page=0):
        devices = self.s.devices.list(user_id)
        status = self.s.subscriptions.status(user_id)
        extra = [self._b(user_id, "add_device", "device:add")] if status["state"] == "active" else []
        title = self._l(user_id, "devices") + "\n" + self._l(user_id, "device_legend") + ("\n" + self._l(user_id, "denied") if status["state"] != "active" else "")
        if status["grace_until"]:
            title += "\n" + self._l(user_id, "grace") + ": " + display_time(status["grace_until"], self.s.users.get(user_id)["timezone"]).rsplit(" ", 1)[0]
        if not devices: title += "\n" + self._l(user_id, "empty_devices")
        return self._list(user_id, title, devices, lambda d: Button(("🍎 " if d["os"] == "iOS" else "🤖 ") + d["name"], "device:view:" + d["id"]), page, "device:page", extra)

    def _device_action(self, user_id, action):
        p = action.split(":"); cmd = p[1]
        if cmd == "page": return self.open_devices(user_id, int(p[2]))
        if cmd == "add":
            self.s.subscriptions.require_active(user_id)
            return self._show(user_id, self._l(user_id, "choose_os"), [Button("Android", "device:os:Android"), Button("iOS", "device:os:iOS"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "os":
            steps = ["device_name", "gaid"] if p[2] == "Android" else ["device_name", "idfa", "idfv"]
            return self._start_form(user_id, "device_add", {"os": p[2]}, steps)
        if cmd == "proxy":
            proxy_id = None if p[2] == "none" else p[2]
            self._state(user_id)["draft"]["proxy_id"] = proxy_id
            return self._device_review(user_id)
        if cmd == "view": return self._device_detail(user_id, p[2])
        if cmd == "edit":
            device = self.s.devices.get(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            steps = ["gaid"] if device["os"] == "Android" else ["idfa", "idfv"]
            return self._start_form(user_id, "device_edit", {"device_id": p[2]}, steps)
        if cmd == "manageproxy": return self._device_proxy_manage(user_id, p[2])
        if cmd == "changeproxy": return self._device_proxy_choice(user_id, p[2])
        if cmd == "proxypage":
            self._state(user_id)["proxy_page"] = int(p[3])
            return self._device_proxy_choice(user_id, None if p[2] == "new" else p[2])
        if cmd == "setproxy":
            self.s.devices.proxy(user_id, p[2], None if p[3] == "none" else p[3]); return self._device_detail(user_id, p[2])
        if cmd == "confirm":
            draft = self._state(user_id)["draft"]
            if self._state(user_id)["flow"] == "device_edit":
                device = self.s.devices.update_ids(user_id, draft["device_id"], {k: draft[k] for k in ("gaid", "idfa", "idfv") if k in draft})
            else:
                device = self.s.devices.add(user_id, draft["os"], draft["device_name"], draft.get("gaid") or draft.get("idfa"), draft.get("idfv"), draft.get("proxy_id"))
            return self._finish(user_id, self._l(user_id, "device_saved"))
        if cmd == "delete":
            device = self.s.devices.get(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            games = self.s.devices.linked(user_id, p[2]); plans = [x for x in self.s.plans.list(user_id) if x["device_id"] == p[2]]
            return self._show(user_id, f"{self._l(user_id,'delete_device')}: {device['name']}\n{self._l(user_id,'games_count')}: {len(games)} / {self._l(user_id,'plans_count')}: {len(plans)}", [self._b(user_id, "confirm", "device:deleteyes:" + p[2]), self._b(user_id, "cancel", "cancel")])
        if cmd == "deleteyes": self.s.devices.delete(user_id, p[2]); return self.open_devices(user_id)
        raise KeyError(action)

    def _device_proxy_choice(self, user_id, device_id=None):
        proxies = self.s.proxies.list(user_id)
        page = self._state(user_id).get("proxy_page", 0)
        action = "setproxy:" + device_id if device_id else "proxy"
        extras = ([self._b(user_id, "skip", f"device:{action}:none")] if not device_id else []) + [self._b(user_id, "cancel", "cancel")]
        return self._list(user_id, self._l(user_id, "choose_proxy"), proxies, lambda x: Button(x["name"] + " (" + x["country"] + ")", f"device:{action}:{x['id']}"), page, "device:proxypage:" + (device_id or "new"), extras)

    def _device_proxy_manage(self, user_id, device_id):
        device = self.s.devices.get(user_id, device_id)
        self.s.subscriptions.require_active(user_id)
        if device["proxy_id"]:
            proxy = self.s.proxies.get(user_id, device["proxy_id"])
            return self._show(user_id, self._l(user_id, "current_proxy") + ": " + proxy["name"], [self._b(user_id, "change_proxy", "device:changeproxy:" + device_id), self._b(user_id, "detach_proxy", "device:setproxy:" + device_id + ":none"), self._b(user_id, "back", "back")])
        return self._show(user_id, self._l(user_id, "no_proxy"), [self._b(user_id, "link_proxy", "device:changeproxy:" + device_id), self._b(user_id, "back", "back")])

    def _device_review(self, user_id):
        draft = self._state(user_id)["draft"]
        labels = {"os": "os_label", "device_name": "device_name_label", "proxy_id": "proxy_label"}
        values = [f"{self._l(user_id,labels.get(k,k))}: {v}" for k, v in draft.items() if k in {"os", "device_name", "gaid", "idfa", "idfv", "proxy_id"}]
        return self._show(user_id, self._l(user_id, "review") + "\n" + "\n".join(values), [self._b(user_id, "save", "device:confirm"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])

    def _device_detail(self, user_id, device_id, prefix="", *, kind="view"):
        d = self.s.devices.get(user_id, device_id); linked = self.s.devices.linked(user_id, device_id)
        proxy = self.s.proxies.get(user_id, d["proxy_id"])["name"] if d["proxy_id"] else self._l(user_id, "no_proxy")
        ids = ", ".join(k.upper() + ": …" + v[-6:] for k, v in d["identifiers"].items())
        buttons = [Button(self.s.catalog.get(x["game_id"])["name"], "link:view:" + x["id"]) for x in linked]
        if self.s.subscriptions.status(user_id)["state"] == "active":
            buttons += [self._b(user_id, "add_game", "link:add:" + device_id), self._b(user_id, "edit_ids", "device:edit:" + device_id), self._b(user_id, "manage_proxy", "device:manageproxy:" + device_id), self._b(user_id, "delete_device", "device:delete:" + device_id)]
        buttons.append(self._b(user_id, "back", "back"))
        return self._show(user_id, prefix + ("\n" if prefix else "") + f"{d['name']} · {d['os']}\n{ids}\n{self._l(user_id,'proxy_label')}: {proxy}\n{self._l(user_id,'linked_games')}: {len(linked)}", buttons, kind=kind)

    def _link_action(self, user_id, action):
        p = action.split(":"); cmd = p[1]
        if cmd == "add":
            self.s.subscriptions.require_active(user_id); self.s.devices.get(user_id, p[2])
            self._state(user_id).update(flow="link_add", draft={"device_id": p[2]}, waiting=None)
            return self._show(user_id, self._l(user_id, "choose_platform"), [Button(x, "link:platform:" + x) for x in ("AppsFlyer", "Adjust", "Singular")] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "platform":
            self._state(user_id)["draft"]["platform"] = p[2]
            self._state(user_id)["waiting"] = "link_name"
            return self._show(user_id, self._l(user_id, "game_name"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "page": return self._render_link_results(user_id, int(p[2]))
        if cmd == "choose":
            draft = self._state(user_id)["draft"]; device = self.s.devices.get(user_id, draft["device_id"]); game = self.s.catalog.get(p[2])
            if game["os"] != device["os"] or game["platform"] != draft["platform"]: raise ValidationError("incompatible_game")
            existing = next((x for x in self.s.devices.linked(user_id, device["id"]) if x["game_id"] == game["id"]), None)
            if existing: return self._linked_detail(user_id, existing["id"], self._l(user_id, "already_linked"))
            draft["game_id"] = game["id"]
            if game["platform"] in {"AppsFlyer", "Singular"}:
                self._state(user_id)["waiting"] = "uid" if game["platform"] == "AppsFlyer" else "singular"
                return self._show(user_id, self._l(user_id, self._state(user_id)["waiting"]), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            return self._link_review(user_id)
        if cmd == "confirm":
            draft = self._state(user_id)["draft"]
            linked = self.s.devices.link_game(user_id, draft["device_id"], draft["game_id"], draft.get("extra_id"), expected_platform=draft["platform"])
            game = self.s.catalog.get(linked["game_id"])
            return self._finish(user_id, game["name"] + " · " + self._l(user_id, "demo"))
        if cmd == "view": return self._linked_detail(user_id, p[2])
        if cmd == "edit":
            linked = self.s.devices.get_linked(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            platform = self.s.catalog.get(linked["game_id"])["platform"]
            if platform == "Adjust": raise ValidationError("invalid_identifier")
            self._state(user_id).update(flow="link_edit", draft={"linked_id": p[2]}, waiting="uid" if platform == "AppsFlyer" else "singular")
            return self._show(user_id, self._l(user_id, self._state(user_id)["waiting"]), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "delete":
            linked = self.s.devices.get_linked(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            game = self.s.catalog.get(linked["game_id"]); plans = [x for x in self.s.plans.list(user_id) if x["linked_id"] == linked["id"]]
            return self._show(user_id, f"{self._l(user_id,'delete_game')}: {game['name']}\n{self._l(user_id,'plans_count')}: {len(plans)}", [self._b(user_id, "confirm", "link:deleteyes:" + p[2]), self._b(user_id, "cancel", "cancel")])
        if cmd == "deleteyes":
            linked = self.s.devices.get_linked(user_id, p[2]); device_id = linked["device_id"]
            self.s.devices.delete_linked(user_id, p[2]); return self._device_detail(user_id, device_id)
        raise KeyError(action)

    def _link_results(self, user_id, query):
        self._state(user_id)["draft"]["query"] = required(query)
        self._state(user_id)["waiting"] = None
        return self._render_link_results(user_id, 0)

    def _render_link_results(self, user_id, page):
        draft = self._state(user_id)["draft"]
        device = self.s.devices.get(user_id, draft["device_id"])
        games = self.s.catalog.search(draft["query"], device["os"], draft["platform"])
        if not games: return self._show(user_id, self._l(user_id, "not_found"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        return self._list(user_id, self._l(user_id, "mock_catalog"), games, lambda g: Button(g["name"], "link:choose:" + g["id"]), page, "link:page", [self._b(user_id, "cancel", "cancel")])

    def _link_review(self, user_id):
        draft = self._state(user_id)["draft"]; game = self.s.catalog.get(draft["game_id"])
        return self._show(user_id, f"{self._l(user_id,'review')}\n{game['name']} · {game['os']} · {game['platform']}\n{draft.get('extra_id','')}", [self._b(user_id, "save", "link:confirm"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])

    def _linked_detail(self, user_id, linked_id, prefix=""):
        linked = self.s.devices.get_linked(user_id, linked_id); game = self.s.catalog.get(linked["game_id"])
        buttons = []
        if self.s.subscriptions.status(user_id)["state"] == "active":
            buttons = [self._b(user_id, "send_event", "event:start:" + linked_id + ":normal"), self._b(user_id, "send_purchase", "event:start:" + linked_id + ":purchase"), self._b(user_id, "custom_event", "event:start:" + linked_id + ":custom")]
            if game["platform"] != "Adjust": buttons.append(self._b(user_id, "edit_extra", "link:edit:" + linked_id))
            buttons.append(self._b(user_id, "delete_game", "link:delete:" + linked_id))
        buttons.append(self._b(user_id, "back", "back"))
        extra = "…" + linked["extra_id"][-6:] if linked["extra_id"] else "—"
        return self._show(user_id, prefix + ("\n" if prefix else "") + f"{game['name']} · {game['platform']} · {game['os']}\nID: {extra}", buttons)

    def _event_action(self, user_id, action):
        p = action.split(":"); cmd = p[1]
        if cmd == "start":
            self.s.subscriptions.require_active(user_id)
            subscription = self.s.subscriptions.status(user_id)
            if subscription["used"] >= self.s.tiers[subscription["tier"]]["limit"]:
                return self._show(user_id, self._l(user_id, "quota_exhausted"), [self._b(user_id, "back", "back")], kind="denied")
            linked = self.s.devices.get_linked(user_id, p[2]); game = self.s.catalog.get(linked["game_id"])
            kind = p[3]
            self._state(user_id).update(flow="event", draft={"linked_id": linked["id"], "kind": kind}, waiting=None)
            if kind == "custom":
                self._state(user_id)["waiting"] = "custom_template"
                return self._show(user_id, self._l(user_id, "custom_template"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            templates = game[kind]
            if not templates: return self._show(user_id, self._l(user_id, "no_purchase_events" if kind == "purchase" else "no_events"), [self._b(user_id, "back", "back")])
            return self._show(user_id, self._l(user_id, "choose_event"), [Button(x, "event:template:" + str(i)) for i, x in enumerate(templates)] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "template":
            draft = self._state(user_id)["draft"]; linked = self.s.devices.get_linked(user_id, draft["linked_id"])
            game = self.s.catalog.get(linked["game_id"]); templates = game[draft["kind"]]
            return self._event_template(user_id, templates[int(p[2])], draft["kind"])
        if cmd == "execute":
            draft = self._state(user_id)["draft"]
            self.s.subscriptions.require_quota(user_id)
            preview = self.s.events.preview(user_id, draft["linked_id"], draft["template"], draft["values"], draft["kind"])
            return self._finish(user_id, preview["event"] + "\n" + self._l(user_id, "mock_result"))
        if cmd == "editvar":
            draft = self._state(user_id)["draft"]
            index = int(p[2]); fields = template_fields(draft["template"])
            if index >= len(fields): raise KeyError(action)
            draft["edit_index"] = index
            self._state(user_id)["waiting"] = "variable_edit"
            return self._show(user_id, f"{self._l(user_id,'variable')}: {fields[index]}", [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        raise KeyError(action)

    def _event_template(self, user_id, template, kind):
        draft = self._state(user_id)["draft"]
        draft.update(template=template, kind=kind, values=[], value_index=0)
        return self._variables_or_next(user_id)

    def _variables_or_next(self, user_id):
        state = self._state(user_id); draft = state["draft"]
        fields = template_fields(draft["template"])
        index = draft.get("value_index", len(draft["values"]))
        if index < len(fields):
            state["waiting"] = "variable"
            return self._show(user_id, f"{self._l(user_id,'variable')}: {fields[index]}", [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        state["waiting"] = None
        if state["flow"] == "plan_add":
            if draft["mode"] == "multiple":
                state["waiting"] = "operation_interval"
                return self._show(user_id, self._l(user_id, "operation_interval"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            return self._plan_add_operation(user_id)
        if state["flow"] == "plan_edit":
            plan = self.s.plans.get(user_id, draft["plan_id"])
            if plan["mode"] == "multiple":
                state["waiting"] = "operation_interval"
                return self._show(user_id, self._l(user_id, "operation_interval"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            return self._plan_edit_review(user_id)
        if state["flow"] == "plan_draft_edit":
            if draft["mode"] == "multiple":
                state["waiting"] = "operation_interval"
                return self._show(user_id, self._l(user_id, "operation_interval"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            return self._plan_draft_edit_review(user_id)
        buttons = [Button(f"{field}: {draft['values'][i]}", f"event:editvar:{i}") for i, field in enumerate(fields)]
        buttons += [self._b(user_id, "execute_demo", "event:execute"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")]
        return self._show(user_id, f"{self._l(user_id,'review')}\n{render_template(draft['template'],draft['values'])}\n{self._l(user_id,'demo')}", buttons, kind="event_review")

    def open_proxies(self, user_id, *, push=True):
        proxies = self.s.proxies.list(user_id); countries = sorted({x["country"] for x in proxies})
        subscription = self.s.subscriptions.status(user_id)
        buttons = [Button(country, "proxy:country:" + country) for country in countries]
        if subscription["state"] == "active":
            buttons += [self._b(user_id, "add_proxy", "proxy:add")]
        buttons.append(self._b(user_id, "back", "back"))
        text = self._l(user_id, "proxies") + ("\n" + self._l(user_id, "empty") if not proxies else "")
        if subscription["state"] != "active": text += "\n" + self._l(user_id, "denied")
        if subscription["grace_until"]:
            text += "\n" + self._l(user_id, "grace") + ": " + display_time(subscription["grace_until"], self.s.users.get(user_id)["timezone"]).rsplit(" ", 1)[0]
        return self._show(user_id, text, buttons, kind="proxy_home", push=push)

    def _proxy_action(self, user_id, action):
        p = action.split(":"); cmd = p[1]
        if cmd == "country":
            proxies = [x for x in self.s.proxies.list(user_id) if x["country"] == p[2]]
            screen = self._list(user_id, p[2], proxies, lambda x: Button(x["name"], "proxy:view:" + x["id"]), int(p[3]) if len(p) > 3 else 0, "proxy:country:" + p[2])
            screen.kind = "proxy_country"
            return screen
        if cmd == "view": return self._proxy_detail(user_id, p[2])
        if cmd == "add":
            self.s.subscriptions.require_active(user_id)
            self._state(user_id).update(flow="proxy_add", draft={}, waiting=None)
            return self._show(user_id, self._l(user_id, "choose_country"), [Button(x, "proxy:selectcountry:" + x) for x in ("USA", "UK", "Canada", "Germany", "Other")] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "selectcountry":
            return self._start_form(user_id, "proxy_add", {"country": p[2]}, ["proxy_name"])
        if cmd == "type":
            self._state(user_id)["draft"]["type"] = p[2]
            return self._start_form(user_id, "proxy_add", self._state(user_id)["draft"], ["endpoint", "username", "password"])
        if cmd == "save":
            d = self._state(user_id)["draft"]
            self.s.proxies.add(user_id, {"country": d["country"], "name": d["proxy_name"], "type": d["type"], "endpoint": d["endpoint"], "username": d["username"], "password": d["password"]})
            return self._finish(user_id, self._l(user_id, "proxy_saved") + "\n" + self._l(user_id, "unchecked"))
        if cmd == "savedcheck":
            self.s.subscriptions.require_active(user_id); self.s.proxies.get(user_id, p[2])
            return self._show(user_id, self._l(user_id, "check_demo") + "\n" + self._l(user_id, "demo"), [self._b(user_id, "back", "proxy:view:" + p[2])])
        if cmd == "edit":
            self.s.subscriptions.require_active(user_id); self.s.proxies.get(user_id, p[2])
            buttons = [Button(self._l(user_id, key), f"proxy:editfield:{p[2]}:{key}") for key in ("proxy_name", "endpoint", "username", "password")]
            buttons += [self._b(user_id, "country", "proxy:choosecountry:" + p[2]), self._b(user_id, "proxy_type", "proxy:choosetype:" + p[2]), self._b(user_id, "back", "back")]
            return self._show(user_id, self._l(user_id, "edit"), buttons)
        if cmd == "choosecountry":
            self.s.subscriptions.require_active(user_id); self.s.proxies.get(user_id, p[2])
            return self._show(user_id, self._l(user_id, "choose_country"), [Button(x, f"proxy:editcountry:{p[2]}:{x}") for x in ("USA", "UK", "Canada", "Germany", "Other")] + [self._b(user_id, "back", "back")])
        if cmd == "choosetype":
            self.s.subscriptions.require_active(user_id); self.s.proxies.get(user_id, p[2])
            return self._show(user_id, self._l(user_id, "choose_type"), [Button(x, f"proxy:edittype:{p[2]}:{x}") for x in ("HTTP/HTTPS", "SOCKS5")] + [self._b(user_id, "back", "back")])
        if cmd in {"editcountry", "edittype"}:
            field_name = "country" if cmd == "editcountry" else "type"
            self.s.proxies.edit(user_id, p[2], {field_name: p[3]})
            return self._finish(user_id, self._l(user_id, "changes_saved"))
        if cmd == "editfield":
            field_name = "name" if p[3] == "proxy_name" else p[3]
            self._state(user_id).update(flow="proxy_edit", draft={"proxy_id": p[2]}, waiting=field_name)
            return self._show(user_id, self._l(user_id, p[3]), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "delete":
            self.s.subscriptions.require_active(user_id); proxy = self.s.proxies.get(user_id, p[2])
            count = sum(x["proxy_id"] == p[2] for x in self.s.devices.list(user_id))
            return self._show(user_id, f"{self._l(user_id,'delete')}: {proxy['name']}\n{self._l(user_id,'devices_detached')}: {count}", [self._b(user_id, "confirm", "proxy:deleteyes:" + p[2]), self._b(user_id, "cancel", "cancel")])
        if cmd == "deleteyes": self.s.proxies.delete(user_id, p[2]); return self.open_proxies(user_id)
        raise KeyError(action)

    def _proxy_review(self, user_id):
        state = self._state(user_id); d = state["draft"]
        if state["flow"] == "proxy_add" and "type" not in d:
            return self._show(user_id, self._l(user_id, "choose_type"), [Button(x, "proxy:type:" + x) for x in ("HTTP/HTTPS", "SOCKS5")] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        labels = {"country": "country", "proxy_name": "proxy_name_label", "type": "proxy_type", "endpoint": "endpoint_label", "username": "username_label"}
        lines = [f"{self._l(user_id,labels[k])}: {v}" for k, v in d.items() if k in labels]
        lines.append(self._l(user_id, "password_label") + ": " + d["password"])
        return self._show(user_id, self._l(user_id, "review") + "\n" + "\n".join(lines) + "\n" + self._l(user_id, "demo"), [self._b(user_id, "save", "proxy:save"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])

    def _proxy_detail(self, user_id, proxy_id):
        p = self.s.proxies.get(user_id, proxy_id)
        buttons = []
        if self.s.subscriptions.status(user_id)["state"] == "active":
            buttons += [self._b(user_id, "check_saved", "proxy:savedcheck:" + proxy_id), self._b(user_id, "edit", "proxy:edit:" + proxy_id), self._b(user_id, "delete", "proxy:delete:" + proxy_id)]
        buttons.append(self._b(user_id, "back", "back"))
        check = p["check"]
        status = self._l(user_id, "unchecked") if check["status"] == "unchecked" else self._l(user_id, "sim_success" if check["status"] == "success" else "sim_failure")
        if check.get("at"):
            status += "\n" + self._l(user_id, "checked_at") + ": " + display_time(check["at"], self.s.users.get(user_id)["timezone"]).rsplit(" ", 1)[0]
        if check.get("ip"):
            status += f"\n{self._l(user_id,'visible_ip')}: {check['ip']} · {self._l(user_id,'country')}: {check['country']} · {self._l(user_id,'latency')}: {check['latency_ms']}ms"
        if check.get("reason"): status += "\n" + self._l(user_id, "reason") + ": " + self._l(user_id, check["reason"])
        if self.dev_mode and user_id in self.dev_ids:
            buttons += [self._b(user_id, "sim_check_success", "dev:proxycheck:" + proxy_id + ":success"), self._b(user_id, "sim_check_failure", "dev:proxycheck:" + proxy_id + ":failure")]
        return self._show(user_id, f"{p['name']} · {p['country']}\n{p['type']} · {p['endpoint']}\n{self._l(user_id,'username_label')}: {p['username']}\n{self._l(user_id,'password_label')}: {p['password']}\n{status}\n{self._l(user_id,'demo')}", buttons)

    def open_deposit(self, user_id):
        return self._show(user_id, self._l(user_id, "deposit"), [Button("📱 Syriatel Cash", "deposit:syriatel"), Button("💵 Sham Cash", "deposit:sham"), Button("💎 USDT (BEP20)", "deposit:usdt"), self._b(user_id, "back", "back")])

    def _deposit_action(self, user_id, action):
        name = {"deposit:syriatel": "Syriatel Cash", "deposit:sham": "Sham Cash", "deposit:usdt": "USDT (BEP20)"}[action]
        return self._show(user_id, name + "\n" + self._l(user_id, "deposit_placeholder") + "\n" + self._l(user_id, "demo"), [self._b(user_id, "back", "back")])

    def open_tiers(self, user_id):
        buttons = [Button(self._l(user_id, "tier_" + tier) + f" · ${info['price']} · {info['limit']}/24h", "tier:view:" + tier) for tier, info in self.s.tiers.items()]
        buttons.append(self._b(user_id, "back", "back"))
        return self._show(user_id, self._l(user_id, "tiers") + "\n" + self._l(user_id, "demo"), buttons)

    def _tier_action(self, user_id, action):
        tier = action.split(":")[2]; info = self.s.tiers[tier]
        balance_note = self._l(user_id, "insufficient_balance") if self.s.finance.balance(user_id) < info["price"] else self._l(user_id, "demo_balance_only")
        return self._show(user_id, f"{self._l(user_id,'tier_'+tier)}: ${info['price']} · {info['days']} {self._l(user_id,'days')} · {info['limit']}/24h\n{balance_note}\n{self._l(user_id,'tier_placeholder')}\n{self._l(user_id,'demo')}", [self._b(user_id, "back", "back"), self._b(user_id, "deposit", "open:deposit")])

    def open_profile(self, user_id):
        status = self.s.subscriptions.status(user_id); balance = self.s.finance.balance(user_id)
        lines = [self._l(user_id, "profile"), f"{self._l(user_id,'balance')}: ${balance:g}", f"{self._l(user_id,'subscription')}: {self._l(user_id,status['state'])}"]
        zone = self.s.users.get(user_id)["timezone"]
        if status["tier"]: lines.append(self._l(user_id, "tier_" + status["tier"]))
        if status["expires_at"]:
            lines.append(self._l(user_id, "expires") + ": " + display_time(status["expires_at"], zone).rsplit(" ", 1)[0])
            if status["state"] == "active":
                remaining = max(datetime.fromisoformat(status["expires_at"]) - utc_now(), timedelta(0))
                lines.append(self._l(user_id, "remaining") + f": {remaining.days} {self._l(user_id,'days')}, {remaining.seconds // 3600}h")
        if status["grace_until"]: lines.append(self._l(user_id, "grace") + ": " + display_time(status["grace_until"], zone).rsplit(" ", 1)[0])
        if status["state"] == "active":
            limit = self.s.tiers[status["tier"]]["limit"]
            lines.append(f"{self._l(user_id,'quota')}: {max(0, limit - status['used'])}/{limit}")
            if status["window_start"]:
                window = datetime.fromisoformat(status["window_start"]) + timedelta(hours=24)
                lines.append(self._l(user_id, "next_window") + ": " + display_time(window, zone).rsplit(" ", 1)[0])
        if status["queue"]: lines.append(self._l(user_id, "queued") + f": {len(status['queue'])}")
        lines.append(self._l(user_id, "demo"))
        buttons = [self._b(user_id, "referrals", "referrals")]
        if status["queue"]: buttons.append(self._b(user_id, "queued", "profile:queue:0"))
        buttons.append(self._b(user_id, "back", "back"))
        return self._show(user_id, "\n".join(lines), buttons)

    def _profile_queue(self, user_id, page):
        queue = self.s.subscriptions.status(user_id)["queue"]
        page = max(0, min(page, max(0, (len(queue) - 1) // self.PAGE_SIZE)))
        visible = queue[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]
        lines = [self._l(user_id, "queued")]
        lines += [f"{page*self.PAGE_SIZE+i+1}. {self._l(user_id,'tier_'+x['tier'])} · ${x['price']} · {x['days']} {self._l(user_id,'days')} · {x['limit']}/24h" for i, x in enumerate(visible)]
        buttons = []
        if page: buttons.append(Button("◀", f"profile:queue:{page-1}"))
        if (page + 1) * self.PAGE_SIZE < len(queue): buttons.append(Button("▶", f"profile:queue:{page+1}"))
        buttons.append(self._b(user_id, "back", "back"))
        return self._show(user_id, "\n".join(lines), buttons)

    def open_referrals(self, user_id):
        stats = self.s.referrals.stats(user_id)
        link = f"https://t.me/{self.bot_username}?start=ref_{user_id}" if self.bot_username else self._l(user_id, "bot_username_missing")
        text = f"{self._l(user_id,'referrals')}\n{link}\n{self._l(user_id,'count')}: {stats['count']}\n{self._l(user_id,'earnings')}: ${stats['earnings']:g}\n{stats['rate']*100:g}% {self._l(user_id,'confirmed_deposits')}\n{self._l(user_id,'demo')}"
        return self._show(user_id, text, [self._b(user_id, "back", "back")])

    def open_support(self, user_id):
        if len(self.support_links) != 2 or not all(self.support_links):
            return self._show(user_id, self._l(user_id, "support_missing"), [self._b(user_id, "back", "back")])
        return self._show(user_id, self._l(user_id, "support"), [Button(self._l(user_id, "support_one"), url=self.support_links[0]), Button(self._l(user_id, "support_two"), url=self.support_links[1]), self._b(user_id, "back", "back")])

    def open_plans(self, user_id):
        buttons = []
        if self.s.subscriptions.status(user_id)["state"] == "active": buttons.append(self._b(user_id, "add_plan", "plan:add"))
        buttons += [self._b(user_id, "saved_plans", "plan:saved"), self._b(user_id, "back", "back")]
        status = self.s.subscriptions.status(user_id)
        note = "\n" + self._l(user_id, "denied") if status["state"] != "active" else ""
        if status["grace_until"]: note += "\n" + self._l(user_id, "grace") + ": " + display_time(status["grace_until"], self.s.users.get(user_id)["timezone"]).rsplit(" ", 1)[0]
        return self._show(user_id, self._l(user_id, "plans") + note, buttons)

    def _plan_action(self, user_id, action):
        p = action.split(":"); cmd = p[1]; state = self._state(user_id)
        if cmd == "add":
            self.s.subscriptions.require_active(user_id)
            state.update(flow="plan_add", draft={"operations": []}, waiting=None)
            devices = self.s.devices.list(user_id)
            if not devices: return self._show(user_id, self._l(user_id, "empty_devices"), [self._b(user_id, "back", "open:devices"), self._b(user_id, "cancel", "cancel")])
            return self._list(user_id, self._l(user_id, "choose_device"), devices, lambda d: Button(d["name"], "plan:device:" + d["id"]), 0, "plan:devices", [self._b(user_id, "cancel", "cancel")])
        if cmd == "devices":
            self.s.subscriptions.require_active(user_id)
            devices = self.s.devices.list(user_id)
            return self._list(user_id, self._l(user_id, "choose_device"), devices, lambda d: Button(d["name"], "plan:device:" + d["id"]), int(p[2]), "plan:devices", [self._b(user_id, "cancel", "cancel")])
        if cmd == "device":
            self.s.devices.get(user_id, p[2]); state["draft"]["device_id"] = p[2]
            return self._show(user_id, self._l(user_id, "choose_plan_type"), [self._b(user_id, "uniform", "plan:mode:uniform"), self._b(user_id, "multiple", "plan:mode:multiple"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "mode":
            state["draft"]["mode"] = p[2]
            linked = self.s.devices.linked(user_id, state["draft"]["device_id"])
            if not linked: return self._show(user_id, self._l(user_id, "no_linked"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            return self._list(user_id, self._l(user_id, "choose_linked"), linked, lambda x: Button(self.s.catalog.get(x["game_id"])["name"], "plan:game:" + x["id"]), 0, "plan:linked", [self._b(user_id, "cancel", "cancel")])
        if cmd == "linked":
            self.s.subscriptions.require_active(user_id)
            linked = self.s.devices.linked(user_id, state["draft"]["device_id"])
            return self._list(user_id, self._l(user_id, "choose_linked"), linked, lambda x: Button(self.s.catalog.get(x["game_id"])["name"], "plan:game:" + x["id"]), int(p[2]), "plan:linked", [self._b(user_id, "cancel", "cancel")])
        if cmd == "game":
            linked = self.s.devices.get_linked(user_id, p[2]); draft = state["draft"]
            if linked["device_id"] != draft["device_id"]: raise ValidationError("incompatible_game")
            game = self.s.catalog.get(linked["game_id"])
            if not game["normal"]: return self._show(user_id, self._l(user_id, "no_events"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            draft["linked_id"] = p[2]
            if draft["mode"] == "uniform":
                state["waiting"] = "shared_interval"
                return self._show(user_id, self._l(user_id, "shared_interval"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
            return self._plan_choose_event(user_id)
        if cmd == "template":
            draft = state["draft"]
            if state["flow"] == "plan_edit":
                plan = self.s.plans.get(user_id, draft["plan_id"])
                game = self.s.catalog.get(self.s.devices.get_linked(user_id, plan["linked_id"])["game_id"])
            else:
                game = self.s.catalog.get(self.s.devices.get_linked(user_id, draft["linked_id"])["game_id"])
            draft["template"] = game["normal"][int(p[2])]; draft["values"] = []; draft["value_index"] = 0
            return self._variables_or_next(user_id)
        if cmd == "continue":
            if len(state["draft"]["operations"]) >= 20: raise ValidationError("too_many_operations")
            return self._plan_choose_event(user_id)
        if cmd == "finish": return self._plan_review(user_id)
        if cmd == "draftlist":
            draft = state["draft"]
            buttons = [Button(f"{i+1}. {render_template(x['template'],x['values'])}", "plan:draftop:" + str(i)) for i, x in enumerate(draft["operations"])]
            buttons += [self._b(user_id, "continue", "plan:continue")]
            if draft["mode"] == "uniform": buttons.append(self._b(user_id, "shared_interval", "plan:draftshared"))
            buttons += [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")]
            return self._show(user_id, self._l(user_id, "edit"), buttons, kind="plan_draftlist")
        if cmd == "draftop":
            draft = state["draft"]; index = int(p[2]); operation = draft["operations"][index]
            draft.update(draft_index=index, template=operation["template"], values=list(operation["values"]), current_interval=operation.get("interval"))
            state["flow"] = "plan_draft_edit"
            game = self.s.catalog.get(self.s.devices.get_linked(user_id, draft["linked_id"])["game_id"])
            return self._show(user_id, self._l(user_id, "choose_event"), [Button(x, "plan:template:" + str(i)) for i, x in enumerate(game["normal"])] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "draftshared":
            state.update(flow="plan_draft_edit_shared", waiting="shared_interval")
            return self._show(user_id, self._l(user_id, "shared_interval"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "draftsave":
            draft = state["draft"]
            draft["operations"][draft["draft_index"]] = {"template": draft["template"], "values": list(draft["values"]), "interval": draft.get("current_interval") if draft["mode"] == "multiple" else None}
            for key in ("draft_index", "template", "values", "value_index", "current_interval"): draft.pop(key, None)
            state["flow"] = "plan_add"
            return self._plan_review(user_id)
        if cmd == "confirm":
            draft = state["draft"]
            self.s.plans.add(user_id, draft["device_id"], draft["linked_id"], draft["mode"], draft.get("shared"), draft["operations"])
            return self._finish(user_id, self._l(user_id, "plan_saved"))
        if cmd == "saved": return self._plan_saved_devices(user_id, 0)
        if cmd == "savedpage": return self._plan_saved_devices(user_id, int(p[2]))
        if cmd == "saveddevice": return self._plan_saved_device(user_id, p[2], 0)
        if cmd == "listpage": return self._plan_saved_device(user_id, p[2], int(p[3]))
        if cmd == "view": return self._plan_detail(user_id, p[2])
        if cmd == "delete":
            plan = self.s.plans.get(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            return self._show(user_id, f"{self._l(user_id,'plan_delete')} #{plan['number']} · {self._l(user_id,'operations')}: {len(plan['operations'])}", [self._b(user_id, "confirm", "plan:deleteyes:" + p[2]), self._b(user_id, "cancel", "cancel")])
        if cmd == "deleteyes": self.s.plans.delete(user_id, p[2]); return self.open_plans(user_id)
        if cmd == "edit":
            plan = self.s.plans.get(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            pending = [x for x in plan["operations"] if x["status"] == "pending"]
            if not pending: return self._show(user_id, self._l(user_id, "no_pending"), [self._b(user_id, "back", "back")])
            buttons = [Button(f"{x['order']}. {x['event']}", "plan:editop:" + plan["id"] + ":" + x["id"]) for x in sorted(pending, key=lambda x: x["order"])]
            if plan["mode"] == "uniform": buttons.append(self._b(user_id, "shared_interval", "plan:editshared:" + plan["id"]))
            buttons.append(self._b(user_id, "back", "back"))
            return self._show(user_id, self._l(user_id, "plan_edit"), buttons)
        if cmd == "editop":
            plan = self.s.plans.get(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            op = next(x for x in plan["operations"] if x["id"] == p[3] and x["status"] == "pending")
            state.update(flow="plan_edit", draft={"plan_id": p[2], "op_id": p[3], "template": op["template"], "values": list(op["values"]), "interval": op["interval"]}, waiting=None)
            game = self.s.catalog.get(self.s.devices.get_linked(user_id, plan["linked_id"])["game_id"])
            return self._show(user_id, self._l(user_id, "choose_event"), [Button(x, "plan:template:" + str(i)) for i, x in enumerate(game["normal"])] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "editshared":
            self.s.plans.get(user_id, p[2]); self.s.subscriptions.require_active(user_id)
            state.update(flow="plan_edit", draft={"plan_id": p[2]}, waiting="shared_interval")
            return self._show(user_id, self._l(user_id, "shared_interval"), [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])
        if cmd == "editsave":
            draft = state["draft"]; plan = self.s.plans.get(user_id, draft["plan_id"])
            pending = [deepcopy(x) for x in plan["operations"] if x["status"] == "pending"]
            for op in pending:
                if op["id"] == draft.get("op_id"):
                    op.update(template=draft["template"], values=draft["values"], interval=draft.get("interval"), event=render_template(draft["template"], draft["values"]))
            self.s.plans.edit_pending(user_id, plan["id"], pending, draft.get("shared"))
            return self._finish(user_id, self._l(user_id, "changes_saved"))
        raise KeyError(action)

    def _plan_choose_event(self, user_id):
        state = self._state(user_id); draft = state["draft"]
        game = self.s.catalog.get(self.s.devices.get_linked(user_id, draft["linked_id"])["game_id"])
        if not game["normal"]: return self._show(user_id, self._l(user_id, "no_events"), [self._b(user_id, "back", "back")])
        if len(game["normal"]) == 1:
            draft["template"] = game["normal"][0]; draft["values"] = []; draft["value_index"] = 0
            return self._variables_or_next(user_id)
        return self._show(user_id, self._l(user_id, "choose_event"), [Button(x, "plan:template:" + str(i)) for i, x in enumerate(game["normal"])] + [self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")])

    def _plan_add_operation(self, user_id):
        draft = self._state(user_id)["draft"]
        draft["operations"].append({"template": draft["template"], "values": list(draft["values"]), "interval": draft.get("current_interval") if draft["mode"] == "multiple" else None})
        for key in ("template", "values", "value_index", "current_interval"): draft.pop(key, None)
        return self._show(user_id, f"{self._l(user_id,'operations')}: {len(draft['operations'])}\n{self._l(user_id,'demo')}", [self._b(user_id, "continue", "plan:continue"), self._b(user_id, "finish", "plan:finish"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")], kind="plan_count")

    def _plan_review(self, user_id):
        draft = self._state(user_id)["draft"]; now = utc_now()
        device = self.s.devices.get(user_id, draft["device_id"])
        game = self.s.catalog.get(self.s.devices.get_linked(user_id, draft["linked_id"])["game_id"])
        ops = [{"id": str(i + 1), "order": i + 1, **x} for i, x in enumerate(draft["operations"])]
        due = due_dates(draft["mode"], ops, now, draft.get("shared"), rules=self.s.validation_rules)
        if not due: raise ValidationError("invalid_schedule")
        lines = [self._l(user_id, "review"), *self._plan_lines(user_id, device, game, draft["mode"], draft.get("shared"), due)]
        lines.append(self._l(user_id, "demo"))
        return self._show(user_id, "\n".join(lines), [self._b(user_id, "edit", "plan:draftlist"), self._b(user_id, "save", "plan:confirm"), self._b(user_id, "cancel", "cancel")], kind="plan_review")

    def _plan_draft_edit_review(self, user_id):
        draft = self._state(user_id)["draft"]
        device = self.s.devices.get(user_id, draft["device_id"])
        game = self.s.catalog.get(self.s.devices.get_linked(user_id, draft["linked_id"])["game_id"])
        operations = deepcopy(draft["operations"])
        operations[draft["draft_index"]] = {"template": draft["template"], "values": draft["values"], "interval": draft.get("current_interval")}
        lines = [self._l(user_id, "review"), *self._plan_lines(user_id, device, game, draft["mode"], draft.get("shared"), operations)]
        return self._show(user_id, "\n".join(lines), [self._b(user_id, "save", "plan:draftsave"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")], kind="plan_draft_edit_review")

    def _plan_lines(self, user_id, device, game, mode, shared, operations):
        lines = [f"{self._l(user_id,'device_label')}: {device['name']}", f"{self._l(user_id,'game_label')}: {game['name']}"]
        if mode == "uniform": lines.append(f"{self._l(user_id,'interval_label')}: {shared}")
        lines.append("")
        ordered = sorted(operations, key=lambda op: op.get("order", 0))
        for index, op in enumerate(ordered, start=1):
            event = render_template(op["template"], op["values"])
            line = f"{op.get('order', index)}. {event}"
            if mode == "multiple": line += f" — {op['interval']}"
            lines.append(line)
        return lines

    def _plan_saved_devices(self, user_id, page):
        ids = {x["device_id"] for x in self.s.plans.list(user_id)}
        devices = [x for x in self.s.devices.list(user_id) if x["id"] in ids]
        return self._list(user_id, self._l(user_id, "saved_plans"), devices, lambda d: Button(("🍎 " if d["os"] == "iOS" else "🤖 ") + d["name"] + f" ({sum(p['device_id']==d['id'] for p in self.s.plans.list(user_id))})", "plan:saveddevice:" + d["id"]), page, "plan:savedpage")

    def _plan_saved_device(self, user_id, device_id, page):
        device = self.s.devices.get(user_id, device_id)
        plans = [x for x in self.s.plans.list(user_id) if x["device_id"] == device_id]
        def label(plan):
            game = self.s.catalog.get(self.s.devices.get_linked(user_id, plan["linked_id"])["game_id"])
            attempted = sum(x["status"] != "pending" for x in plan["operations"])
            return f"{game['name']} — #{plan['number']} · {self._l(user_id,plan['mode'])} · {attempted}/{len(plan['operations'])} · {self._plan_status(user_id,plan)}"
        return self._list(user_id, device["name"], plans, lambda x: Button(label(x), "plan:view:" + x["id"]), page, "plan:listpage:" + device_id)

    def _plan_status(self, user_id, plan):
        status = self.s.subscriptions.status(user_id)
        if not any(x["status"] == "pending" for x in plan["operations"]): return self._l(user_id, "completed")
        if status["state"] == "expired": return self._l(user_id, "frozen_subscription")
        if status["state"] == "active" and status["used"] >= self.s.tiers[status["tier"]]["limit"]: return self._l(user_id, "frozen_quota")
        return self._l(user_id, "scheduled")

    def _plan_detail(self, user_id, plan_id):
        plan = self.s.plans.get(user_id, plan_id); device = self.s.devices.get(user_id, plan["device_id"])
        game = self.s.catalog.get(self.s.devices.get_linked(user_id, plan["linked_id"])["game_id"])
        zone = self.s.users.get(user_id)["timezone"]
        operations = sorted(plan["operations"], key=lambda x: x["order"])
        attempted = sum(x["status"] != "pending" for x in operations)
        next_due = min((x["due_at"] for x in operations if x["status"] == "pending"), default=None)
        lines = self._plan_lines(user_id, device, game, plan["mode"], plan["shared_interval"], operations)
        lines += ["", f"{self._plan_status(user_id,plan)} · {attempted}/{len(operations)}"]
        if next_due: lines.append(f"{self._l(user_id,'next_due')}: {display_time(next_due,zone).rsplit(' ', 1)[0]}")
        for op in operations:
            if op["status"] != "pending":
                lines.append(f"{op['order']}. {self._l(user_id,op['status'])}")
                if op.get("detail"): lines.append(self._l(user_id, op["detail"]))
        status = self.s.subscriptions.status(user_id)
        if status["grace_until"]: lines.append(self._l(user_id, "grace") + ": " + display_time(status["grace_until"], zone).rsplit(" ", 1)[0])
        lines.append(self._l(user_id, "demo"))
        buttons = []
        if status["state"] == "active": buttons += [self._b(user_id, "plan_edit", "plan:edit:" + plan_id), self._b(user_id, "plan_delete", "plan:delete:" + plan_id)]
        buttons.append(self._b(user_id, "back", "back"))
        return self._show(user_id, "\n".join(lines), buttons)

    def _plan_edit_review(self, user_id):
        draft = self._state(user_id)["draft"]; plan = self.s.plans.get(user_id, draft["plan_id"])
        pending = [deepcopy(x) for x in plan["operations"] if x["status"] == "pending"]
        for op in pending:
            if op["id"] == draft.get("op_id"):
                op.update(template=draft["template"], values=draft["values"], interval=draft.get("interval"), event=render_template(draft["template"], draft["values"]))
        revised = due_dates(plan["mode"], pending, utc_now(), draft.get("shared") or plan["shared_interval"], rules=self.s.validation_rules)
        device = self.s.devices.get(user_id, plan["device_id"])
        game = self.s.catalog.get(self.s.devices.get_linked(user_id, plan["linked_id"])["game_id"])
        lines = [self._l(user_id, "review"), self._l(user_id, "pending_only") + ":",
                 *self._plan_lines(user_id, device, game, plan["mode"], draft.get("shared") or plan["shared_interval"], revised)]
        return self._show(user_id, "\n".join(lines), [self._b(user_id, "save", "plan:editsave"), self._b(user_id, "back", "back"), self._b(user_id, "cancel", "cancel")], kind="plan_edit_review")

    def _dev_action(self, user_id, action):
        if not self.dev_mode or user_id not in self.dev_ids: return self._show(user_id, self._l(user_id, "dev_forbidden"), [self._b(user_id, "back", "menu")], kind="denied")
        parts = action.split(":"); cmd = parts[1]
        if cmd == "proxycheck":
            self.s.proxies.dev_check(user_id, parts[2], parts[3])
            return self._proxy_detail(user_id, parts[2])
        if cmd == "menu":
            labels = ("new", "active", "expired", "advance", "renew", "exhaust", "reset", "extend", "queue", "fail", "balance")
            buttons = [Button(self._l(user_id, "dev_" + key), "dev:" + key) for key in labels]
            buttons.append(self._b(user_id, "back", "menu"))
            return self._show(user_id, self._l(user_id, "dev") + "\n" + self._l(user_id, "demo"), buttons)
        if cmd in {"new", "active", "expired"}: self.s.subscriptions.dev_state(user_id, cmd)
        elif cmd == "advance":
            from datetime import timedelta
            self.s.subscriptions.dev_advance(user_id, utc_now() + timedelta(days=32))
        elif cmd == "renew": self.s.subscriptions.dev_renew(user_id, "monthly")
        elif cmd == "extend": self.s.subscriptions.dev_renew(user_id, "monthly")
        elif cmd == "queue": self.s.subscriptions.dev_renew(user_id, "weekly")
        elif cmd == "balance": self.s.finance.dev_balance(user_id, 25)
        elif cmd == "exhaust": self.s.subscriptions.dev_quota(user_id, True)
        elif cmd == "reset": self.s.subscriptions.dev_quota(user_id, False)
        elif cmd == "fail":
            plans = self.s.plans.list(user_id)
            if plans: self.s.plans.dev_fail_first(user_id, plans[0]["id"])
        else: raise KeyError(action)
        return self._show(user_id, self._l(user_id, "dev_updated") + "\n" + self._l(user_id, "demo"), [self._b(user_id, "back", "dev:menu")])
