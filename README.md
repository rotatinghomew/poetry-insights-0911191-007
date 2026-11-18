# 诗词体验内容分群的轮换决策与连续性

本目录保存诗词体验内容分群的轮换决策与连续性相关的本地演练数据。记录均为合成内容，使用 UTF-8 CSV；空字段表示资料未提供，不代表数值为零。

## 数据

- `data/catalog.csv`：catalog记录。
- `data/exposure.csv`：exposure记录。
- `data/sessions.csv`：sessions记录。
- `data/course_commitments.csv`：course_commitments记录。

## 环境与验证

需要 Python 3.11 或更新版本；项目仅使用 Python 标准库。可用 `python3 -m unittest discover -s tests -v` 检查 CSV 结构与本地资料完整性。

分析入口与业务解释由项目使用者按实际工作需要补充。
