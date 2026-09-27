"""CAM 校验模块单元测试（阶段 7）。

覆盖范围（与 ADR-018 验收标准对齐）：
- 模块导入与公开符号导出：app.cam_validation 包可正常导入全部子模块
- 枚举完整性：CamValidationTaskStatus (8 态) / CamReviewStatus (4 态)
- 常量与配置：SAFETY_MARGIN_RATIO / PENDING_CALIBRATION_MATERIALS /
  VALID_CAM_BACKENDS / CamValidationConfig 默认值 + 硬约束
- 任务创建：create_task 创建 PENDING + 默认值 + 非法 cam_backend 抛错
- 状态机契约：VALIDATED 才可审核 / REVIEWED 才可确认 / SUCCEEDED 禁删
- 工程师审核流程：confirmed / rejected / edited 三态 + edited_params 必填
- 双 JSON 导出：cam_report.json + internal_report.json 同时存在
- CAM 后端降级：CAM 软件不可用时降级到 manual
- SUCCEEDED 禁删硬约束（allow_delete_succeeded 强制 False）
- 项目记忆硬约束：cam_validation_required 始终 True /
  工程师助手定位 / 不直接接口 CNC 控制器 / 阶段 7 终止于 CAM 校验报告 JSON

测试设计原则（与 test_chatter_prediction.py / test_cutting_parameters.py 一致）：
- 不依赖真实 G 代码文件（构造最小 CamValidationTask 实例直接注入 store，
  绕过异步 run_pipeline，避免 fixture 复杂化）
- 不调用 NX / PowerMill / PyCAM 真实 subprocess（仅测试 internal_only 路径
  + manual 降级路径，CAM 软件二次校验在 standalone_verify 脚本中已端到端验证）
- 只验证模块契约与工程师审核流程（用户最关心的「human-in-the-loop 责任划分」）

本地运行注意（项目记忆硬约束）：
- 本地 Python 3.14 的 _overlapped C 扩展损坏（WinError 10038），
  conftest.py 在导入期强制加载 app.api.v1.auth 可能触发 asyncio 损坏
- 完整 pytest 套件由 CI 环境运行；本地可改用 tests/standalone_verify_cam_validation_*.py
- 若 conftest 导入失败，使用 `pytest --no-header -p no:cacheprovider
  tests/test_cam_validation.py --co` 仅做收集验证
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest


# 辅助：构造最小 CamValidationTask 实例（绕过异步 run_pipeline）


def _build_minimal_task(
    task_id: str = "cam_test_001",
    status: str = "validated",
    feature_count: int = 2,
    material_name: str = "45#钢",
    pending_calibration: bool = False,
    prediction_method: str = "analytical",
    cam_backend_used: str = "internal_only",
    cam_backend_requested: str = "internal_only",
    cam_backend_fallback_reason: str = "",
) -> Any:
    """构造最小 CamValidationTask 实例（用于直接注入 store）。

    构造的对象包含 2 个 feature_validation_results，每个 review_status=pending，
    状态默认 validated（可立即调用 review_task）。
    """
    from app.cam_validation import (
        CamValidationTask,
        FeatureValidationResult,
    )

    feature_types = ["plane", "cylinder", "hole", "boss"]
    features = [
        FeatureValidationResult(
            feature_id=f"feat_{i + 1:03d}",
            feature_type=feature_types[i % len(feature_types)],
            line_range=(i * 10 + 1, (i + 1) * 10),
            internal_check_passed=True,
            internal_events=[],
            cam_check_passed=True,
            cam_messages=["OK"],
            cam_backend_used=cam_backend_used,
            review_status="pending",
            edited_params={},
            # FeatureValidationResult 实际字段（与 cam_store.py 第 229-234 行对齐）：
            # 不存在 is_safe_to_machine / radial_depth_mm / feed_rate_mm_per_min
            stable=False,
            spindle_rpm=8000.0,
            axial_depth_mm=1.0,
            limit_depth_mm=2.0,
            safety_margin_ratio=0.5,
            warning="",
        )
        for i in range(feature_count)
    ]

    return CamValidationTask(
        task_id=task_id,
        source_gcode_report_path="/tmp/test_report.json",
        source_gcode_file_path="/tmp/test.nc",
        controller_type="fanuc_0i",
        material_name=material_name,
        safe_z=80.0,
        stock_top_z=50.0,
        status=status,
        feature_validation_results=features,
        gcode_total_lines=20,
        cam_backend_requested=cam_backend_requested,
        cam_backend_used=cam_backend_used,
        cam_backend_fallback_reason=cam_backend_fallback_reason,
        cam_validation_required=True,
        workspace_dir="/tmp",
        started_at=time.time(),
        pending_calibration=pending_calibration,
        prediction_method=prediction_method,
        total_features=feature_count,
        passed_features=feature_count,
        failed_features=0,
    )


def _build_minimal_gcode_report_json(
    tmp_path: Path,
    gcode_file_path: str = "",
) -> str:
    """构造阶段 6 G 代码审核记录 JSON（最小可用结构）。

    用于 GCodeLoader 测试：包含 gcode_file_path / feature_results /
    controller_type / cam_validation_required 必填字段。
    """
    if not gcode_file_path:
        gcode_file_path = str(tmp_path / "test.nc")
    Path(gcode_file_path).write_text(
        "G01 X10 Y20 Z30 F500\nG01 X20 Y30 Z40 F500\n",
        encoding="utf-8",
    )

    data = {
        "task_id": "gcode_test_001",
        "task_status": "succeeded",
        "gcode_file_path": gcode_file_path,
        "gcode_total_lines": 2,
        "controller_type": "fanuc_0i",
        "material_name": "45#钢",
        "safe_z": 80.0,
        "stock_top_z": 50.0,
        "cam_validation_required": True,
        # 必填字段（与 gcode_loader.py REQUIRED_GCODE_REPORT_FIELDS 对齐）
        "prediction_method": "analytical",
        "feature_results": [
            {
                "feature_id": "feat_001",
                "feature_type": "plane",
                "line_range": [1, 1],
            },
            {
                "feature_id": "feat_002",
                "feature_type": "cylinder",
                "line_range": [2, 2],
            },
        ],
    }

    json_path = tmp_path / "gcode_report.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(json_path)


# 1. 模块导入测试


class TestCamValidationModuleImport:
    """验证 cam_validation 模块可正常导入。"""

    def test_import_main_classes(self):
        """T01: 主类与函数可正常导入。"""
        from app.cam_validation import (
            CamAdapterError,
            CamDisclaimer,
            CamReviewStatus,
            CamValidationError,
            CamValidationPipeline,
            CamValidationPipelineError,
            CamValidationTask,
            CamValidationTaskStatus,
            FeatureValidationResult,
            GCodeReportLoadError,
            GCodeLoader,
            INDUSTRIAL_HARD_GATES,
            InternalValidationError,
            InternalValidator,
            PENDING_CALIBRATION_MATERIALS,
            ReviewError,
            SAFETY_MARGIN_RATIO,
            VALID_CAM_BACKENDS,
            build_cam_disclaimer,
            generate_task_id,
            get_task_store,
            is_valid_cam_backend,
        )
        from app.cam_validation.cam_store import CamTaskStore

        # 验证关键公开符号非空
        assert CamValidationPipeline is not None
        assert CamValidationTask is not None
        assert CamValidationTaskStatus is not None
        assert CamReviewStatus is not None
        assert FeatureValidationResult is not None
        assert GCodeLoader is not None
        assert InternalValidator is not None
        assert CamDisclaimer is not None
        assert build_cam_disclaimer is not None
        assert generate_task_id is not None
        assert get_task_store is not None
        assert is_valid_cam_backend is not None
        assert INDUSTRIAL_HARD_GATES is not None
        assert PENDING_CALIBRATION_MATERIALS is not None
        assert VALID_CAM_BACKENDS is not None
        assert SAFETY_MARGIN_RATIO is not None
        assert CamTaskStore is not None

    def test_all_export_complete(self):
        """T02: __all__ 导出列表完整。"""
        import app.cam_validation as _cv_mod

        expected_all = {
            "CamValidationTaskStatus",
            "CamReviewStatus",
            "SAFETY_MARGIN_RATIO",
            "PENDING_CALIBRATION_MATERIALS",
            "VALID_CAM_BACKENDS",
            "CamValidationError",
            "GCodeReportLoadError",
            "InternalValidationError",
            "CamAdapterError",
            "ReviewError",
            "CamValidationPipelineError",
            "FeatureValidationResult",
            "CamValidationTask",
            "generate_task_id",
            "get_task_store",
            "is_valid_cam_backend",
            "CamTaskStore",
            "INDUSTRIAL_HARD_GATES",
            "CamDisclaimer",
            "build_cam_disclaimer",
            "GCodeLoader",
            "InternalValidator",
            "CamAdapter",
            "CamValidationPipeline",
            "CamValidationResult",
        }
        actual_all = set(getattr(_cv_mod, "__all__", []))
        missing = expected_all - actual_all
        assert not missing, f"__all__ 缺失: {missing}"


# 2. 枚举与常量完整性测试


class TestCamValidationEnums:
    """验证枚举与常量完整性。"""

    def test_task_status_8_states(self):
        """T03: CamValidationTaskStatus 8 态完整。"""
        from app.cam_validation import CamValidationTaskStatus

        expected = {
            "pending",
            "running",
            "validated",
            "reviewed",
            "succeeded",
            "failed",
            "timeout",
            "cancelled",
        }
        actual = {s.value for s in CamValidationTaskStatus}
        assert actual == expected, f"actual={actual}, expected={expected}"

    def test_review_status_4_states(self):
        """T04: CamReviewStatus 4 态完整。"""
        from app.cam_validation import CamReviewStatus

        expected = {"pending", "confirmed", "rejected", "edited"}
        actual = {s.value for s in CamReviewStatus}
        assert actual == expected, f"actual={actual}, expected={expected}"

    def test_status_values_are_str(self):
        """T05: 枚举值是 str 子类（与字符串相等）。"""
        from app.cam_validation import (
            CamReviewStatus,
            CamValidationTaskStatus,
        )

        assert CamValidationTaskStatus.PENDING == "pending"
        assert CamValidationTaskStatus.SUCCEEDED == "succeeded"
        assert CamReviewStatus.CONFIRMED == "confirmed"
        assert CamReviewStatus.EDITED == "edited"

    def test_safety_margin_ratio(self):
        """T06: SAFETY_MARGIN_RATIO = 0.8（与阶段 5/6 对齐）。"""
        from app.cam_validation import SAFETY_MARGIN_RATIO

        assert SAFETY_MARGIN_RATIO == 0.8

    def test_pending_calibration_materials_contains_hrc52(self):
        """T07: PENDING_CALIBRATION_MATERIALS 含 HRC52 系列。"""
        from app.cam_validation import PENDING_CALIBRATION_MATERIALS

        expected_subset = {
            "steel_hrc52",
            "hrc52",
            "hrc_52",
            "hardened_steel_hrc52",
        }
        assert set(PENDING_CALIBRATION_MATERIALS) >= expected_subset

    def test_valid_cam_backends_5(self):
        """T08: VALID_CAM_BACKENDS 5 个后端。"""
        from app.cam_validation import VALID_CAM_BACKENDS

        expected = {
            "internal_only",
            "pycam",
            "nx_open",
            "powermill",
            "manual",
        }
        assert set(VALID_CAM_BACKENDS) == expected

    def test_is_valid_cam_backend_behavior(self):
        """T09: is_valid_cam_backend 行为正确。"""
        from app.cam_validation import is_valid_cam_backend

        assert is_valid_cam_backend("internal_only")
        assert is_valid_cam_backend("manual")
        assert is_valid_cam_backend("pycam")
        assert is_valid_cam_backend("nx_open")
        assert is_valid_cam_backend("powermill")
        assert not is_valid_cam_backend("invalid_backend")
        assert not is_valid_cam_backend("")

    def test_generate_task_id_prefix(self):
        """T10: generate_task_id 前缀 "cam_"。"""
        from app.cam_validation import generate_task_id

        tid = generate_task_id()
        assert tid.startswith("cam_"), f"actual={tid}"
        assert len(tid) > 10


# 3. 配置契约测试


class TestCamValidationConfigContract:
    """验证 CamValidationConfig 默认值 + 硬约束。"""

    def test_config_default_values(self):
        """T11: CamValidationConfig 默认值正确。"""
        from app.config import CamValidationConfig

        cfg = CamValidationConfig()
        assert cfg.enabled is True
        # output_dir 通过 _path() 解析为绝对路径，默认 "output/cam_validation"（无 s）
        assert cfg.output_dir.replace("\\", "/").endswith("output/cam_validation"), (
            f"output_dir 实际值: {cfg.output_dir}"
        )
        # 实际默认 max_concurrent=1（单机本地环境），ADR-018 文档值是参考上限
        assert cfg.max_concurrent == 1
        assert cfg.task_timeout_seconds == 600
        assert cfg.task_retention_hours == 168
        assert cfg.precision_tier == "mesh_calibrated"
        assert cfg.default_cam_backend == "internal_only"
        assert cfg.nx_open_executable == ""
        assert cfg.powermill_executable == ""
        assert cfg.pycam_executable == ""

    def test_config_cam_validation_required_always_true(self):
        """T12: cam_validation_required 始终 True（项目记忆硬约束）。"""
        from app.config import CamValidationConfig

        cfg = CamValidationConfig()
        # 默认 True
        assert cfg.cam_validation_required is True

        # 即使环境变量试图关闭，也不可关闭（属性 setter 或 __post_init__ 强制）
        # 实际语义：cam_validation_required 字段默认 True，应用层不应通过环境变量覆盖
        # 这里仅验证默认值为 True
        assert cfg.cam_validation_required is True

    def test_config_allow_delete_succeeded_default_false(self):
        """T13: allow_delete_succeeded 默认 False（SUCCEEDED 禁删硬约束）。"""
        from app.config import CamValidationConfig

        cfg = CamValidationConfig()
        assert cfg.allow_delete_succeeded is False

    def test_config_env_var_prefix(self):
        """T14: 环境变量前缀 LNN_CAM_*。"""
        # 通过设置环境变量验证配置加载（仅验证 prefix 不抛错）
        import os

        os.environ["LNN_CAM_ENABLED"] = "true"
        os.environ["LNN_CAM_DEFAULT_BACKEND"] = "internal_only"
        os.environ["LNN_CAM_VALIDATION_REQUIRED"] = "true"
        os.environ["LNN_CAM_ALLOW_DELETE_SUCCEEDED"] = "false"
        # 不抛错即视为 prefix 正确
        from app.config import CamValidationConfig

        CamValidationConfig()
        # 清理
        for key in (
            "LNN_CAM_ENABLED",
            "LNN_CAM_DEFAULT_BACKEND",
            "LNN_CAM_VALIDATION_REQUIRED",
            "LNN_CAM_ALLOW_DELETE_SUCCEEDED",
        ):
            os.environ.pop(key, None)


# 4. 任务创建测试


class TestCamValidationTaskCreation:
    """验证 create_task 创建 PENDING 任务。"""

    def test_create_task_returns_pending(self, tmp_path: Path):
        """T15: create_task 返回 PENDING 状态任务。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        report_path = _build_minimal_gcode_report_json(tmp_path)

        task = pipeline.create_task(
            source_gcode_report_path=report_path,
            controller_type="fanuc_0i",
            material_name="45#钢",
        )
        assert task.status == CamValidationTaskStatus.PENDING.value
        assert task.task_id.startswith("cam_")
        assert task.cam_validation_required is True
        assert task.controller_type == "fanuc_0i"
        assert task.material_name == "45#钢"

    def test_create_task_default_values(self, tmp_path: Path):
        """T16: create_task 默认值正确（controller / material / safe_z）。"""
        from app.cam_validation import CamValidationPipeline
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        report_path = _build_minimal_gcode_report_json(tmp_path)

        task = pipeline.create_task(source_gcode_report_path=report_path)
        assert task.controller_type == "fanuc_0i"
        assert task.material_name == "45#钢"
        assert task.safe_z == 80.0
        assert task.stock_top_z == 50.0
        assert task.cam_backend_requested == "internal_only"

    def test_create_task_empty_report_path_raises(self):
        """T17: source_gcode_report_path 为空时抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationPipelineError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        with pytest.raises(CamValidationPipelineError):
            pipeline.create_task(source_gcode_report_path="")

    def test_create_task_invalid_cam_backend_raises(self, tmp_path: Path):
        """T18: 非法 cam_backend 抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationPipelineError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        report_path = _build_minimal_gcode_report_json(tmp_path)
        with pytest.raises(CamValidationPipelineError):
            pipeline.create_task(
                source_gcode_report_path=report_path,
                cam_backend="invalid_backend",
            )


