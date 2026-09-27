# training/ — 训练框架

被 `research.training.*` 绝对导入（61 处），**目录不可重命名/移动**。

| 文件 | 内容 |
|---|---|
| `dataset.py` / `dataset_cache.py` | 数据集加载与缓存 |
| `bosch_dataset.py` | Bosch 数据集加载 |
| `trainer.py` | 训练器 |
| `evaluator.py` | 评估器 |
| `experiment_tracker.py` | 实验追踪（start_run 等） |
| `device_manager.py` | 设备管理 |
| `reproducibility.py` | 可复现性（set_global_seed） |
| `tracking/` | 追踪组件 |
