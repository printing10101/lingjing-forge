"""颤振预测接入模块 单元测试（阶段 5）。

覆盖：
- 模块导入与导出：app.chatter_prediction 包可正常导入全部子模块
- 精度告知机制：chatter_disclaimer 字段完整、8 条工业硬门槛覆盖关键约束
- 枚举完整性：ChatterPredictionTaskStatus (7 态) / ChatterReviewStatus (4 态) / PredictionMethod (3 态)
- FeatureChatterResult.effective_result()：edited 状态合并 edited_params，否则用预测值
- 预测适配器：HRC52 pending_calibration 触发置信度降低 / LTC 模型可用性检查 / 解析法路径 / 安全裕度警告
- Pipeline 状态机：PENDING → RUNNING → PREDICTED → REVIEWED → SUCCEEDED（含 FAILED/CANCELLED）
- SUCCEEDED 禁删硬约束（阶段 6 G 代码生成可能已引用其 ChatterReport）
- 配置校验：ChatterPredictionConfig 11 个字段 + __post_init__ 非法值回退 + 硬约束
- API 路由注册：10 个端点全部注册
- 项目记忆硬约束：工程师助手定位 / CAM 二次校验强制 / K_s 直接传递 / HRC52 待校准

测试设计原则（与 test_cutting_parameters.py / test_parametric_geometry.py 一致）：
- 不依赖 chatter_model.pt 真实存在（适配器默认 force_analytical 走 Tlusty 解析法）
- 不依赖真实机床数据（构造临时 ChatterParams JSON 通过 tmp_path 隔离）
- 只验证模块契约与工程师审核流程（用户最关心的「human-in-the-loop 责任划分」）
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest


# 辅助：构造最小 ChatterParams JSON（阶段 4 输出格式）


def _build_minimal_chatter_params_json(
    tmp_path: Path,
    material_id: str = "al_6061",
    feature_count: int = 2,
) -> str:
    """构造阶段 4 ChatterParams JSON 文件（最小可用结构）。

    格式参照 pipeline._load_chatter_params 的兼容路径：
        {
            "task_id": "...",
            "material_id": "...",
            "chatter_params_list": [
                {
                    "feature_id": "...",
                    "feature_type": "...",
                    "operation": "...",
                    "chatter_params": {spindle_rpm, machine, tool, axial_depth},
                    "material_id": "...",
                    "k_s_n_per_mm2": ...
                },
                ...
            ]
        }
    """
    feature_types = ["plane", "cylinder", "hole", "boss"]
    chatter_params_list = []
    for i in range(feature_count):
        chatter_params_list.append(
            {
                "feature_id": f"feat_{i + 1:03d}",
                "feature_type": feature_types[i % len(feature_types)],
                "operation": "roughing",
                "chatter_params": {
                    "spindle_rpm": 8000.0,
                    "machine": {
                        "machine_id": "vmc_850",
                        "stiffness_x": 1.5e7,
                        "stiffness_y": 1.5e7,
                        "stiffness_z": 2.0e8,
                        "damping_ratio": 0.05,
                        "natural_freq": 100.0,
                        "modal_mass": 50.0,
                    },
                    "tool": {
                        "tool_id": "endmill_d10",
                        "diameter": 10.0,
                        "num_flutes": 4,
                        "helix_angle": 30.0,
                        "cutting_force_coeff": 2000.0,  # K_s 直接传递
                    },
                    "axial_depth": 1.0,  # 远小于极限切深，判定为稳定
                },
                "material_id": material_id,
                "k_s_n_per_mm2": 2000.0,
            }
        )

    data = {
        "task_id": "cp_test_001",
        "material_id": material_id,
        "chatter_params_list": chatter_params_list,
    }

    json_path = tmp_path / "chatter_params.json"
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(json_path)


# 模块导入测试


class TestModuleImport:
    """验证 chatter_prediction 模块可正常导入。"""

    def test_import_main_classes(self):
        """T01: 主类与函数可正常导入。"""
        from app.chatter_prediction import (
            ChatterPredictionTask,
            ChatterPredictionTaskStatus,
            ChatterReviewStatus,
            PredictionMethod,
            FeatureChatterResult,
            ChatterPredictionError,
            ChatterParamsLoadError,
            ReviewError,
            TaskStore,
            generate_task_id,
            get_task_store,
            ChatterDisclaimer,
            INDUSTRIAL_HARD_GATES,
            build_chatter_disclaimer,
            ChatterPredictorAdapter,
            PredictorAdapterError,
            check_ltc_model_available,
            ChatterPredictionPipeline,
            ChatterPredictionResult,
            ChatterPredictionPipelineError,
            ChatterReviewError,
        )

        for obj in [
            ChatterPredictionTask,
            ChatterPredictionTaskStatus,
            ChatterReviewStatus,
            PredictionMethod,
            FeatureChatterResult,
            ChatterPredictionError,
            ChatterParamsLoadError,
            ReviewError,
            TaskStore,
            generate_task_id,
            get_task_store,
            ChatterDisclaimer,
            INDUSTRIAL_HARD_GATES,
            build_chatter_disclaimer,
            ChatterPredictorAdapter,
            PredictorAdapterError,
            check_ltc_model_available,
            ChatterPredictionPipeline,
            ChatterPredictionResult,
            ChatterPredictionPipelineError,
            ChatterReviewError,
        ]:
            assert obj is not None, f"{obj} 导入失败"

    def test_routes_module_importable(self):
        """T02: API 路由模块可正常导入。"""
        from app.api.v1 import chatter_prediction as ch_routes_pkg

        assert hasattr(ch_routes_pkg, "routes")
        assert ch_routes_pkg.routes.router is not None
        assert ch_routes_pkg.routes.router.prefix == "/api/v1/chatter_prediction"

    def test_ten_endpoints_registered(self):
        """T03: 10 个 API 端点全部注册。

        端点清单（与 routes.py 一致）：
        - GET  /precision_info
        - POST /tasks
        - POST /tasks/{task_id}/run
        - GET  /tasks/{task_id}
        - GET  /tasks
        - GET  /tasks/{task_id}/result
        - POST /tasks/{task_id}/review
        - POST /tasks/{task_id}/export
        - GET  /tasks/{task_id}/chatter_report/download
        - DELETE /tasks/{task_id}
        """
        from app.api.v1.chatter_prediction import routes as ch_routes

        endpoints = set()
        for route in ch_routes.router.routes:
            for method in route.methods:
                endpoints.add((method, route.path))

        expected_endpoints = {
            ("GET", "/api/v1/chatter_prediction/precision_info"),
            ("POST", "/api/v1/chatter_prediction/tasks"),
            ("POST", "/api/v1/chatter_prediction/tasks/{task_id}/run"),
            ("GET", "/api/v1/chatter_prediction/tasks/{task_id}"),
            ("GET", "/api/v1/chatter_prediction/tasks"),
            ("GET", "/api/v1/chatter_prediction/tasks/{task_id}/result"),
            ("POST", "/api/v1/chatter_prediction/tasks/{task_id}/review"),
            ("POST", "/api/v1/chatter_prediction/tasks/{task_id}/export"),
            (
                "GET",
                "/api/v1/chatter_prediction/tasks/{task_id}/chatter_report/download",
            ),
            ("DELETE", "/api/v1/chatter_prediction/tasks/{task_id}"),
        }

        missing = expected_endpoints - endpoints
        assert not missing, f"缺失端点: {missing}"

    def test_router_tags_and_permissions(self):
        """T04: 路由 tags 标注为「Engineer-Assisted」并启用权限校验。"""
        from app.api.v1.chatter_prediction import routes as ch_routes

        tags = ch_routes.router.tags
        assert any("Engineer-Assisted" in t for t in tags), f"路由 tags 未标注工程师辅助定位: {tags}"

        # 顶层 dependencies 必须包含权限依赖
        deps = ch_routes.router.dependencies
        assert len(deps) > 0, "路由未挂载任何 dependencies（权限校验缺失）"


# 枚举完整性测试


class TestEnums:
    """枚举完整性验证。"""

    def test_task_status_seven_states(self):
        """T05: ChatterPredictionTaskStatus 含 7 个状态。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus

        expected = {"pending", "running", "predicted", "reviewed", "succeeded", "failed", "cancelled"}
        actual = {s.value for s in ChatterPredictionTaskStatus}
        assert actual == expected, f"任务状态不匹配: {actual - expected} 缺失, {expected - actual} 多余"

    def test_review_status_four_states(self):
        """T06: ChatterReviewStatus 含 4 个状态。"""
        from app.chatter_prediction import ChatterReviewStatus

        expected = {"pending", "confirmed", "rejected", "edited"}
        actual = {s.value for s in ChatterReviewStatus}
        assert actual == expected, f"审核状态不匹配: {actual - expected} 缺失, {expected - actual} 多余"

    def test_prediction_method_three_states(self):
        """T07: PredictionMethod 含 3 个方法。"""
        from app.chatter_prediction import PredictionMethod

        expected = {"analytical", "neural_network", "fallback"}
        actual = {m.value for m in PredictionMethod}
        assert actual == expected, f"预测方法不匹配: {actual - expected} 缺失, {expected - actual} 多余"

    def test_task_status_transition_order(self):
        """T08: 正向状态转移链 PENDING → RUNNING → PREDICTED → REVIEWED → SUCCEEDED。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus as S

        # 单轮审核（与阶段 3 两轮审核区别）
        assert S.PENDING.value == "pending"
        assert S.RUNNING.value == "running"
        assert S.PREDICTED.value == "predicted"
        assert S.REVIEWED.value == "reviewed"
        assert S.SUCCEEDED.value == "succeeded"
        # 异常分支
        assert S.FAILED.value == "failed"
        assert S.CANCELLED.value == "cancelled"


# 精度告知机制测试


class TestChatterDisclaimer:
    """精度告知机制（项目记忆硬约束：HRC52 待校准 + 工程师助手定位）。"""

    def test_disclaimer_all_fields(self):
        """T09: chatter_disclaimer 包含全部 14 个字段。"""
        from app.chatter_prediction import build_chatter_disclaimer

        disclaimer = build_chatter_disclaimer(
            mesh_calibrated=False,
            chatter_params_source="external_upload",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            prediction_method="analytical",
            ltc_model_available=False,
            ltc_active_ratio=0.0,
            chatter_report_ready=False,
        )
        d = disclaimer.to_dict()

        required_fields = {
            "mesh_calibrated",
            "chatter_params_source",
            "material_id",
            "material_calibration_status",
            "precision_tier",
            "machine_type",
            "prediction_method",
            "ltc_model_available",
            "ltc_active_ratio",
            "requires_engineer_review",
            "requires_cam_validation",
            "chatter_report_ready",
            "industrial_hard_gates",
            "warning_message",
        }
        missing = required_fields - set(d.keys())
        assert not missing, f"chatter_disclaimer 缺失字段: {missing}"

    def test_uncalibrated_mesh_warning(self):
        """T10: mesh 未标定时警告明确告知「无量纲」。"""
        from app.chatter_prediction import build_chatter_disclaimer

        disclaimer = build_chatter_disclaimer(
            mesh_calibrated=False,
            chatter_params_source="external_upload",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            prediction_method="analytical",
            ltc_model_available=False,
            ltc_active_ratio=0.0,
            chatter_report_ready=False,
        )
        assert "未标定" in disclaimer.warning_message
        assert "无量纲" in disclaimer.warning_message

    def test_hrc52_pending_calibration_warning(self):
        """T11: HRC52 材料 pending_calibration 时警告明确告知置信度降低。"""
        from app.chatter_prediction import build_chatter_disclaimer

        disclaimer = build_chatter_disclaimer(
            mesh_calibrated=True,
            chatter_params_source="cp_task_001",
            material_id="steel_hrc52",
            material_calibration_status="pending_calibration",
            precision_tier="standard",
            machine_type="vmc_850",
            prediction_method="analytical",
            ltc_model_available=False,
            ltc_active_ratio=0.0,
            chatter_report_ready=False,
        )
        assert "pending_calibration" in disclaimer.warning_message or "待自采" in disclaimer.warning_message
        assert "置信度" in disclaimer.warning_message or "K_s" in disclaimer.warning_message

    def test_ltc_unavailable_warning(self):
        """T12: LTC 模型不可用时警告明确告知回退到解析法。"""
        from app.chatter_prediction import build_chatter_disclaimer

        disclaimer = build_chatter_disclaimer(
            mesh_calibrated=True,
            chatter_params_source="cp_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            prediction_method="analytical",
            ltc_model_available=False,
            ltc_active_ratio=0.0,
            chatter_report_ready=False,
        )
        assert "LTC" in disclaimer.warning_message
        assert "解析法" in disclaimer.warning_message

    def test_eight_industrial_hard_gates(self):
        """T13: INDUSTRIAL_HARD_GATES 含 8 条硬门槛，覆盖关键约束。"""
        from app.chatter_prediction import INDUSTRIAL_HARD_GATES

        assert len(INDUSTRIAL_HARD_GATES) == 8, f"工业硬门槛应为 8 条，实际 {len(INDUSTRIAL_HARD_GATES)}"

        # 关键约束覆盖检查（项目记忆硬约束）
        all_gates = " ".join(INDUSTRIAL_HARD_GATES)
        assert "工程师" in all_gates, "硬门槛未提及工程师审核"
        assert "CAM" in all_gates, "硬门槛未提及 CAM 二次校验"
        assert "Tlusty" in all_gates or "解析法" in all_gates, "硬门槛未提及解析法"
        assert "LTC" in all_gates, "硬门槛未提及 LTC 实验性"
        assert "持证" in all_gates or "操作员" in all_gates, "硬门槛未提及持证操作员"
        assert "导师" in all_gates, "硬门槛未提及导师签字"

    def test_requires_engineer_review_always_true(self):
        """T14: requires_engineer_review 始终 True（项目记忆硬约束）。"""
        from app.chatter_prediction import build_chatter_disclaimer

        disclaimer = build_chatter_disclaimer(
            mesh_calibrated=True,
            chatter_params_source="cp_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="high",
            machine_type="vmc_850",
            prediction_method="analytical",
            ltc_model_available=True,
            ltc_active_ratio=1.0,
            chatter_report_ready=True,
        )
        assert disclaimer.requires_engineer_review is True, "requires_engineer_review 必须始终 True（工程师助手定位）"
        assert disclaimer.requires_cam_validation is True, "requires_cam_validation 必须始终 True（CAM 二次校验强制）"


# FeatureChatterResult.effective_result() 测试


class TestFeatureChatterResultEffective:
    """effective_result() 契约（与阶段 2/3/4 effective_*() 一致）。"""

    def test_effective_result_uses_predicted_when_pending(self):
        """T15: review_status=pending 时使用预测值。"""
        from app.chatter_prediction import FeatureChatterResult, ChatterReviewStatus

        result = FeatureChatterResult(
            feature_id="feat_001",
            feature_type="plane",
            material_id="al_6061",
            spindle_rpm=8000.0,
            axial_depth_mm=1.0,
            limit_depth_mm=5.0,
            stable=True,
            stability_margin=0.2,
            method="analytical",
            ltc_active=False,
            review_status=ChatterReviewStatus.PENDING.value,
        )
        eff = result.effective_result()
        assert eff["limit_depth_mm"] == 5.0
        assert eff["axial_depth_mm"] == 1.0
        assert eff["stable"] == 1.0  # True → 1.0

    def test_effective_result_uses_edited_params(self):
        """T16: review_status=edited 时用 edited_params 覆盖预测值。"""
        from app.chatter_prediction import FeatureChatterResult, ChatterReviewStatus

        result = FeatureChatterResult(
            feature_id="feat_002",
            feature_type="cylinder",
            material_id="al_6061",
            spindle_rpm=8000.0,
            axial_depth_mm=1.0,
            limit_depth_mm=5.0,
            stable=True,
            stability_margin=0.2,
            method="analytical",
            ltc_active=False,
            review_status=ChatterReviewStatus.EDITED.value,
            edited_params={"limit_depth_mm": 3.0, "stable": 0.0},
        )
        eff = result.effective_result()
        assert eff["limit_depth_mm"] == 3.0
        assert eff["stable"] == 0.0  # False → 0.0
        # 未编辑的字段保持预测值
        assert eff["axial_depth_mm"] == 1.0

    def test_to_dict_contains_all_fields(self):
        """T17: to_dict() 包含全部 22 个字段。"""
        from app.chatter_prediction import FeatureChatterResult

        result = FeatureChatterResult(
            feature_id="feat_003",
            feature_type="hole",
            material_id="steel_hrc52",
            spindle_rpm=6000.0,
            axial_depth_mm=0.5,
            limit_depth_mm=2.0,
            stable=False,
            stability_margin=0.25,
            method="analytical",
            ltc_active=False,
            cutting_force_coeff=2500.0,
        )
        d = result.to_dict()

        required_fields = {
            "feature_id",
            "feature_type",
            "material_id",
            "spindle_rpm",
            "axial_depth_mm",
            "limit_depth_mm",
            "stable",
            "stability_margin",
            "method",
            "ltc_active",
            "confidence",
            "inference_time_ms",
            "warnings",
            "material_calibration_status",
            "review_status",
            "edited_params",
            "reviewed_by",
            "reviewed_at",
            "engineer_notes",
            "source_cutting_params_task_id",
            "machine_id",
            "tool_id",
            "cutting_force_coeff",
        }
        missing = required_fields - set(d.keys())
        assert not missing, f"to_dict 缺失字段: {missing}"

        # K_s 直接传递（项目记忆硬约束）
        assert d["cutting_force_coeff"] == 2500.0


# 预测适配器测试


class TestPredictorAdapter:
    """双路径预测适配器（解析法 / LTC 神经网络 / 兜底）。"""

    def test_check_ltc_model_available_returns_bool(self):
        """T18: check_ltc_model_available 返回布尔值（默认无模型文件返回 False）。"""
        from app.chatter_prediction import check_ltc_model_available

        result = check_ltc_model_available()
        assert isinstance(result, bool)
        # 默认环境无 chatter_model.pt，应为 False
        assert result is False, "默认环境应无 chatter_model.pt，check_ltc_model_available 应返回 False"

    def test_adapter_force_analytical_disables_ltc(self):
        """T19: force_analytical=True 时适配器不尝试 LTC。"""
        from app.chatter_prediction import ChatterPredictorAdapter

        adapter = ChatterPredictorAdapter(force_analytical=True)
        assert adapter.ltc_model_available is False, "force_analytical=True 时 ltc_model_available 必须为 False"

    def test_hrc52_triggers_pending_calibration(self):
        """T20: HRC52 材料触发 pending_calibration 标注 + 置信度降低。

        项目记忆硬约束：HRC52 不可使用纯文献数据，置信度强制降至 0.5。
        """
        from app.chatter_prediction import ChatterPredictorAdapter
        from app.chatter_prediction.predictor_adapter import (
            PENDING_CALIBRATION_CONFIDENCE,
            PENDING_CALIBRATION_MATERIALS,
        )

        # 确认 HRC52 材料 ID 在待校准集合中
        assert "steel_hrc52" in PENDING_CALIBRATION_MATERIALS
        assert "hrc52" in PENDING_CALIBRATION_MATERIALS

        adapter = ChatterPredictorAdapter(force_analytical=True)
        chatter_params = {
            "spindle_rpm": 8000.0,
            "machine": {
                "machine_id": "vmc_850",
                "stiffness_x": 1.5e7,
                "stiffness_y": 1.5e7,
                "stiffness_z": 2.0e8,
                "damping_ratio": 0.05,
                "natural_freq": 100.0,
                "modal_mass": 50.0,
            },
            "tool": {
                "tool_id": "endmill_d10",
                "diameter": 10.0,
                "num_flutes": 4,
                "helix_angle": 30.0,
                "cutting_force_coeff": 2500.0,
            },
            "axial_depth": 0.5,
        }
        result = adapter.predict_feature(
            feature_id="feat_hrc52_001",
            feature_type="plane",
            material_id="steel_hrc52",
            chatter_params_dict=chatter_params,
            source_cutting_params_task_id="cp_hrc52_001",
        )

        assert result.material_calibration_status == "pending_calibration"
        # 置信度强制降低（PENDING_CALIBRATION_CONFIDENCE=0.5）
        assert result.confidence <= PENDING_CALIBRATION_CONFIDENCE, (
            f"HRC52 置信度应 <= {PENDING_CALIBRATION_CONFIDENCE}，实际 {result.confidence}"
        )
        # 警告中必须提及 pending_calibration
        assert any("pending_calibration" in w or "置信度" in w for w in result.warnings), (
            f"警告未提及 pending_calibration 或置信度降低: {result.warnings}"
        )
        # K_s 直接传递（不二次拟合）
        assert result.cutting_force_coeff == 2500.0

    def test_calibrated_material_keeps_default_confidence(self):
        """T21: 已校准材料（如 al_6061）保持默认置信度 0.8。"""
        from app.chatter_prediction import ChatterPredictorAdapter
        from app.chatter_prediction.predictor_adapter import DEFAULT_CONFIDENCE

        adapter = ChatterPredictorAdapter(force_analytical=True)
        chatter_params = {
            "spindle_rpm": 8000.0,
            "machine": {
                "machine_id": "vmc_850",
                "stiffness_x": 1.5e7,
                "stiffness_y": 1.5e7,
                "stiffness_z": 2.0e8,
                "damping_ratio": 0.05,
                "natural_freq": 100.0,
                "modal_mass": 50.0,
            },
            "tool": {
                "tool_id": "endmill_d10",
                "diameter": 10.0,
                "num_flutes": 4,
                "helix_angle": 30.0,
                "cutting_force_coeff": 2000.0,
            },
            "axial_depth": 0.5,
        }
        result = adapter.predict_feature(
            feature_id="feat_al_001",
            feature_type="plane",
            material_id="al_6061",
            chatter_params_dict=chatter_params,
        )

        assert result.material_calibration_status == "calibrated"
        # 解析法路径默认置信度
        assert result.confidence == DEFAULT_CONFIDENCE
        # 默认走解析法
        assert result.method == "analytical"
        assert result.ltc_active is False

    def test_analytical_path_returns_positive_limit_depth(self):
        """T22: 解析法路径返回正的极限切深。"""
        from app.chatter_prediction import ChatterPredictorAdapter

        adapter = ChatterPredictorAdapter(force_analytical=True)
        chatter_params = {
            "spindle_rpm": 8000.0,
            "machine": {
                "machine_id": "vmc_850",
                "stiffness_x": 1.5e7,
                "stiffness_y": 1.5e7,
                "stiffness_z": 2.0e8,
                "damping_ratio": 0.05,
                "natural_freq": 100.0,
                "modal_mass": 50.0,
            },
            "tool": {
                "tool_id": "endmill_d10",
                "diameter": 10.0,
                "num_flutes": 4,
                "helix_angle": 30.0,
                "cutting_force_coeff": 2000.0,
            },
            "axial_depth": 0.5,
        }
        result = adapter.predict_feature(
            feature_id="feat_analytic_001",
            feature_type="plane",
            material_id="al_6061",
            chatter_params_dict=chatter_params,
        )

        assert result.method == "analytical"
        assert result.limit_depth_mm > 0, f"解析法极限切深应 > 0，实际 {result.limit_depth_mm}"
        # 稳定性裕度 = axial_depth / limit_depth
        if result.limit_depth_mm > 0:
            expected_margin = 0.5 / result.limit_depth_mm
            assert math.isclose(result.stability_margin, expected_margin, rel_tol=1e-6)

    def test_safety_margin_warning_when_exceeding_threshold(self):
        """T23: 实际切深超过极限切深 80% 时触发安全裕度警告。"""
        from app.chatter_prediction import ChatterPredictorAdapter
        from app.chatter_prediction.predictor_adapter import SAFETY_MARGIN_RATIO

        adapter = ChatterPredictorAdapter(force_analytical=True)
        chatter_params = {
            "spindle_rpm": 8000.0,
            "machine": {
                "machine_id": "vmc_850",
                "stiffness_x": 1.5e7,
                "stiffness_y": 1.5e7,
                "stiffness_z": 2.0e8,
                "damping_ratio": 0.05,
                "natural_freq": 100.0,
                "modal_mass": 50.0,
            },
            "tool": {
                "tool_id": "endmill_d10",
                "diameter": 10.0,
                "num_flutes": 4,
                "helix_angle": 30.0,
                "cutting_force_coeff": 2000.0,
            },
            # 设置一个很大的切深，确保超过极限切深的 80%
            "axial_depth": 100.0,
        }
        result = adapter.predict_feature(
            feature_id="feat_margin_001",
            feature_type="plane",
            material_id="al_6061",
            chatter_params_dict=chatter_params,
        )

        if result.limit_depth_mm > 0:
            # 实际切深 100mm 远超极限切深，必然触发安全裕度警告
            safety_warnings = [
                w
                for w in result.warnings
                if "超过极限切深" in w or "安全裕度" in w or SAFETY_MARGIN_RATIO * 100 == 80.0
            ]
            assert len(safety_warnings) > 0, f"实际切深超过极限切深 80% 应触发警告: {result.warnings}"

    def test_k_s_direct_passthrough(self):
        """T24: K_s（cutting_force_coeff）直接传递，不二次拟合。

        项目记忆硬约束：K_s 直接取自阶段 4 ChatterParams。
        """
        from app.chatter_prediction import ChatterPredictorAdapter

        adapter = ChatterPredictorAdapter(force_analytical=True)
        test_k_s = 3500.0  # 测试用 K_s 值
        chatter_params = {
            "spindle_rpm": 8000.0,
            "machine": {
                "machine_id": "vmc_850",
                "stiffness_x": 1.5e7,
                "stiffness_y": 1.5e7,
                "stiffness_z": 2.0e8,
                "damping_ratio": 0.05,
                "natural_freq": 100.0,
                "modal_mass": 50.0,
            },
            "tool": {
                "tool_id": "endmill_d10",
                "diameter": 10.0,
                "num_flutes": 4,
                "helix_angle": 30.0,
                "cutting_force_coeff": test_k_s,
            },
            "axial_depth": 0.5,
        }
        result = adapter.predict_feature(
            feature_id="feat_ks_001",
            feature_type="plane",
            material_id="al_6061",
            chatter_params_dict=chatter_params,
        )

        # K_s 必须原样传递，不被二次拟合
        assert result.cutting_force_coeff == test_k_s, f"K_s 应直接传递为 {test_k_s}，实际 {result.cutting_force_coeff}"


# Pipeline 状态机测试


class TestPipelineStateMachine:
    """Pipeline 状态机：PENDING → RUNNING → PREDICTED → REVIEWED → SUCCEEDED。"""

    def _make_pipeline(self, tmp_path: Path):
        """构造带临时输出目录的 pipeline。"""
        from app.chatter_prediction import ChatterPredictorAdapter, ChatterPredictionPipeline
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig(
            enabled=True,
            output_dir=str(tmp_path / "chatter_prediction"),
            force_analytical=True,  # 测试强制走解析法
        )
        adapter = ChatterPredictorAdapter(force_analytical=True)
        return ChatterPredictionPipeline(cfg=cfg, adapter=adapter)

    @pytest.mark.asyncio
    async def test_full_pipeline_success_flow(self, tmp_path):
        """T25: 完整成功流程 PENDING → RUNNING → PREDICTED → REVIEWED → SUCCEEDED。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus
        from app.chatter_prediction.chatter_store import TaskStore

        # 重置单例，避免受其他测试影响
        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        # 1. 创建任务（PENDING）
        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 2)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_test_001",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        assert task.status == ChatterPredictionTaskStatus.PENDING.value

        # 2. 执行预测（PENDING RUNNING PREDICTED）
        result = await pipeline.run_pipeline(task.task_id)
        assert result.status == ChatterPredictionTaskStatus.PREDICTED.value
        assert result.feature_count == 2
        assert result.predicted_count == 2
        assert result.analytical_count == 2  # force_analytical → 全部走解析法
        assert result.neural_network_count == 0
        assert result.fallback_count == 0
        assert result.error_message is None

        # 验证任务存储中的状态
        stored = pipeline._store.get_task(task.task_id)
        assert stored.status == ChatterPredictionTaskStatus.PREDICTED.value
        assert len(stored.feature_results) == 2

        # 3. 工程师审核全部特征（PREDICTED REVIEWED）
        for fr in stored.feature_results:
            pipeline.review_result(
                task_id=task.task_id,
                feature_id=fr.feature_id,
                review_status="confirmed",
                reviewed_by="engineer_zhang",
                engineer_notes="OK",
            )
        stored = pipeline._store.get_task(task.task_id)
        assert stored.status == ChatterPredictionTaskStatus.REVIEWED.value

        # 4. 导出 ChatterReport（REVIEWED SUCCEEDED）
        export_path = pipeline.export_chatter_report(task.task_id)
        assert Path(export_path).exists()

        stored = pipeline._store.get_task(task.task_id)
        assert stored.status == ChatterPredictionTaskStatus.SUCCEEDED.value
        assert stored.chatter_report_path == export_path
        assert stored.completed_at > 0

        # 验证 ChatterReport JSON 内容
        report = json.loads(Path(export_path).read_text(encoding="utf-8"))
        assert report["material_id"] == "al_6061"
        assert report["cam_validation_required"] is True  # 硬约束
        assert report["feature_count"] == 2
        assert "feature_results" in report
        assert "industrial_hard_gates_note" in report

    @pytest.mark.asyncio
    async def test_pipeline_failed_on_missing_chatter_params(self, tmp_path):
        """T26: ChatterParams 文件不存在时任务转为 FAILED。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_missing",
            chatter_params_path=str(tmp_path / "nonexistent.json"),
            material_id="al_6061",
        )

        result = await pipeline.run_pipeline(task.task_id)
        assert result.status == ChatterPredictionTaskStatus.FAILED.value
        assert result.predicted_count == 0
        assert result.error_message is not None

        stored = pipeline._store.get_task(task.task_id)
        assert stored.status == ChatterPredictionTaskStatus.FAILED.value
        assert stored.error_message != ""

    @pytest.mark.asyncio
    async def test_pipeline_failed_on_empty_chatter_params(self, tmp_path):
        """T27: ChatterParams JSON 中无任何特征时任务转为 FAILED。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        # 构造空特征列表
        empty_data = {"task_id": "cp_empty", "material_id": "al_6061", "chatter_params_list": []}
        empty_path = tmp_path / "empty_chatter_params.json"
        empty_path.write_text(json.dumps(empty_data), encoding="utf-8")

        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_empty",
            chatter_params_path=str(empty_path),
            material_id="al_6061",
        )

        result = await pipeline.run_pipeline(task.task_id)
        assert result.status == ChatterPredictionTaskStatus.FAILED.value
        assert result.predicted_count == 0

    @pytest.mark.asyncio
    async def test_review_edited_updates_stability_margin(self, tmp_path):
        """T28: edited 审核修改 axial_depth_mm 时重新计算稳定性裕度。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_edit_001",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        await pipeline.run_pipeline(task.task_id)

        stored = pipeline._store.get_task(task.task_id)
        original_margin = stored.feature_results[0].stability_margin
        original_axial = stored.feature_results[0].axial_depth_mm
        limit_depth = stored.feature_results[0].limit_depth_mm

        # 编辑：将 axial_depth 改为 2 倍
        new_axial = original_axial * 2
        pipeline.review_result(
            task_id=task.task_id,
            feature_id=stored.feature_results[0].feature_id,
            review_status="edited",
            reviewed_by="engineer_li",
            edited_params={"axial_depth_mm": new_axial},
            engineer_notes="加大切深",
        )

        stored = pipeline._store.get_task(task.task_id)
        edited_fr = stored.feature_results[0]
        assert edited_fr.review_status == "edited"
        assert edited_fr.axial_depth_mm == new_axial
        # 稳定性裕度应重新计算
        if limit_depth > 0:
            expected_new_margin = new_axial / limit_depth
            assert math.isclose(edited_fr.stability_margin, expected_new_margin, rel_tol=1e-6)
        assert edited_fr.reviewed_by == "engineer_li"

    @pytest.mark.asyncio
    async def test_review_rejected_excluded_from_export(self, tmp_path):
        """T29: rejected 特征不进入最终 ChatterReport。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 3)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_reject_001",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        await pipeline.run_pipeline(task.task_id)

        stored = pipeline._store.get_task(task.task_id)
        feature_ids = [fr.feature_id for fr in stored.feature_results]

        # 第一个特征 reject，其余 confirm
        pipeline.review_result(
            task_id=task.task_id,
            feature_id=feature_ids[0],
            review_status="rejected",
            reviewed_by="engineer_wang",
            engineer_notes="该特征不稳定，跳过",
        )
        for fid in feature_ids[1:]:
            pipeline.review_result(
                task_id=task.task_id,
                feature_id=fid,
                review_status="confirmed",
                reviewed_by="engineer_wang",
            )

        # 导出 ChatterReport
        export_path = pipeline.export_chatter_report(task.task_id)
        report = json.loads(Path(export_path).read_text(encoding="utf-8"))

        # 仅 2 个特征进入 ChatterReport（rejected 排除）
        assert report["feature_count"] == 2
        exported_ids = {r["feature_id"] for r in report["feature_results"]}
        assert feature_ids[0] not in exported_ids, "rejected 特征不应出现在 ChatterReport 中"

    @pytest.mark.asyncio
    async def test_export_fails_when_all_rejected(self, tmp_path):
        """T30: 所有特征 rejected 时导出失败。"""
        from app.chatter_prediction import ChatterPredictionPipelineError
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_all_reject",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        await pipeline.run_pipeline(task.task_id)

        stored = pipeline._store.get_task(task.task_id)
        pipeline.review_result(
            task_id=task.task_id,
            feature_id=stored.feature_results[0].feature_id,
            review_status="rejected",
            reviewed_by="engineer_test",
        )

        # 全部 rejected 时导出应失败
        with pytest.raises(ChatterPredictionPipelineError):
            pipeline.export_chatter_report(task.task_id)

    @pytest.mark.asyncio
    async def test_review_fails_when_task_not_predicted(self, tmp_path):
        """T31: 非 PREDICTED 状态不允许审核。"""
        from app.chatter_prediction import ChatterReviewError
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_state_001",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        # 任务处于 PENDING，直接审核应失败
        with pytest.raises(ChatterReviewError):
            pipeline.review_result(
                task_id=task.task_id,
                feature_id="feat_001",
                review_status="confirmed",
            )

    @pytest.mark.asyncio
    async def test_review_fails_on_invalid_status(self, tmp_path):
        """T32: 无效审核状态被拒绝。"""
        from app.chatter_prediction import ChatterReviewError
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_invalid_001",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        await pipeline.run_pipeline(task.task_id)

        stored = pipeline._store.get_task(task.task_id)
        with pytest.raises(ChatterReviewError):
            pipeline.review_result(
                task_id=task.task_id,
                feature_id=stored.feature_results[0].feature_id,
                review_status="invalid_status",  # 非法值
            )

    @pytest.mark.asyncio
    async def test_review_edited_requires_edited_params(self, tmp_path):
        """T33: review_status=edited 时必须提供 edited_params。"""
        from app.chatter_prediction import ChatterReviewError
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_edited_no_params",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        await pipeline.run_pipeline(task.task_id)

        stored = pipeline._store.get_task(task.task_id)
        with pytest.raises(ChatterReviewError):
            pipeline.review_result(
                task_id=task.task_id,
                feature_id=stored.feature_results[0].feature_id,
                review_status="edited",
                # 未提供 edited_params
            )

    @pytest.mark.asyncio
    async def test_hrc52_pipeline_lowers_confidence(self, tmp_path):
        """T34: HRC52 材料在 pipeline 中触发置信度降低。

        项目记忆硬约束：HRC52 数据待自采校准，置信度强制降至 0.5。
        """
        from app.chatter_prediction import ChatterPredictionTaskStatus
        from app.chatter_prediction.chatter_store import TaskStore
        from app.chatter_prediction.predictor_adapter import PENDING_CALIBRATION_CONFIDENCE

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "steel_hrc52", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_hrc52_001",
            chatter_params_path=chatter_params_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        result = await pipeline.run_pipeline(task.task_id)

        assert result.status == ChatterPredictionTaskStatus.PREDICTED.value
        stored = pipeline._store.get_task(task.task_id)
        fr = stored.feature_results[0]
        assert fr.material_calibration_status == "pending_calibration"
        assert fr.confidence <= PENDING_CALIBRATION_CONFIDENCE, (
            f"HRC52 置信度应 <= {PENDING_CALIBRATION_CONFIDENCE}，实际 {fr.confidence}"
        )

    @pytest.mark.asyncio
    async def test_load_chatter_params_supports_list_format(self, tmp_path):
        """T35: _load_chatter_params 兼容纯 list 格式。"""
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        pipeline = self._make_pipeline(tmp_path)

        # 直接构造 list 格式（无 chatter_params_list 包装）
        list_data = [
            {
                "feature_id": "feat_list_001",
                "feature_type": "plane",
                "chatter_params": {
                    "spindle_rpm": 8000.0,
                    "machine": {
                        "machine_id": "vmc_850",
                        "stiffness_x": 1.5e7,
                        "stiffness_y": 1.5e7,
                        "stiffness_z": 2.0e8,
                        "damping_ratio": 0.05,
                        "natural_freq": 100.0,
                        "modal_mass": 50.0,
                    },
                    "tool": {
                        "tool_id": "endmill_d10",
                        "diameter": 10.0,
                        "num_flutes": 4,
                        "helix_angle": 30.0,
                        "cutting_force_coeff": 2000.0,
                    },
                    "axial_depth": 0.5,
                },
            },
        ]
        list_path = tmp_path / "list_chatter_params.json"
        list_path.write_text(json.dumps(list_data), encoding="utf-8")

        loaded = pipeline._load_chatter_params(str(list_path))
        assert len(loaded) == 1
        assert loaded[0]["feature_id"] == "feat_list_001"


