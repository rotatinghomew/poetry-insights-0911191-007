# 诗词体验内容分群的轮换决策与连续性

本项目保存内容轮换所需的合成项目目录、开放时段、匿名交互和课程承诺资料，均使用 UTF-8 CSV。空字段表示记录未提供。

## 数据

- `data/catalog.csv`：季度项目、交互形式、教学目标及修订。
- `data/exposure.csv`：项目实际开放时间及状态。
- `data/sessions.csv`：匿名群体、渠道及交互次数。
- `data/course_commitments.csv`：课程对应内容及历史时段。

## 分析包 `poetry_rotation/`

仅使用 Python 标准库，需要 Python 3.11+。

| 模块 | 职责 |
|---|---|
| `loading.py` | CSV 载入、缺列 / 坏值 / 悬空外键校验，问题进入报告而非静默丢弃 |
| `overlap.py` | 共同曝光与共同使用信号、项目对五档判定、冷启动识别 |
| `segments.py` | 分群画像、去时段 / 去团体渠道的稳定性评分、跨季度沿革（拆分 / 合并 / 交叠） |
| `planning.py` | 开放时间、交互形式、课程目标三重约束下的替代候选与限额内方案枚举 |
| `report.py` | 机器可读 JSON、人工可读 Markdown、逐项目档案 |
| `config.py` | 全部阈值与判定码集中管理 |

### 项目对的五档判定

判定顺序固定，先排除“假相似”，再在证据充分时定性：

1. `schedule_separated`：没有任何共同开放时段，排班决定了不可能共同使用；
2. `insufficient_exposure`：共同曝光分钟 / 时段不足，或冷启动项目参与，暂不判定；
3. `route_only`：共同使用只来自团体渠道（同一条团体路线串起来），散客无共同使用；
4. `duplicate`：充分共同曝光、共同使用样本充分且共同使用率达到重复线；
5. `complementary`：充分共同曝光、样本充分但共同使用率低于互补线；
6. `inconclusive`：信号处于中间带，证据不足以定性。

冷启动 / 长期遇冷项目（会话数低于 `cold_min_sessions`）只给暂定位置：
没有共同使用记录既不能保证互补，也不能保证相同，且不进入撤项池。

### 分群稳定性与沿革

- 匹配只看行为构成（课程目标、交互形式、渠道、时段分布、参与深度），
  **不按主题名或 item_id 做同名映射**；沿用主题名不代表受众相同。
- 同时输出三个分数：原始分、去时段权重分（读取切换时段的影响）、
  仅散客重建画像分（读取团体渠道的影响）；散客资料缺失时显式标注“不可算”。
- 一组拆两组、两组合并、互相交叠时输出结构化沿革并进入人工核对清单，
  不强行一一对应；承担过课程承诺的分群在沿革中保留 `course_ids` 可查回。

### 轮换方案约束

撤项必须存在同时满足以下条件的保留替代项目，否则进入阻断清单：

1. 替代项目覆盖被撤项目的**全部**开放时段；
2. 交互形式一致；
3. 覆盖被撤项目承担的**每一个不同**课程目标（按目标逐个核对，
   不能用保留点位的数量冒充目标覆盖）。

方案在限额内全量枚举（含全保留基线），按可行性与证据得分排序；
课程承诺受损会逐条列在方案的 `lost_commitments` 中。

## 运行

建议以可编辑方式安装后用隔离模式运行（避免数据目录中的同名模块被导入）：

```bash
python3 -m pip install -e .
python3 -I -m poetry_rotation \
  --data-dir data --quarter Q2 --quota 2 \
  --compare-quarter Q1 --output-dir out
```

输出：

- `out/rotation_report_<季度>.json`：完整机器可读结果（含阈值、信号、方案、沿革）；
- `out/rotation_report_<季度>.md`：审批用中文报告与逐项目档案。

阈值可通过命令行覆盖（如 `--cold-min-sessions`、`--min-couse-support`、
`--sufficient-coexposure-minutes`），其余阈值在 `poetry_rotation/config.py` 调整。

## 测试

```bash
python3 -I -m unittest discover -s tests -v
```

- `tests/test_fixtures.py`：CSV 结构与本地资料完整性；
- `tests/test_analysis.py`：五档判定路径、冷启动保护、替换约束、
  课程承诺影响、分群拆分 / 合并 / 无后继沿革、CLI 冒烟；
- `tests/synth_fixtures.py`：内存合成数据集，测试不依赖工作目录。
