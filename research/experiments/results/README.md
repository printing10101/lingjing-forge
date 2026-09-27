# results/ — 实验产出目录

## 子目录（主题结果）
| 目录 | 内容 |
|---|---|
| `paper_figs/` | **论文图件**（Fig.1–11，论文 md 直接引用，勿动） |
| `paper_tables/` | 论文表格产物（csv/tex） |
| `figures/` | 其他图件（特征重要性等） |
| `thermal_sld/` | 热 SLD 频域结果（fig1–3 + summary.json） |
| `closed_loop/` | 时域闭环抑制结果（fig + summary.json） |
| `r_sensitivity/` | 温差比 r 鲁棒性（fig10 + summary.json） |
| `multi_modal/` | 双模态验证（fig11 + summary.json） |
| `multi_material/` | 多材料增益（summary.json） |
| `literature_validation/` | 文献交叉验证（summary.json） |
| `lnn_mapping/` | LNN 功率映射（summary.json） |
| `uncertainty/` | 蒙特卡洛不确定性（summary.json） |
| `gain_strategies/` | 快速增益策略 |
| `calibration/` | 标定结果 |
| `logs_训练日志/` | **训练/实验日志**（ablation_v4_*.log、lomo_*.log 等，纯过程产物） |
| `_archive_备份/` | 紧急/关机备份（_emergency_backup_*、_shutdown_backup_*，可删除） |

## 根目录文件（被 analyze/run 脚本引用，勿动）
- `*_results.json` / `*_results.csv` / `*.tex`：各实验的结构化结果（main_comparison、ablation、cross_condition、table1–7 等）
- `*.bat` / `*.cmd` / `*.ps1` / `run_*.py` / `_monitor_*.py` / `_register_*.py`：训练启动/监控/注册脚本（内部含相对路径，保持原位）
- `feature_importance.png` / `comparison_report.txt` 等散件

## 说明
- 论文 md 只引用 8 个主题子目录（paper_figs/thermal_sld/closed_loop/r_sensitivity/multi_modal）中的图件——**这些路径是论文文档的硬依赖**。
- 新增实验请按主题建子目录并更新本索引。
