"""postprocessor 方言声明化子系统。

对应 docs/development/postprocessor-方言声明化设计.md §3：
方言 = 声明（dialect.yaml）+ 模板（Jinja2）+ 可选代码钩子。

模块划分：
- :mod:`declaration` — 声明模型与 YAML 加载校验
- :mod:`compiler` — 声明 + 模板 → BasePostProcessor 子类
- :mod:`registry` — 本地插件目录扫描 + 注册到 PostProcessorRegistry
"""

from __future__ import annotations

from app.postprocessor.dialect.declaration import (
    ALLOWED_TEMPLATE_METHODS,
    BUILTIN_BASE_DIALECTS,
    DIALECT_SCHEMA_VERSION,
    DialectDeclaration,
    DialectDeclarationError,
)
from app.postprocessor.dialect.compiler import DialectCompileError, DialectCompiler
from app.postprocessor.dialect.registry import (
    DEFAULT_DIALECT_PLUGIN_DIR,
    DialectRegistry,
    load_dialects,
)

__all__ = [
    # declaration
    "ALLOWED_TEMPLATE_METHODS",
    "BUILTIN_BASE_DIALECTS",
    "DIALECT_SCHEMA_VERSION",
    "DialectDeclaration",
    "DialectDeclarationError",
    # compiler
    "DialectCompileError",
    "DialectCompiler",
    # registry
    "DEFAULT_DIALECT_PLUGIN_DIR",
    "DialectRegistry",
    "load_dialects",
]

from app.postprocessor.dialect._lifecycle import (
    DialectLifecycleStage,
    can_transition,
    assert_transition_allowed,
    next_stage_after_success,
    next_stage_after_failure,
    can_discover,
    is_terminal,
)