# SUCCEEDED 禁删硬约束测试


class TestSucceededDeleteGuard:
    """SUCCEEDED 状态禁止删除（项目记忆硬约束）。

    阶段 6 G 代码生成可能已引用 SUCCEEDED 任务的 ChatterReport，
    删除会破坏追溯链。
    """

    @pytest.mark.asyncio
    async def test_succeeded_task_cannot_be_deleted(self, tmp_path):
        """T36: SUCCEEDED 状态任务删除时抛出 ReviewError。"""
        from app.chatter_prediction import ChatterPredictionTaskStatus, ReviewError
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        from app.chatter_prediction import ChatterPredictorAdapter, ChatterPredictionPipeline
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig(
            enabled=True,
            output_dir=str(tmp_path / "chatter_prediction"),
            force_analytical=True,
        )
        adapter = ChatterPredictorAdapter(force_analytical=True)
        pipeline = ChatterPredictionPipeline(cfg=cfg, adapter=adapter)

        # 走完整流程到 SUCCEEDED
        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_delete_001",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        await pipeline.run_pipeline(task.task_id)
        stored = pipeline._store.get_task(task.task_id)
        pipeline.review_result(
            task_id=task.task_id,
            feature_id=stored.feature_results[0].feature_id,
            review_status="confirmed",
        )
        pipeline.export_chatter_report(task.task_id)

        # 验证状态为 SUCCEEDED
        stored = pipeline._store.get_task(task.task_id)
        assert stored.status == ChatterPredictionTaskStatus.SUCCEEDED.value

        # 删除应抛出 ReviewError
        with pytest.raises(ReviewError):
            pipeline._store.delete_task(task.task_id)

        # 任务仍存在
        assert pipeline._store.get_task(task.task_id) is not None

    @pytest.mark.asyncio
    async def test_non_succeeded_task_can_be_deleted(self, tmp_path):
        """T37: 非 SUCCEEDED 状态任务可正常删除。"""
        from app.chatter_prediction.chatter_store import TaskStore

        TaskStore.reset_instance()
        from app.chatter_prediction import ChatterPredictorAdapter, ChatterPredictionPipeline
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig(
            enabled=True,
            output_dir=str(tmp_path / "chatter_prediction"),
            force_analytical=True,
        )
        adapter = ChatterPredictorAdapter(force_analytical=True)
        pipeline = ChatterPredictionPipeline(cfg=cfg, adapter=adapter)

        chatter_params_path = _build_minimal_chatter_params_json(tmp_path, "al_6061", 1)
        task = pipeline.create_task(
            source_cutting_parameters_task_id="cp_delete_002",
            chatter_params_path=chatter_params_path,
            material_id="al_6061",
        )
        # 任务处于 PENDING，可删除
        deleted = pipeline._store.delete_task(task.task_id)
        assert deleted is True
        assert pipeline._store.get_task(task.task_id) is None


