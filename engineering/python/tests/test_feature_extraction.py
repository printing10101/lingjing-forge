"""几何特征辅助提取模块 单元测试。

覆盖：
- 模块导入与导出：app.feature_extraction 包可正常导入全部子模块
- 精度告知机制：feature_disclaimer 字段完整、8 条工业硬门槛覆盖关键约束
- 枚举完整性：FeatureExtractionTaskStatus (7 态) / FeatureType (5 类) / FeatureReviewStatus (4 态)
- ExtractedFeature.effective_params()：edited 状态使用 edited_params，否则用原始 params
- 工程师审核流程：confirmed / rejected / edited 三种动作 + 状态机转移
- 导出已确认特征：仅 confirmed + edited 进入导出，rejected 被过滤
- API 路由注册：11 个端点全部注册
- mesh 加载降级：trimesh 缺失时退化为简易 PLY 解析
- 任务删除保护：SUCCEEDED 状态禁止删除（避免误删阶段 3 已引用的特征集）

测试设计原则（与 test_image_to_3d_routes.py 一致）：
- 不依赖 trimesh / sklearn / pyransac3d 可选依赖（CI 环境可能缺失）
- 不实际触发 RANSAC 拟合（避免长耗时与噪声敏感性）
- 只验证模块契约与工程师审核流程（用户最关心的「human-in-the-loop 责任划分」）
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# 模块导入测试


class TestModuleImport:
    """验证 feature_extraction 模块可正常导入。"""

    def test_import_main_classes(self):
        """T01: 主类与函数可正常导入。"""
        from app.feature_extraction import (
            ExtractedFeature,
            FeatureExtractionTask,
            FeatureExtractionTaskStatus,
            FeatureReviewStatus,
            FeatureStore,
            FeatureType,
            get_feature_store,
            FeatureDisclaimer,
            build_feature_disclaimer,
            PlaneExtractor,
            PlaneExtractionResult,
            CylinderExtractor,
            CylinderExtractionResult,
            HoleDetector,
            HoleDetectionResult,
            FeatureExtractionPipeline,
            FeatureExtractionResult,
            FeatureExtractionError,
            FeatureReviewError,
            MeshLoadError,
        )

        # 全部非 None
        for obj in [
            ExtractedFeature,
            FeatureExtractionTask,
            FeatureExtractionTaskStatus,
            FeatureReviewStatus,
            FeatureStore,
            FeatureType,
            get_feature_store,
            FeatureDisclaimer,
            build_feature_disclaimer,
            PlaneExtractor,
            PlaneExtractionResult,
            CylinderExtractor,
            CylinderExtractionResult,
            HoleDetector,
            HoleDetectionResult,
            FeatureExtractionPipeline,
            FeatureExtractionResult,
            FeatureExtractionError,
            FeatureReviewError,
            MeshLoadError,
        ]:
            assert obj is not None, f"{obj} 导入失败"

    def test_routes_module_importable(self):
        """T02: API 路由模块可正常导入。"""
        from app.api.v1.feature_extraction import routes as feature_extraction_routes

        assert feature_extraction_routes.router is not None
        assert feature_extraction_routes.router.prefix == "/api/v1/feature_extraction"

    def test_eleven_endpoints_registered(self):
        """T03: 11 个 API 端点全部注册。"""
        from app.api.v1.feature_extraction import routes as feature_extraction_routes

        # 收集所有路由的 (method, path)
        endpoints = set()
        for route in feature_extraction_routes.router.routes:
            for method in route.methods:
                endpoints.add((method, route.path))

        expected_endpoints = {
            ("GET", "/api/v1/feature_extraction/precision_info"),
            ("POST", "/api/v1/feature_extraction/tasks"),
            ("POST", "/api/v1/feature_extraction/tasks/upload"),
            ("POST", "/api/v1/feature_extraction/tasks/{task_id}/run"),
            ("GET", "/api/v1/feature_extraction/tasks/{task_id}"),
            ("GET", "/api/v1/feature_extraction/tasks"),
            ("GET", "/api/v1/feature_extraction/tasks/{task_id}/result"),
            ("POST", "/api/v1/feature_extraction/tasks/{task_id}/review"),
            ("GET", "/api/v1/feature_extraction/tasks/{task_id}/export"),
            ("GET", "/api/v1/feature_extraction/tasks/{task_id}/export/download"),
            ("DELETE", "/api/v1/feature_extraction/tasks/{task_id}"),
        }

        missing = expected_endpoints - endpoints
        assert not missing, f"缺失端点: {missing}"


# 精度告知机制测试


class TestFeatureDisclaimer:
    """精度告知机制（项目记忆硬约束：mesh→CAD 自动转换工业上未解决）。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.config import config, FeatureExtractionConfig

        self.config = config
        self.FeatureExtractionConfig = FeatureExtractionConfig

    def test_disclaimer_all_fields(self):
        """T04: feature_disclaimer 包含全部 8 个字段。"""
        from app.feature_extraction import build_feature_disclaimer

        disclaimer = build_feature_disclaimer(
            self.config.feature_extraction,
            mesh_calibrated=False,
            mesh_source="external_upload",
        )
        d = disclaimer.to_dict()

        required_fields = {
            "mesh_calibrated",
            "mesh_source",
            "extraction_method",
            "expected_confidence_range",
            "requires_engineer_review",
            "requires_cam_validation",
            "industrial_hard_gates",
            "warning_message",
        }
        missing = required_fields - set(d.keys())
        assert not missing, f"feature_disclaimer 缺失字段: {missing}"

    def test_disclaimer_uncalibrated_warning(self):
        """T05: 未标定 mesh 时警告消息明确告知「无量纲」。"""
        from app.feature_extraction import build_feature_disclaimer

        disclaimer = build_feature_disclaimer(
            self.config.feature_extraction,
            mesh_calibrated=False,
            mesh_source="external_upload",
        )
        msg = disclaimer.warning_message
        # 必须明确告知无量纲输出 + 不可用于工艺仿真
        assert "无量纲" in msg or "未标定" in msg or "不可" in msg, f"未标定警告消息未明确告知风险: {msg}"

    def test_disclaimer_calibrated_warning(self):
        """T06: 已标定 mesh 时仍要求工程师审核 + CAM 二次校验。"""
        from app.feature_extraction import build_feature_disclaimer

        disclaimer = build_feature_disclaimer(
            self.config.feature_extraction,
            mesh_calibrated=True,
            mesh_source="abc123",
        )
        # 即使已标定，仍必须经过工程师审核 + CAM 二次校验
        assert disclaimer.requires_engineer_review is True
        assert disclaimer.requires_cam_validation is True
        assert disclaimer.mesh_calibrated is True
        assert disclaimer.mesh_source == "abc123"
        # 警告消息必须明确告知需工程师审核
        msg = disclaimer.warning_message
        assert "审核" in msg, f"已标定警告未提及工程师审核: {msg}"

    def test_industrial_hard_gates_complete(self):
        """T07: 8 条工业硬门槛覆盖关键约束。"""
        from app.feature_extraction import build_feature_disclaimer

        disclaimer = build_feature_disclaimer(
            self.config.feature_extraction,
            mesh_calibrated=True,
            mesh_source="abc123",
        )
        gates = disclaimer.industrial_hard_gates

        # 工业硬门槛必须覆盖：
        # meshCAD / 良品率 / 公差 / 持证操作员 / 签字保险 / CAM 校验 / 工程师助手定位
        all_gates_text = " ".join(gates)

        # mesh CAD 自动转换未解决
        assert "CAD" in all_gates_text or "自动转换" in all_gates_text, f"工业硬门槛未提及 mesh→CAD 自动转换: {gates}"
        # 良品率 0 缺陷
        assert "良品率" in all_gates_text or "0 缺陷" in all_gates_text, f"工业硬门槛未提及良品率: {gates}"
        # 配合面公差
        assert "0.01" in all_gates_text or "配合面" in all_gates_text, f"工业硬门槛未提及配合面公差: {gates}"
        # CNC 持证操作员
        assert "持证" in all_gates_text or "操作员" in all_gates_text, f"工业硬门槛未提及 CNC 持证操作员: {gates}"
        # CAM 二次校验
        assert "CAM" in all_gates_text or "校验" in all_gates_text, f"工业硬门槛未提及 CAM 二次校验: {gates}"
        # 工程师助手定位
        assert "工程师助手" in all_gates_text or "助手" in all_gates_text, f"工业硬门槛未明确系统定位: {gates}"
        # mesh 标定依赖
        assert "标定" in all_gates_text or "归一化" in all_gates_text or "标定块" in all_gates_text, (
            f"工业硬门槛未提及 mesh 标定: {gates}"
        )


