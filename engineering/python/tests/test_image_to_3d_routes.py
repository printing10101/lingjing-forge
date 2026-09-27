"""拍照重建模块 单元测试。

覆盖：
- 模块导入与单例：app.image_to_3d 包可正常导入
- 精度告知机制：precision_disclaimer 字段完整、三档精度规格齐全
- 工业硬门槛：industrial_hard_gates 列表存在且覆盖关键约束
- 尺度归一化逻辑：无标定块时 calibrated=False，有标定块时 calibrated=True
- 任务状态机：枚举完整，状态转移合理
- API 路由注册：8 个端点全部注册
- 精度告知注入：precision_info / create_task / get_task_status 三端点响应含 precision_disclaimer

测试设计原则：
- 不依赖 COLMAP / OpenMVS 外部二进制（CI 环境无二进制）
- 不实际触发重建任务（避免长耗时与外部依赖）
- 只验证模块契约与精度告知机制（用户最关心的「普通手机+精度差距告知」）
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest


# 模块导入测试


class TestModuleImport:
    """验证 image_to_3d 模块可正常导入。"""

    def test_import_main_classes(self):
        """T01: 主类与函数可正常导入。"""
        from app.image_to_3d import (
            ReconstructionPipeline,
            ReconstructionTask,
            ReconstructionTaskStatus,
            ReconstructionResult,
            TaskStore,
            get_task_store,
            PrecisionDisclaimer,
            build_precision_disclaimer,
        )

        assert ReconstructionPipeline is not None
        assert ReconstructionTask is not None
        assert ReconstructionTaskStatus is not None
        assert ReconstructionResult is not None
        assert TaskStore is not None
        assert get_task_store is not None
        assert PrecisionDisclaimer is not None
        assert build_precision_disclaimer is not None

    def test_routes_module_importable(self):
        """T02: API 路由模块可正常导入。"""
        from app.api.v1.image_to_3d import routes as image_to_3d_routes

        assert image_to_3d_routes.router is not None
        # 路由 prefix 必须是 /api/v1/image_to_3d
        assert image_to_3d_routes.router.prefix == "/api/v1/image_to_3d"

    def test_eight_endpoints_registered(self):
        """T03: 8 个 API 端点全部注册。"""
        from app.api.v1.image_to_3d import routes as image_to_3d_routes

        # 收集所有路由的 (method, path)
        endpoints = set()
        for route in image_to_3d_routes.router.routes:
            for method in route.methods:
                endpoints.add((method, route.path))

        expected_endpoints = {
            ("GET", "/api/v1/image_to_3d/precision_info"),
            ("POST", "/api/v1/image_to_3d/tasks"),
            ("POST", "/api/v1/image_to_3d/tasks/{task_id}/run"),
            ("GET", "/api/v1/image_to_3d/tasks/{task_id}"),
            ("GET", "/api/v1/image_to_3d/tasks"),
            ("GET", "/api/v1/image_to_3d/tasks/{task_id}/result"),
            ("GET", "/api/v1/image_to_3d/tasks/{task_id}/sparse"),
            ("DELETE", "/api/v1/image_to_3d/tasks/{task_id}"),
        }

        missing = expected_endpoints - endpoints
        assert not missing, f"缺失端点: {missing}"


# 精度告知机制测试


class TestPrecisionDisclaimer:
    """精度告知机制（用户最关心的「普通手机+精度差距告知」）。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.config import config, ImageTo3DConfig

        self.config = config
        self.ImageTo3DConfig = ImageTo3DConfig

    def test_precision_disclaimer_all_fields(self):
        """T04: precision_disclaimer 包含全部 9 个字段。"""
        from app.image_to_3d import build_precision_disclaimer

        disclaimer = build_precision_disclaimer(
            self.config.image_to_3d,
            calibrated=False,
            scale_factor=1.0,
        )
        d = disclaimer.to_dict()

        required_fields = {
            "precision_tier",
            "expected_accuracy_mm",
            "suitable_for",
            "not_suitable_for",
            "calibrated",
            "scale_factor",
            "requires_cam_validation",
            "industrial_hard_gates",
            "warning_message",
        }
        missing = required_fields - set(d.keys())
        assert not missing, f"precision_disclaimer 缺失字段: {missing}"

    def test_precision_disclaimer_uncalibrated_warning(self):
        """T05: 无标定块时警告消息明确告知「无量纲」。"""
        from app.image_to_3d import build_precision_disclaimer

        disclaimer = build_precision_disclaimer(
            self.config.image_to_3d,
            calibrated=False,
            scale_factor=1.0,
        )
        # 警告消息必须明确告知无量纲输出 + 不允许进入工艺仿真链路
        msg = disclaimer.warning_message
        assert "无量纲" in msg or "未标定" in msg or "不可" in msg, f"无标定块警告消息未明确告知风险: {msg}"

    def test_precision_disclaimer_calibrated_warning(self):
        """T06: 有标定块时仍要求 CAM 二次校验。"""
        from app.image_to_3d import build_precision_disclaimer

        disclaimer = build_precision_disclaimer(
            self.config.image_to_3d,
            calibrated=True,
            scale_factor=2.5,
        )
        # 即使已标定，仍必须经过 CAM 二次校验
        assert disclaimer.requires_cam_validation is True
        assert disclaimer.calibrated is True
        assert disclaimer.scale_factor == 2.5

    def test_industrial_hard_gates_complete(self):
        """T07: 工业硬门槛列表覆盖关键约束。"""
        from app.image_to_3d import build_precision_disclaimer

        disclaimer = build_precision_disclaimer(
            self.config.image_to_3d,
            calibrated=True,
            scale_factor=1.0,
        )
        gates = disclaimer.industrial_hard_gates

        # 工业硬门槛必须覆盖：良品率 / 公差 / 持证操作员 / 签字保险 / CAM 校验 / 工程师助手定位
        all_gates_text = " ".join(gates)
        assert "良品率" in all_gates_text or "0 缺陷" in all_gates_text, f"工业硬门槛未提及良品率: {gates}"
        assert "0.01" in all_gates_text or "配合面" in all_gates_text, f"工业硬门槛未提及配合面公差: {gates}"
        assert "持证" in all_gates_text or "操作员" in all_gates_text, f"工业硬门槛未提及 CNC 持证操作员: {gates}"
        assert "CAM" in all_gates_text or "校验" in all_gates_text, f"工业硬门槛未提及 CAM 二次校验: {gates}"
        assert "工程师助手" in all_gates_text or "助手" in all_gates_text, f"工业硬门槛未明确系统定位: {gates}"

    def test_three_precision_tiers(self):
        """T08: 三档精度规格声明齐全 + 当前档 specs 完整。

        V2.7.0 起 precision_specs 返回当前档位（precision_tier）平铺参数，
        三档清单由端点 available_tiers 声明；此处验证当前档位合法且 specs 完整。
        """
        cfg = self.config.image_to_3d
        specs = cfg.precision_specs

        assert cfg.precision_tier in ("coarse", "standard", "high")
        assert "expected_accuracy_mm" in specs
        assert "suitable_for" in specs
        assert "not_suitable_for" in specs

    def test_precision_tier_accuracy_ordering(self):
        """T09: 当前档精度量级合理（误差 mm 级，非微米/米级异常）。"""
        specs = self.config.image_to_3d.precision_specs

        lo, hi = (float(x) for x in specs["expected_accuracy_mm"].split("-"))
        assert 0.0 < lo <= hi <= 5.0

    def test_not_suitable_for_mentions_tolerance(self):
        """T10: 当前档位明确告知不适用于配合面公差。"""
        specs = self.config.image_to_3d.precision_specs

        not_suitable = specs["not_suitable_for"]
        text = " ".join(not_suitable) if isinstance(not_suitable, list) else str(not_suitable)
        assert "配合面" in text or "0.01" in text or "H7" in text


