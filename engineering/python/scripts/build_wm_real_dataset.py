"""世界模型真实数据适配器（P0-2：PHM2010 微调集）.

数据来源
--------
``research/datasets/uniwear/uniwear/uniwear.csv`` 中 ``dataset_tag == "phm2010"``
的 8,515 行真实信号（PHM Society 2010 刀具磨损竞赛 c1/c4/c6 三组），
通道：``force_z`` / ``vibration_x`` / ``vibration_y`` / ``tool_wear``。

字段级来源（诚实标注，写入 meta）
--------------------------------
| 字段 | 来源 |
|---|---|
| dynamics[3] tool_wear | **真实标签** |
| dynamics[4] vibration_rms | **真实信号派生**：``sqrt(vx²+vy²)`` |
| dynamics[5] temperature | **缺失通道**，固定 25.0 °C（占位） |
| dynamics[0..2] rpm/feed/depth | **实验组常量**（PHM2010 公开切削条件：10400 rpm / 1555 mm/min / 径向 0.125 mm；轴向 c1=0.5, c4=0.75, c6=0.3 mm）——数据文件本身不含这些列 |
| state[1] wear | **真实标签** |
| state[3] vibration_rms | **真实信号派生** |
| state[5] cutting_force_norm | **真实信号**，``max(force_z,0)/100``（PHM2010 力通道为归一化单位，此换算为文档化约定） |
| state[0] chatter_p / state[2] Ra / state[6] mrr | 物理式派生（Tlusty 闭式 + 经验 Ra + MRR 公式），**非实测标签** |
| state[4] temperature / state[7] confidence | 常量（25°C / 1.0） |
| geometry 37 维 | 常量占位（PHM2010 工件单一，无几何变化）→ 该数据集只能校准动力学通道 |
| actions 4 维 | 全零（PHM2010 单一工况，无动作变化）→ 学不到动作响应 |

**结论性定位**：本数据集用于「真实信号 + 真实磨损标签」的动力学通道校准，
不是动作策略校准。meta 里 ``provenance = real_phm2010_wear_v1``。

划分
----
按实验组切分而非随机切分：train = c1 + c4，val = c6（跨工况泛化，避免重叠窗口泄漏）。
窗口不重叠，stride = T + H。

用法（工作目录 ``engineering/python``）::

    python scripts/build_wm_real_dataset.py --out-dir data/world_model
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent.parent
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

import numpy as np  # noqa: E402

from app.plugins.world_model.training.fusion_dataset import (  # noqa: E402
    FusionSampleError,
    validate_sample,
)

CSV_PATH = _REPO_ROOT / "research" / "datasets" / "uniwear" / "uniwear" / "uniwear.csv"

PROVENANCE = "real_phm2010_wear_v1"
GEO_DIM, DYN_DIM, ACTION_DIM, STATE_DIM = 37, 6, 4, 8

# PHM2010 公开切削条件（数据文件不含这些列，按竞赛说明取常量）
RPM = 10400.0
FEED = 1555.0
AE = 0.125  # 径向切深 mm
FLUTES = 3
AXIAL_DEPTH = {"c1": 0.5, "c4": 0.75, "c6": 0.3}

# 几何占位：PHM2010 工件单一，几何通道无信息量（文档化，不伪装成真实几何）
GEO_BBOX = (60.0, 60.0, 10.0)
GEO_VECTOR = [0.0] * 32 + [1.0, 0.0]  # 32 维特征全零 + symmetry=1.0 + complexity=0.0
GEO_FULL = list(GEO_BBOX) + GEO_VECTOR


def _tusty_a_lim(stiffness_n_per_m: float = 1.5e7, damping_ratio: float = 0.05, ks: float = 2000.0) -> float:
    """单自由度 Tlusty 闭式解（与合成数据集脚本保持一致的口径）."""
    k_mm = stiffness_n_per_m / 1000.0
    re_g_min = 1.0 / (4.0 * k_mm * damping_ratio * (1.0 + damping_ratio))
    return float(np.clip(1.0 / (2.0 * ks * re_g_min), 0.05, 20.0))


def load_rows() -> dict[str, list[dict]]:
    """读取 phm2010 行并按实验组分组（按 timestamp 排序）."""
    if not CSV_PATH.exists():
        raise FileNotFoundError(f"uniwear CSV 不存在: {CSV_PATH}")
    groups: dict[str, list[dict]] = {}
    with CSV_PATH.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("dataset_tag") != "phm2010":
                continue
            groups.setdefault(row["experiment_tag"], []).append(row)
    for tag, rows in groups.items():
        rows.sort(key=lambda r: float(r["timestamp"]))
        print(f"[real] {tag}: {len(rows)} 行", file=sys.stderr)
    return groups


def build_windows(rows: list[dict], experiment: str, history: int, horizon: int) -> tuple[list[dict], dict]:
    """把单组时序切成不重叠窗口 → FusionTrajectoryDataset 样本."""
    depth = AXIAL_DEPTH[experiment]
    a_lim = _tusty_a_lim()
    margin = (depth - a_lim) / max(0.15 * a_lim, 1e-6)
    chatter_p = float(1.0 / (1.0 + math.exp(-margin)))
    fz = FEED / (RPM * FLUTES)
    mrr_norm = (depth * AE * FEED / 1000.0) / 100.0

    wear = np.array([float(r["tool_wear"]) for r in rows], dtype=np.float32)
    force = np.array([float(r["force_z"]) for r in rows], dtype=np.float32)
    vib = np.sqrt(
        np.array([float(r["vibration_x"]) for r in rows], dtype=np.float32) ** 2
        + np.array([float(r["vibration_y"]) for r in rows], dtype=np.float32) ** 2
    ).astype(np.float32)
    temp = 25.0

    n = len(rows)
    span = history + horizon
    stride = span
    starts = list(range(0, max(n - span + 1, 0), stride))

    samples: list[dict] = []
    for s in starts:
        geo = np.tile(np.asarray(GEO_FULL, dtype=np.float32), (history, 1))
        dyn = np.zeros((history, DYN_DIM), dtype=np.float32)
        # state 序列（含未来 horizon 步），用于构造 target
        states = np.zeros((span, STATE_DIM), dtype=np.float32)
        for t in range(span):
            idx = s + t
            w = float(wear[idx])
            v = float(vib[idx])
            f_norm = float(max(force[idx], 0.0) / 100.0)
            ra = 0.5 + 60.0 * (fz**1.5) + 2.5 * chatter_p + 3.0 * w
            states[t] = np.array(
                [chatter_p, w, ra, v, temp / 100.0, f_norm, mrr_norm, 1.0],
                dtype=np.float32,
            )
            if t < history:
                dyn[t] = np.array([RPM, FEED, depth, w, v, temp], dtype=np.float32)
        actions = np.zeros((span, ACTION_DIM), dtype=np.float32)
        samples.append(
            {
                "geometry_seq": geo,
                "dynamics_seq": dyn,
                "actions": actions,
                "target_trajectory": states[history:span].copy(),
                # ADR-023：最后观测状态（状态反馈训练的 initial_state）
                "initial_state": states[history - 1].copy(),
                # 额外保留完整状态序列：训练不使用，仅用于评估持久性基线
                "state_seq": states.copy(),
            }
        )

    meta = {
        "experiment": experiment,
        "rows": n,
        "windows": len(samples),
        "stride": stride,
        "axial_depth_mm": depth,
        "chatter_p_physics": chatter_p,
        "feed_per_tooth_mm": fz,
        "mrr_norm": mrr_norm,
        "wear_range": [float(wear.min()), float(wear.max())],
        "vib_range": [float(vib.min()), float(vib.max())],
        "force_range": [float(force.min()), float(force.max())],
    }
    return samples, meta


def save_split(samples: list[dict], out_path: Path, history: int) -> None:
    geo = np.stack([s["geometry_seq"] for s in samples]).astype(np.float32)
    dyn = np.stack([s["dynamics_seq"] for s in samples]).astype(np.float32)
    act = np.stack([s["actions"] for s in samples]).astype(np.float32)
    tgt = np.stack([s["target_trajectory"] for s in samples]).astype(np.float32)
    state_hist = np.stack([s["state_seq"][:history] for s in samples]).astype(np.float32)
    init_state = np.stack([s["initial_state"] for s in samples]).astype(np.float32)
    for i in range(geo.shape[0]):
        validate_sample(
            geo[i], dyn[i], act[i], tgt[i],
            geo_input_dim=GEO_DIM, dynamics_input_dim=DYN_DIM,
            action_dim=ACTION_DIM, state_dim=STATE_DIM,
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        geometry=geo,
        dynamics=dyn,
        actions=act,
        target=tgt,
        state_history=state_hist,
        initial_state=init_state,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 PHM2010 真实数据微调集")
    parser.add_argument("--out-dir", type=str, default="data/world_model")
    parser.add_argument("--history", type=int, default=20)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--train-tags", type=str, default="c1,c4", help="训练实验组，逗号分隔")
    parser.add_argument("--val-tags", type=str, default="c6", help="验证实验组，逗号分隔")
    parser.add_argument("--out-prefix", type=str, default="phm2010", help="输出文件前缀（留空则不写文件）")
    args = parser.parse_args()

    try:
        groups = load_rows()
    except FileNotFoundError as exc:
        print(f"[real] {exc}", file=sys.stderr)
        return 1

    train_tags = [t.strip() for t in args.train_tags.split(",") if t.strip()]
    val_tags = [t.strip() for t in args.val_tags.split(",") if t.strip()]
    unknown = [t for t in train_tags + val_tags if t not in groups]
    if unknown:
        print(f"[real] 未知实验组: {unknown}（可用: {sorted(groups)}）", file=sys.stderr)
        return 1
    if set(train_tags) & set(val_tags):
        print("[real] 训练组与验证组不能重叠", file=sys.stderr)
        return 1

    train_samples: list[dict] = []
    metas = {}
    for tag in train_tags:
        samples, meta = build_windows(groups[tag], tag, args.history, args.horizon)
        metas[tag] = meta
        train_samples.extend(samples)
    val_samples: list[dict] = []
    for tag in val_tags:
        samples, meta = build_windows(groups[tag], tag, args.history, args.horizon)
        metas[tag] = meta
        val_samples.extend(samples)

    if not train_samples or not val_samples:
        print("[real] 窗口为空，检查 history/horizon 与数据长度", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = _REPO_ROOT / out_dir
    prefix = args.out_prefix.strip()
    train_path = out_dir / f"{prefix}_train.npz"
    val_path = out_dir / f"{prefix}_val.npz"
    save_split(train_samples, train_path, args.history)
    save_split(val_samples, val_path, args.history)

    meta_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": PROVENANCE,
        "source_csv": str(CSV_PATH),
        "note": "真实 PHM2010 信号 + 真实磨损标签；几何/动作/温度/部分状态为常量或物理派生（见脚本文档）",
        "history": args.history,
        "horizon": args.horizon,
        "split": {"train": train_tags, "val": val_tags},
        "train_samples": len(train_samples),
        "val_samples": len(val_samples),
        "per_experiment": metas,
    }
    meta_path = out_dir / f"{prefix}.meta.json"
    meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[real] 完成: train={len(train_samples)} ({'+'.join(train_tags)}) "
        f"val={len(val_samples)} ({'+'.join(val_tags)}) T={args.history} H={args.horizon} out={out_dir}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
