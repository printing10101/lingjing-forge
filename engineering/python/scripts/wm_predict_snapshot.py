"""世界模型预测快照脚本（T0-2 基线 / T1-3 通电验证）.

用途
----
用固定输入跑一次世界模型预测，把完整响应落成 JSON，用于：

1. **T0-2 基线**：训练前跑一次，记录 ``weights_source=random_init`` 的
   随机权重输出，作为「训练是否真的生效」的对照组；
2. **T1-3 通电验证**：训练后带 ``--weights`` 再跑一次，确认
   ``weights_source=checkpoint`` 且预测值与基线不同。

不依赖 FastAPI / 数据库 / HTTP：直接走 ``TrajectoryPredictor``，
只要求 numpy + torch，适合在干净的训练环境里做快速验收。

用法（工作目录 ``engineering/python``）::

    # 基线（随机权重）
    python scripts/wm_predict_snapshot.py --out reports/wm_baseline_20261021.json

    # 训练权重
    python scripts/wm_predict_snapshot.py \
        --weights D:/灵境制造/data/models/world_model/1.0.0.pt \
        --out reports/wm_trained_20261021.json

退出码
------
0 — 预测成功且结果已写入
1 — torch 不可用（无法走融合路径）
2 — 预测异常
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 以 `python scripts/xxx.py` 运行时，sys.path[0] 是 scripts/，
# 需显式把 engineering/python 加入 path 才能 import app.*
_PYTHON_ROOT = Path(__file__).resolve().parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

import numpy as np  # noqa: E402

from app.plugins.world_model.net import WorldModelConfig  # noqa: E402
from app.plugins.world_model.predictor import TrajectoryPredictor  # noqa: E402
from app.plugins.world_model.unified_state import (  # noqa: E402
    DynamicsState,
    GeometryFeatures,
    UnifiedState,
)

MODEL_URI = "model://world_model/1.0.0"


def build_fixed_input() -> tuple[UnifiedState, np.ndarray]:
    """构造固定测试输入（与文档、报告中的基线口径一致）.

    Returns
    -------
    tuple[UnifiedState, np.ndarray]
        UnifiedState（几何 37 维 + 动力学 6 维）与候选动作 [horizon, 4]。
    """
    # 几何：120 x 80 x 40 mm 的块料，32 维特征向量用固定序列
    # （布局见 docs/reports/wm_state_schema_20261021.md）
    feature_vector = [((i * 37) % 100) / 100.0 for i in range(32)]
    geometry = GeometryFeatures(
        bbox_dimensions=(120.0, 80.0, 40.0),
        feature_vector=feature_vector,
        symmetry_score=0.60,
        complexity_score=0.45,
    )
    dynamics = DynamicsState(
        spindle_speed=8000.0,
        feed_rate=1200.0,
        depth_of_cut=0.5,
        tool_wear=0.12,
        vibration_rms=0.8,
        temperature=45.0,
    )
    unified_state = UnifiedState(geometry=geometry, dynamics=dynamics)

    # 动作 4 维：转速增量 / 进给增量 / 切深增量 / 冷却标志
    # 单步动作广播到 horizon 步（predictor 支持 1 维自动 tile）
    candidate_action = np.array([500.0, -100.0, 0.0, 0.0], dtype=np.float32)
    return unified_state, candidate_action


def main() -> int:
    parser = argparse.ArgumentParser(description="世界模型预测快照（基线 / 通电验证）")
    parser.add_argument("--weights", type=str, default=None, help="checkpoint 路径；不给则用随机初始化")
    parser.add_argument("--out", type=str, required=True, help="输出 JSON 路径")
    parser.add_argument("--horizon", type=int, default=10, help="预测步长（默认 10）")
    args = parser.parse_args()

    try:
        import torch
    except ImportError:
        print("[snapshot] torch 不可用，无法走融合路径", file=sys.stderr)
        return 1

    device_info = (
        f"{torch.__version__} cuda={torch.cuda.is_available()}"
        + (f" gpu={torch.cuda.get_device_name(0)}" if torch.cuda.is_available() else "")
    )
    print(f"[snapshot] torch: {device_info}", file=sys.stderr)

    unified_state, action = build_fixed_input()
    config = WorldModelConfig(use_fusion=True)
    predictor = TrajectoryPredictor(config=config, device="auto")
    predictor.load_model(model_uri=MODEL_URI, weights_path=args.weights)

    try:
        prediction = predictor.predict(
            unified_state=unified_state,
            candidate_action=action,
            horizon=args.horizon,
        )
    except Exception as exc:  # noqa: BLE001 - 脚本入口，需完整报错
        print(f"[snapshot] 预测失败: {exc}", file=sys.stderr)
        return 2

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "torch": device_info,
        "weights_arg": args.weights,
        "input": {
            "geometry": unified_state.geometry.to_dict()
            if hasattr(unified_state.geometry, "to_dict")
            else {
                "bbox_dimensions": list(unified_state.geometry.bbox_dimensions),
                "feature_vector": list(unified_state.geometry.feature_vector),
                "symmetry_score": unified_state.geometry.symmetry_score,
                "complexity_score": unified_state.geometry.complexity_score,
            },
            "dynamics": unified_state.dynamics.to_dict()
            if hasattr(unified_state.dynamics, "to_dict")
            else {
                "spindle_speed": unified_state.dynamics.spindle_speed,
                "feed_rate": unified_state.dynamics.feed_rate,
                "depth_of_cut": unified_state.dynamics.depth_of_cut,
                "tool_wear": unified_state.dynamics.tool_wear,
                "vibration_rms": unified_state.dynamics.vibration_rms,
                "temperature": unified_state.dynamics.temperature,
            },
            "candidate_action": action.tolist(),
            "horizon": args.horizon,
        },
        "prediction": prediction.to_dict(),
    }

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _PYTHON_ROOT.parent.parent / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    info = prediction.model_info
    print(
        "[snapshot] 完成 | "
        f"weights_source={info.get('weights_source')} | "
        f"backend={info.get('backend')} | "
        f"mode={info.get('mode', 'legacy')} | "
        f"trajectory_shape={np.asarray(prediction.predicted_trajectory).shape} | "
        f"out={out_path}",
        file=sys.stderr,
    )
    print(
        "[snapshot] metrics="
        + json.dumps(np.asarray(prediction.trajectory_metrics).tolist(), ensure_ascii=False),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
