"""飞轮指标计算与报告生成模块.

对应 core-contracts-design.md 阶段 4 p4-4。

实现飞轮核心指标的计算（全部来自真实数据源）：
- 加工记录数 ``data_volume``：来自 ``feedback_records`` 数据集最新版本 row_count
- 模型质量 ``model_quality``：来自最新 ``ISnapshotStore`` 快照的 metrics['model_quality']
- 用户采纳率 ``adoption_rate``：扫描 ``feedback_records`` 中 ``feedback_type='adoption'`` 的记录
- 不确定性均值 ``uncertainty_mean``：来自最新 ``ISnapshotStore`` 快照的 metrics['uncertainty_mean']
- 回灌延迟 ``feedback_delay``：扫描反馈记录 metadata['prediction_timestamp'] 与 timestamp 差值

设计原则
---------
1. **真实数据源**：所有指标从 ``IDatasetStore`` / ``ISnapshotStore`` 取，不再硬编码。
2. **从 0 开始**：无数据源或数据集为空时，所有指标返回 0.0（系统从未运行过反馈采集）。
3. **异步采集**：核心采集方法为 ``async``，与 ``IDatasetStore`` / ``ISnapshotStore`` 契约对齐。
4. **错误容忍**：单个指标采集失败不影响其他指标（catch + log + 默认 0.0）。
5. **向后兼容**：保留同步方法为 deprecated fallback（返回零值），避免破坏旧调用点。
6. **线程安全**：``_cache`` 受锁保护；全局单例受双重检查锁保护。

依赖注入
---------
``FlywheelMetricsCollector`` 通过构造函数注入：
    - ``dataset_store``: ``IDatasetStore`` 实例（读取 feedback_records 数据集）
    - ``snapshot_store``: ``ISnapshotStore`` 实例（读取模型质量/不确定性）
    - ``feedback_dataset_id``: ``feedback_records`` 数据集 ID（可选，懒注入）

``feedback_dataset_id`` 可通过 ``set_feedback_dataset_id()`` 动态注入，
便于 ``data_flywheel`` 插件在 ``FeedbackCollector`` 首次 flush 后才解析出
dataset_id 时再注入到 ``FlywheelMetricsCollector``。
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.contracts.dataset import IDatasetStore
from app.contracts.observability import ISnapshotStore

logger = logging.getLogger(__name__)


# 常量

#: : feedback_records 数据集名称（与 feedback_collector.FEEDBACK_DATASET_NAME 对齐）
FEEDBACK_DATASET_NAME = "feedback_records"

#: : 反馈记录中标识 adoption 类型的 feedback_type 值
ADOPTION_FEEDBACK_TYPE = "adoption"

#: : 反馈记录 metadata 中存放预测时间戳的键名（用于计算 feedback_delay）
PREDICTION_TIMESTAMP_KEY = "prediction_timestamp"

#: : snapshot.metrics 中模型质量键名
MODEL_QUALITY_METRIC_KEY = "model_quality"

#: : snapshot.metrics 中不确定性均值键名
UNCERTAINTY_MEAN_METRIC_KEY = "uncertainty_mean"


# FlywheelMetrics 数据类


@dataclass
class FlywheelMetrics:
    """飞轮指标数据类."""

    data_volume: int  # 加工记录数
    model_quality: float  # 模型质量(%)
    adoption_rate: float  # 用户采纳率(%)
    uncertainty_mean: float  # 不确定性均值(0-1)
    feedback_delay: float  # 回灌延迟(分钟)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式."""
        return asdict(self)


# FlywheelMetricsCollector