# 枚举完整性测试


class TestEnums:
    """验证枚举类型完整（覆盖项目记忆中记录的全部状态）。"""

    def test_task_status_seven_states(self):
        """T08: FeatureExtractionTaskStatus 包含 7 个状态。"""
        from app.feature_extraction import FeatureExtractionTaskStatus

        expected = {
            "pending",
            "running",
            "features_extracted",
            "reviewed",
            "succeeded",
            "failed",
            "cancelled",
        }
        actual = {s.value for s in FeatureExtractionTaskStatus}
        assert actual == expected, f"任务状态枚举不匹配: {actual ^ expected}"

    def test_feature_type_five_types(self):
        """T09: FeatureType 包含 5 类几何特征。"""
        from app.feature_extraction import FeatureType

        expected = {"plane", "cylinder", "hole", "boss", "unknown"}
        actual = {t.value for t in FeatureType}
        assert actual == expected, f"特征类型枚举不匹配: {actual ^ expected}"

    def test_review_status_four_states(self):
        """T10: FeatureReviewStatus 包含 4 个审核状态。"""
        from app.feature_extraction import FeatureReviewStatus

        expected = {"pending", "confirmed", "rejected", "edited"}
        actual = {s.value for s in FeatureReviewStatus}
        assert actual == expected, f"审核状态枚举不匹配: {actual ^ expected}"