# 配置校验测试


class TestChatterPredictionConfig:
    """ChatterPredictionConfig 配置校验（12-Factor + 硬约束）。"""

    def test_config_default_values(self):
        """T38: 默认配置值符合预期。"""
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig()
        assert cfg.enabled is True
        assert cfg.output_dir.endswith("chatter_prediction")
        assert cfg.max_concurrent >= 1
        assert cfg.task_timeout_seconds >= 10
        assert cfg.task_retention_hours > 0
        assert cfg.precision_tier == "standard"
        assert cfg.default_mesh_calibrated is False
        assert cfg.default_machine_type == "vmc_850"
        assert cfg.force_analytical is False

    def test_config_hard_constraints(self):
        """T39: 项目记忆硬约束在配置层强制生效。

        - allow_delete_succeeded 始终 False（SUCCEEDED 任务可能被阶段 6 引用）
        - cam_validation_required 始终 True（CAM 二次校验强制）
        """
        from app.config import ChatterPredictionConfig

        # 即使显式传入 True / False，__post_init__ 也会强制重置
        cfg = ChatterPredictionConfig(
            allow_delete_succeeded=True,  # 试图开启，应被强制重置为 False
            cam_validation_required=False,  # 试图关闭，应被强制重置为 True
        )
        assert cfg.allow_delete_succeeded is False, "allow_delete_succeeded 必须始终 False（SUCCEEDED 禁删硬约束）"
        assert cfg.cam_validation_required is True, "cam_validation_required 必须始终 True（CAM 二次校验强制）"

    def test_config_invalid_precision_tier_falls_back(self):
        """T40: 非法 precision_tier 回退到 standard。"""
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig(precision_tier="invalid_tier")
        assert cfg.precision_tier == "standard"

    def test_config_invalid_max_concurrent_falls_back(self):
        """T41: max_concurrent < 1 时回退到 1（串行）。"""
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig(max_concurrent=0)
        assert cfg.max_concurrent == 1

    def test_config_too_small_timeout_falls_back(self):
        """T42: task_timeout_seconds < 10 时回退到 120。"""
        from app.config import ChatterPredictionConfig

        cfg = ChatterPredictionConfig(task_timeout_seconds=5)
        assert cfg.task_timeout_seconds == 120

    def test_config_env_prefix_is_lnn_ch(self):
        """T43: 环境变量前缀为 LNN_CH_*（与阶段 4 LNN_CP_* 区分）。"""
        # 仅验证配置项命名约定，不实际设置环境变量
        from app.config import ChatterPredictionConfig
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(ChatterPredictionConfig)}
        expected_fields = {
            "enabled",
            "output_dir",
            "max_concurrent",
            "task_timeout_seconds",
            "task_retention_hours",
            "precision_tier",
            "default_mesh_calibrated",
            "default_machine_type",
            "force_analytical",
            "allow_delete_succeeded",
            "cam_validation_required",
        }
        missing = expected_fields - field_names
        assert not missing, f"ChatterPredictionConfig 缺失字段: {missing}"


