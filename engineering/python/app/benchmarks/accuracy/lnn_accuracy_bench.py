"""LNN 精度基准（对照解析解）——「敢上机」证据链的软件侧部分.

对照对象：cutting_force 模型 vs Kienzle 解析解（合成数据的真值来源）。
口径诚实声明：当前 LNN 学的是 Kienzle 的响应面（仿真合成数据训练），
本基准度量的是**链路保真度**（训练→导出→装载→预测 与解析解的一致性），
不是超越解析解——真实切削数据回灌后才可宣称精度优势（W4.3 / W9.3）。

用法：
    python -m app.benchmarks.accuracy.lnn_accuracy_bench
    （或从代码）from app.benchmarks.accuracy.lnn_accuracy_bench import run_benchmark
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

#: 材料编码表复用导出器契约（单一来源）
from app.training.exporters import MATERIAL_ENCODING  # noqa: E402
from app.training.weight_bridge import regression_metrics  # noqa: E402

#: 默认扫描网格（与训练网格错开一半取值，检验插值而非背题）
DEFAULT_GRID = {
    "rpm": [2000.0, 3500.0, 5000.0, 7000.0],
    "feed": [200.0, 400.0, 650.0, 1000.0],
    "depth": [0.5, 1.2, 2.0],
}


def _default_model_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "models" / "lnn"


#: 当前有解析解对照配置的模型（wear 等实测回归任务无解析解参照，
#: 其精度以训练 manifest 的 held-out 指标为准，不进本基准）
_BENCHABLE_MODELS = {"cutting_force"}


def run_benchmark(
    *,
    model_name: str = "cutting_force",
    material: str = "45steel",
    tool: str = "endmill_d10",
    grid: dict[str, list[float]] | None = None,
    model_dir: str | Path | None = None,
) -> dict[str, Any]:
    """运行「LNN vs Kienzle 解析解」对照基准。

    Returns:
        结果字典：status=trained 时含 metrics/samples；
        status=skipped 时说明权重缺失（不视为失败——CI 无权重环境应跳过）。
    """
    from app.ai.lnn.inference.registry import LNNModelRegistry
    from app.simulation.cutting_force.predictor import predict_cutting_force

    grid = grid or DEFAULT_GRID
    model_dir = Path(model_dir) if model_dir else _default_model_dir()

    if model_name not in _BENCHABLE_MODELS:
        return {
            "status": "skipped",
            "reason": f"模型 '{model_name}' 无解析解对照配置（本基准仅覆盖 {sorted(_BENCHABLE_MODELS)}）",
            "model_name": model_name,
        }

    material_encoded = MATERIAL_ENCODING.get(material)
    if material_encoded is None:
        raise ValueError(f"材料未登记编码: {material}（MATERIAL_ENCODING）")

    registry = LNNModelRegistry(model_dir=str(model_dir))
    verdict = registry.validate_model(model_name)
    if verdict.get("weights_source") != "trained":
        return {
            "status": "skipped",
            "reason": f"模型 '{model_name}' 无已训练权重（weights_source={verdict.get('weights_source')}）",
            "model_name": model_name,
        }

    model = registry.load_model(model_name)

    xs: list[list[float]] = []
    refs: list[float] = []
    for rpm in grid["rpm"]:
        for feed in grid["feed"]:
            for depth in grid["depth"]:
                ref = predict_cutting_force(
                    material, tool, {"speed": rpm, "feed": feed, "depth": depth}, use_pinn=False
                )
                if str(ref.get("method", "")) != "kienzle":
                    # 基线口径固定为 Kienzle：PINN 混入会让对照失去解析解参照
                    continue
                refs.append(math.sqrt(ref["Fx"] ** 2 + ref["Fy"] ** 2 + ref["Fz"] ** 2))
                xs.append([float(rpm), float(feed), float(depth), float(material_encoded)])

    if not xs:
        return {"status": "skipped", "reason": "Kienzle 参照无有效输出", "model_name": model_name}

    preds = np.asarray(model.predict(np.asarray(xs, dtype=np.float64)), dtype=np.float64).reshape(-1)
    refs_arr = np.asarray(refs, dtype=np.float64)
    metrics = regression_metrics(refs_arr, preds)

    return {
        "status": "trained",
        "model_name": model_name,
        "reference": "kienzle_analytic",
        "material": material,
        "sample_count": len(xs),
        "grid": grid,
        "metrics": metrics,
        "note": "LNN 当前为解析解响应面（仿真合成数据训练）：度量链路保真度而非精度优势",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_benchmark(), ensure_ascii=False, indent=2))
