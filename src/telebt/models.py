from typing import Literal, TypedDict

OS = Literal["Android", "iOS"]
Platform = Literal["AppsFlyer", "Adjust", "Singular"]


class Game(TypedDict):
    id: str
    name: str
    os: OS
    platform: Platform
    normal: list[str]
    purchase: list[str]


class Device(TypedDict):
    id: str
    user_id: int
    os: OS
    name: str
    identifiers: dict[str, str]
    proxy_id: str | None


class PlanOperation(TypedDict):
    id: str
    order: int
    template: str
    values: list[str]
    event: str
    interval: str | None
    due_at: str
    status: str


class Plan(TypedDict):
    id: str
    user_id: int
    device_id: str
    linked_id: str
    number: int
    mode: Literal["uniform", "multiple"]
    shared_interval: str | None
    operations: list[PlanOperation]
