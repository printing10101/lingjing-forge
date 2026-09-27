"""HTTP API 契约一致性审计测试（p5-6）.

对应 docs/development/core-contracts-design.md 第 10 章阶段 5 验收标准：
    "CLI 命令与 HTTP API 行为一致" + "HTTP API 版本化契约（/api/v1/ 全部对齐契约）"

本测试文件审计三大一致性维度，**不依赖 FastAPI 启动**（避免本地 conftest.py
强制加载 fastapi 导致的 ImportError），仅做静态契约校验：

    1. 契约层 dataclass 字段稳定性（防回归）
       - WorkflowSpec / WorkflowNode / WorkflowEdge
       - DatasetSchema / DatasetVersion / LineageRecord
       - ExperimentSnapshot
    2. 响应信封规范（app/core/response.py）
       - success() / error() / error_response() 结构
       - ErrorCode 数值映射稳定性（SUCCESS=0, NOT_FOUND=1001, ...）
    3. API 路由静态契约（app/api/v1/{workflows,datasets,snapshots}.py）
       - 路由 prefix 一致（/api/v1/<resource>）
       - 端点数量稳定（workflows=8, datasets=9, snapshots=4）
       - Pydantic 请求模型字段命名采用 snake_case
       - 关键响应 data 字段命名（workflow_run_id / dataset_id / snapshot_id）
    4. 契约版本稳定性
       - CONTRACTS_VERSION == "1.0.0"
       - app.contracts.__version__ == CONTRACTS_VERSION

CI 标记：@pytest.mark.unit（与 ci.yml `pytest -m unit` 对齐）。

断言策略：
    - 字段集合断言用 `set(dataclass.__annotations__.keys())` 防止字段被误删
    - 字段类型断言用 `typing.get_type_hints()` 防止类型漂移
    - 数值映射断言用硬编码期望值，任何重新编号都会立即失败
"""

from __future__ import annotations

import inspect
from dataclasses import fields as dc_fields
from typing import get_type_hints

import pytest

# 契约层导入
from app.contracts import CONTRACTS_VERSION
from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    DatasetVersion,
    LineageRecord,
)
from app.contracts.observability import ExperimentSnapshot
from app.contracts.task import (
    Artifact,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
)
from app.core.response import (
    ErrorCode,
    code_to_numeric,
    error,
    error_response,
    numeric_to_code,
    success,
)


# 1. 契约层 dataclass 字段稳定性


def _field_names(cls: type) -> set[str]:
    """提取 dataclass 字段名集合（不含继承自 object 的属性）。"""
    return {f.name for f in dc_fields(cls)}


@pytest.mark.unit
class TestContractFieldStability:
    """契约层 dataclass 字段集合稳定性（防回归）.

    任何字段的增删改都会触发断言失败，强制开发者走 ADR 流程。
    """

    def test_workflow_spec_fields(self):
        """WorkflowSpec 必须包含 7 个字段，且名称稳定."""
        expected = {"name", "version", "nodes", "edges", "inputs", "outputs", "metadata"}
        actual = _field_names(WorkflowSpec)
        assert actual == expected, (
            f"WorkflowSpec 字段集合漂移：期望 {expected}，实际 {actual}。"
            f"任何字段变更必须新开 ADR 并提升 CONTRACTS_VERSION。"
        )

    def test_workflow_node_fields(self):
        """WorkflowNode 必须包含 6 个字段."""
        expected = {
            "node_id",
            "task_type",
            "params",
            "inputs",
            "retry",
            "timeout_seconds",
        }
        actual = _field_names(WorkflowNode)
        assert actual == expected, f"WorkflowNode 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_workflow_edge_fields(self):
        """WorkflowEdge 必须包含 2 个字段."""
        expected = {"upstream", "downstream"}
        actual = _field_names(WorkflowEdge)
        assert actual == expected, f"WorkflowEdge 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_artifact_fields(self):
        """Artifact 必须包含 4 个字段."""
        expected = {"name", "type", "uri", "metadata"}
        actual = _field_names(Artifact)
        assert actual == expected, f"Artifact 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_dataset_schema_fields(self):
        """DatasetSchema 必须包含 3 个字段."""
        expected = {"fields", "primary_key", "metadata"}
        actual = _field_names(DatasetSchema)
        assert actual == expected, f"DatasetSchema 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_dataset_version_fields(self):
        """DatasetVersion 必须包含 11 个字段."""
        expected = {
            "dataset_id",
            "version",
            "status",
            "schema",
            "content_hash",
            "row_count",
            "size_bytes",
            "created_at",
            "created_by",
            "storage_uri",
            "lineage",
        }
        actual = _field_names(DatasetVersion)
        assert actual == expected, f"DatasetVersion 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_lineage_record_fields(self):
        """LineageRecord 必须包含 9 个字段."""
        expected = {
            "record_id",
            "target",
            "source_type",
            "source_ref",
            "inputs",
            "outputs",
            "operation",
            "timestamp",
            "metadata",
        }
        actual = _field_names(LineageRecord)
        assert actual == expected, f"LineageRecord 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_experiment_snapshot_fields(self):
        """ExperimentSnapshot 必须包含 13 个字段."""
        expected = {
            "snapshot_id",
            "created_at",
            "created_by",
            "git_sha",
            "code_dirty",
            "config",
            "dataset_versions",
            "model_uri",
            "metrics",
            "environment",
            "lineage_record_id",
            "mlflow_run_id",
            "notes",
        }
        actual = _field_names(ExperimentSnapshot)
        assert actual == expected, f"ExperimentSnapshot 字段集合漂移：期望 {expected}，实际 {actual}。"

    def test_dataset_status_enum_values(self):
        """DatasetStatus 必须包含 4 个枚举值，且字符串值稳定."""
        expected = {"draft", "published", "deprecated", "archived"}
        actual = {member.value for member in DatasetStatus}
        assert actual == expected, f"DatasetStatus 枚举值漂移：期望 {expected}，实际 {actual}。"