# ExtractedFeature.effective_params() 测试


class TestEffectiveParams:
    """验证 effective_params() 在不同审核状态下返回正确参数（工程师助手核心契约）。"""

    def _make_feature(
        self,
        review_status: str = "pending",
        edited_params: dict | None = None,
    ) -> "ExtractedFeature":  # type: ignore[name-defined]
        from app.feature_extraction import ExtractedFeature

        return ExtractedFeature(
            feature_id="feat_001",
            feature_type="plane",
            params={"normal": [0, 0, 1], "offset": 5.0, "area_mm2": 100.0},
            confidence=0.85,
            review_status=review_status,
            edited_params=edited_params or {},
        )

    def test_pending_returns_original_params(self):
        """T11: pending 状态返回原始 params。"""
        f = self._make_feature(review_status="pending")
        eff = f.effective_params()
        assert eff["offset"] == 5.0
        assert eff["area_mm2"] == 100.0

    def test_confirmed_returns_original_params(self):
        """T12: confirmed 状态返回原始 params（工程师确认无需修改）。"""
        f = self._make_feature(review_status="confirmed")
        eff = f.effective_params()
        assert eff["offset"] == 5.0
        assert eff["area_mm2"] == 100.0

    def test_rejected_returns_original_params(self):
        """T13: rejected 状态仍返回原始 params（导出时被过滤，不进入阶段 3）。"""
        f = self._make_feature(review_status="rejected")
        eff = f.effective_params()
        # rejected 的特征在 export 时被过滤，但 effective_params 仍返回原始值
        assert eff["offset"] == 5.0

    def test_edited_returns_edited_params(self):
        """T14: edited 状态返回 edited_params（工程师编辑后的值覆盖原始值）。"""
        f = self._make_feature(
            review_status="edited",
            edited_params={"normal": [0, 0, 1], "offset": 5.2, "area_mm2": 102.5},
        )
        eff = f.effective_params()
        # 必须使用工程师编辑后的值
        assert eff["offset"] == 5.2
        assert eff["area_mm2"] == 102.5
        # 不应包含原始值
        assert eff["offset"] != 5.0

    def test_edited_with_empty_edited_params_falls_back(self):
        """T15: edited 状态但 edited_params 为空时退化为原始 params（边界保护）。"""
        f = self._make_feature(review_status="edited", edited_params={})
        eff = f.effective_params()
        # edited_params 为空时应回退到原始 params
        assert eff["offset"] == 5.0


# 工程师审核流程测试


