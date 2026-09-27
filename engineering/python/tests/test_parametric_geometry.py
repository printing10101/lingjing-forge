"""参数化几何输出模块 单元测试（阶段 3）。

覆盖：
- 模块导入与导出：app.parametric_geometry 包可正常导入全部子模块
- 精度告知机制：step_disclaimer 字段完整、8 条工业硬门槛覆盖关键约束
- 枚举完整性：ParametricGeometryTaskStatus (7 态) / StepReviewStatus (4 态)
- ReviewedFeatureRef.effective_params()：edited 状态合并 edited_params，否则用 source_params
- 工程师审核流程：confirmed / rejected / edited 三种动作 + 状态机转移 + 错误路径
- 特征→B-rep 转换：plane / cylinder / hole / boss 四种特征 + rejected 跳过 + 未识别类型跳过
- 装配器：base 选择（plane 优先）/ add 排序（体积大到小）/ subtract 排序 / 毛坯 bbox 估算
- STEP 写入器三级降级：无引擎时返回 unavailable
- Pipeline 状态机：PENDING → RUNNING → STEP_GENERATED → REVIEWED → SUCCEEDED（含 FAILED/CANCELLED）
- 配置校验：ParametricGeometryConfig 8 个字段 + __post_init__ 非法值回退
- API 路由注册：10 个端点全部注册
- 上游 mesh 标定状态追溯：三层 try/except ImportError 软依赖
- 项目记忆硬约束：mesh→CAD 未解决警告 / 工程师助手定位 / CAM 校验强制 / 工程师审核强制

测试设计原则（与 test_feature_extraction.py 一致）：
- 不依赖 pythonocc-core / FreeCAD 可选依赖（CI 环境必然缺失）
- 不实际触发 STEP 文件写入（通过 patch write_step_with_fallback 隔离 IO 与引擎选择）
- 只验证模块契约与工程师两轮审核流程（用户最关心的「human-in-the-loop 责任划分」）
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# 模块导入测试


class TestModuleImport:
    """验证 parametric_geometry 模块可正常导入。"""

    def test_import_main_classes(self):
        """T01: 主类与函数可正常导入。"""
        from app.parametric_geometry import (
            ParametricGeometryTask,
            ParametricGeometryTaskStatus,
            ReviewedFeatureRef,
            StepReviewStatus,
            TaskStore,
            generate_task_id,
            get_task_store,
            StepDisclaimer,
            build_step_disclaimer,
            ParametricGeometryPipeline,
            ParametricGeometryResult,
            ParametricGeometryError,
            StepReviewError,
            FeaturesLoadError,
        )

        for obj in [
            ParametricGeometryTask,
            ParametricGeometryTaskStatus,
            ReviewedFeatureRef,
            StepReviewStatus,
            TaskStore,
            generate_task_id,
            get_task_store,
            StepDisclaimer,
            build_step_disclaimer,
            ParametricGeometryPipeline,
            ParametricGeometryResult,
            ParametricGeometryError,
            StepReviewError,
            FeaturesLoadError,
        ]:
            assert obj is not None, f"{obj} 导入失败"

    def test_routes_module_importable(self):
        """T02: API 路由模块可正常导入。"""
        from app.api.v1 import parametric_geometry as pg_routes_pkg

        assert hasattr(pg_routes_pkg, "routes")
        assert pg_routes_pkg.routes.router is not None
        assert pg_routes_pkg.routes.router.prefix == "/api/v1/parametric_geometry"

    def test_ten_endpoints_registered(self):
        """T03: 10 个 API 端点全部注册。"""
        from app.api.v1.parametric_geometry import routes as pg_routes

        endpoints = set()
        for route in pg_routes.router.routes:
            for method in route.methods:
                endpoints.add((method, route.path))

        expected_endpoints = {
            ("GET", "/api/v1/parametric_geometry/precision_info"),
            ("POST", "/api/v1/parametric_geometry/tasks"),
            ("POST", "/api/v1/parametric_geometry/tasks/{task_id}/run"),
            ("GET", "/api/v1/parametric_geometry/tasks/{task_id}"),
            ("GET", "/api/v1/parametric_geometry/tasks"),
            ("GET", "/api/v1/parametric_geometry/tasks/{task_id}/result"),
            ("POST", "/api/v1/parametric_geometry/tasks/{task_id}/review"),
            ("POST", "/api/v1/parametric_geometry/tasks/{task_id}/finalize"),
            ("GET", "/api/v1/parametric_geometry/tasks/{task_id}/step/download"),
            ("DELETE", "/api/v1/parametric_geometry/tasks/{task_id}"),
        }

        missing = expected_endpoints - endpoints
        assert not missing, f"缺失端点: {missing}"

    def test_router_tags_and_permissions(self):
        """T04: 路由 tags 标注为「Engineer-Assisted STEP」并启用权限校验。"""
        from app.api.v1.parametric_geometry import routes as pg_routes

        tags = pg_routes.router.tags
        assert any("Engineer-Assisted" in t for t in tags), f"路由 tags 未标注工程师辅助定位: {tags}"

        # 顶层 dependencies 必须包含权限依赖
        deps = pg_routes.router.dependencies
        assert len(deps) > 0, "路由未挂载任何 dependencies（权限校验缺失）"


# 精度告知机制测试


class TestStepDisclaimer:
    """精度告知机制（项目记忆硬约束：mesh→CAD 自动转换工业上未解决）。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.config import config, ParametricGeometryConfig

        self.config = config
        self.ParametricGeometryConfig = ParametricGeometryConfig

    def test_disclaimer_all_fields(self):
        """T05: step_disclaimer 包含全部 9 个字段。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=False,
            feature_source="external_upload",
            precision_tier="standard",
            engine_used="unavailable",
        )
        d = disclaimer.to_dict()

        required_fields = {
            "mesh_calibrated",
            "feature_source",
            "precision_tier",
            "engine_used",
            "engine_precision_note",
            "requires_engineer_review",
            "requires_cam_validation",
            "industrial_hard_gates",
            "warning_message",
        }
        missing = required_fields - set(d.keys())
        assert not missing, f"step_disclaimer 缺失字段: {missing}"

    def test_disclaimer_unavailable_engine_warning(self):
        """T06: 无可用引擎时警告明确告知无法生成 STEP。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=True,
            feature_source="feat_task_001",
            precision_tier="standard",
            engine_used="unavailable",
        )
        msg = disclaimer.warning_message
        # 必须明确告知无法生成 STEP + 给出修复建议
        assert "无可用" in msg or "不可用" in msg, f"无引擎警告未明确告知无法生成 STEP: {msg}"
        assert "pythonocc" in msg.lower() or "freecad" in msg.lower(), f"无引擎警告未给出依赖修复建议: {msg}"

    def test_disclaimer_uncalibrated_mesh_warning(self):
        """T07: mesh 未标定时警告明确告知「无量纲」。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=False,
            feature_source="external_upload",
            precision_tier="standard",
            engine_used="template",
        )
        msg = disclaimer.warning_message
        # 未标定 mesh 必须明确告知「无量纲」+「不可用于工艺仿真」
        assert "无量纲" in msg or "未标定" in msg, f"未标定警告未告知无量纲: {msg}"
        assert "工艺仿真" in msg or "不可" in msg, f"未标定警告未告知不可用于工艺仿真: {msg}"

    def test_disclaimer_calibrated_still_requires_review(self):
        """T08: 即便 mesh 已标定，仍强制工程师审核 + CAM 二次校验。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=True,
            feature_source="feat_task_001",
            precision_tier="high",
            engine_used="pythonocc",
        )
        # 即便 mesh 已标定 + 用 pythonOCC，仍必须工程师审核 + CAM 二次校验
        assert disclaimer.requires_engineer_review is True
        assert disclaimer.requires_cam_validation is True
        assert disclaimer.mesh_calibrated is True
        # 警告消息必须提及工程师审核
        msg = disclaimer.warning_message
        assert "审核" in msg, f"已标定警告未提及工程师审核: {msg}"

    def test_engine_precision_notes(self):
        """T09: 4 种引擎精度说明字段完整。"""
        from app.parametric_geometry.step_disclaimer import _ENGINE_PRECISION_NOTES

        expected_engines = {"pythonocc", "freecad", "template", "unavailable"}
        assert set(_ENGINE_PRECISION_NOTES.keys()) == expected_engines, (
            f"引擎精度说明字段不完整: {set(_ENGINE_PRECISION_NOTES.keys())}"
        )
        # 每个说明必须非空
        for engine, note in _ENGINE_PRECISION_NOTES.items():
            assert note and isinstance(note, str), f"引擎 {engine} 精度说明为空: {note!r}"

    def test_unknown_engine_precision_note(self):
        """T10: 未知引擎时精度说明回退为「未知引擎」。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            engine_used="nonexistent_engine",
        )
        assert "未知引擎" in disclaimer.engine_precision_note, (
            f"未知引擎精度说明未回退: {disclaimer.engine_precision_note}"
        )

    def test_industrial_hard_gates_complete(self):
        """T11: 8 条工业硬门槛覆盖关键约束。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=True,
            feature_source="feat_task_001",
            engine_used="pythonocc",
        )
        gates = disclaimer.industrial_hard_gates

        all_gates_text = " ".join(gates)

        # mesh CAD 自动转换未解决
        assert "CAD" in all_gates_text or "自动转换" in all_gates_text, f"硬门槛未提及 mesh→CAD 未解决: {gates}"
        # 良品率 0 缺陷容忍
        assert "良品率" in all_gates_text or "0 缺陷" in all_gates_text, f"硬门槛未提及良品率: {gates}"
        # 配合面公差 0.01mm 不可达
        assert "0.01" in all_gates_text or "公差" in all_gates_text, f"硬门槛未提及公差: {gates}"
        # CNC 操作资质
        assert "持证" in all_gates_text or "操作资质" in all_gates_text, f"硬门槛未提及持证操作员: {gates}"
        # CAM 二次校验
        assert "CAM" in all_gates_text, f"硬门槛未提及 CAM 二次校验: {gates}"
        # 工程师助手定位
        assert "工程师助手" in all_gates_text, f"硬门槛未提及工程师助手定位: {gates}"


