"""Agent Gateway G 代码任务端点测试（W12 扩面）。

覆盖：路径白名单 fail-closed（遍历/越界/后缀/404）、controller 校验、
创建-轮询全链路（真实 pipeline 后台执行）、列表与详情。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.api.v1.agent_gateway.gcode_jobs import reset_shared_gcode_pipeline
from app.gcode_generation.replay_harness import write_seed_input_set


@pytest.fixture()
def input_roots(tmp_path, monkeypatch):
    """种子输入集写入 tmp 并设为唯一白名单根。"""
    write_seed_input_set(tmp_path / "inputs")
    roots = os_pathsep_join(tmp_path / "inputs")
    monkeypatch.setenv("LINGJING_GCODE_INPUT_ROOTS", roots)
    reset_shared_gcode_pipeline()
    yield tmp_path / "inputs"
    reset_shared_gcode_pipeline()


def os_pathsep_join(p) -> str:
    import os

    return str(p)


class TestCreateJobPathValidation:
    def test_rejects_path_traversal(self, client, input_roots, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "LINGJING_GCODE_INPUT_ROOTS", str(input_roots / "case_a_stable")
        )
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(input_roots / "case_a_stable" / ".." / "case_b_unstable" / "chatter_report.json"),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        # resolve 后 .. 归一化到 case_b_unstable —— 不在白名单根 case_a_stable 内
        assert resp.status_code == 403

    def test_rejects_outside_roots(self, client, input_roots, tmp_path):
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": "C:/Windows/notepad.exe.json",
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        assert resp.status_code == 403

    def test_rejects_non_json_suffix(self, client, input_roots, tmp_path):
        import json as _json

        fake = tmp_path / "inputs" / "evil.txt"
        fake.write_text("{}", encoding="utf-8")
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(fake),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        assert resp.status_code == 422

    def test_rejects_missing_file(self, client, input_roots):
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(input_roots / "case_a_stable" / "nope.json"),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        assert resp.status_code == 404

    def test_rejects_invalid_controller(self, client, input_roots):
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(input_roots / "case_a_stable" / "chatter_report.json"),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
                "controller_type": "hack_cnc",
            },
        )
        assert resp.status_code == 422


class TestCreateAndTrackJob:
    def test_create_returns_task_id_and_reaches_terminal(self, client, input_roots):
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(input_roots / "case_a_stable" / "chatter_report.json"),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        task_id = data["data"]["task_id"]
        assert task_id

        # 轮询至终态（真实 pipeline 后台执行；单任务 <5s）
        deadline = time.time() + 20
        status = ""
        while time.time() < deadline:
            detail = client.get(f"/api/agent/v1/gcode/jobs/{task_id}")
            assert detail.status_code == 200
            payload = detail.json()["data"]
            status = payload["status"]
            if status not in ("pending", "running"):
                break
            time.sleep(0.3)
        assert status == "generated", f"任务未按预期生成: status={status}"

    def test_get_job_default_hides_gcode(self, client, input_roots):
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(input_roots / "case_a_stable" / "chatter_report.json"),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        task_id = resp.json()["data"]["task_id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            payload = client.get(f"/api/agent/v1/gcode/jobs/{task_id}").json()["data"]
            if payload["status"] not in ("pending", "running"):
                break
            time.sleep(0.3)
        assert payload["gcode_text"] == ""
        assert payload.get("gcode_text_length", 0) > 0
        assert "CAM" in payload["disclaimer"]

    def test_get_job_include_gcode(self, client, input_roots):
        resp = client.post(
            "/api/agent/v1/gcode/jobs",
            json={
                "chatter_report_path": str(input_roots / "case_a_stable" / "chatter_report.json"),
                "operation_plan_path": str(input_roots / "case_a_stable" / "operation_plan.json"),
            },
        )
        task_id = resp.json()["data"]["task_id"]
        deadline = time.time() + 20
        while time.time() < deadline:
            payload = client.get(
                f"/api/agent/v1/gcode/jobs/{task_id}?include_gcode=true"
            ).json()["data"]
            if payload["status"] not in ("pending", "running"):
                break
            time.sleep(0.3)
        assert "O1000" in payload["gcode_text"] or len(payload["gcode_text"]) > 0

    def test_get_nonexistent_job_404(self, client):
        resp = client.get("/api/agent/v1/gcode/jobs/gc_does_not_exist")
        assert resp.status_code == 404

    def test_get_job_invalid_task_id_422(self, client):
        resp = client.get("/api/agent/v1/gcode/jobs/..%2Fevil")
        assert resp.status_code in (404, 422)  # 路由不匹配或校验拦截

    def test_list_jobs(self, client, input_roots):
        resp = client.get("/api/agent/v1/gcode/jobs?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["code"] == 0
        assert isinstance(data["data"]["tasks"], list)
