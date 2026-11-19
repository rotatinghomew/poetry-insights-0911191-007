"""命令行入口。

示例：
    python -I -m poetry_rotation --data-dir data --quarter Q2 --quota 2 \
        --compare-quarter Q1 --output-dir out

在隔离模式（-I）下运行，避免数据目录中同名模块被意外导入。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Thresholds
from .loading import available_quarters, load_dataset
from .report import build_report, write_json, write_markdown
from .segments import stability_report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="诗词互动内容季度轮换分析")
    parser.add_argument("--data-dir", required=True, help="CSV 数据目录")
    parser.add_argument("--quarter", required=True, help="本季度（如 Q2）")
    parser.add_argument("--quota", type=int, default=2, help="轮换限额（最多撤换点位数）")
    parser.add_argument(
        "--compare-quarter",
        default=None,
        help="用于分群稳定性比较的上一季度；不提供则跳过沿革分析",
    )
    parser.add_argument("--output-dir", default="out", help="报告输出目录")
    parser.add_argument(
        "--sufficient-coexposure-minutes",
        type=int,
        default=Thresholds.sufficient_coexposure_minutes,
    )
    parser.add_argument("--min-couse-support", type=int, default=Thresholds.min_couse_support)
    parser.add_argument("--cold-min-sessions", type=int, default=Thresholds.cold_min_sessions)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    data_dir = Path(args.data_dir)
    known = available_quarters(data_dir)
    if known and args.quarter not in known:
        print(f"季度 {args.quarter} 不在资料中（现有：{', '.join(known)}）", file=sys.stderr)
        return 2

    thresholds = Thresholds(
        sufficient_coexposure_minutes=args.sufficient_coexposure_minutes,
        min_couse_support=args.min_couse_support,
        cold_min_sessions=args.cold_min_sessions,
    )

    dataset = load_dataset(data_dir, args.quarter)

    stability = None
    if args.compare_quarter:
        old_dataset = load_dataset(data_dir, args.compare_quarter)
        stability = stability_report(old_dataset, dataset, thresholds)

    report = build_report(dataset, args.quota, thresholds, stability)
    output_dir = Path(args.output_dir)
    json_path = output_dir / f"rotation_report_{args.quarter}.json"
    md_path = output_dir / f"rotation_report_{args.quarter}.md"
    write_json(report, json_path)
    write_markdown(report, md_path)

    print(f"已写出 {json_path}")
    print(f"已写出 {md_path}")
    if report["data_issues"]:
        print(f"注意：载入阶段发现 {len(report['data_issues'])} 条资料问题，详见报告。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