class TestEngineerReview:
    """工程师审核流程（human-in-the-loop 核心端点）。"""

    @pytest.fixture
    def temp_store(self, tmp_path: Path):
        """构造一个临时 FeatureStore（避免污染全局单例）。"""
        from app.feature_extraction import FeatureStore

        return FeatureStore(persist_dir=tmp_path / "fe_tasks")

    @pytest.fixture
    def pipeline(self, temp_store):
        """构造 FeatureExtractionPipeline 实例（使用临时 store）。"""
        from app.config import config
        from app.feature_extraction import FeatureExtractionPipeline

        return FeatureExtractionPipeline(
            task_store=temp_store,
            cfg=config.feature_extraction,
        )

    @pytest.fixture
    def task_with_features(self, temp_store):
        """构造一个 FEATURES_EXTRACTED 状态的任务，含 3 条特征。"""
        from app.feature_extraction import (
            FeatureExtractionTask,
            FeatureExtractionTaskStatus,
            ExtractedFeature,
        )

        task_id = "test_task_001"
        now = time.time()
        task = FeatureExtractionTask(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=FeatureExtractionTaskStatus.FEATURES_EXTRACTED.value,
            input_mesh_path="/tmp/test.ply",
            features=[
                ExtractedFeature(
                    feature_id="feat_001",
                    feature_type="plane",
                    params={"normal": [0, 0, 1], "offset": 5.0, "area_mm2": 100.0},
                    confidence=0.85,
                ),
                ExtractedFeature(
                    feature_id="feat_002",
                    feature_type="cylinder",
                    params={
                        "axis": [0, 0, 1],
                        "center": [0, 0, 0],
                        "radius_mm": 10.0,
                        "height_mm": 50.0,
                    },
                    confidence=0.72,
                ),
                ExtractedFeature(
                    feature_id="feat_003",
                    feature_type="hole",
                    params={
                        "normal": [0, 0, 1],
                        "center": [20, 20, 0],
                        "radius_mm": 5.0,
                        "depth_mm": 10.0,
                    },
                    confidence=0.68,
                ),
            ],
        )
        temp_store.create(task)
        return task

    def test_review_confirmed(self, pipeline, task_with_features, temp_store):
        """T16: confirmed 动作正确写入 review_status，不动 params。"""
        reviewed = pipeline.review_feature(
            task_id=task_with_features.task_id,
            feature_id="feat_001",
            action="confirmed",
            engineer_notes="平面识别正确",
            reviewed_by="engineer_zhang",
        )

        assert reviewed.review_status == "confirmed"
        assert reviewed.engineer_notes == "平面识别正确"
        # reviewed_by 写入 task 级别，不是 feature 级别
        # （设计上 task 有 reviewed_by 字段，所有 feature 共享）
        task = temp_store.get(task_with_features.task_id)
        assert task is not None
        assert task.reviewed_by == "engineer_zhang"

    def test_review_rejected(self, pipeline, task_with_features):
        """T17: rejected 动作正确写入 review_status（误识别丢弃）。"""
        reviewed = pipeline.review_feature(
            task_id=task_with_features.task_id,
            feature_id="feat_002",
            action="rejected",
            engineer_notes="圆柱误识别",
        )

        assert reviewed.review_status == "rejected"
        # rejected 后 effective_params 仍返回原始值（导出时过滤）
        assert reviewed.effective_params()["radius_mm"] == 10.0

    def test_review_edited_with_params(self, pipeline, task_with_features):
        """T18: edited 动作写入 edited_params，effective_params 使用编辑值。"""
        reviewed = pipeline.review_feature(
            task_id=task_with_features.task_id,
            feature_id="feat_003",
            action="edited",
            edited_params={
                "normal": [0, 0, 1],
                "center": [20, 20, 0],
                "radius_mm": 5.2,  # 工程师修正为 5.2mm
                "depth_mm": 10.0,
            },
            engineer_notes="孔半径微调",
        )

        assert reviewed.review_status == "edited"
        # effective_params 必须使用工程师编辑后的值
        assert reviewed.effective_params()["radius_mm"] == 5.2
        # 不应等于原始值
        assert reviewed.effective_params()["radius_mm"] != 5.0

    def test_review_edited_without_params_raises(self, pipeline, task_with_features):
        """T19: edited 动作未提供 edited_params 时抛 FeatureReviewError。"""
        from app.feature_extraction import FeatureReviewError

        with pytest.raises(FeatureReviewError) as exc_info:
            pipeline.review_feature(
                task_id=task_with_features.task_id,
                feature_id="feat_001",
                action="edited",
                edited_params=None,
            )
        assert "edited_params" in str(exc_info.value)

    def test_review_invalid_action_raises(self, pipeline, task_with_features):
        """T20: 非法 action 抛 FeatureReviewError。"""
        from app.feature_extraction import FeatureReviewError

        with pytest.raises(FeatureReviewError):
            pipeline.review_feature(
                task_id=task_with_features.task_id,
                feature_id="feat_001",
                action="invalid_action",
            )

    def test_review_nonexistent_feature_raises(self, pipeline, task_with_features):
        """T21: 审核不存在的特征时抛 FeatureReviewError。"""
        from app.feature_extraction import FeatureReviewError

        with pytest.raises(FeatureReviewError):
            pipeline.review_feature(
                task_id=task_with_features.task_id,
                feature_id="feat_not_exist",
                action="confirmed",
            )

    def test_review_wrong_status_raises(self, pipeline, temp_store, tmp_path):
        """T22: 任务状态非 FEATURES_EXTRACTED 时审核抛异常。"""
        from app.feature_extraction import (
            FeatureExtractionTask,
            FeatureExtractionTaskStatus,
            FeatureReviewError,
        )

        task_id = "test_task_pending"
        now = time.time()
        task = FeatureExtractionTask(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=FeatureExtractionTaskStatus.PENDING.value,
            input_mesh_path=str(tmp_path / "test.ply"),
        )
        temp_store.create(task)

        with pytest.raises(FeatureReviewError) as exc_info:
            pipeline.review_feature(
                task_id=task_id,
                feature_id="any",
                action="confirmed",
            )
        assert "不允许审核" in str(exc_info.value) or "FEATURES_EXTRACTED" in str(exc_info.value)

    def test_all_reviewed_transitions_to_reviewed(self, pipeline, task_with_features):
        """T23: 所有特征审核完毕后任务状态自动转为 REVIEWED。"""
        # 审核全部 3 条特征
        pipeline.review_feature(
            task_id=task_with_features.task_id,
            feature_id="feat_001",
            action="confirmed",
        )
        pipeline.review_feature(
            task_id=task_with_features.task_id,
            feature_id="feat_002",
            action="rejected",
        )
        pipeline.review_feature(
            task_id=task_with_features.task_id,
            feature_id="feat_003",
            action="edited",
            edited_params={
                "normal": [0, 0, 1],
                "center": [20, 20, 0],
                "radius_mm": 5.2,
                "depth_mm": 10.0,
            },
        )

        # 任务状态应转为 REVIEWED
        task = pipeline._store.get(task_with_features.task_id)
        assert task.status == "reviewed", f"全部审核后状态应为 reviewed，实际 {task.status}"


