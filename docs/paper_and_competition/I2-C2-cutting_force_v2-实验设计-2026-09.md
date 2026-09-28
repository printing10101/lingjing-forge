# I2 · C2 实验设计定稿：cutting_force v2 真实数据重训

> 日期：2026-09-28 ｜ 对应 I1 §3（9 月底承诺项「实验设计定稿」的交付物）
> 定位：可执行实验设计，所有现状数字均为实测（manifest / pandas 直查 / 代码核对），非叙事稿。

## 0. 事实基线（2026-09-28 实测）

| 事项 | 实测结果 | 证据 |
|---|---|---|
| v1 cutting_force | 100 行合成数据，`feature_order=[spindle_rpm, feed_rate, depth_of_cut, material_encoded]`，R²=0.998@合成测试集，MirrorMLP [4,64,64,1]，seed 42 | `engineering/python/models/lnn/cutting_force.manifest.json` |
| v1 溯源格式 | `source.kind=dataset_store`（dataset_id + 全长 content_hash） | 同上 |
| **uniwear.csv 实际列** | 仅 `timestamp, force_z, vibration_x, vibration_y, tool_wear, experiment_tag, dataset_tag`——**没有转速/进给/切深工况列** | pandas 直查 39,903 行 |
| uniwear 构成 | nuaa 31,388 行（W1–W8）+ phm2010 8,515 行（c1/c4/c6…） | `dataset_tag` 分布直查 |
| **I1 §3 假设修正** | I1 原文「主轴转速/进给从数据集工况列取」**不成立**：工况不在 csv，须按 `experiment_tag` 从原始数据集公开文档回填（W1–W8 与 c1–c6 均有公开工况记录） | 本文 §2 |
| v1 的 material_encoded | 训练集中为全零常数（x_mean[3]=0.0, x_std[3]=1.0，合成数据单材料），v2 必须新定编码映射并写入 manifest | v1 manifest data.standardization |
| 训练管线 | `scripts/train_registry_model.py` 已支持 `--csv/--features/--target`，wear_prediction 已走通真实数据路径（R²=0.823）；`MODEL_CLASS_MAP` 需加 `cutting_force_v2 → CFCModel` 一行（≤30 分钟） | 脚本头注释与 `MODEL_CLASS_MAP` |
| registry 并存 | 模型名唯一键注册，`cutting_force_v2` 与 v1 天然并存，互不影响服务 | `app/ai/lnn/inference/_runtime_registry.py` |
| 窗口化先例 | exp50 对 uniwear 按窗口切分（window=50, step=25，W1 得 234 窗） | `research/experiments/04_物理感知颤振/exp50_uniwear_real.py` |

## 1. 目标与达标线

- **主达标线**：真实数据 held-out 上 **R² ≥ 0.90 且 MAPE ≤ 15%**（对齐 wear_prediction 口径），held-out 切分必须用 §4 的实验分组口径。
- **溯源达标线**：manifest 指向真实数据集，path 相对 manifest 目录可解析，sha256 全长，任一同事按 manifest 可复现重训（seed 固定）。
- **时间**：2026-10-31 前完成（I1 §6 排程）；v1 保留不覆盖，作为三级对比的 v1 基线。

## 2. 数据构建（关键路径）

### 2.1 工况回填表 `conditions_lookup.csv`

C2 唯一的新增「外部事实」环节。按 `experiment_tag` 回填工况四元组：

| 列 | 说明 |
|---|---|
| `experiment_tag` | W1–W8 / c1–c6（约 14 行） |
| `dataset_tag` | nuaa / phm2010 |
| `spindle_rpm` / `feed_rate` / `depth_of_cut` | 原始数据集公开文档逐案核对录入 |
| `material_name` | 各实验工件材料，同时用于 `material_encoded` 编码（见 §2.3） |
| `source` | 文档出处（数据集说明书/论文页码/URL）+ 核对日期，**每行必填** |

规则：查不到可靠出处的 tag **不入表**，对应实验整体弃用（计数如实写进 manifest），禁止凭印象补数——与 C3 切参数据同一诚实边界。

### 2.2 窗口聚合脚本 `engineering/python/scripts/build_cutting_force_v2_dataset.py`

信号是时间序列而模型输入是工况（同实验内工况恒定、力随磨损漂移），需聚合成静态回归样本：

- 按 `experiment_tag` 分组 → 滑窗（起点对齐 exp50 先例：window=50, step=25，可在脚本参数里调）。
- 每窗产出：特征 = 该实验的工况四元组（窗口内恒定）；`y = force_z 窗口均值`；附加审计列（force_z 窗口 RMS、时间起止、experiment_tag 透传）——审计列**不进特征**。
- 预估样本量：39,903 行 / step 25 ≈ 1,500+ 窗，量级充分。
- 校验内置：NaN/异常处置计数回写 manifest（`rows_dropped_nan` 等字段）；物理合理性抽查——力随进给/切深的单调性、量纲检查，抽查脚本随单测交付。
- 产物：`engineering/python/data/training_sets/cutting_force_v2/uniwear_force_v2.csv`（目录对齐 v1 的 training_sets 组织）。

