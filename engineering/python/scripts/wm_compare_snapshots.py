"""对比两份世界模型预测快照（T1-3 通电验证）.

判定规则
--------
- ``trained.weights_source == "checkpoint"`` —— 训练权重确实加载；
- ``baseline.weights_source == "random_init"`` —— 对照组确实是随机权重；
- 两条轨迹的平均绝对差 > ``--min-diff`` —— 训练确实改变了输出（不是同一模型）。

用法::

    python scripts/wm_compare_snapshots.py \
        --baseline reports/wm_baseline_20261021.json \
        --trained reports/wm_trained_20261021.json

退出码
------
0 — 三项判定全部通过
2 — 任一判定不通过（打印原因）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"快照不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _trajectory(snapshot: dict) -> list[list[float]]:
    return snapshot["prediction"]["predicted_trajectory"]


def main() -> int:
    parser = argparse.ArgumentParser(description="对比世界模型基线/训练快照")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--trained", required=True)
    parser.add_argument("--min-diff", type=float, default=1e-4, help="平均绝对差下限")
    args = parser.parse_args()

    baseline = _load(Path(args.baseline))
    trained = _load(Path(args.trained))

    b_src = baseline["prediction"]["model_info"].get("weights_source")
    t_src = trained["prediction"]["model_info"].get("weights_source")

    b_traj = _trajectory(baseline)
    t_traj = _trajectory(trained)
    if len(b_traj) != len(t_traj):
        print(f"[compare] 轨迹长度不一致: baseline={len(b_traj)} trained={len(t_traj)}", file=sys.stderr)
        return 2

    total = 0.0
    count = 0
    max_dim_diff = []
    for row_b, row_t in zip(b_traj, t_traj):
        if len(row_b) != len(row_t):
            print("[compare] 轨迹维度不一致", file=sys.stderr)
            return 2
        for i, (vb, vt) in enumerate(zip(row_b, row_t)):
            total += abs(vt - vb)
            count += 1
        max_dim_diff.append(max(abs(vt - vb) for vb, vt in zip(row_b, row_t)))
    mean_abs_diff = total / max(count, 1)

    checks = [
        ("baseline 使用随机权重", b_src == "random_init", f"实际={b_src}"),
        ("trained 加载了 checkpoint", t_src == "checkpoint", f"实际={t_src}"),
        ("输出确实改变", mean_abs_diff > args.min_diff, f"mean_abs_diff={mean_abs_diff:.6g}"),
    ]

    print("[compare] 基线 weights_source =", b_src, file=sys.stderr)
    print("[compare] 训练 weights_source =", t_src, file=sys.stderr)
    print(f"[compare] 平均绝对差 = {mean_abs_diff:.6g}", file=sys.stderr)
    print(f"[compare] 最大单步维度差 = {max(max_dim_diff):.6g}", file=sys.stderr)
    print(
        "[compare] baseline metrics =",
        json.dumps(baseline["prediction"]["trajectory_metrics"], ensure_ascii=False),
        file=sys.stderr,
    )
    print(
        "[compare] trained  metrics =",
        json.dumps(trained["prediction"]["trajectory_metrics"], ensure_ascii=False),
        file=sys.stderr,
    )

    failed = False
    for name, ok, detail in checks:
        print(f"[compare] {'PASS' if ok else 'FAIL'} {name} ({detail})", file=sys.stderr)
        failed = failed or (not ok)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
