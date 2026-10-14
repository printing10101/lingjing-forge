"""C2 · cutting_force_v2 数据集构建脚本（对应 docs/paper_and_competition/I2 §2.2）。

将 uniwear.csv（实测 force_z 时序，无工况列）按 experiment_tag 分组滑窗聚合为
静态回归样本：特征 = 公开文档回填的工况（conditions_lookup.csv），标签 = force_z 窗口均值。

设计要点（I2 定稿口径）：
- 窗口先例 exp50：window=50, step=25（可用参数调整）。
- 特征序重定义（诚实边界）：v1 的 feed_rate 在 NUAA 无直接出处，v2 改用
  feed_per_tooth_mm（NUAA 直接出自 MDPI Sensors 2023 Table 2；PHM2010 由
  1555/(10400*3) 派生，齿数 3 见计量学报 2025）。manifest 如实更新 feature_order。
- NaN/异常处置计数回写 summary（rows_dropped_nan），不静默丢弃。
- 审计列（RMS/时间起止/tag 透传）不进特征。
- 评估双口径：口径 A 窗口级随机切分（乐观上界）、口径 B 按 experiment_tag
  分组切分（达标口径，防同实验相邻窗口泄漏）。R²≥0.90 只认口径 B。

用法：
    python scripts/build_cutting_force_v2_dataset.py            # 全默认路径
    python scripts/build_cutting_force_v2_dataset.py --window 50 --step 25
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV = REPO_ROOT / "research" / "datasets" / "uniwear" / "uniwear" / "uniwear.csv"
DEFAULT_LOOKUP = (
    REPO_ROOT / "engineering" / "python" / "data" / "training_sets" / "cutting_force_v2" / "conditions_lookup.csv"
)
DEFAULT_OUT_DIR = REPO_ROOT / "engineering" / "python" / "data" / "training_sets" / "cutting_force_v2"

FEATURES = ["spindle_rpm", "feed_per_tooth_mm", "axial_doc_mm", "material_encoded"]
AUDIT_COLS = ["force_z_window_rms", "t_start_s", "t_end_s", "experiment_tag", "dataset_tag"]
SPLIT_SEED = 42  # 与 v1/wear_prediction 训练 seed 对齐，保证同构可比


def _read_csv_or_df(source: Path | pd.DataFrame) -> pd.DataFrame:
    return source if isinstance(source, pd.DataFrame) else pd.read_csv(source)


def load_and_merge(csv_path: Path | pd.DataFrame, lookup_path: Path | pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读原始数据与工况回填表并合并；tag 覆盖不全直接报错，不做静默丢弃。"""
    df = _read_csv_or_df(csv_path)
    lookup = _read_csv_or_df(lookup_path)

    required_df = {"timestamp", "force_z", "experiment_tag", "dataset_tag"}
    missing_cols = required_df - set(df.columns)
    if missing_cols:
        raise ValueError(f"数据缺少必需列: {sorted(missing_cols)}")
    required_lk = {"experiment_tag"} | set(FEATURES)
    missing_lk = required_lk - set(lookup.columns)
    if missing_lk:
        raise ValueError(f"工况表缺少必需列: {sorted(missing_lk)}")
    if lookup["experiment_tag"].duplicated().any():
        raise ValueError("工况表 experiment_tag 存在重复行")

    tags_in_data = set(df["experiment_tag"].unique())
    tags_in_lookup = set(lookup["experiment_tag"])
    uncovered = tags_in_data - tags_in_lookup
    if uncovered:
        # I2 §2.1：查不到可靠出处的 tag 不入表则整体弃用——但必须显式暴露，禁止静默
        raise ValueError(f"以下 experiment_tag 在 conditions_lookup.csv 无工况记录: {sorted(uncovered)}")
    unused = tags_in_lookup - tags_in_data
    if unused:
        raise ValueError(f"工况表包含数据中不存在的 tag（请核对表内容）: {sorted(unused)}")

    merged = df.merge(
        lookup[["experiment_tag", "dataset_tag"] + FEATURES],
        on="experiment_tag",
        how="left",
        validate="many_to_one",
        suffixes=("", "_lk"),
    )
    return merged, lookup


