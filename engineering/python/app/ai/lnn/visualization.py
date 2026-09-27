"""加工过程离线可视化渲染管线 —— 借鉴 lingbot-map batch_demo 渲染思路.

lingbot-map 的 ``batch_demo.py`` 提供了一个离线批处理脚本：将流式 3D
重建的关键帧/点云/漂移指标渲染为静态图片序列，便于离线分析重建质量。

本模块将同样的"离线渲染"思想迁移到 LTC 颤振/刀具磨损时序预测场景：

输入：``StreamingPredictor.predict_windowed`` / ``predict_stream`` 产出的
``PredictionResult`` 列表（含 ``model_info`` 流式元数据）。
输出：单张多子图 PNG 报告 + 可选的 CSV/JSON 原始数据转储。

渲染内容
--------
1. **预测值时间序列**：主曲线 + 关键帧高亮（红点）+ 置信带
2. **帧能量曲线**：标注能量突变触发关键帧的位置（颤振前兆）
3. **锚点漂移曲线**：``anchor_drift`` 随时间变化，反映 LTC 长序列状态衰减
4. **轨迹偏差曲线**：``trajectory_deviation`` 反映轨迹记忆约束的修正强度

设计原则
--------
- **无 GUI 依赖**：强制使用 ``matplotlib.use('Agg')`` 后端，可在无显示
  环境的服务器/CI 中运行。
- **优雅降级**：matplotlib 不可用时退化为仅写 CSV/JSON。
- **可复现**：固定 ``random.seed``，渲染参数全显式。
- **非侵入**：仅消费 ``PredictionResult.model_info`` 字段，不修改流式
  推理路径。
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Sequence

import numpy as np

logger = logging.getLogger(__name__)

# matplotlib 强制使用 Agg 后端，避免在无显示环境报错。
# 必须在 pyplot 导入前完成。
Figure: Any
try:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure as Figure

    _HAS_MPL = True
except ImportError:  # pragma: no cover - 仅当环境无 matplotlib 时触发
    plt = None  # type: ignore[assignment]
    Figure = None
    _HAS_MPL = False


# 配置


@dataclass
class VisualizationConfig:
    """离线渲染配置.

    所有字段显式给定默认值，避免隐式状态，保证可复现。
    """

    figsize: tuple[float, float] = (14.0, 9.0)
    dpi: int = 120
    keyframe_marker_size: float = 40.0
    keyframe_color: str = "#d62728"
    line_color: str = "#1f77b4"
    band_color: str = "#aec7e8"
    energy_color: str = "#2ca02c"
    drift_color: str = "#ff7f0e"
    trajectory_color: str = "#9467bd"
    background_alpha: float = 0.25
    title: str = "Streaming Inference Report"
    seed: int = 42

    def __post_init__(self) -> None:
        # M16 修复：不在 dataclass __post_init__ 中设置全局随机种子，
        # 这会污染整个进程的随机状态。改用 RandomState 实例供后续使用。
        self._rng = np.random.RandomState(self.seed)


# 数据提取


def extract_streaming_metrics(
    results: Sequence[Any],
) -> dict[str, np.ndarray]:
    """从 ``PredictionResult`` 列表中提取流式元数据为 numpy 数组.

    Parameters
    ----------
    results : Sequence[Any]
        :meth:`StreamingPredictor.predict_windowed` /
        :meth:`predict_stream` 的输出。每个元素需具备 ``value`` /
        ``confidence`` / ``model_info`` 字段。

    Returns
    -------
    Dict[str, np.ndarray]
        包含 ``values`` / ``confidences`` / ``frame_ids`` /
        ``is_keyframe`` / ``energies`` / ``anchor_drifts`` /
        ``trajectory_deviations`` / ``inference_times`` 等数组。
    """
    n = len(results)
    values: list[float] = []
    confidences = np.zeros(n, dtype=np.float64)
    frame_ids = np.arange(1, n + 1, dtype=np.int64)
    is_keyframe = np.zeros(n, dtype=bool)
    energies = np.zeros(n, dtype=np.float64)
    anchor_drifts = np.zeros(n, dtype=np.float64)
    trajectory_deviations = np.zeros(n, dtype=np.float64)
    inference_times = np.zeros(n, dtype=np.float64)

    for i, r in enumerate(results):
        # 预测值可能是标量/向量/字典，统一提取为标量
        v = getattr(r, "value", r)
        try:
            arr = np.asarray(v, dtype=np.float64).ravel()
            scalar_val = float(arr[0]) if arr.size else float("nan")
        except (ValueError, TypeError):
            scalar_val = float("nan")
        values.append(scalar_val)

        confidences[i] = float(getattr(r, "confidence", 0.0) or 0.0)
        inference_times[i] = float(getattr(r, "inference_time", 0.0) or 0.0)

        info = getattr(r, "model_info", {}) or {}
        if isinstance(info, dict):
            is_keyframe[i] = bool(info.get("is_keyframe", False))
            energies[i] = float(info.get("frame_energy", 0.0) or 0.0)
            anchor_drifts[i] = float(info.get("anchor_drift", 0.0) or 0.0)
            trajectory_deviations[i] = float(info.get("trajectory_deviation", 0.0) or 0.0)

    return {
        "values": np.asarray(values, dtype=np.float64),
        "confidences": confidences,
        "frame_ids": frame_ids,
        "is_keyframe": is_keyframe,
        "energies": energies,
        "anchor_drifts": anchor_drifts,
        "trajectory_deviations": trajectory_deviations,
        "inference_times": inference_times,
    }


# 渲染


class StreamingReportRenderer:
    """将流式推理结果渲染为离线 PNG 报告 + CSV/JSON 转储.

    Example:
        >>> renderer = StreamingReportRenderer()
        >>> renderer.render(results, output_path="report.png")
        {'png_path': '...', 'csv_path': '...', 'json_path': '...'}
    """

    def __init__(self, config: VisualizationConfig | None = None) -> None:
        self._config = config or VisualizationConfig()

    def render(
        self,
        results: Sequence[Any],
        output_path: str,
        dump_csv: bool = True,
        dump_json: bool = True,
        model_name: str | None = None,
    ) -> dict[str, str]:
        """渲染完整报告.

        Parameters
        ----------
        results : Sequence[Any]
            流式推理结果列表。
        output_path : str
            PNG 输出路径。
        dump_csv : bool
            是否同时导出原始指标 CSV。
        dump_json : bool
            是否同时导出原始指标 JSON。
        model_name : Optional[str]
            模型名（写入报告标题与元数据）。

        Returns
        -------
        Dict[str, str]
            实际产出的文件路径：``png_path`` / ``csv_path`` / ``json_path``。
            未产出的字段不包含在返回值中。
        """
        metrics = extract_streaming_metrics(results)
        outputs: dict[str, str] = {}

        # PNG 渲染（matplotlib 不可用时跳过）
        if _HAS_MPL and plt is not None:
            fig = None
            try:
                fig = self._render_figure(metrics, model_name)
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                fig.savefig(
                    output_path,
                    dpi=self._config.dpi,
                    bbox_inches="tight",
                )
                outputs["png_path"] = output_path
                logger.info(
                    "StreamingReportRenderer: PNG 报告已写入 %s",
                    output_path,
                )
            except (ValueError, TypeError, RuntimeError, OSError) as exc:
                logger.error(
                    "StreamingReportRenderer: PNG 渲染失败: %s",
                    exc,
                    exc_info=True,
                )
            finally:
                # savefig 抛错（磁盘满/权限）时也必须关闭 figure，
                # 否则 Agg 后端的 figure 累积导致内存泄漏
                if fig is not None and plt is not None:
                    plt.close(fig)
        else:
            logger.warning("StreamingReportRenderer: matplotlib 不可用，跳过 PNG 渲染。")

        # CSV/JSON 转储（无外部依赖，始终执行）
        base = os.path.splitext(output_path)[0]
        if dump_csv:
            csv_path = f"{base}.csv"
            try:
                self._dump_csv(metrics, csv_path, model_name)
                outputs["csv_path"] = csv_path
            except (ValueError, TypeError, OSError) as exc:
                logger.error("StreamingReportRenderer: CSV 转储失败: %s", exc)
        if dump_json:
            json_path = f"{base}.json"
            try:
                self._dump_json(metrics, json_path, model_name)
                outputs["json_path"] = json_path
            except (ValueError, TypeError, OSError) as exc:
                logger.error("StreamingReportRenderer: JSON 转储失败: %s", exc)

        return outputs

    # 内部方法

    def _render_figure(
        self,
        metrics: dict[str, np.ndarray],
        model_name: str | None,
    ) -> Any:
        """构造 4 子图 matplotlib figure."""
        cfg = self._config
        fig, axes = plt.subplots(4, 1, figsize=cfg.figsize, sharex=True)
        fig.suptitle(
            cfg.title + (f" - {model_name}" if model_name else ""),
            fontsize=13,
        )

        frame_ids = metrics["frame_ids"]
        values = metrics["values"]
        confidences = metrics["confidences"]
        is_kf = metrics["is_keyframe"]
        energies = metrics["energies"]
        anchor_drifts = metrics["anchor_drifts"]
        traj_dev = metrics["trajectory_deviations"]

        # 子图 1: 预测值 + 置信带 + 关键帧标记
        ax1 = axes[0]
        # 置信带：用 1 - confidence 作为半宽
        half_width = np.clip(1.0 - confidences, 0.0, 1.0) * (np.nanstd(values) + 1e-6)
        ax1.fill_between(
            frame_ids,
            values - half_width,
            values + half_width,
            color=cfg.band_color,
            alpha=cfg.background_alpha,
            label="confidence band",
        )
        ax1.plot(
            frame_ids,
            values,
            color=cfg.line_color,
            linewidth=1.5,
            label="predicted value",
        )
        kf_ids = frame_ids[is_kf]
        kf_vals = values[is_kf]
        if kf_ids.size > 0:
            ax1.scatter(
                kf_ids,
                kf_vals,
                color=cfg.keyframe_color,
                s=cfg.keyframe_marker_size,
                zorder=5,
                label="keyframe",
                edgecolors="black",
                linewidths=0.5,
            )
        ax1.set_ylabel("Predicted Value")
        ax1.legend(loc="upper right", fontsize=8)
        ax1.grid(True, alpha=0.3)

        # 子图 2: 帧能量（颤振前兆指标）
        ax2 = axes[1]
        ax2.plot(
            frame_ids,
            energies,
            color=cfg.energy_color,
            linewidth=1.2,
            label="frame energy",
        )
        if kf_ids.size > 0:
            kf_energies = energies[is_kf]
            ax2.scatter(
                kf_ids,
                kf_energies,
                color=cfg.keyframe_color,
                s=cfg.keyframe_marker_size * 0.7,
                zorder=5,
                label="energy spike (keyframe)",
            )
        ax2.set_ylabel("Frame Energy")
        ax2.legend(loc="upper right", fontsize=8)
        ax2.grid(True, alpha=0.3)

        # 子图 3: 锚点漂移
        ax3 = axes[2]
        ax3.plot(
            frame_ids,
            anchor_drifts,
            color=cfg.drift_color,
            linewidth=1.2,
            label="anchor drift",
        )
        ax3.axhline(0.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
        ax3.set_ylabel("Anchor Drift")
        ax3.legend(loc="upper right", fontsize=8)
        ax3.grid(True, alpha=0.3)

        # 子图 4: 轨迹偏差
        ax4 = axes[3]
        ax4.plot(
            frame_ids,
            traj_dev,
            color=cfg.trajectory_color,
            linewidth=1.2,
            label="trajectory deviation",
        )
        ax4.axhline(0.0, color="gray", linestyle="--", linewidth=0.8, alpha=0.5)
        ax4.set_ylabel("Trajectory Deviation")
        ax4.set_xlabel("Frame ID")
        ax4.legend(loc="upper right", fontsize=8)
        ax4.grid(True, alpha=0.3)

        plt.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))
        return fig

    def _dump_csv(
        self,
        metrics: dict[str, np.ndarray],
        path: str,
        model_name: str | None,
    ) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        n = len(metrics["frame_ids"])
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "frame_id",
                    "value",
                    "confidence",
                    "is_keyframe",
                    "frame_energy",
                    "anchor_drift",
                    "trajectory_deviation",
                    "inference_time_ms",
                    "model_name",
                ]
            )
            for i in range(n):
                writer.writerow(
                    [
                        int(metrics["frame_ids"][i]),
                        float(metrics["values"][i]) if not np.isnan(metrics["values"][i]) else "",
                        float(metrics["confidences"][i]),
                        bool(metrics["is_keyframe"][i]),
                        float(metrics["energies"][i]),
                        float(metrics["anchor_drifts"][i]),
                        float(metrics["trajectory_deviations"][i]),
                        float(metrics["inference_times"][i]),
                        model_name or "",
                    ]
                )

    def _dump_json(
        self,
        metrics: dict[str, np.ndarray],
        path: str,
        model_name: str | None,
    ) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_name": model_name,
            "frame_count": int(len(metrics["frame_ids"])),
            "keyframe_count": int(metrics["is_keyframe"].sum()),
            "statistics": {
                "mean_confidence": float(metrics["confidences"].mean()),
                "mean_anchor_drift": float(metrics["anchor_drifts"].mean()),
                "max_anchor_drift": float(metrics["anchor_drifts"].max()),
                "mean_trajectory_deviation": float(metrics["trajectory_deviations"].mean()),
                "max_trajectory_deviation": float(metrics["trajectory_deviations"].max()),
                "mean_inference_time_ms": float(metrics["inference_times"].mean()),
                "total_inference_time_ms": float(metrics["inference_times"].sum()),
            },
            "frames": [
                {
                    "frame_id": int(metrics["frame_ids"][i]),
                    "value": float(metrics["values"][i]) if not np.isnan(metrics["values"][i]) else None,
                    "confidence": float(metrics["confidences"][i]),
                    "is_keyframe": bool(metrics["is_keyframe"][i]),
                    "frame_energy": float(metrics["energies"][i]),
                    "anchor_drift": float(metrics["anchor_drifts"][i]),
                    "trajectory_deviation": float(metrics["trajectory_deviations"][i]),
                    "inference_time_ms": float(metrics["inference_times"][i]),
                }
                for i in range(len(metrics["frame_ids"]))
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def render_streaming_report(
    results: Sequence[Any],
    output_path: str,
    config: VisualizationConfig | None = None,
    model_name: str | None = None,
) -> dict[str, str]:
    """便捷函数：一步渲染流式推理报告.

    Parameters
    ----------
    results : Sequence[Any]
        流式推理结果列表。
    output_path : str
        PNG 输出路径。
    config : Optional[VisualizationConfig]
        渲染配置。None 时使用默认配置。
    model_name : Optional[str]
        模型名（写入报告标题）。

    Returns
    -------
    Dict[str, str]
        实际产出的文件路径。
    """
    renderer = StreamingReportRenderer(config=config)
    return renderer.render(
        results=results,
        output_path=output_path,
        model_name=model_name,
    )


__all__ = [
    "VisualizationConfig",
    "StreamingReportRenderer",
    "render_streaming_report",
    "extract_streaming_metrics",
]