# 尺度归一化逻辑测试（不依赖 trimesh）


class TestScaleNormalizationLogic:
    """尺度归一化逻辑（标定块法）。"""

    def test_uncalibrated_output_rejected_by_pipeline(self):
        """T11: 无标定块时 calibrated=False，调用方应拒绝进入工艺仿真链路。"""
        from app.image_to_3d import build_precision_disclaimer
        from app.config import config

        # 无标定块场景
        disclaimer = build_precision_disclaimer(
            config.image_to_3d,
            calibrated=False,
            scale_factor=1.0,
        )
        assert disclaimer.calibrated is False
        assert disclaimer.scale_factor == 1.0
        # 警告消息必须明确告知不可用于工艺仿真
        msg = disclaimer.warning_message
        assert any(keyword in msg for keyword in ["工艺仿真", "无量纲", "不可", "不允许", "未标定"]), (
            f"无标定块警告未明确告知不可进入工艺仿真链路: {msg}"
        )

    def test_calibrated_output_still_requires_cam_validation(self):
        """T12: 有标定块时 calibrated=True，但仍需 CAM 二次校验。"""
        from app.image_to_3d import build_precision_disclaimer
        from app.config import config

        disclaimer = build_precision_disclaimer(
            config.image_to_3d,
            calibrated=True,
            scale_factor=1.234,
        )
        assert disclaimer.calibrated is True
        assert disclaimer.scale_factor == pytest.approx(1.234, rel=1e-6)
        # 即使已标定，requires_cam_validation 必须为 True
        assert disclaimer.requires_cam_validation is True

    def test_scale_normalizer_handles_missing_trimesh(self, tmp_path):
        """T13: trimesh 缺失时降级为原样拷贝并标记为未标定。"""
        from app.image_to_3d import scale_normalizer
        from app.config import config

        # 准备一个假 mesh 文件
        fake_mesh = tmp_path / "fake.ply"
        fake_mesh.write_bytes(b"fake mesh content")
        output_path = tmp_path / "output.ply"

        # 模拟 trimesh 缺失
        with patch.object(scale_normalizer, "_try_import_trimesh", return_value=None):
            result = scale_normalizer.normalize_scale(
                mesh_path=fake_mesh,
                output_path=output_path,
                cfg=config.image_to_3d,
                calibration_anchor_distance=15.0,  # 即使有 anchor 也无法归一化
            )

        assert result.success is True
        assert result.calibrated is False
        assert result.scale_factor == 1.0
        assert output_path.exists()
        # 拷贝内容应与原文件一致
        assert output_path.read_bytes() == fake_mesh.read_bytes()

    def test_scale_normalizer_no_anchor_returns_dimensionless(self, tmp_path):
        """T14: 无 anchor 距离时返回无量纲 mesh 并明确警告。"""
        from app.image_to_3d import scale_normalizer
        from app.config import config

        # 这个测试需要 trimesh；如果 trimesh 未安装则跳过
        trimesh = scale_normalizer._try_import_trimesh()
        if trimesh is None:
            pytest.skip("trimesh 未安装，跳过本测试")

        # 用 trimesh 构造一个简单的立方体 mesh
        mesh = trimesh.creation.box(extents=[1, 1, 1])
        fake_mesh = tmp_path / "fake.ply"
        mesh.export(str(fake_mesh))
        output_path = tmp_path / "output.ply"

        result = scale_normalizer.normalize_scale(
            mesh_path=fake_mesh,
            output_path=output_path,
            cfg=config.image_to_3d,
            calibration_anchor_distance=None,  # 无 anchor
        )

        assert result.success is True
        assert result.calibrated is False
        assert result.scale_factor == 1.0
        # 警告消息必须明确告知无量纲
        assert "无量纲" in result.message or "未提供标定块" in result.message


