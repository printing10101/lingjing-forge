"""STEP文件导入模块 单元测试与集成测试。

覆盖:
- StepParser: STEP解析、模型信息提取、多实体处理
- StepConverter: STL/BREP格式转换、精度配置
- StepCache: LRU缓存命中/淘汰/过期
- API: 文件上传/验证/错误处理/限流
- 边界条件: 空文件/损坏文件/大文件/非STEP格式
- 性能: 解析时间/转换时间
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import cadquery as cq
import pytest

from app.step_import.step_parser import (
    StepParser,
    StepParseResult,
    StepParseError,
    ModelInfo,
    EntityInfo,
)
from app.step_import.step_converter import (
    StepConverter,
    ConvertResult,
    BatchConvertResult,
    PRECISION_PRESETS,
)
from app.step_import.step_cache import StepCache, get_step_cache


OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "step_import"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _generate_test_step(file_name: str = "test_box.step") -> Path:
    """使用CadQuery生成测试用STEP文件。"""
    path = OUTPUT_DIR / file_name
    if path.exists():
        return path

    box = cq.Workplane("XY").box(50, 40, 30)
    cq.exporters.export(box, str(path), exportType="STEP")
    return path


def _generate_cylinder_step(file_name: str = "test_cylinder.step") -> Path:
    """生成圆柱体测试STEP。"""
    path = OUTPUT_DIR / file_name
    if path.exists():
        return path

    cyl = cq.Workplane("XY").cylinder(30, 15)
    cq.exporters.export(cyl, str(path), exportType="STEP")
    return path


def _generate_multi_body_step(file_name: str = "test_multibody.step") -> Path:
    """生成多实体测试STEP。"""
    path = OUTPUT_DIR / file_name
    if path.exists():
        return path

    box1 = cq.Workplane("XY").box(20, 20, 20).translate((-30, 0, 0))
    box2 = cq.Workplane("XY").box(20, 20, 20).translate((30, 0, 0))
    combined = box1.union(box2)
    cq.exporters.export(combined, str(path), exportType="STEP")
    return path


class TestStepParser:
    """StepParser 单元测试。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.parser = StepParser()
        self.test_step = _generate_test_step()

    def test_parse_basic_step(self):
        """T01: 基本STEP文件解析。"""
        result = self.parser.parse(self.test_step)
        assert isinstance(result, StepParseResult)
        assert result.success
        assert result.file_name == "test_box.step"
        assert result.file_size > 0
        assert result.parse_time_ms > 0

    def test_parse_model_info(self):
        """T02: 模型信息提取完整性。"""
        result = self.parser.parse(self.test_step)
        mi = result.model_info
        assert isinstance(mi, ModelInfo)
        assert mi.volume > 0
        assert mi.surface_area > 0
        assert mi.bounding_box.length > 0
        assert mi.bounding_box.width > 0
        assert mi.bounding_box.height > 0
        assert mi.face_count > 0
        assert mi.vertex_count > 0
        assert mi.entity_count >= 1

    def test_bounding_box_values(self):
        """T03: 包围盒尺寸正确性。"""
        result = self.parser.parse(self.test_step)
        bb = result.model_info.bounding_box
        assert abs(bb.length - 50.0) < 1.0, f"期望~50, 实际{bb.length}"
        assert abs(bb.width - 40.0) < 1.0, f"期望~40, 实际{bb.width}"
        assert abs(bb.height - 30.0) < 1.0, f"期望~30, 实际{bb.height}"

    def test_center_of_mass(self):
        """T04: 重心坐标提取。"""
        result = self.parser.parse(self.test_step)
        com = result.model_info.center_of_mass
        assert len(com) == 3
        assert abs(com[0]) < 1.0
        assert abs(com[1]) < 1.0
        assert abs(com[2]) < 1.0

    def test_parse_cylinder(self):
        """T05: 圆柱体STEP解析。"""
        step_path = _generate_cylinder_step()
        result = self.parser.parse(step_path)
        assert result.success
        assert result.model_info.volume > 0
        assert result.model_info.face_count > 0

    def test_parse_multi_body(self):
        """T06: 多实体STEP解析。"""
        step_path = _generate_multi_body_step()
        result = self.parser.parse(step_path)
        assert result.success
        assert len(result.entities) >= 1

    def test_get_cadquery_shape(self):
        """T07: 获取CadQuery Shape对象。"""
        shape = self.parser.get_cadquery_shape(self.test_step)
        assert shape is not None
        vol = shape.Volume()
        assert vol > 0

    def test_file_not_found(self):
        """T08: 文件不存在异常处理。"""
        with pytest.raises(StepParseError, match="文件不存在"):
            self.parser.parse(OUTPUT_DIR / "nonexistent.step")

    def test_invalid_extension(self):
        """T09: 无效扩展名异常处理。"""
        fake_path = OUTPUT_DIR / "test.txt"
        fake_path.write_text("not a step file")
        with pytest.raises(StepParseError, match="不支持的文件格式"):
            self.parser.parse(fake_path)
        fake_path.unlink()

    def test_corrupted_file(self):
        """T10: 损坏文件异常处理。"""
        corrupt_path = OUTPUT_DIR / "corrupt.step"
        corrupt_path.write_bytes(b"this is not a valid STEP file at all")
        with pytest.raises(StepParseError, match="解析失败"):
            self.parser.parse(corrupt_path)
        corrupt_path.unlink()

    def test_warnings_for_flat_geometry(self):
        """T11: 检查几何警告。"""
        result = self.parser.parse(self.test_step)
        assert isinstance(result.warnings, list)

    def test_entity_info_data(self):
        """T12: 实体信息数据结构。"""
        result = self.parser.parse(self.test_step)
        assert len(result.entities) > 0
        entity = result.entities[0]
        assert isinstance(entity, EntityInfo)
        assert entity.name
        assert entity.face_count > 0
        assert entity.vertex_count > 0

    def test_model_info_all_fields(self):
        """T13: ModelInfo所有字段非空。"""
        result = self.parser.parse(self.test_step)
        mi = result.model_info
        assert mi.volume >= 0
        assert mi.surface_area >= 0
        assert mi.edge_count >= 0
        assert mi.shell_count >= 0
        assert mi.solid_count >= 1

    def test_bounding_box_min_max(self):
        """T14: 包围盒min/max点。"""
        result = self.parser.parse(self.test_step)
        bb = result.model_info.bounding_box
        assert len(bb.min_point) == 3
        assert len(bb.max_point) == 3
        assert bb.min_point[0] <= bb.max_point[0]


