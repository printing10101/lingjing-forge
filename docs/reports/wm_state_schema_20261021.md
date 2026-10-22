# 世界模型状态向量语义与合成数据契约（2026-10-21）

> 状态：**v1 冻结**（世界模型通电 T1-1 前置）
> 适用：`app/plugins/world_model/`（ADR-017 / ADR-020 思路 1）
> 维护纪律：修改状态维度语义必须同步升级 checkpoint 版本号，禁止同版本号换语义。

## 1. state 8 维语义（`WorldModelConfig.state_dim = 8`）

代码依据：`net.py` 的 `trajectory_metrics` 取 `[0]` 峰值、`[1]` 峰值、
`[2]` 均值（`labels = ["chatter_peak", "max_wear", "avg_quality"]`）。
此前仓库仅明确前 3 维，其余 5 维无文档。本文件将其冻结如下：

| 索引 | 名称 | 单位/范围 | 语义 | 依据 |
|---|---|---|---|---|
| 0 | `chatter_probability` | [0, 1] | 颤振发生概率 | ADR-013 / plugin 输出标签 |
| 1 | `tool_wear` | mm, [0, 0.6] | 后刀面磨损量 VB | ADR-017 输出契约 |
| 2 | `surface_roughness` | µm, [0.2, 20] | 表面粗糙度 Ra | ADR-017 输出契约 |
| 3 | `vibration_rms` | g, [0, 10] | 振动 RMS | ADR-017 `current_state.vibration_rms` |
| 4 | `temperature_norm` | °C/100, [0.2, 8] | 温度（除以 100 归一化） | ADR-017 `current_state.temperature` |
| 5 | `cutting_force_norm` | N/1000 | 主切削力 Fz 归一化 | Kienzle 力模型 |
| 6 | `mrr_norm` | (cm³/min)/100 | 材料去除率归一化 | 切削参数派生 |
| 7 | `confidence` | [0, 1] | 状态估计置信度（1 - 0.5·颤振概率，v1 经验式） | 本文件定义 |

> 归一化说明：索引 4/5/6 除以常数是为了与其它维度同量级，便于 MSE 训练；
> 逆变换常数在 `scripts/build_wm_trajectory_dataset.py` 中集中定义。

## 2. 输入维语义

| 张量 | 维度 | 组成 |
|---|---|---|
| `geometry_seq` | 37 | bbox 3（mm）+ feature_vector 32（ADR-007 布局）+ symmetry 1 + complexity 1 |
| `dynamics_seq` | 6 | spindle_speed(rpm) / feed_rate(mm/min) / depth_of_cut(mm) / tool_wear(mm) / vibration_rms(g) / temperature(°C) |
| `actions` | 4 | rpm_delta / feed_delta / depth_delta / coolant_flag |
| `target_trajectory` | 8 | 上表 state 向量 |

feature_vector 32 维布局（合成数据遵循，真实数据由 ADR-007 提取器产出）：

| 区间 | 含义 |
|---|---|
| [0:8] | 平面面积占比（上下/四侧/两个台阶面） |
| [8:16] | 圆柱特征（数量比/直径/高/覆盖率 + 4 个派生统计） |
| [16:24] | 孔特征（数量比/直径/深径比/分布密度 + 4 个派生统计） |
| [24:32] | 槽/凸台特征（数量比/宽度/深度/间距 + 4 个派生统计） |

## 3. 合成轨迹的数据来源与标注纪律

**provenance = `physics_synthetic_v0`**（写入 `.meta.json`）

物理内核（全部复用仓库既有实现，未另造物理）：

| 量 | 来源 | 说明 |
|---|---|---|
| 稳定性极限切深 `a_lim` | 脚本内 `tlusty_a_lim()`（按项目文档公式重算，**未改动公共模块**） | `a_lim = 1/(2·Ks·|min Re G|)`，`|Re G|max = 1/(4kζ(1+ζ))`，按（刀具, 机床）缓存 |
| 切削力 `Fz` | `app/simulation/cutting_force/kienzle.compute_cutting_forces` | Kienzle 公式 |
| 磨损增量 | Archard 风格 `dw = 2e-9 · Fz · n · dt` | 经验系数，**非实测标定** |
| 表面粗糙度 | `Ra = 0.5 + 60·fz^1.5 + 2.5·p_chatter + 3·w` | 经验式，**非实测标定** |
| 温度 | 一阶热惯性滤波：`T ← T + 0.35(T_target - T)` | `T_target = 25 + 0.004·Fz·n/1000` |
| 振动 RMS | `0.0008·Fz + 1.5·p_chatter + N(0,0.05)` | 经验式 |

