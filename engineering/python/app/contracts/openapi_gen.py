"""OpenAPI 生成与一致性校验脚本。

对应 core-contracts-design.md 第 9 章。

设计目标：
- 从契约层（app/contracts/*.py）的 dataclass 反射生成 OpenAPI schema
- 导出 openapi.json 到 docs/api/openapi.json
- CI 强制校验：openapi.json 与代码一致

为什么不直接用 FastAPI 自动生成？
- 阶段 0 不接入业务路由，FastAPI 应用尚未引用契约模型
- 契约层使用 dataclass（无 Pydantic 依赖），便于生态扩展
- 本脚本作为契约层的"独立 schema 源"，业务路由的 OpenAPI 由 FastAPI 单独生成

CLI 用法：
    python -m app.contracts.openapi_gen export [--output PATH]
    python -m app.contracts.openapi_gen verify [--path PATH]
    python -m app.contracts.openapi_gen print

稳定性承诺：本文件为 Stable 契约 v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import field, fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from app.contracts import (
    CONTRACTS_VERSION,
    Artifact,
    ConfigField,
    ConfigSpec,
    DatasetSchema,
    DatasetVersion,
    ExperimentSnapshot,
    ExtensionPointContribution,
    LineageRecord,
    LogEntry,
    LogLevel,
    Metric,
    PluginManifest,
    TaskContext,
    TaskProgress,
    TaskResult,
    TaskStatus,
    TraceSpan,
    WorkflowEdge,
    WorkflowEvent,
    WorkflowNode,
    WorkflowSpec,
    # 世界模型契约（ADR-017 阶段 8）
    ActionField,
    StateField,
    TrajectoryMetrics,
    TrajectoryStep,
    WorldModelInfo,
    WorldModelPredictRequest,
    WorldModelPredictResponse,
    WorldModelVersion,
    # RL Agent 契约（ADR-017 阶段 8）
    ActionEvaluation,
    OptimizationTarget,
    PolicyAlgorithm,
    PolicyInfo,
    PolicyVersion,
    RecommendedAction,
    RLActRequest,
    RLActResponse,
    SafetyConstraintsSpec,
    TrainingMetricsSnapshot,
    TrainingStartRequest,
    TrainingStatus,
    TrainingStatusInfo,
    # 可解释性契约（ADR-016 阶段 7）
    ComparisonType,
    ConfidenceExplanation,
    CounterfactualExplanation,
    ExplanationComparison,
    ExplanationRecord,
    ExplanationRequest,
    ExplanationType,
    GateDynamicsExplanation,
    HiddenStateExplanation,
    ProjectionMethod,
)


# OpenAPI 元信息

OPENAPI_TITLE = "灵境制造 核心架构契约 API"
OPENAPI_DESCRIPTION = (
    "八大核心契约（Task/Workflow、Dataset/Version/Lineage、Plugin/ExtensionPoint、"
    "ConfigSpec、Observability、WorldModel、RLAgent、Explainability）的 OpenAPI schema。"
    "本 schema 由 app/contracts/openapi_gen.py 自动生成，请勿手动修改。"
)
OPENAPI_VERSION = CONTRACTS_VERSION  # 与契约版本同步


# Python 类型 JSON Schema 转换

# 基本类型映射
_BASIC_TYPE_MAP = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    Any: {},
}


def _python_type_to_schema(tp: Any) -> dict[str, Any]:
    """把 Python type hint 转 JSON schema 片段。"""
    # 基本类型
    if tp in _BASIC_TYPE_MAP:
        return dict(_BASIC_TYPE_MAP[tp])

    # Optional / Union
    origin = get_origin(tp)
    if origin is Union:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            schema = _python_type_to_schema(args[0])
            schema["nullable"] = True
            return schema
        return {"oneOf": [_python_type_to_schema(a) for a in args]}

    # list[X]
    if origin is list:
        args = list(get_args(tp))
        items = _python_type_to_schema(args[0]) if args else {}
        return {"type": "array", "items": items}

    # dict[K, V]
    if origin is dict:
        return {"type": "object", "additionalProperties": True}

    # Enum
    if isinstance(tp, type) and issubclass(tp, Enum):
        # str enum string + enum
        if issubclass(tp, str):
            return {
                "type": "string",
                "enum": [e.value for e in tp],
            }
        if issubclass(tp, int):
            return {
                "type": "integer",
                "enum": [e.value for e in tp],
            }
        return {"enum": [e.value for e in tp]}

    # datetime
    if tp is datetime:
        return {"type": "string", "format": "date-time"}

    # 嵌套 dataclass $ref
    if isinstance(tp, type) and is_dataclass(tp):
        ref_name = tp.__name__
        return {"$ref": f"#/components/schemas/{ref_name}"}

    # 兜底
    return {}


def _dataclass_to_schema(cls: type) -> dict[str, Any]:
    """把 dataclass 转 OpenAPI schema 对象。"""
    if not is_dataclass(cls):
        raise ValueError(f"{cls} is not a dataclass")

    hints = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for f in fields(cls):
        tp = hints.get(f.name, f.type)
        prop_schema = _python_type_to_schema(tp)
        properties[f.name] = prop_schema
        # 判断 required：无 default 且无 default_factory
        if f.default is field and f.default_factory is field:  # dataclasses.MISSING
            required.append(f.name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


# 契约 schema 注册


def _collect_contract_dataclasses() -> list[type]:
    """收集所有需要导出 schema 的 dataclass。"""
    return [
        # 任务契约
        Artifact,
        TaskContext,
        TaskResult,
        TaskProgress,
        WorkflowNode,
        WorkflowEdge,
        WorkflowSpec,
        WorkflowEvent,
        # 数据契约
        DatasetSchema,
        DatasetVersion,
        LineageRecord,
        # 插件契约
        PluginManifest,
        ExtensionPointContribution,
        # 配置契约
        ConfigField,
        ConfigSpec,
        # 可观测契约
        TraceSpan,
        Metric,
        LogEntry,
        ExperimentSnapshot,
        # 世界模型契约（ADR-017 阶段 8）
        WorldModelPredictRequest,
        TrajectoryStep,
        TrajectoryMetrics,
        WorldModelInfo,
        WorldModelPredictResponse,
        WorldModelVersion,
        # RL Agent 契约（ADR-017 阶段 8）
        SafetyConstraintsSpec,
        RLActRequest,
        ActionEvaluation,
        PolicyInfo,
        RecommendedAction,
        RLActResponse,
        PolicyVersion,
        TrainingMetricsSnapshot,
        TrainingStatusInfo,
        TrainingStartRequest,
        # 可解释性契约（ADR-016 阶段 7）
        HiddenStateExplanation,
        GateDynamicsExplanation,
        CounterfactualExplanation,
        ConfidenceExplanation,
        ExplanationRequest,
        ExplanationRecord,
        ExplanationComparison,
    ]


def _collect_enum_types() -> list[type]:
    """收集所有需要导出 schema 的 Enum。"""
    return [
        TaskStatus,
        LogLevel,
    ]


def _collect_constant_class_enums() -> list[type]:
    """收集常量类（非 Enum 但提供 ``all()`` classmethod 的类）.

    这些类（如 ``ExplanationType`` / ``StateField`` / ``OptimizationTarget``）
    不是 ``Enum`` 子类，但通过 ``all()`` classmethod 返回所有合法字符串值。
    本函数把它们导出为 ``{"type": "string", "enum": [...]}`` schema，
    与真正的 Enum 在 OpenAPI 输出中保持一致的表现形式。
    """
    return [
        # 世界模型契约
        StateField,
        ActionField,
        # RL Agent 契约
        OptimizationTarget,
        PolicyAlgorithm,
        TrainingStatus,
        # 可解释性契约
        ExplanationType,
        ProjectionMethod,
        ComparisonType,
    ]


def _constant_class_to_enum_schema(cls: type) -> dict[str, Any]:
    """把常量类（带 ``all()`` classmethod）转为 ``string + enum`` schema.

    Args:
        cls: 常量类（如 ``ExplanationType``）。

    Returns:
        OpenAPI schema 片段，形如 ``{"type": "string", "enum": [...]}``。

    Raises:
        ValueError: 类没有 ``all`` classmethod 或 ``all()`` 返回非 list[str]。
    """
    all_method = getattr(cls, "all", None)
    if all_method is None or not callable(all_method):
        raise ValueError(f"{cls.__name__} 没有 all() classmethod，无法转为 enum schema")
    values = all_method()
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f"{cls.__name__}.all() 必须返回 list[str]，实际返回: {type(values)}")
    return {"type": "string", "enum": values}


def build_contracts_schema() -> dict[str, Any]:
    """构建完整的契约 OpenAPI schema。

    Returns:
        OpenAPI 3.0 dict，包含 8 大契约的 schema 定义。
    """
    components_schemas: dict[str, Any] = {}

    # Enum schema（真正的 Enum 子类）
    for enum_cls in _collect_enum_types():
        components_schemas[enum_cls.__name__] = _python_type_to_schema(enum_cls)

    # 常量类 schema（非 Enum 但提供 all() classmethod 的类）
    for const_cls in _collect_constant_class_enums():
        components_schemas[const_cls.__name__] = _constant_class_to_enum_schema(const_cls)

    # Dataclass schema
    for cls in _collect_contract_dataclasses():
        components_schemas[cls.__name__] = _dataclass_to_schema(cls)

    openapi: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {
            "title": OPENAPI_TITLE,
            "description": OPENAPI_DESCRIPTION,
            "version": OPENAPI_VERSION,
            "license": {"name": "MIT"},
        },
        "paths": {},
        "components": {
            "schemas": components_schemas,
        },
        # 标注生成来源，便于 CI 校验
        "x-generated-by": "app.contracts.openapi_gen",
        "x-contracts-version": CONTRACTS_VERSION,
    }
    return openapi


# 导出与校验


DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi.json"


def export_openapi(output: Union[str, Path] | None = None) -> Path:
    """导出 OpenAPI schema 到文件，返回写入路径。"""
    path = Path(output) if output else DEFAULT_OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = build_contracts_schema()
    with path.open("w", encoding="utf-8") as f:
        json.dump(schema, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")
    return path


def verify_openapi(path: Union[str, Path] | None = None) -> tuple[bool, str]:
    """校验现有 openapi.json 与代码生成的 schema 一致。

    Returns:
        (一致, 说明信息)
    """
    target = Path(path) if path else DEFAULT_OUTPUT_PATH
    if not target.exists():
        return False, f"OpenAPI file not found: {target}"
    try:
        with target.open("r", encoding="utf-8") as f:
            existing = json.load(f)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON in {target}: {e}"

    generated = build_contracts_schema()

    # 仅校验 components.schemas 部分（info.version 可能由 git tag 注入，不强校验）
    existing_schemas = existing.get("components", {}).get("schemas", {})
    generated_schemas = generated.get("components", {}).get("schemas", {})

    if existing_schemas == generated_schemas:
        return True, "OpenAPI schemas consistent."
    # 找出差异
    existing_keys = set(existing_schemas.keys())
    generated_keys = set(generated_schemas.keys())
    missing = generated_keys - existing_keys
    extra = existing_keys - generated_keys
    diffs: list[str] = []
    if missing:
        diffs.append(f"missing schemas: {sorted(missing)}")
    if extra:
        diffs.append(f"extra schemas: {sorted(extra)}")
    # 内容差异
    common = existing_keys & generated_keys
    content_diffs = [name for name in sorted(common) if existing_schemas[name] != generated_schemas[name]]
    if content_diffs:
        diffs.append(f"content changed: {content_diffs}")
    return False, "OpenAPI schemas inconsistent: " + "; ".join(diffs)


# CLI


def _cmd_export(args: argparse.Namespace) -> int:
    path = export_openapi(args.output)
    print(f"[openapi_gen] Exported to: {path}", file=sys.stderr)
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    ok, msg = verify_openapi(args.path)
    if ok:
        print(f"[openapi_gen] OK: {msg}", file=sys.stderr)
        return 0
    print(f"[openapi_gen] FAIL: {msg}", file=sys.stderr)
    return 1


def _cmd_print(args: argparse.Namespace) -> int:
    schema = build_contracts_schema()
    print(json.dumps(schema, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.contracts.openapi_gen",
        description="灵境制造核心契约 OpenAPI 生成与校验",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_export = sub.add_parser("export", help="导出 openapi.json")
    p_export.add_argument("--output", "-o", default=None, help="输出路径（默认 docs/api/openapi.json）")
    p_export.set_defaults(func=_cmd_export)

    p_verify = sub.add_parser("verify", help="校验 openapi.json 与代码一致")
    p_verify.add_argument("--path", "-p", default=None, help="校验路径")
    p_verify.set_defaults(func=_cmd_verify)

    p_print = sub.add_parser("print", help="打印 schema 到 stdout")
    p_print.set_defaults(func=_cmd_print)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "OPENAPI_TITLE",
    "OPENAPI_VERSION",
    "DEFAULT_OUTPUT_PATH",
    "build_contracts_schema",
    "export_openapi",
    "verify_openapi",
    "main",
]
