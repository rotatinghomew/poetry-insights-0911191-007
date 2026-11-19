"""可调阈值与判定标签。

所有阈值集中在此处，审批时可以说明每个数字的含义，
而不是把主观切分藏在分析代码里。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Thresholds:
    # 判定“有充分共同曝光”所需的最少共同开放分钟数
    sufficient_coexposure_minutes: int = 30
    # 判定“有充分共同曝光”所需的最少共同开放时段（slot）数
    sufficient_coexposure_slots: int = 1
    # 同时使用两个项目的去重匿名会话数下限（低于此值视为证据稀薄）
    min_couse_support: int = 2
    # 共同使用率（共同会话 / 较少使用方会话）达到此值才算重复信号
    duplicate_couse_rate: float = 0.60
    # 共同使用率低于此值，且共同曝光充分时，视为互补证据
    complementary_couse_rate: float = 0.20
    # 冷启动：使用会话数低于此值的项目不参与同质 / 互补定性
    cold_min_sessions: int = 2
    # 分群沿革匹配的稳定性分数线
    lineage_match_score: float = 0.75
    lineage_low_score: float = 0.50
    # 沿革评分中各维度的权重
    weight_goal: float = 0.35
    weight_format: float = 0.20
    weight_channel: float = 0.20
    weight_slot: float = 0.15
    weight_cohesion: float = 0.10
    # 一组拆成两组 / 两组合成一组时，多个候选的分差小于此值则不确定
    lineage_ambiguous_gap: float = 0.10


# 判定码（机器可读，报告层再翻译为中文说明）
SCHEDULE_SEPARATED = "schedule_separated"
INSUFFICIENT_EXPOSURE = "insufficient_exposure"
ROUTE_ONLY = "route_only"
DUPLICATE = "duplicate"
COMPLEMENTARY = "complementary"
INCONCLUSIVE = "inconclusive"

PAIR_VERDICTS = (
    SCHEDULE_SEPARATED,
    INSUFFICIENT_EXPOSURE,
    ROUTE_ONLY,
    DUPLICATE,
    COMPLEMENTARY,
    INCONCLUSIVE,
)

VERDICT_LABELS_ZH = {
    SCHEDULE_SEPARATED: "从不同时开放（排班造成的假相似，不能判同质）",
    INSUFFICIENT_EXPOSURE: "共同曝光或共同使用样本不足（资料稀薄，暂不判定）",
    ROUTE_ONLY: "仅挂同一团体路线（渠道造成的假相似，散客无共同使用）",
    DUPLICATE: "充分共同曝光下高度共同使用（重复体验候选）",
    COMPLEMENTARY: "充分共同曝光但少有共同使用（互补证据）",
    INCONCLUSIVE: "信号处于中间地带（证据不足以定性）",
}

COLD_LABEL_ZH = "冷启动 / 长期遇冷：会话样本不足，仅给暂定位置，不保证同质或互补"