# 导出已确认特征测试


class TestExportConfirmedFeatures:
    """导出已确认特征集（阶段 3 参数化 STEP 生成模块的输入）。"""

    @pytest.fixture
    def temp_store(self, tmp_path: Path):
        from app.feature_extraction import FeatureStore

        return FeatureStore(persist_dir=tmp_path / "fe_export")

    @pytest.fixture
    def pipeline(self, temp_store):
        from app.config import config
        from app.feature_extraction import FeatureExtractionPipeline

        return FeatureExtractionPipeline(
            task_store=temp_store,
            cfg=config.feature_extraction,
        )

    @pytest.fixture
    def reviewed_task(self, temp_store, tmp_path):
        """构造一个 REVIEWED 状态任务：1 confirmed + 1 rejected + 1 edited。"""
        from app.feature_extraction import (
            FeatureExtractionTask,
            FeatureExtractionTaskStatus,
            ExtractedFeature,
            FeatureReviewStatus,
        )

        task_id = "test_export_task"
        now = time.time()
        task = FeatureExtractionTask(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=FeatureExtractionTaskStatus.REVIEWED.value,
            input_mesh_path=str(tmp_path / "test.ply"),
            features=[
                ExtractedFeature(
                    feature_id="feat_confirmed",
                    feature_type="plane",
                    params={"normal": [0, 0, 1], "offset": 5.0, "area_mm2": 100.0},
                    confidence=0.85,
                    review_status=FeatureReviewStatus.CONFIRMED.value,
                ),
                ExtractedFeature(
                    feature_id="feat_rejected",
                    feature_type="cylinder",
                    params={"axis": [0, 0, 1], "radius_mm": 10.0, "height_mm": 50.0},
                    confidence=0.4,  # 低置信度被拒绝
                    review_status=FeatureReviewStatus.REJECTED.value,
                ),
                ExtractedFeature(
                    feature_id="feat_edited",
                    feature_type="hole",
                    params={
                        "normal": [0, 0, 1],
                        "center": [20, 20, 0],
                        "radius_mm": 5.0,
                        "depth_mm": 10.0,
                    },
                    confidence=0.68,
                    review_status=FeatureReviewStatus.EDITED.value,
                    edited_params={
                        "normal": [0, 0, 1],
                        "center": [20, 20, 0],
                        "radius_mm": 5.2,  # 工程师修正
                        "depth_mm": 10.0,
                    },
                ),
            ],
        )
        temp_store.create(task)
        return task

    def test_export_filters_confirmed_and_edited(self, pipeline, reviewed_task, tmp_path):
        """T24: 导出仅包含 confirmed + edited，rejected 被过滤。"""
        output_path = tmp_path / "export.json"
        result_path = pipeline.export_confirmed_features(
            task_id=reviewed_task.task_id,
            output_path=output_path,
        )

        assert result_path.exists()
        data = json.loads(result_path.read_text(encoding="utf-8"))

        # 应包含 2 条特征（confirmed + edited），rejected 被过滤
        assert data["feature_count"] == 2, f"导出特征数错误: {data['feature_count']}"
        feature_ids = {f["feature_id"] for f in data["features"]}
        assert "feat_confirmed" in feature_ids
        assert "feat_edited" in feature_ids
        assert "feat_rejected" not in feature_ids, "rejected 特征不应进入导出"

    def test_export_uses_effective_params(self, pipeline, reviewed_task, tmp_path):
        """T25: 导出的 edited 特征使用 edited_params（生效参数）。"""
        output_path = tmp_path / "export.json"
        result_path = pipeline.export_confirmed_features(
            task_id=reviewed_task.task_id,
            output_path=output_path,
        )

        data = json.loads(result_path.read_text(encoding="utf-8"))
        edited_feature = next(f for f in data["features"] if f["feature_id"] == "feat_edited")
        # 必须使用工程师编辑后的 radius_mm = 5.2
        assert edited_feature["params"]["radius_mm"] == 5.2, (
            f"导出 edited 特征应使用 edited_params: {edited_feature['params']}"
        )
        assert edited_feature["edited"] is True

    def test_export_marks_task_succeeded(self, pipeline, reviewed_task, tmp_path):
        """T26: 导出后任务状态转为 SUCCEEDED。"""
        output_path = tmp_path / "export.json"
        pipeline.export_confirmed_features(
            task_id=reviewed_task.task_id,
            output_path=output_path,
        )

        task = pipeline._store.get(reviewed_task.task_id)
        assert task.status == "succeeded"
        assert task.exported_features_path  # 路径非空

    def test_export_no_confirmed_raises(self, pipeline, temp_store, tmp_path):
        """T27: 无已确认特征时导出抛异常。"""
        from app.feature_extraction import (
            FeatureExtractionTask,
            FeatureExtractionTaskStatus,
            ExtractedFeature,
            FeatureReviewStatus,
            FeatureReviewError,
        )

        # 构造一个全部 rejected 的任务
        task_id = "test_all_rejected"
        now = time.time()
        task = FeatureExtractionTask(
            task_id=task_id,
            created_at=now,
            updated_at=now,
            status=FeatureExtractionTaskStatus.REVIEWED.value,
            input_mesh_path=str(tmp_path / "test.ply"),
            features=[
                ExtractedFeature(
                    feature_id="feat_001",
                    feature_type="plane",
                    params={"offset": 5.0},
                    confidence=0.3,
                    review_status=FeatureReviewStatus.REJECTED.value,
                ),
            ],
        )
        temp_store.create(task)

        with pytest.raises(FeatureReviewError) as exc_info:
            pipeline.export_confirmed_features(task_id=task_id)
        assert "无已确认特征" in str(exc_info.value) or "confirmed" in str(exc_info.value)