### 3.1 发现的实现偏差：`compute_stability_limit` 量级不符

实测（2026-10-21，本机）：

| 输入 | 返回 |
|---|---|
| `vmc_850` + `endmill_d10` | **30.59 mm** |
| `small_vmc_640` + `endmill_d10` | 30.59 mm（与 vmc_850 相同）|
| `vmc_850` + `endmill_d20` | 25.49 mm |

两个问题：
1. **量级偏离**：真实钢件铣削极限切深通常 0.5–3 mm，30 mm 不可信——该实现只在
   `Re[G] > 0` 的频点里取最小值，单自由度 FRF 实部最负点（真正决定极限的位置）
   被跳过，导致结果被 `100 mm` 上限裁剪附近的值主导；
2. **不随转速变化**：函数体未使用 `spindle_rpm`，叶瓣结构不存在；
3. `MachineParams(machine_id=...)` 不会加载预设，必须用 `get_machine_params()`。

处理（2026-10-21 已修公共模块）：`compute_stability_limit` 已修复——改为
取 FRF 实部**最负**点（再生颤振由负实部供能），并用 `sqrt(k/m)` 推导真实共振
频率（预设表 `natural_freq` 与模态参数不一致，按 100 Hz 扫描会完全错过负实部）。
修复后 vmc_850：d10 **10.5 mm**、d20 8.75 mm（原 30.59 / 25.49 mm）；
`test_stability.py` 24 个用例全部通过。

数据集脚本仍保留自带闭式实现（`tlusty_a_lim`），因为它用 **stiffness_x
（最柔性轴，1.5e7 N/m）** 得到 0.5–1.2 mm——更接近真实钢件铣削；公共函数按
原设计用 `stiffness_z`（2e8 N/m = 200 N/µm），量级仍偏高（7.95–10.5 mm）。

**待办（P1）**：轴选择（Z 刚度 vs 最柔性轴）属建模决策，需单独立项评估；
对外引用颤振极限数值前必须先确认轴口径。另：`test_predictor.py` 有 5 个
**既有**红测试（断言旧契约「无模型返回正值/5.0」，HEAD 代码实际返回 -1.0
哨兵），与本次修复无关，需单独订正契约与测试。

**红线**：
1. 该数据集**不是实测加工数据**，不得对外表述为"真实数据训练"；
2. 磨损/粗糙度经验系数未标定，仅用于打通训练-推理闭环与预训练；
3. 真实数据到位后（PHM2010 / 自采 6061-T6）须做微调校准，并在报告里注明
   "仿真预训练 + 实机校准"；
4. `references`：颤振阈值、刀具磨损、表面粗糙度的最终对外口径以
   《真实数据验证现状.md》与 ADR-020 证据缺口表为准。

## 4. 验收记录

| 项 | 要求 | 实测 |
|---|---|---|
| 样本数 | ≥1000 | **1500**（T=20, H=5，npz 1.14 MB） |
| `validate_sample` | 全量通过 | **1500/1500 通过** |
| state 维度 | 与 `WorldModelConfig.state_dim` 一致 | 8 |
| 可复现 | 固定 `--seed` 重跑一致 | 是（`np.random.default_rng(42)`，无时间依赖） |
| 颤振概率分布 | 覆盖稳定/临界/不稳定 | min 0.0019 / mean 0.42 / max 0.999 |
| 目标值范围 | 物理合理 | wear 0.002–0.31 mm；Ra 0.56–8.39 µm；T 25–39 °C；Fz 1–800 N |

> 产出路径：`data/world_model/trajectories_v1.npz` + `.meta.json`
> （`provenance = physics_synthetic_v0`）。