class FlywheelMetricsCollector:
    """飞轮指标采集器（真实数据源版）.

    生命周期
    --------
    - 全局单例通过 ``get_flywheel_collector()`` 获取
    - 系统启动时由插件管理器或核心层注入 ``dataset_store`` / ``snapshot_store``
    - ``data_flywheel`` 插件在首次反馈 flush 后调用 ``set_feedback_dataset_id()``
    - 每次 API 请求时调用 ``collect_current_metrics_async()`` 采集最新指标

    线程安全
    --------
    - ``_cache`` / ``_feedback_dataset_id`` 受 ``_cache_lock`` 保护
    - 全局单例创建受 ``_collector_lock`` 保护
    """

    def __init__(
        self,
        dataset_store: IDatasetStore | None = None,
        snapshot_store: ISnapshotStore | None = None,
        *,
        feedback_dataset_id: str | None = None,
        data_source: Any = None,
    ) -> None:
        """初始化采集器.

        Args:
            dataset_store: ``IDatasetStore`` 实例，用于读取 feedback_records 数据集。
                为 None 时 data_volume/adoption_rate/feedback_delay 返回 0。
            snapshot_store: ``ISnapshotStore`` 实例，用于读取模型质量/不确定性。
                为 None 时 model_quality/uncertainty_mean 返回 0。
            feedback_dataset_id: feedback_records 数据集 ID。可选，可通过
                ``set_feedback_dataset_id()`` 懒注入。
            data_source: 废弃参数（旧版本兼容），仅记录警告。
        """
        self._dataset_store = dataset_store
        self._snapshot_store = snapshot_store
        self._feedback_dataset_id: str | None = feedback_dataset_id
        if data_source is not None:
            logger.warning("data_source 参数已废弃，请通过 dataset_store + snapshot_store 注入")
        self._cache: dict[str, FlywheelMetrics] = {}
        self._cache_lock = threading.Lock()

    # 依赖注入 API

    @property
    def dataset_store(self) -> IDatasetStore | None:
        return self._dataset_store

    @property
    def snapshot_store(self) -> ISnapshotStore | None:
        return self._snapshot_store

    @property
    def feedback_dataset_id(self) -> str | None:
        """当前已注入的 feedback_records 数据集 ID（None 表示尚未注入）."""
        return self._feedback_dataset_id

    def set_feedback_dataset_id(self, dataset_id: str) -> None:
        """动态注入 feedback_records 数据集 ID.

        供 ``data_flywheel`` 插件在 ``FeedbackCollector`` 首次 flush 后调用，
        把解析出的 dataset_id 注入到 ``FlywheelMetricsCollector``。

        Args:
            dataset_id: feedback_records 数据集 ID（非空字符串）
        """
        if not dataset_id or not isinstance(dataset_id, str):
            raise ValueError(f"dataset_id 必须为非空字符串，收到: {dataset_id!r}")
        with self._cache_lock:
            self._feedback_dataset_id = dataset_id
        logger.info("feedback_dataset_id 已注入: %s", dataset_id)

    # 异步采集：真实数据源

    async def collect_current_metrics_async(self) -> FlywheelMetrics:
        """异步采集当前飞轮指标（全部来自真实数据源）.

        单个指标采集失败不影响其他指标（catch + log + 默认 0.0）。

        Returns:
            ``FlywheelMetrics`` 实例。无数据源或数据为空时所有字段返回 0。
        """
        data_volume = await self._get_data_volume_async()
        model_quality = await self._get_model_quality_async()
        adoption_rate = await self._get_adoption_rate_async()
        uncertainty_mean = await self._get_uncertainty_mean_async()
        feedback_delay = await self._get_feedback_delay_async()

        metrics = FlywheelMetrics(
            data_volume=data_volume,
            model_quality=model_quality,
            adoption_rate=adoption_rate,
            uncertainty_mean=uncertainty_mean,
            feedback_delay=feedback_delay,
        )

        with self._cache_lock:
            self._cache[metrics.timestamp] = metrics
        return metrics

    async def get_historical_metrics_async(self, days: int = 7) -> list[FlywheelMetrics]:
        """从 ``ISnapshotStore`` 取历史快照构造历史指标.

        Args:
            days: 查询天数范围（默认 7 天）

        Returns:
            历史指标列表（按时间降序，最近在前）。无 snapshot_store 或无快照
            时返回空列表。
        """
        if self._snapshot_store is None:
            logger.debug("snapshot_store 为 None，历史指标返回空列表")
            return []

        try:
            snapshots = await self._snapshot_store.list()
        except Exception as e:
            logger.error("列出快照失败: %s", e, exc_info=True)
            return []

        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(days=days)

        historical: list[FlywheelMetrics] = []
        for snap in snapshots:
            # 处理 naive datetime（ISnapshotStore 实现可能返回 naive UTC）
            created = snap.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            if created < cutoff:
                continue

            historical.append(
                FlywheelMetrics(
                    data_volume=int(snap.metrics.get("data_volume", 0)),
                    model_quality=float(snap.metrics.get("model_quality", 0.0)),
                    adoption_rate=float(snap.metrics.get("adoption_rate", 0.0)),
                    uncertainty_mean=float(snap.metrics.get("uncertainty_mean", 0.0)),
                    feedback_delay=float(snap.metrics.get("feedback_delay", 0.0)),
                    timestamp=created.isoformat(),
                )
            )

        # 按时间降序（最近在前）
        historical.sort(key=lambda m: m.timestamp, reverse=True)
        return historical

    # 同步 fallback（deprecated，返回零值或基于缓存）

    def collect_current_metrics(self) -> FlywheelMetrics:
        """同步采集当前指标（deprecated，返回零值）.

        .. deprecated::
            真实数据源为异步接口，请使用 ``collect_current_metrics_async()``。
            本方法仅返回零值 ``FlywheelMetrics``（兼容旧调用点，不抛错）。
        """
        warnings.warn(
            "collect_current_metrics() 已废弃，请使用 collect_current_metrics_async()",
            DeprecationWarning,
            stacklevel=2,
        )
        return FlywheelMetrics(
            data_volume=0,
            model_quality=0.0,
            adoption_rate=0.0,
            uncertainty_mean=0.0,
            feedback_delay=0.0,
        )

    def get_historical_metrics(self, days: int = 7) -> list[FlywheelMetrics]:
        """同步获取历史指标（deprecated，返回空列表）.

        .. deprecated::
            请使用 ``get_historical_metrics_async()``。
        """
        warnings.warn(
            "get_historical_metrics() 已废弃，请使用 get_historical_metrics_async()",
            DeprecationWarning,
            stacklevel=2,
        )
        return []

    # 周报生成（异步）

    async def generate_weekly_report_async(self) -> dict[str, Any]:
        """异步生成每周飞轮报告.

        Returns:
            包含周度数据和趋势分析的字典
        """
        current = await self.collect_current_metrics_async()
        historical = await self.get_historical_metrics_async(days=7)

        trends = self._calculate_trends(historical)

        report = {
            "report_type": "weekly",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "period": {
                "start": historical[-1].timestamp if historical else current.timestamp,
                "end": current.timestamp,
            },
            "current_metrics": current.to_dict(),
            "historical_metrics": [m.to_dict() for m in historical],
            "trends": trends,
            "summary": self._generate_summary(current, trends),
        }

        return report

    def generate_weekly_report(self) -> dict[str, Any]:
        """同步生成周报（deprecated）.

        .. deprecated::
            请使用 ``generate_weekly_report_async()``。本方法返回零值报告。
        """
        warnings.warn(
            "generate_weekly_report() 已废弃，请使用 generate_weekly_report_async()",
            DeprecationWarning,
            stacklevel=2,
        )
        current = FlywheelMetrics(
            data_volume=0,
            model_quality=0.0,
            adoption_rate=0.0,
            uncertainty_mean=0.0,
            feedback_delay=0.0,
        )
        trends = self._calculate_trends([])
        return {
            "report_type": "weekly",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "period": {"start": current.timestamp, "end": current.timestamp},
            "current_metrics": current.to_dict(),
            "historical_metrics": [],
            "trends": trends,
            "summary": self._generate_summary(current, trends),
        }

    # 内部：5 个指标的真实数据采集

    async def _get_data_volume_async(self) -> int:
        """获取加工记录数（feedback_records 最新版本 row_count）."""
        if self._dataset_store is None or self._feedback_dataset_id is None:
            return 0
        try:
            version = await self._dataset_store.get_version(self._feedback_dataset_id)
            return int(version.row_count)
        except Exception as e:
            logger.warning(
                "采集 data_volume 失败（dataset_id=%s）: %s",
                self._feedback_dataset_id,
                e,
            )
            return 0

    async def _get_model_quality_async(self) -> float:
        """获取模型质量（最新 snapshot.metrics['model_quality']，0-100）."""
        snapshot = await self._get_latest_snapshot_async()
        if snapshot is None:
            return 0.0
        try:
            return float(snapshot.metrics.get(MODEL_QUALITY_METRIC_KEY, 0.0))
        except (TypeError, ValueError) as e:
            logger.warning("解析 model_quality 失败: %s", e)
            return 0.0

    async def _get_adoption_rate_async(self) -> float:
        """获取用户采纳率（扫描 adoption 类型反馈，accepted=True 比例，0-100）."""
        records = await self._read_feedback_records_async()
        if not records:
            return 0.0

        adoption_records = [r for r in records if r.get("feedback_type") == ADOPTION_FEEDBACK_TYPE]
        if not adoption_records:
            return 0.0

        accepted_count = sum(1 for r in adoption_records if r.get("accepted") is True)
        return round(accepted_count / len(adoption_records) * 100, 2)

    async def _get_uncertainty_mean_async(self) -> float:
        """获取不确定性均值（最新 snapshot.metrics['uncertainty_mean']，0-1）."""
        snapshot = await self._get_latest_snapshot_async()
        if snapshot is None:
            return 0.0
        try:
            return float(snapshot.metrics.get(UNCERTAINTY_MEAN_METRIC_KEY, 0.0))
        except (TypeError, ValueError) as e:
            logger.warning("解析 uncertainty_mean 失败: %s", e)
            return 0.0

    async def _get_feedback_delay_async(self) -> float:
        """获取回灌延迟（反馈时间 - 预测时间 的平均分钟数）.

        扫描 ``metadata[prediction_timestamp]`` 字段（ISO8601），计算与
        ``timestamp`` 的差值（分钟）。无 prediction_timestamp 的记录跳过。
        """
        records = await self._read_feedback_records_async()
        if not records:
            return 0.0

        delays: list[float] = []
        for r in records:
            meta = r.get("metadata") or {}
            pred_ts_str = meta.get(PREDICTION_TIMESTAMP_KEY)
            if not pred_ts_str:
                continue
            try:
                pred_ts = _parse_iso8601(pred_ts_str)
                feedback_ts = _parse_iso8601(r.get("timestamp", ""))
            except (ValueError, TypeError) as e:
                logger.debug("解析时间戳失败（跳过记录）: %s", e)
                continue
            if pred_ts is None or feedback_ts is None:
                continue
            delta_seconds = (feedback_ts - pred_ts).total_seconds()
            if delta_seconds < 0:
                # 预测时间晚于反馈时间（数据异常），跳过
                continue
            delays.append(delta_seconds / 60.0)

        if not delays:
            return 0.0
        return round(sum(delays) / len(delays), 2)

    # 内部：辅助

    async def _get_latest_snapshot_async(self) -> Any | None:
        """获取最新实验快照（按 created_at 降序的第一个）."""
        if self._snapshot_store is None:
            return None
        try:
            snapshots = await self._snapshot_store.list()
        except Exception as e:
            logger.warning("列出快照失败: %s", e)
            return None
        if not snapshots:
            return None
        # 按 created_at 降序，取第一个
        return sorted(
            snapshots,
            key=lambda s: (
                s.created_at if s.created_at.tzinfo is not None else s.created_at.replace(tzinfo=datetime.timezone.utc)
            ),
            reverse=True,
        )[0]

    async def _read_feedback_records_async(self) -> list[dict[str, Any]]:
        """读取 feedback_records 数据集的全部记录（流式聚合）."""
        if self._dataset_store is None or self._feedback_dataset_id is None:
            return []
        try:
            records: list[dict[str, Any]] = []
            async for batch in self._dataset_store.read(self._feedback_dataset_id):
                records.extend(batch)
            return records
        except Exception as e:
            logger.warning(
                "读取 feedback_records 失败（dataset_id=%s）: %s",
                self._feedback_dataset_id,
                e,
            )
            return []

    # 纯函数：趋势与摘要计算（保持向后兼容）

    def _calculate_trends(self, historical: list[FlywheelMetrics]) -> dict[str, Any]:
        """计算指标趋势."""
        if len(historical) < 2:
            return {}

        latest = historical[0]
        oldest = historical[-1]

        return {
            "data_volume": {
                "current": latest.data_volume,
                "change": latest.data_volume - oldest.data_volume,
                "change_percent": ((latest.data_volume - oldest.data_volume) / oldest.data_volume * 100)
                if oldest.data_volume > 0
                else 0,
            },
            "model_quality": {
                "current": latest.model_quality,
                "change": round(latest.model_quality - oldest.model_quality, 2),
                "change_percent": round(
                    (latest.model_quality - oldest.model_quality) / oldest.model_quality * 100,
                    2,
                )
                if oldest.model_quality > 0
                else 0,
            },
            "adoption_rate": {
                "current": latest.adoption_rate,
                "change": round(latest.adoption_rate - oldest.adoption_rate, 2),
                "change_percent": round(
                    (latest.adoption_rate - oldest.adoption_rate) / oldest.adoption_rate * 100,
                    2,
                )
                if oldest.adoption_rate > 0
                else 0,
            },
            "uncertainty_mean": {
                "current": latest.uncertainty_mean,
                "change": round(latest.uncertainty_mean - oldest.uncertainty_mean, 3),
                "trend": "improving" if latest.uncertainty_mean < oldest.uncertainty_mean else "degrading",
            },
            "feedback_delay": {
                "current": latest.feedback_delay,
                "change": round(latest.feedback_delay - oldest.feedback_delay, 2),
                "trend": "improving" if latest.feedback_delay < oldest.feedback_delay else "degrading",
            },
        }

    def _generate_summary(self, current: FlywheelMetrics, trends: dict[str, Any]) -> dict[str, Any]:
        """生成报告摘要."""
        health_score = self._calculate_health_score(current, trends)

        return {
            "health_score": round(health_score, 1),
            "health_status": self._get_health_status(health_score),
            "highlights": self._generate_highlights(current, trends),
            "recommendations": self._generate_recommendations(current, trends),
        }

    def _calculate_health_score(self, current: FlywheelMetrics, trends: dict[str, Any]) -> float:
        """计算飞轮健康分数(0-100)."""
        score = 0.0

        # 模型质量权重 30%
        score += min(current.model_quality, 100) * 0.3

        # 用户采纳率权重 25%
        score += min(current.adoption_rate, 100) * 0.25

        # 不确定性权重 20% (越低越好)
        uncertainty_score = (1 - current.uncertainty_mean) * 100
        score += uncertainty_score * 0.2

        # 回灌延迟权重 15% (越低越好)
        delay_score = max(0, 100 - current.feedback_delay)
        score += delay_score * 0.15

        # 数据量权重 10%
        volume_score = min(current.data_volume / 100, 100)
        score += volume_score * 0.1

        return score

    def _get_health_status(self, score: float) -> str:
        """根据分数返回健康状态."""
        if score >= 80:
            return "excellent"
        elif score >= 60:
            return "good"
        elif score >= 40:
            return "fair"
        else:
            return "poor"

    def _generate_highlights(self, current: FlywheelMetrics, trends: dict[str, Any]) -> list[str]:
        """生成亮点摘要."""
        highlights: list[str] = []

        if current.model_quality >= 85:
            highlights.append(f"模型质量优秀，达到 {current.model_quality:.1f}%")

        if current.adoption_rate >= 70:
            highlights.append(f"用户采纳率良好，达到 {current.adoption_rate:.1f}%")

        if trends.get("model_quality", {}).get("change", 0) > 0:
            highlights.append("模型质量呈上升趋势")

        if trends.get("adoption_rate", {}).get("change", 0) > 0:
            highlights.append("用户采纳率持续提升")

        return highlights

    def _generate_recommendations(self, current: FlywheelMetrics, trends: dict[str, Any]) -> list[str]:
        """生成改进建议."""
        recommendations: list[str] = []

        if current.model_quality < 80:
            recommendations.append("建议优化模型训练流程，提升预测准确率")

        if current.adoption_rate < 60:
            recommendations.append("建议改进模型建议的可解释性，提升用户采纳率")

        if current.uncertainty_mean > 0.3:
            recommendations.append("模型不确定性较高，建议增加训练数据或优化模型结构")

        if current.feedback_delay > 60:
            recommendations.append("回灌延迟较长，建议优化数据处理流程")

        if not recommendations:
            recommendations.append("飞轮运转良好，继续保持当前策略")

        return recommendations