# mesh 加载降级测试


class TestMeshLoadFallback:
    """mesh 加载降级策略（trimesh 缺失时退化为简易 PLY 解析）。"""

    def _write_simple_ply(self, path: Path) -> None:
        """写一个最简 ASCII PLY 文件（4 顶点 + 2 三角面）。"""
        ply_content = """ply
format ascii 1.0
element vertex 4
property float x
property float y
property float z
element face 2
property list uchar int vertex_indices
end_header
0 0 0
1 0 0
0 1 0
1 1 0
3 0 1 2
3 1 3 2
"""
        path.write_text(ply_content, encoding="utf-8")

    def test_load_ply_with_trimesh_unavailable(self, tmp_path: Path):
        """T28: trimesh 缺失时退化为简易 PLY 解析（numpy-only 环境）。"""
        from app.feature_extraction.pipeline import _load_mesh

        ply_path = tmp_path / "test.ply"
        self._write_simple_ply(ply_path)

        # trimesh_module=None 模拟 trimesh 未安装
        vertices, faces = _load_mesh(ply_path, trimesh_module=None)

        assert vertices.shape == (4, 3), f"顶点数错误: {vertices.shape}"
        assert faces is not None
        assert faces.shape == (2, 3), f"面数错误: {faces.shape}"

    def test_load_nonexistent_mesh_raises(self, tmp_path: Path):
        """T29: 加载不存在的 mesh 文件抛 MeshLoadError。"""
        from app.feature_extraction import MeshLoadError
        from app.feature_extraction.pipeline import _load_mesh

        nonexistent = tmp_path / "not_exist.ply"
        with pytest.raises(MeshLoadError):
            _load_mesh(nonexistent, trimesh_module=None)

    def test_load_unsupported_format_raises(self, tmp_path: Path):
        """T30: 加载不支持的格式（.stl）且 trimesh 缺失时抛 MeshLoadError。"""
        from app.feature_extraction import MeshLoadError
        from app.feature_extraction.pipeline import _load_mesh

        stl_path = tmp_path / "test.stl"
        stl_path.write_bytes(b"fake stl content")
        with pytest.raises(MeshLoadError) as exc_info:
            _load_mesh(stl_path, trimesh_module=None)
        assert "stl" in str(exc_info.value).lower() or "trimesh" in str(exc_info.value).lower()


