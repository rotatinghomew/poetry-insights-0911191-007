"""UTF-8 CSV 载入、基础校验与数据集组装。

设计原则：
- 数据文件是外部资料，读取时不依赖调用方的工作目录；
- 结构问题（缺列、坏布尔、悬空外键）收集到 Dataset.issues，
  而不是静默丢弃；分析报告会原样披露。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Commitment, Dataset, Exposure, Item, Session

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "catalog.csv": ["item_id", "quarter", "title", "format", "course_goal", "revision"],
    "exposure.csv": ["item_id", "quarter", "slot", "minutes", "available"],
    "sessions.csv": ["session_id", "quarter", "anonymous_group", "channel", "interactions"],
    "course_commitments.csv": ["course_id", "quarter", "item_id", "slot", "goal"],
}


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames or []
        rows = [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()} for row in reader]
    return header, rows


def _parse_bool(raw: str, context: str, issues: list[str]) -> bool:
    value = raw.lower()
    if value in ("true", "1", "yes", "y"):
        return True
    if value in ("false", "0", "no", "n"):
        return False
    issues.append(f"{context}: 无法解析布尔值 {raw!r}，按不可用处理")
    return False


def _parse_int(raw: str, context: str, issues: list[str], default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        issues.append(f"{context}: 无法解析整数 {raw!r}，按 {default} 处理")
        return default


def load_dataset(data_dir: str | Path, quarter: str) -> Dataset:
    """载入某个季度的四张表并做交叉校验。"""
    data_dir = Path(data_dir)
    dataset = Dataset(quarter=quarter)

    tables: dict[str, list[dict[str, str]]] = {}
    for name, required in REQUIRED_COLUMNS.items():
        path = data_dir / name
        if not path.exists():
            dataset.issues.append(f"缺少数据文件：{name}")
            tables[name] = []
            continue
        header, rows = _read_csv(path)
        missing = [col for col in required if col not in header]
        if missing:
            dataset.issues.append(f"{name} 缺少列：{', '.join(missing)}")
        tables[name] = rows

    # ---- catalog ----
    for row in tables["catalog.csv"]:
        if row.get("quarter") != quarter:
            continue
        item_id = row.get("item_id", "")
        if not item_id:
            dataset.issues.append("catalog 中存在空 item_id 的行，已跳过")
            continue
        if item_id in dataset.items:
            dataset.issues.append(f"catalog 中 {item_id} 重复定义，后一条覆盖前一条")
        dataset.items[item_id] = Item(
            item_id=item_id,
            quarter=quarter,
            title=row.get("title", "") or "",
            format=row.get("format", "") or "",
            course_goal=row.get("course_goal", "") or "",
            revision=row.get("revision", "") or "",
        )

    known_items = set(dataset.items)

    # ---- exposure ----
    for row in tables["exposure.csv"]:
        if row.get("quarter") != quarter:
            continue
        item_id = row.get("item_id", "")
        if item_id not in known_items:
            dataset.issues.append(f"exposure 引用了 catalog 中不存在的项目 {item_id}，已跳过")
            continue
        exposure = Exposure(
            item_id=item_id,
            quarter=quarter,
            slot=row.get("slot", "") or "",
            minutes=_parse_int(row.get("minutes", ""), f"exposure {item_id}", dataset.issues),
            available=_parse_bool(row.get("available", ""), f"exposure {item_id}", dataset.issues),
        )
        dataset.exposures.append(exposure)

    # ---- sessions ----
    seen_session_ids: set[str] = set()
    for row in tables["sessions.csv"]:
        if row.get("quarter") != quarter:
            continue
        session_id = row.get("session_id", "")
        if session_id in seen_session_ids:
            dataset.issues.append(f"sessions 中 {session_id} 重复，已跳过")
            continue
        seen_session_ids.add(session_id)
        item_id = row.get("item_id", "")
        if item_id not in known_items:
            dataset.issues.append(f"sessions 引用了 catalog 中不存在的项目 {item_id}，已跳过")
            continue
        dataset.sessions.append(
            Session(
                session_id=session_id,
                item_id=item_id,
                quarter=quarter,
                anonymous_group=row.get("anonymous_group", "") or "",
                channel=row.get("channel", "") or "",
                interactions=_parse_int(row.get("interactions", ""), f"sessions {session_id}", dataset.issues),
            )
        )

    # ---- commitments ----
    for row in tables["course_commitments.csv"]:
        if row.get("quarter") != quarter:
            continue
        item_id = row.get("item_id", "")
        if item_id not in known_items:
            dataset.issues.append(f"course_commitments 引用了不存在的项目 {item_id}，已跳过")
            continue
        dataset.commitments.append(
            Commitment(
                course_id=row.get("course_id", "") or "",
                quarter=quarter,
                item_id=item_id,
                slot=row.get("slot", "") or "",
                goal=row.get("goal", "") or "",
            )
        )

    _build_indexes(dataset)
    return dataset


def _build_indexes(dataset: Dataset) -> None:
    for exposure in dataset.exposures:
        dataset.exposure_by_item.setdefault(exposure.item_id, []).append(exposure)
    for session in dataset.sessions:
        dataset.sessions_by_item.setdefault(session.item_id, []).append(session)
    for commitment in dataset.commitments:
        dataset.commitments_by_item.setdefault(commitment.item_id, []).append(commitment)


def available_quarters(data_dir: str | Path) -> list[str]:
    """扫描 catalog，返回资料中出现过的季度（按字符串排序）。"""
    path = Path(data_dir) / "catalog.csv"
    if not path.exists():
        return []
    _, rows = _read_csv(path)
    return sorted({row.get("quarter", "") for row in rows if row.get("quarter")})
