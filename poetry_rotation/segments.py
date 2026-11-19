"""分群构建、稳定性评估与跨季度沿革。

关键约束：
- 分群匹配只看观测到的行为构成（课程目标、交互形式、渠道、时段、参与深度），
  不按项目主题名或 item_id 做同名映射；主题名沿用不代表受众相同。
- 切换时段的影响通过“去时段权重”的控制分数读取；
  团体渠道的影响通过仅保留散客会话重建画像来读取。
- 一组拆成两组、两组合并或互相交叠时，输出结构化沿革并列入人工核对，
  绝不用同名或强行一一对应去算留存。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import Thresholds
from .models import Dataset

MATCH = "match"
MATCH_LOW = "match_low"
SPLIT = "split"
MERGE = "merge"
OVERLAP = "overlap"
NO_SUCCESSOR = "no_successor"
NEW_GROUP = "new_group"

LINEAGE_LABELS_ZH = {
    MATCH: "一一对应（高稳定性）",
    MATCH_LOW: "弱对应（低于稳定线，需核对）",
    SPLIT: "一组拆成多组",
    MERGE: "多组合并为一组",
    OVERLAP: "两组互相交叠（归属不确定）",
    NO_SUCCESSOR: "当季无后继分群",
    NEW_GROUP: "当季新出现分群",
}


@dataclass
class SegmentProfile:
    group_id: str
    quarter: str
    items: set[str]
    content_keys: set[tuple[str, str]]
    session_count: int
    channel_dist: dict[str, float]
    slot_dist: dict[str, float]
    goal_dist: dict[str, float]
    format_dist: dict[str, float]
    avg_interactions: float
    course_ids: set[str]

    def as_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "quarter": self.quarter,
            "items": sorted(self.items),
            "content_keys": [list(key) for key in sorted(self.content_keys)],
            "session_count": self.session_count,
            "channel_dist": {k: round(v, 4) for k, v in sorted(self.channel_dist.items())},
            "slot_dist": {k: round(v, 4) for k, v in sorted(self.slot_dist.items())},
            "goal_dist": {k: round(v, 4) for k, v in sorted(self.goal_dist.items())},
            "format_dist": {k: round(v, 4) for k, v in sorted(self.format_dist.items())},
            "avg_interactions": round(self.avg_interactions, 3),
            "course_ids": sorted(self.course_ids),
        }


@dataclass
class Lineage:
    old_group: str | None
    new_groups: list[str]
    relation: str
    score_raw: float | None
    score_slot_controlled: float | None
    score_walkin: float | None
    course_ids: list[str]
    needs_manual_review: bool
    review_reason: str = ""

    def as_dict(self) -> dict:
        def rounded(value: float | None) -> float | None:
            return None if value is None else round(value, 4)

        return {
            "old_group": self.old_group,
            "new_groups": self.new_groups,
            "relation": self.relation,
            "score_raw": rounded(self.score_raw),
            "score_slot_controlled": rounded(self.score_slot_controlled),
            "score_walkin": rounded(self.score_walkin),
            "course_ids": self.course_ids,
            "needs_manual_review": self.needs_manual_review,
            "review_reason": self.review_reason,
        }


@dataclass
class ManualReviewEntry:
    kind: str
    old_group: str | None
    new_groups: list[str]
    course_ids: list[str]
    reason: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "old_group": self.old_group,
            "new_groups": self.new_groups,
            "course_ids": self.course_ids,
            "reason": self.reason,
        }


# ---------------------------------------------------------------- 画像构建


def _content_key(dataset: Dataset, item_id: str) -> tuple[str, str]:
    item = dataset.items[item_id]
    return item.title, item.format


def _dist(counter: dict[str, int]) -> dict[str, float]:
    total = sum(counter.values())
    if not total:
        return {}
    return {key: value / total for key, value in counter.items()}


def build_profiles(dataset: Dataset, walk_in_only: bool = False) -> list[SegmentProfile]:
    groups: dict[str, dict] = {}
    for session in dataset.sessions:
        if walk_in_only and session.channel != "散客":
            continue
        bucket = groups.setdefault(
            session.anonymous_group,
            {
                "items": set(),
                "sessions": 0,
                "channels": {},
                "slots": {},
                "goals": {},
                "formats": {},
                "interactions": [],
            },
        )
        bucket["items"].add(session.item_id)
        bucket["sessions"] += 1
        bucket["channels"][session.channel] = bucket["channels"].get(session.channel, 0) + 1
        bucket["interactions"].append(session.interactions)
        item = dataset.items[session.item_id]
        if item.course_goal:
            bucket["goals"][item.course_goal] = bucket["goals"].get(item.course_goal, 0) + 1
        if item.format:
            bucket["formats"][item.format] = bucket["formats"].get(item.format, 0) + 1
        for exposure in dataset.exposure_by_item.get(session.item_id, ()):
            if exposure.available:
                bucket["slots"][exposure.slot] = bucket["slots"].get(exposure.slot, 0) + 1

    profiles: list[SegmentProfile] = []
    for group_id in sorted(groups):
        bucket = groups[group_id]
        course_ids = {
            commitment.course_id
            for item_id in bucket["items"]
            for commitment in dataset.commitments_by_item.get(item_id, ())
        }
        profiles.append(
            SegmentProfile(
                group_id=group_id,
                quarter=dataset.quarter,
                items=set(bucket["items"]),
                content_keys={_content_key(dataset, item_id) for item_id in bucket["items"]},
                session_count=bucket["sessions"],
                channel_dist=_dist(bucket["channels"]),
                slot_dist=_dist(bucket["slots"]),
                goal_dist=_dist(bucket["goals"]),
                format_dist=_dist(bucket["formats"]),
                avg_interactions=(
                    sum(bucket["interactions"]) / len(bucket["interactions"])
                    if bucket["interactions"]
                    else 0.0
                ),
                course_ids=course_ids,
            )
        )
    return profiles


# ---------------------------------------------------------------- 相似度


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    keys = set(left) | set(right)
    dot = sum(left.get(key, 0.0) * right.get(key, 0.0) for key in keys)
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def _renormalize(weights: dict[str, float], drop: set[str]) -> dict[str, float]:
    kept = {key: value for key, value in weights.items() if key not in drop}
    total = sum(kept.values())
    return {key: value / total for key, value in kept.items()} if total else {}


def similarity(
    old: SegmentProfile,
    new: SegmentProfile,
    thresholds: Thresholds,
    drop: set[str] | None = None,
) -> float:
    drop = drop or set()
    weights = _renormalize(
        {
            "goal": thresholds.weight_goal,
            "format": thresholds.weight_format,
            "channel": thresholds.weight_channel,
            "slot": thresholds.weight_slot,
            "cohesion": thresholds.weight_cohesion,
        },
        drop,
    )
    if not weights:
        return 0.0

    parts = {
        "goal": _cosine(old.goal_dist, new.goal_dist),
        "format": _cosine(old.format_dist, new.format_dist),
        "channel": _cosine(old.channel_dist, new.channel_dist),
        "slot": _cosine(old.slot_dist, new.slot_dist),
        # 参与深度按各自均值归一到 0..1 的接近度
        "cohesion": 1.0
        - min(1.0, abs(old.avg_interactions - new.avg_interactions) / max(old.avg_interactions, new.avg_interactions, 1.0)),
    }
    return sum(weight * parts[key] for key, weight in weights.items())


# ---------------------------------------------------------------- 沿革匹配


@dataclass
class StabilityReport:
    quarter_old: str
    quarter_new: str
    profiles_old: list[SegmentProfile]
    profiles_new: list[SegmentProfile]
    lineages: list[Lineage]
    manual_review: list[ManualReviewEntry]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "quarter_old": self.quarter_old,
            "quarter_new": self.quarter_new,
            "profiles_old": [profile.as_dict() for profile in self.profiles_old],
            "profiles_new": [profile.as_dict() for profile in self.profiles_new],
            "lineages": [lineage.as_dict() for lineage in self.lineages],
            "manual_review": [entry.as_dict() for entry in self.manual_review],
            "notes": self.notes,
        }


def _candidate_matrix(
    old_profiles: list[SegmentProfile],
    new_profiles: list[SegmentProfile],
    thresholds: Thresholds,
    walkin_old: dict[str, SegmentProfile],
    walkin_new: dict[str, SegmentProfile],
) -> dict[str, dict[str, dict[str, float | None]]]:
    matrix: dict[str, dict[str, dict[str, float | None]]] = {}
    for old in old_profiles:
        matrix[old.group_id] = {}
        for new in new_profiles:
            raw = similarity(old, new, thresholds)
            slot_controlled = similarity(old, new, thresholds, drop={"slot"})
            walkin_score: float | None = None
            walk_old = walkin_old.get(old.group_id)
            walk_new = walkin_new.get(new.group_id)
            if walk_old is not None and walk_new is not None:
                walkin_score = similarity(
                    walk_old, walk_new, thresholds, drop={"slot", "channel"}
                )
            matrix[old.group_id][new.group_id] = {
                "raw": raw,
                "slot_controlled": slot_controlled,
                "walkin": walkin_score,
            }
    return matrix


def stability_report(
    old_dataset: Dataset,
    new_dataset: Dataset,
    thresholds: Thresholds | None = None,
) -> StabilityReport:
    thresholds = thresholds or Thresholds()
    old_profiles = build_profiles(old_dataset)
    new_profiles = build_profiles(new_dataset)
    walkin_old = {p.group_id: p for p in build_profiles(old_dataset, walk_in_only=True)}
    walkin_new = {p.group_id: p for p in build_profiles(new_dataset, walk_in_only=True)}

    matrix = _candidate_matrix(old_profiles, new_profiles, thresholds, walkin_old, walkin_new)
    lineages: list[Lineage] = []
    review: list[ManualReviewEntry] = []
    claimed_new: set[str] = set()

    def scores_of(old_id: str) -> list[tuple[str, float, float, float | None]]:
        rows = []
        for new_id, scores in matrix[old_id].items():
            rows.append(
                (
                    new_id,
                    float(scores["raw"]),
                    float(scores["slot_controlled"]),
                    scores["walkin"],
                )
            )
        return sorted(rows, key=lambda row: row[1], reverse=True)

    def course_links(profile: SegmentProfile) -> list[str]:
        return sorted(profile.course_ids)

    old_by_id = {profile.group_id: profile for profile in old_profiles}

    for old in old_profiles:
        rows = scores_of(old.group_id)
        if not rows:
            lineage = Lineage(
                old.group_id, [], NO_SUCCESSOR, None, None, None,
                course_links(old), True,
                "新季度没有任何分群资料，无法确认沿用本群的课程是否仍被覆盖",
            )
            lineages.append(lineage)
            review.append(ManualReviewEntry(
                NO_SUCCESSOR, old.group_id, [], course_links(old), lineage.review_reason
            ))
            continue

        top_id, top_raw, top_slot, top_walkin = rows[0]
        second_raw = rows[1][1] if len(rows) > 1 else -1.0
        strong_candidates = [row for row in rows if row[1] >= thresholds.lineage_low_score]
        ambiguous = (top_raw - second_raw) <= thresholds.lineage_ambiguous_gap if len(rows) > 1 else False
        linked_courses = course_links(old)

        # 拆分：一个旧群对多个新群都有不低的相似度，且分差落在不确定带内
        if len(strong_candidates) >= 2 and ambiguous:
            new_ids = [row[0] for row in strong_candidates]
            reason = "旧分群对多个新分群相似度接近，疑似拆分或交叠，不能按同名一一对应"
            lineage = Lineage(
                old.group_id, new_ids, SPLIT,
                top_raw, top_slot, top_walkin, linked_courses, True, reason,
            )
            lineages.append(lineage)
            review.append(ManualReviewEntry(SPLIT, old.group_id, new_ids, linked_courses, reason))
            claimed_new.update(new_ids)
            continue

        if top_raw < thresholds.lineage_low_score:
            reason = "所有候选相似度均低于弱对应线，本群在新季度没有可确认的后继"
            lineage = Lineage(
                old.group_id, [top_id], NO_SUCCESSOR,
                top_raw, top_slot, top_walkin, linked_courses, True, reason,
            )
            lineages.append(lineage)
            review.append(ManualReviewEntry(
                NO_SUCCESSOR, old.group_id, [top_id], linked_courses, reason
            ))
            continue

        if top_raw < thresholds.lineage_match_score or ambiguous or top_id in claimed_new:
            relation = OVERLAP if (ambiguous or top_id in claimed_new) else MATCH_LOW
            reason = (
                "多个旧分群指向同一新分群或稳定性不足，归属不确定，需要人工核对课线"
                if relation == OVERLAP
                else "相似度高于弱对应线但低于稳定线，不能直接沿用旧分群结论"
            )
            lineage = Lineage(
                old.group_id, [top_id], relation,
                top_raw, top_slot, top_walkin, linked_courses, True, reason,
            )
            lineages.append(lineage)
            review.append(ManualReviewEntry(
                relation, old.group_id, [top_id], linked_courses, reason
            ))
            claimed_new.add(top_id)
            continue

        lineage = Lineage(
            old.group_id, [top_id], MATCH,
            top_raw, top_slot, top_walkin, linked_courses,
            # 高匹配不需要人工核对；课程可追溯性由沿革中的 course_ids 保证
            False,
            "该分群承担过课程承诺，沿革记录需可追溯" if linked_courses else "",
        )
        lineages.append(lineage)
        claimed_new.add(top_id)

    # 合并检查：同一新群被多个旧群主张
    claimants: dict[str, list[str]] = {}
    for lineage in lineages:
        for new_id in lineage.new_groups:
            claimants.setdefault(new_id, []).append(lineage.old_group or "?")
    for new_id, old_ids in claimants.items():
        if len(set(old_ids)) >= 2:
            profile_new = next(p for p in new_profiles if p.group_id == new_id)
            reason = "多个旧分群同时指向同一新分群，疑似合并，需人工确认课程目标是否都保留"
            review.append(ManualReviewEntry(
                MERGE, None, [new_id], sorted(profile_new.course_ids), reason
            ))

    # 新出现的分群：无后继结论不应占用新群的“已匹配”名额
    matched_new = {
        new_id
        for lineage in lineages
        if lineage.relation != NO_SUCCESSOR
        for new_id in lineage.new_groups
    }
    for new in new_profiles:
        if new.group_id not in matched_new:
            lineages.append(
                Lineage(
                    None, [new.group_id], NEW_GROUP, None, None, None,
                    sorted(new.course_ids), bool(new.course_ids),
                    "新分群无历史沿革；若承担课程承诺需建立基线" if new.course_ids else "",
                )
            )

    notes: list[str] = []
    for old in old_profiles:
        if old.group_id not in walkin_old:
            notes.append(f"{old.quarter} 分群 {old.group_id} 没有散客会话，团体渠道控制后的稳定性无法计算")
    for new in new_profiles:
        if new.group_id not in walkin_new:
            notes.append(f"{new.quarter} 分群 {new.group_id} 没有散客会话，团体渠道控制后的稳定性无法计算")

    return StabilityReport(
        old_dataset.quarter,
        new_dataset.quarter,
        old_profiles,
        new_profiles,
        lineages,
        review,
        notes,
    )
