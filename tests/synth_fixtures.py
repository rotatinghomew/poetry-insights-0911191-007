"""测试用合成数据集构造工具。

直接构造数据对象，避免测试依赖工作目录；
另在 test_loading 中覆盖真实 CSV 的载入与校验。
"""

from __future__ import annotations

from poetry_rotation.models import Commitment, Dataset, Exposure, Item, Session


def make_item(
    item_id: str,
    title: str = "项目",
    fmt: str = "触屏",
    goal: str = "山水",
    quarter: str = "Q1",
    revision: str = "1",
) -> Item:
    return Item(item_id, quarter, title, fmt, goal, revision)


def make_exposure(
    item_id: str, slot: str, minutes: int = 60, available: bool = True, quarter: str = "Q1"
) -> Exposure:
    return Exposure(item_id, quarter, slot, minutes, available)


def make_session(
    session_id: str,
    item_id: str,
    group: str,
    channel: str = "散客",
    interactions: int = 5,
    quarter: str = "Q1",
) -> Session:
    return Session(session_id, item_id, quarter, group, channel, interactions)


def make_commitment(
    course_id: str, item_id: str, slot: str, goal: str, quarter: str = "Q1"
) -> Commitment:
    return Commitment(course_id, quarter, item_id, slot, goal)


def build_dataset(
    items: list[Item],
    exposures: list[Exposure],
    sessions: list[Session],
    commitments: list[Commitment] | None = None,
    quarter: str = "Q1",
) -> Dataset:
    dataset = Dataset(quarter=quarter)
    for item in items:
        dataset.items[item.item_id] = item
    dataset.exposures = list(exposures)
    dataset.sessions = list(sessions)
    dataset.commitments = list(commitments or [])
    for exposure in dataset.exposures:
        dataset.exposure_by_item.setdefault(exposure.item_id, []).append(exposure)
    for session in dataset.sessions:
        dataset.sessions_by_item.setdefault(session.item_id, []).append(session)
    for commitment in dataset.commitments:
        dataset.commitments_by_item.setdefault(commitment.item_id, []).append(commitment)
    return dataset


def overlap_scenario() -> Dataset:
    """覆盖五种判定路径的合成季度数据。

    - A/B：充分共同曝光 + 高共同使用 -> duplicate
    - A/C：充分共同曝光 + 低共同使用 -> complementary
    - A/D：共同使用仅来自团体渠道 -> route_only
    - A/E：从不同时开放 -> schedule_separated
    - A/F：F 冷启动，样本不足 -> 暂定，不定性
    """
    items = [
        make_item("A", "甲", goal="山水"),
        make_item("B", "乙", goal="山水"),
        make_item("C", "丙", goal="地理"),
        make_item("D", "丁", goal="山水"),
        make_item("E", "戊", goal="山水"),
        make_item("F", "己", goal="山水"),
    ]
    exposures = [
        make_exposure("A", "早", 60),
        make_exposure("B", "早", 60),
        make_exposure("C", "早", 60),
        make_exposure("D", "早", 60),
        make_exposure("E", "晚", 60),
        make_exposure("F", "早", 60),
    ]
    sessions: list[Session] = []
    seq = 0

    def add(group: str, item_ids: list[str], channel: str = "散客") -> None:
        nonlocal seq
        for item_id in item_ids:
            seq += 1
            sessions.append(make_session(f"S{seq:02d}", item_id, group, channel))

    # 三个散客群同时使用 A、B；A、C 仅两个群共同使用，C 另有大量互斥用户
    add("G1", ["A", "B"])
    add("G2", ["A", "B", "C"])
    add("G3", ["A", "B"])
    add("G15", ["A", "C"])
    add("G4", ["C"])
    add("G5", ["C"])
    add("G11", ["C"])
    add("G12", ["C"])
    add("G13", ["C"])
    add("G14", ["C"])
    add("G16", ["C"])
    add("G17", ["C"])
    add("G18", ["C"])
    add("G19", ["C"])
    # A 的独占群：把较少使用方 A 的基数拉大，使共同使用率落入互补区间
    add("G20", ["A"])
    add("G21", ["A"])
    add("G22", ["A"])
    add("G23", ["A"])
    # D 只在团体路线上与 A 共现，散客从不同时使用
    add("G6", ["A", "D"], channel="团体")
    add("G7", ["A", "D"], channel="团体")
    # E 在晚时段独立使用
    add("G8", ["E"])
    add("G9", ["E"])
    # F 只有一个会话（冷启动）
    add("G10", ["F"])
    return build_dataset(items, exposures, sessions)
