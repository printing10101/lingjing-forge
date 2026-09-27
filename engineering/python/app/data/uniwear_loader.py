import logging
from typing import Any

from enum import Enum
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class UniwearDataset(Enum):
    NUAA = "nuaa"
    PHM2010 = "phm2010"
    UNIWEAR = "uniwear"


NUAA_SIGNAL_COLUMNS = [
    "axial_force",
    "bending_moment_x",
    "bending_moment_y",
    "torsion",
    "vibration1",
    "vibration2",
    "spindle_power",
    "spindle_current",
    "vibration_x",
    "vibration_y",
    "force_z",
]

NUAA_META_COLUMNS = [
    "feed_per_tooth",
    "spindle_speed",
    "axial_cutting_depth",
]

NUAA_WEAR_COLUMNS = [
    "wear_blade_1",
    "wear_blade_2",
    "wear_blade_3",
    "wear_blade_4",
]

PHM2010_SIGNAL_COLUMNS = [
    "force_x",
    "force_y",
    "force_z",
    "vibration_x",
    "vibration_y",
    "vibration_z",
    "acoustic_emission_rms",
]

UNIWEAR_SIGNAL_COLUMNS = [
    "force_x",
    "force_y",
    "force_z",
    "vibration_x",
    "vibration_y",
    "vibration_z",
]

NUAA_MATERIAL = "TC4"
NUAA_MATERIAL_FULL = "Titanium TC4 (Ti-6Al-4V)"
PHM2010_MATERIAL = "HRC52"
PHM2010_MATERIAL_FULL = "Stainless Steel HRC52"

NUAA_EXPERIMENTS = [f"W{i}" for i in range(1, 10)]
PHM2010_EXPERIMENTS = ["c1", "c4", "c6"]


