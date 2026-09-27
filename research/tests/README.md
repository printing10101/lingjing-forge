# tests/ — 科研侧测试（23 文件）

运行：`cd research && env -u PYTHONPATH ./.venv/Scripts/pytest tests/ -q`

## 分组（文件名即主题）
| 组 | 文件 |
|---|---|
| **LAM 抑颤主线** | test_thermal_sld_model.py、test_closed_loop_chatter.py、test_experimental_calibration.py、test_calibration.py、test_multi_material.py、test_lnn_power_mapping.py |
| **LNN 模型/训练** | test_lnn_models_unit.py、test_torch_lnn_models.py、test_lnn_trainer.py、test_lnn_convergence.py、test_lnn_cross_validation.py、test_lnn_dataset.py、test_lnn_benchmark.py |
| **数据** | test_data_split.py、test_dataset_cache.py、test_prediction_distribution.py |
| **量化/不确定性** | test_quantization.py、test_uncertainty.py、test_residual_analysis.py |
| **训练环境** | test_gpu_training.py |
| **基准（已知遗留）** | test_model_benchmark.py（需 Uniwear 数据集 `data/uniwear/uniwear.csv`，缺失时 3 failed + 4 errors） |

## 已知坑
- 必须 `env -u PYTHONPATH`（Hermes 桌面 app 注入的 PYTHONPATH 遮蔽命名空间）
- 用 `.venv/Scripts/pytest`（科研侧独立 venv），勿用 hermes venv 的 python
