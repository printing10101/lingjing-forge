"""沙箱执行模块。

从原 ``app.plugins.skill_loader.loader`` 拆分而来，提供：
- :class:`SecurityError` 沙箱安全异常
- :class:`_SubprocessSkillExecutor` 子进程隔离执行代理
- :class:`SandboxExecutorMixin` 提供 subprocess 隔离相关配置项
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from typing import Any

logger = logging.getLogger(__name__)


class SecurityError(Exception):
    """沙箱安全异常 - 当检测到代码试图绕过安全沙箱时抛出。"""

    pass


class _SubprocessSkillExecutor:
    """子进程隔离的技能执行代理。"""

    _WORKER_SCRIPT = '''\
# -*- coding: utf-8 -*-
"""子进程 worker 脚本 - 在隔离进程中执行技能代码。"""
import sys
import json
import base64
import binascii

SAFE_BUILTINS = {
    "True": True, "False": False, "None": None,
    "bool": bool, "float": float, "int": int, "str": str,
    "abs": abs, "divmod": divmod, "max": max, "min": min,
    "pow": pow, "round": round, "sum": sum,
    "all": all, "any": any, "enumerate": enumerate, "len": len,
    "list": list, "range": range, "sorted": sorted,
}

try:
    raw = sys.stdin.buffer.read()
    data = json.loads(raw.decode("utf-8"))
except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
    sys.stdout.write(
        json.dumps({"status": "error", "error": "input parse failed"})
    )
    sys.exit(1)

try:
    code = base64.b64decode(data["code"]).decode("utf-8")
    skill_id = data["skill_id"]
    args = data.get("args", {})
except (KeyError, UnicodeDecodeError, binascii.Error, TypeError, ValueError):
    sys.stdout.write(
        json.dumps({"status": "error", "error": "input decode failed"})
    )
    sys.exit(1)

try:
    namespace = {"__builtins__": SAFE_BUILTINS, "__name__": "skill:" + skill_id}
    compiled = compile(code, "<skill:" + skill_id + ">", "exec")
    exec(compiled, namespace)

    entry = None
    for name in ("execute", "run", "main", "handler"):
        if name in namespace and callable(namespace[name]):
            entry = namespace[name]
            break

    if entry is None:
        sys.stdout.write(json.dumps({"status": "error", "error": "no entry point found"}))
        sys.exit(1)

    result = entry(**args)
    sys.stdout.write(json.dumps({"status": "ok", "result": result}, default=str))
except (RuntimeError, ValueError, TypeError, OSError, NameError, AttributeError, KeyError) as e:
    sys.stdout.write(json.dumps({
        "status": "error",
        "error": "skill 执行失败: 内部错误",
        "type": type(e).__name__,
    }))
    sys.exit(1)
'''

    def __init__(self, code: str, skill_id: str, timeout: float = 10.0):
        self.code = code
        self.skill_id = skill_id
        self.timeout = timeout
        self._worker_path: str | None = None
        self._worker_dir: str | None = None  # P1-3：保存临时目录以便主动清理

    def cleanup(self) -> None:
        """主动清理临时 worker 目录。

        P1-3 修复：原实现仅依赖 atexit.register 在进程退出时清理，
        但长运行服务进程不会频繁退出，导致 mkdtemp 创建的临时目录
        在系统 temp 中堆积。应用 shutdown 或 SkillLoader 卸载时应
        主动调用此方法释放资源。
        """
        if self._worker_dir is not None:
            try:
                shutil.rmtree(self._worker_dir, ignore_errors=True)
            except OSError as exc:
                logger.debug(
                    "cleanup skill worker dir %s failed: %s",
                    self._worker_dir,
                    exc,
                )
            finally:
                self._worker_dir = None
                self._worker_path = None

    def _ensure_worker(self) -> str:
        if self._worker_path is not None and os.path.exists(self._worker_path):
            return self._worker_path
        import atexit
        import tempfile

        tmp_dir = tempfile.mkdtemp(prefix="skill_worker_")
        # 注册进程退出时清理，覆盖子进程超时/崩溃/JSON 解析失败等异常路径，
        # 避免 mkdtemp 创建的临时目录在系统 temp 中无限堆积。
        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
        self._worker_dir = tmp_dir  # P1-3：记录目录供 cleanup() 主动清理
        self._worker_path = os.path.join(tmp_dir, "worker.py")
        try:
            with open(self._worker_path, "w", encoding="utf-8") as f:
                f.write(self._WORKER_SCRIPT)
        except Exception as e:
            # 写入失败时清理临时目录，避免泄漏
            logger.error("Failed to write skill worker script to %s: %s", self._worker_path, e, exc_info=True)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self._worker_dir = None
            self._worker_path = None
            raise
        return self._worker_path

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        import base64
        import subprocess
        import json as _json

        worker_path = self._ensure_worker()
        input_data = {
            "code": base64.b64encode(self.code.encode("utf-8")).decode("ascii"),
            "skill_id": self.skill_id,
            "args": kwargs,
        }

        try:
            proc = subprocess.run(
                [sys.executable, worker_path],
                input=_json.dumps(input_data),
                capture_output=True,
                timeout=self.timeout,
                text=True,
            )
        except subprocess.TimeoutExpired as e:
            logger.error(
                "Skill '%s' subprocess timed out after %.1fs",
                self.skill_id,
                self.timeout,
            )
            raise TimeoutError(f"Skill '{self.skill_id}' execution timed out after {self.timeout}s") from e

        if not proc.stdout:
            raise RuntimeError(f"Skill '{self.skill_id}' subprocess produced no output. stderr: {proc.stderr}")

        try:
            result = _json.loads(proc.stdout)
        except _json.JSONDecodeError as e:
            raise RuntimeError(
                f"Skill '{self.skill_id}' produced invalid JSON output: {e}. "
                f"stdout: {proc.stdout[:500]} | stderr: {proc.stderr[:500]}"
            )

        if result.get("status") == "error":
            error_type = result.get("type", "RuntimeError")
            error_msg = result.get("error", "Unknown error")
            exc_class = globals().get(error_type, RuntimeError)
            if isinstance(exc_class, type) and issubclass(exc_class, BaseException):
                raise exc_class(error_msg)
            raise RuntimeError(error_msg)

        return result.get("result")


class SandboxExecutorMixin:
    """沙箱执行 Mixin - 提供 subprocess 隔离相关配置项。

    被 :class:`SkillLoader` 通过多继承组合使用。依赖 ``os.environ``
    读取环境变量配置项。
    """

    _USE_SUBPROCESS_ISOLATION = os.environ.get("SKILL_USE_SUBPROCESS", "0") == "1"
    _SUBPROCESS_TIMEOUT_SEC = float(os.environ.get("SKILL_SUBPROCESS_TIMEOUT", "10"))


__all__ = [
    "SecurityError",
    "_SubprocessSkillExecutor",
    "SandboxExecutorMixin",
]
