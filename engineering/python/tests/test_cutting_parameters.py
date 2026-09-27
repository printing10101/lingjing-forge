"""切削参数推荐模块 单元测试（阶段 4）。

覆盖：
- 模块导入与导出：app.cutting_parameters 包可正常导入全部子模块
- 精度告知机制：cutting_disclaimer 字段完整、8 条工业硬门槛覆盖关键约束
- 枚举完整性：CuttingParametersTaskStatus (7 态) / CuttingReviewStatus (4 态) / OperationType (2 类)
- RecommendedCuttingParams.effective_params()：edited 状态合并 edited_params，否则用推荐值
- 材料解析器：HRC52 补充数据 + materials.json 数据库 + 未找到异常
- 推荐引擎：plane/cylinder/hole/boss 四种特征 + 精度档位→operation + 警告构造 + 异常路径
- to_chatter_params_dict()：阶段 5 对接契约（K_s=cutting_force_coeff）
- Pipeline 状态机：PENDING → RUNNING → PARAMS_RECOMMENDED → REVIEWED → SUCCEEDED（含 FAILED/CANCELLED）
- 配置校验：CuttingParametersConfig 11 个字段 + __post_init__ 非法值回退 + allow_delete_succeeded 硬约束
- API 路由注册：9 个端点全部注册
- 项目记忆硬约束：工程师助手定位 / CAM 二次校验强制 / SUCCEEDED 禁删 / HRC52 待校准

测试设计原则（与 test_parametric_geometry.py / test_feature_extraction.py 一致）：
- 不依赖 materials.json 真实存在（HRC52 补充数据为内存常量，必然可用）
- 不实际触发 ChatterParams JSON 写入磁盘（通过 tmp_path 隔离）
- 只验证模块契约与工程师审核流程（用户最关心的「human-in-the-loop 责任划分」）
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest


# 模块导入测试


class TestModuleImport:
    """验证 cutting_parameters 模块可正常导入。"""

    def test_import_main_classes(self):
        """T01: 主类与函数可正常导入。"""
        from app.cutting_parameters import (
            CuttingParametersTask,
            CuttingParametersTaskStatus,
            CuttingReviewStatus,
            OperationType,
            RecommendedCuttingParams,
            TaskStore,
            generate_task_id,
            get_task_store,
            CuttingDisclaimer,
            INDUSTRIAL_HARD_GATES,
            build_cutting_disclaimer,
            MaterialParams,
            MaterialResolver,
            MaterialResolverError,
            MaterialNotFoundError,
            get_material_resolver,
            reset_material_resolver,
            CuttingParamRecommender,
            RecommendationError,
            FeatureNotSupportedError,
            SUPPORTED_FEATURE_TYPES,
            FEATURE_TYPE_RADIAL_DEPTH_RATIO,
            to_chatter_params_dict,
            CuttingParametersPipeline,
            CuttingParametersResult,
            CuttingParametersPipelineError,
            CuttingReviewError,
            FeaturesLoadError,
        )

        for obj in [
            CuttingParametersTask,
            CuttingParametersTaskStatus,
            CuttingReviewStatus,
            OperationType,
            RecommendedCuttingParams,
            TaskStore,
            generate_task_id,
            get_task_store,
            CuttingDisclaimer,
            INDUSTRIAL_HARD_GATES,
            build_cutting_disclaimer,
            MaterialParams,
            MaterialResolver,
            MaterialResolverError,
            MaterialNotFoundError,
            get_material_resolver,
            reset_material_resolver,
            CuttingParamRecommender,
            RecommendationError,
            FeatureNotSupportedError,
            SUPPORTED_FEATURE_TYPES,
            FEATURE_TYPE_RADIAL_DEPTH_RATIO,
            to_chatter_params_dict,
            CuttingParametersPipeline,
            CuttingParametersResult,
            CuttingParametersPipelineError,
            CuttingReviewError,
            FeaturesLoadError,
        ]:
            assert obj is not None, f"{obj} 导入失败"

    def test_routes_module_importable(self):
        """T02: API 路由模块可正常导入。"""
        from app.api.v1 import cutting_parameters as cp_routes_pkg

        assert hasattr(cp_routes_pkg, "routes")
        assert cp_routes_pkg.routes.router is not None
        assert cp_routes_pkg.routes.router.prefix == "/api/v1/cutting_parameters"

    def test_nine_endpoints_registered(self):
        """T03: 9 个 API 端点全部注册。

        端点清单（与 routes.py 一致）：
        - GET  /precision_info
        - POST /tasks
        - POST /tasks/{task_id}/run
        - GET  /tasks/{task_id}
        - GET  /tasks
        - GET  /tasks/{task_id}/result
        - POST /tasks/{task_id}/review
        - POST /tasks/{task_id}/export
        - GET  /tasks/{task_id}/chatter_params/download
        - DELETE /tasks/{task_id}
        """
        from app.api.v1.cutting_parameters import routes as cp_routes

        endpoints = set()
        for route in cp_routes.router.routes:
            for method in route.methods:
                endpoints.add((method, route.path))

        expected_endpoints = {
            ("GET", "/api/v1/cutting_parameters/precision_info"),
            ("POST", "/api/v1/cutting_parameters/tasks"),
            ("POST", "/api/v1/cutting_parameters/tasks/{task_id}/run"),
            ("GET", "/api/v1/cutting_parameters/tasks/{task_id}"),
            ("GET", "/api/v1/cutting_parameters/tasks"),
            ("GET", "/api/v1/cutting_parameters/tasks/{task_id}/result"),
            ("POST", "/api/v1/cutting_parameters/tasks/{task_id}/review"),
            ("POST", "/api/v1/cutting_parameters/tasks/{task_id}/export"),
            (
                "GET",
                "/api/v1/cutting_parameters/tasks/{task_id}/chatter_params/download",
            ),
            ("DELETE", "/api/v1/cutting_parameters/tasks/{task_id}"),
        }

        missing = expected_endpoints - endpoints
        assert not missing, f"缺失端点: {missing}"

    def test_router_tags_and_permissions(self):
        """T04: 路由 tags 标注为「Engineer-Assisted」并启用权限校验。"""
        from app.api.v1.cutting_parameters import routes as cp_routes

        tags = cp_routes.router.tags
        assert any("Engineer-Assisted" in t for t in tags), f"路由 tags 未标注工程师辅助定位: {tags}"

        # 顶层 dependencies 必须包含权限依赖
        deps = cp_routes.router.dependencies
        assert len(deps) > 0, "路由未挂载任何 dependencies（权限校验缺失）"


# 精度告知机制测试


class TestCuttingDisclaimer:
    """精度告知机制（项目记忆硬约束：HRC52 待校准 + 工程师助手定位）。"""

    def test_disclaimer_all_fields(self):
        """T05: cutting_disclaimer 包含全部 13 个字段。"""
        from app.cutting_parameters import build_cutting_disclaimer

        disclaimer = build_cutting_disclaimer(
            mesh_calibrated=False,
            feature_source="external_upload",
            step_source="step_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            tool_diameter_mm=10.0,
            chatter_params_ready=False,
        )
        d = disclaimer.to_dict()

        required_fields = {
            "mesh_calibrated",
            "feature_source",
            "step_source",
            "material_id",
            "material_calibration_status",
            "precision_tier",
            "machine_type",
            "tool_diameter_mm",
            "requires_engineer_review",
            "requires_cam_validation",
            "chatter_params_ready",
            "industrial_hard_gates",
            "warning_message",
        }
        missing = required_fields - set(d.keys())
        assert not missing, f"cutting_disclaimer 缺失字段: {missing}"

    def test_uncalibrated_mesh_warning(self):
        """T06: mesh 未标定时警告明确告知「无量纲」。"""
        from app.cutting_parameters import build_cutting_disclaimer

        disclaimer = build_cutting_disclaimer(
            mesh_calibrated=False,
            feature_source="external_upload",
            step_source="step_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            tool_diameter_mm=10.0,
        )
        msg = disclaimer.warning_message
        assert "未标定" in msg or "无量纲" in msg, f"未标定 mesh 警告未告知无量纲: {msg}"

    def test_pending_calibration_material_warning(self):
        """T07: HRC52 pending_calibration 状态在警告中体现。"""
        from app.cutting_parameters import build_cutting_disclaimer

        disclaimer = build_cutting_disclaimer(
            mesh_calibrated=True,
            feature_source="feat_task_001",
            step_source="step_task_001",
            material_id="steel_hrc52",
            material_calibration_status="pending_calibration",
            precision_tier="standard",
            machine_type="vmc_850",
            tool_diameter_mm=10.0,
        )
        msg = disclaimer.warning_message
        assert "待自采" in msg or "pending_calibration" in msg or "估算" in msg, f"HRC52 警告未体现待校准状态: {msg}"

    def test_chatter_params_not_ready_warning(self):
        """T08: ChatterParams 未输出时警告明确告知阶段 5 不可用。"""
        from app.cutting_parameters import build_cutting_disclaimer

        disclaimer = build_cutting_disclaimer(
            mesh_calibrated=True,
            feature_source="feat_task_001",
            step_source="step_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            tool_diameter_mm=10.0,
            chatter_params_ready=False,
        )
        msg = disclaimer.warning_message
        assert "ChatterParams" in msg or "颤振预测" in msg or "阶段 5" in msg, (
            f"未输出 ChatterParams 警告未体现阶段 5 不可用: {msg}"
        )

    def test_calibrated_still_requires_review(self):
        """T09: 即便 mesh 已标定 + 材料已校准，仍强制工程师审核 + CAM 二次校验。"""
        from app.cutting_parameters import build_cutting_disclaimer

        disclaimer = build_cutting_disclaimer(
            mesh_calibrated=True,
            feature_source="feat_task_001",
            step_source="step_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="high",
            machine_type="vmc_850",
            tool_diameter_mm=10.0,
            chatter_params_ready=True,
        )
        # 即便全标定，仍必须工程师审核 + CAM 二次校验
        assert disclaimer.requires_engineer_review is True
        assert disclaimer.requires_cam_validation is True
        assert disclaimer.mesh_calibrated is True
        assert disclaimer.chatter_params_ready is True
        # 警告消息必须提及工程师审核或 CAM 校验
        msg = disclaimer.warning_message
        assert "审核" in msg or "CAM" in msg, f"已标定警告未提及工程师审核或 CAM 校验: {msg}"

    def test_industrial_hard_gates_complete(self):
        """T10: 8 条工业硬门槛覆盖关键约束。"""
        from app.cutting_parameters import build_cutting_disclaimer

        disclaimer = build_cutting_disclaimer(
            mesh_calibrated=True,
            feature_source="feat_task_001",
            step_source="step_task_001",
            material_id="al_6061",
            material_calibration_status="calibrated",
            precision_tier="standard",
            machine_type="vmc_850",
            tool_diameter_mm=10.0,
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
        # K_s 影响颤振预测
        assert "K_s" in all_gates_text or "specific_cutting_force" in all_gates_text, (
            f"硬门槛未提及 K_s 影响颤振预测: {gates}"
        )
        # 导师签字 + 保险
        assert "导师" in all_gates_text or "保险" in all_gates_text, f"硬门槛未提及导师签字或保险: {gates}"


# 枚举完整性测试


class TestEnums:
    """枚举完整性测试。"""

    def test_task_status_seven_states(self):
        """T11: CuttingParametersTaskStatus 7 个状态完整。"""
        from app.cutting_parameters import CuttingParametersTaskStatus

        expected = {
            "pending",
            "running",
            "params_recommended",
            "reviewed",
            "succeeded",
            "failed",
            "cancelled",
        }
        actual = {s.value for s in CuttingParametersTaskStatus}
        assert actual == expected, f"任务状态枚举不匹配: {actual - expected}"

    def test_review_status_four_states(self):
        """T12: CuttingReviewStatus 4 个状态完整。"""
        from app.cutting_parameters import CuttingReviewStatus

        expected = {"pending", "confirmed", "rejected", "edited"}
        actual = {s.value for s in CuttingReviewStatus}
        assert actual == expected, f"审核状态枚举不匹配: {actual - expected}"

    def test_operation_type_two_types(self):
        """T13: OperationType 2 个类型完整。"""
        from app.cutting_parameters import OperationType

        expected = {"roughing", "finishing"}
        actual = {s.value for s in OperationType}
        assert actual == expected, f"操作类型枚举不匹配: {actual - expected}"

    def test_status_enum_is_str_enum(self):
        """T14: 状态枚举继承 str，便于 JSON 序列化。"""
        from app.cutting_parameters import (
            CuttingParametersTaskStatus,
            CuttingReviewStatus,
            OperationType,
        )

        assert CuttingParametersTaskStatus.PENDING == "pending"
        assert CuttingReviewStatus.CONFIRMED == "confirmed"
        assert OperationType.ROUGHING == "roughing"


# effective_params 测试


class TestEffectiveParams:
    """RecommendedCuttingParams.effective_params() 测试。"""

    def _make_params(self, review_status="pending", edited_params=None):
        from app.cutting_parameters import RecommendedCuttingParams

        return RecommendedCuttingParams(
            feature_id="feat_001",
            feature_type="plane",
            operation="roughing",
            spindle_speed_rpm=3000.0,
            feed_rate_mm_per_min=480.0,
            feed_per_tooth_mm=0.04,
            cutting_speed_m_per_min=94.25,
            axial_depth_mm=1.25,
            radial_depth_mm=5.0,
            review_status=review_status,
            edited_params=edited_params or {},
        )

    def test_pending_returns_recommended_values(self):
        """T15: pending 状态返回推荐值副本。"""
        params = self._make_params(review_status="pending")
        effective = params.effective_params()

        assert effective["spindle_speed_rpm"] == 3000.0
        assert effective["axial_depth_mm"] == 1.25
        # 修改返回值不应影响原参数
        effective["spindle_speed_rpm"] = 999.0
        assert params.spindle_speed_rpm == 3000.0, "effective_params() 未返回副本，污染了推荐值"

    def test_confirmed_returns_recommended_values(self):
        """T16: confirmed 状态返回推荐值副本。"""
        params = self._make_params(review_status="confirmed")
        effective = params.effective_params()

        assert effective["spindle_speed_rpm"] == 3000.0
        assert effective["feed_rate_mm_per_min"] == 480.0

    def test_rejected_returns_recommended_values(self):
        """T17: rejected 状态返回推荐值副本（export 时会被排除）。"""
        params = self._make_params(review_status="rejected")
        effective = params.effective_params()

        assert effective["spindle_speed_rpm"] == 3000.0

    def test_edited_merges_edited_params(self):
        """T18: edited 状态合并 edited_params 到推荐值（edited 优先）。"""
        params = self._make_params(
            review_status="edited",
            edited_params={
                "spindle_speed_rpm": 2500.0,  # 覆盖推荐值
                "axial_depth_mm": 0.8,  # 覆盖推荐值
            },
        )
        effective = params.effective_params()

        # edited_params 优先覆盖
        assert effective["spindle_speed_rpm"] == 2500.0, f"edited_params 未覆盖推荐值: {effective}"
        assert effective["axial_depth_mm"] == 0.8
        # 未编辑的字段保留推荐值
        assert effective["feed_rate_mm_per_min"] == 480.0
        assert effective["feed_per_tooth_mm"] == 0.04

    def test_edited_with_empty_edited_params_falls_back(self):
        """T19: edited 状态但 edited_params 为空时回退到推荐值。"""
        params = self._make_params(review_status="edited", edited_params={})
        effective = params.effective_params()

        # edited_params 为空时回退到推荐值
        assert effective["spindle_speed_rpm"] == 3000.0
        assert effective["axial_depth_mm"] == 1.25


# 材料解析器测试


class TestMaterialResolver:
    """材料解析器测试（HRC52 补充数据 + 数据库查询）。"""

    def test_hrc52_supplement_available(self):
        """T20: HRC52 淬火钢补充数据可查询。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        material = resolver.get_material("steel_hrc52")

        assert material.id == "steel_hrc52"
        assert material.name == "HRC52淬火钢"
        assert material.category == "hardened_steel"

    def test_hrc52_pending_calibration(self):
        """T21: HRC52 标记为 pending_calibration（项目记忆硬约束）。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        material = resolver.get_material("steel_hrc52")

        assert material.calibration_status == "pending_calibration", (
            f"HRC52 应标记 pending_calibration，实际: {material.calibration_status}"
        )
        assert material.data_source == "hrc52_supplement"

    def test_hrc52_hardness(self):
        """T22: HRC52 硬度字段正确。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        material = resolver.get_material("steel_hrc52")

        assert material.hardness_hrc == 52.0
        assert material.hardness_hb == 495.0  # HRC52 ≈ HB495

    def test_hrc52_k_s_value(self):
        """T23: HRC52 的 K_s（specific_cutting_force）取值符合淬火钢工程估算。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        material = resolver.get_material("steel_hrc52")

        # HRC52 淬火钢 K_s ≈ 2800 N/mm²（项目记忆约束）
        assert material.specific_cutting_force == 2800.0, (
            f"HRC52 K_s 应为 2800 N/mm²，实际: {material.specific_cutting_force}"
        )

    def test_list_materials_includes_hrc52(self):
        """T24: list_materials() 必须包含 HRC52。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        materials = resolver.list_materials()
        ids = [m.id for m in materials]

        assert "steel_hrc52" in ids, f"list_materials() 未包含 HRC52 补充数据: {ids}"

    def test_has_material_hrc52(self):
        """T25: has_material() 正确识别 HRC52。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        assert resolver.has_material("steel_hrc52") is True

    def test_unknown_material_raises(self):
        """T26: 未找到材料 ID 抛出 MaterialResolverError。"""
        from app.cutting_parameters import (
            MaterialResolver,
            MaterialResolverError,
        )

        resolver = MaterialResolver()
        with pytest.raises(MaterialResolverError) as exc_info:
            resolver.get_material("nonexistent_material_xyz")
        assert "nonexistent_material_xyz" in str(exc_info.value)

    def test_hrc52_cutting_speed_range_lower_than_aluminum(self):
        """T27: HRC52 切削速度上限低于铝合金（淬火钢难加工）。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        hrc52 = resolver.get_material("steel_hrc52")

        # HRC52 roughing 切削速度 [20, 40] m/min，远低于铝合金
        roughing_max = hrc52.cutting_speed_range["roughing"][1]
        assert roughing_max <= 50.0, f"HRC52 切削速度上限 {roughing_max} 应低于 50 m/min"