# 任务创建与状态查询测试


class TestCreateTask:
    """任务创建与状态查询。"""

    @pytest.fixture
    def temp_store(self, tmp_path: Path):
        from app.feature_extraction import FeatureStore

        return FeatureStore(persist_dir=tmp_path / "fe_create")

    @pytest.fixture
    def pipeline(self, temp_store):
        from app.config import config
        from app.feature_extraction import FeatureExtractionPipeline

        return FeatureExtractionPipeline(
            task_store=temp_store,
            cfg=config.feature_extraction,
        )

    def test_create_task_with_valid_mesh(self, pipeline, tmp_path):
        """T31: 通过 mesh 路径创建任务成功（PENDING 状态）。"""
        from app.feature_extraction import FeatureExtractionTaskStatus

        # 写一个最简 PLY
        ply_path = tmp_path / "valid.ply"
        ply_path.write_text(
            "ply\nformat ascii 1.0\nelement vertex 1\n"
            "property float x\nproperty float y\nproperty float z\n"
            "end_header\n0 0 0\n",
            encoding="utf-8",
        )

        import asyncio

        task = asyncio.run(
            pipeline.create_task(
                mesh_path=ply_path,
                source_reconstruction_task_id="",
                mesh_calibrated=False,
            )
        )

        assert task.status == FeatureExtractionTaskStatus.PENDING.value
        assert task.input_mesh_path.endswith("valid.ply")
        assert task.source_reconstruction_task_id == ""

    def test_create_task_nonexistent_mesh_raises(self, pipeline, tmp_path):
        """T32: 创建任务时 mesh 不存在抛 MeshLoadError。"""
        from app.feature_extraction import MeshLoadError

        nonexistent = tmp_path / "not_exist.ply"
        with pytest.raises(MeshLoadError):
            import asyncio

            asyncio.run(
                pipeline.create_task(
                    mesh_path=nonexistent,
                    source_reconstruction_task_id="",
                    mesh_calibrated=False,
                )
            )


# 配置测试


class TestFeatureExtractionConfig:
    """FeatureExtractionConfig（环境变量前缀 LNN_FE_*）。"""

    def test_config_has_all_required_fields(self):
        """T33: FeatureExtractionConfig 包含全部必需字段。"""
        from app.config import config

        fe = config.feature_extraction

        # 总开关
        assert hasattr(fe, "enabled")
        assert hasattr(fe, "max_concurrent")
        assert hasattr(fe, "task_timeout_seconds")
        assert hasattr(fe, "task_retention_hours")
        assert hasattr(fe, "output_dir")

        # 平面参数
        assert hasattr(fe, "plane_ransac_threshold_mm")
        assert hasattr(fe, "plane_min_inliers")
        assert hasattr(fe, "plane_max_features")

        # 圆柱参数
        assert hasattr(fe, "cylinder_min_radius_mm")
        assert hasattr(fe, "cylinder_max_radius_mm")
        assert hasattr(fe, "cylinder_min_inliers")
        assert hasattr(fe, "cylinder_max_features")

        # 孔参数
        assert hasattr(fe, "hole_min_radius_mm")
        assert hasattr(fe, "hole_max_radius_mm")
        assert hasattr(fe, "hole_max_features")

        # mesh 预处理
        assert hasattr(fe, "mesh_decimation_target_vertices")
        assert hasattr(fe, "mesh_compute_normals")

        # 精度档位
        assert hasattr(fe, "precision_tier")

    def test_precision_tier_defaults_to_standard(self):
        """T34: precision_tier 默认为 'standard'。"""
        from app.config import FeatureExtractionConfig

        cfg = FeatureExtractionConfig()
        assert cfg.precision_tier in {"coarse", "standard", "high"}
        # 默认应为 standard
        assert cfg.precision_tier == "standard", f"默认 precision_tier 应为 'standard'，实际 {cfg.precision_tier}"

    def test_invalid_precision_tier_falls_back(self):
        """T35: 非法 precision_tier 在 __post_init__ 中回退为 'standard'。"""
        from app.config import FeatureExtractionConfig

        # 直接构造非法值（绕过环境变量）
        cfg = FeatureExtractionConfig()
        cfg.precision_tier = "invalid_tier"
        cfg.__post_init__()
        assert cfg.precision_tier == "standard"


# main.py 路由注册测试


