"""
实验11: 可视化与结果分析脚本

生成论文所需的所有图表:
- 图2: SLD对比图(DL-LNN vs 基线 vs 真实)
- 图3: 跨工况泛化热力图
- 图4: 消融实验柱状图
- 图5: 训练曲线
- 图6: 工业案例时间序列
- 表4: 完整实验结果
- 表5: 超参数敏感性
"""

import os
import sys
import json
import numpy as np
import matplotlib.pyplot as plt
import matplotlib
from matplotlib import rcParams
from typing import Dict, List, Tuple
from pathlib import Path

# 设置中文字体
matplotlib.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
matplotlib.rcParams["axes.unicode_minus"] = False

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from config import ExperimentConfig
from metrics import ChatterMetrics


class ExperimentVisualizer:
    """实验可视化器"""

    def __init__(self, results_dir: str = "results"):
        self.results_dir = Path(results_dir)
        self.figures_dir = self.results_dir / "figures"
        self.figures_dir.mkdir(parents=True, exist_ok=True)

        # 论文图表样式
        self.paper_style = {
            "figure.figsize": (8, 6),
            "font.size": 10,
            "axes.labelsize": 12,
            "axes.titlesize": 14,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 9,
            "figure.dpi": 300,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
        }

    def plot_sld_comparison(
        self,
        spindle_speeds: np.ndarray,
        true_sld: np.ndarray,
        predictions: Dict[str, np.ndarray],
        dataset_name: str = "PHM2010",
        save_path: str = None,
    ):
        """
        绘制SLD对比图(论文图2)

        Args:
            spindle_speeds: 主轴转速数组
            true_sld: 真实SLD(极限切深)
            predictions: 各模型预测结果 {model_name: sld_array}
            dataset_name: 数据集名称
            save_path: 保存路径
        """
        with plt.style.context(self.paper_style):
            fig, ax = plt.subplots(figsize=(10, 6))

            # 绘制真实SLD
            ax.plot(spindle_speeds, true_sld, "k-", linewidth=2.5, label="Ground Truth", zorder=10)

            # 绘制各模型预测
            colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]

            for idx, (model_name, pred_sld) in enumerate(predictions.items()):
                color = colors[idx % len(colors)]
                linestyle = "--" if "DL-LNN" not in model_name else "-"
                linewidth = 2.0 if "DL-LNN" in model_name else 1.5

                ax.plot(
                    spindle_speeds,
                    pred_sld,
                    linestyle=linestyle,
                    color=color,
                    linewidth=linewidth,
                    label=model_name,
                    alpha=0.8,
                )

            ax.set_xlabel("Spindle Speed (rpm)")
            ax.set_ylabel("Limit Cutting Depth (mm)")
            ax.set_title(f"Stability Lobe Diagram Comparison - {dataset_name}")
            ax.legend(loc="best", ncol=2)
            ax.grid(True, alpha=0.3)
            ax.set_xlim([spindle_speeds.min(), spindle_speeds.max()])
            ax.set_ylim([0, np.max(true_sld) * 1.2])

            if save_path is None:
                save_path = self.figures_dir / f"sld_comparison_{dataset_name}.png"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            plt.close()

            print(f"✓ SLD对比图已保存: {save_path}")

    def plot_cross_condition_heatmap(
        self,
        results_matrix: np.ndarray,
        row_labels: List[str],
        col_labels: List[str],
        metric_name: str = "MAE (mm)",
        save_path: str = None,
    ):
        """
        绘制跨工况泛化热力图(论文图3)

        Args:
            results_matrix: 结果矩阵 (models x conditions)
            row_labels: 行标签(模型名称)
            col_labels: 列标签(工况/材料)
            metric_name: 指标名称
            save_path: 保存路径
        """
        with plt.style.context(self.paper_style):
            fig, ax = plt.subplots(figsize=(10, 8))

            # 绘制热力图
            im = ax.imshow(results_matrix, cmap="YlOrRd_r", aspect="auto")

            # 设置刻度
            ax.set_xticks(np.arange(len(col_labels)))
            ax.set_yticks(np.arange(len(row_labels)))
            ax.set_xticklabels(col_labels, rotation=45, ha="right")
            ax.set_yticklabels(row_labels)

            # 添加数值标注
            for i in range(len(row_labels)):
                for j in range(len(col_labels)):
                    value = results_matrix[i, j]
                    text = ax.text(
                        j,
                        i,
                        f"{value:.3f}",
                        ha="center",
                        va="center",
                        color="black" if value < 0.15 else "white",
                        fontsize=9,
                    )

            ax.set_xlabel("Test Condition / Material")
            ax.set_ylabel("Model")
            ax.set_title(f"Cross-Condition Generalization ({metric_name})")

            # 添加颜色条
            cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
            cbar.set_label(metric_name, rotation=270, labelpad=20)

            if save_path is None:
                save_path = self.figures_dir / "cross_condition_heatmap.png"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            plt.close()

            print(f"✓ 跨工况热力图已保存: {save_path}")

    def plot_ablation_study(
        self,
        ablation_results: Dict[str, Dict[str, float]],
        metrics: List[str] = ["MAE", "PCC", "R²"],
        save_path: str = None,
    ):
        """
        绘制消融实验柱状图(论文图4)

        Args:
            ablation_results: 消融结果 {variant: {metric: value}}
            metrics: 要展示的指标
            save_path: 保存路径
        """
        with plt.style.context(self.paper_style):
            variants = list(ablation_results.keys())
            n_variants = len(variants)
            n_metrics = len(metrics)

            x = np.arange(n_metrics)
            width = 0.8 / n_variants

            fig, ax = plt.subplots(figsize=(10, 6))

            colors = ["#d73027", "#fc8d59", "#fee08b", "#91bfdb", "#4575b4"]

            for idx, variant in enumerate(variants):
                values = [ablation_results[variant][m] for m in metrics]
                offset = (idx - n_variants / 2 + 0.5) * width

                bars = ax.bar(x + offset, values, width, label=variant, color=colors[idx % len(colors)], alpha=0.85)

                # 添加数值标注
                for bar, val in zip(bars, values):
                    height = bar.get_height()
                    ax.text(
                        bar.get_x() + bar.get_width() / 2.0, height, f"{val:.3f}", ha="center", va="bottom", fontsize=8
                    )

            ax.set_ylabel("Score")
            ax.set_xlabel("Metric")
            ax.set_title("Ablation Study: Impact of Key Components")
            ax.set_xticks(x)
            ax.set_xticklabels(metrics)
            ax.legend(loc="best")
            ax.grid(True, axis="y", alpha=0.3)

            if save_path is None:
                save_path = self.figures_dir / "ablation_study.png"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            plt.close()

            print(f"✓ 消融实验图已保存: {save_path}")

    def plot_training_curves(
        self, train_history: Dict[str, List[float]], val_history: Dict[str, List[float]], save_path: str = None
    ):
        """
        绘制训练曲线(论文图5)

        Args:
            train_history: 训练历史 {stage: [loss_values]}
            val_history: 验证历史 {stage: [loss_values]}
            save_path: 保存路径
        """
        with plt.style.context(self.paper_style):
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

            # 左图: 训练损失
            for stage, losses in train_history.items():
                epochs = range(1, len(losses) + 1)
                ax1.plot(epochs, losses, label=f"Train ({stage})", linewidth=2)

            ax1.set_xlabel("Epoch")
            ax1.set_ylabel("Training Loss")
            ax1.set_title("Training Loss Curves")
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            ax1.set_yscale("log")

            # 右图: 验证损失
            for stage, losses in val_history.items():
                epochs = range(1, len(losses) + 1)
                ax2.plot(epochs, losses, label=f"Val ({stage})", linewidth=2)

            ax2.set_xlabel("Epoch")
            ax2.set_ylabel("Validation Loss")
            ax2.set_title("Validation Loss Curves")
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            ax2.set_yscale("log")

            plt.tight_layout()

            if save_path is None:
                save_path = self.figures_dir / "training_curves.png"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            plt.close()

            print(f"✓ 训练曲线已保存: {save_path}")

    def plot_industrial_case(
        self,
        time_series: np.ndarray,
        true_labels: np.ndarray,
        predictions: Dict[str, np.ndarray],
        save_path: str = None,
    ):
        """
        绘制工业案例时间序列(论文图6)

        Args:
            time_series: 时间戳数组
            true_labels: 真实标签(0=稳定, 1=颤振)
            predictions: 各模型预测 {model_name: predictions}
            save_path: 保存路径
        """
        with plt.style.context(self.paper_style):
            fig, ax = plt.subplots(figsize=(12, 6))

            # 绘制真实标签
            ax.plot(time_series, true_labels, "k-", linewidth=2, label="Ground Truth", zorder=10)

            # 绘制颤振区域
            chatter_regions = np.where(true_labels == 1)[0]
            if len(chatter_regions) > 0:
                ax.axvspan(
                    time_series[chatter_regions[0]],
                    time_series[chatter_regions[-1]],
                    alpha=0.2,
                    color="red",
                    label="Chatter Region",
                )

            # 绘制各模型预测
            colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]

            for idx, (model_name, pred) in enumerate(predictions.items()):
                color = colors[idx % len(colors)]
                ax.plot(time_series, pred, "--", color=color, linewidth=1.5, label=model_name, alpha=0.8)

            ax.set_xlabel("Time (s)")
            ax.set_ylabel("Stability State")
            ax.set_title("Industrial Case: Chatter Detection Over Time")
            ax.set_yticks([0, 1])
            ax.set_yticklabels(["Stable", "Chatter"])
            ax.legend(loc="best")
            ax.grid(True, alpha=0.3)

            if save_path is None:
                save_path = self.figures_dir / "industrial_case.png"

            plt.savefig(save_path, dpi=300, bbox_inches="tight")
            plt.close()

            print(f"✓ 工业案例图已保存: {save_path}")

    def generate_paper_table(
        self, results: Dict[str, Dict[str, float]], table_name: str = "table2_main_results", save_path: str = None
    ):
        """
        生成论文表格(LaTeX格式)

        Args:
            results: 实验结果 {model_name: {metric: value}}
            table_name: 表格名称
            save_path: 保存路径
        """
        models = list(results.keys())
        metrics = list(results[models[0]].keys())

        # 生成LaTeX表格
        latex_table = []
        latex_table.append("\\begin{table}[htbp]")
        latex_table.append("\\centering")
        latex_table.append("\\caption{Comparison with State-of-the-Art Methods}")
        latex_table.append("\\label{tab:" + table_name + "}")

        # 表头
        header = "\\begin{tabular}{l" + "c" * len(metrics) + "}"
        latex_table.append(header)
        latex_table.append("\\toprule")

        col_names = " & ".join(["Method"] + [f"\\textbf{{{m}}}" for m in metrics])
        latex_table.append(col_names + " \\\\")
        latex_table.append("\\midrule")

        # 数据行
        for model in models:
            row_data = [model.replace("_", " ")]
            for metric in metrics:
                value = results[model][metric]
                if model == "DL-LNN (Ours)":
                    row_data.append(f"\\textbf{{{value:.3f}}}")
                else:
                    row_data.append(f"{value:.3f}")

            latex_table.append(" & ".join(row_data) + " \\\\")

        latex_table.append("\\bottomrule")
        latex_table.append("\\end{tabular}")
        latex_table.append("\\end{table}")

        # 保存
        if save_path is None:
            save_path = self.results_dir / f"{table_name}.tex"

        with open(save_path, "w", encoding="utf-8") as f:
            f.write("\n".join(latex_table))

        print(f"✓ LaTeX表格已保存: {save_path}")

        # 同时保存CSV格式
        csv_path = self.results_dir / f"{table_name}.csv"
        with open(csv_path, "w", encoding="utf-8") as f:
            f.write("Model," + ",".join(metrics) + "\n")
            for model in models:
                values = [str(results[model][m]) for m in metrics]
                f.write(f"{model}," + ",".join(values) + "\n")

        print(f"✓ CSV表格已保存: {csv_path}")


