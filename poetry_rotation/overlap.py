"""共同曝光与共同使用分析。

要区分三种“看起来像、其实不能判同质”的情形：

1. 两个项目从不同时开放（排班决定了没人能同时用）；
2. 共同使用只来自团体渠道（同一条团体路线把它们串起来）；
3. 有充分共同曝光、样本也够，但确实少有共同使用（这才是互补证据）。

冷启动项目不参与同质 / 互补的定性，只保留带解释的暂定位置。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from .config import (
    COMPLEMENTARY,
    DUPLICATE,
    INCONCLUSIVE,
    INSUFFICIENT_EXPOSURE,
    ROUTE_ONLY,
    SCHEDULE_SEPARATED,
    Thresholds,
)
from .models import Dataset

WALK_IN_CHANNEL = "散客"


@dataclass(frozen=True)
class PairSignals:
    item_a: str
    item_b: str
    shared_slots: frozenset[str]
    coexposure_minutes: int
    coexposure_slot_count: int
    groups_a: frozenset[str]
    groups_b: frozenset[str]
    shared_groups: frozenset[str]
    couse_support: int
    jaccard: float
    couse_rate: float
    walkin_shared_groups: frozenset[str]
    group_only_shared: bool
    sessions_a: int
    sessions_b: int

    def as_dict(self) -> dict:
        return {
            "item_a": self.item_a,
            "item_b": self.item_b,
            "shared_slots": sorted(self.shared_slots),
            "coexposure_minutes": self.coexposure_minutes,
            "coexposure_slot_count": self.coexposure_slot_count,
            "sessions_a": self.sessions_a,
            "sessions_b": self.sessions_b,
            "groups_a": len(self.groups_a),
            "groups_b": len(self.groups_b),
            "couse_support": self.couse_support,
            "jaccard": round(self.jaccard, 4),
            "couse_rate": round(self.couse_rate, 4),
            "walkin_shared_groups": sorted(self.walkin_shared_groups),
            "group_only_shared": self.group_only_shared,
        }


@dataclass
class PairVerdict:
    item_a: str
    item_b: str
    verdict: str
    reason_codes: list[str] = field(default_factory=list)
    signals: PairSignals | None = None
    cold_items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "item_a": self.item_a,
            "item_b": self.item_b,
            "verdict": self.verdict,
            "reason_codes": self.reason_codes,
            "cold_items": self.cold_items,
            "signals": self.signals.as_dict() if self.signals else None,
        }


def build_group_index(dataset: Dataset) -> dict[str, dict[str, set[str]]]:
    """item_id -> {匿名群体 -> 该群体在此项目上的渠道集合}。"""
    index: dict[str, dict[str, set[str]]] = {item_id: {} for item_id in dataset.items}
    for session in dataset.sessions:
        channels = index.setdefault(session.item_id, {}).setdefault(session.anonymous_group, set())
        channels.add(session.channel)
    return index


def _slot_minutes(dataset: Dataset) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for exposure in dataset.exposures:
        if not exposure.available:
            continue
        result.setdefault(exposure.item_id, {})[exposure.slot] = exposure.minutes
    return result


def pair_signals(dataset: Dataset, item_a: str, item_b: str) -> PairSignals:
    slots_a = dataset.slots_of(item_a)
    slots_b = dataset.slots_of(item_b)
    shared_slots = slots_a & slots_b

    minutes = _slot_minutes(dataset)
    coexposure_minutes = 0
    for slot in shared_slots:
        coexposure_minutes += min(
            minutes.get(item_a, {}).get(slot, 0),
            minutes.get(item_b, {}).get(slot, 0),
        )

    group_index = build_group_index(dataset)
    groups_a = set(group_index.get(item_a, {}))
    groups_b = set(group_index.get(item_b, {}))
    shared = groups_a & groups_b

    union = groups_a | groups_b
    jaccard = len(shared) / len(union) if union else 0.0
    smaller = min(len(groups_a), len(groups_b))
    couse_rate = len(shared) / smaller if smaller else 0.0

    walkin_shared: set[str] = set()
    for group in shared:
        channels_a = group_index[item_a].get(group, set())
        channels_b = group_index[item_b].get(group, set())
        if WALK_IN_CHANNEL in channels_a or WALK_IN_CHANNEL in channels_b:
            walkin_shared.add(group)
    group_only = bool(shared) and not walkin_shared

    return PairSignals(
        item_a=item_a,
        item_b=item_b,
        shared_slots=frozenset(shared_slots),
        coexposure_minutes=coexposure_minutes,
        coexposure_slot_count=len(shared_slots),
        groups_a=frozenset(groups_a),
        groups_b=frozenset(groups_b),
        shared_groups=frozenset(shared),
        couse_support=len(shared),
        jaccard=jaccard,
        couse_rate=couse_rate,
        walkin_shared_groups=frozenset(walkin_shared),
        group_only_shared=group_only,
        sessions_a=dataset.session_count(item_a),
        sessions_b=dataset.session_count(item_b),
    )


def cold_items(dataset: Dataset, thresholds: Thresholds) -> list[str]:
    """会话数低于阈值的项目：冷启动或长期遇冷，只能给暂定位置。"""
    return sorted(
        item_id
        for item_id in dataset.items
        if dataset.session_count(item_id) < thresholds.cold_min_sessions
    )


def classify_pair(
    dataset: Dataset,
    item_a: str,
    item_b: str,
    thresholds: Thresholds,
    cold: set[str] | None = None,
) -> PairVerdict:
    cold = set(cold or cold_items(dataset, thresholds))
    signals = pair_signals(dataset, item_a, item_b)
    reasons: list[str] = []
    cold_in_pair = sorted(cold & {item_a, item_b})

    # 1) 排班分离：没有任何共同开放时段（确定性结论，与冷启动状态无关）
    if not signals.shared_slots:
        reasons.append("no_shared_slot")
        if signals.shared_groups:
            reasons.append("couse_across_separate_slots_via_routing")
        return PairVerdict(item_a, item_b, SCHEDULE_SEPARATED, reasons, signals, [])

    # 2) 共同曝光不充分
    enough_minutes = signals.coexposure_minutes >= thresholds.sufficient_coexposure_minutes
    enough_slots = signals.coexposure_slot_count >= thresholds.sufficient_coexposure_slots
    if not (enough_minutes and enough_slots):
        reasons.append("coexposure_below_threshold")
        return PairVerdict(item_a, item_b, INSUFFICIENT_EXPOSURE, reasons, signals, cold_in_pair)

    # 3) 共同使用只来自团体路线（确定性的“假相似”结论）
    if signals.shared_groups and signals.group_only_shared:
        reasons.append("all_couse_from_group_channel")
        return PairVerdict(item_a, item_b, ROUTE_ONLY, reasons, signals, [])

    # 4) 共同使用样本不足
    if signals.couse_support < thresholds.min_couse_support:
        reasons.append("couse_support_below_threshold")
        if signals.couse_rate <= thresholds.complementary_couse_rate:
            reasons.append("low_couse_rate_but_thin_support")
        # 冷启动项目在此分支尤其常见：没有共同使用不能证明任何事
        if cold_in_pair:
            reasons.append("cold_item_provisional")
            return PairVerdict(item_a, item_b, INSUFFICIENT_EXPOSURE, reasons, signals, cold_in_pair)
        return PairVerdict(item_a, item_b, INCONCLUSIVE, reasons, signals, [])

    # 样本充分后的定性；若涉及冷启动项目，一律降级为暂不判定
    if signals.couse_rate >= thresholds.duplicate_couse_rate:
        if cold_in_pair:
            reasons.append("cold_item_provisional")
            return PairVerdict(item_a, item_b, INCONCLUSIVE, reasons, signals, cold_in_pair)
        reasons.append("high_couse_rate_under_sufficient_exposure")
        return PairVerdict(item_a, item_b, DUPLICATE, reasons, signals, [])

    if signals.couse_rate <= thresholds.complementary_couse_rate:
        if cold_in_pair:
            reasons.append("cold_item_provisional")
            return PairVerdict(item_a, item_b, INCONCLUSIVE, reasons, signals, cold_in_pair)
        reasons.append("low_couse_rate_under_sufficient_exposure")
        return PairVerdict(item_a, item_b, COMPLEMENTARY, reasons, signals, [])

    reasons.append("couse_rate_in_middle_band")
    return PairVerdict(item_a, item_b, INCONCLUSIVE, reasons, signals, cold_in_pair)


def analyze_pairs(dataset: Dataset, thresholds: Thresholds | None = None) -> list[PairVerdict]:
    thresholds = thresholds or Thresholds()
    cold = set(cold_items(dataset, thresholds))
    verdicts = [
        classify_pair(dataset, item_a, item_b, thresholds, cold)
        for item_a, item_b in combinations(sorted(dataset.items), 2)
    ]
    verdicts.sort(key=lambda verdict: (verdict.verdict, verdict.item_a, verdict.item_b))
    return verdicts