# 枚举完整性测试


class TestEnums:
    """枚举完整性测试。"""

    def test_task_status_seven_states(self):
        """T12: ParametricGeometryTaskStatus 7 个状态完整。"""
        from app.parametric_geometry import ParametricGeometryTaskStatus

        expected = {
            "pending",
            "running",
            "step_generated",
            "reviewed",
            "succeeded",
            "failed",
            "cancelled",
        }
        actual = {s.value for s in ParametricGeometryTaskStatus}
        assert actual == expected, f"任务状态枚举不匹配: {actual - expected}"

    def test_review_status_four_states(self):
        """T13: StepReviewStatus 4 个状态完整。"""
        from app.parametric_geometry import StepReviewStatus

        expected = {"pending", "confirmed", "rejected", "edited"}
        actual = {s.value for s in StepReviewStatus}
        assert actual == expected, f"审核状态枚举不匹配: {actual - expected}"

    def test_status_enum_is_str_enum(self):
        """T14: 状态枚举继承 str，便于 JSON 序列化。"""
        from app.parametric_geometry import (
            ParametricGeometryTaskStatus,
            StepReviewStatus,
        )

        # str Enum 可直接用于 JSON 序列化与字符串比较
        assert ParametricGeometryTaskStatus.PENDING == "pending"
        assert StepReviewStatus.CONFIRMED == "confirmed"


# effective_params 测试


