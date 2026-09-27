"""W7.2 Dreaming API 测试（反思触发 / 规则联查 / 灰度放行晋级 / 一键回滚 / 学习事件）。

所有依赖（发布器/草稿库/回滚管理器/反思管线）通过模块级获取器注入，
测试全程不触碰 python/outputs/ 真实目录，也不跑真实反思管线。
"""

from __future__ import annotations

import os

os.environ.setdefault("LNN_PERMISSION_ENFORCED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import dreaming as dreaming_api
from app.dreaming.apply_rules import ApplyResult
from app.dreaming.progressive_publisher import ProgressivePublisher
from app.dreaming.rollback_manager import RollbackManager
from app.dreaming.rule_synthesizer import RuleDraft, RuleSynthesizer
from app.dreaming._validator_models import ValidationResult


class _StubValidator:
    def __init__(self):
        self.calls = 0

    def validate(self, rule, **kwargs):
        self.calls += 1
        return ValidationResult(passed=True, errors=[])


class _StubApplicator:
    def __init__(self):
        self.apply_calls: list[str] = []
        self.rollback_calls: list[str] = []

    def apply(self, rule, skip_validation: bool = False, **kwargs):
        self.apply_calls.append(rule.rule_id)
        return ApplyResult(success=True, rule_id=rule.rule_id, audit_entry_seq=1)

    def rollback(self, rule_id: str, **kwargs):
        self.rollback_calls.append(rule_id)
        from app.dreaming.apply_rules import RollbackResult

        return RollbackResult(
            success=True,
            rule_id=rule_id,
            rolled_back_at="2026-09-07T00:00:00+00:00",
            previous_status="APPLIED",
        )


class _StubAuditRecorder:
    def record_rule_application(self, **kwargs):
        pass


def _make_draft(rule_id: str = "rule_api_001") -> RuleDraft:
    return RuleDraft(
        rule_id=rule_id,
        rule_type="parameter_adjustment",
        description="API 测试规则：高转速降速",
        condition={"param": "spindle_speed", "op": ">", "value": 8000},
        action={"action": "adjust", "target": "spindle_speed", "delta": -500},
        confidence=0.85,
    )


GOOD_METRICS = {"accuracy": 0.9, "false_positive_rate": 0.05, "sample_size": 50, "error_rate": 0.02}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离的 dreaming API 环境：发布器/草稿库/回滚管理器全部指向 tmp。"""
    publisher = ProgressivePublisher(
        state_dir=str(tmp_path / "pub_state"),
        validator=_StubValidator(),
        applicator=_StubApplicator(),
    )
    applicator = publisher._applicator
    drafts_store = RuleSynthesizer(output_dir=str(tmp_path / "rules"))

    def _manager(publisher_: ProgressivePublisher) -> RollbackManager:
        manager = RollbackManager(
            history_dir=str(tmp_path / "rb_history"),
            publisher=publisher_,
            applicator=applicator,
        )
        manager._get_audit_recorder = lambda: _StubAuditRecorder()
        return manager

    monkeypatch.setattr(dreaming_api, "_get_publisher", lambda: publisher)
    monkeypatch.setattr(dreaming_api, "_get_drafts_store", lambda: drafts_store)
    monkeypatch.setattr(dreaming_api, "_get_rollback_manager", _manager)
    monkeypatch.setattr(dreaming_api, "_get_reports_dir", lambda: tmp_path / "reports")

    app = FastAPI()
    app.include_router(dreaming_api.router)
    client = TestClient(app)
    return {"client": client, "publisher": publisher, "drafts_store": drafts_store}


def _seed_draft(store: RuleSynthesizer, rule_id: str = "rule_api_001") -> RuleDraft:
    draft = _make_draft(rule_id)
    store._persist_rules([draft])
    return draft


@pytest.mark.api
class TestRulesEndpoint:
    def test_list_rules_empty(self, env):
        resp = env["client"].get("/api/v1/dreaming/rules")
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["total"] == 0

    def test_list_rules_joins_publication(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        # 未发布：publication 为 None
        body = client.get("/api/v1/dreaming/rules").json()
        assert body["data"]["total"] == 1
        assert body["data"]["rules"][0]["publication"] is None
        assert body["data"]["rules"][0]["description"] == "API 测试规则：高转速降速"

        # 发布后：联查到灰度状态
        client.post("/api/v1/dreaming/rules/rule_api_001/publish")
        body = client.get("/api/v1/dreaming/rules").json()
        pub = body["data"]["rules"][0]["publication"]
        assert pub["current_stage"] == "shadow"

    def test_list_rules_status_filter(self, env):
        store = env["drafts_store"]
        draft_a = _make_draft("rule_a")
        draft_b = _make_draft("rule_b")
        draft_b.status = "applied"
        store._persist_rules([draft_a, draft_b])

        body = env["client"].get("/api/v1/dreaming/rules", params={"status": "draft"}).json()
        assert {r["rule_id"] for r in body["data"]["rules"]} == {"rule_a"}


@pytest.mark.api
class TestPublishAndPromote:
    def test_publish_unknown_rule_returns_not_found(self, env):
        body = env["client"].post("/api/v1/dreaming/rules/rule_missing/publish").json()
        assert body["code"] != 0
        assert "不存在" in body["message"]

    def test_publish_then_promote_to_canary(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        assert client.post("/api/v1/dreaming/rules/rule_api_001/publish").json()["code"] == 0

        resp = client.post(
            "/api/v1/dreaming/rules/rule_api_001/promote",
            json={"metrics": GOOD_METRICS},
        )
        body = resp.json()
        assert body["code"] == 0, body
        assert body["data"]["stage"] == "canary"
        assert body["data"]["traffic_percentage"] == 0.01

    def test_promote_unknown_rule_returns_not_found(self, env):
        body = env["client"].post(
            "/api/v1/dreaming/rules/rule_missing/promote", json={"metrics": GOOD_METRICS}
        ).json()
        assert body["code"] != 0
        assert "未发布" in body["message"]

    def test_promote_to_full_requires_revalidation_and_applies(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        assert client.post("/api/v1/dreaming/rules/rule_api_001/publish").json()["code"] == 0
        for _ in range(3):
            resp = client.post(
                "/api/v1/dreaming/rules/rule_api_001/promote", json={"metrics": GOOD_METRICS}
            )
            assert resp.json()["code"] == 0

        resp = client.post(
            "/api/v1/dreaming/rules/rule_api_001/promote",
            json={"metrics": GOOD_METRICS, "target_stage": "full"},
        )
        body = resp.json()
        assert body["code"] == 0, body
        assert body["data"]["stage"] == "full"
        # FULL 晋级必须触发沙箱重校验 + 真正应用
        assert env["publisher"]._validator.calls >= 1
        assert env["publisher"]._applicator.apply_calls == ["rule_api_001"]


@pytest.mark.api
class TestRollbackEndpoint:
    def test_manual_rollback_demotes_one_stage(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        client.post("/api/v1/dreaming/rules/rule_api_001/publish")
        client.post("/api/v1/dreaming/rules/rule_api_001/promote", json={"metrics": GOOD_METRICS})

        resp = client.post(
            "/api/v1/dreaming/rules/rule_api_001/rollback",
            json={"reason": "误报率升高"},
        )
        body = resp.json()
        assert body["code"] == 0, body
        assert body["data"]["current_stage"] == "shadow"

    def test_hard_constraint_rollback_deprecates(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        client.post("/api/v1/dreaming/rules/rule_api_001/publish")

        resp = client.post(
            "/api/v1/dreaming/rules/rule_api_001/rollback",
            json={"reason": "违反 cam_validation_required", "severity": "hard_constraint"},
        )
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["fully_deprecated"] is True
        assert body["data"]["current_stage"] == "deprecated"


@pytest.mark.api
class TestLearningsAndStatus:
    def test_learnings_contains_draft_and_stage_events(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        client.post("/api/v1/dreaming/rules/rule_api_001/publish")
        client.post("/api/v1/dreaming/rules/rule_api_001/promote", json={"metrics": GOOD_METRICS})

        body = client.get("/api/v1/dreaming/learnings", params={"days": 1}).json()
        assert body["code"] == 0
        types = {e["type"] for e in body["data"]["events"]}
        assert "draft_created" in types
        assert "stage_publish" in types
        assert "stage_promote" in types
        # 事件按时间倒序
        times = [e["operated_at"] for e in body["data"]["events"]]
        assert times == sorted(times, reverse=True)

    def test_status_counts_stages_and_drafts(self, env):
        _seed_draft(env["drafts_store"])
        client = env["client"]
        client.post("/api/v1/dreaming/rules/rule_api_001/publish")

        body = client.get("/api/v1/dreaming/status").json()
        data = body["data"]
        assert data["draft_count"] == 1
        assert data["published_count"] == 1
        assert data["stage_counts"] == {"shadow": 1}


@pytest.mark.api
class TestReflectEndpoint:
    def test_reflect_success_summary(self, env, monkeypatch):
        from app.dreaming.service import ReflectionRunSummary

        async def _stub_run(**kwargs):
            return ReflectionRunSummary(
                ok=True,
                session_count=5,
                draft_rule_count=2,
                insight_count=3,
                llm_model=None,
            )

        monkeypatch.setattr("app.dreaming.service.run_reflection", _stub_run)
        resp = env["client"].post("/api/v1/dreaming/reflect", json={"lookback_days": 7})
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["session_count"] == 5
        assert body["data"]["draft_rule_count"] == 2
        assert "2 条规则草稿" in body["message"]

    def test_reflect_failure_returns_structured_error(self, env, monkeypatch):
        from app.dreaming.service import ReflectionRunSummary

        async def _stub_run(**kwargs):
            return ReflectionRunSummary(ok=False, error="未提取到任何 Session，请检查数据源配置")

        monkeypatch.setattr("app.dreaming.service.run_reflection", _stub_run)
        resp = env["client"].post("/api/v1/dreaming/reflect", json={"lookback_days": 7})
        body = resp.json()
        assert body["code"] != 0
        assert "未提取到任何 Session" in body["message"]
        assert body["detail"]["ok"] is False

    def test_reflect_rejects_invalid_lookback(self, env):
        resp = env["client"].post("/api/v1/dreaming/reflect", json={"lookback_days": 0})
        assert resp.status_code == 422
