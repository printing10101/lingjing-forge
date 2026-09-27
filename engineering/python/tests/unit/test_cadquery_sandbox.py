"""CadQuery 沙箱子进程隔离 单元测试。

针对 _run_cadquery_script 的可强杀子进程隔离（Linux OCCT 原生挂死根因修复）：
- 父进程侧 AST 审计先行，不合法脚本不派发子进程；
- 子进程内的脚本异常经 JSON 协议回传并转换为 CadQueryScriptError；
- 超时由 subprocess.run 强杀子进程兜底（死循环/原生挂死均可回收）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_cadquery_sandbox.py -v --no-cov
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.cad._cadquery_helpers import (
    _run_cadquery_script,
    _wrap_script,
)
from app.cad.cadquery_gen import CadQueryScriptError

_VALID_BOX = "result = cq.Workplane('XY').box(50, 30, 20)"


def _run(coro):
    return asyncio.run(coro)


def _forbid_spawn(*args, **kwargs):
    raise AssertionError("不合法脚本不应派发子进程")


class TestParentSideAudit:
    """AST 审计在父进程完成：不合法脚本零子进程开销。"""

    def test_syntax_error_rejected_without_spawn(self, monkeypatch) -> None:
        monkeypatch.setattr("app.cad._cadquery_helpers.subprocess.run", _forbid_spawn)
        with pytest.raises(CadQueryScriptError, match="syntax error"):
            _run_cadquery_script("result = cq.Workplane('XY').box(", "t1")

    def test_import_rejected_without_spawn(self, monkeypatch) -> None:
        monkeypatch.setattr("app.cad._cadquery_helpers.subprocess.run", _forbid_spawn)
        with pytest.raises(CadQueryScriptError, match="Import"):
            _run_cadquery_script("import os\n" + _VALID_BOX, "t2")

    def test_dangerous_attr_rejected_without_spawn(self, monkeypatch) -> None:
        monkeypatch.setattr("app.cad._cadquery_helpers.subprocess.run", _forbid_spawn)
        with pytest.raises(CadQueryScriptError, match="dangerous attribute"):
            _run_cadquery_script("result = cq.Workplane().__class__", "t3")


class TestChildExecution:
    def test_valid_script_exports_model(self, tmp_path) -> None:
        """合法脚本经子进程沙箱执行并导出模型文件。"""
        out = tmp_path / "box.step"
        _run_cadquery_script(_wrap_script(_VALID_BOX, str(out), "step"), "t4")
        assert out.exists()
        assert out.stat().st_size > 0

    def test_runtime_error_propagates_from_child(self) -> None:
        """子进程内的脚本异常回传并转换为 CadQueryScriptError，任务号保留。"""
        with pytest.raises(CadQueryScriptError, match="boom") as exc_info:
            _run_cadquery_script("result = cq.Workplane('XY')\nraise ValueError('boom')", "t5")
        assert "t5" in str(exc_info.value)

    def test_getattr_dunder_blocked_in_child(self) -> None:
        """AST 审计无法覆盖的 getattr 字符串逃逸，被子进程内反射 wrapper 拒绝。"""
        with pytest.raises(CadQueryScriptError, match="forbidden via getattr"):
            _run_cadquery_script("result = getattr(cq.Workplane('XY'), '__class__')", "t6")


class TestTimeoutKill:
    def test_runaway_script_killed_with_child(self, monkeypatch) -> None:
        """死循环脚本触发强杀兜底：无论卡在字节码还是原生层都可回收。"""
        monkeypatch.setenv("LNN_CADQUERY_TIMEOUT", "2")
        with pytest.raises(CadQueryScriptError, match="timed out"):
            _run_cadquery_script("while True:\n    pass", "t7")
