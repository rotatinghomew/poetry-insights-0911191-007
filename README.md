# 诗词体验内容分群的轮换决策与连续性

本项目保存内容轮换所需的合成项目目录、开放时段、匿名交互和课程承诺资料，均使用 UTF-8 CSV。空字段表示记录未提供。

## 数据

- `data/catalog.csv`：季度项目、交互形式、教学目标及修订。
- `data/exposure.csv`：项目实际开放时间及状态。
- `data/sessions.csv`：匿名群体、渠道及交互次数。
- `data/course_commitments.csv`：课程对应内容及历史时段。

## 环境与验证

需要 Python 3.11 或更新版本；项目仅使用 Python 标准库。可用 `python3 -m unittest discover -s tests -v` 检查 CSV 结构与本地资料完整性。