### 2.3 `material_encoded` 编码规则（新定）

v1 的 material_encoded 是常数，无先例可继承。定稿规则：按材料类别整数编码（如 45钢=0、6061=1、TC4=2、铸铁=3…），映射表写入 manifest `source.material_encoding`；编码顺序与 C3 切参数据库的材料命名对齐，避免两套口径。

## 3. 训练与溯源

- **模型名** `cutting_force_v2`（registry 并存，v1 不动）。
- 训练：`python scripts/train_registry_model.py cutting_force_v2 --csv .../uniwear_force_v2.csv --features spindle_rpm,feed_rate,depth_of_cut,material_encoded --target force_z_window_mean`；`MODEL_CLASS_MAP` 补 `cutting_force_v2: CFCModel`。超参沿用脚本默认（hidden 64×2、seed 42、patience 30），保证与 v1/wear 同构可比。
- **溯源格式**：P0 用 `source.kind=csv`（wear_prediction 已验证：相对路径 + 全长 sha256 + rows_dropped_nan，零代码改动）；dataset_store 注册（`DatasetStore.create/commit_version` 或 `POST /api/v1/datasets`）为 P1 增强，不阻塞验收。
- manifest `notes` 必须写明：数据为 uniwear 实测力信号 + 公开文档工况回填；评估切分口径（§4）；conditions_lookup 的 source 汇总。

## 4. 评估协议（防泄漏是本设计的核心决策）

**同实验相邻窗口强相关（工况相同、力慢漂移），随机窗口切分会虚高 R²。** 因此双口径并报，达标线只认分组口径：

| 口径 | 切分方式 | 用途 |
|---|---|---|
| 口径 A（乐观上界） | 窗口级随机切分（70/15/15） | 与文献常见做法可比 |
| **口径 B（达标口径）** | **按 experiment_tag 分组**（GroupShuffleSplit；tag 少时留一实验 LOEO） | 部署真实场景：对未见过的工况组合插值。**R²≥0.90 只在此口径上判定** |

- 若口径 B 不达标：**不许**退回口径 A 充数。如实双报并解释差距来源（组间工况差异 / 组内磨损漂移），这本身就是 D8 防 reward hacking 叙事的正面素材。
- 下游联动指标（D7 素材）：用 v2 对 C3 切参数据库的推荐参数做力预测，与手册区间比对，出「以前（v1 合成）/ 现在（v2 真实）」两列表。
- 人工抽检：随机 20 个窗口，人工核力值量级与工况一致性，记录进结果文档。

## 5. 备选与降级

| 方案 | 触发条件 | 内容与代价 |
|---|---|---|
| B1：仅用 phm2010 子集 | NUAA W1–W8 工况文档核实受阻 | 8,515 行（c1–c6，工况表最权威），样本量仍足；标签口径照实写 |
| B2：特征序重定义 | 工况回填全面受阻 | 振动特征 → 力（仿 `force_vibration_567` 思路，206 行太少仅作方法验证）；**与 v1 服务接口断裂**，只做科研对比、不入 registry 服务链 |
| 扩展 v2b（可选） | 主线完成后有余力 | 特征加振动 RMS 等信号统计量提精度；推理时无传感器信号，**不进服务链**，仅作敏感性分析 |

**标签性质诚实边界（沿 I1 §7）**：uniwear force_z 是实测力信号（优）；若走 B1/B2 且涉及 PHM2010 派生标签，材料里必须写「实测信号 + 物理派生标签」，不可称实测验证。

## 6. 排程（2026-10，含缓冲）

| 周 | 交付 |
|---|---|
| 第 1 周 | `conditions_lookup.csv` 核实入库 + 构建脚本 + 单测（窗口切分、分组防泄漏）+ 数据集落地 |
| 第 2 周 | 训练 + parity 校验 + manifest v2；不达标则调窗口/特征迭代 |
| 第 3 周 | 双口径评估执行 + v1/v2 对比表 + 结果文档（I3）+（P1）dataset_store 注册 |
| 第 4 周 | 缓冲 |

## 7. 验收清单

- [ ] `conditions_lookup.csv` 每行带 source 与核对日期，无凭印象补数
- [ ] 构建脚本 + 单测（含分组防泄漏的回归测试：随机切分与分组切分差异可复现）
- [ ] `cutting_force_v2.manifest.json`：口径 B 下 R²≥0.90 / MAPE≤15%，sha256 全长可复现
- [ ] `MODEL_CLASS_MAP` 补映射，`train_registry_model.py` 全链路跑通
- [ ] v1/v2 对比表（D7 两列格式）+ 20 条人工抽检记录
- [ ] manifest notes 含标签性质与数据来源诚实声明
