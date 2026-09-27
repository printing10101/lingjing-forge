"""实测数据回灌管线

实现加工记录到知识图谱和训练数据的回灌流程，包括：
- 异步处理机制：非阻塞式数据回灌
- 数据去重策略：基于record_id的幂等处理
- 失败重试机制：任务队列与自动重试
- 模块化设计：低耦合、高内聚

设计原则：
- 只负责数据搬运，不包含数据分析或决策逻辑
- 异步执行，不阻塞主加工流程
- 基于record_id实现严格去重
- 实现消息队列与失败重试机制
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, cast

from app.knowledge_graph.feedback_updater import FeedbackUpdater
from app.training.data_lake import TrainingDataLake

logger = logging.getLogger(__name__)


class FeedbackLoopError(Exception):
    """回灌流程异常"""

    pass


class FeedbackTask:
    """回灌任务

    封装单个加工记录的回灌任务，包括：
    - 任务ID
    - 加工记录数据
    - 重试次数
    - 任务状态
    """

    def __init__(self, record: dict[str, Any], task_id: str | None = None):
        self.task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        self.record = record
        self.retry_count = 0
        self.max_retries = 3
        self.status = "pending"  # pending, processing, completed, failed
        self.error_message: str | None = None
        self.created_at = datetime.now(timezone.utc)
        self.processed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "task_id": self.task_id,
            "record_id": self.record.get("record_id"),
            "retry_count": self.retry_count,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
        }


class FeedbackLoopPipeline:
    """回灌管线

    负责协调知识图谱更新和训练数据存储，实现：
    - 异步任务处理
    - 失败重试机制
    - 任务队列管理
    - 去重检查
    """

    def __init__(
        self, feedback_updater: FeedbackUpdater | None = None, training_data_lake: TrainingDataLake | None = None
    ):
        """初始化回灌管线

        Args:
            feedback_updater: 知识图谱更新器
            training_data_lake: 训练数据湖
        """
        self.feedback_updater = feedback_updater or FeedbackUpdater()
        self.training_data_lake = training_data_lake or TrainingDataLake()

        # 任务队列
        self._task_queue: deque[FeedbackTask] = deque()
        self._processed_record_ids: set[str] = set()
        self._processing = False

        logger.info("FeedbackLoopPipeline initialized")

    async def ingest_machining_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """异步摄入加工记录

        将加工记录加入任务队列，异步执行回灌流程。

        Args:
            record: 加工记录字典，必须包含record_id字段

        Returns:
            处理结果字典，包含：
            - success: 是否成功
            - task_id: 任务ID
            - record_id: 记录ID
            - stats: 更新统计信息

        Raises:
            ValueError: 记录缺少必要字段
            FeedbackLoopError: 回灌流程异常
        """
        # 验证必要字段
        if "record_id" not in record:
            raise ValueError("record must contain 'record_id' field")

        record_id = record["record_id"]

        # 去重检查
        if record_id in self._processed_record_ids:
            logger.info("Record %s already processed, skipping", record_id)
            return {
                "success": True,
                "task_id": None,
                "record_id": record_id,
                "stats": {"skipped": True, "reason": "duplicate"},
            }

        # 检查训练数据湖是否已存在
        if self.training_data_lake.check_record_exists(record_id):
            logger.info("Record %s already exists in training data lake, skipping", record_id)
            self._processed_record_ids.add(record_id)
            return {
                "success": True,
                "task_id": None,
                "record_id": record_id,
                "stats": {"skipped": True, "reason": "already_exists"},
            }

        # 创建任务
        task = FeedbackTask(record)
        self._task_queue.append(task)

        logger.info("Task %s created for record %s", task.task_id, record_id)

        # 异步处理任务
        result = await self._process_task(task)

        return result

    async def _process_task(self, task: FeedbackTask) -> dict[str, Any]:
        """处理单个回灌任务

        Args:
            task: 回灌任务

        Returns:
            处理结果字典
        """
        task.status = "processing"
        record_id = task.record.get("record_id")

        try:
            # 1. 转换训练样本
            training_sample = self._convert_to_training_sample(task.record)

            # 2. 写入训练数据湖
            sample_written = self.training_data_lake.write_training_sample(training_sample)

            # 3. 更新知识图谱
            kg_stats = self.feedback_updater.update_from_machining_record(task.record)

            # 4. 标记任务完成
            task.status = "completed"
            task.processed_at = datetime.now(timezone.utc)
            self._processed_record_ids.add(cast(str, record_id))

            logger.info("Task %s completed successfully", task.task_id)

            return {
                "success": True,
                "task_id": task.task_id,
                "record_id": record_id,
                "stats": {"sample_written": sample_written, "kg_update": kg_stats},
            }

        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as e:
            task.retry_count += 1
            task.error_message = "任务执行失败: 内部错误，请联系管理员"

            logger.error(f"Task {task.task_id} failed (retry {task.retry_count}/{task.max_retries}): {e}")

            # 重试机制
            if task.retry_count < task.max_retries:
                logger.info("Retrying task %s...", task.task_id)
                await asyncio.sleep(0.1 * (2**task.retry_count))  # 指数退避
                return await self._process_task(task)
            else:
                task.status = "failed"
                logger.error("Task %s failed after %s retries", task.task_id, task.max_retries)
                raise FeedbackLoopError(f"Task {task.task_id} failed after {task.max_retries} retries: {e}") from e

    def _convert_to_training_sample(self, record: dict[str, Any]) -> dict[str, Any]:
        """将加工记录转换为训练样本

        Args:
            record: 加工记录

        Returns:
            训练样本字典
        """
        # 提取关键特征
        features = {
            "machine_id": record.get("machine_id"),
            "tool_id": record.get("tool_id"),
            "workpiece_material": record.get("workpiece_material"),
            "spindle_speed": record.get("process_plan", {}).get("spindle_speed"),
            "feed_rate": record.get("process_plan", {}).get("feed_rate"),
            "depth_of_cut": record.get("process_plan", {}).get("depth_of_cut"),
        }

        # 提取标签
        labels = {
            "first_pass_acceptance": record.get("first_pass_acceptance"),
            "actual_dimensions": record.get("actual_dimensions"),
            "surface_roughness": record.get("surface_roughness"),
        }

        # 构建训练样本
        sample = {
            "record_id": record.get("record_id"),
            "timestamp": record.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "features": features,
            "labels": labels,
            "metadata": {"source": "feedback_loop", "version": "1.0"},
        }

        return sample

    async def process_queue(self, batch_size: int = 10) -> dict[str, int]:
        """批量处理任务队列

        Args:
            batch_size: 单次处理的最大任务数

        Returns:
            处理统计信息
        """
        if self._processing:
            logger.warning("Queue is already being processed")
            return {"processed": 0, "failed": 0}

        self._processing = True
        processed = 0
        failed = 0

        try:
            while self._task_queue and processed < batch_size:
                task = self._task_queue.popleft()
                try:
                    await self._process_task(task)
                    processed += 1
                except (OSError, ValueError, TypeError, KeyError, RuntimeError) as e:
                    logger.error("Task %s failed: %s", task.task_id, e)
                    failed += 1
        finally:
            self._processing = False

        logger.info("Queue processing completed: %s processed, %s failed", processed, failed)
        return {"processed": processed, "failed": failed}

    def get_queue_status(self) -> dict[str, Any]:
        """获取任务队列状态

        Returns:
            队列状态字典
        """
        return {
            "queue_size": len(self._task_queue),
            "processed_count": len(self._processed_record_ids),
            "is_processing": self._processing,
        }

    def clear_processed_records(self) -> None:
        """清空已处理记录集合（用于测试或重置）"""
        self._processed_record_ids.clear()
        logger.info("Processed records cleared")


# 全局实例（单例模式）
_pipeline_instance: FeedbackLoopPipeline | None = None
_pipeline_instance_lock = threading.Lock()


def get_pipeline() -> FeedbackLoopPipeline:
    """获取全局回灌管线实例"""
    # 安全修复：双重检查锁，防止并发创建多个实例
    global _pipeline_instance
    if _pipeline_instance is None:
        with _pipeline_instance_lock:
            if _pipeline_instance is None:
                _pipeline_instance = FeedbackLoopPipeline()
    return _pipeline_instance


def reset_pipeline() -> None:
    """重置全局回灌管线实例（主要用于测试）。"""
    global _pipeline_instance
    with _pipeline_instance_lock:
        _pipeline_instance = None


async def ingest_machining_record(record: dict[str, Any]) -> dict[str, Any]:
    """便捷函数：异步摄入加工记录

    这是验收测试中使用的入口函数。

    Args:
        record: 加工记录字典

    Returns:
        处理结果字典
    """
    pipeline = get_pipeline()
    return await pipeline.ingest_machining_record(record)


__all__ = ["FeedbackLoopPipeline", "FeedbackTask", "FeedbackLoopError", "ingest_machining_record", "get_pipeline"]