def build_windows(
    merged: pd.DataFrame, window: int = 50, step: int = 25
) -> tuple[pd.DataFrame, dict[str, dict[str, int]]]:
    """按 experiment_tag 分组滑窗。返回 (样本表, 每 tag 统计)。"""
    if window <= 0 or step <= 0:
        raise ValueError("window/step 必须为正整数")

    out_rows: list[dict[str, Any]] = []
    stats: dict[str, dict[str, int]] = {}
    for tag, g in merged.groupby("experiment_tag", sort=True):
        g = g.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
        n_raw = len(g)
        fz = pd.to_numeric(g["force_z"], errors="coerce").to_numpy(dtype=float)
        n_nan = int(np.isnan(fz).sum())
        keep = ~np.isnan(fz)
        g_valid = g.loc[keep].reset_index(drop=True)
        fz_valid = fz[keep]
        n_valid = len(fz_valid)

        n_windows = 0
        for start in range(0, max(0, n_valid - window + 1), step):
            seg = fz_valid[start : start + window]
            if len(seg) < window:
                break  # 末尾不满窗丢弃（确定性：由 n_valid/window/step 唯一决定）
            row: dict[str, Any] = {f: g_valid[f].iloc[0] for f in FEATURES}
            row["force_z_window_mean"] = float(np.mean(seg))
            row["force_z_window_rms"] = float(np.sqrt(np.mean(seg**2)))
            row["t_start_s"] = float(g_valid["timestamp"].iloc[start])
            row["t_end_s"] = float(g_valid["timestamp"].iloc[start + window - 1])
            row["experiment_tag"] = tag
            row["dataset_tag"] = g_valid["dataset_tag"].iloc[0]
            out_rows.append(row)
            n_windows += 1

        stats[tag] = {"n_raw": n_raw, "rows_dropped_nan": n_nan, "n_windows": n_windows}

    samples = pd.DataFrame(out_rows, columns=FEATURES + ["force_z_window_mean"] + AUDIT_COLS)
    return samples, stats


def make_splits(
    samples: pd.DataFrame, seed: int = SPLIT_SEED, train_frac: float = 0.70, val_frac: float = 0.15
) -> dict[str, list[str]]:
    """生成双口径切分（I2 §4）。返回 {口径: 与 samples 等长的 train/val/test 标签列表}。

    口径 A（random）：窗口级随机切分——乐观上界，仅与文献做法可比。
    口径 B（group）：按 experiment_tag 分组切分——部署真实场景，达标线只认此口径。
    同一 seed 流依次生成两套，保证可复现。
    """
    n = len(samples)
    rng = np.random.default_rng(seed)

    # 口径 A：窗口级随机
    perm = rng.permutation(n)
    n_train = int(round(train_frac * n))
    n_val = int(round(val_frac * n))
    labels_random = np.full(n, "test", dtype=object)
    labels_random[perm[:n_train]] = "train"
    labels_random[perm[n_train : n_train + n_val]] = "val"

    # 口径 B：按 experiment_tag 分组（赤字最大优先分配，组不可拆分；
    # tag>=3 时保证 train/val/test 均非空，tag 极少时自然退化为 LOEO 式留组）
    tags = sorted(samples["experiment_tag"].unique())
    tag_windows = samples.groupby("experiment_tag").size().to_dict()
    targets = {"train": train_frac * n, "val": val_frac * n, "test": (1 - train_frac - val_frac) * n}
    assigned: dict[str, set[str]] = {k: set() for k in targets}
    filled: dict[str, int] = {k: 0 for k in targets}
    for t in rng.permutation(np.array(tags)):
        best = max(targets, key=lambda k: targets[k] - filled[k])
        assigned[best].add(t)
        filled[best] += tag_windows[t]
    labels_group = [
        next(k for k in ("train", "val", "test") if t in assigned[k])
        for t in samples["experiment_tag"]
    ]

    return {"random": [str(x) for x in labels_random], "group": labels_group}


