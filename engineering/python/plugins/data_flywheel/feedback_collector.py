"""数据飞轮 - 反馈采集层.

对应 core-contracts-design.md 阶段 4 p4-2。

将用户对模型预测结果的反馈（标注 / 采纳 / 修正）写入契约化 ``IDatasetStore``，
为后续模型迭代（p4-3）与飞轮指标（p4-4）提供真实数据源。

设计原则
---------
1. **统一数据集**：三种反馈类型写入同一 ``feedback_records`` 数据集，
   通过 ``feedback_type`` 字段区分。避免数据集爆炸，便于跨类型聚合统计。
2. **批量提交**：内存缓冲区达到 ``batch_size`` 或显式 ``flush()`` 时才
   ``commit_version()``。每条反馈立即提交会导致版本爆炸（IDatasetStore
   版本不可变，每次 commit 都产生新 content_hash 文件）。
3. **懒注册**：首次 flush 时才 ``create()`` 数据集，避免插件加载即占资源。
   ``dataset_id`` 缓存后复用。
4. **lineage 关联**：每次 flush 生成 ``LineageRecord``（source_type=manual,
   operation=feedback_collection），保证反馈数据可追溯到飞轮插件。
5. **并发安全**：``flush`` 使用 ``threading.Lock`` 保护，防止并发触发
   产生重复版本或缓冲区状态错乱（与 audit_log / LNN predictor 一致模式）。
6. **降级**：``PluginContext.dataset_store`` 为 None 时构造抛错，
   ``record_*`` 方法在 store 不可用时仅记录警告不抛错（反馈丢失优于阻塞主流程）。

反馈类型
---------
- ``annotation``：用户对预测结果添加的标注（如正确/错误/类别标签）
- ``adoption``：用户是否采纳模型建议（accepted: bool）
- ``correction``：用户对模型输出的具体修正内容（original → corrected）

Schema
------
    feedback_id        str      反馈唯一 ID（uuid4，主键）
    timestamp          datetime 反馈时间戳（UTC）
    user_id            str      反馈用户 ID
    feedback_type      str      annotation | adoption | correction
    prediction_id      str      对应的预测 ID（可选）
    model_version      str      被反馈的模型版本（可选）
    original_output    dict     原始模型输出
    corrected_output   dict     用户修正后的输出（correction 必填）
    accepted           bool     是否采纳（adoption 必填）
    notes              str      用户备注
    metadata           dict     扩展元数据
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.contracts.dataset import (
    DatasetSchema,
    DatasetVersion,
    IDatasetStore,
    LineageRecord,
)

logger = logging.getLogger(__name__)


# 常量与 Schema

#: : 反馈数据集名称（全局唯一，多次实例化复用同一数据集）
FEEDBACK_DATASET_NAME = "feedback_records"

#: : 三种合法反馈类型
VALID_FEEDBACK_TYPES = frozenset({"annotation", "adoption", "correction"})

#: : 反馈数据集 schema（与 DatasetSchema.validate 兼容）
FEEDBACK_DATASET_SCHEMA = DatasetSchema(
    fields={
        "feedback_id": {
            "type": "str",
            "required": True,
            "description": "反馈唯一 ID（uuid4）",
        },
        "timestamp": {
            "type": "datetime",
            "required": True,
            "description": "反馈时间戳（UTC ISO8601）",
        },
        "user_id": {
            "type": "str",
            "required": True,
            "description": "反馈用户 ID",
        },
        "feedback_type": {
            "type": "str",
            "required": True,
            "description": "annotation | adoption | correction",
        },
        "prediction_id": {
            "type": "str",
            "required": False,
            "description": "对应的预测 ID",
        },
        "model_version": {
            "type": "str",
            "required": False,
            "description": "被反馈的模型版本",
        },
        "original_output": {
            "type": "dict",
            "required": False,
            "description": "原始模型输出",
        },
        "corrected_output": {
            "type": "dict",
            "required": False,
            "description": "用户修正后的输出（correction 类型必填）",
        },
        "accepted": {
            "type": "bool",
            "required": False,
            "description": "是否采纳（adoption 类型必填）",
        },
        "notes": {
            "type": "str",
            "required": False,
            "description": "用户备注",
        },
        "metadata": {
            "type": "dict",
            "required": False,
            "description": "扩展元数据",
        },
    },
    primary_key=["feedback_id"],
    metadata={
        "source": "data_flywheel.feedback_collector",
        "schema_version": "1.0.0",
    },
)


# 默认配置（与 plugin.yaml 的 feedback_collection section 对齐）

_DEFAULT_CONFIG = {
    "window_hours": 24,
    "min_samples_for_training": 50,
    "batch_size": 100,
}


# FeedbackCollector


class FeedbackCollector:
    """反馈采集器：缓冲 → 批量写入 IDatasetStore.

    生命周期由 Plugin 管理：
        - ``Plugin.on_load`` 时构造（注入 ``PluginContext.dataset_store`` 与 config）
        - ``Plugin.on_unload`` 时调用 ``flush()`` 落盘剩余缓冲区

    线程安全：``flush`` 受 ``threading.Lock`` 保护；``record_*`` 方法仅修改
    内存缓冲区，使用 GIL 保证的 list.append 原子性，无需额外锁。
    """

    def __init__(
        self,
        dataset_store: Optional[IDatasetStore],
        *,
        owner_id: str = "plugin:data_flywheel",
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        """初始化反馈采集器.

        Args:
            dataset_store: IDatasetStore 实例（来自 PluginContext.dataset_store）。
                为 None 时 ``record_*`` 方法降级为仅日志记录，``flush`` 抛错。
            owner_id: 数据集 owner，用于 lineage 与 audit。默认 ``plugin:data_flywheel``。
            config: feedback_collection 配置 section（来自 plugin.yaml）。
                支持键：window_hours / min_samples_for_training / batch_size。
        """
        self._store = dataset_store
        self._owner_id = owner_id
        merged = dict(_DEFAULT_CONFIG)
        if config:
            for k in _DEFAULT_CONFIG:
                if k in config and config[k] is not None:
                    merged[k] = config[k]
        self._config = merged

        self._buffer: list[dict[str, Any]] = []
        self._buffer_lock = threading.Lock()
        self._flush_lock = threading.Lock()
        self._dataset_id: Optional[str] = None  # 懒注册
        self._last_flush_at: Optional[datetime] = None
        self._total_recorded = 0
        self._total_flushed = 0

        if self._store is None:
            logger.warning("FeedbackCollector 初始化时 dataset_store=None，反馈将仅记录日志不落盘（降级模式）")

    # 属性

    @property
    def config(self) -> dict[str, Any]:
        """当前配置（只读视图）."""
        return dict(self._config)

    @property
    def buffer_size(self) -> int:
        """当前缓冲区长度."""
        with self._buffer_lock:
            return len(self._buffer)

    @property
    def dataset_id(self) -> Optional[str]:
        """已注册的 dataset_id（未 flush 前为 None）."""
        return self._dataset_id

    @property
    def total_recorded(self) -> int:
        """累计记录的反馈数（含未落盘的缓冲区）."""
        return self._total_recorded

    @property
    def total_flushed(self) -> int:
        """累计已落盘的反馈数."""
        return self._total_flushed

    # 公开 API：三种反馈类型

    async def record_annotation(
        self,
        *,
        user_id: str,
        prediction_id: Optional[str] = None,
        model_version: Optional[str] = None,
        original_output: Optional[dict[str, Any]] = None,
        notes: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """记录用户标注反馈.

        用户对模型预测结果添加的标注（正确/错误/类别标签等）。
        与 adoption 区别：annotation 不涉及采纳决策，只是补充信息。

        Args:
            user_id: 反馈用户 ID（必填）
            prediction_id: 对应预测 ID
            model_version: 被反馈的模型版本
            original_output: 原始模型输出
            notes: 用户备注
            metadata: 扩展元数据

        Returns:
            feedback_id
        """
        return await self._record(
            feedback_type="annotation",
            user_id=user_id,
            prediction_id=prediction_id,
            model_version=model_version,
            original_output=original_output,
            corrected_output=None,
            accepted=None,
            notes=notes,
            metadata=metadata,
        )

    async def record_adoption(
        self,
        *,
        user_id: str,
        accepted: bool,
        prediction_id: Optional[str] = None,
        model_version: Optional[str] = None,
        original_output: Optional[dict[str, Any]] = None,
        notes: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """记录用户采纳/拒绝反馈.

        用户是否采纳了模型建议。``accepted=True`` 表示采纳，``False`` 表示拒绝。
        此字段是飞轮指标 ``adoption_rate`` 的核心数据源（p4-4 计算）。

        Args:
            user_id: 反馈用户 ID（必填）
            accepted: 是否采纳（必填）
            prediction_id: 对应预测 ID
            model_version: 被反馈的模型版本
            original_output: 原始模型输出
            notes: 用户备注
            metadata: 扩展元数据

        Returns:
            feedback_id
        """
        if not isinstance(accepted, bool):
            raise ValueError(f"accepted 必须为 bool 类型，收到: {type(accepted).__name__}")
        return await self._record(
            feedback_type="adoption",
            user_id=user_id,
            prediction_id=prediction_id,
            model_version=model_version,
            original_output=original_output,
            corrected_output=None,
            accepted=accepted,
            notes=notes,
            metadata=metadata,
        )

    async def record_correction(
        self,
        *,
        user_id: str,
        original_output: dict[str, Any],
        corrected_output: dict[str, Any],
        prediction_id: Optional[str] = None,
        model_version: Optional[str] = None,
        notes: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """记录用户修正反馈.

        用户对模型输出的具体修正内容。original_output 与 corrected_output
        同时记录，便于 p4-3 模型迭代时计算修正距离与微调目标。

        Args:
            user_id: 反馈用户 ID（必填）
            original_output: 原始模型输出（必填）
            corrected_output: 用户修正后的输出（必填）
            prediction_id: 对应预测 ID
            model_version: 被反馈的模型版本
            notes: 用户备注
            metadata: 扩展元数据

        Returns:
            feedback_id
        """
        if not original_output:
            raise ValueError("correction 类型必须提供 original_output")
        if not corrected_output:
            raise ValueError("correction 类型必须提供 corrected_output")
        return await self._record(
            feedback_type="correction",
            user_id=user_id,
            prediction_id=prediction_id,
            model_version=model_version,
            original_output=original_output,
            corrected_output=corrected_output,
            accepted=None,
            notes=notes,
            metadata=metadata,
        )

    # 内部：通用记录逻辑

    async def _record(
        self,
        *,
        feedback_type: str,
        user_id: str,
        prediction_id: Optional[str],
        model_version: Optional[str],
        original_output: Optional[dict[str, Any]],
        corrected_output: Optional[dict[str, Any]],
        accepted: Optional[bool],
        notes: str,
        metadata: Optional[dict[str, Any]],
    ) -> str:
        """通用记录入口：构造反馈记录并入缓冲区，必要时触发 flush."""
        if feedback_type not in VALID_FEEDBACK_TYPES:
            raise ValueError(f"feedback_type 不合法: {feedback_type}，合法值: {VALID_FEEDBACK_TYPES}")
        if not user_id:
            raise ValueError("user_id 不能为空")

        feedback_id = f"fb-{uuid.uuid4().hex}"
        record: dict[str, Any] = {
            "feedback_id": feedback_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "feedback_type": feedback_type,
            "prediction_id": prediction_id,
            "model_version": model_version,
            "original_output": original_output or {},
            "corrected_output": corrected_output or {},
            "accepted": accepted,
            "notes": notes or "",
            "metadata": metadata or {},
        }

        should_flush = False
        with self._buffer_lock:
            self._buffer.append(record)
            self._total_recorded += 1
            if len(self._buffer) >= self._config["batch_size"]:
                should_flush = True

        if should_flush:
            try:
                await self.flush()
            except Exception as e:  # noqa: BLE001
                # flush 失败不阻塞 record_*（反馈已入缓冲区，下次重试）
                logger.error(
                    "FeedbackCollector 自动 flush 失败（缓冲区仍有 %d 条）: %s",
                    self.buffer_size,
                    e,
                    exc_info=True,
                )

        if self._store is None:
            logger.info(
                "FeedbackCollector 降级模式：反馈 %s[%s] 仅记录未落盘",
                feedback_id,
                feedback_type,
            )
        else:
            logger.debug(
                "反馈已入缓冲区: id=%s type=%s user=%s buffer=%d/%d",
                feedback_id,
                feedback_type,
                user_id,
                self.buffer_size,
                self._config["batch_size"],
            )
        return feedback_id

    # flush：批量提交到 IDatasetStore

    async def flush(self) -> Optional[DatasetVersion]:
        """将缓冲区中的反馈批量提交为一个新版本.

        - 缓冲区为空时直接返回 None（不产生空版本）
        - ``dataset_store`` 为 None 时抛出 RuntimeError（显式 flush 不应降级）
        - 首次 flush 时懒注册数据集（``create``）
        - 后续 flush 自动递增 patch 版本号（由 DatasetStore.commit_version 处理）
        - 关联 ``LineageRecord`` 便于血缘追溯

        Returns:
            新提交的 DatasetVersion，或缓冲区为空时返回 None

        Raises:
            RuntimeError: dataset_store 未注入
        """
        if self._store is None:
            raise RuntimeError("FeedbackCollector.dataset_store 为 None，无法 flush（降级模式）")

        with self._flush_lock:
            # 取出缓冲区快照（在锁内操作，避免并发 flush 拿到不同切片）
            with self._buffer_lock:
                if not self._buffer:
                    logger.debug("flush 调用但缓冲区为空，跳过")
                    return None
                records_to_flush = list(self._buffer)
                self._buffer.clear()

            try:
                dataset_id = await self._ensure_dataset()
                lineage = self._build_lineage(len(records_to_flush))
                version = await self._store.commit_version(
                    dataset_id=dataset_id,
                    records=records_to_flush,
                    lineage=lineage,
                )
                self._last_flush_at = datetime.now(timezone.utc)
                self._total_flushed += len(records_to_flush)
                logger.info(
                    "FeedbackCollector flush 成功: dataset_id=%s version=%s rows=%d total_flushed=%d",
                    dataset_id,
                    version.version,
                    len(records_to_flush),
                    self._total_flushed,
                )
                return version
            except Exception:
                # flush 失败：把记录放回缓冲区头部，下次 flush 重试
                with self._buffer_lock:
                    self._buffer = records_to_flush + self._buffer
                logger.error(
                    "FeedbackCollector flush 失败，%d 条记录已放回缓冲区",
                    len(records_to_flush),
                    exc_info=True,
                )
                raise

    async def _ensure_dataset(self) -> str:
        """懒注册数据集，返回 dataset_id（缓存后复用）.

        首次调用 ``create()``，若 name 已存在（多次插件实例化）则按
        ``TrainingDataLakeAdapter`` 一致策略：复用稳定 ID。
        """
        if self._dataset_id is not None:
            return self._dataset_id

        try:
            new_id = await self._store.create(
                name=FEEDBACK_DATASET_NAME,
                schema=FEEDBACK_DATASET_SCHEMA,
                owner_id=self._owner_id,
                description=("数据飞轮反馈采集层数据集（用户标注/采纳/修正记录）"),
            )
            self._dataset_id = new_id
            logger.info(
                "FeedbackCollector 注册新数据集: id=%s name=%s",
                new_id,
                FEEDBACK_DATASET_NAME,
            )
        except Exception as e:  # noqa: BLE001
            # name 唯一约束冲突 复用稳定 ID（与 TrainingDataLakeAdapter 一致）。
            # 前提：store 已按同一稳定规则存在 fb- 数据集（另一实例创建），
            # 否则 flush 会提交失败并放回缓冲区（ERROR 日志，数据不丢）。
            import hashlib

            stable_id = "fb-" + hashlib.sha256(FEEDBACK_DATASET_NAME.encode("utf-8")).hexdigest()[:16]
            logger.warning(
                "FeedbackCollector create 失败（%s），复用 stable_id=%s",
                e,
                stable_id,
            )
            self._dataset_id = stable_id
        return self._dataset_id

    def _build_lineage(self, record_count: int) -> LineageRecord:
        """构造本次 flush 的 LineageRecord."""
        return LineageRecord(
            record_id=f"lineage-{uuid.uuid4().hex}",
            target=f"dataset://{FEEDBACK_DATASET_NAME}/latest",
            source_type="manual",
            source_ref=f"{self._owner_id}:feedback_collector",
            inputs=[],  # 反馈来自用户交互，无上游 artifact
            outputs=[f"dataset://{FEEDBACK_DATASET_NAME}/latest"],
            operation="feedback_collection",
            metadata={
                "record_count": record_count,
                "owner_id": self._owner_id,
                "feedback_types_collected": list({r["feedback_type"] for r in self._buffer}),
            },
        )

    # 读取：供 p4-4 飞轮指标使用

    async def get_recent_feedback(self, *, hours: Optional[int] = None) -> list[dict[str, Any]]:
        """读取最近 N 小时内的反馈记录.

        从最新版本的反馈数据集中读取全部记录，按 timestamp 过滤时间窗口。
        用于 p4-4 飞轮指标计算（adoption_rate / feedback_delay）。

        Args:
            hours: 时间窗口（小时），默认取 config.window_hours

        Returns:
            时间窗口内的反馈记录列表（按 timestamp 升序）

        Raises:
            RuntimeError: dataset_store 未注入或数据集未注册
        """
        if self._store is None:
            raise RuntimeError("dataset_store 为 None，无法读取反馈")
        if self._dataset_id is None:
            logger.warning("数据集未注册（从未 flush），返回空列表")
            return []

        window_hours = hours if hours is not None else self._config["window_hours"]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        records: list[dict[str, Any]] = []
        async for batch in self._store.read(self._dataset_id, batch_size=500):
            for rec in batch:
                ts_str = rec.get("timestamp")
                if not ts_str:
                    continue
                try:
                    # 兼容带时区与不带时区的 ISO 时间戳
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= cutoff:
                        records.append(rec)
                except (ValueError, TypeError):
                    logger.warning("跳过无法解析的 timestamp: %s", ts_str)
                    continue

        records.sort(key=lambda r: r.get("timestamp", ""))
        return records

    async def get_stats(self) -> dict[str, Any]:
        """返回采集器统计信息（供 health_check 与飞轮看板使用）."""
        return {
            "buffer_size": self.buffer_size,
            "total_recorded": self._total_recorded,
            "total_flushed": self._total_flushed,
            "dataset_id": self._dataset_id,
            "last_flush_at": self._last_flush_at.isoformat() if self._last_flush_at else None,
            "dataset_store_available": self._store is not None,
            "config": dict(self._config),
        }


__all__ = [
    "FEEDBACK_DATASET_NAME",
    "FEEDBACK_DATASET_SCHEMA",
    "VALID_FEEDBACK_TYPES",
    "FeedbackCollector",
]