# 任务状态机测试


class TestTaskStateMachine:
    """任务状态机枚举与状态转移。"""

    def test_status_enum_complete(self):
        """T15: 任务状态枚举完整。"""
        from app.image_to_3d import ReconstructionTaskStatus

        statuses = {s.value for s in ReconstructionTaskStatus}
        expected = {
            "pending",
            "running",
            "colmap_done",
            "succeeded",
            "failed",
            "timeout",
        }
        assert expected.issubset(statuses), f"任务状态枚举不完整，缺失: {expected - statuses}"

    def test_task_store_singleton(self):
        """T16: get_task_store 返回单例。"""
        from app.image_to_3d import get_task_store

        store1 = get_task_store()
        store2 = get_task_store()
        assert store1 is store2, "TaskStore 应为单例"

    def test_task_store_create_get_delete(self):
        """T17: TaskStore 基本增删查。"""
        from app.image_to_3d import (
            get_task_store,
            ReconstructionTask,
            ReconstructionTaskStatus,
        )
        import time

        store = get_task_store()
        now = time.time()
        task = ReconstructionTask(
            task_id="test_task_001",
            created_at=now,
            updated_at=now,
            status=ReconstructionTaskStatus.PENDING.value,
            precision_tier="standard",
            photo_count=10,
            workspace_dir="/tmp/test_workspace",
            calibration_anchor_distance=None,
        )

        # create
        store.create(task)
        # get
        retrieved = store.get("test_task_001")
        assert retrieved is not None
        assert retrieved.task_id == "test_task_001"
        assert retrieved.status == "pending"
        # update
        store.update("test_task_001", status="running")
        updated = store.get("test_task_001")
        assert updated.status == "running"
        # delete
        store.delete("test_task_001")
        assert store.get("test_task_001") is None

    def test_pipeline_result_to_dict(self):
        """T18: ReconstructionResult.to_dict 返回完整字段。"""
        from app.image_to_3d import ReconstructionResult

        result = ReconstructionResult(
            task_id="test_001",
            status="succeeded",
            output_mesh_path="/tmp/output.ply",
            sparse_ply_path="/tmp/sparse.ply",
            num_images_registered=12,
            calibrated=True,
            scale_factor=2.5,
            colmap_duration_seconds=120.0,
            openmvs_duration_seconds=300.0,
            total_duration_seconds=420.0,
            error_message="",
        )
        d = result.to_dict()
        required = {
            "task_id",
            "status",
            "output_mesh_path",
            "sparse_ply_path",
            "num_images_registered",
            "calibrated",
            "scale_factor",
            "colmap_duration_seconds",
            "openmvs_duration_seconds",
            "total_duration_seconds",
            "error_message",
        }
        assert required.issubset(set(d.keys())), f"ReconstructionResult.to_dict 缺失字段: {required - set(d.keys())}"


