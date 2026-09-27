"""实测数据回灌管线单元测试

测试覆盖：
- 训练数据湖的写入、读取、去重
- 知识图谱更新器的节点和关系更新
- 回灌管线的主流程、异步处理、重试机制
- 端到端功能验证
"""

import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.knowledge_graph.feedback_updater import FeedbackUpdater
from app.knowledge_graph.graph_store import GraphStore
from app.pipelines.feedback_loop import (
    FeedbackLoopError,
    FeedbackLoopPipeline,
    FeedbackTask,
    get_pipeline,
    ingest_machining_record,
)
from app.training.data_lake import TrainingDataLake


@pytest.fixture
def sample_machining_record():
    """示例加工记录"""
    return {
        "record_id": "mrec_test_001",
        "machine_id": "M-001",
        "tool_id": "T-endmill-10",
        "workpiece_material": "M-45steel",
        "timestamp": datetime.now().isoformat(),
        "process_plan": {
            "spindle_speed": 4500.0,
            "feed_rate": 800.0,
            "depth_of_cut": 1.5,
            "steps": [{"process_id": "process-face-mill-001", "name": "面铣", "feature_id": "feature-plane-top"}],
        },
        "first_pass_acceptance": True,
        "actual_dimensions": [{"dimension": "length", "actual": 100.05, "nominal": 100.0, "tolerance": 0.1}],
        "surface_roughness": 1.6,
        "operator_id": "OP-001",
        "shift": "day",
    }