# 推荐引擎测试


class TestRecommender:
    """切削参数推荐引擎测试。"""

    @pytest.fixture(autouse=True)
    def setup_recommender(self):
        from app.cutting_parameters import (
            CuttingParamRecommender,
            MaterialResolver,
        )

        self.resolver = MaterialResolver()
        self.recommender = CuttingParamRecommender(resolver=self.resolver)

    def test_plane_recommendation_complete(self):
        """T28: plane 特征推荐参数完整（含全部字段）。"""
        params = self.recommender.recommend(
            feature_id="feat_plane_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )

        assert params.feature_id == "feat_plane_001"
        assert params.feature_type == "plane"
        assert params.operation in ("roughing", "finishing")
        assert params.spindle_speed_rpm > 0
        assert params.feed_rate_mm_per_min > 0
        assert params.feed_per_tooth_mm > 0
        assert params.cutting_speed_m_per_min > 0
        assert params.axial_depth_mm > 0
        assert params.radial_depth_mm > 0
        assert params.material_id == "steel_hrc52"
        assert params.tool_diameter_mm == 10.0
        assert params.num_flutes == 4
        assert params.review_status == "pending"

    def test_high_tier_uses_finishing(self):
        """T29: precision_tier=high → finishing 操作。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="high",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        assert params.operation == "finishing", f"high 档位应为 finishing，实际: {params.operation}"

    def test_coarse_tier_uses_roughing(self):
        """T30: precision_tier=coarse → roughing 操作。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="coarse",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        assert params.operation == "roughing"

    def test_standard_hole_uses_finishing(self):
        """T31: standard + hole → finishing（孔精度敏感）。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="hole",
            material_id="steel_hrc52",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        assert params.operation == "finishing", f"standard + hole 应为 finishing，实际: {params.operation}"

    def test_standard_plane_uses_roughing(self):
        """T32: standard + plane → roughing（大面先粗加工）。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        assert params.operation == "roughing"

    def test_unsupported_feature_type_raises(self):
        """T33: 不支持的特征类型抛 FeatureNotSupportedError。"""
        from app.cutting_parameters import FeatureNotSupportedError

        with pytest.raises(FeatureNotSupportedError) as exc_info:
            self.recommender.recommend(
                feature_id="feat_001",
                feature_type="cone",  # 不支持
                material_id="steel_hrc52",
                precision_tier="standard",
                tool_diameter_mm=10.0,
                num_flutes=4,
            )
        assert "cone" in str(exc_info.value)

    def test_invalid_tool_diameter_raises(self):
        """T34: 刀具直径 <=0 抛 RecommendationError。"""
        from app.cutting_parameters import RecommendationError

        with pytest.raises(RecommendationError):
            self.recommender.recommend(
                feature_id="feat_001",
                feature_type="plane",
                material_id="steel_hrc52",
                precision_tier="standard",
                tool_diameter_mm=0.0,
                num_flutes=4,
            )

    def test_invalid_num_flutes_raises(self):
        """T35: 齿数 <=0 抛 RecommendationError。"""
        from app.cutting_parameters import RecommendationError

        with pytest.raises(RecommendationError):
            self.recommender.recommend(
                feature_id="feat_001",
                feature_type="plane",
                material_id="steel_hrc52",
                precision_tier="standard",
                tool_diameter_mm=10.0,
                num_flutes=0,
            )

    def test_unknown_material_raises(self):
        """T36: 未找到材料抛 MaterialNotFoundError。"""
        from app.cutting_parameters import MaterialNotFoundError

        with pytest.raises(MaterialNotFoundError):
            self.recommender.recommend(
                feature_id="feat_001",
                feature_type="plane",
                material_id="nonexistent_xyz",
                precision_tier="standard",
                tool_diameter_mm=10.0,
                num_flutes=4,
            )

    def test_hrc52_warning_pending_calibration(self):
        """T37: HRC52 推荐参数 warnings 必须含 pending_calibration 提示。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        warnings_text = " ".join(params.warnings)
        assert "pending_calibration" in warnings_text or "待自采" in warnings_text, (
            f"HRC52 warnings 未提及 pending_calibration: {params.warnings}"
        )

    def test_hrc52_warning_high_hardness(self):
        """T38: HRC52 推荐参数 warnings 必须含高硬度刀具提示。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        warnings_text = " ".join(params.warnings)
        # 必须提及硬质合金或陶瓷刀具
        assert "硬质合金" in warnings_text or "陶瓷" in warnings_text, (
            f"HRC52 warnings 未提及硬质合金/陶瓷刀具: {params.warnings}"
        )

    def test_hole_warning_recommends_reaming(self):
        """T39: hole 特征 warnings 必须含铰孔/镗孔建议。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="hole",
            material_id="steel_hrc52",
            precision_tier="standard",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        warnings_text = " ".join(params.warnings)
        assert "铰孔" in warnings_text or "镗孔" in warnings_text, f"hole warnings 未提及铰孔/镗孔: {params.warnings}"

    def test_coarse_tier_warning(self):
        """T40: coarse 档位 warnings 必须含「不可用于配合面」提示。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="coarse",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        warnings_text = " ".join(params.warnings)
        assert "配合面" in warnings_text, f"coarse warnings 未提及配合面限制: {params.warnings}"

    def test_radial_depth_ratios(self):
        """T41: 4 种特征径向切深比例正确。"""
        from app.cutting_parameters import FEATURE_TYPE_RADIAL_DEPTH_RATIO

        # plane: 0.5 * D
        assert FEATURE_TYPE_RADIAL_DEPTH_RATIO["plane"] == 0.5
        # cylinder: 0.3 * D（侧铣保守）
        assert FEATURE_TYPE_RADIAL_DEPTH_RATIO["cylinder"] == 0.3
        # hole: 1.0 * D（满刀）
        assert FEATURE_TYPE_RADIAL_DEPTH_RATIO["hole"] == 1.0
        # boss: 0.5 * D
        assert FEATURE_TYPE_RADIAL_DEPTH_RATIO["boss"] == 0.5

    def test_spindle_rpm_formula(self):
        """T42: 主轴转速公式 n = V_c * 1000 / (π * D) 正确。"""
        params = self.recommender.recommend(
            feature_id="feat_001",
            feature_type="plane",
            material_id="steel_hrc52",
            precision_tier="coarse",  # roughing
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        # V_c 取材料范围 1/3 分位，D=10
        # 验证公式：n = V_c * 1000 / (π * 10)
        expected_rpm = params.cutting_speed_m_per_min * 1000.0 / (math.pi * 10.0)
        assert abs(params.spindle_speed_rpm - expected_rpm) < 1.0, (
            f"主轴转速公式错误: 实际 {params.spindle_speed_rpm}, 期望 {expected_rpm}"
        )

    def test_supported_feature_types_complete(self):
        """T43: SUPPORTED_FEATURE_TYPES 包含 4 种特征。"""
        from app.cutting_parameters import SUPPORTED_FEATURE_TYPES

        expected = {"plane", "cylinder", "hole", "boss"}
        assert set(SUPPORTED_FEATURE_TYPES) == expected, f"支持特征类型不完整: {SUPPORTED_FEATURE_TYPES}"


# to_chatter_params_dict 测试（阶段 5 对接契约）


class TestToChatterParams:
    """to_chatter_params_dict() 测试（阶段 5 颤振预测输入契约）。"""

    def _make_recommended_params(self, review_status="confirmed", edited_params=None):
        from app.cutting_parameters import RecommendedCuttingParams

        return RecommendedCuttingParams(
            feature_id="feat_001",
            feature_type="plane",
            operation="roughing",
            spindle_speed_rpm=3000.0,
            feed_rate_mm_per_min=480.0,
            feed_per_tooth_mm=0.04,
            cutting_speed_m_per_min=94.25,
            axial_depth_mm=1.25,
            radial_depth_mm=5.0,
            review_status=review_status,
            edited_params=edited_params or {},
            material_id="steel_hrc52",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )

    def test_chatter_params_dict_structure(self):
        """T44: 输出 dict 含 spindle_rpm / machine / tool / axial_depth 4 个键。"""
        from app.cutting_parameters import to_chatter_params_dict

        params = self._make_recommended_params()
        cp = to_chatter_params_dict(params)

        required_keys = {"spindle_rpm", "machine", "tool", "axial_depth"}
        assert set(cp.keys()) == required_keys, f"ChatterParams dict 键不匹配: {set(cp.keys())}"

    def test_tool_contains_cutting_force_coeff(self):
        """T45: tool dict 含 cutting_force_coeff 字段（阶段 5 关键契约）。"""
        from app.cutting_parameters import to_chatter_params_dict

        params = self._make_recommended_params()
        cp = to_chatter_params_dict(params)

        tool = cp["tool"]
        assert "cutting_force_coeff" in tool, f"tool dict 缺失 cutting_force_coeff: {tool}"

    def test_cutting_force_coeff_equals_k_s(self):
        """T46: cutting_force_coeff = MaterialParams.specific_cutting_force (K_s)。"""
        from app.cutting_parameters import (
            MaterialResolver,
            to_chatter_params_dict,
        )

        params = self._make_recommended_params()
        resolver = MaterialResolver()
        cp = to_chatter_params_dict(params, resolver=resolver)

        material = resolver.get_material("steel_hrc52")
        assert cp["tool"]["cutting_force_coeff"] == material.specific_cutting_force, (
            f"cutting_force_coeff {cp['tool']['cutting_force_coeff']} 不等于 K_s {material.specific_cutting_force}"
        )

    def test_edited_params_override(self):
        """T47: edited 状态时 ChatterParams 使用工程师编辑值。"""
        from app.cutting_parameters import to_chatter_params_dict

        params = self._make_recommended_params(
            review_status="edited",
            edited_params={"spindle_speed_rpm": 2500.0, "axial_depth_mm": 0.8},
        )
        cp = to_chatter_params_dict(params)

        # edited 值应覆盖推荐值
        assert cp["spindle_rpm"] == 2500.0, f"ChatterParams 未使用 edited spindle_rpm: {cp['spindle_rpm']}"
        assert cp["axial_depth"] == 0.8, f"ChatterParams 未使用 edited axial_depth: {cp['axial_depth']}"

    def test_machine_dict_structure(self):
        """T48: machine dict 含阶段 5 必需字段。"""
        from app.cutting_parameters import to_chatter_params_dict

        params = self._make_recommended_params()
        cp = to_chatter_params_dict(params, machine_id="vmc_850")

        machine = cp["machine"]
        required_fields = {
            "machine_id",
            "stiffness_x",
            "stiffness_y",
            "stiffness_z",
            "damping_ratio",
            "natural_freq",
            "modal_mass",
        }
        assert required_fields.issubset(set(machine.keys())), (
            f"machine dict 缺失字段: {required_fields - set(machine.keys())}"
        )
        assert machine["machine_id"] == "vmc_850"


# Pipeline 状态机测试


class TestPipelineStateMachine:
    """流水线状态机测试（PENDING → RUNNING → PARAMS_RECOMMENDED → REVIEWED → SUCCEEDED）。"""

    @pytest.fixture(autouse=True)
    def setup_pipeline(self, tmp_path):
        """每个测试前重置 TaskStore 单例 + 构造独立 pipeline。"""
        from app.cutting_parameters import (
            CuttingParametersPipeline,
            MaterialResolver,
            TaskStore,
        )
        from app.config import CuttingParametersConfig

        TaskStore.reset_instance()
        self.CuttingParametersConfig = CuttingParametersConfig
        self.cfg = CuttingParametersConfig(
            enabled=True,
            output_dir=str(tmp_path / "cp_output"),
        )
        self.resolver = MaterialResolver()
        self.pipeline = CuttingParametersPipeline(cfg=self.cfg, resolver=self.resolver)
        yield
        TaskStore.reset_instance()

    def _write_features_json(self, feature_count=2) -> str:
        """构造阶段 2 confirmed_features.json。"""
        features = []
        for i in range(feature_count):
            features.append(
                {
                    "feature_id": f"feat_plane_{i:03d}",
                    "feature_type": "plane",
                }
            )
        features_json = Path(self.cfg.output_dir) / "confirmed_features.json"
        features_json.parent.mkdir(parents=True, exist_ok=True)
        features_json.write_text(
            json.dumps({"features": features}, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(features_json)

    def test_create_task_pending(self):
        """T49: create_task() 创建的任务初始状态为 PENDING。"""
        features_path = self._write_features_json()
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        assert task.status == "pending"
        assert task.material_id == "steel_hrc52"
        assert task.precision_tier == "standard"
        assert task.mesh_calibrated is True
        assert task.cam_validation_required is True  # 硬约束：始终 True

    def test_run_pipeline_to_params_recommended(self):
        """T50: run_pipeline() 后状态变为 PARAMS_RECOMMENDED。"""
        import asyncio

        features_path = self._write_features_json(feature_count=2)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        result = asyncio.run(self.pipeline.run_pipeline(task.task_id))

        assert result.status == "params_recommended"
        assert result.feature_count == 2
        assert result.recommended_count == 2
        assert result.material_id == "steel_hrc52"

    def test_review_all_features_to_reviewed(self):
        """T51: 全部特征审核完毕后状态变为 REVIEWED。"""
        import asyncio

        features_path = self._write_features_json(feature_count=2)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        # 审核第一个特征
        self.pipeline.review_params(
            task_id=task.task_id,
            feature_id="feat_plane_000",
            review_status="confirmed",
            reviewed_by="engineer_zhang",
        )
        # 此时状态应仍为 PARAMS_RECOMMENDED
        task_after_one = self.pipeline._store.get_task(task.task_id)
        assert task_after_one.status == "params_recommended"

        # 审核第二个特征
        self.pipeline.review_params(
            task_id=task.task_id,
            feature_id="feat_plane_001",
            review_status="confirmed",
            reviewed_by="engineer_zhang",
        )
        # 全部审核完毕 REVIEWED
        task_after_all = self.pipeline._store.get_task(task.task_id)
        assert task_after_all.status == "reviewed", f"全部审核完毕未转 REVIEWED: {task_after_all.status}"

    def test_export_chatter_params_to_succeeded(self):
        """T52: export_chatter_params() 后状态变为 SUCCEEDED 并写 JSON。"""
        import asyncio

        features_path = self._write_features_json(feature_count=2)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        # 审核全部特征
        for i in range(2):
            self.pipeline.review_params(
                task_id=task.task_id,
                feature_id=f"feat_plane_{i:03d}",
                review_status="confirmed",
                reviewed_by="engineer_zhang",
            )

        # 导出
        export_path = self.pipeline.export_chatter_params(task.task_id)
        assert Path(export_path).exists(), f"ChatterParams JSON 未写入: {export_path}"

        task_final = self.pipeline._store.get_task(task.task_id)
        assert task_final.status == "succeeded"
        assert task_final.chatter_params_path == export_path

        # 验证 JSON 内容
        with open(export_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["task_id"] == task.task_id
        assert data["material_id"] == "steel_hrc52"
        assert data["feature_count"] == 2
        assert len(data["chatter_params_list"]) == 2
        # 每条含 k_s 字段
        for cp_entry in data["chatter_params_list"]:
            assert "k_s_n_per_mm2" in cp_entry
            assert cp_entry["k_s_n_per_mm2"] == 2800.0  # HRC52 K_s

    def test_succeeded_task_cannot_be_deleted(self):
        """T53: SUCCEEDED 状态任务禁止删除（项目记忆硬约束）。"""
        import asyncio

        from app.cutting_parameters import ReviewError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))
        self.pipeline.review_params(
            task_id=task.task_id,
            feature_id="feat_plane_000",
            review_status="confirmed",
        )
        self.pipeline.export_chatter_params(task.task_id)

        # SUCCEEDED 任务禁止删除
        with pytest.raises(ReviewError) as exc_info:
            self.pipeline._store.delete_task(task.task_id)
        assert "SUCCEEDED" in str(exc_info.value) or "禁止删除" in str(exc_info.value)

    def test_non_pending_cannot_run(self):
        """T54: 非 PENDING/FAILED 状态不能触发 run_pipeline。"""
        import asyncio

        from app.cutting_parameters import CuttingParametersPipelineError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        # 已 PARAMS_RECOMMENDED，再次 run 应失败
        with pytest.raises(CuttingParametersPipelineError):
            asyncio.run(self.pipeline.run_pipeline(task.task_id))

    def test_non_params_recommended_cannot_review(self):
        """T55: 非 PARAMS_RECOMMENDED 状态不能审核。"""
        from app.cutting_parameters import CuttingReviewError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        # PENDING 状态不能审核
        with pytest.raises(CuttingReviewError):
            self.pipeline.review_params(
                task_id=task.task_id,
                feature_id="feat_plane_000",
                review_status="confirmed",
            )

    def test_non_reviewed_cannot_export(self):
        """T56: 非 REVIEWED 状态不能导出 ChatterParams。"""
        import asyncio

        from app.cutting_parameters import CuttingParametersPipelineError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))
        # PARAMS_RECOMMENDED 状态不能导出
        with pytest.raises(CuttingParametersPipelineError):
            self.pipeline.export_chatter_params(task.task_id)

    def test_rejected_features_excluded_from_export(self):
        """T57: rejected 特征不进入 ChatterParams JSON。"""
        import asyncio

        features_path = self._write_features_json(feature_count=2)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        # 一个 confirmed，一个 rejected
        self.pipeline.review_params(
            task_id=task.task_id,
            feature_id="feat_plane_000",
            review_status="confirmed",
        )
        self.pipeline.review_params(
            task_id=task.task_id,
            feature_id="feat_plane_001",
            review_status="rejected",
            engineer_notes="该特征不需要切削参数",
        )

        export_path = self.pipeline.export_chatter_params(task.task_id)
        with open(export_path, encoding="utf-8") as f:
            data = json.load(f)

        # 只有 1 条（confirmed），rejected 被排除
        assert data["feature_count"] == 1, f"rejected 特征未排除: feature_count={data['feature_count']}"
        assert len(data["chatter_params_list"]) == 1
        assert data["chatter_params_list"][0]["feature_id"] == "feat_plane_000"

    def test_edited_features_included_in_export(self):
        """T58: edited 特征进入 ChatterParams JSON（使用编辑值）。"""
        import asyncio

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        # edited 状态
        self.pipeline.review_params(
            task_id=task.task_id,
            feature_id="feat_plane_000",
            review_status="edited",
            edited_params={"spindle_speed_rpm": 2500.0},
            engineer_notes="降低转速以减小颤振风险",
        )
        export_path = self.pipeline.export_chatter_params(task.task_id)

        with open(export_path, encoding="utf-8") as f:
            data = json.load(f)

        # edited 特征应进入导出
        assert data["feature_count"] == 1
        cp = data["chatter_params_list"][0]["chatter_params"]
        # spindle_rpm 应为编辑值 2500.0
        assert cp["spindle_rpm"] == 2500.0, f"edited 特征未使用编辑值: {cp['spindle_rpm']}"

    def test_edited_without_params_raises(self):
        """T59: edited 状态但未提供 edited_params 抛 CuttingReviewError。"""
        import asyncio

        from app.cutting_parameters import CuttingReviewError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        with pytest.raises(CuttingReviewError):
            self.pipeline.review_params(
                task_id=task.task_id,
                feature_id="feat_plane_000",
                review_status="edited",
                # 不提供 edited_params
            )

    def test_invalid_review_status_raises(self):
        """T60: 无效审核状态抛 CuttingReviewError。"""
        import asyncio

        from app.cutting_parameters import CuttingReviewError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        with pytest.raises(CuttingReviewError):
            self.pipeline.review_params(
                task_id=task.task_id,
                feature_id="feat_plane_000",
                review_status="invalid_status",
            )

    def test_nonexistent_feature_id_raises(self):
        """T61: 审核不存在的 feature_id 抛 CuttingReviewError。"""
        import asyncio

        from app.cutting_parameters import CuttingReviewError

        features_path = self._write_features_json(feature_count=1)
        task = self.pipeline.create_task(
            source_parametric_geometry_task_id="pg_task_001",
            step_file_path="/tmp/mock.step",
            input_features_path=features_path,
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        asyncio.run(self.pipeline.run_pipeline(task.task_id))

        with pytest.raises(CuttingReviewError):
            self.pipeline.review_params(
                task_id=task.task_id,
                feature_id="nonexistent_feat",
                review_status="confirmed",
            )


# 配置校验测试


class TestConfigValidation:
    """CuttingParametersConfig 配置校验测试。"""

    def test_default_config(self):
        """T62: 默认配置字段完整。"""
        from app.config import CuttingParametersConfig

        cfg = CuttingParametersConfig()
        assert cfg.enabled is True
        assert cfg.output_dir.endswith("cutting_parameters")
        assert cfg.max_concurrent == 1
        assert cfg.task_timeout_seconds == 60
        assert cfg.task_retention_hours == 168
        assert cfg.default_tool_diameter_mm == 10.0
        assert cfg.default_num_flutes == 4
        assert cfg.default_machine_type == "vmc_850"
        assert cfg.precision_tier == "standard"
        assert cfg.default_mesh_calibrated is False
        # allow_delete_succeeded 强制 False
        assert cfg.allow_delete_succeeded is False

    def test_invalid_precision_tier_falls_back(self):
        """T63: 非法 precision_tier 回退为 standard。"""
        from app.config import CuttingParametersConfig

        cfg = CuttingParametersConfig(precision_tier="invalid_tier")
        assert cfg.precision_tier == "standard", f"非法 precision_tier 未回退: {cfg.precision_tier}"

    def test_invalid_tool_diameter_falls_back(self):
        """T64: 非法 default_tool_diameter_mm 回退为 10.0。"""
        from app.config import CuttingParametersConfig

        cfg = CuttingParametersConfig(default_tool_diameter_mm=-5.0)
        assert cfg.default_tool_diameter_mm == 10.0

    def test_invalid_num_flutes_falls_back(self):
        """T65: 非法 default_num_flutes 回退为 4。"""
        from app.config import CuttingParametersConfig

        cfg = CuttingParametersConfig(default_num_flutes=0)
        assert cfg.default_num_flutes == 4

    def test_invalid_max_concurrent_falls_back(self):
        """T66: 非法 max_concurrent 回退为 1。"""
        from app.config import CuttingParametersConfig

        cfg = CuttingParametersConfig(max_concurrent=0)
        assert cfg.max_concurrent == 1

    def test_invalid_task_timeout_falls_back(self):
        """T67: 过小的 task_timeout_seconds 回退为 60。"""
        from app.config import CuttingParametersConfig

        cfg = CuttingParametersConfig(task_timeout_seconds=5)
        assert cfg.task_timeout_seconds == 60

    def test_allow_delete_succeeded_forced_false(self):
        """T68: allow_delete_succeeded=True 时强制重置为 False（项目记忆硬约束）。"""
        from app.config import CuttingParametersConfig

        # 即便显式传 True，也应被强制重置
        cfg = CuttingParametersConfig(allow_delete_succeeded=True)
        assert cfg.allow_delete_succeeded is False, (
            "allow_delete_succeeded=True 未被强制重置为 False，违反项目记忆硬约束"
        )


# 项目记忆硬约束集中验证


class TestProjectMemoryHardConstraints:
    """项目记忆硬约束集中验证（CI 必跑）。"""

    def test_cam_validation_always_required(self):
        """T69: cam_validation_required 字段始终为 True（不可关闭）。"""
        from app.cutting_parameters import CuttingParametersTask

        task = CuttingParametersTask(
            task_id="cp_test_001",
            created_at=time.time(),
            source_parametric_geometry_task_id="pg_001",
            step_file_path="/tmp/mock.step",
            input_features_path="/tmp/features.json",
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
        )
        # cam_validation_required 字段默认 True，无法在构造时设为 False
        assert task.cam_validation_required is True

    def test_engineer_assistant_positioning(self):
        """T70: 工业硬门槛明确标注「工程师助手」定位。"""
        from app.cutting_parameters import INDUSTRIAL_HARD_GATES

        gates_text = " ".join(INDUSTRIAL_HARD_GATES)
        assert "工程师助手" in gates_text
        assert "非「全自动切削参数生成器」" in gates_text or "非「全自动" in gates_text

    def test_cam_validation_in_hard_gates(self):
        """T71: 工业硬门槛明确要求 CAM 二次校验。"""
        from app.cutting_parameters import INDUSTRIAL_HARD_GATES

        gates_text = " ".join(INDUSTRIAL_HARD_GATES)
        assert "CAM" in gates_text
        assert "NX" in gates_text or "PowerMill" in gates_text or "PyCAM" in gates_text

    def test_mentor_signoff_in_hard_gates(self):
        """T72: 工业硬门槛明确要求导师签字 + 保险。"""
        from app.cutting_parameters import INDUSTRIAL_HARD_GATES

        gates_text = " ".join(INDUSTRIAL_HARD_GATES)
        assert "导师" in gates_text
        assert "保险" in gates_text

    def test_hrc52_calibration_status(self):
        """T73: HRC52 数据 calibration_status=pending_calibration（待自采校准）。"""
        from app.cutting_parameters import MaterialResolver

        resolver = MaterialResolver()
        material = resolver.get_material("steel_hrc52")
        assert material.calibration_status == "pending_calibration"
        assert material.data_source == "hrc52_supplement"

    def test_k_s_affects_chatter_prediction(self):
        """T74: K_s 直接传入 ChatterParams 的 cutting_force_coeff（阶段 5 契约）。"""
        from app.cutting_parameters import (
            MaterialResolver,
            RecommendedCuttingParams,
            to_chatter_params_dict,
        )

        resolver = MaterialResolver()
        hrc52 = resolver.get_material("steel_hrc52")

        params = RecommendedCuttingParams(
            feature_id="feat_001",
            feature_type="plane",
            operation="roughing",
            spindle_speed_rpm=3000.0,
            feed_rate_mm_per_min=480.0,
            feed_per_tooth_mm=0.04,
            cutting_speed_m_per_min=94.25,
            axial_depth_mm=1.25,
            radial_depth_mm=5.0,
            material_id="steel_hrc52",
            tool_diameter_mm=10.0,
            num_flutes=4,
        )
        cp = to_chatter_params_dict(params, resolver=resolver)
        # K_s 直接进入 cutting_force_coeff
        assert cp["tool"]["cutting_force_coeff"] == hrc52.specific_cutting_force

    def test_succeeded_no_delete_constraint(self):
        """T75: SUCCEEDED 状态任务禁止删除（阶段 5 已引用 ChatterParams）。"""
        from app.cutting_parameters import (
            CuttingParametersTask,
            CuttingParametersTaskStatus,
            ReviewError,
            TaskStore,
        )

        TaskStore.reset_instance()
        store = TaskStore()

        task = CuttingParametersTask(
            task_id="cp_succ_001",
            created_at=time.time(),
            source_parametric_geometry_task_id="pg_001",
            step_file_path="/tmp/mock.step",
            input_features_path="/tmp/features.json",
            material_id="steel_hrc52",
            precision_tier="standard",
            mesh_calibrated=True,
            status=CuttingParametersTaskStatus.SUCCEEDED.value,
        )
        store.create_task(task)

        with pytest.raises(ReviewError):
            store.delete_task(task.task_id)

        TaskStore.reset_instance()