def main():
    """主函数 - 使用真实实验数据生成论文图表"""
    print("=" * 60)
    print("实验11: 可视化与结果分析")
    print("=" * 60)

    # 创建可视化器
    visualizer = ExperimentVisualizer(results_dir="results")

    # 加载真实实验结果
    print("\n加载实验结果...")

    # 1. 主对比实验结果
    with open("results/main_results.json", "r", encoding="utf-8") as f:
        main_results = json.load(f)

    # 2. 跨工况泛化结果
    with open("results/cross_condition_results.json", "r", encoding="utf-8") as f:
        cross_condition_results = json.load(f)

    # 3. 消融实验结果
    with open("results/ablation_results.json", "r", encoding="utf-8") as f:
        ablation_results = json.load(f)["ablation"]

    print("✓ 实验结果加载完成")

    # 图1: 主对比实验柱状图
    print("\n生成图1: 主对比实验...")

    # 合成数据集柱状图
    synthetic_results = main_results["Synthetic"]
    models = list(synthetic_results.keys())
    metrics = ["mae", "rmse", "mape"]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    x = np.arange(len(models))
    width = 0.6

    colors = ["#d73027", "#fc8d59", "#fee08b", "#91bfdb", "#4575b4"]

    for idx, metric in enumerate(metrics):
        values = [synthetic_results[model][metric] for model in models]
        bars = axes[idx].bar(x, values, width, color=colors, alpha=0.85)

        # 添加数值标注
        for bar, val in zip(bars, values):
            height = bar.get_height()
            axes[idx].text(
                bar.get_x() + bar.get_width() / 2.0, height, f"{val:.3f}", ha="center", va="bottom", fontsize=8
            )

        axes[idx].set_ylabel(metric.upper() + " (mm)")
        axes[idx].set_xlabel("Model")
        axes[idx].set_title(f"Synthetic Dataset - {metric.upper()}")
        axes[idx].set_xticks(x)
        axes[idx].set_xticklabels(models, rotation=45, ha="right")
        axes[idx].grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig("results/figures/main_results_synthetic.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("✓ 合成数据集对比图已保存")

    # 工业数据集柱状图
    industrial_results = main_results["Industrial"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    for idx, metric in enumerate(metrics):
        values = [industrial_results[model][metric] for model in models]
        bars = axes[idx].bar(x, values, width, color=colors, alpha=0.85)

        for bar, val in zip(bars, values):
            height = bar.get_height()
            axes[idx].text(
                bar.get_x() + bar.get_width() / 2.0, height, f"{val:.3f}", ha="center", va="bottom", fontsize=8
            )

        axes[idx].set_ylabel(metric.upper() + " (mm)")
        axes[idx].set_xlabel("Model")
        axes[idx].set_title(f"Industrial Dataset - {metric.upper()}")
        axes[idx].set_xticks(x)
        axes[idx].set_xticklabels(models, rotation=45, ha="right")
        axes[idx].grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig("results/figures/main_results_industrial.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("✓ 工业数据集对比图已保存")

    # 图2: 跨工况泛化热力图
    print("\n生成图2: 跨工况泛化热力图...")

    # LOMO热力图（Leave-One-Material-Out）
    lomo_results = cross_condition_results["LOMO"]
    materials = [k for k in lomo_results.keys() if k != "Average"]
    models_lomo = list(lomo_results[materials[0]].keys())

    # 构建MAE矩阵
    mae_matrix_lomo = np.zeros((len(models_lomo), len(materials)))
    for i, model in enumerate(models_lomo):
        for j, material in enumerate(materials):
            mae_matrix_lomo[i, j] = lomo_results[material][model]["MAE"]

    # 绘制热力图
    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(mae_matrix_lomo, cmap="YlOrRd", aspect="auto")

    ax.set_xticks(np.arange(len(materials)))
    ax.set_yticks(np.arange(len(models_lomo)))
    ax.set_xticklabels(materials, rotation=45, ha="right")
    ax.set_yticklabels(models_lomo)

    # 添加数值标注
    for i in range(len(models_lomo)):
        for j in range(len(materials)):
            value = mae_matrix_lomo[i, j]
            text = ax.text(
                j,
                i,
                f"{value:.3f}",
                ha="center",
                va="center",
                color="black" if value < np.median(mae_matrix_lomo) else "white",
                fontsize=9,
            )

    ax.set_xlabel("Material")
    ax.set_ylabel("Model")
    ax.set_title("LOMO Cross-Material Generalization (MAE)")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("MAE (mm)", rotation=270, labelpad=20)

    plt.tight_layout()
    plt.savefig("results/figures/lomo_heatmap.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("✓ LOMO热力图已保存")

    # LOCO热力图（Leave-One-Condition-Out）
    loco_results = cross_condition_results["LOCO"]
    conditions = [k for k in loco_results.keys() if k != "Average"]
    models_loco = list(loco_results[conditions[0]].keys())

    mae_matrix_loco = np.zeros((len(models_loco), len(conditions)))
    for i, model in enumerate(models_loco):
        for j, condition in enumerate(conditions):
            mae_matrix_loco[i, j] = loco_results[condition][model]["MAE"]

    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(mae_matrix_loco, cmap="YlOrRd", aspect="auto")

    ax.set_xticks(np.arange(len(conditions)))
    ax.set_yticks(np.arange(len(models_loco)))
    ax.set_xticklabels(conditions, rotation=45, ha="right")
    ax.set_yticklabels(models_loco)

    for i in range(len(models_loco)):
        for j in range(len(conditions)):
            value = mae_matrix_loco[i, j]
            text = ax.text(
                j,
                i,
                f"{value:.3f}",
                ha="center",
                va="center",
                color="black" if value < np.median(mae_matrix_loco) else "white",
                fontsize=9,
            )

    ax.set_xlabel("Condition")
    ax.set_ylabel("Model")
    ax.set_title("LOCO Cross-Condition Generalization (MAE)")
    cbar = plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("MAE (mm)", rotation=270, labelpad=20)

    plt.tight_layout()
    plt.savefig("results/figures/loco_heatmap.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("✓ LOCO热力图已保存")

    # 图3: 消融实验柱状图
    print("\n生成图3: 消融实验...")

    variants = list(ablation_results.keys())
    metrics_ablation = ["MAE", "RMSE", "R²"]

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    x = np.arange(len(variants))
    width = 0.6

    colors_ablation = ["#d73027", "#fc8d59", "#fee08b", "#91bfdb", "#4575b4"]

    for idx, metric in enumerate(metrics_ablation):
        values = [ablation_results[variant][metric] for variant in variants]
        bars = axes[idx].bar(x, values, width, color=colors_ablation, alpha=0.85)

        for bar, val in zip(bars, values):
            height = bar.get_height()
            axes[idx].text(
                bar.get_x() + bar.get_width() / 2.0, height, f"{val:.3f}", ha="center", va="bottom", fontsize=8
            )

        axes[idx].set_ylabel(metric)
        axes[idx].set_xlabel("Variant")
        axes[idx].set_title(f"Ablation Study - {metric}")
        axes[idx].set_xticks(x)
        axes[idx].set_xticklabels(variants, rotation=45, ha="right")
        axes[idx].grid(True, axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig("results/figures/ablation_study.png", dpi=300, bbox_inches="tight")
    plt.close()
    print("✓ 消融实验图已保存")

    # 表1: 主对比实验结果（LaTeX + CSV）
    print("\n生成表1: 主对比实验结果...")

    # 合成数据集表格
    visualizer.generate_paper_table(main_results["Synthetic"], table_name="table1_synthetic_results")

    # 工业数据集表格
    visualizer.generate_paper_table(main_results["Industrial"], table_name="table2_industrial_results")

    # 表2: 跨工况泛化结果（CSV）
    print("\n生成表2: 跨工况泛化结果...")

    # LOMO平均结果
    lomo_avg = lomo_results["Average"]
    with open("results/table3_lomo_average.csv", "w", encoding="utf-8") as f:
        f.write("Model,MAE,RMSE,R²,PCC\n")
        for model in models_lomo:
            mae = lomo_avg[model]["MAE"]
            rmse = lomo_avg[model]["RMSE"]
            r2 = lomo_avg[model]["R²"]
            pcc = lomo_avg[model]["PCC"]
            f.write(f"{model},{mae:.4f},{rmse:.4f},{r2:.4f},{pcc:.4f}\n")
    print("✓ LOMO平均结果已保存")

    # LOCO平均结果
    loco_avg = loco_results["Average"]
    with open("results/table4_loco_average.csv", "w", encoding="utf-8") as f:
        f.write("Model,MAE,RMSE,R²,PCC\n")
        for model in models_loco:
            mae = loco_avg[model]["MAE"]
            rmse = loco_avg[model]["RMSE"]
            r2 = loco_avg[model]["R²"]
            pcc = loco_avg[model]["PCC"]
            f.write(f"{model},{mae:.4f},{rmse:.4f},{r2:.4f},{pcc:.4f}\n")
    print("✓ LOCO平均结果已保存")

    # 表3: 消融实验结果（CSV）
    print("\n生成表3: 消融实验结果...")

    with open("results/table5_ablation_results.csv", "w", encoding="utf-8") as f:
        f.write("Variant,MAE,RMSE,R²,PCC\n")
        for variant in variants:
            mae = ablation_results[variant]["MAE"]
            rmse = ablation_results[variant]["RMSE"]
            r2 = ablation_results[variant]["R²"]
            pcc = ablation_results[variant]["PCC"]
            f.write(f"{variant},{mae:.4f},{rmse:.4f},{r2:.4f},{pcc:.4f}\n")
    print("✓ 消融实验结果已保存")

    # 汇总报告
    print("\n" + "=" * 60)
    print("✓ 所有论文图表已生成完成!")
    print(f"图表保存位置: {visualizer.figures_dir}")
    print("=" * 60)

    print("\n生成的文件清单:")
    print("  图表:")
    print("    - main_results_synthetic.png (合成数据集对比)")
    print("    - main_results_industrial.png (工业数据集对比)")
    print("    - lomo_heatmap.png (LOMO跨材料泛化热力图)")
    print("    - loco_heatmap.png (LOCO跨工况泛化热力图)")
    print("    - ablation_study.png (消融实验柱状图)")
    print("\n  表格:")
    print("    - table1_synthetic_results.tex/csv (合成数据集结果)")
    print("    - table2_industrial_results.tex/csv (工业数据集结果)")
    print("    - table3_lomo_average.csv (LOMO平均结果)")
    print("    - table4_loco_average.csv (LOCO平均结果)")
    print("    - table5_ablation_results.csv (消融实验结果)")
    print("=" * 60)


if __name__ == "__main__":
    main()