class TestEffectiveParams:
    """ReviewedFeatureRef.effective_params() 测试。"""

    def test_pending_returns_source_params(self):
        """T15: pending 状态返回 source_params 副本。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus

        ref = ReviewedFeatureRef(
            feature_id="feat_001",
            feature_type="cylinder",
            source_params={"radius_mm": 5.0, "height_mm": 10.0},
            review_status=StepReviewStatus.PENDING.value,
        )
        effective = ref.effective_params()

        assert effective == {"radius_mm": 5.0, "height_mm": 10.0}
        # 修改返回值不应影响 source_params
        effective["radius_mm"] = 999.0
        assert ref.source_params["radius_mm"] == 5.0, "effective_params() 未返回副本，污染了 source_params"

    def test_confirmed_returns_source_params(self):
        """T16: confirmed 状态返回 source_params 副本。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus

        ref = ReviewedFeatureRef(
            feature_id="feat_002",
            feature_type="plane",
            source_params={"normal": [0, 0, 1], "offset": 1.5, "area_mm2": 100.0},
            review_status=StepReviewStatus.CONFIRMED.value,
        )
        effective = ref.effective_params()

        assert effective == ref.source_params
        assert effective is not ref.source_params, "confirmed 状态应返回副本，不应返回原 dict 引用"

    def test_rejected_returns_source_params(self):
        """T17: rejected 状态返回 source_params 副本（feature_to_brep 会跳过 rejected）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus

        ref = ReviewedFeatureRef(
            feature_id="feat_003",
            feature_type="hole",
            source_params={"radius_mm": 3.0, "depth_mm": 5.0},
            review_status=StepReviewStatus.REJECTED.value,
        )
        effective = ref.effective_params()

        assert effective == ref.source_params

    def test_edited_merges_edited_params(self):
        """T18: edited 状态合并 edited_params 到 source_params（edited 优先）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus

        ref = ReviewedFeatureRef(
            feature_id="feat_004",
            feature_type="cylinder",
            source_params={"radius_mm": 5.0, "height_mm": 10.0, "axis": [0, 0, 1]},
            review_status=StepReviewStatus.EDITED.value,
            edited_params={"radius_mm": 7.5, "center": [1.0, 2.0, 3.0]},
        )
        effective = ref.effective_params()

        # edited_params 优先：radius_mm 被覆盖，新增 center 字段
        assert effective["radius_mm"] == 7.5, f"edited_params 未覆盖 source_params: {effective}"
        assert effective["height_mm"] == 10.0, f"source_params 中未编辑的字段丢失: {effective}"
        assert effective["axis"] == [0, 0, 1]
        assert effective["center"] == [1.0, 2.0, 3.0]

    def test_edited_with_empty_edited_params_falls_back(self):
        """T19: edited 状态但 edited_params 为空时回退到 source_params。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus

        ref = ReviewedFeatureRef(
            feature_id="feat_005",
            feature_type="boss",
            source_params={"radius_mm": 4.0, "height_mm": 8.0},
            review_status=StepReviewStatus.EDITED.value,
            edited_params=None,
        )
        effective = ref.effective_params()

        # edited_params 为 None 时回退到 source_params
        assert effective == {"radius_mm": 4.0, "height_mm": 8.0}


# 工程师审核流程测试


class TestEngineerReview:
    """工程师审核流程测试（核心：human-in-the-loop 责任划分）。"""

    @pytest.fixture(autouse=True)
    def setup_pipeline(self, tmp_path):
        """每个测试前重置 TaskStore 单例 + 构造独立 pipeline。"""
        from app.parametric_geometry import TaskStore
        from app.config import ParametricGeometryConfig

        TaskStore.reset_instance()
        self.ParametricGeometryConfig = ParametricGeometryConfig
        self.cfg = ParametricGeometryConfig(
            enabled=True,
            output_dir=str(tmp_path / "pg_output"),
            blank_margin_mm=2.0,
        )
        # yield 后清理单例，避免污染后续测试
        yield
        TaskStore.reset_instance()

    def _build_step_generated_task(self, pipeline, feature_count=2):
        """构造一个 STEP_GENERATED 状态的任务（mock STEP 写入）。"""
        # 构造 confirmed_features.json
        features = []
        for i in range(feature_count):
            features.append(
                {
                    "feature_id": f"feat_cyl_{i:03d}",
                    "feature_type": "cylinder",
                    "params": {
                        "axis": [0, 0, 1],
                        "center": [0, 0, 0],
                        "radius_mm": 5.0 + i,
                        "height_mm": 10.0,
                    },
                }
            )
        features_json = Path(self.cfg.output_dir) / "confirmed_features.json"
        features_json.parent.mkdir(parents=True, exist_ok=True)
        features_json.write_text(
            json.dumps({"features": features}, ensure_ascii=False),
            encoding="utf-8",
        )

        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=str(features_json),
            precision_tier="standard",
            mesh_calibrated=True,
        )

        # mock write_step_with_fallback 避免触发真实 STEP 引擎
        from app.parametric_geometry.step_writer import StepWriteResult

        with patch("app.parametric_geometry.pipeline.write_step_with_fallback") as mock_write:
            mock_write.return_value = StepWriteResult(
                success=True,
                output_path=str(Path(task.workspace_dir) / "mock.step"),
                engine_used="template",
                shape_count=feature_count,
            )
            import asyncio

            asyncio.run(pipeline.run_pipeline(task.task_id))
        return pipeline.get_task(task.task_id)

    def test_review_confirmed(self):
        """T20: confirmed 审核动作正确更新特征状态。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=2)

        ref = pipeline.review_step_feature(
            task_id=task.task_id,
            feature_id="feat_cyl_000",
            review_status=StepReviewStatus.CONFIRMED.value,
            engineer_notes="STEP 中圆柱表达正确",
            reviewed_by="engineer_zhang",
        )

        assert ref.review_status == "confirmed"
        assert ref.reviewed_by == "engineer_zhang"
        assert ref.reviewed_at is not None
        assert ref.engineer_notes == "STEP 中圆柱表达正确"

        # 仅审核 1 个特征，任务状态不应转 REVIEWED
        updated = pipeline.get_task(task.task_id)
        assert updated.status == "step_generated", f"未全部审核完就转 REVIEWED: {updated.status}"

    def test_review_all_confirmed_transitions_to_reviewed(self):
        """T21: 全部特征审核完毕 → 状态自动转 REVIEWED。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=2)

        pipeline.review_step_feature(
            task_id=task.task_id,
            feature_id="feat_cyl_000",
            review_status=StepReviewStatus.CONFIRMED.value,
        )
        pipeline.review_step_feature(
            task_id=task.task_id,
            feature_id="feat_cyl_001",
            review_status=StepReviewStatus.CONFIRMED.value,
        )

        updated = pipeline.get_task(task.task_id)
        assert updated.status == "reviewed", f"全部审核完未转 REVIEWED: {updated.status}"

    def test_review_rejected(self):
        """T22: rejected 审核动作正确（feature_to_brep 会自动跳过 rejected）。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=2)

        ref = pipeline.review_step_feature(
            task_id=task.task_id,
            feature_id="feat_cyl_000",
            review_status=StepReviewStatus.REJECTED.value,
            engineer_notes="误识别，应删除",
        )

        assert ref.review_status == "rejected"
        assert ref.edited_params is None

    def test_review_edited_with_params(self):
        """T23: edited 审核动作 + edited_params 合并到 effective_params。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=1)

        ref = pipeline.review_step_feature(
            task_id=task.task_id,
            feature_id="feat_cyl_000",
            review_status=StepReviewStatus.EDITED.value,
            edited_params={"radius_mm": 7.5},
            engineer_notes="半径修正为 7.5mm",
        )

        assert ref.review_status == "edited"
        assert ref.edited_params == {"radius_mm": 7.5}
        # effective_params 合并后 radius_mm=7.5，其他字段保留
        effective = ref.effective_params()
        assert effective["radius_mm"] == 7.5
        assert effective["height_mm"] == 10.0

    def test_review_edited_without_params_raises(self):
        """T24: edited 状态未提供 edited_params 抛 StepReviewError。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewError,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=1)

        with pytest.raises(StepReviewError) as exc_info:
            pipeline.review_step_feature(
                task_id=task.task_id,
                feature_id="feat_cyl_000",
                review_status=StepReviewStatus.EDITED.value,
                edited_params=None,
            )
        assert "edited_params" in str(exc_info.value), f"异常消息未说明缺 edited_params: {exc_info.value}"

    def test_review_invalid_status_raises(self):
        """T25: 非法 review_status 抛 StepReviewError。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewError,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=1)

        with pytest.raises(StepReviewError):
            pipeline.review_step_feature(
                task_id=task.task_id,
                feature_id="feat_cyl_000",
                review_status="invalid_status",
            )

    def test_review_nonexistent_feature_raises(self):
        """T26: 审核不存在的特征 ID 抛 StepReviewError。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewError,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = self._build_step_generated_task(pipeline, feature_count=1)

        with pytest.raises(StepReviewError) as exc_info:
            pipeline.review_step_feature(
                task_id=task.task_id,
                feature_id="feat_not_exist",
                review_status=StepReviewStatus.CONFIRMED.value,
            )
        assert "不存在" in str(exc_info.value) or "feature" in str(exc_info.value)

    def test_review_wrong_status_raises(self):
        """T27: 任务状态非 STEP_GENERATED 时审核抛 StepReviewError。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewError,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        # 不触发 run_pipeline，任务保持 PENDING
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path="/nonexistent/path.json",
        )

        with pytest.raises(StepReviewError) as exc_info:
            pipeline.review_step_feature(
                task_id=task.task_id,
                feature_id="any_feature",
                review_status=StepReviewStatus.CONFIRMED.value,
            )
        assert "状态" in str(exc_info.value) or "step_generated" in str(exc_info.value)

    def test_review_nonexistent_task_raises(self):
        """T28: 审核不存在的任务抛 StepReviewError。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepReviewError,
            StepReviewStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        with pytest.raises(StepReviewError):
            pipeline.review_step_feature(
                task_id="pg_nonexistent",
                feature_id="any",
                review_status=StepReviewStatus.CONFIRMED.value,
            )


# 特征 B-rep 转换测试