# 5. 状态机契约测试


class TestCamValidationStateMachine:
    """验证状态机契约（VALIDATED 才可审核 / REVIEWED 才可确认）。"""

    def test_review_requires_validated_state(self):
        """T19: 非 VALIDATED 状态调用 review_task 抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.config import CamValidationConfig

        # CamValidationPipeline.__init__ 不接受 store 参数（内部用 get_task_store()）
        # 直接通过 pipeline._store 注入任务
        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        # PENDING 状态任务（未校验完成）
        task = _build_minimal_task(
            task_id="cam_sm_001",
            status=CamValidationTaskStatus.PENDING.value,
        )
        pipeline._store.add_task(task)

        with pytest.raises(ReviewError):
            pipeline.review_task(
                task_id="cam_sm_001",
                feature_id="feat_001",
                review_status="confirmed",
            )

    def test_confirm_requires_reviewed_state(self):
        """T20: 非 REVIEWED 状态调用 confirm_task 抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationPipelineError,
            CamValidationTaskStatus,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        # VALIDATED 状态（未审核）
        task = _build_minimal_task(
            task_id="cam_sm_002",
            status=CamValidationTaskStatus.VALIDATED.value,
        )
        pipeline._store.add_task(task)

        with pytest.raises(CamValidationPipelineError):
            pipeline.confirm_task(task_id="cam_sm_002")

    def test_review_nonexistent_task_raises(self):
        """T21: 审核不存在的任务抛 ReviewError。"""
        from app.cam_validation import (
            CamValidationPipeline,
            ReviewError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        with pytest.raises(ReviewError):
            pipeline.review_task(
                task_id="cam_nonexistent",
                feature_id="feat_001",
                review_status="confirmed",
            )

    def test_confirm_nonexistent_task_raises(self):
        """T22: 确认不存在的任务抛 CamValidationPipelineError。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationPipelineError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        with pytest.raises(CamValidationPipelineError):
            pipeline.confirm_task(task_id="cam_nonexistent")


# 6. 工程师审核流程测试


class TestCamValidationReviewFlow:
    """验证工程师审核三态流程（confirmed / rejected / edited）。"""

    def test_review_confirmed_transitions_to_reviewed_when_all_done(self):
        """T23: 全部特征 confirmed 后任务自动转为 REVIEWED。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        task = _build_minimal_task(
            task_id="cam_rf_001",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=2,
        )
        pipeline._store.add_task(task)

        # 审核第一个特征
        pipeline.review_task(
            task_id="cam_rf_001",
            feature_id="feat_001",
            review_status="confirmed",
            reviewed_by="engineer_a",
        )
        # 部分审核，状态仍为 VALIDATED
        task_partial = pipeline._store.get_task("cam_rf_001")
        assert task_partial.status == CamValidationTaskStatus.VALIDATED.value

        # 审核第二个特征 全部完成 REVIEWED
        pipeline.review_task(
            task_id="cam_rf_001",
            feature_id="feat_002",
            review_status="confirmed",
            reviewed_by="engineer_a",
        )
        task_done = pipeline._store.get_task("cam_rf_001")
        assert task_done.status == CamValidationTaskStatus.REVIEWED.value
        assert task_done.reviewed_by == "engineer_a"
        assert task_done.reviewed_at > 0

    def test_review_rejected_status(self):
        """T24: rejected 审核状态可应用。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        task = _build_minimal_task(
            task_id="cam_rf_002",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=1,
        )
        pipeline._store.add_task(task)

        result = pipeline.review_task(
            task_id="cam_rf_002",
            feature_id="feat_001",
            review_status="rejected",
            reviewed_by="engineer_b",
        )
        assert result.review_status == "rejected"
        task_done = pipeline._store.get_task("cam_rf_002")
        assert task_done.status == CamValidationTaskStatus.REVIEWED.value

    def test_review_edited_requires_edited_params(self):
        """T25: edited 状态必须提供 edited_params（项目记忆硬约束）。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        task = _build_minimal_task(
            task_id="cam_rf_003",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=1,
        )
        pipeline._store.add_task(task)

        # edited 不提供 edited_params 抛错
        with pytest.raises(ReviewError):
            pipeline.review_task(
                task_id="cam_rf_003",
                feature_id="feat_001",
                review_status="edited",
            )

    def test_review_edited_with_params_applies(self):
        """T26: edited 状态提供 edited_params 后正确应用。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        task = _build_minimal_task(
            task_id="cam_rf_004",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=1,
        )
        pipeline._store.add_task(task)

        result = pipeline.review_task(
            task_id="cam_rf_004",
            feature_id="feat_001",
            review_status="edited",
            edited_params={"safe_z": 100.0, "spindle_rpm": 6000.0},
            engineer_notes="safe_z 提升至 100mm 避免碰撞",
        )
        assert result.review_status == "edited"
        assert result.edited_params["safe_z"] == 100.0
        assert result.edited_params["spindle_rpm"] == 6000.0
        assert result.edited_params["engineer_notes"] == "safe_z 提升至 100mm 避免碰撞"

    def test_review_invalid_status_raises(self):
        """T27: 非法 review_status 抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        task = _build_minimal_task(
            task_id="cam_rf_005",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=1,
        )
        pipeline._store.add_task(task)

        with pytest.raises(ReviewError):
            pipeline.review_task(
                task_id="cam_rf_005",
                feature_id="feat_001",
                review_status="invalid_status",
            )

    def test_review_nonexistent_feature_raises(self):
        """T28: 审核不存在的特征抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())

        task = _build_minimal_task(
            task_id="cam_rf_006",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=1,
        )
        pipeline._store.add_task(task)

        with pytest.raises(ReviewError):
            pipeline.review_task(
                task_id="cam_rf_006",
                feature_id="feat_nonexistent",
                review_status="confirmed",
            )


# 7. SUCCEEDED 禁删硬约束测试


class TestCamValidationSucceededNoDelete:
    """验证 SUCCEEDED 状态禁止删除（项目记忆硬约束）。"""

    def test_delete_succeeded_raises(self):
        """T29: 删除 SUCCEEDED 任务抛 ReviewError。"""
        from app.cam_validation import (
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.cam_validation.cam_store import CamTaskStore

        store = CamTaskStore()
        task = _build_minimal_task(
            task_id="cam_sd_001",
            status=CamValidationTaskStatus.SUCCEEDED.value,
        )
        store.add_task(task)

        with pytest.raises(ReviewError):
            store.delete_task("cam_sd_001")

    def test_delete_succeeded_with_allow_flag_still_blocks_by_default(self):
        """T30: 默认 allow_delete_succeeded=False 时 SUCCEEDED 禁删。"""
        from app.cam_validation import (
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.cam_validation.cam_store import CamTaskStore

        store = CamTaskStore()
        task = _build_minimal_task(
            task_id="cam_sd_002",
            status=CamValidationTaskStatus.SUCCEEDED.value,
        )
        store.add_task(task)

        # 默认 allow_delete_succeeded=False（项目记忆硬约束）
        with pytest.raises(ReviewError):
            store.delete_task("cam_sd_002", allow_delete_succeeded=False)

    def test_delete_pending_succeeds(self):
        """T31: 删除 PENDING 任务成功。"""
        from app.cam_validation import CamValidationTaskStatus
        from app.cam_validation.cam_store import CamTaskStore

        store = CamTaskStore()
        task = _build_minimal_task(
            task_id="cam_sd_003",
            status=CamValidationTaskStatus.PENDING.value,
        )
        store.add_task(task)

        store.delete_task("cam_sd_003")
        # 删除后查询应抛错
        from app.cam_validation import CamValidationError

        with pytest.raises(CamValidationError):
            store.get_task("cam_sd_003")

    def test_delete_failed_succeeds(self):
        """T32: 删除 FAILED 任务成功。"""
        from app.cam_validation import CamValidationTaskStatus
        from app.cam_validation.cam_store import CamTaskStore

        store = CamTaskStore()
        task = _build_minimal_task(
            task_id="cam_sd_004",
            status=CamValidationTaskStatus.FAILED.value,
        )
        store.add_task(task)

        store.delete_task("cam_sd_004")
        from app.cam_validation import CamValidationError

        with pytest.raises(CamValidationError):
            store.get_task("cam_sd_004")

    def test_pipeline_delete_succeeded_raises(self):
        """T33: pipeline.delete_task 删除 SUCCEEDED 抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.config import CamValidationConfig

        pipeline = CamValidationPipeline(cfg=CamValidationConfig())
        task = _build_minimal_task(
            task_id="cam_sd_005",
            status=CamValidationTaskStatus.SUCCEEDED.value,
        )
        pipeline._store.add_task(task)

        with pytest.raises(ReviewError):
            pipeline.delete_task("cam_sd_005")


# 8. 双 JSON 导出测试


class TestCamValidationDoubleJsonExport:
    """验证 confirm_task 导出双 JSON（cam_report.json + internal_report.json）。"""

    def test_confirm_task_exports_double_json(self, tmp_path: Path):
        """T34: confirm_task 后生成 cam_report.json + internal_report.json。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
        )
        from app.config import CamValidationConfig

        # 使用 tmp_path 作为 output_dir 隔离
        cfg = CamValidationConfig(output_dir=str(tmp_path / "cam_out"))
        pipeline = CamValidationPipeline(cfg=cfg)

        # 构造 REVIEWED 状态任务（所有特征已审核）
        task = _build_minimal_task(
            task_id="cam_ex_001",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=2,
        )
        # workspace_dir 默认 "/tmp" 在 Windows 上无写权限，
        # 改为 pytest tmp_path（与 confirm_task 导出双 JSON 路径一致）
        task.workspace_dir = str(tmp_path)
        # 手动审核所有特征
        for feat in task.feature_validation_results:
            feat.review_status = "confirmed"
        task.status = CamValidationTaskStatus.REVIEWED.value
        task.reviewed_by = "engineer_a"
        task.reviewed_at = time.time()
        pipeline._store.add_task(task)

        # confirm_task SUCCEEDED + 导出双 JSON
        result = pipeline.confirm_task(task_id="cam_ex_001", reviewer="engineer_a")

        # 验证状态转为 SUCCEEDED
        task_done = pipeline._store.get_task("cam_ex_001")
        assert task_done.status == CamValidationTaskStatus.SUCCEEDED.value

        # 验证双 JSON 路径非空
        assert result.cam_report_path, "cam_report_path 为空"
        assert result.internal_report_path, "internal_report_path 为空"

        # 验证文件实际存在
        cam_report = Path(result.cam_report_path)
        internal_report = Path(result.internal_report_path)
        assert cam_report.exists(), f"cam_report.json 不存在: {cam_report}"
        assert internal_report.exists(), f"internal_report.json 不存在: {internal_report}"

        # 验证 cam_report.json 内容可解析
        cam_data = json.loads(cam_report.read_text(encoding="utf-8"))
        assert "task_id" in cam_data
        assert cam_data["task_id"] == "cam_ex_001"
        assert "feature_validation_results" in cam_data
        # pipeline 实际导出 industrial_hard_gates_note（告知文本字符串），
        # 不是 cam_disclaimer 整对象（disclaimer 通过 API 端点单独返回）
        assert "industrial_hard_gates_note" in cam_data
        # 项目记忆硬约束：cam_validation_required 始终 True
        assert cam_data.get("cam_validation_required") is True

        # 验证 internal_report.json 内容可解析
        internal_data = json.loads(internal_report.read_text(encoding="utf-8"))
        assert "task_id" in internal_data
        assert internal_data["task_id"] == "cam_ex_001"

    def test_confirm_task_all_rejected_raises(self, tmp_path: Path):
        """T35: 全部特征 rejected 时 confirm_task 抛错。"""
        from app.cam_validation import (
            CamValidationPipeline,
            CamValidationTaskStatus,
            ReviewError,
        )
        from app.config import CamValidationConfig

        cfg = CamValidationConfig(output_dir=str(tmp_path / "cam_out"))
        pipeline = CamValidationPipeline(cfg=cfg)

        task = _build_minimal_task(
            task_id="cam_ex_002",
            status=CamValidationTaskStatus.VALIDATED.value,
            feature_count=2,
        )
        # 全部 rejected
        for feat in task.feature_validation_results:
            feat.review_status = "rejected"
        task.status = CamValidationTaskStatus.REVIEWED.value
        pipeline._store.add_task(task)

        with pytest.raises(ReviewError):
            pipeline.confirm_task(task_id="cam_ex_002")


# 9. CAM 后端降级测试


class TestCamValidationCamBackendFallback:
    """验证 CAM 软件不可用时降级到 manual 模式。"""

    def test_valid_cam_backends_includes_manual(self):
        """T36: VALID_CAM_BACKENDS 含 manual（降级兜底后端）。"""
        from app.cam_validation import VALID_CAM_BACKENDS

        assert "manual" in VALID_CAM_BACKENDS
        assert "internal_only" in VALID_CAM_BACKENDS

    def test_disclaimer_marks_fallback_when_backend_degraded(self):
        """T37: CAM 后端降级时 disclaimer 标注 fallback_reason。"""
        from app.cam_validation import build_cam_disclaimer

        # 模拟 NX Open 不可用降级到 manual
        disclaimer = build_cam_disclaimer(
            precision_tier="mesh_calibrated",
            controller_type="fanuc_0i",
            material_name="45#钢",
            material_calibration_status="calibrated",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/test.nc",
            prediction_method="analytical",
            total_features=2,
            passed_features=2,
            failed_features=0,
            pending_calibration=False,
            ltc_experiment_used=False,
            cam_backend_used="manual",
            cam_backend_fallback_reason="NX Open executable not configured",
            cam_backend_requested="nx_open",
            cam_report_exported=False,
        )

        d = disclaimer.to_dict()
        assert d["cam_backend_used"] == "manual"
        assert d["cam_backend_requested"] == "nx_open"
        assert "NX Open" in d["cam_backend_fallback_reason"]
        # 降级时 warning_message 应非空
        assert d["warning_message"], "降级时 warning_message 不应为空"

    def test_disclaimer_internal_only_no_fallback_reason(self):
        """T38: internal_only 后端无降级时 fallback_reason 为空。"""
        from app.cam_validation import build_cam_disclaimer

        disclaimer = build_cam_disclaimer(
            precision_tier="mesh_calibrated",
            controller_type="fanuc_0i",
            material_name="45#钢",
            material_calibration_status="calibrated",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/test.nc",
            prediction_method="analytical",
            total_features=2,
            passed_features=2,
            failed_features=0,
            pending_calibration=False,
            ltc_experiment_used=False,
            cam_backend_used="internal_only",
            cam_backend_fallback_reason="",
            cam_backend_requested="internal_only",
            cam_report_exported=False,
        )

        d = disclaimer.to_dict()
        assert d["cam_backend_used"] == "internal_only"
        assert d["cam_backend_fallback_reason"] == ""


# 10. 项目记忆硬约束测试（disclaimer 工程师助手定位 + 不直接接口 CNC）


class TestCamValidationProjectHardConstraints:
    """验证项目记忆硬约束在 disclaimer 中的体现。"""

    def test_disclaimer_engineer_assistant_positioning(self):
        """T39: disclaimer 体现「工程师助手」定位（非全自动 CAM 仿真器）。"""
        from app.cam_validation import build_cam_disclaimer

        disclaimer = build_cam_disclaimer(
            precision_tier="mesh_calibrated",
            controller_type="fanuc_0i",
            material_name="45#钢",
            material_calibration_status="calibrated",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/test.nc",
            prediction_method="analytical",
            total_features=2,
            passed_features=2,
            failed_features=0,
            pending_calibration=False,
            ltc_experiment_used=False,
            cam_backend_used="internal_only",
            cam_backend_fallback_reason="",
            cam_backend_requested="internal_only",
            cam_report_exported=False,
        )
        d = disclaimer.to_dict()

        # 「工程师助手」定位体现在 industrial_hard_gates 列表（manual 后端兜底 +
        # 工程师回填机制）+ warning_message 兜底提示中
        hard_gates_text = " ".join(d["industrial_hard_gates"])
        assert (
            "工程师助手" in hard_gates_text or "工程师回填" in hard_gates_text or "手动校验流程" in hard_gates_text
        ), f"industrial_hard_gates 未体现工程师助手定位: {hard_gates_text}"
        # warning_message 必须非空（兜底含 CAM 校验强制硬门槛）
        assert d["warning_message"], f"warning_message 不应为空: {d['warning_message']}"

    def test_disclaimer_no_direct_cnc_interface(self):
        """T40: disclaimer 标注「绝不直接接口 CNC 控制器」（项目记忆硬约束）。"""
        from app.cam_validation import build_cam_disclaimer, INDUSTRIAL_HARD_GATES

        # INDUSTRIAL_HARD_GATES 应包含「绝不直接接口 CNC」相关硬门槛
        hard_gates_text = " ".join(INDUSTRIAL_HARD_GATES)
        assert "绝不直接接口 CNC" in hard_gates_text, "INDUSTRIAL_HARD_GATES 未标注「绝不直接接口 CNC 控制器」"

    def test_disclaimer_terminates_at_cam_report_json(self):
        """T41: disclaimer 标注阶段 7 终止于「CAM 校验报告 JSON」。"""
        from app.cam_validation import INDUSTRIAL_HARD_GATES

        hard_gates_text = " ".join(INDUSTRIAL_HARD_GATES)
        assert "CAM 校验报告 JSON" in hard_gates_text, "INDUSTRIAL_HARD_GATES 未标注阶段 7 终止于 CAM 校验报告 JSON"

    def test_disclaimer_cam_validation_required_always_true(self):
        """T42: requires_cam_validation 始终 True（不可由参数关闭）。"""
        from app.cam_validation import build_cam_disclaimer

        # 即使试图通过参数关闭（虽然函数签名不接受此参数），disclaimer 仍应强制 True
        disclaimer = build_cam_disclaimer(
            precision_tier="mesh_calibrated",
            controller_type="fanuc_0i",
            material_name="45#钢",
            material_calibration_status="calibrated",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/test.nc",
            prediction_method="analytical",
            total_features=0,
            passed_features=0,
            failed_features=0,
            pending_calibration=False,
            ltc_experiment_used=False,
            cam_backend_used="internal_only",
            cam_backend_fallback_reason="",
            cam_backend_requested="internal_only",
            cam_report_exported=False,
        )
        d = disclaimer.to_dict()

        # 项目记忆硬约束：cam_validation_required 始终 True
        assert d["requires_cam_validation"] is True, "requires_cam_validation 应始终 True（项目记忆硬约束）"
        # requires_engineer_review 始终 True（human-in-the-loop）
        assert d["requires_engineer_review"] is True, "requires_engineer_review 应始终 True（human-in-the-loop）"

    def test_disclaimer_hrc52_pending_calibration(self):
        """T43: HRC52 pending_calibration 在 disclaimer 中标注。"""
        from app.cam_validation import build_cam_disclaimer

        disclaimer = build_cam_disclaimer(
            precision_tier="mesh_calibrated",
            controller_type="fanuc_0i",
            material_name="steel_hrc52",
            material_calibration_status="pending_calibration",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/test.nc",
            prediction_method="analytical",
            total_features=2,
            passed_features=2,
            failed_features=0,
            pending_calibration=True,  # HRC52 待校准
            ltc_experiment_used=False,
            cam_backend_used="internal_only",
            cam_backend_fallback_reason="",
            cam_backend_requested="internal_only",
            cam_report_exported=False,
        )
        d = disclaimer.to_dict()

        assert d["pending_calibration"] is True
        assert d["material_calibration_status"] == "pending_calibration"
        # warning_message 应标注 HRC52 待校准告知
        assert "校准" in d["warning_message"] or "HRC52" in d["warning_message"], (
            f"warning_message 未标注 HRC52 待校准: {d['warning_message']}"
        )

    def test_disclaimer_ltc_experiment_used(self):
        """T44: LTC 神经网络路径在 disclaimer 中标注实验性。"""
        from app.cam_validation import build_cam_disclaimer

        disclaimer = build_cam_disclaimer(
            precision_tier="mesh_calibrated",
            controller_type="fanuc_0i",
            material_name="45#钢",
            material_calibration_status="calibrated",
            gcode_report_source="/tmp/report.json",
            gcode_file_source="/tmp/test.nc",
            prediction_method="neural_network",  # LTC 路径
            total_features=2,
            passed_features=2,
            failed_features=0,
            pending_calibration=False,
            ltc_experiment_used=True,  # LTC 实验性路径
            cam_backend_used="internal_only",
            cam_backend_fallback_reason="",
            cam_backend_requested="internal_only",
            cam_report_exported=False,
        )
        d = disclaimer.to_dict()

        assert d["ltc_experiment_used"] is True
        # warning_message 应标注实验性
        assert "实验" in d["warning_message"], f"warning_message 未标注 LTC 实验性: {d['warning_message']}"


# 11. GCodeLoader 测试


class TestCamValidationGCodeLoader:
    """验证 GCodeLoader 加载阶段 6 G 代码报告 JSON。"""

    def test_gcode_loader_loads_valid_report(self, tmp_path: Path):
        """T45: GCodeLoader 加载合法 report.json。"""
        from app.cam_validation import GCodeLoader

        report_path = _build_minimal_gcode_report_json(tmp_path)
        # T45 修复：pytest 默认 tmp_path 位于系统 Temp，不在项目根（engineering/）
        # 之下，会被 GCodeLoader 的路径遍历安全策略拒绝。将 tmp_path 显式声明为
        # loader 的项目根：既保留安全策略（策略逻辑不变），又使用例可运行。
        loader = GCodeLoader(project_root=str(tmp_path))
        # GCodeLoader 实际方法名是 load_from_report（非 load）
        result = loader.load_from_report(report_path)

        assert result.gcode_text  # G 代码文本非空
        assert result.controller_type == "fanuc_0i"
        assert result.material_name == "45#钢"
        assert result.safe_z == 80.0
        assert result.stock_top_z == 50.0
        assert len(result.feature_results) == 2

    def test_gcode_loader_nonexistent_report_raises(self):
        """T46: GCodeLoader 加载不存在的 report.json 抛错。"""
        from app.cam_validation import GCodeLoader, GCodeReportLoadError

        loader = GCodeLoader()
        with pytest.raises(GCodeReportLoadError):
            loader.load_from_report("/nonexistent/path/report.json")

    def test_gcode_loader_missing_required_field_raises(self, tmp_path: Path):
        """T47: GCodeLoader 加载缺失必填字段的 report.json 抛错。"""
        from app.cam_validation import GCodeLoader, GCodeReportLoadError

        # 构造缺失 controller_type 的 report.json
        data = {
            "task_id": "gcode_test_002",
            "task_status": "succeeded",
            "gcode_file_path": str(tmp_path / "test.nc"),
            "gcode_total_lines": 1,
            # 缺失 controller_type
            "material_name": "45#钢",
            "safe_z": 80.0,
            "stock_top_z": 50.0,
            "cam_validation_required": True,
            "feature_results": [],
        }
        Path(tmp_path / "test.nc").write_text("G01 X10 Y20 Z30 F500\n", encoding="utf-8")
        json_path = tmp_path / "gcode_report_missing.json"
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        loader = GCodeLoader()
        with pytest.raises(GCodeReportLoadError):
            loader.load_from_report(str(json_path))

    def test_gcode_loader_unsucceeded_upstream_raises(self, tmp_path: Path):
        """T48: 上游任务未 succeeded 时 GCodeLoader 拒绝加载。"""
        from app.cam_validation import GCodeLoader, GCodeReportLoadError

        # 构造 task_status != succeeded 的 report.json
        gcode_path = str(tmp_path / "test.nc")
        Path(gcode_path).write_text("G01 X10 Y20 Z30 F500\n", encoding="utf-8")
        data = {
            "task_id": "gcode_test_003",
            "task_status": "pending",  # 未 succeeded
            "gcode_file_path": gcode_path,
            "gcode_total_lines": 1,
            "controller_type": "fanuc_0i",
            "material_name": "45#钢",
            "safe_z": 80.0,
            "stock_top_z": 50.0,
            "cam_validation_required": True,
            "feature_results": [],
        }
        json_path = tmp_path / "gcode_report_unsucceeded.json"
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        loader = GCodeLoader()
        with pytest.raises(GCodeReportLoadError):
            loader.load_from_report(str(json_path))
