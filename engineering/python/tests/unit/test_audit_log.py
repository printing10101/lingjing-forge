"""Unit tests for audit_log module."""

from __future__ import annotations

import pytest

from app.audit.audit_log import AuditLog, AIModule, UserDecision, OperationStatus, AuditLogEntry


class TestAIModuleEnum:
    def test_all_modules_defined(self):
        modules = [
            AIModule.LNN_PREDICT,
            AIModule.LNN_TRAIN,
            AIModule.PROCESS_OPTIMIZE,
            AIModule.TOOL_WEAR_ANALYZE,
            AIModule.CAD_GENERATE,
        ]
        for m in modules:
            assert isinstance(m.value, str)
            assert len(m.value) > 0


class TestUserDecisionEnum:
    def test_all_decisions_defined(self):
        decisions = [
            UserDecision.ACCEPT,
            UserDecision.MODIFY,
            UserDecision.REJECT,
            UserDecision.AUTO_EXECUTED,
        ]
        for d in decisions:
            assert isinstance(d.value, str)


class TestOperationStatusEnum:
    def test_all_statuses_defined(self):
        statuses = [
            OperationStatus.SUCCESS,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.PENDING,
        ]
        for s in statuses:
            assert isinstance(s.value, str)


class TestAuditLog:
    def test_create_audit_log_instance(self):
        audit = AuditLog()
        assert audit is not None

    def test_log_decision_returns_entry(self):
        audit = AuditLog()
        entry = audit.log_decision(
            ai_module=AIModule.LNN_PREDICT,
            ai_recommendation={"value": 0.95},
            user_decision=UserDecision.ACCEPT,
            final_execution={"result": "success"},
            operation_status=OperationStatus.SUCCESS,
            confidence=0.95,
        )
        assert entry is not None
        assert entry.timestamp_ms > 0

    def test_log_decision_with_user_id(self):
        audit = AuditLog()
        entry = audit.log_decision(
            ai_module=AIModule.LNN_PREDICT,
            ai_recommendation={"value": 0.95},
            user_decision=UserDecision.ACCEPT,
            final_execution={"result": "success"},
            operation_status=OperationStatus.SUCCESS,
            user_id="user-001",
            username="测试用户",
            input_parameters={"model_name": "test_model"},
            confidence=0.95,
        )
        assert entry.user_id == "user-001"
        assert entry.username == "测试用户"
        assert entry.input_parameters == {"model_name": "test_model"}

    def test_get_logs_returns_list(self):
        audit = AuditLog()
        logs = audit.get_logs(limit=10, offset=0)
        assert isinstance(logs, list)

    def test_get_logs_with_user_id_filter(self):
        audit = AuditLog()
        logs = audit.get_logs(
            limit=10,
            offset=0,
            user_id="user-001",
        )
        assert isinstance(logs, list)

    def test_get_statistics_returns_dict(self):
        audit = AuditLog()
        stats = audit.get_statistics()
        assert isinstance(stats, dict)

    def test_clear_logs_blocked_by_compliance(self):
        """clear_logs() 被合规保护禁止（FDA 21 CFR Part 11 / SOC 2）。

        审计日志不可任意清空——必须走 clear_logs_with_authorization()。
        """
        audit = AuditLog()
        with pytest.raises(RuntimeError, match="compliance"):
            audit.clear_logs()

    def test_search_logs_returns_list(self):
        audit = AuditLog()
        logs = audit.search_logs(keyword="test", limit=10)
        assert isinstance(logs, list)

    def test_export_logs_csv_format(self):
        audit = AuditLog()
        result = audit.export_logs(format="csv")
        assert isinstance(result, str)

    def test_export_logs_json_format(self):
        audit = AuditLog()
        result = audit.export_logs(format="json")
        assert isinstance(result, str)

    def test_get_logs_with_filters(self):
        audit = AuditLog()
        logs = audit.get_logs(
            limit=5,
            offset=0,
            ai_module="lnn_predict",
            user_decision="accept",
        )
        assert isinstance(logs, list)


class TestAuditLogEntryNewFields:
    def test_entry_user_id_default(self):
        entry = AuditLogEntry(
            timestamp_ms=1234567890123,
            ai_module="lnn_predict",
            ai_recommendation={"v": 0.5},
            user_decision="accept",
            final_execution={"v": 0.5},
            operation_status="success",
        )
        assert entry.user_id is None
        assert entry.username is None

    def test_entry_from_dict_backward_compat(self):
        data = {
            "timestamp_ms": 9999999999999,
            "ai_module": "lnn_predict",
            "ai_recommendation": {"v": 0.5},
            "user_decision": "accept",
            "final_execution": {"v": 0.5},
            "operation_status": "success",
        }
        entry = AuditLogEntry.from_dict(data)
        assert entry.user_id is None
        assert entry.username is None
        assert entry.input_parameters == {}

    def test_entry_to_dict_includes_new_fields(self):
        entry = AuditLogEntry(
            timestamp_ms=1234567890123,
            ai_module="lnn_predict",
            ai_recommendation={"v": 0.5},
            user_decision="accept",
            final_execution={"v": 0.5},
            operation_status="success",
            user_id="u001",
            username="张三",
            input_parameters={"p1": "v1"},
        )
        d = entry.to_dict()
        assert d["user_id"] == "u001"
        assert d["username"] == "张三"
        assert d["input_parameters"] == {"p1": "v1"}
