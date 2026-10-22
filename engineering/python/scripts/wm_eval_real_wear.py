"""世界模型真实数据评估（P0-2）：逐维 MAE + 持久性基线对比.

为什么要有持久性基线
--------------------
真实数据评估不能只看 MAE 绝对值。这里同时给出「保持当前状态不变」的
朴素基线（persistence）：若模型打不过持久性基线，说明它没有提供预测增益，
必须如实报告，而不是拿一个好看的 MAE 交差。

用法::

    # 微调前（合成预训练权重）
    python scripts/wm_eval_real_wear.py --dataset data/world_model/phm2010_val.npz \
        --weights data/models/world_model/1.0.0.pt --out reports/wm_eval_real_before.json

    # 微调后
    python scripts/wm_eval_real_wear.py --dataset data/world_model/phm2010_val.npz \
        --weights data/models/world_model/1.1.0-real.pt --out reports/wm_eval_real_after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

import numpy as np  # noqa: E402

DIM_NAMES = [
    "chatter_p",
    "wear_mm",
    "Ra_um",
    "vib_rms",
    "temp/100",
    "force_norm",
    "mrr_norm",
    "confidence",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="世界模型真实数据评估")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--weights", default=None)
    parser.add_argument("--out", required=True)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--state-feedback",
        action="store_true",
        help="what-if：把最后观测状态直连为解码器初始状态（验证状态反馈能否修复磨损常数化）",
    )
    args = parser.parse_args()

    try:
        import torch

        from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    except ImportError as exc:
        print(f"[eval] torch 不可用: {exc}", file=sys.stderr)
        return 1

    ds_path = Path(args.dataset)
    if not ds_path.is_absolute():
        ds_path = _REPO_ROOT / ds_path
    if not ds_path.is_file():
        print(f"[eval] 数据集不存在: {ds_path}", file=sys.stderr)
        return 1

    data = np.load(ds_path)
    geo, dyn, act, tgt = data["geometry"], data["dynamics"], data["actions"], data["target"]
    state_hist = data["state_history"] if "state_history" in data.files else None
    if state_hist is None:
        print("[eval] 数据集缺 state_history，无法计算持久性基线", file=sys.stderr)
        return 1

    state_dim = int(tgt.shape[2])
    action_dim = int(act.shape[2])
    feature_dim = int(geo.shape[2]) - 3 - 1 - 1
    horizon = int(tgt.shape[1])

    # 先读 checkpoint（用于自动识别 Δ 预测模式），再构造模型
    payload = None
    state_dict = None
    if args.weights:
        wpath = Path(args.weights)
        if not wpath.is_absolute():
            wpath = _REPO_ROOT / wpath
        if not wpath.is_file():
            print(f"[eval] 权重不存在: {wpath}", file=sys.stderr)
            return 1
        payload = torch.load(wpath, map_location="cpu", weights_only=True)
        state_dict = payload.get("model_state_dict", payload) if isinstance(payload, dict) else payload

    delta_flag = False
    if state_dict is not None:
        cfg_meta = payload.get("model_config", {}) if isinstance(payload, dict) else {}
        delta_flag = bool(cfg_meta.get("delta_prediction")) or any(
            k.startswith("delta_head.") for k in state_dict
        )

    config = WorldModelConfig(
        state_dim=state_dim,
        action_dim=action_dim,
        use_fusion=True,
        feature_dim=feature_dim,
        delta_prediction=delta_flag,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = WorldModelNet(config)
    weights_source = "random_init"
    if state_dict is not None:
        model.load_state_dict(state_dict)
        weights_source = "checkpoint"
    model.to(device).eval()
    if delta_flag:
        print("[eval] 检测到 Δ 状态预测模式（delta_head 存在）", file=sys.stderr)

    preds = []
    with torch.inference_mode():
        for start in range(0, geo.shape[0], args.batch_size):
            sl = slice(start, min(start + args.batch_size, geo.shape[0]))
            out = model(
                states=None,
                actions=torch.from_numpy(act[sl]).to(device),
                horizon=horizon,
                unified_states=(
                    torch.from_numpy(geo[sl]).to(device),
                    torch.from_numpy(dyn[sl]).to(device),
                ),
                initial_state=(
                    torch.from_numpy(state_hist[sl, -1, :]).to(device)
                    if args.state_feedback
                    else None
                ),
            )
            preds.append(out["predicted_trajectory"].cpu().numpy())
    pred = np.concatenate(preds, axis=0)

    model_mae = np.abs(pred - tgt).mean(axis=(0, 1))
    persist_state = np.repeat(state_hist[:, -1:, :], horizon, axis=1)
    persist_mae = np.abs(persist_state - tgt).mean(axis=(0, 1))

    rows = []
    for i, name in enumerate(DIM_NAMES[:state_dim]):
        m = float(model_mae[i])
        p = float(persist_mae[i])
        gain = (p - m) / p * 100.0 if p > 1e-12 else 0.0
        rows.append(
            {
                "dim": i,
                "name": name,
                "model_mae": round(m, 6),
                "persistence_mae": round(p, 6),
                "gain_pct": round(gain, 2),
                "beats_persistence": bool(m < p),
            }
        )

    print(f"[eval] dataset={ds_path.name} n={geo.shape[0]} H={horizon} weights={weights_source}", file=sys.stderr)
    print(f"{'dim':<4}{'name':<12}{'model MAE':>12}{'persist MAE':>14}{'gain%':>9}", file=sys.stderr)
    for r in rows:
        print(
            f"{r['dim']:<4}{r['name']:<12}{r['model_mae']:>12.6f}{r['persistence_mae']:>14.6f}{r['gain_pct']:>9.2f}",
            file=sys.stderr,
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(ds_path),
        "n_samples": int(geo.shape[0]),
        "horizon": horizon,
        "weights": args.weights,
        "weights_source": weights_source,
        "dims": rows,
        "overall": {
            "model_mae": round(float(model_mae.mean()), 6),
            "persistence_mae": round(float(persist_mae.mean()), 6),
        },
    }
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = _REPO_ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[eval] 结果 → {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