class UniwearDataLoader:
    """Uniwear 多材料刀具磨损数据集加载器

    支持三种数据格式：
    - NUAA：高分辨率正交切削束数据（钛合金 TC4）
    - PHM2010：PHM2010 竞赛束数据（不锈钢 HRC52）
    - Uniwear：统一格式数据集
    """

    def __init__(self, data_dir: str = "python/data/uniwear"):
        self.data_dir = Path(data_dir).resolve()
        self._cache: dict[str, pd.DataFrame] = {}

    def _resolve_path(self, dataset: UniwearDataset) -> Path:
        mapping = {
            UniwearDataset.NUAA: "nuaa_orthogonal_bundle_high_resolution.csv",
            UniwearDataset.PHM2010: "phm2010_bundle_high_resolution.csv",
            UniwearDataset.UNIWEAR: "uniwear.csv",
        }
        return self.data_dir / mapping[dataset]

    def load_dataset(self, dataset: UniwearDataset, use_cache: bool = True) -> pd.DataFrame:
        cache_key = dataset.value
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key].copy()

        file_path = self._resolve_path(dataset)
        if not file_path.exists():
            raise FileNotFoundError(
                f"UniWear 数据集加载失败：找不到数据集文件 '{file_path}'。可能原因：1) 文件路径配置错误；2) 数据集文件未下载或已删除。请检查配置文件中的数据集路径，或运行数据下载脚本获取数据集文件。"
            )

        df = pd.read_csv(file_path, index_col=0)
        self._cache[cache_key] = df
        logger.info(
            "Loaded %s dataset: %d rows, %d columns",
            dataset.value,
            len(df),
            len(df.columns),
        )
        return df.copy()

    def get_signal_data(
        self,
        dataset: UniwearDataset,
        columns: list[str] | None = None,
        experiment_tag: str | None = None,
    ) -> pd.DataFrame:
        df = self.load_dataset(dataset)

        if experiment_tag and "experiment_tag" in df.columns:
            df = df[df["experiment_tag"] == experiment_tag]

        if columns is None:
            if dataset == UniwearDataset.NUAA:
                columns = NUAA_SIGNAL_COLUMNS
            elif dataset == UniwearDataset.PHM2010:
                columns = PHM2010_SIGNAL_COLUMNS
            elif dataset == UniwearDataset.UNIWEAR:
                columns = UNIWEAR_SIGNAL_COLUMNS

        available = [c for c in columns if c in df.columns]
        missing = set(columns) - set(available)
        if missing:
            logger.warning("Columns not found in %s dataset: %s", dataset.value, missing)

        return df[available] if available else df

    def get_wear_data(
        self,
        dataset: UniwearDataset,
        experiment_tag: str | None = None,
    ) -> pd.DataFrame:
        df = self.load_dataset(dataset)

        if experiment_tag and "experiment_tag" in df.columns:
            df = df[df["experiment_tag"] == experiment_tag]

        if "tool_wear" in df.columns:
            wear_cols = ["tool_wear", "timestamp"]
        elif "wear_blade_1" in df.columns:
            wear_cols = [
                "wear_blade_1",
                "wear_blade_2",
                "wear_blade_3",
                "wear_blade_4",
                "timestamp",
            ]
        else:
            raise ValueError(
                f"UniWear 数据集解析失败：在 '{dataset.value}' 数据集中未找到刀具磨损（wear）相关列。可能原因：数据集格式不符合预期或列名已变更。请检查数据集文件结构，或参考文档确认预期的列名格式。"
            )

        available = [c for c in wear_cols if c in df.columns]
        result = df[available].copy()

        if "tool_wear" not in result.columns and "wear_blade_1" in result.columns:
            wear_cols_present = [
                c
                for c in [
                    "wear_blade_1",
                    "wear_blade_2",
                    "wear_blade_3",
                    "wear_blade_4",
                ]
                if c in result.columns
            ]
            if wear_cols_present:
                result["tool_wear"] = result[wear_cols_present].mean(axis=1)

        return result

    def get_experiment_tags(self, dataset: UniwearDataset) -> list[str]:
        df = self.load_dataset(dataset)
        if "experiment_tag" not in df.columns:
            if dataset == UniwearDataset.UNIWEAR:
                df = self.load_dataset(dataset)
                if "experiment_tag" in df.columns:
                    return sorted(df["experiment_tag"].dropna().unique().tolist())
            return []
        return sorted(df["experiment_tag"].dropna().unique().tolist())

    def get_dataset_summary(self) -> dict:
        summary: dict[str, Any] = {
            "datasets": {},
            "total_experiments": 0,
            "total_samples": 0,
        }

        for ds in UniwearDataset:
            try:
                df = self.load_dataset(ds)
                experiments = self.get_experiment_tags(ds) if "experiment_tag" in df.columns else []

                ds_summary = {
                    "file": self._resolve_path(ds).name,
                    "rows": len(df),
                    "columns": len(df.columns),
                    "column_names": list(df.columns),
                    "experiment_count": len(experiments),
                    "experiments": experiments,
                }

                if ds == UniwearDataset.NUAA:
                    ds_summary["material"] = NUAA_MATERIAL
                    ds_summary["material_full"] = NUAA_MATERIAL_FULL
                    ds_summary["signal_types"] = "force/vibration/power"
                elif ds == UniwearDataset.PHM2010:
                    ds_summary["material"] = PHM2010_MATERIAL
                    ds_summary["material_full"] = PHM2010_MATERIAL_FULL
                    ds_summary["signal_types"] = "force/vibration/acoustic_emission"
                elif ds == UniwearDataset.UNIWEAR:
                    ds_summary["material"] = f"{NUAA_MATERIAL}+{PHM2010_MATERIAL}"
                    ds_summary["material_full"] = f"{NUAA_MATERIAL_FULL} / {PHM2010_MATERIAL_FULL}"
                    ds_summary["signal_types"] = "unified force/vibration"

                summary["datasets"][ds.value] = ds_summary
                summary["total_experiments"] += ds_summary["experiment_count"]
                summary["total_samples"] += ds_summary["rows"]
            except (OSError, ValueError, TypeError, KeyError) as e:
                logger.warning("Failed to load %s: %s", ds.value, e)
                from app.core.safe_errors import safe_error_message

                safe = safe_error_message(
                    e,
                    context="uniwear_loader.summary",
                    fallback="数据集加载失败",
                )
                summary["datasets"][ds.value] = {
                    "error": safe["message"],
                    "error_id": safe["error_id"],
                }

        return summary

    def extract_signal_features(
        self,
        dataset: UniwearDataset,
        experiment_tag: str | None = None,
    ) -> dict[str, np.ndarray]:
        df = self.get_signal_data(dataset, experiment_tag=experiment_tag)

        features: dict[str, np.ndarray] = {}
        for col in df.columns:
            if col == "timestamp":
                continue
            values = df[col].values.astype(np.float64)
            if np.all(np.isfinite(values)):
                features[col] = values

        return features

    def compute_statistics(
        self,
        dataset: UniwearDataset,
        experiment_tag: str | None = None,
    ) -> dict:
        df = self.get_signal_data(dataset, experiment_tag=experiment_tag)
        wear_df = self.get_wear_data(dataset, experiment_tag=experiment_tag)

        stats: dict = {
            "dataset": dataset.value,
            "experiment": experiment_tag or "ALL",
            "signal_stats": {},
            "wear_stats": {},
        }

        for col in df.columns:
            if col == "timestamp":
                continue
            values = df[col].values.astype(np.float64)
            finite = values[np.isfinite(values)]
            if len(finite) == 0:
                continue
            stats["signal_stats"][col] = {
                "mean": round(float(np.mean(finite)), 6),
                "std": round(float(np.std(finite)), 6),
                "min": round(float(np.min(finite)), 6),
                "max": round(float(np.max(finite)), 6),
                "rms": round(float(np.sqrt(np.mean(np.square(finite)))), 6),
            }

        if "tool_wear" in wear_df.columns:
            wear_values = wear_df["tool_wear"].values.astype(np.float64)
            finite_w = wear_values[np.isfinite(wear_values)]
            if len(finite_w) > 0:
                stats["wear_stats"] = {
                    "initial_wear": round(float(finite_w[0]), 6),
                    "final_wear": round(float(finite_w[-1]), 6),
                    "max_wear": round(float(np.max(finite_w)), 6),
                    "mean_wear_rate": round(float((finite_w[-1] - finite_w[0]) / max(len(finite_w), 1)), 8),
                    "total_wear_increment": round(float(finite_w[-1] - finite_w[0]), 6),
                    "sample_count": len(finite_w),
                }

        return stats

    def get_wear_curve(
        self,
        dataset: UniwearDataset,
        experiment_tag: str | None = None,
    ) -> pd.DataFrame:
        wear_df = self.get_wear_data(dataset, experiment_tag=experiment_tag)
        if "timestamp" not in wear_df.columns:
            wear_df["timestamp"] = np.arange(len(wear_df))
        return wear_df

    def compare_experiments(
        self,
        dataset: UniwearDataset,
        experiments: list[str] | None = None,
    ) -> dict:
        if experiments is None:
            experiments = self.get_experiment_tags(dataset)

        comparison: dict = {
            "dataset": dataset.value,
            "material": NUAA_MATERIAL if dataset == UniwearDataset.NUAA else PHM2010_MATERIAL,
            "experiments": {},
        }

        for exp in experiments:
            try:
                stats = self.compute_statistics(dataset, experiment_tag=exp)
                wear_df = self.get_wear_data(dataset, experiment_tag=exp)
                wear_rate = 0.0
                if "tool_wear" in wear_df.columns and len(wear_df) > 1:
                    wear_rate = float(
                        (wear_df["tool_wear"].iloc[-1] - wear_df["tool_wear"].iloc[0]) / max(len(wear_df), 2)
                    )
                comparison["experiments"][exp] = {
                    "wear_stats": stats.get("wear_stats", {}),
                    "wear_rate": round(wear_rate, 8),
                    "sample_count": len(wear_df),
                }
            except (OSError, ValueError, TypeError, KeyError) as e:
                from app.core.safe_errors import safe_error_message

                safe = safe_error_message(
                    e,
                    context="uniwear_loader.comparison",
                    fallback="对比实验加载失败",
                )
                comparison["experiments"][exp] = {
                    "error": safe["message"],
                    "error_id": safe["error_id"],
                }

        sorted_exps = sorted(
            comparison["experiments"].items(),
            key=lambda x: x[1].get("wear_rate", 0),
            reverse=True,
        )
        comparison["ranked_by_wear_rate"] = [exp for exp, _ in sorted_exps]

        return comparison

    def get_material_comparison(self) -> dict:
        nuaa_comparison = self.compare_experiments(UniwearDataset.NUAA)
        phm2010_comparison = self.compare_experiments(UniwearDataset.PHM2010)

        total_nuaa_samples = sum(e.get("sample_count", 0) for e in nuaa_comparison["experiments"].values())
        total_phm2010_samples = sum(e.get("sample_count", 0) for e in phm2010_comparison["experiments"].values())

        return {
            "materials": {
                NUAA_MATERIAL: {
                    "full_name": NUAA_MATERIAL_FULL,
                    "dataset": "nuaa",
                    "experiment_count": len(nuaa_comparison["experiments"]),
                    "total_samples": total_nuaa_samples,
                    "experiments": nuaa_comparison["experiments"],
                    "ranked_by_wear_rate": nuaa_comparison["ranked_by_wear_rate"],
                },
                PHM2010_MATERIAL: {
                    "full_name": PHM2010_MATERIAL_FULL,
                    "dataset": "phm2010",
                    "experiment_count": len(phm2010_comparison["experiments"]),
                    "total_samples": total_phm2010_samples,
                    "experiments": phm2010_comparison["experiments"],
                    "ranked_by_wear_rate": phm2010_comparison["ranked_by_wear_rate"],
                },
            },
        }

    def clear_cache(self):
        self._cache.clear()