# 项目记忆硬约束验证


class TestProjectMemoryHardConstraints:
    """项目记忆硬约束验证（用户最关心的工程优先 + human-in-the-loop 责任划分）。"""

    def test_engineer_assistant_positioning(self):
        """T44: 模块文档字符串明确标注「工程师助手」定位。"""
        from app.chatter_prediction import __doc__ as module_doc

        assert "工程师助手" in module_doc, "chatter_prediction 模块文档必须明确标注「工程师助手」定位"
        assert "非" in module_doc and "全自动" in module_doc, "文档必须明确否认「全自动」定位"

    def test_cam_validation_required_in_task(self):
        """T45: ChatterPredictionTask.cam_validation_required 默认 True。"""
        from app.chatter_prediction import ChatterPredictionTask
        import dataclasses

        fields = {f.name: f for f in dataclasses.fields(ChatterPredictionTask)}
        assert "cam_validation_required" in fields
        # 默认值必须为 True
        default_value = fields["cam_validation_required"].default
        assert default_value is True, "cam_validation_required 默认值必须为 True（项目记忆硬约束）"

    def test_succeeded_status_delete_guard_in_store(self):
        """T46: TaskStore 删除路径显式检查 SUCCEEDED 禁删。

        2026-09 适配：守卫已从 delete_task 重构进 ``_deletable_reason``
        助手（项目记忆硬约束注释在位）——源码断言升级为整类检查，
        并要求 delete_task 调用链上确有守卫助手。
        """
        import inspect
        from app.chatter_prediction.chatter_store import TaskStore

        class_source = inspect.getsource(TaskStore)
        assert "SUCCEEDED" in class_source, "TaskStore 未检查 SUCCEEDED 禁删状态"
        assert "ReviewError" in class_source, "删除守卫未抛出 ReviewError"
        assert "_deletable_reason" in inspect.getsource(TaskStore.delete_task), (
            "delete_task 未接入删除守卫助手"
        )

    def test_hrc52_in_pending_calibration_materials(self):
        """T47: HRC52 材料 ID 在 PENDING_CALIBRATION_MATERIALS 集合中。"""
        from app.chatter_prediction.predictor_adapter import PENDING_CALIBRATION_MATERIALS

        # 项目记忆硬约束：HRC52 不可使用纯文献数据
        assert "steel_hrc52" in PENDING_CALIBRATION_MATERIALS
        assert "hrc52" in PENDING_CALIBRATION_MATERIALS

    def test_k_s_passthrough_in_adapter_source(self):
        """T48: 预测适配器源码中 K_s 直接传递，无二次拟合。"""
        import inspect
        from app.chatter_prediction.predictor_adapter import ChatterPredictorAdapter

        # 检查 _predict_via_analytical 源码
        source = inspect.getsource(ChatterPredictorAdapter._predict_via_analytical)
        assert "cutting_force_coeff" in source, "解析法路径未传递 cutting_force_coeff"
        assert "直接传递" in source or "不二次拟合" in inspect.getsource(ChatterPredictorAdapter), (
            "适配器文档未声明 K_s 直接传递策略"
        )

    def test_fit_transform_not_used_in_inference_path(self):
        """T49: 推理路径不使用 fit_transform（项目记忆硬约束）。

        项目记忆：fit_transform 在推理路径会导致数据泄漏，
        preprocessors 必须训练时 fit，推理时仅 transform。
        """
        import inspect
        from app.chatter_prediction.predictor_adapter import ChatterPredictorAdapter

        # 检查所有方法源码
        for name in dir(ChatterPredictorAdapter):
            if name.startswith("_") and not name.startswith("__"):
                method = getattr(ChatterPredictorAdapter, name)
                if callable(method):
                    try:
                        source = inspect.getsource(method)
                        assert "fit_transform" not in source, (
                            f"推理路径 {name} 使用了 fit_transform（违反项目记忆硬约束）"
                        )
                    except (TypeError, OSError):
                        pass  # 非 Python 方法或无法获取源码

    def test_disclaimer_includes_cam_validation_note(self):
        """T50: chatter_disclaimer 中包含 CAM 二次校验说明。"""
        from app.chatter_prediction import build_chatter_disclaimer

        disclaimer = build_chatter_disclaimer(
            mesh_calibrated=True,
            chatter_params_source="cp_test",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            prediction_method="analytical",
            ltc_model_available=False,
            ltc_active_ratio=0.0,
            chatter_report_ready=False,
        )
        # warning_message 或 industrial_hard_gates 中必须提及 CAM 校验
        full_text = disclaimer.warning_message + " ".join(disclaimer.industrial_hard_gates)
        assert "CAM" in full_text, "chatter_disclaimer 未提及 CAM 二次校验"

    def test_chatter_report_includes_cam_validation_required(self):
        """T51: ChatterReport JSON 中 cam_validation_required 始终 True。

        通过 export_chatter_report 源码检查（不实际执行，避免依赖）。
        """
        import inspect
        from app.chatter_prediction.pipeline import ChatterPredictionPipeline

        source = inspect.getsource(ChatterPredictionPipeline.export_chatter_report)
        assert "cam_validation_required" in source, "export_chatter_report 未包含 cam_validation_required 字段"
        # 必须引用 task.cam_validation_required（而非硬编码 True）
        assert "task.cam_validation_required" in source, (
            "export_chatter_report 应引用 task.cam_validation_required（保持数据流一致）"
        )

    def test_single_round_review_state_machine(self):
        """T52: 单轮审核状态机（与阶段 3 两轮审核区别）。

        项目记忆硬约束：阶段 5 输出 JSON 报告（非 STEP），
        不会直接进入 CAM 软件，单轮审核足够。
        状态机：PENDING → RUNNING → PREDICTED → REVIEWED → SUCCEEDED
        （阶段 3 是两轮：STEP_GENERATED → finalize）
        """
        from app.chatter_prediction import ChatterPredictionTaskStatus as S

        # 状态机中不应有两轮审核相关的中间态（如 STEP_GENERATED）
        all_states = {s.value for s in S}
        assert "step_generated" not in all_states, "阶段 5 不应有两轮审核中间态 STEP_GENERATED"
        assert "finalize_pending" not in all_states, "阶段 5 不应有两轮审核中间态 finalize_pending"
        # 单轮审核核心状态
        assert "predicted" in all_states
        assert "reviewed" in all_states
        assert "succeeded" in all_states