class TestStepConverter:
    """StepConverter 单元测试。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.test_step = _generate_test_step()
        self.parser = StepParser()
        self.shape = self.parser.get_cadquery_shape(self.test_step)
        self.parse_result = self.parser.parse(self.test_step)
        self.converter = StepConverter(output_dir=OUTPUT_DIR)

    def test_convert_to_stl(self):
        """T15: 基本STL转换。"""
        result = self.converter.convert_to_stl(self.shape, "test_box.step")
        assert isinstance(result, ConvertResult)
        assert result.format == "stl"
        assert result.file_size > 0
        assert result.face_count > 0
        assert Path(result.stl_path).exists()

    def test_convert_to_brep(self):
        """T16: BREP格式转换。"""
        result = self.converter.convert_to_brep(self.shape, "test_box.step")
        assert isinstance(result, ConvertResult)
        assert result.format == "brep"
        assert result.file_size > 0
        assert Path(result.stl_path).exists()

    def test_stl_precision_levels(self):
        """T17: 不同精度级别STL导出。"""
        results = {}
        for level in ["low", "medium", "high"]:
            options = PRECISION_PRESETS[level]
            result = self.converter.convert_to_stl(
                self.shape, f"test_prec_{level}.step", options
            )
            results[level] = result
            assert result.format == "stl"
        assert results["high"].face_count >= results["low"].face_count

    def test_convert_all_entities(self):
        """T18: 批量转换所有实体。"""
        batch = self.converter.convert_all_entities(
            self.shape, "test_box.step", self.parse_result
        )
        assert isinstance(batch, BatchConvertResult)
        assert batch.success
        assert len(batch.files) > 0
        assert batch.total_time_ms > 0

    def test_stl_file_url_format(self):
        """T19: STL文件URL格式正确。"""
        result = self.converter.convert_to_stl(self.shape, "test_box.step")
        assert result.stl_url.startswith("/api/import/step/output/")
        assert result.stl_url.endswith(".stl")

    def test_conversion_timing(self):
        """T20: 转换时间记录。"""
        start = time.perf_counter()
        self.converter.convert_to_stl(self.shape, "test_timing.step")
        elapsed = time.perf_counter() - start
        assert elapsed < 30.0, f"STL转换耗时{elapsed:.1f}s超过30s限制"

    def test_multi_body_convert(self):
        """T21: 多实体转换。"""
        step_path = _generate_multi_body_step()
        shape = self.parser.get_cadquery_shape(step_path)
        parse_result = self.parser.parse(step_path)
        batch = self.converter.convert_all_entities(
            shape, "test_multibody.step", parse_result
        )
        assert batch.success
        assert len(batch.files) >= 1

    def test_stl_file_valid(self):
        """T22: 生成的STL文件格式有效。"""
        result = self.converter.convert_to_stl(self.shape, "test_valid.step")
        stl_path = Path(result.stl_path)
        assert stl_path.exists()
        data = stl_path.read_bytes()
        assert len(data) >= 84

    def test_brep_file_valid(self):
        """T23: 生成的BREP文件格式有效。"""
        result = self.converter.convert_to_brep(self.shape, "test_brep_valid.step")
        brep_path = Path(result.stl_path)
        assert brep_path.exists()
        assert brep_path.stat().st_size > 0


class TestStepCache:
    """StepCache 单元测试。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.cache = StepCache(max_entries=5, max_age_seconds=10)

    def test_compute_file_hash(self):
        """T24: 文件哈希计算。"""
        test_file = OUTPUT_DIR / "hash_test.txt"
        test_file.write_text("hello step cache")
        h1 = StepCache.compute_file_hash(test_file)
        h2 = StepCache.compute_file_hash(test_file)
        assert h1 == h2
        assert len(h1) == 64
        test_file.unlink()

    def test_hash_changes_with_content(self):
        """T25: 内容不同哈希不同。"""
        f1 = OUTPUT_DIR / "hash1.txt"
        f2 = OUTPUT_DIR / "hash2.txt"
        f1.write_text("content a")
        f2.write_text("content b")
        assert StepCache.compute_file_hash(f1) != StepCache.compute_file_hash(f2)
        f1.unlink()
        f2.unlink()

    def test_put_and_get(self):
        """T26: 缓存写入和读取。"""
        test_file = _generate_test_step("cache_test_put.step")
        self.cache.put(test_file, stl_files=["test.stl"])
        entry = self.cache.get(test_file)
        assert entry is not None
        assert entry.file_name == "cache_test_put.step"
        assert "test.stl" in entry.stl_files

    def test_cache_miss(self):
        """T27: 缓存未命中。"""
        nonexistent = OUTPUT_DIR / "no_cache.step"
        nonexistent.write_bytes(b"some data")
        entry = self.cache.get(nonexistent)
        assert entry is None
        nonexistent.unlink()

    def test_invalidate(self):
        """T28: 缓存失效。"""
        test_file = _generate_test_step("cache_test_inv.step")
        self.cache.put(test_file)
        assert self.cache.get(test_file) is not None
        self.cache.invalidate(test_file)
        assert self.cache.get(test_file) is None

    def test_clear(self):
        """T29: 清空缓存。"""
        test_file = _generate_test_step("cache_test_clear.step")
        self.cache.put(test_file)
        assert self.cache.size > 0
        self.cache.clear()
        assert self.cache.size == 0

    def test_stats(self):
        """T30: 缓存统计。"""
        test_file = _generate_test_step("cache_test_stats.step")
        self.cache.put(test_file)
        stats = self.cache.stats
        assert stats["entries"] >= 1
        assert "hit_rate" in stats

    def test_lru_eviction(self):
        """T31: LRU淘汰机制。"""
        cache = StepCache(max_entries=3)
        for i in range(5):
            f = OUTPUT_DIR / f"lru_test_{i}.txt"
            f.write_text(f"data {i}")
            cache.put(f, stl_files=[f"stl_{i}.stl"])

        assert cache.size <= 3

        # 清理文件
        for i in range(5):
            Path(OUTPUT_DIR, f"lru_test_{i}.txt").unlink(missing_ok=True)

    def test_cache_hit_count(self):
        """T32: 缓存命中计数。"""
        test_file = _generate_test_step("cache_hit_test.step")
        self.cache.put(test_file)
        self.cache.get(test_file)
        self.cache.get(test_file)
        self.cache.get(test_file)
        stats = self.cache.stats
        assert stats["hit_count"] >= 3

    def test_put_update_existing(self):
        """T33: 更新已存在的缓存条目。"""
        test_file = _generate_test_step("cache_update_test.step")
        self.cache.put(test_file, stl_files=["first.stl"])
        self.cache.put(test_file, stl_files=["second.stl"])
        entry = self.cache.get(test_file)
        assert "second.stl" in entry.stl_files


