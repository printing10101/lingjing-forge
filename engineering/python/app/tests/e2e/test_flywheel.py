"""飞轮系统端到端测试

覆盖完整业务链路：数据进→工艺出→加工回→模型更新
"""

from __future__ import annotations

import pytest
from datetime import datetime

from app.pipelines.feedback_loop import FeedbackLoopPipeline, ingest_machining_record
from app.knowledge_graph.feedback_updater import FeedbackUpdater
from app.training.data_lake import TrainingDataLake
from app.metrics.flywheel_metrics import get_flywheel_collector


class TestFlywheelE2E:
    """飞轮端到端测试套件"""

    @pytest.fixture
    def sample_machining_record(self):
        """示例加工记录"""
        return {
            "record_id": "REC-E2E-001",
            "timestamp": datetime.now().isoformat(),
            "machine_id": "CNC-001",
            "tool_id": "TOOL-001",
            "workpiece_material": "45号钢",
            "process_plan": {
                "spindle_speed": 1200,
                "feed_rate": 0.2,
                "depth_of_cut": 1.5,
                "steps": [
                    {
                        "process_id": "PROC-001",
                        "name": "粗车外圆",
                        "feature_id": "FEAT-001"
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {
                "diameter": 100.05,
                "length": 150.02
            },
            "surface_roughness": 1.6
        }

    @pytest.fixture
    def temp_storage_dir(self, tmp_path):
        """临时存储目录"""
        return tmp_path / "training_data"

    @pytest.mark.asyncio
    async def test_data_ingestion_to_training_lake(self, sample_machining_record, temp_storage_dir):
        """测试数据摄入到训练数据湖"""
        # 创建独立的训练数据湖实例
        data_lake = TrainingDataLake(storage_dir=temp_storage_dir)
        
        # 摄入加工记录
        result = await ingest_machining_record(sample_machining_record)
        
        # 验证结果
        assert result["success"] is True
        assert result["record_id"] == sample_machining_record["record_id"]
        
        # 验证数据已写入训练数据湖
        stats = data_lake.get_statistics()
        assert stats["total_samples"] > 0
        assert sample_machining_record["record_id"] in stats["record_ids"]

    @pytest.mark.asyncio
    async def test_knowledge_graph_update(self, sample_machining_record, temp_storage_dir):
        """测试知识图谱更新"""
        # 创建反馈更新器
        feedback_updater = FeedbackUpdater()
        
        # 更新知识图谱
        kg_stats = feedback_updater.update_from_machining_record(sample_machining_record)
        
        # 验证更新统计
        assert "process_nodes_updated" in kg_stats
        assert "tool_material_edges_updated" in kg_stats
        assert "process_feature_edges_updated" in kg_stats
        assert "confidence_adjustments" in kg_stats
        
        # 验证至少有一个节点或关系被更新
        total_updates = (
            kg_stats["process_nodes_updated"] +
            kg_stats["tool_material_edges_updated"] +
            kg_stats["process_feature_edges_updated"]
        )
        assert total_updates > 0

    @pytest.mark.asyncio
    async def test_feedback_loop_pipeline_integration(self, sample_machining_record, temp_storage_dir):
        """测试回灌管线集成"""
        # 创建完整的回灌管线
        pipeline = FeedbackLoopPipeline()
        
        # 摄入记录
        result = await pipeline.ingest_machining_record(sample_machining_record)
        
        # 验证处理成功
        assert result["success"] is True
        assert result["task_id"] is not None
        
        # 验证队列状态
        queue_status = pipeline.get_queue_status()
        assert queue_status["processed_count"] > 0

    @pytest.mark.asyncio
    async def test_duplicate_record_handling(self, sample_machining_record, temp_storage_dir):
        """测试重复记录处理"""
        # 第一次摄入
        result1 = await ingest_machining_record(sample_machining_record)
        assert result1["success"] is True
        
        # 第二次摄入相同记录
        result2 = await ingest_machining_record(sample_machining_record)
        
        # 验证去重成功
        assert result2["success"] is True
        assert result2.get("stats", {}).get("skipped") is True

    @pytest.mark.asyncio
    async def test_flywheel_metrics_collection(self, sample_machining_record, temp_storage_dir):
        """测试飞轮指标采集"""
        # 先摄入一些数据
        await ingest_machining_record(sample_machining_record)
        
        # 获取飞轮指标采集器
        collector = get_flywheel_collector()
        
        # 采集当前指标
        metrics = collector.collect_current_metrics()
        
        # 验证指标完整性
        assert metrics.data_volume >= 0
        assert 0 <= metrics.model_quality <= 100
        assert 0 <= metrics.adoption_rate <= 100
        assert 0 <= metrics.uncertainty_mean <= 1
        assert metrics.feedback_delay >= 0
        assert metrics.timestamp is not None

    @pytest.mark.asyncio
    async def test_flywheel_weekly_report_generation(self, temp_storage_dir):
        """测试飞轮周报生成"""
        # 获取采集器
        collector = get_flywheel_collector()
        
        # 生成周报
        report = collector.generate_weekly_report()
        
        # 验证报告结构
        assert report["report_type"] == "weekly"
        assert "generated_at" in report
        assert "period" in report
        assert "current_metrics" in report
        assert "trends" in report
        assert "summary" in report
        
        # 验证摘要内容
        summary = report["summary"]
        assert "health_score" in summary
        assert "health_status" in summary
        assert 0 <= summary["health_score"] <= 100

    @pytest.mark.asyncio
    async def test_complete_business_cycle(self, sample_machining_record, temp_storage_dir):
        """测试完整业务循环：数据进→工艺出→加工回→模型更新"""
        # 1. 数据进：摄入加工记录
        ingest_result = await ingest_machining_record(sample_machining_record)
        assert ingest_result["success"] is True
        
        # 2. 工艺出：验证知识图谱已更新工艺参数
        kg_stats = ingest_result.get("stats", {}).get("kg_update", {})
        assert kg_stats.get("process_nodes_updated", 0) > 0 or \
               kg_stats.get("tool_material_edges_updated", 0) > 0
        
        # 3. 加工回：验证训练数据已存储
        data_lake = TrainingDataLake(storage_dir=temp_storage_dir)
        assert data_lake.check_record_exists(sample_machining_record["record_id"])
        
        # 4. 模型更新：验证飞轮指标已更新
        collector = get_flywheel_collector()
        metrics = collector.collect_current_metrics()
        assert metrics.data_volume > 0

    @pytest.mark.asyncio
    async def test_batch_processing(self, temp_storage_dir):
        """测试批量处理"""
        # 创建多条加工记录
        records = [
            {
                "record_id": f"REC-BATCH-{i:03d}",
                "timestamp": datetime.now().isoformat(),
                "machine_id": "CNC-001",
                "tool_id": f"TOOL-{i:03d}",
                "workpiece_material": "45号钢",
                "process_plan": {
                    "spindle_speed": 1200,
                    "feed_rate": 0.2,
                    "depth_of_cut": 1.5
                },
                "first_pass_acceptance": i % 2 == 0,
                "surface_roughness": 1.6 + i * 0.1
            }
            for i in range(5)
        ]
        
        # 批量摄入
        results = []
        for record in records:
            result = await ingest_machining_record(record)
            results.append(result)
        
        # 验证所有记录处理成功
        assert all(r["success"] for r in results)
        
        # 验证数据湖统计
        data_lake = TrainingDataLake(storage_dir=temp_storage_dir)
        stats = data_lake.get_statistics()
        assert stats["total_samples"] >= 5

    @pytest.mark.asyncio
    async def test_error_recovery_with_retry(self, temp_storage_dir):
        """测试错误恢复与重试机制"""
        # 创建一条缺少必要字段的记录
        invalid_record = {
            "timestamp": datetime.now().isoformat(),
            "machine_id": "CNC-001"
            # 缺少 record_id
        }
        
        # 验证抛出异常
        with pytest.raises(ValueError, match="record_id"):
            await ingest_machining_record(invalid_record)

    @pytest.mark.asyncio
    async def test_metrics_trend_analysis(self, temp_storage_dir):
        """测试指标趋势分析"""
        # 摄入多条记录以产生趋势数据
        for i in range(3):
            record = {
                "record_id": f"REC-TREND-{i:03d}",
                "timestamp": datetime.now().isoformat(),
                "machine_id": "CNC-001",
                "tool_id": "TOOL-001",
                "workpiece_material": "45号钢",
                "process_plan": {"spindle_speed": 1200},
                "first_pass_acceptance": True,
                "surface_roughness": 1.5
            }
            await ingest_machining_record(record)
        
        # 获取历史指标
        collector = get_flywheel_collector()
        historical = collector.get_historical_metrics(days=7)
        
        # 验证历史数据
        assert len(historical) > 0
        
        # 生成报告并验证趋势
        report = collector.generate_weekly_report()
        trends = report.get("trends", {})
        assert "data_volume" in trends or "model_quality" in trends


class TestFlywheelAPIE2E:
    """飞轮 API 端到端测试"""

    @pytest.mark.asyncio
    async def test_flywheel_status_api(self):
        """测试飞轮状态 API"""
        from app.api.v1.flywheel import get_flywheel_status
        
        # 调用 API
        response = await get_flywheel_status()
        
        # 验证响应结构
        assert response.status in ["healthy", "warning", "critical"]
        assert response.data_volume >= 0
        assert 0 <= response.model_quality <= 100
        assert 0 <= response.adoption_rate <= 100
        assert 0 <= response.uncertainty_mean <= 1
        assert response.feedback_delay >= 0
        assert 0 <= response.health_score <= 100

    @pytest.mark.asyncio
    async def test_flywheel_metrics_api(self):
        """测试飞轮指标 API"""
        from app.api.v1.flywheel import get_flywheel_metrics

        # 调用 API
        response = await get_flywheel_metrics(days=7)
        
        # 验证响应结构
        assert "current" in response
        assert "historical" in response
        assert response["period_days"] == 7

    @pytest.mark.asyncio
    async def test_flywheel_report_api(self):
        """测试飞轮报告 API"""
        from app.api.v1.flywheel import generate_weekly_report
        
        # 调用 API
        response = await generate_weekly_report(save=False)
        
        # 验证响应结构
        assert response["report_type"] == "weekly"
        assert "generated_at" in response
        assert "current_metrics" in response

    @pytest.mark.asyncio
    async def test_metric_definitions_api(self):
        """测试指标定义 API"""
        from app.api.v1.flywheel import get_metric_definitions
        
        # 调用 API
        response = await get_metric_definitions()
        
        # 验证响应结构（MetricDefinitionsResponse 对象，用属性访问）
        assert len(response.metrics) > 0
        
        # 验证指标定义完整性
        metric_names = [m.name for m in response.metrics]
        assert "data_volume" in metric_names
        assert "model_quality" in metric_names
        assert "adoption_rate" in metric_names


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
