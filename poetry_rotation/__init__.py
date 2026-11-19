"""诗词互动内容季度轮换分析包。

仅依赖 Python 标准库。子模块：

- models:    数据类与数据集容器
- loading:   UTF-8 CSV 载入与一致性校验
- overlap:   共同曝光 / 共同使用分析与项目对判定
- segments:  分群构建、切换时段与团体渠道处理后的稳定性、分群沿革
- planning:  受约束的轮换方案生成与限额内比较
- report:    机器可读 JSON 与人工可读 Markdown 报告
"""

__all__ = [
    "models",
    "loading",
    "overlap",
    "segments",
    "planning",
    "report",
    "config",
]
