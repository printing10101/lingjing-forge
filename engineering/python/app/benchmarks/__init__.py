"""基准对比实验框架 —— LNN vs 传统机器学习模型。

提供XGBoost/Random Forest/SVR/MLP vs LNN的系统化性能对比。

定位说明（E-23）：
- 本目录为**研究期内部基准测试代码**，用于论文实验复现与模型选型对比，
  非生产运行时依赖，也**不纳入 pytest 默认收集**（testpaths=python/tests）。
- 性能基准（performance/）的结果写入 ``performance/history/`` 目录，
  属于实验产物，非单元测试 fixture。
- 如需运行：``python -m app.benchmarks.run_benchmark`` 或
  ``python -m app.benchmarks.performance.run_perf_benchmark``。
- 对比基线模型位于 ``models/``，与 LNN 推理路径解耦，可独立加载评估。
"""

from __future__ import annotations

from app.benchmarks.datasets import (
    load_uniwear_data,
    sample_training_subset,
    split_dataset,
)
from app.benchmarks.metrics import (
    MetricsResult,
    compute_all_metrics,
    compute_mae,
    compute_mape,
    compute_r2,
    compute_rmse,
)
from app.benchmarks.models.xgboost_baseline import XGBoostBaseline
from app.benchmarks.models.rf_baseline import RFBaseline
from app.benchmarks.models.svm_baseline import SVMBaseline
from app.benchmarks.models.mlp_baseline import MLPBaseline

__all__ = [
    "XGBoostBaseline",
    "RFBaseline",
    "SVMBaseline",
    "MLPBaseline",
    "MetricsResult",
    "compute_mae",
    "compute_rmse",
    "compute_r2",
    "compute_mape",
    "compute_all_metrics",
    "load_uniwear_data",
    "split_dataset",
    "sample_training_subset",
]