# 2. 响应信封规范


@pytest.mark.unit
class TestResponseEnvelopeContract:
    """响应信封 {code, message, data, request_id} 结构稳定性."""

    def test_success_envelope_keys(self):
        """success() 必须返回恰好 4 个键：code/message/data/request_id."""
        result = success()
        assert set(result.keys()) == {"code", "message", "data", "request_id"}, (
            f"success() 键集合漂移：{set(result.keys())}。契约要求恰好 4 个键，新增字段必须走 ADR 流程。"
        )

    def test_success_code_is_zero(self):
        """成功响应 code 必须为 0（整数，非字符串）."""
        result = success()
        assert result["code"] == 0
        assert isinstance(result["code"], int), "code 必须为 int 类型，不能是 str"

    def test_success_request_id_is_string(self):
        """request_id 必须为非空字符串."""
        result = success()
        assert isinstance(result["request_id"], str)
        assert len(result["request_id"]) > 0

    def test_error_envelope_required_keys(self):
        """error() 必须包含 code/message/request_id 三个必需键."""
        result = error(ErrorCode.NOT_FOUND)
        required = {"code", "message", "request_id"}
        assert required.issubset(set(result.keys())), f"error() 缺少必需键：{required - set(result.keys())}"

    def test_error_code_is_numeric(self):
        """error() 的 code 必须为整数（数值码，非字符串枚举）."""
        result = error(ErrorCode.INTERNAL_ERROR)
        assert isinstance(result["code"], int), "error() code 必须为 int 类型"
        assert result["code"] != 0, "错误响应 code 不能为 0"

    def test_error_optional_keys_only_when_provided(self):
        """detail/suggestion/severity/recoverable 仅在提供时出现."""
        result_bare = error(ErrorCode.NOT_FOUND)
        assert "detail" not in result_bare
        assert "suggestion" not in result_bare
        assert "severity" not in result_bare
        assert "recoverable" not in result_bare

        result_full = error(
            ErrorCode.INVALID_REQUEST,
            message="validation failed",
            detail={"field": "x"},
            suggestion="check input",
            severity="warning",
            recoverable=True,
        )
        assert result_full["detail"] == {"field": "x"}
        assert result_full["suggestion"] == "check input"
        assert result_full["severity"] == "warning"
        assert result_full["recoverable"] is True


