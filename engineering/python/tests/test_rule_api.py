"""
工艺规则 API 测试

测试所有 RESTful 端点的正确性
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.database.rule_db as rule_db_module  # noqa: E402
import app.rules.api as rules_api  # noqa: E402
from app.api.v1.auth import get_current_user  # noqa: E402
from app.database.rule_db import RuleDatabase  # noqa: E402


@pytest.fixture(autouse=True)
def setup_temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    temp_db = RuleDatabase(path)
    original_global = rule_db_module._global_db
    rule_db_module._global_db = temp_db
    yield temp_db
    rule_db_module._global_db = original_global
    temp_db.close()
    # 等待文件句柄完全释放后再删除（Windows 下 close_all 异步关闭）
    for _ in range(5):
        try:
            if os.path.exists(path):
                os.unlink(path)
            break
        except PermissionError:
            import time
            time.sleep(0.1)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rules_api.router)

    # 测试环境绕过认证：override get_current_user 和 require_permission 返回的 checker
    # 背景：app/rules/api.py 在后续版本为所有路由添加了认证依赖，但测试不提供 token。
    # get_current_user 可直接通过 dependency_overrides 覆盖；
    # require_permission 返回的 checker 是闭包，每次调用都是新对象，无法通过模块级
    # callable 覆盖，因此遍历路由的 dependant.dependencies 找到所有 checker 函数逐一覆盖。
    async def _mock_current_user():
        return {"username": "test_user", "role": "admin"}

    app.dependency_overrides[get_current_user] = _mock_current_user

    async def _mock_checker(request: Request):
        # require_permission 的 checker 期望 request.state.username 已设置
        request.state.username = "test_user"

    # 遍历所有路由，覆盖所有名为 "checker" 的依赖（require_permission 返回的闭包）
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        for dep in dependant.dependencies:
            if getattr(dep.call, "__name__", "") == "checker":
                app.dependency_overrides[dep.call] = _mock_checker

    return TestClient(app)


@pytest.fixture
def sample_rule_data():
    return {
        "name": "45钢粗铣切深限制",
        "description": "45钢粗铣加工时切深不超过2mm",
        "conditions": [
            {"parameter": "材料", "operator": "=", "value": "45钢"},
            {"parameter": "工序", "operator": "=", "value": "粗铣"},
        ],
        "logic_operator": "AND",
        "result": {"parameter": "切深", "operator": "<=", "value": "2", "unit": "mm"},
        "status": "active",
        "priority": 10,
    }


@pytest.fixture
def sample_group_data():
    return {
        "name": "铣削规则",
        "description": "铣削加工相关规则",
    }


class TestRuleCrudApi:
    def test_create_rule(self, client, sample_rule_data):
        response = client.post("/api/rules/create", json=sample_rule_data)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "45钢粗铣切深限制"
        assert data["data"]["id"] is not None

    def test_create_rule_validation_error(self, client):
        response = client.post(
            "/api/rules/create",
            json={
                "name": "test",
                "conditions": [],
                "result": {"parameter": "", "operator": "=", "value": ""},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002

    def test_create_rule_invalid_operator(self, client):
        response = client.post(
            "/api/rules/create",
            json={
                "name": "test",
                "conditions": [
                    {"parameter": "材料", "operator": "~~", "value": "45钢"}
                ],
                "result": {"parameter": "切深", "operator": "<=", "value": "2"},
            },
        )
        data = response.json()
        assert data["code"] == 1002

    def test_list_rules(self, client, sample_rule_data):
        client.post("/api/rules/create", json=sample_rule_data)
        response = client.get("/api/rules/list")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["total"] == 1
        assert len(data["data"]["rules"]) == 1

    def test_get_rule_detail(self, client, sample_rule_data):
        create_resp = client.post("/api/rules/create", json=sample_rule_data)
        rule_id = create_resp.json()["data"]["id"]

        response = client.get(f"/api/rules/detail/{rule_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["name"] == "45钢粗铣切深限制"

    def test_get_rule_not_found(self, client):
        response = client.get("/api/rules/detail/99999")
        data = response.json()
        assert data["code"] == 1001

    def test_update_rule(self, client, sample_rule_data):
        create_resp = client.post("/api/rules/create", json=sample_rule_data)
        rule_id = create_resp.json()["data"]["id"]

        response = client.put(
            f"/api/rules/update/{rule_id}",
            json={
                "name": "更新名称",
                "priority": 20,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["name"] == "更新名称"
        assert data["data"]["priority"] == 20

    def test_delete_rule(self, client, sample_rule_data):
        create_resp = client.post("/api/rules/create", json=sample_rule_data)
        rule_id = create_resp.json()["data"]["id"]

        response = client.delete(f"/api/rules/delete/{rule_id}")
        assert response.status_code == 200

        response = client.get(f"/api/rules/detail/{rule_id}")
        assert response.json()["code"] == 1001

    def test_filter_rules_by_status(self, client):
        for i in range(5):
            client.post(
                "/api/rules/create",
                json={
                    "name": f"规则{i}",
                    "conditions": [
                        {"parameter": "材料", "operator": "=", "value": "45钢"}
                    ],
                    "result": {"parameter": "切深", "operator": "<=", "value": "2"},
                    "status": "active" if i < 3 else "draft",
                },
            )

        response = client.get("/api/rules/list?status=active")
        assert response.json()["data"]["total"] == 3

        response = client.get("/api/rules/list?status=draft")
        assert response.json()["data"]["total"] == 2

    def test_search_rules_by_keyword(self, client):
        client.post(
            "/api/rules/create",
            json={
                "name": "45钢规则",
                "description": "这是45钢的加工规则",
                "conditions": [{"parameter": "材料", "operator": "=", "value": "45钢"}],
                "result": {"parameter": "切深", "operator": "<=", "value": "2"},
            },
        )
        client.post(
            "/api/rules/create",
            json={
                "name": "铝合金规则",
                "conditions": [{"parameter": "材料", "operator": "=", "value": "6061"}],
                "result": {"parameter": "切深", "operator": "<=", "value": "3"},
            },
        )

        response = client.get("/api/rules/list?keyword=45钢")
        assert response.json()["data"]["total"] == 1


class TestGroupApi:
    def test_create_group(self, client, sample_group_data):
        response = client.post("/api/rules/groups/create", json=sample_group_data)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["name"] == "铣削规则"

    def test_create_duplicate_group(self, client, sample_group_data):
        client.post("/api/rules/groups/create", json=sample_group_data)
        response = client.post("/api/rules/groups/create", json=sample_group_data)
        data = response.json()
        assert data["code"] == 1002

    def test_list_groups(self, client, sample_group_data):
        client.post("/api/rules/groups/create", json=sample_group_data)
        response = client.get("/api/rules/groups/list")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["total"] == 1

    def test_update_group(self, client, sample_group_data):
        create_resp = client.post("/api/rules/groups/create", json=sample_group_data)
        group_id = create_resp.json()["data"]["id"]

        response = client.put(
            f"/api/rules/groups/update/{group_id}",
            json={
                "name": "更新分组",
            },
        )
        assert response.status_code == 200
        assert response.json()["data"]["name"] == "更新分组"

    def test_delete_group_with_rules(self, client, sample_group_data, sample_rule_data):
        create_resp = client.post("/api/rules/groups/create", json=sample_group_data)
        group_id = create_resp.json()["data"]["id"]

        sample_rule_data["group_id"] = group_id
        client.post("/api/rules/create", json=sample_rule_data)

        response = client.delete(f"/api/rules/groups/delete/{group_id}")
        data = response.json()
        assert data["code"] == 1002

    def test_delete_empty_group(self, client, sample_group_data):
        create_resp = client.post("/api/rules/groups/create", json=sample_group_data)
        group_id = create_resp.json()["data"]["id"]

        response = client.delete(f"/api/rules/groups/delete/{group_id}")
        assert response.status_code == 200


class TestImportExportApi:
    def test_export_rules(self, client, sample_rule_data):
        client.post("/api/rules/create", json=sample_rule_data)
        response = client.get("/api/rules/export")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        data = json.loads(response.content)
        assert data["total_rules"] == 1

    def test_import_rules(self, client):
        # 导入文件版本需与当前项目版本（2.7.0）主版本号匹配，否则触发版本不兼容
        from app.database.rule_db import get_project_version

        project_version = get_project_version()
        export_data = {
            "version": project_version,
            "groups": [{"name": "导入分组", "description": ""}],
            "rules": [
                {
                    "name": "导入规则",
                    "description": "",
                    "conditions": [
                        {"parameter": "材料", "operator": "=", "value": "45钢"}
                    ],
                    "logic_operator": "AND",
                    "result": {"parameter": "切深", "operator": "<=", "value": "2"},
                    "status": "active",
                    "priority": 0,
                }
            ],
        }

        response = client.post(
            "/api/rules/import",
            files={"file": ("rules.json", json.dumps(export_data), "application/json")},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["imported_rules"] == 1

    def test_import_invalid_json(self, client):
        # 使用 .json 扩展名通过 validate_upload 的扩展名检查，
        # 但内容为无效 JSON，触发 json.JSONDecodeError 返回 code 1002
        response = client.post(
            "/api/rules/import",
            files={"file": ("test.json", "not json", "application/json")},
        )
        data = response.json()
        assert data["code"] == 1002


class TestBackupApi:
    def test_backup_database(self, client):
        response = client.post("/api/rules/backup")
        assert response.status_code == 200
        data = response.json()
        assert "backup_path" in data["data"]


class TestStatsApi:
    def test_get_stats(self, client, sample_rule_data):
        for i in range(5):
            sample_rule_data["name"] = f"规则{i}"
            sample_rule_data["status"] = "active" if i < 3 else "draft"
            client.post("/api/rules/create", json=sample_rule_data)

        response = client.get("/api/rules/stats")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total_rules"] == 5
        assert data["active_rules"] == 3
        assert data["draft_rules"] == 2


class TestPreviewApi:
    def test_preview_rule_text(self, client):
        conditions = json.dumps(
            [
                {"parameter": "材料", "operator": "=", "value": "45钢"},
                {"parameter": "工序", "operator": "=", "value": "粗铣"},
            ]
        )
        result = json.dumps({"parameter": "切深", "operator": "<=", "value": "2"})

        response = client.get(
            "/api/rules/preview",
            params={"conditions": conditions, "result": result},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert "IF" in data["preview_text"]
        assert "材料 = 45钢" in data["preview_text"]
        assert "THEN" in data["preview_text"]
