"""世界模型插件端到端验证（T1-3，走生产插件路径而非裸 predictor）.

验证链路：``WorldModelPlugin.execute(wm_predict_state)`` →
``_resolve_weights_path``（ModelRegistry → 约定式 URI 解析）→
``TrajectoryPredictor.load_model`` → 融合路径前向 → 输出 Artifact。

与 ``wm_predict_snapshot.py`` 的区别：后者直接调 predictor（验证模型层），
本脚本验证**插件层与权重解析约定**，即生产工作流实际走的路径。

用法（工作目录 ``engineering/python``）::

    python scripts/wm_plugin_e2e.py --models-dir D:/灵境制造/data/models \
        --out reports/wm_plugin_e2e_20261021.json

退出码
------
0 — 任务 COMPLETED 且 weights_source=checkpoint
1 — 权重未解析到 / 任务失败 / 权重来源不是 checkpoint
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="世界模型插件端到端验证")
    parser.add_argument(
        "--models-dir",
        type=str,
        default=None,
        help="checkpoint 根目录；省略则走默认解析链（env → cwd → 仓库根 data/models）",
    )
    parser.add_argument("--out", type=str, required=True, help="输出 JSON 路径")
    args = parser.parse_args()

    # 显式指定时写入 env（weights_resolver 在导入期读该变量）；
    # 省略时故意不设 env，用于验证"生产默认解析链"不依赖启动目录。
    if args.models_dir:
        os.environ["WORLD_MODEL_MODELS_DIR"] = str(Path(args.models_dir).resolve())
    sys.path.insert(0, str(_PYTHON_ROOT))

    from app.contracts.task import Artifact, TaskContext
    from app.plugins.world_model.plugin import WorldModelPlugin
    from app.plugins.world_model.unified_state import (
        DynamicsState,
        GeometryFeatures,
        UnifiedState,
    )

    model_uri = "model://world_model/1.0.0"
    plugin = WorldModelPlugin()
    resolved = plugin._resolve_weights_path(model_uri)
    print(f"[e2e] 权重解析: uri={model_uri} path={resolved}", file=sys.stderr)
    if not resolved:
        print("[e2e] 权重解析失败（约定式路径未命中）", file=sys.stderr)
        return 1

    unified = UnifiedState(
        geometry=GeometryFeatures(
            bbox_dimensions=(120.0, 80.0, 40.0),
            feature_vector=[((i * 37) % 100) / 100.0 for i in range(32)],
            symmetry_score=0.60,
            complexity_score=0.45,
        ),
        dynamics=DynamicsState(
            spindle_speed=8000.0,
            feed_rate=1200.0,
            depth_of_cut=0.5,
            tool_wear=0.12,
            vibration_rms=0.8,
            temperature=45.0,
        ),
    )

    horizon = 10
    action_row = [500.0, -100.0, 0.0, 0.0]
    ctx = TaskContext(
        job_id="wm_e2e_20261021",
        inputs={
            "current_state": Artifact(
                name="current_state",
                type="metrics",
                uri="metrics://wm_e2e/state",
                metadata={"unified_state": unified.to_dict()},
            ),
            "candidate_action": Artifact(
                name="candidate_action",
                type="metrics",
                uri="metrics://wm_e2e/action",
                metadata={"data": [action_row for _ in range(horizon)]},
            ),
        },
        config={"horizon": horizon, "model_uri": model_uri},
    )

    try:
        import asyncio

        # WorldModelPlugin.execute 为 async（TaskHandler 协议），脚本入口用 asyncio.run
        result = asyncio.run(plugin.execute(ctx))
    except Exception as exc:  # noqa: BLE001 - 脚本入口
        print(f"[e2e] 插件执行异常: {exc}", file=sys.stderr)
        return 1

    status = getattr(result, "status", None)
    status_str = getattr(status, "value", str(status))
    outputs = {
        key: getattr(artifact, "metadata", {})
        for key, artifact in (getattr(result, "outputs", {}) or {}).items()
    }
    print(f"[e2e] status={status_str}", file=sys.stderr)
    print(f"[e2e] outputs={json.dumps(outputs, ensure_ascii=False)[:600]}", file=sys.stderr)

    traj_meta = outputs.get("predicted_trajectory", {})
    weights_source = traj_meta.get("weights_source")
    if status_str != "completed":
        print(f"[e2e] 任务未完成: error={getattr(result, 'error', None)}", file=sys.stderr)
        return 1
    if weights_source != "checkpoint":
        print(f"[e2e] 权重来源不是 checkpoint: {weights_source}", file=sys.stderr)
        return 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_uri": model_uri,
        "resolved_weights_path": resolved,
        "status": status_str,
        "weights_source": weights_source,
        "input_mode": traj_meta.get("input_mode"),
        "trajectory_shape": traj_meta.get("shape"),
        "trajectory_preview": traj_meta.get("preview"),
        "metrics": outputs.get("trajectory_metrics", {}),
        "outputs": outputs,
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _PYTHON_ROOT.parent.parent / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[e2e] PASS 插件链路走通，weights_source=checkpoint → {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