# 辅助函数


def _parse_iso8601(ts: str) -> datetime.datetime | None:
    """解析 ISO8601 时间戳字符串，返回 timezone-aware datetime.

    支持的格式：
        - ``2026-07-13T12:34:56+00:00`` (带时区)
        - ``2026-07-13T12:34:56`` (naive，按 UTC 处理)
        - ``2026-07-13T12:34:56.789012+00:00`` (带微秒)

    Returns:
        timezone-aware datetime。解析失败返回 None。
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


# 报告保存


def save_report_to_file(report: dict[str, Any], output_dir: str | Path) -> Path:
    """将报告保存到文件.

    Args:
        report: 报告数据
        output_dir: 输出目录

    Returns:
        报告文件路径
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"flywheel_report_{timestamp}.json"
    filepath = output_dir / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info("Report saved to: %s", filepath)
    return filepath


# 全局单例

_collector: FlywheelMetricsCollector | None = None
_collector_lock = threading.Lock()


def get_flywheel_collector() -> FlywheelMetricsCollector:
    """获取全局飞轮指标采集器实例.

    返回的实例默认无数据源（``dataset_store=None`` / ``snapshot_store=None``）。
    系统启动时由插件管理器调用 ``configure_flywheel_collector()`` 注入数据源。
    """
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = FlywheelMetricsCollector()
    return _collector


