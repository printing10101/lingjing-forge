# experiments/ — 实验脚本与成果（物理分组版）

> **结构**：核心库留根（tests 锚点 + 30+ 脚本依赖），主题实验按目录物理分组。**目录名即主题**。
> **运行方式（重要）**：脚本间用无前缀 import（`from models import ...`、`import exp46_tlusty_mismatch` 等），运行入口固定为 **`cd research/experiments` + PYTHONPATH 注入**（sys.path[0]=experiments/ 使根库 models.py 优先于 research/models 包）：
> ```bash
> cd research/experiments
> export PYTHONPATH="02_LAM激光抑颤;03_PHM2010工具磨损;04_物理感知颤振;05_论文图件与表格;90_临时归档;..;../..;../../engineering/python"
> ../.venv/Scripts/python.exe 02_LAM激光抑颤/thermal_sld_model.py   # 例：热 SLD 实验
> ```

## 目录结构（名称即内容）

```
experiments/
├── models.py / config.py / metrics.py / losses.py / data_generator.py / trainer.py / train_models.py
│        ↑ 核心库（留根：tests 锚定 + 被 30+ 脚本引用，勿移）
├── 02_LAM激光抑颤/        ← 2026-08 论文主线：热 SLD 模型 → 标定 → 闭环 → 鲁棒性 → 论文评审
├── 03_PHM2010工具磨损/    ← exp7–45 系列：LNN 工具磨损深度学习 30+ 实验
├── 04_物理感知颤振/       ← exp46–52b：Tlusty 失配诊断 → 物理感知门控 → 跨数据集迁移
├── 05_论文图件与表格/     ← 图件生成/表格修订/文档工具（独立脚本）
├── 90_临时归档/           ← 一次性诊断/冒烟脚本（零依赖，可清理）
├── results/               → 实验产出（见 results/README.md）
├── logs/  checkpoints/  backup_150dpi_json/   ← 日志/检查点/备份
```

## 02_LAM激光抑颤/（论文主线，15 脚本 + lab_protocols/）
| 文件 | 内容 |
|---|---|
| `thermal_sld_model.py` | 热扩展 Tlusty 稳定性模型（定理 1/2 载体） |
| `exp_thermal_sld.py` | 热 SLD 频域实验（Fig.1–3） |
| `closed_loop_chatter.py` | 时域闭环抑制（ff+pi，§6，含双模态扩展 mode2） |
| `literature_validation.py` | 文献力降交叉验证（7/7，§4.5，Fig.9） |
| `multi_material.py` | 多材料增益（§8） |
| `multi_modal.py` | 双模态时域验证（11/11，§8.1，Fig.11） |
| `r_sensitivity.py` | 温差比 r 鲁棒性（25/25，§7.3，Fig.10） |
| `lnn_power_mapping.py` | LNN→功率映射代理（R²=0.98，§9） |
| `uncertainty_propagation.py` | 蒙特卡洛不确定性（§7.2） |
| `calibrate_kappa_delta.py` / `calibrate_from_literature_experiments.py` | κ/δ/ξ 标定（§4） |
| `paper_figures.py` | 论文图件统一生成（Fig.5–7） |
| `review_paper_v1.py` | 论文 8 采样 AI 评审（→ ../docs/review_outputs/） |
| `lab_analysis.py` + `lab_protocols/` | E1–E5 实验协议与分析 |
| `quick_gain_strategies.py` | 快速增益策略 |

## 03_PHM2010工具磨损/（49 脚本）
- `exp7_main_comparison.py` 主对比；`exp9_cross_condition.py` 跨工况；**exp10–45**：消融/噪声鲁棒/迁移/不确定性/多传感器/数据效率等（文件名即主题）
- 入口：`run_experiment.py`、`run_all_experiments.py`、`run_benchmark.py`、`run_lnn_benchmark.py`、`run_phm2010_only.py`
- 其他：`optuna_search.py`（超参）、`business_logic_bench.py`、`lnn_training_example.py`、`test_phm2010*.py`

## 04_物理感知颤振/（9 脚本，组内强互依赖）
`exp46_tlusty_mismatch.py`（基类）← `exp47/47b_physics_aware_gate*.py` ← `exp48_mismatch_augmented.py` ← `exp49_spindle_extrapolation.py`；`exp50_uniwear_real.py` ← `exp52/52b_cross_dataset_transfer.py`；`exp51_sld_distribution.py`

## 05_论文图件与表格/（23 脚本）
图件：`generate_*_figures.py`、`insert_figures_to_paper.py`、`re_render_300dpi.py`、`fig01_sld.py`、`visualize.py`、`paper_experiment_comparison.py`
表格/文档：`update_paper_tables.py`、`fill_table7*.py`、`check/view_table7.py`、`analyze_*`、`revise_paper_academic_integrity.py`、`verify_final_paper.py`、`gen_chaxin.py`、`gen_shenbao.py`、`extract_*.py`

## 90_临时归档/（17 脚本，零依赖，可清理）
`_smoke_exp*.py`、`_test_*.py`、`diagnose_*.py`、`verify_gp_fix.py`、`rerun_gp.py`、`show_results.py`、`analyze_results.py`、`run_benchmark_废弃启动器.py`
（废弃启动器：引用仓库中不存在的 `app.ai.lnn.tests.benchmark_lnn`，docstring 指向解耦前旧路径）

## 测试
```bash
cd research && env -u PYTHONPATH ./.venv/Scripts/pytest tests/ -q
```

## 遗留问题（2026-08-12 巡检后）
- `tests/test_model_benchmark.py` 中 **CFC benchmark 失败**：numpy `CFCModel` 是 MLP 近似（无液体状态机制），torch `TorchCFCModel` 是真 CFC（backbone 输入为 input+hidden 拼接）——权重布局不兼容（numpy 权重 `(in,out)` vs torch `(in+hidden,hidden)`），属**架构级缺陷**，修复需重写 numpy 版（对齐 torch CFC 结构）。LTC/HybridLNN benchmark 已修复通过。
- **DL-LNN 数值稳定性缺陷（2026-08 真实数据验证中发现，待修）**：在部分真实数据上
  - torchdiffeq 自适应积分发散：`AssertionError: underflow in dt nan`（LTCCell + dopri5，发生在 567 力数据）；
  - DLLNNWithPhysics 包装器 stage-2 训练出现 NaN（即便强制 Euler 路径）——可能与 PCC_Loss 数值路径有关。
  建议修复：① LTC ODE 求解器统一强制 `solver='euler'`（或加入 torchdiffeq 失败自动降级 + 梯度裁剪）；
  ② 排查 stage-2 PCC_Loss 的 NaN 来源。复现：`real_validation/run_force_prediction_validation.py`（当前已用干净 LTCCell 回归器绕过）。
- 其余测试全绿（Uniwear 数据路径已修复：测试指向 `datasets/uniwear/uniwear/`）。