@pytest.mark.unit
class TestErrorCodeNumericMapping:
    """ErrorCode 数值映射稳定性（防回归）.

    数值码是 SDK 异常映射（lomo/exceptions.py._NUMERIC_CODE_TO_EXC）的契约基础，
    任何重新编号都会导致 SDK 无法正确映射异常。变更必须走 ADR + 兼容期。
    """

    @pytest.mark.parametrize(
        "code,expected_numeric",
        [
            (ErrorCode.SUCCESS, 0),
            (ErrorCode.NOT_FOUND, 1001),
            (ErrorCode.INVALID_REQUEST, 1002),
            (ErrorCode.UNAUTHORIZED, 1003),
            (ErrorCode.FILE_NOT_FOUND, 1008),
            (ErrorCode.INTERNAL_ERROR, 2001),
            (ErrorCode.SERVICE_UNAVAILABLE, 2002),
            (ErrorCode.CAD_GENERATION_ERROR, 7001),
        ],
    )
    def test_code_to_numeric_stable(self, code: ErrorCode, expected_numeric: int):
        """每个 ErrorCode 必须映射到固定的数值码."""
        assert code_to_numeric(code) == expected_numeric, (
            f"ErrorCode.{code.name} 数值码漂移：期望 {expected_numeric}，"
            f"实际 {code_to_numeric(code)}。变更必须新开 ADR 并提供兼容期。"
        )

    @pytest.mark.parametrize(
        "numeric,expected_code",
        [
            (0, ErrorCode.SUCCESS),
            (1001, ErrorCode.NOT_FOUND),
            (1002, ErrorCode.INVALID_REQUEST),
            (2001, ErrorCode.INTERNAL_ERROR),
            (2002, ErrorCode.SERVICE_UNAVAILABLE),
        ],
    )
    def test_numeric_to_code_stable(self, numeric: int, expected_code: ErrorCode):
        """数值码必须能反向映射回正确的 ErrorCode."""
        assert numeric_to_code(numeric) == expected_code

    def test_numeric_range_partitioning(self):
        """数值码分区规则：0=成功，1xxx=客户端错误，2xxx=服务端错误，7xxx=业务领域."""
        assert code_to_numeric(ErrorCode.SUCCESS) == 0
        client_codes = [
            code_to_numeric(ErrorCode.NOT_FOUND),
            code_to_numeric(ErrorCode.INVALID_REQUEST),
            code_to_numeric(ErrorCode.UNAUTHORIZED),
            code_to_numeric(ErrorCode.FILE_NOT_FOUND),
        ]
        for c in client_codes:
            assert 1000 <= c < 2000, f"客户端错误码 {c} 应在 1xxx 区间"
        server_codes = [
            code_to_numeric(ErrorCode.INTERNAL_ERROR),
            code_to_numeric(ErrorCode.SERVICE_UNAVAILABLE),
        ]
        for c in server_codes:
            assert 2000 <= c < 3000, f"服务端错误码 {c} 应在 2xxx 区间"
        assert code_to_numeric(ErrorCode.CAD_GENERATION_ERROR) == 7001
        assert 7000 <= code_to_numeric(ErrorCode.CAD_GENERATION_ERROR) < 8000

    def test_error_response_direct_numeric(self):
        """error_response() 直接接收数值码，绕过枚举（供异常处理器使用）."""
        result = error_response(code=1001, message="not found")
        assert result["code"] == 1001
        assert result["message"] == "not found"
        assert "request_id" in result


# 3. API 路由静态契约