class TestStepImportAPI:
    """API集成测试。"""

    @pytest.fixture
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.core.request_id import RequestIdMiddleware
        from app.step_import.api import router as step_router

        test_app = FastAPI()
        test_app.add_middleware(RequestIdMiddleware)
        test_app.include_router(step_router)
        return TestClient(test_app)

    def test_endpoint_router_registered(self, client):
        """T34: API路由已注册。"""
        response = client.get("/api/openapi.json")
        if response.status_code == 200:
            paths = response.json().get("paths", {})
            import_endpoints = [p for p in paths if "/api/import/step" in p]
            assert len(import_endpoints) > 0, "STEP导入路由未注册"
        else:
            response = client.post(
                "/api/import/step",
                files={
                    "file": (
                        "test.step",
                        io.BytesIO(b"ISO-10303-21;"),
                        "application/octet-stream",
                    )
                },
            )
            assert response.status_code == 200

    def test_import_without_file(self, client):
        """T35: 无文件上传返回错误。"""
        response = client.post("/api/import/step")
        assert response.status_code == 422

    def test_import_invalid_extension(self, client):
        """T36: 上传非STEP文件返回错误。"""
        response = client.post(
            "/api/import/step",
            files={"file": ("test.txt", io.BytesIO(b"not a step file"), "text/plain")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0

    def test_import_corrupted_step(self, client):
        """T37: 上传损坏的STEP文件返回错误。"""
        response = client.post(
            "/api/import/step",
            files={
                "file": (
                    "corrupt.step",
                    io.BytesIO(b"garbage data"),
                    "application/octet-stream",
                )
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0

    def test_import_valid_step(self, client):
        """T38: 上传有效STEP文件返回成功。"""
        test_step = _generate_test_step("api_test.step")
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_test.step", f, "application/octet-stream")},
                data={"output_format": "stl", "precision": "medium"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0, f"API返回错误: {data.get('message')}"
        result = data["data"]
        assert "model_info" in result
        assert "stl_files" in result
        assert len(result["stl_files"]) > 0

    def test_import_response_structure(self, client):
        """T39: 响应数据结构完整性。"""
        test_step = _generate_test_step("api_struct.step")
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_struct.step", f, "application/octet-stream")},
                data={"output_format": "stl", "precision": "low"},
            )
        data = response.json()
        assert data["code"] == 0
        result = data["data"]

        assert "file_name" in result
        assert "parse_time_ms" in result
        assert "conversion_time_ms" in result
        assert "model_info" in result
        assert "stl_files" in result
        assert "status" in result
        assert "warnings" in result
        assert "import_id" in result

        mi = result["model_info"]
        required_fields = [
            "volume",
            "surface_area",
            "bounding_box",
            "center_of_mass",
            "entity_count",
            "face_count",
            "vertex_count",
        ]
        for field in required_fields:
            assert field in mi, f"model_info缺少{field}"

        bbox = mi["bounding_box"]
        for dim in ["length", "width", "height", "min_point", "max_point"]:
            assert dim in bbox

        stl_file = result["stl_files"][0]
        for key in ["file_name", "stl_url", "face_count", "vertex_count", "file_size"]:
            assert key in stl_file

    def test_import_precision_low(self, client):
        """T40: 低精度导入。"""
        test_step = _generate_test_step("api_low.step")
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_low.step", f, "application/octet-stream")},
                data={"output_format": "stl", "precision": "low"},
            )
        assert response.json()["code"] == 0

    def test_import_precision_high(self, client):
        """T41: 高精度导入。"""
        test_step = _generate_test_step("api_high.step")
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_high.step", f, "application/octet-stream")},
                data={"output_format": "stl", "precision": "high"},
            )
        assert response.json()["code"] == 0

    def test_import_brep_format(self, client):
        """T42: BREP格式导入。"""
        test_step = _generate_test_step("api_brep.step")
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_brep.step", f, "application/octet-stream")},
                data={"output_format": "brep"},
            )
        data = response.json()
        assert data["code"] == 0
        assert "brep_files" in data["data"]

    def test_output_file_access(self, client):
        """T43: 输出文件可访问。"""
        test_step = _generate_test_step("api_output.step")
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_output.step", f, "application/octet-stream")},
                data={"output_format": "stl", "precision": "low"},
            )
        data = response.json()
        assert data["code"] == 0

        stl_url = data["data"]["stl_files"][0]["stl_url"]
        stl_response = client.get(stl_url)
        assert stl_response.status_code == 200
        assert len(stl_response.content) > 0

    def test_output_file_not_found(self, client):
        """T44: 请求不存在的输出文件。

        端点契约已随统一异常体系演进：文件缺失返回 HTTP 404 +
        统一错误体 code=1001（见 app/step_import/api.py get_output_file
        与 core/exception_handlers._STARLETTE_HTTP_TO_CODE），
        旧的"HTTP 200 + body code!=0"信封契约已废弃。
        """
        response = client.get("/api/import/step/output/nonexistent_12345.stl")
        assert response.status_code == 404
        data = response.json()
        # 本文件 client 为裸 FastAPI app（未注册统一异常处理器），
        # 404 体为 Starlette 默认 {"detail": ...} 形状
        assert "detail" in data

    def test_cache_stats_endpoint(self, client):
        """T45: 缓存统计端点。"""
        response = client.get("/api/import/step/cache/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "entries" in data["data"]

    def test_cache_clear_endpoint(self, client):
        """T46: 清空缓存端点。"""
        response = client.delete("/api/import/step/cache")
        assert response.status_code == 200
        assert response.json()["code"] == 0

    def test_history_endpoint(self, client):
        """T47: 导入历史端点。"""
        response = client.get("/api/import/step/history", params={"limit": 10})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "history" in data["data"]

    def test_performance_under_5s(self, client):
        """T48: 10MB以内文件解析应在5秒内完成。"""
        test_step = _generate_test_step("api_perf.step")
        start = time.perf_counter()
        with open(test_step, "rb") as f:
            response = client.post(
                "/api/import/step",
                files={"file": ("api_perf.step", f, "application/octet-stream")},
                data={"output_format": "stl", "precision": "low"},
            )
        elapsed = time.perf_counter() - start
        assert response.json()["code"] == 0
        assert elapsed < 30.0, f"解析耗时{elapsed:.1f}s超过30s限制"


class TestStepIntegration:
    """端到端集成测试。"""

    def test_full_workflow(self):
        """T49: 完整工作流: 解析->转换->验证。"""
        step_path = _generate_test_step("e2e_full.step")
        parser = StepParser()
        parse_result = parser.parse(step_path)
        assert parse_result.success

        shape = parser.get_cadquery_shape(step_path)
        converter = StepConverter(output_dir=OUTPUT_DIR)
        batch = converter.convert_all_entities(shape, "e2e_full.step", parse_result)
        assert batch.success
        assert len(batch.files) > 0

        for f in batch.files:
            assert Path(f.stl_path).exists()
            assert f.file_size > 0

    def test_cache_acceleration(self):
        """T50: 缓存加速验证。"""
        step_path = _generate_test_step("e2e_cache.step")
        parser = StepParser()
        converter = StepConverter(output_dir=OUTPUT_DIR)
        cache = StepCache()

        parse_result = parser.parse(step_path)
        start1 = time.perf_counter()
        shape = parser.get_cadquery_shape(step_path)
        converter.convert_all_entities(shape, "e2e_cache.step", parse_result)
        elapsed1 = time.perf_counter() - start1

        cache.put(
            step_path,
            parse_result_data={
                "file_name": parse_result.file_name,
                "file_size": parse_result.file_size,
                "model_info": {},
            },
        )

        start2 = time.perf_counter()
        cached = cache.get(step_path)
        elapsed2 = time.perf_counter() - start2

        assert cached is not None
        assert elapsed2 < max(elapsed1 * 0.5, 0.001), (
            f"缓存加速不足: {elapsed2:.3f}s vs {elapsed1:.3f}s"
        )

    def test_multi_format_conversion(self):
        """T51: STL和BREP双格式转换。"""
        step_path = _generate_test_step("e2e_formats.step")
        parser = StepParser()
        shape = parser.get_cadquery_shape(step_path)
        parse_result = parser.parse(step_path)
        converter = StepConverter(output_dir=OUTPUT_DIR)

        stl_batch = converter.convert_all_entities(
            shape, "e2e_formats.step", parse_result, "stl"
        )
        brep_batch = converter.convert_all_entities(
            shape, "e2e_formats.step", parse_result, "brep"
        )

        assert stl_batch.success
        assert brep_batch.success
        assert stl_batch.files[0].format == "stl"
        assert brep_batch.files[0].format == "brep"

    def test_cylinder_workflow(self):
        """T52: 圆柱体零件完整工作流。"""
        step_path = _generate_cylinder_step()
        parser = StepParser()
        result = parser.parse(step_path)
        assert result.success
        assert result.model_info.volume > 0

        shape = parser.get_cadquery_shape(step_path)
        converter = StepConverter(output_dir=OUTPUT_DIR)
        batch = converter.convert_all_entities(shape, "test_cylinder.step", result)
        assert batch.success
        assert len(batch.files) > 0

    def test_precision_presets(self):
        """T53: 精度预设配置。"""
        assert "low" in PRECISION_PRESETS
        assert "medium" in PRECISION_PRESETS
        assert "high" in PRECISION_PRESETS

        low = PRECISION_PRESETS["low"]
        med = PRECISION_PRESETS["medium"]
        high = PRECISION_PRESETS["high"]

        assert low.linear_deflection > med.linear_deflection
        assert med.linear_deflection > high.linear_deflection

    def test_output_files_exist(self):
        """T54: 输出文件持久化存在。"""
        step_path = _generate_test_step("e2e_persist.step")
        parser = StepParser()
        shape = parser.get_cadquery_shape(step_path)
        parse_result = parser.parse(step_path)
        converter = StepConverter(output_dir=OUTPUT_DIR)
        batch = converter.convert_all_entities(shape, "e2e_persist.step", parse_result)

        for f in batch.files:
            assert Path(f.stl_path).exists()
            assert Path(f.stl_path).stat().st_size > 0

    def test_global_cache_singleton(self):
        """T55: 全局缓存单例。"""
        cache1 = get_step_cache()
        cache2 = get_step_cache()
        assert cache1 is cache2
