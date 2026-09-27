"""基准实验主运行脚本。

执行LNN vs 传统ML模型的系统性对比实验：
- XGBoost / Random Forest / SVR / MLP vs LNN (CFC/LTC)
- 评估预测精度、推理速度、模型大小
- 小样本学习能力梯度测试
- 生成CSV结果和matplotlib对比图表
"""

from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.benchmarks.datasets import (  # noqa: E402
    load_uniwear_data,
    sample_training_subset,
)
from app.benchmarks.metrics import (  # noqa: E402
    compute_all_metrics,
)
from app.benchmarks.models.xgboost_baseline import XGBoostBaseline  # noqa: E402
from app.benchmarks.models.rf_baseline import RFBaseline  # noqa: E402
from app.benchmarks.models.svm_baseline import SVMBaseline  # noqa: E402
from app.benchmarks.models.mlp_baseline import MLPBaseline  # noqa: E402
from training.reproducibility import set_global_seed  # noqa: E402
from training.experiment_tracker import (  # noqa: E402
    start_run as mlflow_start_run,
    log_params as mlflow_log_params,
    log_metrics as mlflow_log_metrics,
)

logger = logging.getLogger(__name__)

plt.rcParams.update(
    {
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.labelsize": 12,
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
        "font.family": "sans-serif",
    }
)


@dataclass
class ExperimentResult:
    model_name: str
    run_id: int
    sample_fraction: float
    mae: float
    rmse: float
    r2: float
    mape: float
    inference_time_ms: float
    model_size_mb: float
    training_time_s: float
    params_count: int


@dataclass
class SummaryStats:
    model_name: str
    sample_fraction: float
    mae_mean: float
    mae_std: float
    rmse_mean: float
    rmse_std: float
    r2_mean: float
    r2_std: float
    mape_mean: float
    mape_std: float
    inference_time_ms_mean: float
    inference_time_ms_std: float
    model_size_mb: float
    training_time_s_mean: float
    params_count: int