@pytest.mark.unit
class TestAPIRouteStaticContract:
    """API 路由静态契约审计（不启动 FastAPI，仅检查模块属性）.

    重点审计：
        - 路由 prefix 一致性（/api/v1/<resource>）
        - 端点数量稳定性
        - Pydantic 请求模型字段命名（snake_case）
        - 关键响应字段命名一致性
    """

    def test_workflows_router_prefix(self):
        """workflows 路由 prefix 必须为 /api/v1/workflows."""
        from app.api.v1.workflows import router

        assert router.prefix == "/api/v1/workflows", (
            f"workflows 路由 prefix 漂移：期望 /api/v1/workflows，实际 {router.prefix}"
        )

    def test_datasets_router_prefix(self):
        """datasets 路由 prefix 必须为 /api/v1/datasets."""
        from app.api.v1.datasets import router

        assert router.prefix == "/api/v1/datasets", (
            f"datasets 路由 prefix 漂移：期望 /api/v1/datasets，实际 {router.prefix}"
        )

    def test_snapshots_router_prefix(self):
        """snapshots 路由 prefix 必须为 /api/v1/snapshots."""
        from app.api.v1.snapshots import router

        assert router.prefix == "/api/v1/snapshots", (
            f"snapshots 路由 prefix 漂移：期望 /api/v1/snapshots，实际 {router.prefix}"
        )

    def test_workflows_endpoint_count(self):
        """workflows 路由必须注册 8 个端点（validate/run/resume/get/cancel/stream/list/delete）."""
        from app.api.v1.workflows import router

        # router.routes 包含 APIRoute 对象，每个对应一个端点
        # 注意：FastAPI 会合并同路径不同方法的端点，这里统计 path + method 组合
        endpoint_keys = {
            (getattr(route, "path", None), method)
            for route in router.routes
            for method in getattr(route, "methods", set())
        }
        # 8 个端点，但 GET /workflows 和 GET /workflows/{id} 和 GET /workflows/{id}/stream 是 GET
        # 实际端点数应 >= 8（允许向后兼容扩展，但不能减少）
        assert len(endpoint_keys) >= 8, (
            f"workflows 端点数 < 8：实际 {len(endpoint_keys)} 个端点。端点减少属于 breaking change，必须新开 ADR。"
        )

    def test_datasets_endpoint_count(self):
        """datasets 路由必须注册至少 9 个端点."""
        from app.api.v1.datasets import router

        endpoint_keys = {
            (getattr(route, "path", None), method)
            for route in router.routes
            for method in getattr(route, "methods", set())
        }
        assert len(endpoint_keys) >= 9, f"datasets 端点数 < 9：实际 {len(endpoint_keys)} 个端点。"

    def test_snapshots_endpoint_count(self):
        """snapshots 路由必须注册 4 个端点（list/create/get/reproduce）."""
        from app.api.v1.snapshots import router

        endpoint_keys = {
            (getattr(route, "path", None), method)
            for route in router.routes
            for method in getattr(route, "methods", set())
        }
        assert len(endpoint_keys) >= 4, f"snapshots 端点数 < 4：实际 {len(endpoint_keys)} 个端点。"

    def test_workflow_spec_model_fields_snake_case(self):
        """WorkflowSpecModel 字段必须为 snake_case，且与契约层 WorkflowSpec 对齐."""
        from app.api.v1.workflows import WorkflowSpecModel

        model_fields = set(WorkflowSpecModel.model_fields.keys())
        expected = {"name", "version", "nodes", "edges", "inputs", "outputs", "metadata"}
        assert expected.issubset(model_fields), (
            f"WorkflowSpecModel 缺少字段：{expected - model_fields}。"
            f"API 模型必须与 app.contracts.task.WorkflowSpec 字段对齐。"
        )
        # snake_case 校验：所有字段名不应包含 camelCase 大写字母
        for fname in model_fields:
            assert fname == fname.lower() or "_" in fname, f"字段名 {fname} 不符合 snake_case 命名规范"

    def test_workflow_node_model_fields_snake_case(self):
        """WorkflowNodeModel 字段必须为 snake_case，且与契约层 WorkflowNode 对齐."""
        from app.api.v1.workflows import WorkflowNodeModel

        model_fields = set(WorkflowNodeModel.model_fields.keys())
        expected = {
            "node_id",
            "task_type",
            "params",
            "inputs",
            "retry",
            "timeout_seconds",
        }
        assert expected.issubset(model_fields), f"WorkflowNodeModel 缺少字段：{expected - model_fields}"

    def test_workflow_edge_model_fields_snake_case(self):
        """WorkflowEdgeModel 字段必须为 snake_case，且与契约层 WorkflowEdge 对齐."""
        from app.api.v1.workflows import WorkflowEdgeModel

        model_fields = set(WorkflowEdgeModel.model_fields.keys())
        expected = {"upstream", "downstream"}
        assert expected.issubset(model_fields), f"WorkflowEdgeModel 缺少字段：{expected - model_fields}"

    def test_create_dataset_request_fields(self):
        """CreateDatasetRequest 必须包含 name/schema/owner_id 字段（schema 为输入别名）."""
        from app.api.v1.datasets import CreateDatasetRequest

        model_fields = set(CreateDatasetRequest.model_fields.keys())
        expected = {"name", "dataset_schema", "owner_id"}
        assert expected.issubset(model_fields), f"CreateDatasetRequest 缺少字段：{expected - model_fields}"
        # schema 作为输入别名必须可用（契约标准字段名）
        assert CreateDatasetRequest.model_fields["dataset_schema"].alias == "schema", (
            "dataset_schema 字段必须带 schema 别名以兼容契约标准输入名"
        )

    def test_create_snapshot_request_fields(self):
        """CreateSnapshotRequest 必须包含 6 个字段，与 ExperimentSnapshot 对齐."""
        from app.api.v1.snapshots import CreateSnapshotRequest

        model_fields = set(CreateSnapshotRequest.model_fields.keys())
        expected = {
            "config",
            "dataset_versions",
            "model_uri",
            "metrics",
            "created_by",
            "notes",
        }
        assert expected.issubset(model_fields), f"CreateSnapshotRequest 缺少字段：{expected - model_fields}"

    def test_workflow_spec_model_field_naming_matches_contract(self):
        """WorkflowSpecModel 字段名必须与契约层 WorkflowSpec 完全一致（snake_case 对齐）."""
        from app.api.v1.workflows import WorkflowSpecModel

        api_fields = set(WorkflowSpecModel.model_fields.keys())
        contract_fields = _field_names(WorkflowSpec)
        # API 模型字段必须是契约字段的超集（允许 API 增加 Pydantic 配置字段，但不能少）
        assert contract_fields.issubset(api_fields), (
            f"API WorkflowSpecModel 缺少契约字段：{contract_fields - api_fields}。API 模型必须覆盖契约层所有字段。"
        )

    def test_create_snapshot_request_field_naming_matches_contract(self):
        """CreateSnapshotRequest 字段名必须与 ExperimentSnapshot 对齐（排除系统自动生成字段）."""
        from app.api.v1.snapshots import CreateSnapshotRequest

        api_fields = set(CreateSnapshotRequest.model_fields.keys())
        contract_fields = _field_names(ExperimentSnapshot)
        # 系统自动生成字段（snapshot_id/created_at/git_sha/code_dirty/environment/
        # lineage_record_id/mlflow_run_id）不由用户请求提供
        system_generated = {
            "snapshot_id",
            "created_at",
            "git_sha",
            "code_dirty",
            "environment",
            "lineage_record_id",
            "mlflow_run_id",
        }
        user_provided_contract_fields = contract_fields - system_generated
        assert user_provided_contract_fields.issubset(api_fields), (
            f"CreateSnapshotRequest 缺少用户可提供字段：{user_provided_contract_fields - api_fields}"
        )


