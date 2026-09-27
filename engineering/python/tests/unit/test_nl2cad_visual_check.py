"""NL2CAD 视觉回看校验 单元测试（渲染 + VLM 判定 + 重生成闭环接线）。

运行：unset PYTHONPATH && python -m pytest engineering/python/tests/unit/test_nl2cad_visual_check.py -v --no-cov
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import cadquery as cq
import pytest

from app.cad.nl2cad_llm import generate_cadquery_script
from app.cad.visual_verify import (
    ENV_VISUAL_CHECK,
    VisualCheckError,
    VisualCheckNotConfigured,
    VisualCheckStatus,
    VisualRenderError,
    build_visual_check_prompt,
    make_vision_call_from_provider,
    parse_visual_verdict,
    render_model_views,
    run_visual_check,
)
from app.ai.llm.provider_base import ProviderCapability

_VALID_BOX = "result = cq.Workplane('XY').box(50, 30, 20)"
_MATCH_JSON = '{"match": true, "issues": [], "confidence": 0.9}'
_MISMATCH_JSON = '{"match": false, "issues": ["孔位偏移", "凸台缺失"], "confidence": 0.8}'


class _MockLLM:
    """按调用顺序返回预设脚本的 mock LLM。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class _MockVision:
    """按调用顺序返回预设判定的 mock 视觉调用。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []
        self.images: list[list[str]] = []

    async def __call__(self, prompt: str, images: list[str]) -> str:
        self.prompts.append(prompt)
        self.images.append(images)
        return self.responses.pop(0)


def _run(coro):
    return asyncio.run(coro)


def _export_box_step(directory: Path, name: str = "box.step") -> str:
    """导出一个合法盒子模型供渲染测试使用。"""
    path = directory / name
    cq.exporters.export(cq.Workplane("XY").box(50, 30, 20), str(path))
    return str(path)


class TestParseVerdict:
    def test_match_json(self) -> None:
        match, issues, conf = parse_visual_verdict(_MATCH_JSON)
        assert match is True
        assert issues == []
        assert conf == pytest.approx(0.9)

    def test_fenced_json_with_issues(self) -> None:
        text = f"结论如下：\n```json\n{_MISMATCH_JSON}\n```"
        match, issues, _ = parse_visual_verdict(text)
        assert match is False
        assert issues == ["孔位偏移", "凸台缺失"]

    def test_issues_as_string_coerced_to_list(self) -> None:
        _, issues, _ = parse_visual_verdict('{"match": false, "issues": "孔位偏移"}')
        assert issues == ["孔位偏移"]

    def test_no_json_raises(self) -> None:
        with pytest.raises(VisualCheckError):
            parse_visual_verdict("模型看起来没问题")

    def test_missing_match_raises(self) -> None:
        with pytest.raises(VisualCheckError):
            parse_visual_verdict('{"issues": []}')


class TestRender:
    def test_renders_step_to_png(self, tmp_path) -> None:
        model = _export_box_step(tmp_path)
        out = tmp_path / "views.png"
        result = render_model_views(model, str(out))
        assert Path(result).exists()
        assert out.stat().st_size > 0

    def test_missing_model_raises(self, tmp_path) -> None:
        with pytest.raises(VisualRenderError):
            render_model_views(str(tmp_path / "nope.step"), str(tmp_path / "v.png"))

    def test_unsupported_format_raises(self, tmp_path) -> None:
        fake = tmp_path / "model.txt"
        fake.write_text("not a model", encoding="utf-8")
        with pytest.raises(VisualRenderError):
            render_model_views(str(fake), str(tmp_path / "v.png"))


class TestProviderAdapter:
    def test_builds_multimodal_message(self, tmp_path) -> None:
        image = tmp_path / "v.png"
        image.write_bytes(b"\x89PNG-fake-bytes")
        captured: list[list[dict]] = []

        async def chat_completion(messages, max_tokens=2048, temperature=0.7, model=None):
            captured.append(messages)
            return {"content": _MATCH_JSON}

        provider = SimpleNamespace(
            provider_id="mock",
            config=SimpleNamespace(capabilities=[ProviderCapability.VISION]),
            chat_completion=chat_completion,
        )
        call = make_vision_call_from_provider(provider)
        reply = _run(call("判定一下", [str(image)]))
        assert reply == _MATCH_JSON
        content = captured[0][0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_non_vision_provider_rejected(self) -> None:
        provider = SimpleNamespace(
            provider_id="mock",
            config=SimpleNamespace(capabilities=[ProviderCapability.CHAT]),
        )
        with pytest.raises(VisualCheckNotConfigured):
            make_vision_call_from_provider(provider)


class TestRunVisualCheck:
    def test_passed(self, tmp_path) -> None:
        model = _export_box_step(tmp_path)
        vision = _MockVision([_MATCH_JSON])
        result = _run(run_visual_check("一个 50x30x20 的盒子", model, vision, out_png=str(tmp_path / "v.png")))
        assert result.status == VisualCheckStatus.PASSED
        assert result.image_path is not None and Path(result.image_path).exists()
        # 描述进入了提示词
        assert "50x30x20" in vision.prompts[0]

    def test_mismatch_carries_issues(self, tmp_path) -> None:
        model = _export_box_step(tmp_path)
        vision = _MockVision([_MISMATCH_JSON])
        result = _run(run_visual_check("带孔的板", model, vision, out_png=str(tmp_path / "v.png")))
        assert result.status == VisualCheckStatus.MISMATCH
        assert result.issues == ["孔位偏移", "凸台缺失"]

    def test_vlm_failure_degrades_to_error(self, tmp_path) -> None:
        model = _export_box_step(tmp_path)

        class _Boom:
            async def __call__(self, prompt: str, images: list[str]) -> str:
                raise TimeoutError("VLM 超时")

        result = _run(run_visual_check("盒子", model, _Boom(), out_png=str(tmp_path / "v.png")))
        assert result.status == VisualCheckStatus.ERROR
        assert "VLM 调用失败" in result.summary

    def test_unparseable_verdict_degrades_to_error(self, tmp_path) -> None:
        model = _export_box_step(tmp_path)
        vision = _MockVision(["我觉得还行"])
        result = _run(run_visual_check("盒子", model, vision, out_png=str(tmp_path / "v.png")))
        assert result.status == VisualCheckStatus.ERROR
        assert result.raw_response == "我觉得还行"

    def test_render_failure_short_circuits_vlm(self, tmp_path) -> None:
        vision = _MockVision([_MATCH_JSON])
        result = _run(run_visual_check("盒子", str(tmp_path / "nope.step"), vision))
        assert result.status == VisualCheckStatus.ERROR
        assert "渲染失败" in result.summary
        assert vision.prompts == []  # 渲染失败不应浪费 VLM 调用


class TestGenerateLoopIntegration:
    def test_disabled_by_default(self, monkeypatch, tmp_path) -> None:
        """默认关闭：不注入 vision_call 且 env 未开 → 不做视觉校验。"""
        monkeypatch.delenv(ENV_VISUAL_CHECK, raising=False)
        mock = _MockLLM([_VALID_BOX])
        result = _run(generate_cadquery_script("盒子", llm_call=mock, max_attempts=2, task_id="t-v0"))
        assert result.visual_check is None
        assert result.attempts == 1

    def test_env_enabled_without_provider_skips(self, monkeypatch, tmp_path) -> None:
        """env 开启但无视觉 Provider → 安静跳过，不阻断生成。"""
        monkeypatch.setenv(ENV_VISUAL_CHECK, "1")
        import app.cad.nl2cad_llm as mod

        def _raise():
            raise VisualCheckNotConfigured("无视觉 Provider")

        monkeypatch.setattr(mod, "get_default_vision_call", _raise)
        mock = _MockLLM([_VALID_BOX])
        result = _run(generate_cadquery_script("盒子", llm_call=mock, max_attempts=2, task_id="t-v1"))
        assert result.visual_check is None
        assert result.attempts == 1

    def test_mismatch_triggers_regeneration_then_pass(self, monkeypatch, tmp_path) -> None:
        """第一次视觉不一致 → 反馈重生成 → 第二次通过。"""
        monkeypatch.delenv(ENV_VISUAL_CHECK, raising=False)
        llm = _MockLLM([_VALID_BOX, _VALID_BOX])
        vision = _MockVision([_MISMATCH_JSON, _MATCH_JSON])
        result = _run(
            generate_cadquery_script(
                "带孔的板",
                llm_call=llm,
                max_attempts=3,
                task_id="t-v2",
                vision_call=vision,
            )
        )
        assert result.attempts == 2
        assert result.visual_check is not None and result.visual_check.status == VisualCheckStatus.PASSED
        assert any("视觉回看" in fb and "孔位偏移" in fb for fb in result.feedback_used)
        assert "带孔的板" in vision.prompts[0]

    def test_mismatch_on_last_attempt_accepts_and_records(self, monkeypatch, tmp_path) -> None:
        """重试预算耗尽时的 mismatch：接受拓扑合法模型，结论如实标注。"""
        monkeypatch.delenv(ENV_VISUAL_CHECK, raising=False)
        vision = _MockVision([_MISMATCH_JSON])
        result = _run(
            generate_cadquery_script(
                "带孔的板",
                llm_call=_MockLLM([_VALID_BOX]),
                max_attempts=1,
                task_id="t-v3",
                vision_call=vision,
            )
        )
        assert result.attempts == 1
        assert result.visual_check is not None and result.visual_check.status == VisualCheckStatus.MISMATCH

    def test_vision_error_does_not_block(self, monkeypatch, tmp_path) -> None:
        """VLM 调用失败 → 降级 error，模型照常接受。"""
        monkeypatch.delenv(ENV_VISUAL_CHECK, raising=False)

        class _Boom:
            async def __call__(self, prompt: str, images: list[str]) -> str:
                raise ConnectionError("网络不可用")

        result = _run(
            generate_cadquery_script(
                "盒子",
                llm_call=_MockLLM([_VALID_BOX]),
                max_attempts=2,
                task_id="t-v4",
                vision_call=_Boom(),
            )
        )
        assert result.attempts == 1
        assert result.visual_check is not None and result.visual_check.status == VisualCheckStatus.ERROR
