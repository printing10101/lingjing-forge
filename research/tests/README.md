# tests/ — 科研侧测试

运行：先清除 PYTHONPATH，`cd research` 后执行 `.venv/Scripts/python.exe -m pytest tests/ -q`（科研独立环境，当前 Python 3.12.14）。

## 分组（文件名即主题）
| 组 | 文件 |
|---|---|
| **LAM 抑颤主线** | test_thermal_sld_model.py、test_closed_loop_chatter.py、test_experimental_calibration.py、test_calibration.py、test_multi_material.py、test_lnn_power_mapping.py |
| **LNN 模型/训练** | test_lnn_models_unit.py、test_torch_lnn_models.py、test_lnn_trainer.py、test_lnn_convergence.py、test_lnn_cross_validation.py、test_lnn_dataset.py、test_lnn_benchmark.py |
| **数据** | test_data_split.py、test_dataset_cache.py、test_prediction_distribution.py |
| **量化/不确定性** | test_quantization.py、test_uncertainty.py、test_residual_analysis.py |
| **训练环境** | test_gpu_training.py |
| **基准（已知遗留）** | test_model_benchmark.py（训练基准；数据实际位于 `datasets/uniwear/uniwear/uniwear.csv`，结果以当前运行判定） |

## 已知坑
- 必须 `env -u PYTHONPATH`（Hermes 桌面 app 注入的 PYTHONPATH 遮蔽命名空间）
- 用 `.venv/Scripts/python.exe -m pytest`（科研侧独立 venv），勿用 hermes venv 的 python

2026-10-21 清理已移除旧 Phase 1、agents_research、lnn_research 独立入口；现有测试未引用这些入口。完整清理与验证边界见 `../../docs/reports/research_cleanup_2026-10-21.md`。原型测试仍按各自目录独立执行，避免同名模块冲突。