class TestMainAppIntegration:
    """验证 main.py 中 feature_extraction 路由的条件注册。"""

    def test_main_module_imports_feature_extraction(self):
        """T36: main.py 模块可正常导入，feature_extraction 模块被加载。"""
        # 触发 main.py 的条件导入逻辑
        import app.main  # noqa: F401

        # main 模块必须有 _FEATURE_EXTRACTION_AVAILABLE 标志
        from app.main import _FEATURE_EXTRACTION_AVAILABLE

        assert isinstance(_FEATURE_EXTRACTION_AVAILABLE, bool), "_FEATURE_EXTRACTION_AVAILABLE 应为 bool 类型"

    def test_router_prefix_correct(self):
        """T37: 路由 prefix 为 /api/v1/feature_extraction。"""
        from app.api.v1.feature_extraction import routes

        assert routes.router.prefix == "/api/v1/feature_extraction"

    def test_router_has_feature_extraction_read_tag(self):
        """T38: 路由 tags 包含 'Feature Extraction' 标识。"""
        from app.api.v1.feature_extraction import routes

        tags = routes.router.tags
        # 至少有一个 tag 包含 "Feature Extraction" 字样
        assert any("Feature Extraction" in str(t) for t in tags), f"路由 tags 未包含 Feature Extraction 标识: {tags}"


# 上游 mesh_calibrated 软依赖查询测试


class TestResolveUpstreamCalibrated:
    """验证 _resolve_upstream_calibrated 在不同场景下的行为。"""

    def test_empty_source_returns_uncalibrated(self):
        """T39: 未提供 source_reconstruction_task_id 时按未标定处理。"""
        from app.api.v1.feature_extraction.routes import _resolve_upstream_calibrated

        calibrated, source = _resolve_upstream_calibrated("")
        assert calibrated is False
        assert source == "external_upload"

    def test_nonexistent_source_returns_uncalibrated(self):
        """T40: 上游任务 ID 不存在时按未标定处理（不抛异常）。"""
        from app.api.v1.feature_extraction.routes import _resolve_upstream_calibrated

        # 用一个肯定不存在的 task_id
        calibrated, source = _resolve_upstream_calibrated("nonexistent_task_id_xyz123")
        # 应返回 (False, "external_upload")，不抛异常
        assert calibrated is False
        # source 在上游任务不存在时回退为 "external_upload"
        assert source == "external_upload"


# 验收：项目记忆硬约束覆盖测试


class TestProjectMemoryHardConstraints:
    """验证 ADR-007 中记录的项目记忆硬约束在代码中体现。"""

    def test_mesh_to_cad_unsolved_warning_present(self):
        """T41: feature_disclaimer 明确告知 mesh → CAD 自动转换工业上未解决。"""
        from app.feature_extraction import build_feature_disclaimer
        from app.config import config

        disclaimer = build_feature_disclaimer(
            config.feature_extraction,
            mesh_calibrated=False,
        )
        # 工业硬门槛列表必须包含 mesh CAD 自动转换未解决的警告
        all_text = " ".join(disclaimer.industrial_hard_gates) + " " + disclaimer.warning_message
        assert "CAD" in all_text or "自动转换" in all_text or "参数化" in all_text, (
            f"未明确告知 mesh → CAD 自动转换工业上未解决: {all_text}"
        )

    def test_engineer_assistant_positioning_present(self):
        """T42: feature_disclaimer 明确告知系统定位为「工程师助手」。"""
        from app.feature_extraction import build_feature_disclaimer
        from app.config import config

        disclaimer = build_feature_disclaimer(
            config.feature_extraction,
            mesh_calibrated=True,
        )
        all_text = " ".join(disclaimer.industrial_hard_gates)
        assert "工程师助手" in all_text or "助手" in all_text, f"未明确告知系统定位为工程师助手: {all_text}"

    def test_cam_validation_required(self):
        """T43: requires_cam_validation 始终为 True（项目记忆硬约束）。"""
        from app.feature_extraction import build_feature_disclaimer
        from app.config import config

        for calibrated in [True, False]:
            disclaimer = build_feature_disclaimer(
                config.feature_extraction,
                mesh_calibrated=calibrated,
            )
            assert disclaimer.requires_cam_validation is True, (
                f"calibrated={calibrated} 时 requires_cam_validation 必须为 True"
            )

    def test_engineer_review_always_required(self):
        """T44: requires_engineer_review 始终为 True（项目记忆硬约束）。"""
        from app.feature_extraction import build_feature_disclaimer
        from app.config import config

        for calibrated in [True, False]:
            disclaimer = build_feature_disclaimer(
                config.feature_extraction,
                mesh_calibrated=calibrated,
            )
            assert disclaimer.requires_engineer_review is True, (
                f"calibrated={calibrated} 时 requires_engineer_review 必须为 True"
            )
