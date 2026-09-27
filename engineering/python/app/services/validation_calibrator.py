"""Validation rule calibrator using Bosch CNC real-world data."""

import json
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from app.data.bosch_cnc_loader import BoschCNCDataLoader

logger = logging.getLogger(__name__)


class ValidationCalibrator:
    """Calibrate validation rule thresholds using real industrial data."""

    def __init__(
        self,
        data_dir: str | None = None,
        rules_path: str | None = None,
        warning_k: float = 2.0,
        critical_k: float = 3.0,
    ):
        project_root = Path(__file__).parent.parent.parent
        if data_dir is None:
            data_dir = str(project_root / "data" / "datasets" / "bosch_cnc")
        if rules_path is None:
            rules_path = str(project_root / "app" / "core" / "validation_rules.json")
        self.loader = BoschCNCDataLoader(data_dir)
        self.rules_path = Path(rules_path)
        self.warning_k = warning_k
        self.critical_k = critical_k
        self._calibration_cache: dict = {}

    def calibrate_vibration_thresholds(
        self,
        process: str | None = None,
        machine: str | None = None,
    ) -> dict:
        """Calculate vibration threshold baselines from normal operation data."""
        cache_key = f"{process}_{machine}"
        if cache_key in self._calibration_cache:
            return self._calibration_cache[cache_key]

        try:
            samples = self.loader.load_dataset(
                machines=[machine] if machine else None,
                processes=[process] if process else None,
                labels=["good"],
            )
        except (FileNotFoundError, ValueError) as e:
            raise ValueError(f"校验数据加载失败：{e}") from e

        if not samples:
            raise ValueError(
                f"校验数据加载失败：未找到标记为 'good'（良品）的样本数据。"
                f"筛选条件: 工序={process}, 机床={machine}。"
                "可能原因：1) 数据集中不存在该工序/机床组合的良品样本；"
                "2) 数据标注有误。"
                "请检查数据筛选条件，或调用 GET /api/v1/data/samples?labels=good 查看可用样本。"
            )

        rms_x, rms_y, rms_z = [], [], []
        freq_x, freq_y, freq_z = [], [], []
        energy_x, energy_y, energy_z = [], [], []

        for sample in samples:
            features = self.loader.extract_features(sample)

            rms_x.append(features.get("time_x_rms", 0.0))
            rms_y.append(features.get("time_y_rms", 0.0))
            rms_z.append(features.get("time_z_rms", 0.0))

            freq_x.append(features.get("freq_x_dominant_freq", 0.0))
            freq_y.append(features.get("freq_y_dominant_freq", 0.0))
            freq_z.append(features.get("freq_z_dominant_freq", 0.0))

            energy_x.append(features.get("cross_x_energy_ratio", 0.0))
            energy_y.append(features.get("cross_y_energy_ratio", 0.0))
            energy_z.append(features.get("cross_z_energy_ratio", 0.0))

        n_samples = len(samples)

        result = {
            "process": process or "ALL",
            "machine": machine or "ALL",
            "vibration": {
                "x_axis": self._axis_thresholds(np.array(rms_x)),
                "y_axis": self._axis_thresholds(np.array(rms_y)),
                "z_axis": self._axis_thresholds(np.array(rms_z)),
            },
            "frequency": {
                "dominant_range": [
                    round(float(np.mean(freq_x + freq_y + freq_z)) * 0.8, 2),
                    round(float(np.mean(freq_x + freq_y + freq_z)) * 1.2, 2),
                ],
                "shift_warning_percent": 15.0,
                "shift_critical_percent": 25.0,
            },
            "energy_ratio": {
                "normal_distribution": {
                    "x": round(float(np.mean(energy_x)), 4),
                    "y": round(float(np.mean(energy_y)), 4),
                    "z": round(float(np.mean(energy_z)), 4),
                },
                "imbalance_warning_percent": 30.0,
            },
            "sample_count": n_samples,
            "confidence": self._calculate_confidence(n_samples),
        }

        if n_samples < 20:
            logger.warning(
                "Insufficient samples (%d < 20) for %s - thresholds may be unreliable",
                n_samples,
                cache_key,
            )

        self._calibration_cache[cache_key] = result
        return result

    def calibrate_all_processes(self) -> dict:
        """Generate calibrated thresholds for all processes."""
        summary = self.loader.get_dataset_summary()
        all_processes = summary.get("available_processes", [])
        summary.get("available_machines", [])

        calibrated: dict = {}
        for process in all_processes:
            try:
                result = self.calibrate_vibration_thresholds(process=process)
                calibrated[process] = result
                logger.info(
                    "Calibrated process %s with %d samples (confidence=%.2f)",
                    process,
                    result["sample_count"],
                    result["confidence"],
                )
            except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
                logger.warning("Failed to calibrate %s: %s", process, e)

        return calibrated

    def compare_with_current_rules(self) -> dict:
        """Compare calibrated results with current validation_rules.json."""
        calibrated = self._load_or_calibrate()
        if not calibrated:
            return {"error": "No calibrated data available"}

        current_rules = self._load_rules()
        theoretical = current_rules.get("theoretical_thresholds", {})
        bosch_section = current_rules.get("bosch_calibrated", {})
        existing_calibrated = bosch_section.get("process_thresholds", {})

        comparison: dict = {}
        for process, cal_data in calibrated.items():
            process_comparison: dict = {}

            vibration_limits = theoretical.get("vibration_rms_limits", {})
            current_warning = vibration_limits.get("warning_threshold_g", 0.30)
            current_critical = vibration_limits.get("critical_threshold_g", 0.45)

            for axis in ["x_axis", "y_axis", "z_axis"]:
                axis_data = cal_data["vibration"][axis]
                cal_warning = axis_data["warning_threshold"]
                cal_critical = axis_data["critical_threshold"]

                warning_dev = self._percent_deviation(current_warning, cal_warning)
                critical_dev = self._percent_deviation(current_critical, cal_critical)

                process_comparison[f"vibration_{axis}_warning"] = {
                    "current": current_warning,
                    "calibrated": round(cal_warning, 4),
                    "deviation": round(warning_dev, 2),
                }
                process_comparison[f"vibration_{axis}_critical"] = {
                    "current": current_critical,
                    "calibrated": round(cal_critical, 4),
                    "deviation": round(critical_dev, 2),
                }

            existing = existing_calibrated.get(process, {})
            if existing:
                process_comparison["_already_calibrated"] = True

            comparison[process] = process_comparison

        return comparison

    def generate_updated_rules(self) -> dict:
        """Generate complete calibrated validation rules in JSON format."""
        calibrated = self.calibrate_all_processes()

        rules = self._load_rules()
        rules["bosch_calibrated"]["last_calibrated"] = datetime.now().strftime("%Y-%m-%d")
        rules["bosch_calibrated"]["calibration_status"] = "pending"
        rules["bosch_calibrated"]["process_thresholds"] = {}

        for process, cal_data in calibrated.items():
            rules["bosch_calibrated"]["process_thresholds"][process] = {
                "vibration": cal_data["vibration"],
                "frequency": cal_data["frequency"],
                "energy_ratio": cal_data["energy_ratio"],
                "sample_count": cal_data["sample_count"],
                "confidence": cal_data["confidence"],
                "vibration_rms_warning_multiplier": round(
                    cal_data["vibration"]["x_axis"]["warning_threshold"]
                    / max(cal_data["vibration"]["x_axis"]["rms_normal_range"][0], 1e-10),
                    2,
                ),
                "vibration_rms_critical_multiplier": round(
                    cal_data["vibration"]["x_axis"]["critical_threshold"]
                    / max(cal_data["vibration"]["x_axis"]["rms_normal_range"][0], 1e-10),
                    2,
                ),
                "frequency_shift_warning_percent": cal_data["frequency"]["shift_warning_percent"],
                "frequency_shift_critical_percent": cal_data["frequency"]["shift_critical_percent"],
            }

        return rules

    def apply_calibration(self, confirmed: bool = True) -> dict:
        """Apply calibrated rules after user confirmation."""
        if not confirmed:
            return {"status": "cancelled", "message": "Calibration not confirmed"}

        updated_rules = self.generate_updated_rules()
        self._save_rules(updated_rules)
        logger.info(
            "Calibration applied: %d processes calibrated",
            len(updated_rules["bosch_calibrated"]["process_thresholds"]),
        )
        return {
            "status": "applied",
            "process_count": len(updated_rules["bosch_calibrated"]["process_thresholds"]),
            "last_calibrated": updated_rules["bosch_calibrated"]["last_calibrated"],
        }

    def _axis_thresholds(self, rms_values: np.ndarray) -> dict:
        """Calculate per-axis thresholds using mean + k*std."""
        mean_val = float(np.mean(rms_values))
        std_val = float(np.std(rms_values, ddof=1))

        normal_min = float(np.percentile(rms_values, 5))
        normal_max = float(np.percentile(rms_values, 95))

        return {
            "rms_normal_range": [round(normal_min, 4), round(normal_max, 4)],
            "warning_threshold": round(mean_val + self.warning_k * std_val, 4),
            "critical_threshold": round(mean_val + self.critical_k * std_val, 4),
            "mean": round(mean_val, 4),
            "std": round(std_val, 4),
        }

    def _calculate_confidence(self, n_samples: int) -> float:
        """Calculate confidence level based on sample count."""
        if n_samples < 5:
            return 0.5
        if n_samples < 20:
            return 0.7
        if n_samples < 50:
            return 0.85
        return 0.95

    def _load_rules(self) -> dict:
        # M17 修复：移除 TOCTOU 检查，直接 try/except 处理文件不存在和格式错误
        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("校准规则文件损坏，使用空规则: %s: %s", self.rules_path, e)
            return {}

    def _save_rules(self, rules: dict) -> None:
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(rules, f, indent=2, ensure_ascii=False)

    def _load_or_calibrate(self) -> dict:
        if self._calibration_cache:
            return self._calibration_cache
        try:
            return self.calibrate_all_processes()
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
            logger.error("Auto-calibration failed: %s", e)
            return {}

    def cross_source_calibration(
        self,
        uniwear_data_dir: str = "python/data/uniwear",
        bosch_data_dir: str = "python/data/datasets/bosch_cnc",
    ) -> dict:
        bosch_thresholds = {}
        try:
            bosch_thresholds = self.calibrate_all_processes()
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
            logger.warning("Bosch calibration failed: %s", e)

        uniwear_result = {}
        try:
            uniwear_result = self.calibrate_with_uniwear(
                uniwear_data_dir=uniwear_data_dir,
            )
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
            logger.warning("Uniwear calibration failed: %s", e)

        alignment: dict = {"bosch_uniwear_alignment": {}}

        if bosch_thresholds and uniwear_result:
            for ds_name, ds_data in uniwear_result.get("sources", {}).items():
                if "error" in ds_data:
                    continue

                signal_rms = ds_data.get("signal_rms", {})
                common_signals: dict = {}
                for u_signal, u_rms in signal_rms.items():
                    for _, bosch_data in bosch_thresholds.items():
                        bosch_vib = bosch_data.get("vibration", {})
                        axis_map = {
                            "vibration_x": "x_axis",
                            "vibration_y": "y_axis",
                            "vibration_z": "z_axis",
                            "force_x": "x_axis",
                            "force_y": "y_axis",
                            "force_z": "z_axis",
                        }
                        mapped_axis = axis_map.get(u_signal)
                        if mapped_axis and mapped_axis in bosch_vib:
                            bosch_warning = bosch_vib[mapped_axis].get("warning_threshold", 0)
                            bosch_critical = bosch_vib[mapped_axis].get("critical_threshold", 0)
                            common_signals[u_signal] = {
                                "uniwear_rms": round(u_rms, 6),
                                "bosch_warning": round(bosch_warning, 6),
                                "bosch_critical": round(bosch_critical, 6),
                            }

                alignment["bosch_uniwear_alignment"][ds_name] = {
                    "material": ds_data.get("material", "unknown"),
                    "common_signals": common_signals,
                    "sample_count": ds_data.get("sample_count", 0),
                }

        alignment["recommendation"] = (
            "Bosch and Uniwear thresholds show complementary validation ranges. "
            "Use Uniwear data for material-specific fine-tuning and Bosch data for "
            "process-level industrial validation."
        )

        return alignment

    def merge_calibration_rules(self) -> dict:
        try:
            bosch_calibrated = self._load_or_calibrate()
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
            logger.warning("加载 Bosch 校准规则失败: %s", e)
            bosch_calibrated = {}

        try:
            uniwear_calibrated = self.calibrate_with_uniwear()
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
            logger.warning("加载 Uniwear 校准规则失败: %s", e)
            uniwear_calibrated = {}

        merged_rules = self._load_rules()
        merged_rules["bosch_calibrated"] = merged_rules.get("bosch_calibrated", {})
        merged_rules["uniwear_calibrated"] = merged_rules.get("uniwear_calibrated", {})

        for process, cal_data in bosch_calibrated.items():
            merged_rules["bosch_calibrated"][process] = cal_data

        merged_rules["uniwear_calibrated"] = {
            "sources": uniwear_calibrated.get("sources", {}),
            "joint_thresholds": uniwear_calibrated.get("joint_thresholds", {}),
            "cross_validation": uniwear_calibrated.get("cross_validation", {}),
        }

        merged_rules["multi_source_calibration"] = {
            "last_calibrated": datetime.now().strftime("%Y-%m-%d"),
            "sources": ["bosch_cnc", "uniwear_nuaa", "uniwear_phm2010"],
            "compatibility_note": (
                "Bosch和Uniwear验证阈值互补使用。Bosch提供工业现场工艺级别参考，"
                "Uniwear提供材料级别精确控制。两者差异应在10-30%视为正常。"
            ),
        }

        return merged_rules

    def calibrate_with_uniwear(
        self,
        uniwear_data_dir: str = "python/data/uniwear",
        process: str | None = None,
    ) -> dict:
        from app.data.uniwear_loader import (
            UniwearDataLoader,
            UniwearDataset,
            NUAA_MATERIAL,
            PHM2010_MATERIAL,
        )

        uniwear_loader = UniwearDataLoader(data_dir=uniwear_data_dir)
        result: dict = {
            "sources": {},
            "joint_thresholds": {},
            "cross_validation": {},
        }

        for ds, material_name in [
            (UniwearDataset.NUAA, NUAA_MATERIAL),
            (UniwearDataset.PHM2010, PHM2010_MATERIAL),
        ]:
            try:
                stats = uniwear_loader.compute_statistics(ds)
                wear_stats = stats.get("wear_stats", {})

                signal_rms: dict[str, float] = {}
                for col_name, col_stats in stats.get("signal_stats", {}).items():
                    signal_rms[col_name] = col_stats.get("rms", 0.0)

                vibration_keys = [k for k in signal_rms if "vibration" in k.lower()]
                avg_vibration_rms = float(np.mean([signal_rms[k] for k in vibration_keys])) if vibration_keys else 0.0

                result["sources"][ds.value] = {
                    "material": material_name,
                    "signal_rms": signal_rms,
                    "avg_vibration_rms": round(avg_vibration_rms, 6),
                    "wear_range": {
                        "min": wear_stats.get("initial_wear", 0),
                        "max": wear_stats.get("final_wear", 0) or wear_stats.get("max_wear", 0),
                    },
                    "mean_wear_rate": wear_stats.get("mean_wear_rate", 0),
                    "sample_count": wear_stats.get("sample_count", 0),
                }
            except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
                logger.warning("Uniwear calibration failed for %s: %s", ds.value, e, exc_info=True)
                from app.core.safe_errors import safe_error_message

                safe = safe_error_message(
                    e,
                    context="validation_calibrator.calibrate_uniwear",
                    fallback="Uniwear校准失败",
                )
                result["sources"][ds.value] = {
                    "error": safe["message"],
                    "error_id": safe["error_id"],
                }

        result["joint_thresholds"] = {
            "description": "基于 Bosch CNC + Uniwear 联合数据的跨源验证阈值",
            "vibration_warning_multiplier": 2.0,
            "vibration_critical_multiplier": 3.0,
            "wear_rate_warning": 0.0001,
            "wear_rate_critical": 0.0005,
        }

        try:
            (self.calibrate_vibration_thresholds(process=process) if process else None)
            bosch_all = self._load_or_calibrate()

            cross_validation: dict = {}

            uniwear_stats_available = all("error" not in result["sources"].get(s, {}) for s in ["nuaa", "phm2010"])

            if uniwear_stats_available and bosch_all:
                nuaa_wear_rate = result["sources"]["nuaa"]["mean_wear_rate"]
                phm2010_wear_rate = result["sources"]["phm2010"]["mean_wear_rate"]

                bosch_vibration_ranges: dict[str, dict] = {}
                for proc, proc_data in bosch_all.items():
                    vib_data = proc_data.get("vibration", {})
                    bosch_vibration_ranges[proc] = vib_data

                cross_validation["material_wear_rate_comparison"] = {
                    "tc4_nuaa": nuaa_wear_rate,
                    "hrc52_phm2010": phm2010_wear_rate,
                    "unit": "mm/sample",
                }

                cross_validation["calibration_notes"] = [
                    f"TC4(NUAA)磨损率为HRC52(PHM2010)的{nuaa_wear_rate / max(phm2010_wear_rate, 1e-10):.2f}倍",
                    "高硬度材料(HRC52)切削参数需比TC4更保守",
                    "振动阈值需根据材料类型独立校准",
                ]

                cross_validation["bosch_process_count"] = len(bosch_all)
                cross_validation["uniwear_experiment_count"] = result["sources"]["nuaa"].get(
                    "sample_count", 0
                ) + result["sources"]["phm2010"].get("sample_count", 0)

            result["cross_validation"] = cross_validation
        except (ValueError, TypeError, ZeroDivisionError, KeyError, AttributeError) as e:
            logger.warning("Cross-source calibration failed: %s", e, exc_info=True)
            from app.core.safe_errors import safe_error_message

            safe = safe_error_message(
                e,
                context="validation_calibrator.cross_source",
                fallback="跨源校准失败",
            )
            result["cross_validation"] = {
                "error": safe["message"],
                "error_id": safe["error_id"],
            }

        return result

    def generate_unified_validation_rules(self) -> dict:
        bosch_rules = self._load_rules()
        uniwear_analysis = self.calibrate_with_uniwear()

        unified: dict = {
            "metadata": {
                "version": "2.5.0",
                "description": "Unified validation rules calibrated with Bosch CNC + Uniwear",
                "last_updated": self._get_timestamp_str(),
            },
            "bosch_calibrated": bosch_rules.get("bosch_calibrated", {}),
            "uniwear_calibrated": {
                "tc4": {
                    "material": "Titanium TC4 (Ti-6Al-4V)",
                    "source": "NUAA",
                    "recommended_cutting_speed_range": [60, 120],
                    "recommended_feed_range": [0.04, 0.15],
                    "expected_wear_rate_range": self._extract_wear_rate_range(uniwear_analysis, "nuaa"),
                },
                "hrc52": {
                    "material": "Stainless Steel HRC52",
                    "source": "PHM2010",
                    "recommended_cutting_speed_range": [50, 100],
                    "recommended_feed_range": [0.04, 0.12],
                    "expected_wear_rate_range": self._extract_wear_rate_range(uniwear_analysis, "phm2010"),
                },
            },
            "joint_thresholds": uniwear_analysis.get("joint_thresholds", {}),
            "cross_validation": uniwear_analysis.get("cross_validation", {}),
        }

        return unified

    @staticmethod
    def _extract_wear_rate_range(uniwear_analysis: dict, source: str) -> list[float]:
        try:
            rate = uniwear_analysis["sources"][source]["mean_wear_rate"]
            return [round(rate * 0.5, 8), round(rate * 1.5, 8)]
        except (KeyError, TypeError) as e:
            logger.warning("Missing expected data in calibration result: %s", e)
            return [0.0, 0.0]

    @staticmethod
    def _get_timestamp_str() -> str:
        from datetime import datetime

        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _percent_deviation(current: float, calibrated: float) -> float:
        if current == 0:
            return 0.0
        return (calibrated - current) / current * 100