def configure_flywheel_collector(
    *,
    dataset_store: IDatasetStore | None = None,
    snapshot_store: ISnapshotStore | None = None,
    feedback_dataset_id: str | None = None,
) -> FlywheelMetricsCollector:
    """配置全局飞轮采集器（注入真实数据源）.

    系统启动时由插件管理器或核心层调用，把 ``IDatasetStore`` / ``ISnapshotStore``
    注入到全局单例。已存在的单例会被替换（避免单例状态污染）。

    Args:
        dataset_store: 数据集存储实例
        snapshot_store: 快照存储实例
        feedback_dataset_id: feedback_records 数据集 ID（可选）

    Returns:
        配置后的全局采集器实例
    """
    global _collector
    with _collector_lock:
        _collector = FlywheelMetricsCollector(
            dataset_store=dataset_store,
            snapshot_store=snapshot_store,
            feedback_dataset_id=feedback_dataset_id,
        )
    return _collector


def reset_flywheel_collector() -> None:
    """重置全局采集器（仅供测试使用）."""
    global _collector
    with _collector_lock:
        _collector = None


# CLI


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="飞轮指标报告生成器")
    parser.add_argument(
        "--report",
        choices=["weekly"],
        default="weekly",
        help="报告类型",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="reports",
        help="报告输出目录",
    )

    args = parser.parse_args()

    collector = get_flywheel_collector()

    if args.report == "weekly":
        # CLI 模式下无数据源，返回零值报告（兼容旧行为）
        report = collector.generate_weekly_report()
        filepath = save_report_to_file(report, args.output_dir)
        logger.info("Weekly report generated: %s", filepath)
