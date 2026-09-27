"""Cutting Experience 采集 API 集成测试（P2-3 数据飞轮闭环）。

真实链路（无业务 mock）：
临时 SQLite（``DB_URL``）+ 临时用户存储 → HTTP 注册（自助注册用户默认
拥有全部功能权限码，见 ``app/auth/permissions.py`` 的 ``_SELF_SERVICE_ROLES``
设计）→ HTTP 登录获取真实 JWT → RBAC 鉴权下的采集 / 批量 / 查询 / 统计 /
详情 / 删除全流程。

历史教训：本文件早期版本大量使用 ``assert status in [200, 400, 503]`` 式
容忍断言——几乎永不失败，无法区分「路由故障」与「DB 故障」，且当时
目标路由（旧 ``experience_routes.py``，前缀 ``/api/cutting/experience``）
与真实消费方（前端 ``@/api/cuttingExperience``，``/api/v1/experience``）
不一致，测试实际没有覆盖任何被前端调用的端点。现全部改为精确断言，
并对接线后的 ``app/api/v1/cutting_experience/routes.py``。
"""

from __future__ import annotations

import os

os.environ.setdefault("LNN_ENV", "dev")
os.environ.setdefault("LNN_JWT_SECRET", "test-secret-key-32-characters-long!!!")
# 注册端点：配置非空邀请码时进入邀请码模式；测试固定码并在注册时携带
os.environ.setdefault("LNN_REGISTRATION_CODE", "exp-it-invite-code")

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

BASE = "/api/v1/experience"
USERNAME = "exp_it_user"
PASSWORD = "ExpIt#2026"


def _record(**overrides) -> dict:
    """构造合法的 CuttingExperience 请求体（契约见 app/contracts/cutting_experience.py）。"""
    payload: dict = {
        "machine_id": "VM-CRUD-01",
        "tool_id": "T01",
        "material": "45",
        "machining_type": "milling",
        "parameters": {
            "depth_of_cut_mm": 1.5,
            "feed_mm_per_rev": 0.2,
            "spindle_rpm": 6000,
            "coolant": "flood",
        },
        "results": {
            "cycle_time_s": 120.0,
            "surface_roughness_ra": 1.6,
            "tool_wear_percent": 12.5,
            "result": "ok",
        },
        "source": "manual",
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """模块级一次性环境：临时 SQLite + 临时用户存储 + 登录后的 TestClient。

    teardown 恢复 DB_URL / 引擎单例 / 用户存储，避免影响同进程内
    其他测试模块（部分用例依赖「数据库未配置 → 503」的行为）。
    """
    tmp = tmp_path_factory.mktemp("cutting_experience_it")

    import asyncio

    from app.database import connection as db_connection
    from app.database.models.cutting_experience import CuttingExperienceRecord
    from app.middleware import rate_limiter as rate_limiter_module
    from app.models import user as user_module

    # DB：指向临时 SQLite 并重置引擎单例，确保懒初始化读到新 DB_URL
    original_db_url = os.environ.get("DB_URL")
    original_singletons = db_connection._singletons
    os.environ["DB_URL"] = f"sqlite:///{(tmp / 'experience.db').as_posix()}"
    db_connection._singletons = db_connection._DatabaseSingletons()

    async def _create_tables() -> None:
        engine = db_connection.get_engine()
        assert engine is not None, "DB_URL 已设置，引擎不应为 None"
        async with engine.begin() as conn:
            await conn.run_sync(CuttingExperienceRecord.metadata.create_all)

    asyncio.run(_create_tables())

    # --- 用户存储：重定向到临时文件，避免污染开发数据（.lnn_users.json）---
    original_store_file = user_module.USER_STORE_FILE
    user_module.USER_STORE_FILE = str(tmp / "users.json")
    user_module._holder.reset()

    # 登录/注册限流：清空 slowapi 存储，避免与其他模块的请求叠加触发 429
    for _name in ("limiter", "_registration_limiter"):
        _limiter = getattr(rate_limiter_module, _name, None)
        storage = getattr(_limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            try:
                storage.reset()
            except Exception:
                pass

    from app.main import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": USERNAME,
                "password": PASSWORD,
                "invite_code": os.environ["LNN_REGISTRATION_CODE"],
            },
        )
        assert resp.status_code == 200, f"注册失败: {resp.text}"
        resp = client.post(
            "/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
        )
        assert resp.status_code == 200, f"登录失败: {resp.text}"
        body = resp.json()
        assert body["code"] == 0
        token = body["data"]["access_token"]
        yield SimpleNamespace(client=client, headers={"Authorization": f"Bearer {token}"})

    # 恢复环境
    user_module.USER_STORE_FILE = original_store_file
    user_module._holder.reset()
    if original_db_url is None:
        os.environ.pop("DB_URL", None)
    else:
        os.environ["DB_URL"] = original_db_url
    db_connection._singletons = original_singletons