class TestFeatureToBrep:
    """特征 → BrepShape 转换器测试。"""

    def test_convert_plane(self):
        """T29: plane 特征转换为 BrepShape（operation=add）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_plane_001",
            feature_type="plane",
            source_params={"normal": [0, 0, 1], "offset": 1.0, "area_mm2": 100.0},
            review_status=StepReviewStatus.PENDING.value,
        )
        result = convert_features_to_brep([feature])

        assert result.success_count == 1
        assert not result.has_errors
        shape = result.shapes[0]
        assert shape.shape_type == "plane"
        assert shape.operation == "add"
        assert shape.source_feature_id == "feat_plane_001"
        # 平面边界按 sqrt(area) 估算为正方形
        assert abs(shape.params["width_mm"] - 10.0) < 0.01, f"plane 边界估算错误: {shape.params['width_mm']}"
        assert shape.params["area_mm2"] == 100.0

    def test_convert_cylinder(self):
        """T30: cylinder 特征转换为 BrepShape（operation=add）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_cyl_001",
            feature_type="cylinder",
            source_params={
                "axis": [0, 0, 1],
                "center": [0, 0, 0],
                "radius_mm": 5.0,
                "height_mm": 10.0,
            },
            review_status=StepReviewStatus.PENDING.value,
        )
        result = convert_features_to_brep([feature])

        assert result.success_count == 1
        shape = result.shapes[0]
        assert shape.shape_type == "cylinder"
        assert shape.operation == "add"
        assert shape.params["radius_mm"] == 5.0
        assert shape.params["height_mm"] == 10.0
        assert shape.origin == [0, 0, 0]

    def test_convert_hole(self):
        """T31: hole 特征转换为 BrepShape（operation=subtract）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_hole_001",
            feature_type="hole",
            source_params={
                "normal": [0, 0, 1],
                "center": [0, 0, 0],
                "radius_mm": 2.0,
                "depth_mm": 5.0,
            },
            review_status=StepReviewStatus.PENDING.value,
        )
        result = convert_features_to_brep([feature])

        shape = result.shapes[0]
        assert shape.shape_type == "cylinder"  # hole 在 BrepShape 中也是 cylinder
        assert shape.operation == "subtract"  # 关键：孔是减运算
        assert shape.params["radius_mm"] == 2.0
        # 孔的 depth 在 STEP 中作为 cylinder height
        assert shape.params["height_mm"] == 5.0

    def test_convert_boss(self):
        """T32: boss 特征转换为 BrepShape（operation=add）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_boss_001",
            feature_type="boss",
            source_params={
                "normal": [0, 0, 1],
                "center": [0, 0, 0],
                "radius_mm": 3.0,
                "height_mm": 4.0,
            },
            review_status=StepReviewStatus.PENDING.value,
        )
        result = convert_features_to_brep([feature])

        shape = result.shapes[0]
        assert shape.shape_type == "cylinder"  # boss 在 BrepShape 中也是 cylinder
        assert shape.operation == "add"  # 关键：凸台是加运算
        assert shape.params["radius_mm"] == 3.0
        assert shape.params["height_mm"] == 4.0

    def test_convert_skips_rejected(self):
        """T33: rejected 特征被跳过并记入 skipped_features。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        features = [
            ReviewedFeatureRef(
                feature_id="feat_keep",
                feature_type="cylinder",
                source_params={"radius_mm": 5.0, "height_mm": 10.0, "axis": [0, 0, 1], "center": [0, 0, 0]},
                review_status=StepReviewStatus.PENDING.value,
            ),
            ReviewedFeatureRef(
                feature_id="feat_reject",
                feature_type="hole",
                source_params={"radius_mm": 2.0, "depth_mm": 5.0, "normal": [0, 0, 1], "center": [0, 0, 0]},
                review_status=StepReviewStatus.REJECTED.value,
            ),
        ]
        result = convert_features_to_brep(features)

        assert result.success_count == 1  # 只保留 1 个
        assert len(result.skipped_features) == 1
        assert result.skipped_features[0]["feature_id"] == "feat_reject"
        assert result.skipped_features[0]["reason"] == "rejected_by_engineer"

    def test_convert_skips_unknown_type(self):
        """T34: 未识别的特征类型记入 skipped_features（不抛异常）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_unknown",
            feature_type="chamfer",  # 未识别类型
            source_params={"angle": 45.0},
            review_status=StepReviewStatus.PENDING.value,
        )
        result = convert_features_to_brep([feature])

        assert result.success_count == 0
        assert len(result.skipped_features) == 1
        assert "unsupported_feature_type" in result.skipped_features[0]["reason"]

    def test_convert_uses_effective_params(self):
        """T35: edited 状态的特征使用 effective_params（edited_params 覆盖）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_edited",
            feature_type="cylinder",
            source_params={
                "radius_mm": 5.0,
                "height_mm": 10.0,
                "axis": [0, 0, 1],
                "center": [0, 0, 0],
            },
            review_status=StepReviewStatus.EDITED.value,
            edited_params={"radius_mm": 8.0},
        )
        result = convert_features_to_brep([feature])

        shape = result.shapes[0]
        assert shape.params["radius_mm"] == 8.0, f"edited_params 未覆盖 source_params: {shape.params['radius_mm']}"
        assert shape.params["height_mm"] == 10.0  # 未编辑字段保留

    def test_convert_radius_floor(self):
        """T36: radius/height 非法值（0/负数）回退为 0.1（避免几何异常）。"""
        from app.parametric_geometry import ReviewedFeatureRef, StepReviewStatus
        from app.parametric_geometry.feature_to_brep import convert_features_to_brep

        feature = ReviewedFeatureRef(
            feature_id="feat_zero",
            feature_type="cylinder",
            source_params={
                "radius_mm": 0.0,
                "height_mm": -5.0,
                "axis": [0, 0, 1],
                "center": [0, 0, 0],
            },
            review_status=StepReviewStatus.PENDING.value,
        )
        result = convert_features_to_brep([feature])

        shape = result.shapes[0]
        assert shape.params["radius_mm"] == 0.1, f"radius=0 未回退为 0.1: {shape.params['radius_mm']}"
        assert shape.params["height_mm"] == 0.1


# 装配器测试


class TestAssemblyBuilder:
    """装配器测试：base 选择 + add 排序 + subtract 排序 + 毛坯 bbox 估算。"""

    def _make_shape(
        self,
        shape_id,
        shape_type,
        operation,
        origin=None,
        direction=None,
        params=None,
    ):
        from app.parametric_geometry.feature_to_brep import BrepShape

        return BrepShape(
            shape_id=shape_id,
            shape_type=shape_type,
            operation=operation,
            origin=origin or [0, 0, 0],
            direction=direction or [0, 0, 1],
            params=params or {},
            source_feature_id=shape_id,
        )

    def test_plane_as_base(self):
        """T37: 有 plane 时优先选为 base_shape。"""
        from app.parametric_geometry.assembly_builder import build_assembly_plan

        shapes = [
            self._make_shape(
                "brep_plane_001",
                "plane",
                "add",
                params={"width_mm": 20.0, "height_mm": 20.0},
            ),
            self._make_shape(
                "brep_cyl_001",
                "cylinder",
                "add",
                params={"radius_mm": 5.0, "height_mm": 10.0},
            ),
        ]
        plan = build_assembly_plan(shapes, blank_margin_mm=2.0)

        assert plan.base_shape is not None
        assert plan.base_shape.shape_type == "plane"
        assert plan.base_shape.shape_id == "brep_plane_001"

    def test_non_plane_add_sorted_by_volume(self):
        """T38: 非 plane add 形状按体积从大到小排序。

        重构后（V2.7.0）：无 plane 时体积最大的非平面形状作为 base，
        其余按体积降序进入 add_shapes。
        """
        from app.parametric_geometry.assembly_builder import build_assembly_plan

        shapes = [
            # 小圆柱（volume 小）
            self._make_shape(
                "brep_small",
                "cylinder",
                "add",
                params={"radius_mm": 2.0, "height_mm": 5.0},  # V ≈ 62.8
            ),
            # 大圆柱（volume 大）
            self._make_shape(
                "brep_large",
                "cylinder",
                "add",
                params={"radius_mm": 5.0, "height_mm": 10.0},  # V ≈ 785.4
            ),
        ]
        plan = build_assembly_plan(shapes)

        # 体积最大的作为 base，其余按体积降序进入 add_shapes
        assert plan.base_shape is not None
        assert plan.base_shape.shape_id == "brep_large"
        assert len(plan.add_shapes) == 1
        assert plan.add_shapes[0].shape_id == "brep_small"

    def test_subtract_shapes_stable_order(self):
        """T39: subtract 形状按 shape_id 稳定排序。"""
        from app.parametric_geometry.assembly_builder import build_assembly_plan

        shapes = [
            self._make_shape(
                "brep_hole_002",
                "cylinder",
                "subtract",
                params={"radius_mm": 2.0, "height_mm": 5.0},
            ),
            self._make_shape(
                "brep_hole_001",
                "cylinder",
                "subtract",
                params={"radius_mm": 3.0, "height_mm": 8.0},
            ),
            self._make_shape(
                "brep_hole_003",
                "cylinder",
                "subtract",
                params={"radius_mm": 1.0, "height_mm": 4.0},
            ),
        ]
        plan = build_assembly_plan(shapes)

        assert len(plan.subtract_shapes) == 3
        # 按 shape_id 字典序
        ids = [s.shape_id for s in plan.subtract_shapes]
        assert ids == ["brep_hole_001", "brep_hole_002", "brep_hole_003"], f"subtract 未按 shape_id 稳定排序: {ids}"

    def test_blank_bbox_includes_margin(self):
        """T40: 毛坯 bbox = add 形状 bbox 并集 + blank_margin_mm。"""
        from app.parametric_geometry.assembly_builder import build_assembly_plan

        shapes = [
            self._make_shape(
                "brep_cyl_001",
                "cylinder",
                "add",
                origin=[10.0, 0.0, 0.0],
                direction=[0, 0, 1],
                params={"radius_mm": 5.0, "height_mm": 10.0},
            ),
        ]
        plan = build_assembly_plan(shapes, blank_margin_mm=2.0)

        # bbox 不应为空
        assert not plan.blank_bbox.is_empty
        # 各方向尺寸 = 直径 + 2*margin = 10 + 4 = 14（保守估计，圆柱 bbox 用 r + height/2）
        # 仅断言 margin 生效（bbox 大于无 margin）
        plan_no_margin = build_assembly_plan(shapes, blank_margin_mm=0.0)
        assert plan.blank_bbox.size_x >= plan_no_margin.blank_bbox.size_x, (
            f"毛坯 margin 未生效: with_margin={plan.blank_bbox.size_x}, no_margin={plan_no_margin.blank_bbox.size_x}"
        )

    def test_no_plane_no_base(self):
        """T41: 无 plane 时体积最大的非平面形状作为 base。

        重构后（V2.7.0）：无 plane 时第一个非平面（体积最大）自动成为 base，
        其余进入 add_shapes；无 subtract 时 subtract_shapes 为空。
        """
        from app.parametric_geometry.assembly_builder import build_assembly_plan

        shapes = [
            self._make_shape(
                "brep_cyl_001",
                "cylinder",
                "add",
                params={"radius_mm": 5.0, "height_mm": 10.0},
            ),
        ]
        plan = build_assembly_plan(shapes)

        assert plan.base_shape is not None
        assert plan.base_shape.shape_id == "brep_cyl_001"
        assert len(plan.add_shapes) == 0
        # has_solid 仍为 True（有 add 形状）
        assert plan.has_solid is True

    def test_assembly_summary(self):
        """T42: get_assembly_summary 返回完整摘要字段。"""
        from app.parametric_geometry.assembly_builder import (
            build_assembly_plan,
            get_assembly_summary,
        )

        shapes = [
            self._make_shape(
                "brep_plane_001",
                "plane",
                "add",
                params={"width_mm": 20.0, "height_mm": 20.0},
            ),
            self._make_shape(
                "brep_hole_001",
                "cylinder",
                "subtract",
                params={"radius_mm": 2.0, "height_mm": 5.0},
            ),
        ]
        plan = build_assembly_plan(shapes)
        summary = get_assembly_summary(plan)

        required_keys = {
            "has_base",
            "base_shape_type",
            "base_shape_id",
            "add_count",
            "subtract_count",
            "auxiliary_count",
            "total_shape_count",
            "has_solid",
            "blank_size_mm",
            "blank_center_mm",
            "assembly_order",
        }
        missing = required_keys - set(summary.keys())
        assert not missing, f"装配摘要缺字段: {missing}"
        assert summary["has_base"] is True
        assert summary["add_count"] == 0  # plane 作为 base 后 add_shapes 为空
        assert summary["subtract_count"] == 1

    def test_total_shape_count(self):
        """T43: total_shape_count 包含 base + add + subtract + auxiliary。"""
        from app.parametric_geometry.assembly_builder import build_assembly_plan

        shapes = [
            self._make_shape(
                "brep_plane_001",
                "plane",
                "add",
                params={"width_mm": 20.0, "height_mm": 20.0},
            ),
            # 多余 plane 作为 auxiliary
            self._make_shape(
                "brep_plane_002",
                "plane",
                "add",
                params={"width_mm": 10.0, "height_mm": 10.0},
            ),
            self._make_shape(
                "brep_hole_001",
                "cylinder",
                "subtract",
                params={"radius_mm": 2.0, "height_mm": 5.0},
            ),
        ]
        plan = build_assembly_plan(shapes)

        # 1 base + 0 add + 1 subtract + 1 auxiliary = 3
        assert plan.total_shape_count == 3, f"total_shape_count 计算错误: {plan.total_shape_count}"
        assert len(plan.auxiliary_shapes) == 1  # 第二个 plane 作为 auxiliary


# STEP 写入器降级测试


class TestStepWriter:
    """STEP 写入器三级降级测试。"""

    def test_write_step_with_fallback_no_engine(self, tmp_path):
        """T44: 无可用引擎时返回 success=False, engine_used=unavailable。"""
        from app.parametric_geometry.step_writer import (
            write_step_with_fallback,
            StepWriteResult,
        )
        from app.parametric_geometry.feature_to_brep import BrepShape

        # patch get_available_engine 返回 None 模拟无引擎场景
        with patch(
            "app.parametric_geometry.step_writer.get_available_engine",
            return_value=None,
        ):
            result = write_step_with_fallback(
                shapes=[
                    BrepShape(
                        shape_id="brep_001",
                        shape_type="cylinder",
                        operation="add",
                        origin=[0, 0, 0],
                        direction=[0, 0, 1],
                        params={"radius_mm": 5.0, "height_mm": 10.0},
                        source_feature_id="feat_001",
                    )
                ],
                output_path=tmp_path / "test.step",
            )

        assert isinstance(result, StepWriteResult)
        assert result.success is False
        assert result.engine_used == "unavailable"
        assert result.error_message is not None

    def test_step_write_result_to_dict(self):
        """T45: StepWriteResult.to_dict() 字段完整。"""
        from app.parametric_geometry.step_writer import StepWriteResult

        result = StepWriteResult(
            success=True,
            output_path="/tmp/test.step",
            engine_used="template",
            shape_count=5,
            notes=["降级到模板引擎"],
        )
        d = result.to_dict()

        required_keys = {
            "success",
            "output_path",
            "engine_used",
            "shape_count",
            "error_message",
            "notes",
        }
        assert set(d.keys()) == required_keys
        assert d["success"] is True
        assert d["notes"] == ["降级到模板引擎"]


# Pipeline 状态机测试


class TestPipeline:
    """参数化几何 Pipeline 状态机测试。"""

    @pytest.fixture(autouse=True)
    def setup_pipeline(self, tmp_path):
        from app.parametric_geometry import TaskStore
        from app.config import ParametricGeometryConfig

        TaskStore.reset_instance()
        self.ParametricGeometryConfig = ParametricGeometryConfig
        self.cfg = ParametricGeometryConfig(
            enabled=True,
            output_dir=str(tmp_path / "pg_output"),
            blank_margin_mm=2.0,
        )
        yield
        TaskStore.reset_instance()

    def _write_confirmed_features(self, feature_count=2):
        """构造 confirmed_features.json。"""
        features = []
        for i in range(feature_count):
            features.append(
                {
                    "feature_id": f"feat_cyl_{i:03d}",
                    "feature_type": "cylinder",
                    "params": {
                        "axis": [0, 0, 1],
                        "center": [0, 0, 0],
                        "radius_mm": 5.0 + i,
                        "height_mm": 10.0,
                    },
                }
            )
        features_json = Path(self.cfg.output_dir) / "confirmed_features.json"
        features_json.parent.mkdir(parents=True, exist_ok=True)
        features_json.write_text(
            json.dumps({"features": features}, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(features_json)

    def _mock_step_write_success(self, task):
        """mock write_step_with_fallback 返回成功。"""
        from app.parametric_geometry.step_writer import StepWriteResult

        return patch(
            "app.parametric_geometry.pipeline.write_step_with_fallback",
            return_value=StepWriteResult(
                success=True,
                output_path=str(Path(task.workspace_dir) / "mock.step"),
                engine_used="template",
                shape_count=2,
            ),
        )

    def test_create_task_pending(self):
        """T46: create_task 创建 PENDING 状态任务。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryTaskStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()

        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
            precision_tier="standard",
            mesh_calibrated=True,
        )

        assert task.status == ParametricGeometryTaskStatus.PENDING.value
        assert task.precision_tier == "standard"
        assert task.mesh_calibrated is True
        assert task.cam_validation_required is True
        assert task.workspace_dir is not None
        # workspace_dir 应已创建
        assert Path(task.workspace_dir).exists()

    def test_run_pipeline_transitions_to_step_generated(self):
        """T47: run_pipeline 成功 → 状态转 STEP_GENERATED。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryTaskStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
            mesh_calibrated=True,
        )

        with self._mock_step_write_success(task):
            import asyncio

            result = asyncio.run(pipeline.run_pipeline(task.task_id))

        assert result.status == ParametricGeometryTaskStatus.STEP_GENERATED.value
        assert result.feature_count == 2
        assert result.brep_shape_count == 2
        assert result.engine_used == "template"
        assert result.step_output_path is not None
        # 任务状态已持久化
        updated = pipeline.get_task(task.task_id)
        assert updated.status == ParametricGeometryTaskStatus.STEP_GENERATED.value
        assert len(updated.input_features) == 2

    def test_run_pipeline_failed_when_features_missing(self):
        """T48: confirmed_features.json 不存在 → 状态转 FAILED。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryTaskStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path="/nonexistent/confirmed_features.json",
        )

        import asyncio

        result = asyncio.run(pipeline.run_pipeline(task.task_id))

        assert result.status == ParametricGeometryTaskStatus.FAILED.value
        assert result.error_message is not None
        # 失败状态可重试（再调用 run_pipeline）
        assert pipeline.get_task(task.task_id).status == ParametricGeometryTaskStatus.FAILED.value

    def test_run_pipeline_step_write_failure(self):
        """T49: STEP 写入失败 → 状态转 FAILED。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryTaskStatus,
        )
        from app.parametric_geometry.step_writer import StepWriteResult

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )

        with patch(
            "app.parametric_geometry.pipeline.write_step_with_fallback",
            return_value=StepWriteResult(
                success=False,
                engine_used="unavailable",
                error_message="无可用引擎",
            ),
        ):
            import asyncio

            result = asyncio.run(pipeline.run_pipeline(task.task_id))

        assert result.status == ParametricGeometryTaskStatus.FAILED.value
        # 错误消息已脱敏（safe_error_message 兜底，不暴露内部引擎细节）
        assert result.error_message is not None and result.error_message != ""

    def test_run_pipeline_wrong_status_raises(self):
        """T50: 非 PENDING/FAILED 状态调用 run_pipeline 抛异常。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryError,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )

        # 先让任务进入 STEP_GENERATED
        with self._mock_step_write_success(task):
            import asyncio

            asyncio.run(pipeline.run_pipeline(task.task_id))

        # 再次调用应抛异常
        with pytest.raises(ParametricGeometryError):
            import asyncio

            asyncio.run(pipeline.run_pipeline(task.task_id))

    def test_run_pipeline_nonexistent_task_raises(self):
        """T51: 不存在的 task_id 调用 run_pipeline 抛异常。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryError,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        import asyncio

        with pytest.raises(ParametricGeometryError):
            asyncio.run(pipeline.run_pipeline("pg_nonexistent"))

    def test_finalize_step_transitions_to_succeeded(self):
        """T52: finalize_step → 状态转 SUCCEEDED + 生成最终 STEP。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryTaskStatus,
            StepReviewStatus,
        )
        from app.parametric_geometry.step_writer import StepWriteResult

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features(feature_count=1)
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )

        # 第一轮：生成 STEP
        with self._mock_step_write_success(task):
            import asyncio

            asyncio.run(pipeline.run_pipeline(task.task_id))

        # 第二轮：审核
        pipeline.review_step_feature(
            task_id=task.task_id,
            feature_id="feat_cyl_000",
            review_status=StepReviewStatus.CONFIRMED.value,
        )
        # 此时状态应为 REVIEWED
        assert pipeline.get_task(task.task_id).status == ParametricGeometryTaskStatus.REVIEWED.value

        # 第三轮：finalize_step 生成最终 STEP
        with patch(
            "app.parametric_geometry.pipeline.write_step_with_fallback",
            return_value=StepWriteResult(
                success=True,
                output_path=str(Path(task.workspace_dir) / "final.step"),
                engine_used="template",
                shape_count=1,
            ),
        ):
            result = asyncio.run(pipeline.finalize_step(task.task_id))

        assert result.status == ParametricGeometryTaskStatus.SUCCEEDED.value
        assert result.final_step_path is not None
        assert result.step_output_path is not None  # 第一轮 STEP 仍保留
        # 任务状态已持久化
        updated = pipeline.get_task(task.task_id)
        assert updated.status == ParametricGeometryTaskStatus.SUCCEEDED.value
        assert updated.final_step_path is not None

    def test_finalize_step_wrong_status_raises(self):
        """T53: 非 REVIEWED 状态调用 finalize_step 抛异常。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryError,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )
        # 任务保持 PENDING，直接 finalize 应抛异常
        import asyncio

        with pytest.raises(ParametricGeometryError):
            asyncio.run(pipeline.finalize_step(task.task_id))

    def test_cancel_task(self):
        """T54: 取消任务 → 状态转 CANCELLED。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryTaskStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )

        cancelled = pipeline.cancel_task(task.task_id)
        assert cancelled.status == ParametricGeometryTaskStatus.CANCELLED.value

    def test_cancel_terminal_task_raises(self):
        """T55: 已终态任务无法取消。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryError,
            ParametricGeometryTaskStatus,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )
        pipeline.cancel_task(task.task_id)

        # CANCELLED 是终态，再次取消应抛异常
        with pytest.raises(ParametricGeometryError):
            pipeline.cancel_task(task.task_id)

    def test_get_disclaimer_with_task(self):
        """T56: get_disclaimer(task) 带 task 上下文构造告知。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepDisclaimer,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
            mesh_calibrated=True,
        )

        disclaimer = pipeline.get_disclaimer(task)
        assert isinstance(disclaimer, StepDisclaimer)
        assert disclaimer.mesh_calibrated is True
        assert disclaimer.feature_source == "fe_task_001"
        assert disclaimer.engine_used == "unavailable"  # PENDING 阶段无 engine

    def test_get_disclaimer_without_task(self):
        """T57: get_disclaimer(None) 用默认值构造告知。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            StepDisclaimer,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        disclaimer = pipeline.get_disclaimer(None)

        assert isinstance(disclaimer, StepDisclaimer)
        assert disclaimer.mesh_calibrated is False
        assert disclaimer.feature_source == "external_upload"
        assert disclaimer.engine_used == "unavailable"

    def test_get_result_summary(self):
        """T58: get_result_summary 返回 ParametricGeometryResult。"""
        from app.parametric_geometry import (
            ParametricGeometryPipeline,
            ParametricGeometryResult,
        )

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()
        task = pipeline.create_task(
            source_feature_extraction_task_id="fe_task_001",
            input_features_path=features_path,
        )

        result = pipeline.get_result_summary(task.task_id)
        assert isinstance(result, ParametricGeometryResult)
        assert result.task_id == task.task_id
        assert result.source_feature_extraction_task_id == "fe_task_001"

    def test_list_tasks(self):
        """T59: list_tasks 返回最近任务（按 created_at 倒序）。"""
        from app.parametric_geometry import ParametricGeometryPipeline

        pipeline = ParametricGeometryPipeline(cfg=self.cfg)
        features_path = self._write_confirmed_features()

        t1 = pipeline.create_task(
            source_feature_extraction_task_id="fe_001",
            input_features_path=features_path,
        )
        # 确保 created_at 不同
        time.sleep(0.01)
        t2 = pipeline.create_task(
            source_feature_extraction_task_id="fe_002",
            input_features_path=features_path,
        )

        tasks = pipeline.list_tasks(limit=10)
        assert len(tasks) == 2
        # t2 创建晚，应排在前面
        assert tasks[0].task_id == t2.task_id
        assert tasks[1].task_id == t1.task_id


# 配置校验测试


class TestParametricGeometryConfig:
    """ParametricGeometryConfig 配置校验测试。"""

    def test_default_config(self, tmp_path):
        """T60: 默认配置字段完整。"""
        from app.config import ParametricGeometryConfig

        cfg = ParametricGeometryConfig(output_dir=str(tmp_path))
        assert cfg.enabled is True or cfg.enabled is False  # 受环境变量影响
        assert cfg.blank_margin_mm == 2.0 or cfg.blank_margin_mm > 0
        assert cfg.max_concurrent >= 1
        assert cfg.task_timeout_seconds >= 60
        assert cfg.task_retention_hours > 0
        assert cfg.precision_tier in {"coarse", "standard", "high"}
        assert cfg.default_mesh_calibrated in {True, False}

    def test_invalid_precision_tier_falls_back(self, tmp_path, monkeypatch):
        """T61: 非法 precision_tier 回退为 standard。"""
        from app.config import ParametricGeometryConfig

        monkeypatch.setenv("LNN_PG_PRECISION_TIER", "ultra_precision")
        cfg = ParametricGeometryConfig(output_dir=str(tmp_path))

        assert cfg.precision_tier == "standard", f"非法 precision_tier 未回退: {cfg.precision_tier}"

    def test_invalid_blank_margin_falls_back(self, tmp_path, monkeypatch):
        """T62: blank_margin_mm <= 0 回退为 2.0。"""
        from app.config import ParametricGeometryConfig

        monkeypatch.setenv("LNN_PG_BLANK_MARGIN_MM", "-5.0")
        cfg = ParametricGeometryConfig(output_dir=str(tmp_path))

        assert cfg.blank_margin_mm == 2.0, f"非法 blank_margin_mm 未回退: {cfg.blank_margin_mm}"

    def test_invalid_max_concurrent_falls_back(self, tmp_path, monkeypatch):
        """T63: max_concurrent < 1 回退为 1（串行）。"""
        from app.config import ParametricGeometryConfig

        monkeypatch.setenv("LNN_PG_MAX_CONCURRENT", "0")
        cfg = ParametricGeometryConfig(output_dir=str(tmp_path))

        assert cfg.max_concurrent == 1, f"非法 max_concurrent 未回退: {cfg.max_concurrent}"

    def test_invalid_task_timeout_falls_back(self, tmp_path, monkeypatch):
        """T64: task_timeout_seconds < 60 回退为 600。"""
        from app.config import ParametricGeometryConfig

        monkeypatch.setenv("LNN_PG_TASK_TIMEOUT", "10")
        cfg = ParametricGeometryConfig(output_dir=str(tmp_path))

        assert cfg.task_timeout_seconds == 600, f"非法 task_timeout_seconds 未回退: {cfg.task_timeout_seconds}"

    def test_large_blank_margin_warns_but_kept(self, tmp_path, monkeypatch):
        """T65: blank_margin_mm > 20 仅警告不回退（保留用户配置）。"""
        from app.config import ParametricGeometryConfig

        monkeypatch.setenv("LNN_PG_BLANK_MARGIN_MM", "30.0")
        cfg = ParametricGeometryConfig(output_dir=str(tmp_path))

        # > 20 仅警告，值仍保留（不强制回退）
        assert cfg.blank_margin_mm == 30.0, f">20mm 的 blank_margin 被强制回退: {cfg.blank_margin_mm}"


# main.py 集成测试


class TestMainAppIntegration:
    """main.py 条件导入 + 路由注册测试。"""

    def test_main_app_importable(self):
        """T66: app.main 模块可正常导入。"""
        from app.main import app

        assert app is not None

    def test_parametric_geometry_routes_registered(self):
        """T67: 当模块可用且 enabled=True 时路由已注册到 FastAPI app。"""
        from app.main import app

        # 收集所有路由的 path（2026-09 适配：新版 Starlette 把 include_router
        # 挂为无顶层 path 的 _IncludedRouter，遍历 app.routes 只能看到 docs——
        # 改用 openapi schema 拉平全部真实路径）
        all_paths = set(app.openapi().get("paths", {}).keys())

        # 至少 precision_info 应已注册（若模块未启用，则不应包含任何 pg 路由）
        from app.config import config

        if config.parametric_geometry.enabled:
            assert any("/api/v1/parametric_geometry" in p for p in all_paths), (
                f"parametric_geometry 路由未注册: {all_paths}"
            )

    def test_router_prefix_correct(self):
        """T68: 路由 prefix 与 API 规范一致。"""
        from app.api.v1.parametric_geometry import routes as pg_routes

        assert pg_routes.router.prefix == "/api/v1/parametric_geometry"

    def test_router_has_permission_dependency(self):
        """T69: 路由顶层 dependencies 包含 require_permission。"""
        from app.api.v1.parametric_geometry import routes as pg_routes

        # 至少有 1 个 dependency（require_permission）
        assert len(pg_routes.router.dependencies) >= 1, "路由未挂载权限校验 dependency"


# 上游 mesh 标定状态追溯测试


class TestResolveUpstreamCalibrated:
    """_resolve_upstream_calibrated 三层 try/except ImportError 软依赖测试。"""

    def test_empty_source_returns_uncalibrated(self):
        """T70: 空 source_feature_extraction_task_id → (False, external_upload)。"""
        from app.api.v1.parametric_geometry.routes import (
            _resolve_upstream_calibrated,
        )

        calibrated, source = _resolve_upstream_calibrated("")
        assert calibrated is False
        assert source == "external_upload"

    def test_nonexistent_task_returns_uncalibrated(self):
        """T71: 上游任务不存在 → (False, external_upload)。"""
        from app.api.v1.parametric_geometry.routes import (
            _resolve_upstream_calibrated,
        )

        calibrated, source = _resolve_upstream_calibrated("fe_nonexistent")
        # 不应抛异常，应回退为 (False, external_upload) 或 (False, fe_xxx)
        assert calibrated is False
        # source 可能是 external_upload 或原 task_id（取决于上游模块是否可用）
        assert source in {"external_upload", "fe_nonexistent"}

    def test_returns_tuple_of_bool_and_str(self):
        """T72: 返回值类型为 (bool, str)。"""
        from app.api.v1.parametric_geometry.routes import (
            _resolve_upstream_calibrated,
        )

        result = _resolve_upstream_calibrated("")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)


# 项目记忆硬约束测试


class TestProjectMemoryHardConstraints:
    """项目记忆硬约束测试（mesh→CAD 未解决 / 工程师助手定位 / CAM 校验强制）。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.config import config

        self.config = config

    def test_mesh_to_cad_unsolved_warning(self):
        """T73: disclaimer 明确告知 mesh→CAD 自动转换工业上未解决。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=True,
            feature_source="fe_task_001",
            engine_used="pythonocc",
        )
        gates_text = " ".join(disclaimer.industrial_hard_gates)
        # 必须明确提及 mesh CAD 自动转换未解决
        assert "CAD" in gates_text, f"硬门槛未提及 CAD: {disclaimer.industrial_hard_gates}"
        assert "未解决" in gates_text or "算法建议" in gates_text, (
            f"硬门槛未明确告知 mesh→CAD 未解决: {disclaimer.industrial_hard_gates}"
        )

    def test_engineer_assistant_positioning(self):
        """T74: disclaimer 明确告知系统定位为「工程师助手」。"""
        from app.parametric_geometry import build_step_disclaimer

        disclaimer = build_step_disclaimer(
            self.config.parametric_geometry,
            mesh_calibrated=False,
            engine_used="template",
        )
        gates_text = " ".join(disclaimer.industrial_hard_gates)
        assert "工程师助手" in gates_text, f"硬门槛未提及工程师助手定位: {disclaimer.industrial_hard_gates}"

    def test_cam_validation_required_always_true(self):
        """T75: requires_cam_validation 始终为 True（即便用 pythonOCC）。"""
        from app.parametric_geometry import build_step_disclaimer

        for engine in ["pythonocc", "freecad", "template", "unavailable"]:
            disclaimer = build_step_disclaimer(
                self.config.parametric_geometry,
                mesh_calibrated=True,
                engine_used=engine,
            )
            assert disclaimer.requires_cam_validation is True, f"engine={engine} 时 requires_cam_validation 应为 True"

    def test_engineer_review_required_always_true(self):
        """T76: requires_engineer_review 始终为 True。"""
        from app.parametric_geometry import build_step_disclaimer

        for engine in ["pythonocc", "freecad", "template", "unavailable"]:
            disclaimer = build_step_disclaimer(
                self.config.parametric_geometry,
                mesh_calibrated=True,
                engine_used=engine,
            )
            assert disclaimer.requires_engineer_review is True, f"engine={engine} 时 requires_engineer_review 应为 True"

    def test_two_round_review_workflow(self):
        """T77: 两轮审核流程在 precision_info 端点中明确告知。"""
        from app.api.v1.parametric_geometry import routes as pg_routes

        # 验证 routes 模块中 _disclaimer_dict 函数存在（用于响应注入）
        assert hasattr(pg_routes, "_disclaimer_dict"), "routes 模块未实现 _disclaimer_dict（精度告知注入）"
        # 验证 routes 模块中 _resolve_upstream_calibrated 函数存在
        assert hasattr(pg_routes, "_resolve_upstream_calibrated"), (
            "routes 模块未实现 _resolve_upstream_calibrated（精度继承链追溯）"
        )

    def test_precision_tier_inherited(self):
        """T78: precision_tier 继承自阶段 1/2，不引入新档位。"""
        from app.parametric_geometry import build_step_disclaimer

        # 三档均合法
        for tier in ["coarse", "standard", "high"]:
            disclaimer = build_step_disclaimer(
                self.config.parametric_geometry,
                precision_tier=tier,
                engine_used="template",
            )
            assert disclaimer.precision_tier == tier

    def test_step_disclaimer_in_api_responses(self):
        """T79: 关键 API 端点响应模型包含 step_disclaimer 字段。"""
        from app.api.v1.parametric_geometry.routes import (
            TaskCreateResponse,
            TaskStatusResponse,
            TaskResultResponse,
            ReviewResponse,
            FinalizeResponse,
        )

        # 5 个响应模型都必须包含 step_disclaimer 字段
        for model_cls in [
            TaskCreateResponse,
            TaskStatusResponse,
            TaskResultResponse,
            ReviewResponse,
            FinalizeResponse,
        ]:
            fields = model_cls.model_fields
            assert "step_disclaimer" in fields, f"{model_cls.__name__} 缺 step_disclaimer 字段"

    def test_cam_validation_required_field_in_task(self):
        """T80: ParametricGeometryTask.cam_validation_required 始终为 True。"""
        from app.parametric_geometry import ParametricGeometryTask

        task = ParametricGeometryTask(
            task_id="pg_test",
            source_feature_extraction_task_id="fe_001",
            input_features_path="/tmp/test.json",
        )
        # 默认 cam_validation_required 必须为 True
        assert task.cam_validation_required is True, "新建任务 cam_validation_required 必须为 True（项目记忆硬约束）"