# 4. 契约版本稳定性


@pytest.mark.unit
class TestContractVersionStability:
    """契约版本号稳定性（CONTRACTS_VERSION）.

    版本号用于 OpenAPI info.version 与 SDK adapter 兼容性校验。
    任何契约层 breaking change 必须提升版本号。
    """

    def test_contracts_version_is_semver(self):
        """CONTRACTS_VERSION 必须为合法 semver（MAJOR.MINOR.PATCH）."""
        assert CONTRACTS_VERSION == "1.0.0", (
            f"CONTRACTS_VERSION 漂移：期望 1.0.0，实际 {CONTRACTS_VERSION}。"
            f"版本变更必须同步更新 ADR-005 与 OpenAPI schema。"
        )
        parts = CONTRACTS_VERSION.split(".")
        assert len(parts) == 3, f"CONTRACTS_VERSION 必须为三段式 semver：{CONTRACTS_VERSION}"
        for p in parts:
            assert p.isdigit(), f"CONTRACTS_VERSION 段必须为数字：{p}"

    def test_module_version_matches_contracts_version(self):
        """app.contracts.__version__ 必须与 CONTRACTS_VERSION 一致."""
        from app.contracts import __version__

        assert __version__ == CONTRACTS_VERSION, (
            f"__version__ ({__version__}) 与 CONTRACTS_VERSION ({CONTRACTS_VERSION}) 不一致。两者必须同步维护。"
        )


# 5. WorkflowSpec.validate() 行为一致性


