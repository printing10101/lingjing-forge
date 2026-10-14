"""C2 cutting_force_v2 数据集构建脚本的单测（I2 §2.2 / §4）。

覆盖：窗口切分计数与取值、NaN 处置计数、工况表覆盖校验（缺 tag 报错不静默）、
双口径切分（随机 vs 分组）的确定性与防泄漏、物理合理性抽查辅助函数。

导入方式：脚本位于 scripts/（非 app 包），用 importlib 按路径加载。
运行：系统 Python 3.14，`unset PYTHONPATH` 后 `python -m pytest <本文件>`。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "engineering" / "python" / "scripts" / "build_cutting_force_v2_dataset.py"
)
_spec = importlib.util.spec_from_file_location("build_cutting_force_v2_dataset", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _make_merged(tag: str = "W1", n: int = 120, dataset: str = "nuaa") -> pd.DataFrame:
    """构造合并后的最小数据：工况恒定 + force_z 线性可辨。"""
    rng = np.random.default_rng(7)
    return pd.DataFrame(
        {
            "timestamp": np.arange(n, dtype=float) / 2.0,
            "force_z": 100.0 + 0.1 * np.arange(n) + rng.normal(0, 0.01, n),
            "experiment_tag": tag,
            "dataset_tag": dataset,
            "spindle_rpm": 1750.0,
            "feed_per_tooth_mm": 0.045,
            "axial_doc_mm": 2.5,
            "material_encoded": 11,
        }
    )


def _make_lookup(tags: list[str]) -> pd.DataFrame:
    rows = [
        {
            "experiment_tag": t,
            "spindle_rpm": 1750.0,
            "feed_per_tooth_mm": 0.045,
            "axial_doc_mm": 2.5,
            "material_encoded": 11,
        }
        for t in tags
    ]
    return pd.DataFrame(rows)


class TestWindowing:
    def test_window_count_and_values(self) -> None:
        df = _make_merged(n=120)
        samples, stats = mod.build_windows(df, window=50, step=25)
        # range(0, 120-50+1, 25) -> starts 0,25,50 => 3 windows
        assert stats["W1"] == {"n_raw": 120, "rows_dropped_nan": 0, "n_windows": 3}
        assert len(samples) == 3
        first = samples.iloc[0]
        assert first["force_z_window_mean"] == pytest.approx(df["force_z"].iloc[:50].mean(), abs=1e-9)
        assert first["t_start_s"] == pytest.approx(0.0, abs=1e-9)
        assert first["t_end_s"] == pytest.approx(49 / 2.0, abs=1e-9)
        # 窗口不重叠时 y 应单调递增（源信号线性增长）
        assert samples["force_z_window_mean"].is_monotonic_increasing

    def test_tail_partial_window_discarded_deterministically(self) -> None:
        df = _make_merged(n=101)
        _, stats = mod.build_windows(df, window=50, step=25)
        # range(0, 101-50+1=52, 25) -> 0,25,50; start=50 需 [50:100] 长度 50<=51 有效
        assert stats["W1"]["n_windows"] == 3

    def test_nan_counted_and_excluded(self) -> None:
        df = _make_merged(n=120)
        df.loc[[3, 10, 60, 90, 119], "force_z"] = np.nan
        samples, stats = mod.build_windows(df, window=50, step=25)
        assert stats["W1"]["rows_dropped_nan"] == 5
        assert stats["W1"]["n_raw"] == 120
        # 115 有效行：range(0, 66, 25) -> 3 windows，且无 NaN 进入均值
        assert stats["W1"]["n_windows"] == 3
        assert samples["force_z_window_mean"].notna().all()

    def test_invalid_window_step_raises(self) -> None:
        with pytest.raises(ValueError, match="window/step"):
            mod.build_windows(_make_merged(), window=0, step=25)


class TestLookupCoverage:
    def test_missing_tag_raises(self) -> None:
        merged = pd.concat([_make_merged("W1"), _make_merged("W9")], ignore_index=True)
        lookup = _make_lookup(["W1"])  # 缺 W9
        with pytest.raises(ValueError, match="W9"):
            mod.load_and_merge(merged, lookup)  # type: ignore[arg-type]

    def test_unused_lookup_tag_raises(self) -> None:
        merged = _make_merged("W1")
        lookup = _make_lookup(["W1", "W2"])  # W2 不在数据里
        with pytest.raises(ValueError, match="W2"):
            mod.load_and_merge(merged, lookup)  # type: ignore[arg-type]

    def test_duplicate_lookup_tag_raises(self) -> None:
        lookup = pd.concat([_make_lookup(["W1"]), _make_lookup(["W1"])], ignore_index=True)
        with pytest.raises(ValueError, match="重复"):
            mod.load_and_merge(_make_merged(), lookup)  # type: ignore[arg-type]


class TestSplits:
    def _samples_multi_tag(self, windows_per_tag: int = 10) -> pd.DataFrame:
        frames = []
        for tag, feed in [("W1", 0.045), ("W2", 0.05), ("W3", 0.055), ("W4", 0.045), ("W5", 0.05)]:
            frames.append(
                pd.DataFrame(
                    {
                        "spindle_rpm": [1750.0] * windows_per_tag,
                        "feed_per_tooth_mm": [feed] * windows_per_tag,
                        "axial_doc_mm": [2.5] * windows_per_tag,
                        "material_encoded": [11] * windows_per_tag,
                        "force_z_window_mean": np.linspace(100, 110, windows_per_tag) + feed * 100,
                        "force_z_window_rms": [1.0] * windows_per_tag,
                        "t_start_s": np.arange(windows_per_tag, dtype=float),
                        "t_end_s": np.arange(windows_per_tag, dtype=float) + 25.0,
                        "experiment_tag": tag,
                        "dataset_tag": "nuaa",
                    }
                )
            )
        return pd.concat(frames, ignore_index=True)

    def test_deterministic_same_seed(self) -> None:
        s = self._samples_multi_tag()
        a = mod.make_splits(s, seed=42)
        b = mod.make_splits(s, seed=42)
        assert a == b

    def test_group_split_no_tag_leakage(self) -> None:
        """防泄漏核心断言：口径 B 下同一 experiment_tag 不得跨 train/test。"""
        s = self._samples_multi_tag()
        labels = mod.make_splits(s, seed=42)["group"]
        tag_split = pd.DataFrame({"tag": s["experiment_tag"], "split": labels}).drop_duplicates()
        counts = tag_split.groupby("tag")["split"].nunique()
        assert (counts == 1).all(), "同一 experiment_tag 出现在多个切分中——分组泄漏"

    def test_split_sizes_approximate_target(self) -> None:
        s = self._samples_multi_tag(windows_per_tag=10)
        n = len(s)
        for mode in ("random", "group"):
            labels = mod.make_splits(s, seed=42)[mode]
            sizes = pd.Series(labels).value_counts()
            # 允许组粒度/取整偏差：train 70%±15pct，val+test 其余
            assert sizes.get("train", 0) >= 0.55 * n
            assert sizes.get("val", 0) >= 0.05 * n or mode == "group"
            assert sizes.get("test", 0) >= 0.05 * n


class TestSanity:
    def test_monotonic_force_vs_feed_positive(self) -> None:
        # feed 越大 force 越大（物理预期）-> 正相关
        frames = [
            pd.DataFrame(
                {
                    "spindle_rpm": [1750.0] * 5,
                    "feed_per_tooth_mm": [fz] * 5,
                    "axial_doc_mm": [2.5] * 5,
                    "material_encoded": [11] * 5,
                    "force_z_window_mean": np.linspace(100, 102, 5) * (1 + fz * 10),
                    "force_z_window_rms": [1.0] * 5,
                    "t_start_s": np.arange(5, dtype=float),
                    "t_end_s": np.arange(5, dtype=float) + 25.0,
                    "experiment_tag": f"W{i}",
                    "dataset_tag": "nuaa",
                }
            )
            for i, fz in enumerate([0.045, 0.05, 0.055])
        ]
        report = mod.sanity_monotonicity(pd.concat(frames, ignore_index=True))
        assert report["nuaa"]["checked"] is True
        assert report["nuaa"]["pearson_corr_force_vs_feed"] > 0.99

    def test_single_condition_skipped(self) -> None:
        df = _make_merged(tag="c1", dataset="phm2010", n=60)
        samples, _ = mod.build_windows(df, window=50, step=25)
        report = mod.sanity_monotonicity(samples)
        assert report["phm2010"] == {"checked": False, "reason": "single_condition"}


def test_audit_cols_never_in_features() -> None:
    assert not (set(mod.FEATURES) & set(mod.AUDIT_COLS)), "审计列不得进入特征"
