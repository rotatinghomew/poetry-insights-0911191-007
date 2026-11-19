"""审批报告：机器可读 JSON、人工可读 Markdown、逐项目档案。

策展人从任一“拟撤 / 拟留”项目出发，应能查到：
- 与其他项目的判定与原始信号（替代证据）；
- 因资料稀薄而未作的判断；
- 对已承诺课程时段的真实影响。
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .config import (
    COLD_LABEL_ZH,
    DUPLICATE,
    INCONCLUSIVE,
    INSUFFICIENT_EXPOSURE,
    VERDICT_LABELS_ZH,
)
from .models import Dataset
from .overlap import PairVerdict, cold_items
from .planning import RotationPlan, eligible_substitutes, generate_plans
from .segments import LINEAGE_LABELS_ZH, StabilityReport
from .config import Thresholds


def _pair_summary(dataset: Dataset, verdicts: list[PairVerdict]) -> list[dict]:
    rows: list[dict] = []
    for verdict in verdicts:
        signals = verdict.signals
        rows.append(
            {
                "item_a": verdict.item_a,
                "item_b": verdict.item_b,
                "item_a_title": dataset.items[verdict.item_a].title,
                "item_b_title": dataset.items[verdict.item_b].title,
                "verdict": verdict.verdict,
                "verdict_zh": VERDICT_LABELS_ZH[verdict.verdict],
                "reason_codes": verdict.reason_codes,
                "cold_items": verdict.cold_items,
                "signals": signals.as_dict() if signals else None,
            }
        )
    return rows


def build_item_dossiers(
    dataset: Dataset,
    verdicts: list[PairVerdict],
    plans: list[RotationPlan],
    cold: list[str],
) -> dict[str, dict]:
    plan_by_removal: dict[str, list[str]] = {}
    for plan in plans:
        for item_id in plan.removals:
            plan_by_removal.setdefault(item_id, []).append(plan.plan_id)

    dossiers: dict[str, dict] = {}
    for item_id in sorted(dataset.items):
        item = dataset.items[item_id]
        relationships = []
        withheld: list[str] = []
        for verdict in verdicts:
            if item_id not in (verdict.item_a, verdict.item_b):
                continue
            other = verdict.item_b if verdict.item_a == item_id else verdict.item_a
            entry = {
                "other_item": other,
                "other_title": dataset.items[other].title,
                "verdict": verdict.verdict,
                "verdict_zh": VERDICT_LABELS_ZH[verdict.verdict],
                "reason_codes": verdict.reason_codes,
                "signals": verdict.signals.as_dict() if verdict.signals else None,
            }
            relationships.append(entry)
            # “未作判断”只收集资料稀薄类结论；排班分离、路线假相似、重复、互补都是已作判断
            if verdict.verdict in {INCONCLUSIVE, INSUFFICIENT_EXPOSURE} or bool(verdict.cold_items):
                withheld.append(
                    f"与 {other}：{VERDICT_LABELS_ZH[verdict.verdict]}（{'; '.join(verdict.reason_codes)}）"
                )

        commitments = [
            {
                "course_id": c.course_id,
                "slot": c.slot,
                "goal": c.goal,
            }
            for c in dataset.commitments_by_item.get(item_id, ())
        ]

        planned_in = plan_by_removal.get(item_id, [])
        duplicate_partners = [
            relation["other_item"]
            for relation in relationships
            if relation["verdict"] == DUPLICATE
        ]
        duplicate_without_substitute = [
            other
            for other in duplicate_partners
            if other not in eligible_substitutes(dataset, item_id)
        ]
        recommended_action = "provisional_keep" if item_id in cold else (
            "removable_only_with_qualified_substitute" if planned_in else "keep"
        )
        dossiers[item_id] = {
            "item_id": item_id,
            "title": item.title,
            "quarter": item.quarter,
            "revision": item.revision,
            "format": item.format,
            "course_goal": item.course_goal,
            "slots": sorted(dataset.slots_of(item_id)),
            "channels": sorted(dataset.channels_of(item_id)),
            "session_count": dataset.session_count(item_id),
            "is_cold": item_id in cold,
            "cold_note": COLD_LABEL_ZH if item_id in cold else None,
            "recommended_action": recommended_action,
            "duplicate_partners": duplicate_partners,
            "duplicate_without_qualified_substitute": duplicate_without_substitute,
            "relationships": relationships,
            "judgements_withheld": withheld,
            "course_commitments": commitments,
            "appears_in_removal_plans": planned_in,
        }
    return dossiers


def build_report(
    dataset: Dataset,
    quota: int,
    thresholds: Thresholds | None = None,
    stability: StabilityReport | None = None,
) -> dict:
    thresholds = thresholds or Thresholds()
    plans, verdicts, cold = generate_plans(dataset, quota, thresholds)
    dossiers = build_item_dossiers(dataset, verdicts, plans, cold)
    report = {
        "quarter": dataset.quarter,
        "rotation_quota": quota,
        "thresholds": asdict(thresholds),
        "data_issues": dataset.issues,
        "cold_items": [
            {"item_id": item_id, "note": COLD_LABEL_ZH, "sessions": dataset.session_count(item_id)}
            for item_id in cold
        ],
        "pair_analysis": _pair_summary(dataset, verdicts),
        "item_dossiers": dossiers,
        "rotation_plans": [plan.as_dict() for plan in plans],
        "stability": stability.as_dict() if stability else None,
    }
    return report


def write_json(report: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- Markdown


def _md_pairs(report: dict, number: int) -> list[str]:
    lines = [f"## {number}、项目对判定（共同曝光 / 共同使用）", ""]
    lines.append(
        "| 项目 A | 项目 B | 判定 | 共同时段 | 共同曝光分钟 | 共同使用群 | Jaccard | 共同使用率 | 依据码 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in report["pair_analysis"]:
        signals = row["signals"] or {}
        lines.append(
            "| {a}（{ta}）| {b}（{tb}）| {v} | {slot} | {minute} | {support} | {jac} | {rate} | {codes} |".format(
                a=row["item_a"],
                ta=row["item_a_title"],
                b=row["item_b"],
                tb=row["item_b_title"],
                v=row["verdict_zh"],
                slot="、".join(signals.get("shared_slots", [])) or "无",
                minute=signals.get("coexposure_minutes", 0),
                support=signals.get("couse_support", 0),
                jac=signals.get("jaccard", 0),
                rate=signals.get("couse_rate", 0),
                codes="、".join(row["reason_codes"]),
            )
        )
    lines.append("")
    return lines


def _md_cold(report: dict, number: int) -> list[str]:
    lines = [f"## {number}、冷启动 / 长期遇冷项目（暂定位置，未作同质或互补定性）", ""]
    if not report["cold_items"]:
        lines.append("（本季无）")
    for entry in report["cold_items"]:
        lines.append(
            f"- `{entry['item_id']}`：会话数 {entry['sessions']}。{entry['note']}；"
            "没有共同使用记录既不能证明互补，也不能证明相同。"
        )
    lines.append("")
    return lines


def _md_plans(report: dict, number: int) -> list[str]:
    lines = [f"## {number}、限额内轮换方案比较", ""]
    for plan in report["rotation_plans"]:
        status = "可行" if plan["feasible"] else "不可行"
        lines.append(f"### {plan['plan_id']}（{status}，得分 {plan['score']}）")
        if plan["removals"]:
            lines.append(f"- 拟撤项目：{', '.join(f'`{x}`' for x in plan['removals'])}")
        else:
            lines.append("- 拟撤项目：无（全保留基线）")
        for replacement in plan["replacements"]:
            lines.append(
                "- 替代：`{r}` → `{s}`；同时段 {slot}、形式 {fmt}、目标覆盖 {goals}；"
                "项目对判定：{v}（{codes}）".format(
                    r=replacement["removed_item"],
                    s=replacement["substitute_item"],
                    slot="、".join(replacement["matched_slots"]),
                    fmt=replacement["matched_format"],
                    goals="、".join(replacement["goals_covered"]),
                    v=VERDICT_LABELS_ZH[replacement["pair_verdict"]],
                    codes="、".join(replacement["pair_reason_codes"]),
                )
            )
        if plan["warnings"]:
            lines.append("- 警告：")
            lines.extend(f"  - {warning}" for warning in plan["warnings"])
        if plan["blockers"]:
            lines.append("- 阻断原因：")
            lines.extend(f"  - {blocker}" for blocker in plan["blockers"])
        if plan["lost_commitments"]:
            lines.append("- 课程承诺受损：")
            for lost in plan["lost_commitments"]:
                lines.append(
                    f"  - 课程 `{lost['course_id']}` {lost['slot']} 时段目标「{lost['goal']}」"
                    f"失去承载（原项目 `{lost['original_item']}`）"
                )
        lines.append("")
    return lines


def _md_stability(report: dict, number: int) -> list[str]:
    stability = report.get("stability")
    if not stability:
        return []
    lines = [
        f"## {number}、分群稳定性（{stability['quarter_old']} → {stability['quarter_new']}）",
        "",
        "分数说明：原始分 / 切换时段控制后 / 团体渠道剔除（仅散客）。",
        "",
        "| 旧分群 | 新分群 | 关系 | 原始分 | 去时段分 | 散客分 | 关联课程 | 人工核对 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lineage in stability["lineages"]:
        lines.append(
            "| {old} | {new} | {relation} | {raw} | {slot} | {walk} | {courses} | {review} |".format(
                old=lineage["old_group"] or "—",
                new="、".join(lineage["new_groups"]) or "—",
                relation=LINEAGE_LABELS_ZH[lineage["relation"]],
                raw=lineage["score_raw"] if lineage["score_raw"] is not None else "—",
                slot=lineage["score_slot_controlled"]
                if lineage["score_slot_controlled"] is not None
                else "—",
                walk=lineage["score_walkin"] if lineage["score_walkin"] is not None else "不可算",
                courses="、".join(lineage["course_ids"]) or "—",
                review=("是：" + lineage["review_reason"]) if lineage["needs_manual_review"] else "否",
            )
        )
    lines.append("")
    if stability["manual_review"]:
        lines.append("### 需要人工核对的课线")
        for entry in stability["manual_review"]:
            lines.append(
                "- {kind}：旧群 {old} → 新群 {new}；课程 {courses}；{reason}".format(
                    kind=LINEAGE_LABELS_ZH.get(entry["kind"], entry["kind"]),
                    old=entry["old_group"] or "—",
                    new="、".join(entry["new_groups"]) or "—",
                    courses="、".join(entry["course_ids"]) or "—",
                    reason=entry["reason"],
                )
            )
        lines.append("")
    if stability["notes"]:
        lines.append("### 资料限制说明")
        lines.extend(f"- {note}" for note in stability["notes"])
        lines.append("")
    return lines


def _md_dossiers(report: dict, number: int) -> list[str]:
    lines = [f"## {number}、逐项目档案（替代证据 / 未作判断 / 课程影响）", ""]
    for item_id, dossier in report["item_dossiers"].items():
        lines.append(
            f"### `{item_id}` {dossier['title']}（修订 {dossier['revision']}，{dossier['format']}，"
            f"目标「{dossier['course_goal']}」）"
        )
        lines.append(
            f"- 开放时段：{'、'.join(dossier['slots']) or '无可用记录'}；"
            f"渠道：{'、'.join(dossier['channels']) or '无'}；会话数：{dossier['session_count']}"
        )
        if dossier["is_cold"]:
            lines.append(f"- {dossier['cold_note']}")
        lines.append(f"- 建议动作：`{dossier['recommended_action']}`")
        if dossier["duplicate_without_qualified_substitute"]:
            lines.append(
                "- 虽有重复证据（{partners}），但无项目同时满足开放时段、交互形式与课程目标约束，"
                "撤换会丢失课程目标覆盖，故不进入可撤池".format(
                    partners="、".join(f"`{x}`" for x in dossier["duplicate_without_qualified_substitute"])
                )
            )
        if dossier["course_commitments"]:
            lines.append("- 已承诺课程：")
            for commitment in dossier["course_commitments"]:
                lines.append(
                    f"  - `{commitment['course_id']}` {commitment['slot']} 时段，目标「{commitment['goal']}」"
                )
        else:
            lines.append("- 已承诺课程：无")
        lines.append("- 项目关系与替代证据：")
        for relation in dossier["relationships"]:
            signals = relation["signals"] or {}
            lines.append(
                "  - 与 `{other}`（{title}）：{verdict}；共同使用率 {rate}、Jaccard {jac}、"
                "共同曝光 {minute} 分钟；依据 {codes}".format(
                    other=relation["other_item"],
                    title=relation["other_title"],
                    verdict=relation["verdict_zh"],
                    rate=signals.get("couse_rate", 0),
                    jac=signals.get("jaccard", 0),
                    minute=signals.get("coexposure_minutes", 0),
                    codes="、".join(relation["reason_codes"]),
                )
            )
        withheld = list(dossier["judgements_withheld"])
        if dossier["is_cold"] and not withheld:
            withheld.append("本项目会话样本不足，全部项目对均未作同质或互补定性（暂定保留）")
        if withheld:
            lines.append("- 因资料稀薄而未作的判断：")
            lines.extend(f"  - {text}" for text in withheld)
        lines.append("")
    return lines


def write_markdown(report: dict, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {report['quarter']} 季度诗词互动内容轮换分析报告",
        "",
        f"轮换限额：{report['rotation_quota']} 个点位。",
        "",
    ]
    if report["data_issues"]:
        lines.append("> 数据资料问题（载入阶段发现）：")
        lines.extend(f"> - {issue}" for issue in report["data_issues"])
        lines.append("")
    section = 1
    lines += _md_pairs(report, section)
    section += 1
    lines += _md_cold(report, section)
    section += 1
    lines += _md_plans(report, section)
    section += 1
    stability_lines = _md_stability(report, section)
    if stability_lines:
        lines += stability_lines
        section += 1
    lines += _md_dossiers(report, section)
    path.write_text("\n".join(lines), encoding="utf-8")