@pytest.fixture
def temp_storage_dir():
    """临时存储目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def training_data_lake(temp_storage_dir):
    """训练数据湖实例"""
    return TrainingDataLake(storage_dir=temp_storage_dir)


@pytest.fixture
def graph_store():
    """知识图谱存储实例"""
    return GraphStore(auto_load=False)


@pytest.fixture
def feedback_updater(graph_store):
    """反馈更新器实例"""
    return FeedbackUpdater(graph_store=graph_store)


@pytest.fixture
def feedback_pipeline(feedback_updater, training_data_lake):
    """回灌管线实例"""
    return FeedbackLoopPipeline(feedback_updater=feedback_updater, training_data_lake=training_data_lake)


class TestTrainingDataLake:
    """训练数据湖测试"""

    def test_write_training_sample(self, training_data_lake, sample_machining_record):
        """测试写入训练样本"""
        sample = {
            "record_id": sample_machining_record["record_id"],
            "features": {"tool_id": "T-001"},
            "labels": {"surface_roughness": 1.6},
        }

        result = training_data_lake.write_training_sample(sample)
        assert result is True

        # 验证文件已创建
        files = list(training_data_lake.storage_dir.glob("training_data_*.jsonl"))
        assert len(files) == 1

        # 验证内容
        with open(files[0], "r", encoding="utf-8") as f:
            content = json.loads(f.readline())
            assert content["record_id"] == sample["record_id"]

    def test_write_duplicate_sample(self, training_data_lake, sample_machining_record):
        """测试写入重复样本（去重）"""
        sample = {"record_id": sample_machining_record["record_id"], "features": {}}

        # 第一次写入
        result1 = training_data_lake.write_training_sample(sample)
        assert result1 is True

        # 第二次写入（重复）
        result2 = training_data_lake.write_training_sample(sample)
        assert result2 is False

    def test_write_sample_without_record_id(self, training_data_lake):
        """测试写入缺少record_id的样本"""
        sample: dict[str, Any] = {"features": {}}

        with pytest.raises(ValueError, match="must contain 'record_id'"):
            training_data_lake.write_training_sample(sample)

    def test_load_training_samples(self, training_data_lake, sample_machining_record):
        """测试加载训练样本"""
        # 写入多个样本
        for i in range(3):
            sample = {"record_id": f"mrec_test_{i:03d}", "features": {"index": i}}
            training_data_lake.write_training_sample(sample)

        # 加载所有样本
        samples = training_data_lake.load_training_samples()
        assert len(samples) == 3

        # 验证内容
        record_ids = [s["record_id"] for s in samples]
        assert "mrec_test_000" in record_ids
        assert "mrec_test_001" in record_ids
        assert "mrec_test_002" in record_ids

    def test_load_training_samples_with_limit(self, training_data_lake):
        """测试加载训练样本（带限制）"""
        for i in range(5):
            sample = {"record_id": f"mrec_test_{i:03d}", "features": {}}
            training_data_lake.write_training_sample(sample)

        samples = training_data_lake.load_training_samples(limit=3)
        assert len(samples) == 3

    def test_check_record_exists(self, training_data_lake):
        """测试检查记录是否存在"""
        sample = {"record_id": "mrec_test_exists", "features": {}}
        training_data_lake.write_training_sample(sample)

        assert training_data_lake.check_record_exists("mrec_test_exists") is True
        assert training_data_lake.check_record_exists("mrec_test_not_exists") is False

    def test_get_statistics(self, training_data_lake):
        """测试获取统计信息"""
        for i in range(3):
            sample = {"record_id": f"mrec_test_{i:03d}", "features": {}}
            training_data_lake.write_training_sample(sample)

        stats = training_data_lake.get_statistics()
        assert stats["total_samples"] == 3
        assert stats["total_files"] == 1
        assert len(stats["record_ids"]) == 3


class TestFeedbackUpdater:
    """反馈更新器测试"""

    def test_update_process_nodes(self, feedback_updater, sample_machining_record, graph_store):
        """测试更新Process节点"""
        stats = feedback_updater.update_from_machining_record(sample_machining_record)

        assert stats["process_nodes_updated"] > 0

        # 验证节点已创建
        process_id = "process-face-mill-001"
        assert graph_store.has_node(process_id)

        node = graph_store.get_node(process_id)
        assert node is not None
        assert node["node_type"] == "process"
        assert node["properties"]["sample_count"] == 1
        assert node["properties"]["success_count"] == 1

    def test_update_tool_material_relationship(self, feedback_updater, sample_machining_record, graph_store):
        """测试更新Tool-Material关系"""
        stats = feedback_updater.update_from_machining_record(sample_machining_record)

        assert stats["tool_material_edges_updated"] > 0

        # 验证关系已创建
        tool_id = sample_machining_record["tool_id"]
        material = sample_machining_record["workpiece_material"]

        assert graph_store.has_edge(tool_id, material, "SUITABLE_FOR")

        edge = graph_store.get_edge(tool_id, material, "SUITABLE_FOR")
        assert edge is not None
        assert edge["properties"]["sample_count"] == 1
        assert edge["properties"]["success_count"] == 1

    def test_update_process_feature_relationships(self, feedback_updater, sample_machining_record, graph_store):
        """测试更新Process-Feature关系"""
        stats = feedback_updater.update_from_machining_record(sample_machining_record)

        assert stats["process_feature_edges_updated"] > 0

        # 验证关系已创建
        process_id = "process-face-mill-001"
        feature_id = "feature-plane-top"

        assert graph_store.has_edge(process_id, feature_id, "APPLIED_TO")

    def test_adjust_confidence_on_success(self, feedback_updater, sample_machining_record, graph_store):
        """测试合格时调整可信度"""
        # 第一次更新
        feedback_updater.update_from_machining_record(sample_machining_record)

        tool_id = sample_machining_record["tool_id"]
        material = sample_machining_record["workpiece_material"]

        edge1 = graph_store.get_edge(tool_id, material, "SUITABLE_FOR")
        confidence1 = edge1["properties"]["confidence"]

        # 第二次更新（合格）
        feedback_updater.update_from_machining_record(sample_machining_record)

        edge2 = graph_store.get_edge(tool_id, material, "SUITABLE_FOR")
        confidence2 = edge2["properties"]["confidence"]

        # 可信度应该提升
        assert confidence2 >= confidence1

    def test_adjust_confidence_on_failure(self, feedback_updater, sample_machining_record, graph_store):
        """测试不合格时调整可信度"""
        # 第一次更新（合格）
        feedback_updater.update_from_machining_record(sample_machining_record)

        tool_id = sample_machining_record["tool_id"]
        material = sample_machining_record["workpiece_material"]

        edge1 = graph_store.get_edge(tool_id, material, "SUITABLE_FOR")
        confidence1 = edge1["properties"]["confidence"]

        # 第二次更新（不合格）
        failed_record = sample_machining_record.copy()
        failed_record["record_id"] = "mrec_test_failed"
        failed_record["first_pass_acceptance"] = False

        feedback_updater.update_from_machining_record(failed_record)

        edge2 = graph_store.get_edge(tool_id, material, "SUITABLE_FOR")
        confidence2 = edge2["properties"]["confidence"]

        # 可信度应该降低
        assert confidence2 < confidence1

    def test_update_without_record_id(self, feedback_updater):
        """测试更新缺少record_id的记录"""
        record = {"tool_id": "T-001"}

        with pytest.raises(ValueError, match="must contain 'record_id'"):
            feedback_updater.update_from_machining_record(record)


class TestFeedbackLoopPipeline:
    """回灌管线测试"""

    @pytest.mark.asyncio
    async def test_ingest_machining_record(self, feedback_pipeline, sample_machining_record):
        """测试摄入加工记录"""
        result = await feedback_pipeline.ingest_machining_record(sample_machining_record)

        assert result["success"] is True
        assert result["record_id"] == sample_machining_record["record_id"]
        assert result["stats"]["sample_written"] is True

    @pytest.mark.asyncio
    async def test_ingest_duplicate_record(self, feedback_pipeline, sample_machining_record):
        """测试摄入重复记录（去重）"""
        # 第一次摄入
        result1 = await feedback_pipeline.ingest_machining_record(sample_machining_record)
        assert result1["success"] is True

        # 第二次摄入（重复）
        result2 = await feedback_pipeline.ingest_machining_record(sample_machining_record)
        assert result2["success"] is True
        assert result2["stats"]["skipped"] is True
        assert result2["stats"]["reason"] == "duplicate"

    @pytest.mark.asyncio
    async def test_ingest_record_without_record_id(self, feedback_pipeline):
        """测试摄入缺少record_id的记录"""
        record = {"tool_id": "T-001"}

        with pytest.raises(ValueError, match="must contain 'record_id'"):
            await feedback_pipeline.ingest_machining_record(record)

    @pytest.mark.asyncio
    async def test_convert_to_training_sample(self, feedback_pipeline, sample_machining_record):
        """测试转换为训练样本"""
        sample = feedback_pipeline._convert_to_training_sample(sample_machining_record)

        assert sample["record_id"] == sample_machining_record["record_id"]
        assert "features" in sample
        assert "labels" in sample
        assert sample["features"]["tool_id"] == sample_machining_record["tool_id"]
        assert sample["labels"]["surface_roughness"] == sample_machining_record["surface_roughness"]

    @pytest.mark.asyncio
    async def test_process_queue(self, feedback_pipeline, sample_machining_record):
        """测试批量处理任务队列"""
        # 添加多个任务到队列
        for i in range(3):
            record = sample_machining_record.copy()
            record["record_id"] = f"mrec_test_queue_{i:03d}"
            feedback_pipeline._task_queue.append(FeedbackTask(record))

        # 处理队列
        result = await feedback_pipeline.process_queue(batch_size=10)

        assert result["processed"] == 3
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_retry_mechanism(self, feedback_pipeline, sample_machining_record):
        """测试重试机制"""
        # 模拟训练数据湖写入失败
        with patch.object(
            feedback_pipeline.training_data_lake, "write_training_sample", side_effect=Exception("Write failed")
        ):
            with pytest.raises(FeedbackLoopError, match="failed after .* retries"):
                await feedback_pipeline.ingest_machining_record(sample_machining_record)

    def test_get_queue_status(self, feedback_pipeline, sample_machining_record):
        """测试获取队列状态"""
        status = feedback_pipeline.get_queue_status()

        assert "queue_size" in status
        assert "processed_count" in status
        assert "is_processing" in status

        assert status["queue_size"] == 0
        assert status["processed_count"] == 0

    def test_clear_processed_records(self, feedback_pipeline, sample_machining_record):
        """测试清空已处理记录"""
        # 先处理一条记录
        asyncio.run(feedback_pipeline.ingest_machining_record(sample_machining_record))

        assert len(feedback_pipeline._processed_record_ids) == 1

        # 清空
        feedback_pipeline.clear_processed_records()

        assert len(feedback_pipeline._processed_record_ids) == 0


class TestFeedbackTask:
    """反馈任务测试"""

    def test_create_task(self, sample_machining_record):
        """测试创建任务"""
        task = FeedbackTask(sample_machining_record)

        assert task.task_id is not None
        assert task.record == sample_machining_record
        assert task.retry_count == 0
        assert task.status == "pending"

    def test_task_to_dict(self, sample_machining_record):
        """测试任务序列化"""
        task = FeedbackTask(sample_machining_record)
        task_dict = task.to_dict()

        assert "task_id" in task_dict
        assert "record_id" in task_dict
        assert "retry_count" in task_dict
        assert "status" in task_dict
        assert task_dict["record_id"] == sample_machining_record["record_id"]


class TestEndToEnd:
    """端到端测试"""

    @pytest.mark.asyncio
    async def test_full_feedback_loop(self, temp_storage_dir, sample_machining_record):
        """测试完整的回灌流程"""
        # 创建独立的组件
        graph_store = GraphStore(auto_load=False)
        feedback_updater = FeedbackUpdater(graph_store=graph_store)
        training_data_lake = TrainingDataLake(storage_dir=temp_storage_dir)
        pipeline = FeedbackLoopPipeline(feedback_updater=feedback_updater, training_data_lake=training_data_lake)

        # 摄入加工记录
        result = await pipeline.ingest_machining_record(sample_machining_record)

        # 验证结果
        assert result["success"] is True

        # 验证知识图谱已更新
        process_id = "process-face-mill-001"
        assert graph_store.has_node(process_id)

        tool_id = sample_machining_record["tool_id"]
        material = sample_machining_record["workpiece_material"]
        assert graph_store.has_edge(tool_id, material, "SUITABLE_FOR")

        # 验证训练数据已写入
        samples = training_data_lake.load_training_samples()
        assert len(samples) == 1
        assert samples[0]["record_id"] == sample_machining_record["record_id"]

    @pytest.mark.asyncio
    async def test_multiple_records_feedback(self, temp_storage_dir, sample_machining_record):
        """测试多条记录的回灌"""
        graph_store = GraphStore(auto_load=False)
        feedback_updater = FeedbackUpdater(graph_store=graph_store)
        training_data_lake = TrainingDataLake(storage_dir=temp_storage_dir)
        pipeline = FeedbackLoopPipeline(feedback_updater=feedback_updater, training_data_lake=training_data_lake)

        # 摄入多条记录
        for i in range(5):
            record = sample_machining_record.copy()
            record["record_id"] = f"mrec_test_multi_{i:03d}"
            record["first_pass_acceptance"] = i % 2 == 0  # 交替合格/不合格

            result = await pipeline.ingest_machining_record(record)
            assert result["success"] is True

        # 验证训练数据
        samples = training_data_lake.load_training_samples()
        assert len(samples) == 5

        # 验证知识图谱
        process_id = "process-face-mill-001"
        node = graph_store.get_node(process_id)
        assert node is not None
        assert node["properties"]["sample_count"] == 5
        assert node["properties"]["success_count"] == 3  # 3次合格


class TestGlobalPipeline:
    """全局管线实例测试"""

    @pytest.mark.asyncio
    async def test_get_pipeline(self):
        """测试获取全局管线实例"""
        pipeline1 = get_pipeline()
        pipeline2 = get_pipeline()

        assert pipeline1 is pipeline2

    @pytest.mark.asyncio
    async def test_ingest_machining_record_function(self, sample_machining_record):
        """测试便捷函数"""
        result = await ingest_machining_record(sample_machining_record)

        assert result["success"] is True
        assert result["record_id"] == sample_machining_record["record_id"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