# 采集（capture / batch）


class TestCapture:
    def test_capture_returns_201_and_persists(self, env):
        """单条采集：201 + ORM 主键（exp_ 前缀）+ 契约回显。"""
        resp = env.client.post(f"{BASE}/capture", json=_record(), headers=env.headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["id"].startswith("exp_")
        assert data["machine_id"] == "VM-CRUD-01"
        assert data["tool_id"] == "T01"
        assert data["machining_type"] == "milling"
        assert data["parameters"]["depth_of_cut_mm"] == 1.5
        assert data["results"]["cycle_time_s"] == 120.0
        assert data["results"]["result"] == "ok"
        assert data["source"] == "manual"
        assert data["created_at"] is not None

    def test_capture_roundtrip_detail_by_returned_id(self, env):
        """capture 返回的 id 必须能直接查详情（回归：路径参数曾为 UUID 类型，
        对 exp_ 前缀主键必然 422）。"""
        resp = env.client.post(f"{BASE}/capture", json=_record(), headers=env.headers)
        assert resp.status_code == 201
        record_id = resp.json()["id"]

        detail = env.client.get(f"{BASE}/{record_id}", headers=env.headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == record_id

    def test_capture_rejects_zero_depth_of_cut(self, env):
        """契约边界：切深必须 > 0（飞轮监督信号有效性）。"""
        resp = env.client.post(
            f"{BASE}/capture",
            json=_record(parameters={"depth_of_cut_mm": 0, "feed_mm_per_rev": 0.2, "spindle_rpm": 6000}),
            headers=env.headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 1002

    def test_capture_rejects_out_of_range_tool_wear(self, env):
        """契约边界：刀具磨损 0-100。"""
        resp = env.client.post(
            f"{BASE}/capture",
            json=_record(results={"cycle_time_s": 100.0, "tool_wear_percent": 150, "result": "ok"}),
            headers=env.headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 1002

    def test_capture_rejects_unknown_result_enum(self, env):
        resp = env.client.post(
            f"{BASE}/capture",
            json=_record(results={"cycle_time_s": 100.0, "result": "INVALID"}),
            headers=env.headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 1002


class TestBatchCapture:
    def test_batch_inserts_all_and_reports_counts(self, env):
        payloads = [_record(machine_id=f"VM-BATCH-{i}") for i in range(3)]
        resp = env.client.post(f"{BASE}/batch", json=payloads, headers=env.headers)
        assert resp.status_code == 201, resp.text
        assert resp.json() == {"inserted": 3, "requested": 3}

    def test_batch_empty_is_accepted_as_zero(self, env):
        resp = env.client.post(f"{BASE}/batch", json=[], headers=env.headers)
        assert resp.status_code == 201
        assert resp.json() == {"inserted": 0, "requested": 0}

    def test_batch_over_limit_rejected(self, env):
        """批量上限 1000 条（防止单请求长事务）。"""
        payloads = [_record(machine_id="VM-OVERLIMIT") for _ in range(1001)]
        resp = env.client.post(f"{BASE}/batch", json=payloads, headers=env.headers)
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == 1002
        assert "1000" in body["message"]


# 查询与统计


class TestListExperiences:
    def test_list_returns_pagination_envelope(self, env):
        for i in range(3):
            resp = env.client.post(
                f"{BASE}/capture",
                json=_record(machine_id="VM-LIST-01", tool_id=f"T{i:02d}"),
                headers=env.headers,
            )
            assert resp.status_code == 201

        resp = env.client.get(
            f"{BASE}",
            params={"machine_id": "VM-LIST-01", "limit": 2, "offset": 0},
            headers=env.headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 3
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert len(data["records"]) == 2
        for row in data["records"]:
            assert row["machine_id"] == "VM-LIST-01"

    def test_list_second_page_returns_remaining_records(self, env):
        resp = env.client.get(
            f"{BASE}",
            params={"machine_id": "VM-LIST-01", "limit": 2, "offset": 2},
            headers=env.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["records"]) == 1

    def test_list_filters_by_result(self, env):
        resp = env.client.post(
            f"{BASE}/capture",
            json=_record(machine_id="VM-FILTER-01", results={"cycle_time_s": 60.0, "result": "scrap"}),
            headers=env.headers,
        )
        assert resp.status_code == 201
        resp = env.client.get(
            f"{BASE}",
            params={"machine_id": "VM-FILTER-01", "result": "scrap"},
            headers=env.headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["records"][0]["results"]["result"] == "scrap"

    def test_list_rejects_invalid_time_format(self, env):
        resp = env.client.get(
            f"{BASE}",
            params={"start_time": "not-a-date"},
            headers=env.headers,
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == 1002


class TestStats:
    def test_stats_aggregates_seeded_records(self, env):
        """固定种子数据下断言精确统计值（2 ok + 1 scrap，异常率 0）。"""
        seeded = [
            (100.0, "ok"),
            (200.0, "ok"),
            (300.0, "scrap"),
        ]
        for cycle, result in seeded:
            resp = env.client.post(
                f"{BASE}/capture",
                json=_record(
                    machine_id="VM-STAT-01",
                    results={"cycle_time_s": cycle, "result": result},
                ),
                headers=env.headers,
            )
            assert resp.status_code == 201

        resp = env.client.get(f"{BASE}/stats", params={"machine_id": "VM-STAT-01"}, headers=env.headers)
        assert resp.status_code == 200, resp.text
        stats = resp.json()
        assert stats["total_records"] == 3
        assert stats["avg_cycle_time_s"] == pytest.approx(200.0)
        assert stats["ok_rate"] == pytest.approx(2 / 3)
        assert stats["anomaly_rate"] == 0.0

    def test_stats_empty_machine_returns_zero_totals(self, env):
        resp = env.client.get(f"{BASE}/stats", params={"machine_id": "VM-STAT-EMPTY"}, headers=env.headers)
        assert resp.status_code == 200
        assert resp.json()["total_records"] == 0
        assert resp.json()["ok_rate"] is None


# 详情与删除


class TestDetailAndDelete:
    def test_detail_unknown_id_returns_404(self, env):
        resp = env.client.get(f"{BASE}/exp_deadbeefdeadbeefdeadbeefdeadbeef", headers=env.headers)
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == 3002

    def test_detail_accepts_uuid_form_of_id(self, env):
        """契约 UUID 形式（含连字符）经归一化后同样可查（repository _normalize_pk）。"""
        import uuid as uuid_module

        raw_uuid = uuid_module.uuid4()
        resp = env.client.post(
            f"{BASE}/capture",
            json=_record(machine_id="VM-UUID-01", id=str(raw_uuid)),
            headers=env.headers,
        )
        assert resp.status_code == 201
        stored_id = resp.json()["id"]

        detail = env.client.get(f"{BASE}/{raw_uuid}", headers=env.headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == stored_id

    def test_delete_roundtrip(self, env):
        resp = env.client.post(f"{BASE}/capture", json=_record(machine_id="VM-DEL-01"), headers=env.headers)
        assert resp.status_code == 201
        record_id = resp.json()["id"]

        deleted = env.client.delete(f"{BASE}/{record_id}", headers=env.headers)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"deleted": True, "id": record_id}

        detail = env.client.get(f"{BASE}/{record_id}", headers=env.headers)
        assert detail.status_code == 404

    def test_delete_missing_record_returns_404(self, env):
        resp = env.client.delete(f"{BASE}/exp_deadbeefdeadbeefdeadbeefdeadbeef", headers=env.headers)
        assert resp.status_code == 404
        assert resp.json()["code"] == 3002


class TestPermissionDependency:
    """``require_permission`` 依赖的 RBAC 行为。

    背景：工程侧测试套件在 ``tests/conftest.py`` 全局关闭鉴权中间件
    （``LNN_AUTH_ENABLED=false`` 等），HTTP 层无法复现 401/403；
    此处以单元级直接驱动依赖函数，保留权限回归覆盖。
    """

    @staticmethod
    def _make_checker():
        from app.auth.permissions import require_permission

        return require_permission("experience:write")

    def test_checker_without_identity_raises_401(self, monkeypatch):
        """request.state.username 缺失（未认证）→ 401。

        套件全局关闭了权限强制（tests/conftest.py），此处显式恢复以驱动真实分支。
        """
        import asyncio

        from fastapi import HTTPException

        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "true")

        class _FakeRequest:
            state = SimpleNamespace()  # 无 username 属性

        checker = self._make_checker()
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(checker(_FakeRequest()))
        assert exc_info.value.status_code == 401

    def test_checker_bypassed_when_enforcement_disabled(self, monkeypatch):
        """LNN_PERMISSION_ENFORCED=false 时放行（与 UnifiedAuthMiddleware 语义一致）。"""
        import asyncio

        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")

        class _FakeRequest:
            state = SimpleNamespace()

        checker = self._make_checker()
        assert asyncio.run(checker(_FakeRequest())) is None
