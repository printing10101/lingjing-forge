"""后处理器方言管理 API 测试（P3 后端）。

覆盖：
- GET  /api/v1/postprocessor/dialects — 列表（内置 + 声明镜像）
- GET  /api/v1/postprocessor/dialects/{id} — 详情
- POST /api/v1/postprocessor/dialects/template — 读取模板
- POST /api/v1/postprocessor/dialects/preview — NC 预览

测试方式：mini FastAPI 应用只挂方言路由（不依赖 app.main，避免 cadquery 等
环境依赖），并用 LNN_PERMISSION_ENFORCED=false 绕过权限检查（require_permission
在权限强制关闭时放行，与生产语义一致）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import postprocessor_dialects

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PLUGIN_ROOT = REPO_ROOT / "postprocessor-plugins"


@pytest.fixture
def client(monkeypatch):
    """mini FastAPI 应用 + 关闭权限强制。"""
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    app = FastAPI()
    app.include_router(postprocessor_dialects.router)
    with TestClient(app) as c:
        yield c


@pytest.mark.api
@pytest.mark.postprocessor
class TestListDialects:
    def test_list_returns_declared_and_builtin(self, client):
        resp = client.get("/api/v1/postprocessor/dialects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        ids = {d["id"] for d in data["dialects"]}

        # 内置方言存在
        assert "fanuc_0i" in ids
        assert "siemens_840d" in ids
        assert "heidenhain_tnc" in ids
        assert "xmachine_xm100" in ids

        # 声明镜像存在（postprocessor-plugins/ 下）
        assert "knd_1000_2000_3000" in ids
        assert "gsk_980_25i" in ids

    def test_list_declared_count(self, client):
        resp = client.get("/api/v1/postprocessor/dialects")
        data = resp.json()["data"]
        assert data["declared"] >= 5  # KND/GSK/HNC/Mitsubishi/Fagor


@pytest.mark.api
@pytest.mark.postprocessor
class TestGetDialectDetail:
    def test_detail_declared_dialect(self, client):
        resp = client.get("/api/v1/postprocessor/dialects/knd_1000_2000_3000")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["id"] == "knd_1000_2000_3000"
        assert data["is_declared"] is True
        assert data["compile_ok"] is True
        assert "format_header" in data["templates"]

    def test_detail_builtin_dialect(self, client):
        resp = client.get("/api/v1/postprocessor/dialects/fanuc_0i")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["id"] == "fanuc_0i"
        assert data["is_declared"] is False

    def test_detail_unknown_dialect_404(self, client):
        resp = client.get("/api/v1/postprocessor/dialects/nonexistent_xyz")
        assert resp.status_code == 200  # 统一响应，错误在 body
        body = resp.json()
        assert body["code"] != 0
        assert "不存在" in body["message"]


@pytest.mark.api
@pytest.mark.postprocessor
class TestReadTemplate:
    def test_read_existing_template(self, client):
        resp = client.post(
            "/api/v1/postprocessor/dialects/template",
            json={"dialect_id": "knd_1000_2000_3000", "method": "format_header"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["method"] == "format_header"
        assert "O{{" in data["content"]  # Jinja2 模板内容

    def test_read_undeclared_method(self, client):
        # KND 未声明 format_footer（继承基类） 404
        resp = client.post(
            "/api/v1/postprocessor/dialects/template",
            json={"dialect_id": "knd_1000_2000_3000", "method": "format_footer"},
        )
        body = resp.json()
        assert body["code"] != 0
        assert "未声明模板方法" in body["message"]

    def test_read_invalid_method(self, client):
        resp = client.post(
            "/api/v1/postprocessor/dialects/template",
            json={"dialect_id": "knd_1000_2000_3000", "method": "format_evil"},
        )
        body = resp.json()
        assert body["code"] != 0
        assert "白名单" in body["message"]


@pytest.mark.api
@pytest.mark.postprocessor
class TestPreviewDialect:
    def test_preview_declared_dialect(self, client):
        resp = client.post(
            "/api/v1/postprocessor/dialects/preview",
            json={"dialect_id": "knd_1000_2000_3000", "program_number": 2000},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert "O2000" in data["output"]
        assert "M30" in data["output"]

    def test_preview_builtin_dialect(self, client):
        resp = client.post(
            "/api/v1/postprocessor/dialects/preview",
            json={"dialect_id": "fanuc_0i"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "Fanuc" in data["output"]

    def test_preview_unknown_dialect(self, client):
        resp = client.post(
            "/api/v1/postprocessor/dialects/preview",
            json={"dialect_id": "nonexistent_xyz"},
        )
        body = resp.json()
        assert body["code"] != 0
        assert "无法解析" in body["message"]

    def test_preview_gsk_specific_output(self, client):
        # GSK 声明镜像应输出 G30 回零（声明化行为正确）
        resp = client.post(
            "/api/v1/postprocessor/dialects/preview",
            json={"dialect_id": "gsk_980_25i"},
        )
        data = resp.json()["data"]
        assert "G30 X0. Y0." in data["output"]

    def test_preview_fagor_specific_output(self, client):
        # Fagor 声明镜像应输出 %xxxxx 程序号 + G75 回零
        resp = client.post(
            "/api/v1/postprocessor/dialects/preview",
            json={"dialect_id": "fagor_8055", "program_number": 1000},
        )
        data = resp.json()["data"]
        assert "%01000" in data["output"]
        assert "G75 Z0." in data["output"]


# 写路径：新建 / 保存模板 / 删除（工艺员自由度闭环）
# 使用 tmp_path + patch _default_plugin_root，不污染真实插件目录


@pytest.mark.api
@pytest.mark.postprocessor
class TestDialectWritePath:
    @pytest.fixture
    def write_client(self, monkeypatch, tmp_path):
        """mini-app + 权限关闭 + 插件根目录指向 tmp_path。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
        from unittest.mock import patch

        app = FastAPI()
        app.include_router(postprocessor_dialects.router)
        with patch.object(postprocessor_dialects, "_default_plugin_root", return_value=tmp_path):
            with TestClient(app) as c:
                c._plugin_root = tmp_path
                yield c

    def test_create_dialect_full_flow(self, write_client):
        resp = write_client.post(
            "/api/v1/postprocessor/dialects",
            json={
                "id": "test_fanuc",
                "name": "Test Fanuc",
                "extends": "fanuc_0i",
                "description": "test dialect",
                "author": "tester",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0

        plugin_root = write_client._plugin_root
        dialect_dir = plugin_root / "test_fanuc"
        assert (dialect_dir / "dialect.yaml").exists()
        assert (dialect_dir / "templates" / "header.j2").exists()
        assert (dialect_dir / "templates" / "footer.j2").exists()

        # 新建的 dialect.yaml 声明了骨架模板
        from app.postprocessor.dialect.declaration import DialectDeclaration

        decl = DialectDeclaration.from_yaml(dialect_dir / "dialect.yaml")
        assert "format_header" in decl.templates
        assert "format_footer" in decl.templates

    def test_create_then_read_save_template(self, write_client):
        write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "editable", "name": "Editable", "extends": "fanuc_0i"},
        )
        # 读取骨架 header
        read = write_client.post(
            "/api/v1/postprocessor/dialects/template",
            json={"dialect_id": "editable", "method": "format_header"},
        )
        assert read.json()["code"] == 0
        content = read.json()["data"]["content"]
        # 骨架 header 已参数化（含 program_number 变量引用）
        assert "program_number" in content
        assert "M08" in content

        # 保存修改后的模板
        new_content = content + "\n(EDITED)\n"
        save = write_client.put(
            "/api/v1/postprocessor/dialects/editable/template",
            json={
                "dialect_id": "editable",
                "method": "format_header",
                "content": new_content,
            },
        )
        assert save.json()["code"] == 0

        # 读回验证
        read2 = write_client.post(
            "/api/v1/postprocessor/dialects/template",
            json={"dialect_id": "editable", "method": "format_header"},
        )
        assert "(EDITED)" in read2.json()["data"]["content"]

    def test_create_dialect_compiles_and_previews(self, write_client):
        write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "compilable", "name": "Compilable", "extends": "fanuc_0i"},
        )
        # 新建方言应能预览（编译成功）
        preview = write_client.post(
            "/api/v1/postprocessor/dialects/preview",
            json={"dialect_id": "compilable", "program_number": 3000},
        )
        assert preview.json()["code"] == 0
        assert "O3000" in preview.json()["data"]["output"]

    def test_security_invalid_extends(self, write_client):
        resp = write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "bad_ext", "name": "X", "extends": "nonexistent"},
        )
        assert resp.json()["code"] == 1002  # INVALID_REQUEST

    def test_security_duplicate_create(self, write_client):
        write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "dup", "name": "Dup", "extends": "fanuc_0i"},
        )
        resp = write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "dup", "name": "Dup2", "extends": "fanuc_0i"},
        )
        assert resp.json()["code"] == 1002

    def test_security_save_undeclared_method(self, write_client):
        write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "limited", "name": "Limited", "extends": "fanuc_0i"},
        )
        resp = write_client.put(
            "/api/v1/postprocessor/dialects/limited/template",
            json={"dialect_id": "limited", "method": "format_arc", "content": "x"},
        )
        assert resp.json()["code"] == 1002  # 未声明方法不可保存

    def test_security_path_traversal_rejected(self, write_client):
        # Pydantic pattern 校验拦截目录穿越 id
        resp = write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "../../evil", "name": "X", "extends": "fanuc_0i"},
        )
        assert resp.status_code == 422

    def test_delete_builtin_rejected(self, write_client):
        resp = write_client.delete("/api/v1/postprocessor/dialects/fanuc_0i")
        assert resp.json()["code"] == 1002  # 内置不可删

    def test_delete_flow(self, write_client):
        write_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "removable", "name": "Removable", "extends": "fanuc_0i"},
        )
        assert (write_client._plugin_root / "removable").exists()

        resp = write_client.delete("/api/v1/postprocessor/dialects/removable")
        assert resp.json()["code"] == 0
        assert not (write_client._plugin_root / "removable").exists()

    def test_delete_missing_404(self, write_client):
        resp = write_client.delete("/api/v1/postprocessor/dialects/nope")
        assert resp.json()["code"] == 1001  # NOT_FOUND