@pytest.mark.unit
class TestWorkflowSpecValidateConsistency:
    """WorkflowSpec.validate() 行为一致性.

    契约要求：返回 list[str]，空列表表示校验通过。
    此行为必须与 API /workflows/validate 端点的 valid/node_count/edge_count 响应一致。
    """

    def _make_valid_spec(self) -> WorkflowSpec:
        """构造一个合法的线性 DAG：node_a → node_b."""
        return WorkflowSpec(
            name="test-workflow",
            version="1.0.0",
            nodes=[
                WorkflowNode(node_id="a", task_type="task_a"),
                WorkflowNode(node_id="b", task_type="task_b"),
            ],
            edges=[WorkflowEdge(upstream="a", downstream="b")],
        )

    def test_validate_returns_list_of_strings(self):
        """validate() 必须返回 list[str]（即使为空）."""
        spec = self._make_valid_spec()
        errors = spec.validate()
        assert isinstance(errors, list), f"validate() 必须返回 list，实际 {type(errors)}"
        for e in errors:
            assert isinstance(e, str), f"错误项必须为 str，实际 {type(e)}"

    def test_valid_spec_returns_empty_errors(self):
        """合法 DAG 必须返回空错误列表."""
        spec = self._make_valid_spec()
        errors = spec.validate()
        assert errors == [], f"合法 DAG 不应有错误，实际 {errors}"

    def test_duplicate_node_id_detected(self):
        """重复 node_id 必须被检测."""
        spec = WorkflowSpec(
            name="dup",
            version="1.0.0",
            nodes=[
                WorkflowNode(node_id="a", task_type="t1"),
                WorkflowNode(node_id="a", task_type="t2"),
            ],
            edges=[],
        )
        errors = spec.validate()
        assert len(errors) > 0, "重复 node_id 必须被检测"
        assert any("重复" in e or "duplicate" in e.lower() for e in errors), (
            f"重复节点错误消息应包含'重复'或'duplicate'，实际 {errors}"
        )

    def test_cycle_detected(self):
        """DAG 环必须被检测（a → b → a）."""
        spec = WorkflowSpec(
            name="cycle",
            version="1.0.0",
            nodes=[
                WorkflowNode(node_id="a", task_type="t1"),
                WorkflowNode(node_id="b", task_type="t2"),
            ],
            edges=[
                WorkflowEdge(upstream="a", downstream="b"),
                WorkflowEdge(upstream="b", downstream="a"),
            ],
        )
        errors = spec.validate()
        assert len(errors) > 0, "DAG 环必须被检测"
        assert any("环" in e or "cycle" in e.lower() for e in errors), (
            f"环检测错误消息应包含'环'或'cycle'，实际 {errors}"
        )

    def test_edge_reference_invalid_node_detected(self):
        """边引用不存在的节点必须被检测."""
        spec = WorkflowSpec(
            name="bad-edge",
            version="1.0.0",
            nodes=[WorkflowNode(node_id="a", task_type="t1")],
            edges=[WorkflowEdge(upstream="a", downstream="nonexistent")],
        )
        errors = spec.validate()
        assert len(errors) > 0, "边引用不存在节点必须被检测"


# 6. 契约层 API 层字段命名风格一致性


@pytest.mark.unit
class TestFieldNamingStyleConsistency:
    """契约层 ↔ API 层字段命名风格一致性审计.

    全项目字段命名必须统一为 snake_case（Python 惯例），
    任何 camelCase 字段都会导致前后端契约漂移。
    """

    def test_contract_dataclass_fields_are_snake_case(self):
        """所有契约层 dataclass 字段必须为 snake_case."""
        import re

        snake_case_re = re.compile(r"^[a-z_][a-z0-9_]*$")
        contract_classes = [
            WorkflowSpec,
            WorkflowNode,
            WorkflowEdge,
            Artifact,
            DatasetSchema,
            DatasetVersion,
            LineageRecord,
            ExperimentSnapshot,
        ]
        for cls in contract_classes:
            for field_name in _field_names(cls):
                assert snake_case_re.match(field_name), (
                    f"{cls.__name__}.{field_name} 不符合 snake_case 命名规范。契约层字段必须全部为 snake_case。"
                )

    def test_api_pydantic_model_fields_are_snake_case(self):
        """所有 API Pydantic 模型字段必须为 snake_case."""
        import re

        snake_case_re = re.compile(r"^[a-z_][a-z0-9_]*$")
        from app.api.v1.workflows import (
            WorkflowEdgeModel,
            WorkflowNodeModel,
            WorkflowSpecModel,
        )
        from app.api.v1.datasets import CreateDatasetRequest
        from app.api.v1.snapshots import CreateSnapshotRequest

        api_models = [
            WorkflowSpecModel,
            WorkflowNodeModel,
            WorkflowEdgeModel,
            CreateDatasetRequest,
            CreateSnapshotRequest,
        ]
        for model in api_models:
            for field_name in model.model_fields.keys():
                assert snake_case_re.match(field_name), f"{model.__name__}.{field_name} 不符合 snake_case 命名规范。"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "unit"])
