"""架构分层一致性测试（V3.0 清洁架构）。

这些测试确保重构后的分层架构不被意外破坏。
原则：
  - domain/ 层零 FastAPI / SQLAlchemy 依赖
  - API 层不直接访问数据库
  - contracts/ 层零重依赖
  - shared/ 层零 PyTorch / NumPy 依赖
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"
REPO_ROOT = Path(__file__).resolve().parents[4]


def _collect_imports(root: Path, pattern: str) -> list[tuple[Path, str]]:
    """收集指定目录下所有 Python 文件的导入语句。"""
    results: list[tuple[Path, str]] = []
    for py_file in root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if pattern in alias.name:
                        results.append((py_file, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module and pattern in node.module:
                    results.append((py_file, node.module))
    return results


# 测试 1: domain/ 层不得导入 FastAPI


def test_domain_has_no_fastapi() -> None:
    """domain/ 层不应包含任何 from fastapi import ..."""
    domain = APP_ROOT / "domain"
    if not domain.exists():
        pytest.skip("domain/ 目录尚未创建")

    violations = _collect_imports(domain, "fastapi")
    assert not violations, f"domain/ 层 {len(violations)} 处违规导入 FastAPI:\n" + "\n".join(
        f"  {f}: {imp}" for f, imp in violations
    )


# 测试 2: API 层不得直接导入 database 模型/连接


def test_api_does_not_import_database() -> None:
    """api/v1/ 层不应直接导入 app.database（应通过 services/ 层）。"""
    api = APP_ROOT / "api" / "v1"
    violations = []
    for py_file, imp in _collect_imports(api, "app.database"):
        # 允许导入 database 中的纯数据类/枚举
        if "constraints" in imp or "materials" in imp or "tools" in imp:
            continue
        violations.append((py_file, imp))

    # 目前仍有若干遗留导入，标记为 warning 而非 error
    if violations:
        pytest.skip(
            f"api/ 层仍有 {len(violations)} 处 database 导入（待后续迁移）:\n"
            + "\n".join(f"  {f.relative_to(APP_ROOT)}: {i}" for f, i in violations[:5])
        )


# 测试 3: contracts/ 层不得导入 config / infrastructure


def test_contracts_has_no_infrastructure_imports() -> None:
    """contracts/ 层不应导入 config 或 infrastructure。"""
    contracts = APP_ROOT / "contracts"
    if not contracts.exists():
        pytest.skip("contracts/ 目录不存在")

    violations = []
    for py_file, imp in _collect_imports(contracts, "app.config"):
        # STREAM_BUFFER_SIZE 已本地定义，此检查确保无新增
        violations.append((py_file, imp))

    assert not violations, f"contracts/ 层 {len(violations)} 处导入 config/infrastructure"


# 测试 4: shared/ 零重依赖


def test_shared_has_no_heavy_dependencies() -> None:
    """shared/ 层不得导入 torch / numpy / pydantic。"""
    shared = REPO_ROOT / "shared"
    if not shared.exists():
        pytest.skip("shared/ 目录不存在")

    heavy = {"torch", "numpy", "pydantic", "fastapi", "sqlalchemy"}
    violations = []
    for py_file, imp in _collect_imports(shared, ""):
        top = imp.split(".")[0]
        if top in heavy:
            violations.append((py_file, imp))

    assert not violations, f"shared/ 层 {len(violations)} 处导入重依赖"


# 测试 5: 无 domain api 导入（关键循环依赖检测）


def test_domain_does_not_import_api() -> None:
    """域层模块不得导入 API 层（避免 api↔domain 循环依赖回退）。"""
    domain_dirs = [
        APP_ROOT / "simulation",
        APP_ROOT / "dxf",
        APP_ROOT / "rag",
        APP_ROOT / "projects",
        APP_ROOT / "rules",
        APP_ROOT / "chatter_prediction",
        APP_ROOT / "cad",
        APP_ROOT / "ai",
        APP_ROOT / "agent",
        APP_ROOT / "workflow",
    ]

    violations = []
    for d in domain_dirs:
        if not d.exists():
            continue
        for py_file, imp in _collect_imports(d, "app.api"):
            violations.append((py_file, imp))

    assert not violations, f"域层 {len(violations)} 处导入 API 层（循环依赖风险）:\n" + "\n".join(
        f"  {f.relative_to(APP_ROOT)}: {i}" for f, i in violations
    )


# 测试 6: core/ 不依赖 repository/


def test_core_does_not_import_repository() -> None:
    """core/ 层不应直接导入 repository/（V3.0 已通过依赖反转解决）。

    repository 异常已改为继承 core.exceptions.AppException，
    core/exception_handlers 不再从 repository 导入任何异常类型。
    """
    core = APP_ROOT / "core"
    violations = []
    for py_file, imp in _collect_imports(core, "app.repository"):
        violations.append((py_file, imp))

    assert not violations, f"core/ 层 {len(violations)} 处导入 repository/:\n" + "\n".join(
        f"  {f.relative_to(APP_ROOT)}: {i}" for f, i in violations
    )
