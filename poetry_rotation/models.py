"""数据模型。

字段名与 data/*.csv 的表头保持一致；CSV 中的空字符串统一解析为 None。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Item:
    item_id: str
    quarter: str
    title: str
    format: str
    course_goal: str
    revision: str


@dataclass(frozen=True)
class Exposure:
    item_id: str
    quarter: str
    slot: str
    minutes: int
    available: bool


@dataclass(frozen=True)
class Session:
    session_id: str
    item_id: str
    quarter: str
    anonymous_group: str
    channel: str
    interactions: int


@dataclass(frozen=True)
class Commitment:
    course_id: str
    quarter: str
    item_id: str
    slot: str
    goal: str


@dataclass
class Dataset:
    quarter: str
    items: dict[str, Item] = field(default_factory=dict)
    exposures: list[Exposure] = field(default_factory=list)
    sessions: list[Session] = field(default_factory=list)
    commitments: list[Commitment] = field(default_factory=list)
    # 载入阶段发现的结构 / 引用问题（不致命，但报告必须披露）
    issues: list[str] = field(default_factory=list)

    # ---- 便捷索引（在 loading.assemble 中填充） ----
    exposure_by_item: dict[str, list[Exposure]] = field(default_factory=dict)
    sessions_by_item: dict[str, list[Session]] = field(default_factory=dict)
    commitments_by_item: dict[str, list[Commitment]] = field(default_factory=dict)

    def item_ids(self) -> list[str]:
        return sorted(self.items)

    def slots_of(self, item_id: str) -> set[str]:
        return {e.slot for e in self.exposure_by_item.get(item_id, ()) if e.available}

    def channels_of(self, item_id: str) -> set[str]:
        return {s.channel for s in self.sessions_by_item.get(item_id, ())}

    def goals_of(self, item_id: str) -> set[str]:
        goals: set[str] = set()
        item = self.items.get(item_id)
        if item is not None and item.course_goal:
            goals.add(item.course_goal)
        for c in self.commitments_by_item.get(item_id, ()):
            if c.goal:
                goals.add(c.goal)
        return goals

    def session_count(self, item_id: str) -> int:
        return len(self.sessions_by_item.get(item_id, ()))