@pytest.mark.api
@pytest.mark.postprocessor
class TestDialectParams:
    """方言参数读写（遗留项⑤）。"""

    @pytest.fixture
    def param_client(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
        from unittest.mock import patch

        app = FastAPI()
        app.include_router(postprocessor_dialects.router)
        with patch.object(postprocessor_dialects, "_default_plugin_root", return_value=tmp_path):
            with TestClient(app) as c:
                c._plugin_root = tmp_path
                yield c

    def test_get_params_returns_effective_and_dialect(self, param_client):
        param_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "params_dialect", "name": "Params", "extends": "fanuc_0i"},
        )
        resp = param_client.get("/api/v1/postprocessor/dialects/params_dialect/params")
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        data = resp.json()["data"]
        # 有效配置包含 base 展开的键（safe_z_height/spindle 等）
        assert "safe_z_height" in data["effective"]
        assert "spindle" in data["effective"]
        # 方言自己的 params 初始为空
        assert data["dialect_params"] == {}

    def test_save_params_then_effective_reflects(self, param_client):
        param_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "params2", "name": "Params2", "extends": "fanuc_0i"},
        )
        save = param_client.put(
            "/api/v1/postprocessor/dialects/params2/params",
            json={
                "dialect_id": "params2",
                "params": {"safe_z_height": 120.0, "spindle": {"max_rpm": 30000}},
            },
        )
        assert save.json()["code"] == 0

        get = param_client.get("/api/v1/postprocessor/dialects/params2/params")
        data = get.json()["data"]
        assert data["dialect_params"]["safe_z_height"] == 120.0
        # 有效配置反映覆盖
        assert data["effective"]["safe_z_height"] == 120.0
        assert data["effective"]["spindle"]["max_rpm"] == 30000

    def test_saved_params_affect_compiled_instance(self, param_client):
        """保存 params 后，编译方言实例应反映参数（标量提升 + config 合并）。"""
        param_client.post(
            "/api/v1/postprocessor/dialects",
            json={"id": "params3", "name": "Params3", "extends": "fanuc_0i"},
        )
        param_client.put(
            "/api/v1/postprocessor/dialects/params3/params",
            json={"dialect_id": "params3", "params": {"safe_z_height": 100.0}},
        )

        from app.postprocessor.dialect import DialectCompiler, DialectDeclaration

        decl = DialectDeclaration.from_yaml(param_client._plugin_root / "params3" / "dialect.yaml")
        cls = DialectCompiler().compile(decl)
        pp = cls()
        assert pp.safe_z_height == 100.0

    def test_save_params_unknown_dialect(self, param_client):
        resp = param_client.put(
            "/api/v1/postprocessor/dialects/nope/params",
            json={"dialect_id": "nope", "params": {}},
        )
        assert resp.json()["code"] == 1001  # NOT_FOUND
