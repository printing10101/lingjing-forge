"""技能代码编译 Mixin。

从原 ``app.plugins.skill_loader.loader`` 拆分而来，提供：
- 代码安全审计（:meth:`_audit_code_security`）
- RestrictedPython 编译与执行（:meth:`_compile_code`）
- 备用编译路径（:meth:`_compile_code_in_process`）
- 可调用对象提取（:meth:`_extract_callable`）
- 内容哈希计算（:meth:`_compute_content_hash`）

被 :class:`SkillLoader` 通过多继承组合使用，依赖 :class:`SandboxExecutorMixin`
提供的 ``_USE_SUBPROCESS_ISOLATION`` / ``_SUBPROCESS_TIMEOUT_SEC`` 类属性。
"""

from __future__ import annotations

import hashlib

import logging
import re
import time
from typing import Any
from collections.abc import Callable

from .sandbox_executor import SecurityError, _SubprocessSkillExecutor

logger = logging.getLogger(__name__)


class SkillCompilerMixin:
    # 宿主契约：由兄弟 mixin 提供（SandboxExecutorMixin）
    _USE_SUBPROCESS_ISOLATION: bool
    _SUBPROCESS_TIMEOUT_SEC: float
    """技能代码编译 Mixin。

    依赖宿主类组合 :class:`SandboxExecutorMixin` 提供的 subprocess 隔离配置项。
    """

    _SAFE_BUILTINS = {
        "True": True,
        "False": False,
        "None": None,
        "bool": bool,
        "float": float,
        "int": int,
        "str": str,
        "abs": abs,
        "divmod": divmod,
        "max": max,
        "min": min,
        "pow": pow,
        "round": round,
        "sum": sum,
        "all": all,
        "any": any,
        "enumerate": enumerate,
        "len": len,
        "list": list,
        "range": range,
        "sorted": sorted,
    }

    _FORBIDDEN_BUILTINS = frozenset(
        {
            "__import__",
            "exec",
            "eval",
            "compile",
            "open",
            "input",
            "breakpoint",
            "type",
            "vars",
            "dir",
            "getattr",
            "setattr",
            "delattr",
            "hasattr",
            "object",
            "super",
            "callable",
            "isinstance",
            "issubclass",
            "print",
            "memoryview",
            "property",
            "staticmethod",
            "classmethod",
            "ascii",
            "repr",
            "hash",
            "iter",
            "next",
            "map",
            "filter",
            "bytes",
            "bytearray",
        }
    )

    def _compile_code(self, code: str, skill_id: str) -> Callable | None:
        self._audit_code_security(code, skill_id)

        if self._USE_SUBPROCESS_ISOLATION:
            return _SubprocessSkillExecutor(
                code=code,
                skill_id=skill_id,
                timeout=self._SUBPROCESS_TIMEOUT_SEC,
            )

        try:
            from RestrictedPython import compile_restricted
            from RestrictedPython.Guards import (
                safe_builtins as rp_safe_builtins,
                guarded_iter_unpack_sequence,
            )
        except ImportError:
            logger.error(
                "RestrictedPython is required for secure skill execution but is not installed. "
                "Skill '%s' cannot be loaded. Install it with: pip install RestrictedPython",
                skill_id,
            )
            raise RuntimeError(
                f"Cannot load skill '{skill_id}': RestrictedPython is required for secure "
                "code execution but is not installed. Install it with: pip install RestrictedPython"
            )

        try:
            byte_code = compile_restricted(code, f"<skill:{skill_id}>", "exec")
        except SyntaxError as e:
            logger.error("Skill '%s' syntax error: %s", skill_id, e)
            return None

        restricted_globals = {
            "__builtins__": rp_safe_builtins,
            "_getattr_": getattr,
            "_write_": lambda x: None,
            "_getiter_": iter,
            "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
            "__name__": f"skill:{skill_id}",
            "__metaclass__": type,
        }

        try:
            exec(byte_code, restricted_globals)
        except (SyntaxError, NameError, AttributeError, TypeError, ValueError, RuntimeError, OSError) as e:
            logger.error(
                "Skill '%s' execution error: %s",
                skill_id,
                e,
                exc_info=True,
            )
            return None

        return self._extract_callable(restricted_globals, skill_id)

    def _compile_code_in_process(self, code: str, skill_id: str) -> Callable | None:
        """备用编译方法：当 RestrictedPython 不可用时使用受限 builtins。

        安全警告：此方法不提供与 compile_restricted 相同级别的安全保护，
        仅通过限制 __builtins__ 和 AST 审计来降低风险。

        P0 修复要求：
        - RestrictedPython 已列为生产强制依赖（requirements.txt），此降级路径
          仅在极端异常情况下触发（如 Python 版本不兼容 RestrictedPython）
        - 每次触发时记录 WARNING 日志 + 审计时间戳
        - 生产环境应在监控中对此 WARNING 设置告警
        """
        # P0 修复: 降级路径触发时必须显式警告，便于运维发现
        logger.warning(
            "SECURITY: RestrictedPython unavailable, using downgraded execution for skill '%s'. "
            "This is less secure. Install RestrictedPython: pip install RestrictedPython",
            skill_id,
        )

        # 安全修复 [P0-1]：降级路径同样必须经过 AST 审计，
        # 与主路径 _compile_code 保持一致的安全基线。
        # 防止攻击者通过直接调用本方法绕过 _audit_code_security。
        self._audit_code_security(code, skill_id)

        try:
            compiled = compile(code, f"<skill:{skill_id}>", "exec")
        except SyntaxError as e:
            logger.error("Skill '%s' syntax error: %s", skill_id, e)
            return None

        # 安全修复：使用受限的 builtins，移除危险函数
        namespace: dict[str, Any] = {"__builtins__": self._SAFE_BUILTINS}
        try:
            exec(compiled, namespace)
        except (SyntaxError, NameError, AttributeError, TypeError, ValueError, RuntimeError, OSError) as e:
            logger.error(
                "Skill '%s' execution error: %s",
                skill_id,
                e,
                exc_info=True,
            )
            return None

        # P0 修复: 记录降级路径使用，包含时间戳用于审计追踪
        logger.warning(
            "Skill '%s' executed via downgraded path at %s — audit this periodically",
            skill_id,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        return self._extract_callable(namespace, skill_id)

    def _extract_callable(self, namespace: dict[str, Any], skill_id: str) -> Callable | None:
        for name in ("execute", "run", "main", "handler"):
            if name in namespace and callable(namespace[name]):
                return namespace[name]

        if "SkillExecutor" in namespace:
            cls = namespace["SkillExecutor"]
            try:
                return cls()
            except (TypeError, ValueError, RuntimeError, OSError) as e:
                logger.debug(
                    "SkillExecutor instantiation failed, fallback to attribute scan: %s",
                    e,
                    exc_info=True,
                )

        for attr_name, attr_val in namespace.items():
            if not attr_name.startswith("_") and callable(attr_val):
                return attr_val

        return None

    @staticmethod
    def _audit_code_security(code: str, skill_id: str) -> None:
        dangerous_patterns: list[tuple] = [
            (r"__import__\s*\(", "直接调用 __import__"),
            # P1 安全修复：原负向预查 (?!...) 导致安全 import 被误报、危险 import 漏报。
            # 改为正向匹配危险模块，仅阻断 import os/sys/subprocess/ctypes/socket/shutil。
            # 防复发：禁止使用负向预查做安全审计，必须正向匹配危险项。
            (r"\bimport\b\s+(?:os|sys|subprocess|ctypes|socket|shutil)\b", "import 危险模块"),
            (r"\bexec\s*\(", "exec() 调用"),
            (r"\beval\s*\(", "eval() 调用"),
            (r"\bcompile\s*\(", "compile() 调用"),
            (r"\bopen\s*\(", "open() 调用"),
            (r"\binput\s*\(", "input() 调用"),
            (r"\bbreakpoint\s*\(", "breakpoint() 调用"),
            (r"__subclasses__\s*\(\)", "访问 __subclasses__()"),
            (r"__bases__", "访问 __bases__"),
            (r"__mro__", "访问 __mro__"),
            (r"__globals__", "访问 __globals__"),
            (r"__code__", "访问 __code__"),
            (r"__builtins__", "访问 __builtins__"),
            (r"__class__", "访问 __class__"),
            (r"__dict__", "访问 __dict__"),
            (r"__func__", "访问 __func__"),
            (r"__self__", "访问 __self__"),
            (r"\bos\b", "引用 os 模块"),
            (r"\bsys\b", "引用 sys 模块"),
            (r"\bsubprocess\b", "引用 subprocess 模块"),
            (r"\bctypes\b", "引用 ctypes 模块"),
            (r"\bsocket\b", "引用 socket 模块"),
            (r"\bshutil\b", "引用 shutil 模块"),
            (r"\bimportlib\b", "引用 importlib 模块"),
            (r"\bpickle\b", "引用 pickle 模块"),
            (r"\bmarshal\b", "引用 marshal 模块"),
            (r"getattr\s*\(", "getattr() 调用"),
            (r"hasattr\s*\(", "hasattr() 调用"),
            (r"\btype\s*\(", "type() 调用"),
            (r"\bvars\s*\(", "vars() 调用"),
            (r"\bdir\s*\(", "dir() 调用"),
            (r"\._module_\b", "访问 .__module__"),
            (r"load_module\s*\(", "load_module() 调用"),
        ]

        for pattern, description in dangerous_patterns:
            if re.search(pattern, code):
                raise SecurityError(
                    f"技能 '{skill_id}' 包含危险的代码模式: {description}。该模式可能用于沙箱逃逸，已被拒绝执行。"
                )

    @staticmethod
    def _compute_content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


__all__ = ["SkillCompilerMixin"]
