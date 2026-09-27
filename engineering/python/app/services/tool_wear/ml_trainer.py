"""ML 训练子服务：sklearn 模型训练 / 评估 / 推理（Bosch CNC + Uniwear）。"""

import logging
from typing import Any

import numpy as np

from app.services.tool_wear._constants import DEFAULT_REPLACEMENT_THRESHOLD
from app.services.tool_wear.curve_predictor import WearCurvePredictor


class WearMLTrainer:
    """
    基于 sklearn 的传统 ML 磨损预测训练与推理服务。

    支持两个数据集：
    - Bosch CNC：振动分类（good/bad），模型 random_forest / xgboost / svm
    - Uniwear（NUAA + PHM2010）：磨损回归，模型 random_forest / gradient_boosting / linear

    注意：本类与 lnn_workflow.yaml 中注册的 LNN 模型 'wear_prediction'（type: "ltc"，
    基于 LTC 神经网络，通过 /api/v1/lnn/predict 调用）是两套独立的系统，
    两者命名空间互不相关，不应混淆。
    """

    def __init__(self, curve_predictor: WearCurvePredictor) -> None:
        self.default_replacement_threshold = DEFAULT_REPLACEMENT_THRESHOLD
        self._curve_predictor = curve_predictor
        self._bosch_model: Any | None = None
        self._bosch_scaler: Any | None = None
        self._bosch_feature_loader: Any | None = None
        self._uniwear_models: dict[str, Any] = {}
        self._uniwear_scalers: dict[str, Any] = {}
        self._uniwear_loader: Any | None = None
        self._logger = logging.getLogger(self.__class__.__name__)

    # Bosch CNC

    def _get_bosch_loader(self, data_dir: str = "python/data/datasets/bosch_cnc"):
        if self._bosch_feature_loader is not None:
            return self._bosch_feature_loader
        try:
            from app.data.bosch_cnc_loader import BoschCNCDataLoader

            loader = BoschCNCDataLoader(data_dir=data_dir)
            self._bosch_feature_loader = loader
            return loader
        except ImportError:
            self._logger.error("bosch_cnc_loader 模块不存在。Bosch CNC 数据处理功能不可用。")
            return None

    def train_with_bosch_data(
        self,
        data_dir: str = "python/data/datasets/bosch_cnc",
        machines: list[str] | None = None,
        processes: list[str] | None = None,
        test_size: float = 0.2,
        model_type: str = "random_forest",
    ) -> dict:
        try:
            import sklearn
            from packaging import version

            sklearn_version = version.parse(sklearn.__version__)
            min_version = version.parse("1.0.0")
            if sklearn_version < min_version:
                self._logger.error(
                    "scikit-learn 版本过低 (%s < 1.0.0)，不兼容当前训练逻辑",
                    sklearn.__version__,
                )
                return {
                    "error": (
                        f"scikit-learn 版本过低 ({sklearn.__version__} < 1.0.0)，"
                        "请升级: pip install 'scikit-learn>=1.0.0'"
                    ),
                    "accuracy": 0.0,
                    "precision": 0.0,
                    "recall": 0.0,
                    "f1": 0.0,
                    "confusion_matrix": [],
                    "feature_importance": [],
                }

            from sklearn.ensemble import RandomForestClassifier
            from sklearn.metrics import (
                accuracy_score,
                confusion_matrix,
                f1_score,
                precision_score,
                recall_score,
            )
            from sklearn.model_selection import train_test_split
            from sklearn.preprocessing import StandardScaler
            from sklearn.svm import SVC
        except ImportError:
            self._logger.error("机器学习依赖未安装，请安装 scikit-learn 等包")
            return {
                "error": "机器学习依赖未安装，请运行: pip install scikit-learn",
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "confusion_matrix": [],
                "feature_importance": [],
            }

        loader = self._get_bosch_loader(data_dir=data_dir)
        if loader is None:
            return {
                "error": "bosch_cnc_loader 模块不可用，Bosch CNC 数据处理功能需要此模块支持",
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "confusion_matrix": [],
                "feature_importance": [],
            }

        try:
            records = loader.load_dataset(split="train")
        except (FileNotFoundError, ValueError) as e:
            self._logger.error("Bosch CNC 数据不可用: %s", e)
            return {
                "error": f"Bosch CNC 数据不可用: {e}",
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "confusion_matrix": [],
                "feature_importance": [],
            }
        feature_names = ["tool_wear", "cutting_force", "vibration"]
        X = np.array(
            [[float(r.get(name, 0.0)) for name in feature_names] for r in records],
            dtype=float,
        )
        # 磨损量 > 0.15mm 视为需要关注的正样本（label=1）
        y = np.array([1 if r.get("tool_wear", 0.0) > 0.15 else 0 for r in records])
        _metadata_list = records

        unique, counts = np.unique(y, return_counts=True)
        self._logger.info(
            "Dataset loaded: %d samples, label distribution: %s",
            len(y),
            dict(zip(unique.astype(str).tolist(), counts.tolist())),
        )

        if len(unique) < 2:
            return {
                "error": "Dataset must contain both good and bad samples for training",
                "accuracy": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "f1": 0.0,
                "confusion_matrix": [],
                "feature_importance": [],
            }

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42, stratify=y)

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        self._bosch_scaler = scaler

        if model_type == "random_forest":
            model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
        elif model_type == "xgboost":
            try:
                from xgboost import XGBClassifier

                model = XGBClassifier(
                    n_estimators=100,
                    max_depth=6,
                    learning_rate=0.1,
                    random_state=42,
                    n_jobs=-1,
                    eval_metric="logloss",
                )
            except ImportError:
                self._logger.warning("XGBoost not installed, falling back to RandomForest")
                model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
                model_type = "random_forest"
        elif model_type == "svm":
            model = SVC(kernel="rbf", probability=True, random_state=42)
        else:
            raise ValueError(
                f"刀具磨损预测失败：不支持的模型类型 '{model_type}'。"
                "支持的模型类型包括：'random_forest'（随机森林）、"
                "'xgboost'（极端梯度提升）、'svm'（支持向量机）。"
                "请检查 model_type 参数配置。"
            )

        model.fit(X_train_scaled, y_train)
        self._bosch_model = model

        y_pred = model.predict(X_test_scaled)

        accuracy = round(float(accuracy_score(y_test, y_pred)), 4)
        precision = round(float(precision_score(y_test, y_pred, zero_division=0)), 4)
        recall = round(float(recall_score(y_test, y_pred, zero_division=0)), 4)
        f1 = round(float(f1_score(y_test, y_pred, zero_division=0)), 4)
        cm = confusion_matrix(y_test, y_pred).tolist()

        feature_importance: list[dict] = []
        if model_type in ("random_forest", "xgboost") and hasattr(model, "feature_importances_"):
            feature_keys = sorted(feature_names)
            importances = model.feature_importances_.tolist()
            feature_importance = sorted(
                [
                    {
                        "feature": feature_keys[i] if i < len(feature_keys) else f"f{i}",
                        "importance": round(imp, 6),
                    }
                    for i, imp in enumerate(importances)
                ],
                key=lambda x: x["importance"],
                reverse=True,
            )[:20]

        self._logger.info(
            "Training complete: model=%s, accuracy=%.4f, f1=%.4f",
            model_type,
            accuracy,
            f1,
        )

        return {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "confusion_matrix": cm,
            "feature_importance": feature_importance,
            "model_type": model_type,
            "train_samples": len(X_train),
            "test_samples": len(X_test),
        }

    def predict_vibration_anomaly(
        self,
        vibration_data: np.ndarray,
    ) -> dict:
        if self._bosch_model is None or self._bosch_scaler is None:
            return {
                "prediction": "unknown",
                "confidence": 0.0,
                "features": {},
                "explanation": "Model not trained. Call train_with_bosch_data() first.",
            }

        if self._bosch_feature_loader is None:
            self._bosch_feature_loader = self._get_bosch_loader()
            if self._bosch_feature_loader is None:
                return {
                    "prediction": "unknown",
                    "confidence": 0.0,
                    "features": {},
                    "explanation": "bosch_cnc_loader 模块不可用。",
                }

        features = self._bosch_feature_loader.extract_features(vibration_data)
        feature_keys = sorted(features.keys())
        X = np.array([[features[k] for k in feature_keys]], dtype=np.float64)
        X_scaled = self._bosch_scaler.transform(X)

        proba = self._bosch_model.predict_proba(X_scaled)[0]
        pred_class = int(self._bosch_model.predict(X_scaled)[0])
        label = "bad" if pred_class == 1 else "good"
        confidence = round(float(max(proba)), 4)

        explanation_parts: list[str] = []
        rms_values = {ax: features.get(f"time_{ax}_rms", 0) for ax in ["x", "y", "z"]}
        max_rms_axis = max(rms_values, key=lambda k: rms_values[k])
        explanation_parts.append(f"RMS峰值出现在{max_rms_axis.upper()}轴 ({rms_values[max_rms_axis]:.4f}g)")

        dom_freqs = {ax: features.get(f"freq_{ax}_dominant_freq", 0) for ax in ["x", "y", "z"]}
        max_freq_axis = max(dom_freqs, key=lambda k: dom_freqs[k])
        explanation_parts.append(f"主频{dom_freqs[max_freq_axis]:.1f}Hz ({max_freq_axis.upper()}轴)")

        if label == "bad":
            explanation_parts.append("检测到异常振动模式，建议检查刀具状态")
        else:
            explanation_parts.append("振动模式正常")

        return {
            "prediction": label,
            "confidence": confidence,
            "features": {k: round(v, 6) for k, v in features.items()},
            "explanation": "；".join(explanation_parts),
        }

    def get_process_baseline(self, process: str, machine: str = "M01") -> dict:
        loader = self._get_bosch_loader()
        if loader is None:
            return {
                "process": process,
                "machine": machine,
                "rms_ranges": {},
                "dominant_frequencies": {},
                "energy_distribution": {},
                "sample_count": 0,
                "warning": "bosch_cnc_loader 模块不可用",
            }
        try:
            samples = loader.load_dataset(machines=[machine], processes=[process], labels=["good"])
        except (FileNotFoundError, ValueError) as e:
            return {
                "process": process,
                "machine": machine,
                "rms_ranges": {},
                "dominant_frequencies": {},
                "energy_distribution": {},
                "sample_count": 0,
                "warning": f"Bosch CNC 数据不可用: {e}",
            }

        if not samples:
            return {
                "process": process,
                "machine": machine,
                "rms_ranges": {},
                "dominant_frequencies": {},
                "energy_distribution": {},
                "sample_count": 0,
                "warning": f"No good samples found for {machine}/{process}",
            }

        axis_data: dict[str, list[float]] = {"x_rms": [], "y_rms": [], "z_rms": []}
        axis_dom_freqs: dict[str, list[float]] = {
            "x_dom_freq": [],
            "y_dom_freq": [],
            "z_dom_freq": [],
        }
        axis_energies: dict[str, list[float]] = {
            "x_energy_ratio": [],
            "y_energy_ratio": [],
            "z_energy_ratio": [],
        }

        for sample in samples:
            feats = loader.extract_features(sample)
            for ax in ["x", "y", "z"]:
                axis_data[f"{ax}_rms"].append(feats.get(f"time_{ax}_rms", 0))
                axis_dom_freqs[f"{ax}_dom_freq"].append(feats.get(f"freq_{ax}_dominant_freq", 0))
                axis_energies[f"{ax}_energy_ratio"].append(feats.get(f"cross_{ax}_energy_ratio", 0))

        rms_ranges = {}
        for key, values in axis_data.items():
            if values:
                rms_ranges[key] = {
                    "min": round(float(np.min(values)), 6),
                    "max": round(float(np.max(values)), 6),
                    "mean": round(float(np.mean(values)), 6),
                    "std": round(float(np.std(values)), 6),
                }

        dominant_frequencies = {}
        for key, values in axis_dom_freqs.items():
            if values:
                dominant_frequencies[key] = {
                    "min": round(float(np.min(values)), 2),
                    "max": round(float(np.max(values)), 2),
                    "mean": round(float(np.mean(values)), 2),
                }

        energy_distribution = {}
        for key, values in axis_energies.items():
            if values:
                energy_distribution[key] = {
                    "min": round(float(np.min(values)), 6),
                    "max": round(float(np.max(values)), 6),
                    "mean": round(float(np.mean(values)), 6),
                }

        return {
            "process": process,
            "machine": machine,
            "rms_ranges": rms_ranges,
            "dominant_frequencies": dominant_frequencies,
            "energy_distribution": energy_distribution,
            "sample_count": len(samples),
        }

    # Uniwear（NUAA + PHM2010）

    def get_uniwear_material_params(self) -> dict:
        return {
            "tc4": {
                "taylor_n": 0.14,
                "taylor_C": 110.0,
                "usui_A": 0.028,
                "usui_B": 620.0,
                "hardness_factor": 1.85,
                "name": "Titanium TC4 (Uniwear-NUAA)",
                "dataset": "nuaa",
                "experiments": ["W1", "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9"],
            },
            "hrc52": {
                "taylor_n": 0.17,
                "taylor_C": 160.0,
                "usui_A": 0.020,
                "usui_B": 720.0,
                "hardness_factor": 1.6,
                "name": "Stainless Steel HRC52 (Uniwear-PHM2010)",
                "dataset": "phm2010",
                "experiments": ["c1", "c4", "c6"],
            },
        }

    def train_with_uniwear_data(
        self,
        data_dir: str = "python/data/uniwear",
        model_type: str = "random_forest",
        test_size: float = 0.2,
    ) -> dict:
        try:
            from sklearn.ensemble import (
                RandomForestRegressor,
                GradientBoostingRegressor,
            )
            from sklearn.linear_model import LinearRegression
            from sklearn.metrics import (
                mean_absolute_error,
                mean_squared_error,
                r2_score,
            )
            from sklearn.model_selection import train_test_split
            from sklearn.preprocessing import StandardScaler
        except ImportError:
            self._logger.error("机器学习依赖未安装，请安装 scikit-learn")
            return {"error": "scikit-learn not installed"}

        from app.data.uniwear_loader import (
            UniwearDataLoader,
            UniwearDataset,
            NUAA_SIGNAL_COLUMNS,
            PHM2010_SIGNAL_COLUMNS,
        )

        loader = UniwearDataLoader(data_dir=data_dir)
        self._uniwear_loader = loader

        results: dict = {"datasets": {}}

        ds_configs = [
            (UniwearDataset.NUAA, NUAA_SIGNAL_COLUMNS, "tc4"),
            (UniwearDataset.PHM2010, PHM2010_SIGNAL_COLUMNS, "hrc52"),
        ]

        for ds, signal_cols, material_key in ds_configs:
            try:
                df = loader.load_dataset(ds)

                if "tool_wear" not in df.columns:
                    results["datasets"][ds.value] = {"error": "No tool_wear column"}
                    continue

                feature_cols = [c for c in signal_cols if c in df.columns and c != "timestamp"]
                if not feature_cols:
                    results["datasets"][ds.value] = {"error": "No valid feature columns"}
                    continue

                df_clean = df.dropna(subset=feature_cols + ["tool_wear"])
                X = df_clean[feature_cols].values.astype(np.float64)
                y = df_clean["tool_wear"].values.astype(np.float64)

                if len(X) < 10:
                    results["datasets"][ds.value] = {"error": f"Insufficient samples: {len(X)}"}
                    continue

                X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=test_size, random_state=42)

                scaler = StandardScaler()
                X_train_scaled = scaler.fit_transform(X_train)
                X_test_scaled = scaler.transform(X_test)

                if model_type == "random_forest":
                    model = RandomForestRegressor(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
                elif model_type == "gradient_boosting":
                    model = GradientBoostingRegressor(
                        n_estimators=100,
                        max_depth=5,
                        learning_rate=0.1,
                        random_state=42,
                    )
                elif model_type == "linear":
                    model = LinearRegression()
                else:
                    raise ValueError(
                        f"刀具磨损预测失败：不支持的模型类型 '{model_type}'。"
                        "支持的模型类型包括：'random_forest'（随机森林）、"
                        "'gradient_boosting'（梯度提升）、'linear'（线性回归）。"
                        "请检查 model_type 参数配置。"
                    )

                model.fit(X_train_scaled, y_train)
                y_pred = model.predict(X_test_scaled)

                mae = round(float(mean_absolute_error(y_test, y_pred)), 6)
                rmse = round(float(np.sqrt(mean_squared_error(y_test, y_pred))), 6)
                r2 = round(float(r2_score(y_test, y_pred)), 4)

                self._uniwear_models[material_key] = model
                self._uniwear_scalers[material_key] = scaler

                feature_importance = []
                if hasattr(model, "feature_importances_"):
                    importances = model.feature_importances_.tolist()
                    feature_importance = sorted(
                        [
                            {
                                "feature": feature_cols[i] if i < len(feature_cols) else f"f{i}",
                                "importance": round(imp, 6),
                            }
                            for i, imp in enumerate(importances)
                        ],
                        key=lambda x: x["importance"],
                        reverse=True,
                    )[:15]

                results["datasets"][ds.value] = {
                    "material": material_key,
                    "model_type": model_type,
                    "train_samples": len(X_train),
                    "test_samples": len(X_test),
                    "features": len(feature_cols),
                    "mae": mae,
                    "rmse": rmse,
                    "r2": r2,
                    "feature_importance": feature_importance,
                }

                self._logger.info(
                    "Uniwear %s training: MAE=%.6f, RMSE=%.6f, R²=%.4f",
                    ds.value,
                    mae,
                    rmse,
                    r2,
                )
            except (ValueError, TypeError, RuntimeError, OSError) as e:
                self._logger.error("Uniwear training failed for %s: %s", ds.value, e, exc_info=True)
                from app.core.safe_errors import safe_error_message

                safe = safe_error_message(
                    e,
                    context="tool_wear_predictor.train_uniwear",
                    fallback="Uniwear训练失败",
                )
                results["datasets"][ds.value] = {
                    "error": safe["message"],
                    "error_id": safe["error_id"],
                }

        return results

    def predict_wear_from_signals(
        self,
        signal_features: dict[str, float],
        material: str = "tc4",
    ) -> dict:
        model = self._uniwear_models.get(material)
        scaler = self._uniwear_scalers.get(material)

        if model is None or scaler is None:
            return {
                "error": f"Model not trained for {material}. Call train_with_uniwear_data() first.",
                "predicted_wear": None,
                "confidence": 0.0,
            }

        feature_order = list(signal_features.keys())
        X = np.array([[signal_features[k] for k in feature_order]], dtype=np.float64)
        X_scaled = scaler.transform(X)

        predicted = float(model.predict(X_scaled)[0])

        if hasattr(model, "predict_proba"):
            confidence = float(np.max(model.predict_proba(X_scaled)))
        else:
            confidence = max(0.6, min(0.95, 1.0 - abs(predicted) * 0.1))

        return {
            "predicted_wear": round(predicted, 6),
            "confidence": round(confidence, 4),
            "material": material,
            "features_used": feature_order,
        }

    def cross_dataset_analysis(self) -> dict:
        if not self._bosch_model:
            bosch_status = "not_trained"
        else:
            bosch_status = "trained"

        uniwear_status = (
            {k: "trained" for k in self._uniwear_models}
            if self._uniwear_models
            else {"tc4": "not_trained", "hrc52": "not_trained"}
        )

        analysis = {
            "bosch_cnc": {
                "status": bosch_status,
                "data_type": "vibration_classification",
            },
            "uniwear": {
                "status": uniwear_status,
                "data_type": "wear_regression",
                "materials": {
                    "tc4": {
                        "source": "NUAA",
                        "experiment_count": 9,
                        "signal_types": "force/vibration/power",
                    },
                    "hrc52": {
                        "source": "PHM2010",
                        "experiment_count": 3,
                        "signal_types": "force/vibration/acoustic_emission",
                    },
                },
            },
            "cross_validation_strategy": [
                "Use Bosch vibration features with Uniwear wear regression to estimate wear",
                "Cross-validate Bosch good/bad labels against Uniwear predicted wear thresholds",
                "Use Uniwear TC4/HRC52 models for material-specific wear predictions in Bosch data",
            ],
            "material_specific_thresholds": {
                "tc4": self._curve_predictor.get_replacement_threshold("titanium_tc4"),
                "hrc52": self._curve_predictor.get_replacement_threshold("stainless_hrc52"),
                "default": self.default_replacement_threshold,
            },
        }

        return analysis
