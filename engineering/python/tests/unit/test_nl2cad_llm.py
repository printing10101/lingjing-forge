"""NL2CAD LLM 生成 + 校验重生成闭环 单元测试（升级2：③ Pointer-CAD 核心补全）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_nl2cad_llm.py -v --no-cov
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.cad.nl2cad_llm import (
    Nl2CadLLMError,
    Nl2CadLLMNotConfigured,
    build_user_prompt,
    extract_code,
    generate_cadquery_script,
)

_VALID_BOX = "result = cq.Workplane('XY').box(50, 30, 20)"
_BAD_IMPORT = "import os\n" + _VALID_BOX
_BAD_ZERO = "result = cq.Workplane('XY').box(0, 0, 0)"


class _MockLLM:
    """按调用顺序返回预设文本的 mock LLM。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("mock LLM 调用次数超过预设响应数")
        return self.responses.pop(0)


def _run(coro):
    return asyncio.run(coro)


class TestExtractCode:
    def test_strips_fences(self) -> None:
        assert extract_code("```python\nresult = 1\n```") == "result = 1"

    def test_plain_code_passthrough(self) -> None:
        assert extract_code("result = 1") == "result = 1"


class TestPrompt:
    def test_feedback_appended(self) -> None:
        prompt = build_user_prompt("做一个盒子", feedback="B-rep 校验失败: ['NOT_SOLID']")
        assert "上一次生成未通过校验" in prompt
        assert "NOT_SOLID" in prompt


class TestGenerateWithRetry:
    def test_bad_import_then_valid(self, tmp_path) -> None:
        """第一次 AST 审计失败 → 反馈 → 第二次成功（重生成闭环）。"""
        mock = _MockLLM([_BAD_IMPORT, _VALID_BOX])
        result = _run(
            generate_cadquery_script(
                "生成一个 50x30x20 的盒子",
                llm_call=mock,
                max_attempts=3,
                task_id="t1",
            )
        )
        assert result.attempts == 2
        assert result.validation_report is not None and result.validation_report.is_valid
        assert Path(result.output_path).exists()
        assert result.output_path.endswith(".step")
        # 第二次 prompt 携带了反馈
        assert "上一次生成未通过校验" in mock.prompts[1]
        assert any("AST" in fb for fb in result.feedback_used)

    def test_zero_dim_then_valid(self, monkeypatch) -> None:
        """第一次执行失败（box(0,0,0)）→ 反馈 → 第二次成功。

        历史上标注 skip_ci：Linux CI（cadquery-ocp Linux 轮子）上执行 box(0,0,0)
        在 OCCT 原生层挂死，旧的"子线程 + 异步异常"超时无法打断原生执行，泄漏的
        工作线程随后令同进程内所有 CadQuery 调用一并卡死（unit job 曾静默挂
        56 分钟至被杀）。现脚本执行已迁移到可强杀的一次性子进程（见
        _run_cadquery_script），超时强杀兜底，用例恢复进入 CI；超时预算压到
        15s 以约束 Linux 上挂死回收的时长，Windows 上 OCCT 对同一输入立即抛错。
        """
        monkeypatch.setenv("LNN_CADQUERY_TIMEOUT", "15")
        mock = _MockLLM([_BAD_ZERO, _VALID_BOX])
        result = _run(
            generate_cadquery_script(
                "生成一个盒子",
                llm_call=mock,
                max_attempts=3,
                task_id="t2",
            )
        )
        assert result.attempts == 2
        assert result.validation_report is not None and result.validation_report.is_valid

    def test_exhaustion_raises(self) -> None:
        """连续 max_attempts 次失败 → 抛 Nl2CadLLMError 且带反馈记录。"""
        mock = _MockLLM([_BAD_IMPORT, _BAD_IMPORT, _BAD_IMPORT])
        with pytest.raises(Nl2CadLLMError, match="连续 3 次生成均未通过校验"):
            _run(
                generate_cadquery_script(
                    "生成盒子",
                    llm_call=mock,
                    max_attempts=3,
                    task_id="t3",
                )
            )

    def test_markdown_fenced_valid(self, tmp_path) -> None:
        """LLM 返回带 ```python 围栏的代码也能成功。"""
        mock = _MockLLM([f"```python\n{_VALID_BOX}\n```"])
        result = _run(
            generate_cadquery_script(
                "生成盒子",
                llm_call=mock,
                max_attempts=2,
                task_id="t4",
            )
        )
        assert result.attempts == 1
        assert result.validation_report is not None and result.validation_report.is_valid


class TestNotConfigured:
    def test_no_llm_call_raises_not_configured(self, monkeypatch) -> None:
        """未注入 llm_call 且默认 Provider 不可用 → 优雅降级异常。"""

        def _raise():
            raise Nl2CadLLMNotConfigured("未配置任何可用的 LLM Provider")

        monkeypatch.setattr("app.cad.nl2cad_llm._get_default_llm_call", _raise)
        with pytest.raises(Nl2CadLLMNotConfigured):
            _run(generate_cadquery_script("生成盒子", llm_call=None))