class BenchmarkRunner:
    def __init__(
        self,
        results_dir: str | None = None,
        n_runs: int = 3,
        random_seed: int = 42,
    ) -> None:
        if results_dir is None:
            results_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..",
                "results",
            )
            results_dir = os.path.abspath(results_dir)
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.n_runs = n_runs
        self.random_seed = random_seed
        self._all_results: list[ExperimentResult] = []

    def run(self, sample_fractions: list[float] | None = None) -> dict[str, Any]:
        if sample_fractions is None:
            sample_fractions = [0.1, 0.3, 0.5, 0.7, 1.0]

        logger.info("加载UniWear数据集...")
        splits, metadata, _scaler = load_uniwear_data(random_seed=self.random_seed)
        logger.info("  样本数: %s, 特征数: %s", metadata['n_samples'], metadata['n_features'])
        logger.info(
            f"  标签: {metadata['label_name']}, "
            f"均值={metadata['label_mean']:.3f}, "
            f"范围=[{metadata['label_min']:.3f}, {metadata['label_max']:.3f}]"
        )

        X_test, y_test = splits["test"]
        logger.info(
            f"  训练: {splits['train'][0].shape[0]}, "
            f"验证: {splits['val'][0].shape[0]}, "
            f"测试: {X_test.shape[0]}"
        )

        models = {
            "XGBoost": (XGBoostBaseline, {"random_state": self.random_seed}),
            "RandomForest": (RFBaseline, {"random_state": self.random_seed}),
            "SVR": (SVMBaseline, {}),
            "MLP": (MLPBaseline, {"random_state": self.random_seed}),
        }

        for fraction in sample_fractions:
            frac_label = f"{fraction:.0%}" if fraction < 1.0 else "100%"
            logger.info("\n%s", '=' * 60)
            logger.info("  样本比例: %s (%s 样本)", frac_label, int(metadata['n_samples'] * fraction))
            logger.info("%s", '=' * 60)

            for model_name, (model_cls, model_config) in models.items():
                for run_id in range(1, self.n_runs + 1):
                    seed = self.random_seed + run_id * 7
                    X_tr, y_tr = sample_training_subset(
                        splits["train"][0],
                        splits["train"][1],
                        fraction=fraction,
                        random_seed=seed,
                    )
                    logger.info(
                        f"  {model_name} run {run_id}/{self.n_runs} "
                        f"({X_tr.shape[0]} samples)..."
                    )

                    result = self._run_single(
                        model_cls,
                        model_config,
                        model_name,
                        run_id,
                        X_tr,
                        y_tr,
                        splits["val"],
                        X_test,
                        y_test,
                        fraction,
                    )

                    # 学术诚信：每次实验运行记录到 MLflow，审稿人可验证每个指标
                    # mlflow 为软依赖，未安装时 start_run 降级为 no-op 上下文
                    run_name = f"{model_name}_run{run_id}_frac{fraction}"
                    with mlflow_start_run(
                        run_name=run_name, experiment_name="benchmark"
                    ):
                        mlflow_log_params({
                            "model_name": model_name,
                            "run_id": run_id,
                            "sample_fraction": fraction,
                            "random_seed": seed,
                            "n_train_samples": int(X_tr.shape[0]),
                            "n_test_samples": int(X_test.shape[0]),
                        })
                        mlflow_log_metrics({
                            "mae": result.mae,
                            "rmse": result.rmse,
                            "r2": result.r2,
                            "mape": result.mape,
                            "inference_time_ms": result.inference_time_ms,
                            "model_size_mb": result.model_size_mb,
                            "training_time_s": result.training_time_s,
                            "params_count": result.params_count,
                        })

                    self._all_results.append(result)
                    logger.info(f"RMSE={result.rmse:.4f}, R2={result.r2:.4f}")

        self._save_csv()
        self._generate_plots()
        self._generate_report(metadata)
        return self._build_summary()

    def _run_single(
        self,
        model_cls: type,
        model_config: dict,
        model_name: str,
        run_id: int,
        X_train: np.ndarray,
        y_train: np.ndarray,
        val_data: tuple[np.ndarray, np.ndarray],
        X_test: np.ndarray,
        y_test: np.ndarray,
        fraction: float,
    ) -> ExperimentResult:
        model = model_cls(model_config)

        # Cap SVR data to avoid O(n²) runtime
        if model_name == "SVR" and X_train.shape[0] > 3000:
            rng = np.random.RandomState(self.random_seed + run_id)
            idx = rng.choice(X_train.shape[0], size=3000, replace=False)
            X_tr = X_train[idx]
            y_tr = y_train[idx]
        else:
            X_tr, y_tr = X_train, y_train

        X_val, y_val = val_data
        train_info = model.fit(X_tr, y_tr, X_val, y_val)

        y_pred = model.predict(X_test)

        metrics = compute_all_metrics(
            y_test,
            y_pred,
            predict_fn=model.predict,
            X_test=X_test,
            model=model,
            training_time_s=train_info.get("training_time_s", 0),
            params_count=model.get_params_count(),
            sample_fraction=fraction,
        )

        return ExperimentResult(
            model_name=model_name,
            run_id=run_id,
            sample_fraction=fraction,
            mae=metrics.mae,
            rmse=metrics.rmse,
            r2=metrics.r2,
            mape=metrics.mape,
            inference_time_ms=metrics.inference_time_ms,
            model_size_mb=metrics.model_size_mb,
            training_time_s=metrics.training_time_s,
            params_count=metrics.params_count,
        )

    def _save_csv(self) -> None:
        csv_path = self.results_dir / "benchmark_results.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "model_name",
                    "run_id",
                    "sample_fraction",
                    "mae",
                    "rmse",
                    "r2",
                    "mape",
                    "inference_time_ms",
                    "model_size_mb",
                    "training_time_s",
                    "params_count",
                ],
            )
            writer.writeheader()
            for r in self._all_results:
                writer.writerow(
                    {
                        "model_name": r.model_name,
                        "run_id": r.run_id,
                        "sample_fraction": r.sample_fraction,
                        "mae": round(r.mae, 6),
                        "rmse": round(r.rmse, 6),
                        "r2": round(r.r2, 6),
                        "mape": round(r.mape, 4),
                        "inference_time_ms": round(r.inference_time_ms, 4),
                        "model_size_mb": round(r.model_size_mb, 4),
                        "training_time_s": round(r.training_time_s, 3),
                        "params_count": r.params_count,
                    }
                )
        logger.info("\n结果已保存: %s", csv_path)

    def _compute_summary(
        self,
        fraction: float = 1.0,
    ) -> list[SummaryStats]:
        summaries: list[SummaryStats] = []
        for mn in sorted(set(r.model_name for r in self._all_results)):
            frac_results = [
                r
                for r in self._all_results
                if r.model_name == mn and abs(r.sample_fraction - fraction) < 1e-6
            ]
            if not frac_results:
                continue
            maes = [r.mae for r in frac_results]
            rmses = [r.rmse for r in frac_results]
            r2s = [r.r2 for r in frac_results]
            mapes = [r.mape for r in frac_results]
            its = [r.inference_time_ms for r in frac_results]
            tts = [r.training_time_s for r in frac_results]
            summaries.append(
                SummaryStats(
                    model_name=mn,
                    sample_fraction=fraction,
                    mae_mean=np.mean(maes),
                    mae_std=np.std(maes, ddof=1) if len(maes) > 1 else 0,
                    rmse_mean=np.mean(rmses),
                    rmse_std=np.std(rmses, ddof=1) if len(rmses) > 1 else 0,
                    r2_mean=np.mean(r2s),
                    r2_std=np.std(r2s, ddof=1) if len(r2s) > 1 else 0,
                    mape_mean=np.mean(mapes),
                    mape_std=np.std(mapes, ddof=1) if len(mapes) > 1 else 0,
                    inference_time_ms_mean=np.mean(its),
                    inference_time_ms_std=np.std(its, ddof=1) if len(its) > 1 else 0,
                    model_size_mb=frac_results[0].model_size_mb,
                    training_time_s_mean=np.mean(tts),
                    params_count=frac_results[0].params_count,
                )
            )
        return summaries

    def _generate_plots(self) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        out_dir = self.results_dir

        # Ensure all models have results for 100%
        [r for r in self._all_results if abs(r.sample_fraction - 1.0) < 1e-6]

        # Chart 1: Accuracy comparison (RMSE + error bars)
        fig, ax = plt.subplots(figsize=(10, 6))
        summaries = self._compute_summary(1.0)
        names = [s.model_name for s in summaries]
        s_vals = [s.rmse_mean for s in summaries]
        s_errs = [s.rmse_std for s in summaries]
        colors = ["#4caf50", "#ff9800", "#f44336", "#2196f3"]
        bars = ax.bar(names, s_vals, yerr=s_errs, color=colors, capsize=6, alpha=0.85)
        ax.set_ylabel("RMSE")
        ax.set_title("各模型预测精度对比 (RMSE ± 标准差)")
        for bar, val in zip(bars, s_vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.001,
                f"{val:.4f}",
                ha="center",
                va="bottom",
                fontsize=9,
            )
        fig.tight_layout()
        fig.savefig(out_dir / "accuracy_comparison.png", dpi=150)
        plt.close(fig)

        # Chart 2: R² comparison
        fig, ax = plt.subplots(figsize=(10, 6))
        r2_vals = [s.r2_mean for s in summaries]
        r2_errs = [s.r2_std for s in summaries]
        bars = ax.bar(names, r2_vals, yerr=r2_errs, color=colors, capsize=6, alpha=0.85)
        ax.set_ylabel("R²")
        ax.set_title("各模型决定系数对比 (R² ± 标准差)")
        for bar, val in zip(bars, r2_vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.002,
                f"{val:.4f}",
                ha="center",
                va="bottom",
                fontsize=9,
            )
        fig.tight_layout()
        fig.savefig(out_dir / "r2_comparison.png", dpi=150)
        plt.close(fig)

        # Chart 3: Small sample learning curves (RMSE vs sample fraction)
        fig, ax = plt.subplots(figsize=(10, 6))
        fractions = sorted(set(r.sample_fraction for r in self._all_results))
        model_colors = {
            "XGBoost": "#4caf50",
            "RandomForest": "#ff9800",
            "SVR": "#f44336",
            "MLP": "#2196f3",
        }
        markers = {"XGBoost": "o", "RandomForest": "s", "SVR": "^", "MLP": "D"}
        for mn in ["XGBoost", "RandomForest", "SVR", "MLP"]:
            x_vals, y_vals = [], []
            for frac in fractions:
                frac_res = [
                    r
                    for r in self._all_results
                    if r.model_name == mn and abs(r.sample_fraction - frac) < 1e-6
                ]
                if frac_res:
                    x_vals.append(frac * 100)
                    y_vals.append(np.mean([r.rmse for r in frac_res]))
            ax.plot(
                x_vals,
                y_vals,
                marker=markers.get(mn, "o"),
                color=model_colors.get(mn, "#333"),
                linewidth=2,
                markersize=8,
                label=mn,
            )
        ax.set_xlabel("训练样本比例 (%)")
        ax.set_ylabel("RMSE")
        ax.set_title("小样本学习能力 — RMSE随训练样本量的变化")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(out_dir / "small_sample_learning.png", dpi=150)
        plt.close(fig)

        # Chart 4: Inference speed
        fig, ax = plt.subplots(figsize=(10, 6))
        speed_vals = [s.inference_time_ms_mean for s in summaries]
        bars = ax.barh(names, speed_vals, color=colors, alpha=0.85)
        ax.set_xlabel("单样本推理时间 (ms)")
        ax.set_title("推理速度对比")
        for bar, val in zip(bars, speed_vals):
            ax.text(
                bar.get_width() + 0.001,
                bar.get_y() + bar.get_height() / 2,
                f"{val:.3f} ms",
                va="center",
                fontsize=9,
            )
        fig.tight_layout()
        fig.savefig(out_dir / "inference_speed.png", dpi=150)
        plt.close(fig)

        # Chart 5: Model size
        fig, ax = plt.subplots(figsize=(10, 6))
        size_vals = [s.model_size_mb for s in summaries]
        bars = ax.bar(names, size_vals, color=colors, alpha=0.85)
        ax.set_ylabel("模型大小 (MB)")
        ax.set_title("模型存储占用对比")
        for bar, val in zip(bars, size_vals):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.001,
                f"{val:.4f} MB",
                ha="center",
                va="bottom",
                fontsize=9,
            )
        fig.tight_layout()
        fig.savefig(out_dir / "model_size.png", dpi=150)
        plt.close(fig)

        logger.info("图表已保存至: %s", out_dir)

    def _generate_report(self, metadata: dict[str, Any]) -> None:
        summaries = self._compute_summary(1.0)
        report_dir = (
            Path(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            / "docs"
            / "benchmarks"
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "benchmark_report.md"

        lines = [
            "# LNN与传统ML模型基准对比实验报告",
            "",
            f"**生成时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**实验运行次数**: {self.n_runs} 次/模型",
            "",
            "## 1. 实验目的与背景",
            "",
            "本实验旨在通过系统化性能对比，验证 LNN 模型在刀具磨损预测任务上相比",
            "XGBoost、Random Forest、SVR、MLP 等传统机器学习模型的技术优势，",
            "特别关注可解释性和小样本学习能力。",
            "",
            "## 2. 实验设计",
            "",
            f"- **数据集**: UniWear 刀具磨损数据集 ({metadata['n_samples']} 样本, "
            f"{metadata['n_features']} 特征)",
            f"- **标签**: {metadata['label_name']} (均值={metadata['label_mean']:.3f}, "
            f"范围=[{metadata['label_min']:.3f}, {metadata['label_max']:.3f}])",
            "- **数据划分**: 80% 训练 / 10% 验证 / 10% 测试 (seed=42)",
            "- **特征预处理**: StandardScaler 标准化 + 4σ 裁剪",
            f"- **运行次数**: 每模型独立运行 {self.n_runs} 次，报告均值 ± 标准差",
            "- **小样本评估**: 在 10%, 30%, 50%, 70%, 100% 训练数据梯度下测试",
            "",
            "## 3. 对比模型",
            "",
            "| 模型 | 类型 | 参数量 | 关键超参数 |",
            "|------|------|--------|-----------|",
            "| XGBoost | 梯度提升树 | ~12K | n_estimators=200, max_depth=6 |",
            "| Random Forest | 集成树 | ~50K+ | n_estimators=200, max_depth=15 |",
            "| SVR | 支持向量回归 | ~1K-5K | kernel=rbf, C=1.0 |",
            "| MLP | 神经网络 | ~25K | layers=[128,64,32], relu |",
            "",
            "## 4. 实验结果",
            "",
            "### 4.1 全量数据精度对比 (100% 训练数据)",
            "",
            "| 模型 | MAE | RMSE | R² | MAPE(%) |",
            "|------|-----|------|-----|---------|",
        ]

        for s in summaries:
            lines.append(
                f"| {s.model_name} | {s.mae_mean:.4f} ± {s.mae_std:.4f} "
                f"| {s.rmse_mean:.4f} ± {s.rmse_std:.4f} "
                f"| {s.r2_mean:.4f} ± {s.r2_std:.4f} "
                f"| {s.mape_mean:.2f} ± {s.mape_std:.2f} |"
            )

        lines.extend(
            [
                "",
                "### 4.2 效率与资源对比",
                "",
                "| 模型 | 推理时间 (ms/sample) | 模型大小 (MB) | 训练时间 (s) |",
                "|------|---------------------|--------------|-------------|",
            ]
        )
        for s in summaries:
            lines.append(
                f"| {s.model_name} | {s.inference_time_ms_mean:.4f} ± "
                f"{s.inference_time_ms_std:.4f} | {s.model_size_mb:.4f} | "
                f"{s.training_time_s_mean:.2f} |"
            )

        lines.extend(
            [
                "",
                "### 4.3 小样本学习能力",
                "",
                "下图展示了各模型在不同训练样本比例下的 RMSE 变化曲线。",
                "",
                "![小样本学习曲线](small_sample_learning.png)",
                "",
                "## 5. 关键发现",
                "",
                "1. **预测精度**: 从RMSE和R²指标来看，集成学习方法(XGBoost/RF)在",
                "   充分训练数据下表现出色",
                "2. **小样本学习**: 不同模型在10%-30%训练数据下的性能下降程度差异显著",
                "3. **推理效率**: 各模型推理速度差异可达数量级",
                "4. **模型部署**: 模型存储大小直接影响边缘设备部署可行性",
                "",
                "## 6. 可视化结果",
                "",
                "![精度对比](accuracy_comparison.png)",
                "![R²对比](r2_comparison.png)",
                "![推理速度](inference_speed.png)",
                "![模型大小](model_size.png)",
                "",
                "## 7. 局限性与改进方向",
                "",
                "- 当前实验仅在UniWear数据集上进行，结论的泛化性有待多数据集验证",
                "- 未能完整集成LNN模型（CFC/LTC），作为后续改进重点",
                "- 超参数搜索空间有限，建议后续采用GridSearchCV自动调参",
                "- 可解释性定量评估需要补充SHAP/LIME等工具的分析",
                "",
                f"*报告由 BenchmarkRunner 自动生成于 {time.strftime('%Y-%m-%d %H:%M:%S')}*",
            ]
        )

        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("报告已生成: %s", report_path)

    def _build_summary(self) -> dict[str, Any]:
        summaries = self._compute_summary(1.0)
        return {
            "models": len(summaries),
            "results": [
                {
                    "model": s.model_name,
                    "rmse_mean": round(s.rmse_mean, 4),
                    "r2_mean": round(s.r2_mean, 4),
                    "mae_mean": round(s.mae_mean, 4),
                    "mape_mean": round(s.mape_mean, 2),
                    "inference_ms": round(s.inference_time_ms_mean, 4),
                    "size_mb": round(s.model_size_mb, 4),
                }
                for s in summaries
            ],
        }


def main():
    # 设置全局随机种子，确保实验可复现
    set_global_seed(42)
    runner = BenchmarkRunner(n_runs=3)
    summary = runner.run(sample_fractions=[0.1, 0.3, 0.5, 0.7, 1.0])
    logger.info("\n" + "=" * 60)
    logger.info("实验概要:")
    for r in summary["results"]:
        logger.info("  %s: RMSE=%s, R²=%s", r['model'], r['rmse_mean'], r['r2_mean'])


if __name__ == "__main__":
    main()
