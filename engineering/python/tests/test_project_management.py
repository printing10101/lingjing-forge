"""工程文件管理系统 单元测试。

覆盖:
- ProjectMetadata 创建与更新
- ResourceEntry 验证
- ProjectManifest 序列化/反序列化
- ProjectStore 创建/打开/保存/另存为
- 版本兼容性检查
- 资源文件管理
- API端点请求/响应
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.projects.project_store import (
    ProjectStore,
    ProjectMetadata,
    ResourceEntry,
    ProjectManifest,
    PROJECT_FILE_EXTENSION,
    PROJECT_FORMAT_VERSION as CURRENT_VERSION,
)


class TestProjectMetadata:
    def test_default_creation(self):
        m = ProjectMetadata()
        assert m.name == "未命名工程"
        assert m.created_at
        assert m.modified_at

    def test_custom_creation(self):
        m = ProjectMetadata(
            name="铣削加工-001",
            author="张三",
            description="测试工程",
        )
        assert m.name == "铣削加工-001"
        assert m.author == "张三"
        assert m.description == "测试工程"

    def test_touch_updates_modified_at(self):
        m = ProjectMetadata()
        old = m.modified_at
        import time

        time.sleep(0.002)
        m.touch()
        assert m.modified_at != old


class TestResourceEntry:
    def test_default_creation(self):
        r = ResourceEntry(type="model", path="models/test.stl")
        assert r.id
        assert r.type == "model"
        assert r.path == "models/test.stl"
        assert r.added_at

    def test_validation_valid(self):
        r = ResourceEntry(type="drawing", path="drawings/blueprint.dxf")
        assert r.validate()

    def test_validation_invalid_type(self):
        r = ResourceEntry(type="invalid", path="test.txt")
        assert not r.validate()

    def test_validation_empty_path(self):
        r = ResourceEntry(type="model", path="")
        assert not r.validate()

    def test_auto_id(self):
        r1 = ResourceEntry(type="model", path="a.stl")
        r2 = ResourceEntry(type="model", path="b.stl")
        assert r1.id != r2.id
        assert len(r1.id) == 12


class TestProjectManifest:
    def test_create_default(self):
        m = ProjectManifest()
        assert m.version == CURRENT_VERSION
        assert m.metadata is None
        assert m.resources == []
        assert m.data == {}
        assert m.extensions == {}

    def test_to_dict(self):
        m = ProjectManifest(
            metadata=ProjectMetadata(name="测试工程"),
            resources=[
                ResourceEntry(
                    type="model", path="models/stock.stl", original_name="stock.stl"
                )
            ],
            data={"stock_definition": {"length": 100}},
            extensions={"custom_plugin": {"enabled": True}},
        )
        d = m.to_dict()
        assert d["version"] == CURRENT_VERSION
        assert d["metadata"]["name"] == "测试工程"
        assert len(d["resources"]) == 1
        assert d["data"]["stock_definition"]["length"] == 100
        assert d["extensions"]["custom_plugin"]["enabled"]

    def test_from_dict(self):
        d = {
            "version": "1.0",
            "metadata": {
                "name": "打开测试",
                "created_at": "2026-01-01T00:00:00+00:00",
                "modified_at": "2026-01-02T00:00:00+00:00",
                "author": "李四",
                "description": "测试打开",
            },
            "resources": [
                {
                    "id": "abc123",
                    "type": "toolpath",
                    "path": "toolpaths/program.nc",
                    "original_name": "program.nc",
                    "mime_type": "text/x-gcode",
                    "added_at": "2026-01-01T00:00:00+00:00",
                    "metadata": {},
                }
            ],
            "data": {
                "stock_definition": {"length": 200, "width": 150},
                "tool_selection": [{"name": "T1", "diameter": 10}],
                "process_steps": [],
                "toolpath_config": {},
                "postprocessor_config": {},
                "simulation_config": {},
            },
            "extensions": {},
        }
        m = ProjectManifest.from_dict(d)
        assert m.version == "1.0"
        assert m.metadata.name == "打开测试"
        assert m.metadata.author == "李四"
        assert len(m.resources) == 1
        assert m.resources[0].id == "abc123"
        assert m.resources[0].type == "toolpath"
        assert m.data["stock_definition"]["length"] == 200

    def test_add_resource(self):
        m = ProjectManifest()
        r = m.add_resource("model", "models/new.stl", "new.stl", "application/sla")
        assert r.type == "model"
        assert r.original_name == "new.stl"
        assert len(m.resources) == 1

    def test_find_resource(self):
        m = ProjectManifest()
        r = m.add_resource("model", "models/a.stl")
        found = m.find_resource(r.id)
        assert found is not None
        assert found.id == r.id

    def test_find_resource_not_found(self):
        m = ProjectManifest()
        assert m.find_resource("nonexistent") is None

    def test_remove_resource(self):
        m = ProjectManifest()
        r = m.add_resource("model", "models/a.stl")
        assert m.remove_resource(r.id)
        assert len(m.resources) == 0
        assert not m.remove_resource("nonexistent")

    def test_get_resources_by_type(self):
        m = ProjectManifest()
        m.add_resource("model", "models/a.stl")
        m.add_resource("model", "models/b.stl")
        m.add_resource("toolpath", "toolpaths/prog.nc")
        models = m.get_resources_by_type("model")
        assert len(models) == 2
        toolpaths = m.get_resources_by_type("toolpath")
        assert len(toolpaths) == 1

    def test_data_default_structure(self):
        m = ProjectManifest()
        m.data = {
            "stock_definition": {},
            "tool_selection": [],
            "process_steps": [],
            "toolpath_config": {},
            "postprocessor_config": {},
            "simulation_config": {},
        }
        d = m.to_dict()
        assert "stock_definition" in d["data"]

    def test_extensions_field(self):
        m = ProjectManifest()
        m.extensions = {
            "simulation_result": {"task_id": "sim_001"},
            "lnn_model": {"name": "CFC-Fast"},
        }
        d = m.to_dict()
        assert "extensions" in d
        assert d["extensions"]["lnn_model"]["name"] == "CFC-Fast"


class TestProjectStore:
    def test_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            ProjectStore(tmp)
            assert Path(tmp).exists()

    def test_create_project(self):
        store = ProjectStore()
        manifest = store.create_project(
            name="测试工程-001",
            author="王五",
            description="单元测试工程",
        )
        assert manifest.version == CURRENT_VERSION
        assert manifest.metadata.name == "测试工程-001"
        assert manifest.metadata.author == "王五"
        assert "stock_definition" in manifest.data

    def test_save_and_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            manifest = store.create_project(name="保存测试")

            manifest.add_resource("model", "models/stock.stl", "stock.stl")
            manifest.data["stock_definition"] = {
                "length": 100,
                "width": 80,
                "height": 30,
            }

            output = Path(tmp) / "save_test.vrm"
            save_path = store.save_project(manifest, output)
            assert Path(save_path).exists()
            assert Path(save_path).suffix == PROJECT_FILE_EXTENSION

            loaded = store.open_project(save_path)
            assert loaded.version == CURRENT_VERSION
            assert loaded.metadata.name == "保存测试"
            assert loaded.data["stock_definition"]["length"] == 100
            assert len(loaded.resources) == 1
            assert loaded.resources[0].original_name == "stock.stl"

    def test_save_as_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            manifest = store.create_project(name="原始工程")

            output1 = Path(tmp) / "original.vrm"
            store.save_project(manifest, output1)

            output2 = Path(tmp) / "copy.vrm"
            store.save_as_project(manifest, output2)

            assert output1.exists()
            assert output2.exists()
            assert output1.stat().st_size > 0
            assert output2.stat().st_size > 0

    def test_open_invalid_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            invalid = Path(tmp) / "not_a_project.txt"
            invalid.write_text("not a project file")
            with pytest.raises(ValueError):
                store.open_project(invalid)

    def test_open_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            with pytest.raises(FileNotFoundError):
                store.open_project(Path(tmp) / "nonexistent.vrm")

    def test_version_validation_compatible(self):
        store = ProjectStore()
        store._validate_version("1.0")
        store._validate_version("1.5")

    def test_version_validation_incompatible(self):
        store = ProjectStore()
        with pytest.raises(ValueError, match="版本"):
            store._validate_version("2.0")

    def test_version_validation_empty(self):
        store = ProjectStore()
        with pytest.raises(ValueError, match="version"):
            store._validate_version("")

    def test_invalid_version_format(self):
        store = ProjectStore()
        with pytest.raises(ValueError, match="无效"):
            store._validate_version("not-a-version")

    def test_add_resource_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            manifest = store.create_project(name="资源测试")

            src = Path(tmp) / "test_model.stl"
            src.write_bytes(b"fake stl data")

            entry = store.add_resource_file(manifest, "model", src)
            assert entry.id
            assert entry.type == "model"
            assert "models" in entry.path

    def test_add_resource_file_invalid_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            manifest = store.create_project()
            with pytest.raises(ValueError, match="不支持的资源类型"):
                store.add_resource_file(manifest, "unknown", Path("test.txt"))

    def test_list_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            m1 = store.create_project(name="工程A")
            store.save_project(m1, Path(tmp) / "proj_a.vrm")
            m2 = store.create_project(name="工程B")
            store.save_project(m2, Path(tmp) / "proj_b.vrm")

            projects = store.list_projects()
            assert len(projects) >= 2

    def test_delete_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            manifest = store.create_project(name="待删除")
            path = Path(tmp) / "to_delete.vrm"
            store.save_project(manifest, path)
            assert path.exists() or (path.parent / (path.name + ".vrm")).exists()
            path if path.exists() else (path.parent / (path.name + ".vrm"))
            store.delete_project(path)
            assert not path.exists()

    def test_delete_corrupted_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectStore(tmp)
            corrupted = Path(tmp) / "corrupted.vrm"
            corrupted.write_bytes(b"not a valid zip")
            assert corrupted.exists()

            store.delete_project(corrupted)
            assert not corrupted.exists()

    def test_project_manifest_roundtrip(self):
        """完整数据往返测试: create -> to_dict -> from_dict -> to_dict"""
        original = ProjectManifest(
            metadata=ProjectMetadata(name="完整测试", author="测试者"),
            resources=[
                ResourceEntry(type="model", path="models/a.stl", original_name="a.stl"),
                ResourceEntry(
                    type="drawing", path="drawings/b.dxf", original_name="b.dxf"
                ),
            ],
            data={
                "stock_definition": {"length": 200, "width": 150, "height": 50},
                "tool_selection": [
                    {"name": "T1", "diameter": 10, "type": "flat"},
                    {"name": "T2", "diameter": 6, "type": "ball"},
                ],
                "process_steps": [
                    {"name": "粗加工", "tool": "T1", "depth": 5},
                    {"name": "精加工", "tool": "T2", "depth": 0.5},
                ],
                "toolpath_config": {},
                "postprocessor_config": {"format": "fanuc", "output_path": "output.nc"},
                "simulation_config": {"voxel_size": 1.0},
            },
            extensions={
                "lnn_model": {"name": "CFC-Fast", "version": "2.1"},
                "wear_prediction": {"enabled": True},
                "simulation_result": {"task_id": "sim_001", "collision": False},
            },
        )

        d1 = original.to_dict()
        restored = ProjectManifest.from_dict(d1)
        d2 = restored.to_dict()

        assert d1["version"] == d2["version"]
        assert d1["metadata"]["name"] == d2["metadata"]["name"]
        assert len(d1["resources"]) == len(d2["resources"])
        assert (
            d1["data"]["stock_definition"]["length"]
            == d2["data"]["stock_definition"]["length"]
        )
        assert len(d1["data"]["tool_selection"]) == len(d2["data"]["tool_selection"])
        assert len(d1["data"]["process_steps"]) == len(d2["data"]["process_steps"])
        assert (
            d1["extensions"]["lnn_model"]["name"]
            == d2["extensions"]["lnn_model"]["name"]
        )
