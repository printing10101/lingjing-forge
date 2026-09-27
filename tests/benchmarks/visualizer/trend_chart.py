"""性能趋势可视化生成器。

使用 Matplotlib 生成性能趋势图表，支持：
- 单个指标的历史趋势折线图
- 版本间指标对比柱状图
- 关键指标仪表盘
"""

from __future__ import annotations

import os

from tests.benchmarks.database.repository import BenchmarkRepository


class TrendVisualizer:
    def __init__(self, repository: BenchmarkRepository) -> None:
        self.repository = repository
        self.output_dir = ""

    def set_output_dir(self, output_dir: str) -> None:
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate_metric_trend_chart(
        self,
        metric_name: str,
        branch: str | None = None,
        limit: int = 30,
    ) -> str | None:
        if not self.output_dir:
            return None

        history = self.repository.get_metric_history(metric_name, limit=limit, branch=branch)
        if len(history) < 2:
            return None

        values = [h.get("metric_value", 0) for h in history][::-1]

        output_path = os.path.join(self.output_dir, f"trend_{metric_name.replace('.', '_')}.png")

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False

            fig, ax = plt.subplots(figsize=(12, 5))
            ax.plot(range(len(values)), values, marker="o", linestyle="-", linewidth=2, markersize=4)
            ax.set_xlabel("测试运行 (按时间顺序)")
            ax.set_ylabel(metric_name)
            ax.set_title(f"{metric_name} 性能趋势", fontsize=14, fontweight="bold")
            ax.grid(True, alpha=0.3)

            if len(values) > 1:
                y_mean = sum(values) / len(values)
                y_std = (sum((v - y_mean) ** 2 for v in values) / len(values)) ** 0.5
                ax.axhline(y=y_mean, color="green", linestyle="--", alpha=0.7, label=f"均值: {y_mean:.3f}")
                ax.axhline(
                    y=y_mean + y_std, color="orange", linestyle=":",
                    alpha=0.5, label=f"+1σ: {y_mean + y_std:.3f}",
                )
                ax.axhline(
                    y=y_mean - y_std, color="orange", linestyle=":",
                    alpha=0.5, label=f"-1σ: {y_mean - y_std:.3f}",
                )

            ax.legend()
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except ImportError:
            return None

        return output_path

    def generate_comparison_chart(
        self,
        run_id_a: str,
        run_id_b: str,
    ) -> str | None:
        if not self.output_dir:
            return None

        comparison = self.repository.compare_versions(run_id_a, run_id_b)
        if "error" in comparison:
            return None

        comparisons = comparison["comparisons"]
        has_change = [c for c in comparisons if c.get("change_pct") is not None]
        if not has_change:
            return None

        metrics = [c["metric_name"][:25] for c in has_change]
        changes = [c["change_pct"] for c in has_change]
        colors = ["red" if c > 0 else "green" for c in changes]

        output_path = os.path.join(self.output_dir, f"comparison_{run_id_a[:8]}_vs_{run_id_b[:8]}.png")

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False

            fig, ax = plt.subplots(figsize=(14, max(6, len(metrics) * 0.4)))
            bars = ax.barh(metrics, changes, color=colors, alpha=0.7)
            ax.axvline(x=0, color="black", linewidth=1)
            ax.axvline(x=20, color="orange", linestyle="--", alpha=0.5, label="Warning (+20%)")
            ax.axvline(x=-20, color="orange", linestyle="--", alpha=0.5, label="Warning (-20%)")
            ax.axvline(x=50, color="red", linestyle="--", alpha=0.5, label="Critical (+50%)")
            ax.axvline(x=-50, color="red", linestyle="--", alpha=0.5, label="Critical (-50%)")

            for bar, change in zip(bars, changes):
                label = f"{change:+.1f}%"
                ax.text(
                    change + (1 if change >= 0 else -1),
                    bar.get_y() + bar.get_height() / 2,
                    label,
                    va="center",
                    fontsize=9,
                    fontweight="bold",
                )

            ax.set_xlabel("变化率 (%)")
            ax.set_title(f"版本间性能对比\n{comparison['run_a']['git_commit'][:8]} vs {comparison['run_b']['git_commit'][:8]}",
                         fontsize=14, fontweight="bold")
            ax.grid(True, axis="x", alpha=0.3)
            ax.legend(loc="lower right")
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except ImportError:
            return None

        return output_path

    def generate_dashboard(self) -> str | None:
        if not self.output_dir:
            return None

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False

            stats = self.repository.get_summary_stats()
            types = self.repository.get_benchmark_types()

            fig, axes = plt.subplots(2, 2, figsize=(14, 10))
            fig.suptitle("性能基准测试仪表盘", fontsize=16, fontweight="bold")

            axes[0, 0].text(
                0.5, 0.5, f"总运行次数\n{stats['total_runs']}",
                ha="center", va="center", fontsize=24, fontweight="bold",
            )
            axes[0, 0].set_title("测试运行统计")

            axes[0, 1].text(
                0.5, 0.5, f"回归检测次数\n{stats['regression_runs']}",
                ha="center", va="center", fontsize=24,
                fontweight="bold",
                color="red" if stats["regression_runs"] > 0 else "green",
            )
            axes[0, 1].set_title("回归检测统计")

            if types:
                type_counts = {}
                for t in types:
                    history = self.repository.get_metric_history(f"{t}_p50_ms", limit=1)
                    type_counts[t] = len(history)
                axes[1, 0].bar(type_counts.keys(), type_counts.values(), color=["#667eea", "#22c55e", "#f59e0b"])
                axes[1, 0].set_title("各模块测试次数")
                axes[1, 0].tick_params(axis="x", rotation=45)
            else:
                axes[1, 0].text(0.5, 0.5, "暂无数据", ha="center", va="center")

            axes[1, 1].text(
                0.5, 0.5, f"总指标数\n{stats['total_results']}",
                ha="center", va="center", fontsize=24, fontweight="bold",
            )
            axes[1, 1].set_title("指标总数")

            plt.tight_layout()
            output_path = os.path.join(self.output_dir, "dashboard.png")
            plt.savefig(output_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except ImportError:
            return None

        return (
            os.path.join(self.output_dir, "dashboard.png")
            if os.path.exists(os.path.join(self.output_dir, "dashboard.png"))
            else None
        )
