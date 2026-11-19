"""核心分析逻辑测试：判定路径、冷启动、约束替换、分群沿革。

运行：
    python -I -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from poetry_rotation.config import (
    COMPLEMENTARY,
    DUPLICATE,
    INSUFFICIENT_EXPOSURE,
    ROUTE_ONLY,
    SCHEDULE_SEPARATED,
    Thresholds,
)
from poetry_rotation.loading import load_dataset
from poetry_rotation.models import Dataset
from poetry_rotation.overlap import analyze_pairs, classify_pair, cold_items, pair_signals
from poetry_rotation.planning import (
    eligible_substitutes,
    generate_plans,
)
from poetry_rotation.segments import (
    MERGE,
    MATCH,
    NEW_GROUP,
    NO_SUCCESSOR,
    SPLIT,
    build_profiles,
    stability_report,
)

try:  # 作为 tests 包成员导入
    from .synth_fixtures import (
        build_dataset,
        make_commitment,
        make_exposure,
        make_item,
        make_session,
        overlap_scenario,
    )
except ImportError:  # unittest discover -s tests 时按顶层模块导入
    from synth_fixtures import (  # type: ignore[no-redef]
        build_dataset,
        make_commitment,
        make_exposure,
        make_item,
        make_session,
        overlap_scenario,
    )

TH = Thresholds()


def verdict_map(dataset: Dataset) -> dict[tuple[str, str], str]:
    return {(v.item_a, v.item_b): v.verdict for v in analyze_pairs(dataset, TH)}


class PairVerdictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dataset = overlap_scenario()
        self.verdicts = verdict_map(self.dataset)

    def test_duplicate_under_sufficient_coexposure(self) -> None:
        self.assertEqual(self.verdicts[("A", "B")], DUPLICATE)
        signals = pair_signals(self.dataset, "A", "B")
        self.assertGreaterEqual(signals.coexposure_minutes, TH.sufficient_coexposure_minutes)
        self.assertGreaterEqual(signals.couse_support, TH.min_couse_support)
        self.assertGreaterEqual(signals.couse_rate, TH.duplicate_couse_rate)

    def test_complementary_when_coexposure_sufficient_but_little_couse(self) -> None:
        self.assertEqual(self.verdicts[("A", "C")], COMPLEMENTARY)
        signals = pair_signals(self.dataset, "A", "C")
        self.assertLessEqual(signals.couse_rate, TH.complementary_couse_rate)

    def test_route_only_group_channel_is_false_similarity(self) -> None:
        self.assertEqual(self.verdicts[("A", "D")], ROUTE_ONLY)
        signals = pair_signals(self.dataset, "A", "D")
        self.assertTrue(signals.group_only_shared)
        self.assertEqual(signals.walkin_shared_groups, frozenset())

    def test_schedule_separated_never_judged_duplicate(self) -> None:
        self.assertEqual(self.verdicts[("A", "E")], SCHEDULE_SEPARATED)
        signals = pair_signals(self.dataset, "A", "E")
        self.assertEqual(signals.shared_slots, frozenset())
        self.assertEqual(signals.coexposure_minutes, 0)

    def test_cold_item_gets_provisional_verdict_only(self) -> None:
        cold = set(cold_items(self.dataset, TH))
        self.assertIn("F", cold)
        verdict = classify_pair(self.dataset, "A", "F", TH, cold)
        # 冷启动项目即使共同使用率为 0，也不允许被保证为互补或相同
        self.assertIn(verdict.verdict, (INSUFFICIENT_EXPOSURE,))
        self.assertIn("cold_item_provisional", verdict.reason_codes)


class PlanningTests(unittest.TestCase):
    def test_substitute_must_match_slot_format_and_goals(self) -> None:
        items = [
            make_item("A", "甲", fmt="触屏", goal="山水"),
            make_item("B", "乙", fmt="触屏", goal="山水"),  # 同形式同时段同目标
            make_item("C", "丙", fmt="音频", goal="山水"),  # 形式不符
            make_item("D", "丁", fmt="触屏", goal="地理"),  # 目标不符
        ]
        exposures = [
            make_exposure("A", "早", 60),
            make_exposure("B", "早", 60),
            make_exposure("C", "早", 60),
            make_exposure("D", "早", 60),
        ]
        sessions = [
            make_session("S1", "A", "G1"),
            make_session("S2", "B", "G1"),
            make_session("S3", "A", "G2"),
            make_session("S4", "B", "G2"),
        ]
        dataset = build_dataset(items, exposures, sessions)
        self.assertEqual(eligible_substitutes(dataset, "A"), ["B"])

    def test_slot_coverage_is_required_not_just_overlap(self) -> None:
        items = [
            make_item("A", "甲"),
            make_item("B", "乙"),
        ]
        exposures = [
            make_exposure("A", "早", 60),
            make_exposure("A", "晚", 60),
            make_exposure("B", "早", 60),
        ]
        dataset = build_dataset(items, exposures, [])
        # B 只覆盖早时段，不能替代同时占用早/晚的 A
        self.assertEqual(eligible_substitutes(dataset, "A"), [])

    def test_duplicate_pair_with_qualified_substitute_produces_feasible_plan(self) -> None:
        items = [
            make_item("A", "甲", goal="山水"),
            make_item("B", "乙", goal="山水"),
        ]
        exposures = [make_exposure("A", "早", 60), make_exposure("B", "早", 60)]
        sessions = [
            make_session("S1", "A", "G1"),
            make_session("S2", "B", "G1"),
            make_session("S3", "A", "G2"),
            make_session("S4", "B", "G2"),
            make_session("S5", "A", "G3"),
            make_session("S6", "B", "G3"),
        ]
        dataset = build_dataset(items, exposures, sessions)
        plans, verdicts, cold = generate_plans(dataset, quota=1, thresholds=TH)
        self.assertEqual(cold, [])
        feasible = [plan for plan in plans if plan.feasible]
        self.assertTrue(any(plan.removals == ["A"] for plan in feasible))
        plan = next(plan for plan in feasible if plan.removals == ["A"])
        self.assertEqual(plan.replacements[0].substitute_item, "B")

    def test_unique_goal_coverage_blocks_removal_even_for_duplicate(self) -> None:
        # A/B 使用高度重合，但承担不同课程目标，且无其它项目覆盖 B 的目标
        items = [
            make_item("A", "甲", goal="山水"),
            make_item("B", "乙", goal="书写"),
        ]
        exposures = [make_exposure("A", "早", 60), make_exposure("B", "早", 60)]
        sessions = [
            make_session("S1", "A", "G1"),
            make_session("S2", "B", "G1"),
            make_session("S3", "A", "G2"),
            make_session("S4", "B", "G2"),
        ]
        commitments = [
            make_commitment("L1", "A", "早", "山水"),
            make_commitment("L1", "B", "早", "书写"),
        ]
        dataset = build_dataset(items, exposures, sessions, commitments)
        plans, _, _ = generate_plans(dataset, quota=1, thresholds=TH)
        # 没有合格替代（目标不同），撤项池为空，只剩全保留基线
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].removals, [])
        self.assertTrue(plans[0].feasible)

    def test_cold_item_never_enter_removal_pool(self) -> None:
        dataset = overlap_scenario()
        plans, _, cold = generate_plans(dataset, quota=3, thresholds=TH)
        self.assertIn("F", cold)
        for plan in plans:
            self.assertNotIn("F", plan.removals)

    def test_commitment_loss_is_reported_for_forced_removal(self) -> None:
        items = [
            make_item("A", "甲", goal="山水"),
            make_item("B", "乙", goal="山水"),
        ]
        exposures = [make_exposure("A", "早", 60), make_exposure("B", "早", 60)]
        sessions = [
            make_session("S1", "A", "G1"),
            make_session("S2", "B", "G1"),
            make_session("S3", "A", "G2"),
            make_session("S4", "B", "G2"),
            make_session("S5", "A", "G3"),
            make_session("S6", "B", "G3"),
        ]
        commitments = [make_commitment("L9", "A", "早", "山水")]
        dataset = build_dataset(items, exposures, sessions, commitments)
        plans, _, _ = generate_plans(dataset, quota=1, thresholds=TH)
        plan = next(plan for plan in plans if plan.removals == ["A"])
        # B 覆盖同同时段同目标，因此承诺仍在，且替换记录可追溯
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.lost_commitments, [])
        carriers = plan.committed_coverage_after["L9"]["早"]["山水"]
        self.assertEqual(carriers, ["B"])


class SegmentStabilityTests(unittest.TestCase):
    def _quarter(self, quarter: str, specs: list[tuple[str, list[str], str]]) -> Dataset:
        """specs: (群体, 该群体使用的项目列表, 这些项目的课程目标)。"""
        item_goals: dict[str, str] = {}
        sessions: list = []
        seq = 0
        for group, item_ids, goal in specs:
            for item_id in item_ids:
                item_goals[item_id] = goal
                seq += 1
                sessions.append(
                    make_session(f"{quarter}-S{seq}", item_id, group, quarter=quarter)
                )
        items = [
            make_item(item_id, f"题{item_id}", goal=goal, quarter=quarter)
            for item_id, goal in sorted(item_goals.items())
        ]
        exposures = [make_exposure(item.item_id, "早", 60, quarter=quarter) for item in items]
        return build_dataset(items, exposures, sessions, quarter=quarter)

    def test_one_to_one_lineage_matches_on_behavior_not_name(self) -> None:
        old = self._quarter("Q1", [("G1", ["A", "B"], "山水")])
        new = self._quarter("Q2", [("G1", ["C", "D"], "山水")])
        report = stability_report(old, new, TH)
        lineage = next(item for item in report.lineages if item.old_group == "G1")
        self.assertEqual(lineage.relation, MATCH)
        self.assertEqual(lineage.new_groups, ["G1"])

    def test_split_is_flagged_for_manual_review(self) -> None:
        old = self._quarter(
            "Q1",
            [
                ("G1", ["A", "B"], "山水"),
                ("G2", ["C"], "地理"),
            ],
        )
        new = self._quarter(
            "Q2",
            [
                ("G1", ["D"], "山水"),
                ("G2", ["E"], "山水"),
                ("G3", ["F"], "地理"),
            ],
        )
        report = stability_report(old, new, TH)
        relations = {lineage.old_group: lineage.relation for lineage in report.lineages}
        # 拆分或交叠都必须进入人工核对，不能静默一一对应
        self.assertIn(relations["G1"], (SPLIT,))
        self.assertTrue(any(entry.kind == SPLIT for entry in report.manual_review))

    def test_merge_creates_manual_review_entry(self) -> None:
        old = self._quarter(
            "Q1",
            [
                ("G1", ["A"], "山水"),
                ("G2", ["B"], "山水"),
            ],
        )
        new = self._quarter("Q2", [("G9", ["C", "D"], "山水")])
        report = stability_report(old, new, TH)
        self.assertTrue(any(entry.kind == MERGE for entry in report.manual_review))

    def test_no_successor_and_new_group(self) -> None:
        old = self._quarter("Q1", [("G1", ["A"], "山水")])
        # 新季度分群改用完全不同的渠道构成，使相似度低于弱对应线
        new_items = [make_item("B", "乙", goal="地理", quarter="Q2")]
        new_exposures = [make_exposure("B", "晚", 60, quarter="Q2")]
        new_sessions = [
            make_session("T1", "B", "G7", channel="团体", quarter="Q2"),
            make_session("T2", "B", "G7", channel="团体", quarter="Q2"),
        ]
        new = build_dataset(new_items, new_exposures, new_sessions, quarter="Q2")
        report = stability_report(old, new, TH)
        relations = {
            (lineage.old_group, tuple(lineage.new_groups)): lineage.relation
            for lineage in report.lineages
        }
        self.assertIn(NO_SUCCESSOR, relations.values())
        self.assertIn(NEW_GROUP, relations.values())

    def test_walkin_control_detects_channel_driven_similarity(self) -> None:
        old_items = [make_item("A", "甲", goal="山水")]
        old_exposures = [make_exposure("A", "早", 60)]
        old_sessions = [
            make_session("S1", "A", "G1", channel="团体"),
            make_session("S2", "A", "G1", channel="团体"),
        ]
        old = build_dataset(old_items, old_exposures, old_sessions, quarter="Q1")
        new_items = [make_item("B", "乙", goal="山水", quarter="Q2")]
        new_exposures = [make_exposure("B", "早", 60, quarter="Q2")]
        new_sessions = [
            make_session("T1", "B", "G1", channel="团体", quarter="Q2"),
        ]
        new = build_dataset(new_items, new_exposures, new_sessions, quarter="Q2")
        report = stability_report(old, new, TH)
        lineage = next(item for item in report.lineages if item.old_group == "G1")
        # 没有散客会话时，团体控制分数必须显式标记为不可算，而不是编造稳定性
        self.assertIsNone(lineage.score_walkin)
        self.assertTrue(any("散客" in note for note in report.notes))

    def test_profiles_include_course_links_for_traceability(self) -> None:
        items = [make_item("A", "甲", goal="山水")]
        exposures = [make_exposure("A", "早", 60)]
        sessions = [make_session("S1", "A", "G1")]
        commitments = [make_commitment("L1", "A", "早", "山水")]
        dataset = build_dataset(items, exposures, sessions, commitments)
        profile = build_profiles(dataset)[0]
        self.assertEqual(profile.course_ids, {"L1"})


class LoadingTests(unittest.TestCase):
    def test_fixture_csv_loads_and_cross_checks(self) -> None:
        from pathlib import Path

        data_dir = Path(__file__).resolve().parents[1] / "data"
        dataset = load_dataset(data_dir, "Q2")
        self.assertEqual(set(dataset.items), {"P04", "P05", "P06"})
        self.assertTrue(dataset.sessions)
        self.assertEqual(dataset.issues, [])

    def test_dangling_reference_is_recorded_not_raised(self) -> None:
        import csv
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with (tmp_path / "catalog.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["item_id", "quarter", "title", "format", "course_goal", "revision"])
                writer.writerow(["A", "Q1", "甲", "触屏", "山水", "1"])
            with (tmp_path / "exposure.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["item_id", "quarter", "slot", "minutes", "available"])
                writer.writerow(["GHOST", "Q1", "早", "60", "true"])
                writer.writerow(["A", "Q1", "早", "bad", "maybe"])
            for name, header in (
                ("sessions.csv", ["session_id", "item_id", "quarter", "anonymous_group", "channel", "interactions"]),
                ("course_commitments.csv", ["course_id", "quarter", "item_id", "slot", "goal"]),
            ):
                with (tmp_path / name).open("w", encoding="utf-8", newline="") as f:
                    csv.writer(f).writerow(header)
            dataset = load_dataset(tmp_path, "Q1")
            self.assertTrue(any("GHOST" in issue for issue in dataset.issues))
            self.assertTrue(any("布尔" in issue for issue in dataset.issues))


class CliSmokeTests(unittest.TestCase):
    def test_cli_writes_both_reports_for_real_fixture(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from poetry_rotation.__main__ import main

        data_dir = Path(__file__).resolve().parents[1] / "data"
        with tempfile.TemporaryDirectory() as tmp:
            code = main(
                [
                    "--data-dir",
                    str(data_dir),
                    "--quarter",
                    "Q2",
                    "--quota",
                    "1",
                    "--compare-quarter",
                    "Q1",
                    "--output-dir",
                    tmp,
                ]
            )
            self.assertEqual(code, 0)
            payload = json.loads(
                (Path(tmp) / "rotation_report_Q2.json").read_text(encoding="utf-8")
            )
            self.assertTrue((Path(tmp) / "rotation_report_Q2.md").exists())
            self.assertEqual(payload["quarter"], "Q2")
            self.assertTrue(payload["pair_analysis"])
            self.assertTrue(payload["stability"]["manual_review"])
            # 限额 1 时方案数 = 全保留基线（本季无合格撤项）
            self.assertEqual(len(payload["rotation_plans"]), 1)


if __name__ == "__main__":
    unittest.main()