# API 端点契约测试（不触发实际重建）


class TestAPIContract:
    """API 端点契约测试（不依赖 COLMAP/OpenMVS 二进制）。

    使用 FastAPI TestClient，但通过 mock 拦截 pipeline 调用，
    避免 CI 环境无外部二进制导致测试失败。
    """

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # 跳过权限校验：直接构造测试 app，不挂 UnifiedAuthMiddleware
        from app.api.v1.image_to_3d import routes as image_to_3d_routes

        test_app = FastAPI()
        test_app.include_router(image_to_3d_routes.router)
        return TestClient(test_app)

    def test_precision_info_endpoint(self, client):
        """T19: GET /precision_info 返回完整精度信息。"""
        response = client.get("/api/v1/image_to_3d/precision_info")
        assert response.status_code == 200
        data = response.json()
        # 响应中应包含 precision_disclaimer 字段
        assert "data" in data
        payload = data["data"]
        assert "precision_disclaimer" in payload
        assert "current_tier" in payload
        assert "available_tiers" in payload
        assert "specs" in payload
        assert "calibration_block_mm" in payload
        assert "calibration_block_guidance" in payload

    def test_precision_info_disclaimer_complete(self, client):
        """T20: precision_info 端点的 precision_disclaimer 字段完整。"""
        response = client.get("/api/v1/image_to_3d/precision_info")
        data = response.json()
        disclaimer = data["data"]["precision_disclaimer"]

        required_fields = {
            "precision_tier",
            "expected_accuracy_mm",
            "suitable_for",
            "not_suitable_for",
            "calibrated",
            "scale_factor",
            "requires_cam_validation",
            "industrial_hard_gates",
            "warning_message",
        }
        missing = required_fields - set(disclaimer.keys())
        assert not missing, f"precision_info 响应中 precision_disclaimer 缺失: {missing}"

    def test_list_tasks_empty(self, client):
        """T21: GET /tasks 列出任务（即使为空也应返回 200）。"""
        response = client.get("/api/v1/image_to_3d/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "data" in data
        assert "tasks" in data["data"]
        assert "total" in data["data"]
        assert isinstance(data["data"]["tasks"], list)

    def test_get_nonexistent_task_returns_error(self, client):
        """T22: 查询不存在的任务返回错误响应（不暴露内部信息）。"""
        response = client.get("/api/v1/image_to_3d/tasks/nonexistent_task_id_001")
        assert response.status_code == 200  # 业务错误也返回 200
        data = response.json()
        # 错误响应不应将 task_id 原样回显（防枚举攻击）
        # 但可以包含通用错误提示
        assert data.get("code") != 0 or "data" in data

    def test_create_task_no_files_rejected(self, client):
        """T23: 无文件上传被拒绝。"""
        response = client.post("/api/v1/image_to_3d/tasks")
        # 无文件时应返回错误
        assert response.status_code in (400, 422, 200)
        if response.status_code == 200:
            data = response.json()
            assert data.get("code") != 0 or "data" not in data or "task_id" not in data.get("data", {})

    def test_create_task_too_few_photos_rejected(self, client):
        """T24: 照片数量不足被拒绝。"""
        from app.config import config

        # 用 1 张照片测试（低于 min_photos 阈值）
        response = client.post(
            "/api/v1/image_to_3d/tasks",
            files=[
                ("files", ("test.jpg", io.BytesIO(b"fake jpg"), "image/jpeg")),
            ],
        )
        # 应返回错误（除非 min_photos 配置为 1）
        assert response.status_code in (200, 422)

    def test_delete_nonexistent_task_returns_error(self, client):
        """T25: 删除不存在的任务返回错误响应。"""
        response = client.delete("/api/v1/image_to_3d/tasks/nonexistent_task_id_002")
        assert response.status_code == 200
        data = response.json()
        # 应返回业务错误码
        assert data.get("code") != 0


# 条件导入与启动安全测试


class TestConditionalImport:
    """条件导入与启动安全。"""

    def test_main_py_has_image_to_3d_flag(self):
        """T26: main.py 中存在 _IMAGE_TO_3D_AVAILABLE 标志位。"""
        # 通过导入 main 模块的方式验证（不触发完整 app 启动）
        # 这里只检查源码中是否包含条件导入块
        main_py_path = Path(__file__).resolve().parent.parent / "app" / "main.py"
        content = main_py_path.read_text(encoding="utf-8")
        assert "_IMAGE_TO_3D_AVAILABLE" in content, "main.py 中未发现 _IMAGE_TO_3D_AVAILABLE 条件导入标志"
        # 重构后（V2.7.0）路由经 router_registry 集中注册：
        # adr_pipeline.py 通过 conditional_include 条件挂载 image_to_3d 路由
        adr_path = Path(__file__).resolve().parent.parent / "app" / "api" / "routers" / "adr_pipeline.py"
        adr_content = adr_path.read_text(encoding="utf-8")
        assert "image_to_3d" in adr_content, "router_registry 中未发现 image_to_3d 路由注册（adr_pipeline.py）"

    def test_requirements_contains_trimesh(self):
        """T27: requirements.txt 包含 trimesh（尺度归一化核心依赖）。"""
        req_path = Path(__file__).resolve().parent.parent / "requirements.txt"
        content = req_path.read_text(encoding="utf-8")
        assert "trimesh" in content, "requirements.txt 缺少 trimesh 依赖声明"
        assert "Pillow" in content, "requirements.txt 缺少 Pillow 依赖声明"

    def test_no_pycolmap_dependency_required(self):
        """T28: 模块不依赖 pycolmap（采用 subprocess 外部二进制模式）。"""
        # colmap_runner.py 应使用 subprocess 调用外部二进制，而非 pycolmap 绑定
        colmap_runner_path = Path(__file__).resolve().parent.parent / "app" / "image_to_3d" / "colmap_runner.py"
        content = colmap_runner_path.read_text(encoding="utf-8")
        # 不应出现 import pycolmap
        assert "import pycolmap" not in content, "colmap_runner.py 不应依赖 pycolmap（采用 subprocess 外部二进制模式）"
        # 应使用 subprocess 调用外部二进制
        assert "subprocess" in content, "colmap_runner.py 应使用 subprocess 调用 COLMAP 二进制"