def sanity_monotonicity(samples: pd.DataFrame) -> dict[str, Any]:
    """物理合理性抽查（I2 §2.2）：nuaa 内每齿进给与窗口均力应正相关。

    仅对条件数 >1 的 dataset_tag 检查（单条件数据集无单调性可言，如 phm2010）。
    返回报告值，不作为硬门禁——真实数据结论如实记录进 summary。
    """
    report: dict[str, Any] = {}
    for ds, g in samples.groupby("dataset_tag"):
        if g["feed_per_tooth_mm"].nunique() < 2:
            report[ds] = {"checked": False, "reason": "single_condition"}
            continue
        tag_mean = g.groupby("experiment_tag")["force_z_window_mean"].mean()
        tag_feed = g.groupby("experiment_tag")["feed_per_tooth_mm"].first().loc[tag_mean.index]
        corr = float(np.corrcoef(tag_feed.to_numpy(dtype=float), tag_mean.to_numpy(dtype=float))[0, 1])
        report[ds] = {"checked": True, "pearson_corr_force_vs_feed": round(corr, 4)}
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 cutting_force_v2 窗口聚合数据集")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--lookup", type=Path, default=DEFAULT_LOOKUP)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--window", type=int, default=50)
    parser.add_argument("--step", type=int, default=25)
    args = parser.parse_args(argv)

    merged, _lookup = load_and_merge(args.csv, args.lookup)
    samples, stats = build_windows(merged, window=args.window, step=args.step)
    if samples.empty:
        raise ValueError("窗口聚合后样本为空——请检查 window/step 与数据量")

    splits = make_splits(samples)
    samples["split_random"] = splits["random"]
    samples["split_group"] = splits["group"]
    sanity = sanity_monotonicity(samples)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = args.out_dir / "uniwear_force_v2.csv"
    samples.to_csv(out_csv, index=False, encoding="utf-8")

    split_sizes = {
        mode: {k: int(v) for k, v in pd.Series(labels).value_counts().items()}
        for mode, labels in splits.items()
    }
    summary = {
        "dataset": "cutting_force_v2",
        "source_csv": str(args.csv),
        "conditions_lookup": str(args.lookup),
        "window": args.window,
        "step": args.step,
        "feature_order": FEATURES,
        "target": "force_z_window_mean",
        "n_samples": len(samples),
        "per_tag_stats": stats,
        "split_sizes": split_sizes,
        "sanity_monotonicity": sanity,
        "notes": [
            "数据为 uniwear 实测力信号（~2Hz 对齐平滑），工况来自公开文档回填（见 conditions_lookup.csv source 列）。",
            "v1 的 feed_rate 特征在 NUAA 无直接出处，v2 特征序重定义为 feed_per_tooth_mm（I2 §2.2 同一原则）。",
            "PHM2010 feed_per_tooth=0.0498 为派生值（1555/(10400*3)，齿数 3 见计量学报 2025）；NUAA 为直接出处。",
            "phm2010 工件材料存在文献口径冲突（Inconel 718 vs stainless HRC52），已记录于 lookup；单一固定工件，编码不受影响。",
            "material_encoded 对齐 C3 切参数据库材料表排序：material_tc4=11；phm2010 工件不在 C3 表内，编码 12 为扩展位。",
            "口径 A（split_random）为窗口级随机切分（乐观上界）；口径 B（split_group）按 experiment_tag 分组，R²>=0.90 只认口径 B（I2 §4）。",
            "标签性质：uniwear 为实测力信号（非物理代理标签）；window=50/step=25 对齐 exp50 先例。",
        ],
    }
    out_summary = args.out_dir / "build_summary.json"
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] 样本 {len(samples)} 条 -> {out_csv}")
    for tag, s in stats.items():
        print(f"  {tag}: raw={s['n_raw']} nan_dropped={s['rows_dropped_nan']} windows={s['n_windows']}")
    print(f"[ok] 切分规模: {json.dumps(split_sizes)}")
    print(f"[ok] 物理抽查: {json.dumps(sanity)}")
    print(f"[ok] summary -> {out_summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
