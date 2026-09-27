"""real_validation — SLD/LNN 引擎的"实测数据"真实验证包（零设备方案）。

目标：给 7 维切削参数 → 极限切深 a_lim 的预测引擎提供**真实测量数据**验证通道，
替代仓库中"自采 6061-T6 / NIST"等合成占位。

三个零成本真实数据源：
  1. Zenodo i-CNC 铣削颤振数据集（record 15308467）——真实振动 + 颤振标注
  2. 文献实测稳定性点（带 DOI 的已发表测量数据，经 ingest_literature_points.py 录入）
  3. Piecuch 2025（仓库已有真实铣削信号数据，作补充实验，非 SLD 验证）

学术诚信硬约束：
  - 任何进入 measured_stability_points.csv 的行必须有 source + doi 字段
  - source 标注 "SCHEMA-FIXTURE" 的行是接口测试示例，**严禁**当作实测数据使用
  - 本包只做"评估/验证"，不做 a_lim 回归训练（实测边界通常只有二元稳定/失稳标签）
"""

from .measured_stability_dataset import (
    MeasuredStabilityPointsDataset,
    evaluate_stability_classification,
)
from .schema import SCHEMA_COLUMNS, validate_schema

__all__ = [
    "MeasuredStabilityPointsDataset",
    "evaluate_stability_classification",
    "SCHEMA_COLUMNS",
    "validate_schema",
]
