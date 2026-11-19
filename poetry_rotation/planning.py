"""受约束的轮换方案生成与比较。

互换约束（任一不满足则该撤项不可行）：
1. 开放时间：替代项目必须覆盖被撤项目的全部开放时段；
2. 交互形式：替代项目与被撤项目形式一致；
3. 课程目标：替代项目必须覆盖被撤项目承担的每一个课程目标，
   覆盖按“不同目标”逐个核对，不允许用同一组保留点位的数量冒充覆盖。

冷启动 / 遇冷项目不会因“没有共同使用记录”被撤，也不会因同样原因被当作互补保留；
它们只以暂定位置出现在方案里，并附带资料稀薄说明。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .config import (
    DUPLICATE,
    Thresholds,
)
from .models import Dataset
from .overlap import PairVerdict, analyze_pairs, cold_items


@dataclass
class Replacement:
    removed_item: str
    substitute_item: str
    matched_slots: list[str]
    matched_format: str
    goals_covered: list[str]
    pair_verdict: str
    pair_reason_codes: list[str]

    def as_dict(self) -> dict:
        return {
            "removed_item": self.removed_item,
            "substitute_item": self.substitute_item,
            "matched_slots": self.matched_slots,
            "matched_format": self.matched_format,
            "goals_covered": self.goals_covered,
            "pair_verdict": self.pair_verdict,
            "pair_reason_codes": self.pair_reason_codes,
        }


@dataclass
class RotationPlan:
    plan_id: str
    removals: list[str]
    replacements: list[Replacement]
    blockers: list[str]
    warnings: list[str]
    committed_coverage_after: dict
    lost_commitments: list[dict]
    score: float
    feasible: bool

    def as_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "removals": self.removals,
            "replacements": [r.as_dict() for r in self.replacements],
            "blockers": self.blockers,
            "warnings": self.warnings,
            "committed_coverage_after": self.committed_coverage_after,
            "lost_commitments": self.lost_commitments,
            "score": round(self.score, 4),
            "feasible": self.feasible,
        }


def _verdict_index(verdicts: list[PairVerdict]) -> dict[tuple[str, str], PairVerdict]:
    index: dict[tuple[str, str], PairVerdict] = {}
    for verdict in verdicts:
        index[(verdict.item_a, verdict.item_b)] = verdict
        index[(verdict.item_b, verdict.item_a)] = verdict
    return index


def eligible_substitutes(dataset: Dataset, item_id: str) -> list[str]:
    """满足开放时间、交互形式、课程目标硬约束的替代候选。"""
    item = dataset.items[item_id]
    required_slots = dataset.slots_of(item_id)
    required_goals = dataset.goals_of(item_id)
    result: list[str] = []
    for other_id, other in dataset.items.items():
        if other_id == item_id:
            continue
        if other.format != item.format:
            continue
        if not required_slots <= dataset.slots_of(other_id):
            continue
        if not required_goals <= dataset.goals_of(other_id):
            continue
        result.append(other_id)
    return sorted(result)


def _commitment_coverage(
    dataset: Dataset, kept: set[str]
) -> tuple[dict, list[dict]]:
    """逐条核对课程承诺在保留集合下是否仍有同同时段、同目标的项目覆盖。"""
    coverage: dict[str, dict[str, dict[str, list[str]]]] = {}
    lost: list[dict] = []
    for commitment in dataset.commitments:
        carriers = sorted(
            item_id
            for item_id in kept
            if commitment.slot in dataset.slots_of(item_id)
            and commitment.goal in dataset.goals_of(item_id)
        )
        course = coverage.setdefault(commitment.course_id, {})
        slot = course.setdefault(commitment.slot, {})
        slot[commitment.goal] = carriers
        if not carriers:
            lost.append(
                {
                    "course_id": commitment.course_id,
                    "slot": commitment.slot,
                    "goal": commitment.goal,
                    "original_item": commitment.item_id,
                }
            )
    return coverage, lost


def _duplicate_partners(
    item_id: str, verdict_index: dict[tuple[str, str], PairVerdict]
) -> list[str]:
    partners: list[str] = []
    for (left, right), verdict in verdict_index.items():
        if left == item_id and verdict.verdict == DUPLICATE:
            partners.append(right)
    return sorted(partners)


def candidate_removals(
    dataset: Dataset,
    thresholds: Thresholds,
    verdict_index: dict[tuple[str, str], PairVerdict],
    cold: set[str],
) -> list[str]:
    """只把“有重复证据且存在合格替代”的非冷启动项目放进撤项池。"""
    pool: list[str] = []
    for item_id in dataset.items:
        if item_id in cold:
            continue
        partners = _duplicate_partners(item_id, verdict_index)
        if not partners:
            continue
        substitutes = set(eligible_substitutes(dataset, item_id))
        if substitutes & set(partners):
            pool.append(item_id)
    return sorted(pool)


def _build_plan(
    dataset: Dataset,
    removals: list[str],
    plan_id: str,
    thresholds: Thresholds,
    verdict_index: dict[tuple[str, str], PairVerdict],
    cold: set[str],
) -> RotationPlan:
    kept = set(dataset.items) - set(removals)
    blockers: list[str] = []
    warnings: list[str] = []
    replacements: list[Replacement] = []

    for removed in removals:
        if removed in cold:
            blockers.append(f"{removed} 资料稀薄（冷启动/遇冷），不允许仅凭无共同使用记录撤下")
        feasible = eligible_substitutes(dataset, removed)
        feasible_kept = [item_id for item_id in feasible if item_id in kept]
        if not feasible_kept:
            blockers.append(
                f"{removed} 没有同时满足开放时段、交互形式与课程目标的保留替代项目"
            )
            continue
        # 优先选择与被撤项目有直接重复证据的替代
        duplicate_choice = [
            item_id
            for item_id in feasible_kept
            if verdict_index.get((removed, item_id), PairVerdict("", "", "")).verdict == DUPLICATE
        ]
        substitute = sorted(duplicate_choice or feasible_kept)[0]
        if not duplicate_choice:
            warnings.append(
                f"{removed} 的替代项目 {substitute} 缺少直接重复证据，属于约束可行但证据较弱的替换"
            )
        pair = verdict_index[(removed, substitute)]
        replacements.append(
            Replacement(
                removed_item=removed,
                substitute_item=substitute,
                matched_slots=sorted(dataset.slots_of(removed)),
                matched_format=dataset.items[removed].format,
                goals_covered=sorted(dataset.goals_of(removed)),
                pair_verdict=pair.verdict,
                pair_reason_codes=pair.reason_codes,
            )
        )

    coverage, lost = _commitment_coverage(dataset, kept)
    for entry in lost:
        blockers.append(
            f"课程 {entry['course_id']} 在 {entry['slot']} 时段的目标「{entry['goal']}」"
            f"失去唯一/全部承载（原项目 {entry['original_item']}）"
        )

    score = 0.0
    for removed in removals:
        score += 1.0
        if any(
            verdict_index.get((removed, other), PairVerdict("", "", "")).verdict == DUPLICATE
            for other in kept
        ):
            score += 0.5
    score -= 0.25 * len(warnings)

    return RotationPlan(
        plan_id=plan_id,
        removals=sorted(removals),
        replacements=replacements,
        blockers=blockers,
        warnings=warnings,
        committed_coverage_after=coverage,
        lost_commitments=lost,
        score=score,
        feasible=not blockers,
    )


def generate_plans(
    dataset: Dataset,
    quota: int,
    thresholds: Thresholds | None = None,
) -> tuple[list[RotationPlan], list[PairVerdict], list[str]]:
    """生成限额内的全部方案（含全保留基线），按可行性与得分排序。"""
    thresholds = thresholds or Thresholds()
    verdicts = analyze_pairs(dataset, thresholds)
    verdict_index = _verdict_index(verdicts)
    cold = set(cold_items(dataset, thresholds))

    pool = candidate_removals(dataset, thresholds, verdict_index, cold)
    plans: list[RotationPlan] = [
        _build_plan(dataset, [], "plan_0_keep_all", thresholds, verdict_index, cold)
    ]
    plan_no = 1
    for size in range(1, quota + 1):
        for removals in combinations(pool, size):
            plans.append(
                _build_plan(
                    dataset,
                    list(removals),
                    f"plan_{plan_no}",
                    thresholds,
                    verdict_index,
                    cold,
                )
            )
            plan_no += 1

    plans.sort(key=lambda plan: (not plan.feasible, -plan.score, plan.plan_id))
    return plans, verdicts, sorted(cold)


def verdict_distribution(verdicts: list[PairVerdict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    return counts
