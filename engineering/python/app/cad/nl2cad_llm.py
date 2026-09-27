"""NL2CAD：LLM 生成 CadQuery 代码 + 校验重生成闭环（升级2：③ Pointer-CAD 核心补全）。

补上 Phase 1a 缺失的「LLM 生成路径」：
自然语言 → LLM 生成 CadQuery 脚本 → AST 安全审计 → 执行导出 → B-rep 拓扑校验
→ 视觉回看校验（可选，渲染三视图交 VLM 对照描述判定）
→ 校验失败时把错误反馈给 LLM 重新生成（Pointer-CAD 重生成闭环），
最多 max_attempts 次。

设计：
- ``llm_call: Callable[[str], str]`` 可注入（测试用 mock；生产用 provider 适配）；
- 未配置任何 LLM Provider 时优雅降级：抛 :class:`Nl2CadLLMNotConfigured`，
  调用方回退到既有「CV 提参 + 参数模板」路径；
- 视觉回看是软门禁（借鉴 agent3dify Render Verifier）：env
  ``LNN_NL2CAD_VISUAL_CHECK=1`` 开启或显式注入 ``vision_call`` 生效；
  mismatch 在有重试预算时反馈重生成，预算耗尽时接受拓扑合法的模型并
  在结果中如实标注；VLM 未配置/失败一律降级，不影响主流程；
- 代码只允许使用预注入的 ``cq``/``cadquery``（复用 _CadQueryScriptValidator
  AST 审计 + _run_cadquery_script 沙箱执行）。
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Awaitable, Callable

from app.cad._brep_validator import BrepValidationReport, validate_exported_model
from app.cad.cadquery_gen import (
    CadQueryError,
    CadQueryGenerator,
    CadQueryScriptError,
)
from app.cad.parametric_model import extract_parameters
from app.cad.visual_verify import (
    VisualCheckResult,
    VisualCheckStatus,
    VisionCall,
    get_default_vision_call,
    is_visual_check_enabled,
    run_visual_check,
)

logger = logging.getLogger(__name__)

LLMCall = Callable[[str], Awaitable[str]]

_SYSTEM_PROMPT = (
    "你是数控加工零件建模专家。根据用户的自然语言描述，用 CadQuery 编写 Python 脚本"
    "生成 3D 实体模型。"
    "硬性约束：\n"
    "1. 只能使用预注入的 `cq`（cadquery），禁止 import 任何模块；\n"
    "2. 用 `cq.Workplane('XY').box(l, w, h)` / `.cylinder(h, r)` / `.sphere(r)` / "
    "`.cone(h, r1, r2)` 等基础体素，可用 `.translate((x,y,z))`、`.cut()`、`.chamfer()`、"
    "`.fillet()` 组合特征；\n"
    "3. 尺寸必须为有限正数（单位 mm），禁止 0 或负数；\n"
    "4. 关键尺寸先在顶层赋值给命名变量再使用（如 `length = 50.0`），便于用户后续"
    "直接调参——模型几何通过这些变量驱动；\n"
    "5. 脚本最后必须把最终模型赋值给变量 `result`；\n"
    "6. 只输出纯 Python 代码，不要 markdown 代码块标记、不要注释解释。"
)

_USER_TEMPLATE = "生成以下零件的 CadQuery 脚本：\n{description}\n请只输出 Python 代码。"


class Nl2CadLLMError(Exception):
    """NL2CAD LLM 生成失败。"""


class Nl2CadLLMNotConfigured(Nl2CadLLMError):
    """未配置任何可用的 LLM Provider（调用方应回退到参数模板路径）。"""


@dataclass
class Nl2CadLLMResult:
    """LLM 生成结果。"""

    script: str
    output_path: str
    attempts: int
    validation_report: BrepValidationReport | None
    feedback_used: list[str] = None  # type: ignore[assignment]
    # 视觉回看结论（未启用/未执行时为 None；mismatch 见 status 与 issues）
    visual_check: VisualCheckResult | None = None
    # 可调参数表（顶层命名尺寸变量；脚本内联字面量时为空——前端不展示滑杆）
    parameters: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.feedback_used is None:
            self.feedback_used = []


# Prompt / 代码提取
def build_user_prompt(description: str, feedback: str | None = None) -> str:
    prompt = _USER_TEMPLATE.format(description=description)
    if feedback:
        prompt += f"\n\n【上一次生成未通过校验，请修复以下问题后重新生成】\n{feedback}"
    return prompt


_CODE_FENCE_RE = re.compile(r"^```(?:python)?\s*$", re.MULTILINE)


def extract_code(text: str) -> str:
    """去除 markdown 代码块标记，返回纯 Python 代码。"""
    code = _CODE_FENCE_RE.sub("", text).strip()
    # 若 LLM 用 ```python 包裹，去掉首尾残留 ``` 行
    if code.startswith("```"):
        code = code.lstrip("`").strip()
    if code.endswith("```"):
        code = code.rstrip("`").strip()
    return code


def _run_ast_audit(script: str) -> None:
    """AST 安全审计（拒绝 import / 危险 dunder）。"""
    import ast

    from app.cad.cadquery_gen import _CadQueryScriptValidator

    tree = ast.parse(script)
    _CadQueryScriptValidator().visit(tree)


# Provider 适配
def make_llm_call_from_provider(provider: Any) -> LLMCall:
    """把 LLMProvider.chat_completion 适配为 prompt→text 的 async callable。"""

    async def _call(prompt: str) -> str:
        resp = await provider.chat_completion(
            [{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.2,
        )
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        if not content:
            raise Nl2CadLLMError("LLM 返回空内容")
        return content

    return _call


def _get_default_llm_call() -> LLMCall:
    """从活跃 Provider 构造默认调用；无可用 Provider 时抛 Nl2CadLLMNotConfigured。"""
    try:
        from app.ai.llm._registry import get_registry

        registry = get_registry()
        provider = registry.get_active_provider()
        if provider is None:
            raise Nl2CadLLMNotConfigured(
                "未配置任何可用的 LLM Provider（NL2CAD 文本生成路径不可用）。"
                "请先在 LLM 设置中配置 Provider，或回退到「三视图参数提取」路径。"
            )
        return make_llm_call_from_provider(provider)
    except Nl2CadLLMNotConfigured:
        raise
    except Exception as e:  # noqa: BLE001 - Provider 层异常统一降级
        logger.warning("获取默认 LLM Provider 失败，降级: %s", e)
        raise Nl2CadLLMNotConfigured(f"无法初始化 LLM Provider: {e}") from e


# 主入口：生成 + 校验 + 重生成闭环
async def generate_cadquery_script(
    natural_language: str,
    llm_call: LLMCall | None = None,
    max_attempts: int = 3,
    output_format: str = "step",
    task_id: str | None = None,
    vision_call: VisionCall | None = None,
) -> Nl2CadLLMResult:
    """自然语言 → CadQuery 脚本 → 执行导出 → B-rep 校验 → （可选）视觉回看 → 失败重生成。

    Args:
        natural_language: 零件描述（中文自然语言）。
        llm_call: prompt→text 的异步 callable（缺省尝试活跃 Provider）。
        max_attempts: 最大生成尝试次数（含首次）。
        output_format: 导出格式（step 可全量 B-rep 校验）。
        task_id: 任务 ID（缺省自动生成）。
        vision_call: 视觉回看调用（注入即生效；缺省时读
            env ``LNN_NL2CAD_VISUAL_CHECK``，开启则取注册表中的视觉 Provider）。

    Returns:
        Nl2CadLLMResult（script/output_path/attempts/校验报告/反馈记录/视觉结论）。

    Raises:
        Nl2CadLLMNotConfigured: 未配置 Provider 且未注入 llm_call。
        Nl2CadLLMError: 连续 max_attempts 次校验失败。
    """
    if not natural_language or not natural_language.strip():
        raise ValueError("natural_language 不能为空")
    if not isinstance(natural_language, str) or len(natural_language) > 2000:
        raise ValueError("natural_language 必须是非空字符串且不超过 2000 字符")

    if llm_call is None:
        llm_call = _get_default_llm_call()

    # 视觉回看：显式注入 > env 开关；未启用或 VLM 未配置时安静跳过
    active_vision_call: VisionCall | None = vision_call
    if active_vision_call is None and is_visual_check_enabled():
        try:
            active_vision_call = get_default_vision_call()
        except Exception as e:  # noqa: BLE001 - 视觉 Provider 不可用不阻断生成
            logger.info("视觉回看未启用（无可用视觉 Provider）: %s", e)

    generator = CadQueryGenerator()
    tid = task_id or f"nl2cad_{uuid.uuid4().hex[:8]}"
    feedback_used: list[str] = []
    last_report: BrepValidationReport | None = None
    last_visual: VisualCheckResult | None = None

    for attempt in range(1, max_attempts + 1):
        prompt = build_user_prompt(natural_language, feedback=feedback_used[-1] if feedback_used else None)
        text = await llm_call(prompt)
        script = extract_code(text)

        # 1. AST 安全审计（语法错误 / import / 危险属性）
        try:
            _run_ast_audit(script)
        except (SyntaxError, CadQueryScriptError) as e:
            feedback = f"AST 审计未通过: {e}"
            feedback_used.append(feedback)
            logger.warning("NL2CAD 第 %d 次 AST 审计失败: %s", attempt, e)
            continue

        # 2. 沙箱执行 + 导出
        try:
            output_path = await generator.execute_and_export(script, tid, output_format)
        except (CadQueryError, ValueError, TypeError, OSError, RuntimeError) as e:
            feedback = f"CadQuery 执行/导出失败: {e}"
            feedback_used.append(feedback)
            logger.warning("NL2CAD 第 %d 次执行失败: %s", attempt, e)
            continue

        # 3. B-rep 拓扑校验（STEP 全量）
        last_report = validate_exported_model(output_path, output_format)
        if last_report is not None and last_report.errors:
            feedback = (
                "B-rep 拓扑校验失败，错误码 "
                f"{last_report.error_codes}: {'; '.join(i.message for i in last_report.errors)}"
            )
            feedback_used.append(feedback)
            logger.warning("NL2CAD 第 %d 次校验失败: %s", attempt, last_report.error_codes)
            continue

        # 4. 视觉回看（软门禁）：mismatch 且有重试预算 → 反馈重生成；
        #    预算耗尽时接受拓扑合法的模型，结论随结果如实带出
        if active_vision_call is not None:
            last_visual = await run_visual_check(natural_language, output_path, active_vision_call)
            if last_visual.status == VisualCheckStatus.MISMATCH and attempt < max_attempts:
                feedback = "视觉回看发现模型与描述不一致: " + "; ".join(last_visual.issues)
                feedback_used.append(feedback)
                logger.warning("NL2CAD 第 %d 次视觉校验不一致: %s", attempt, last_visual.issues)
                continue
            if last_visual.status != VisualCheckStatus.PASSED:
                logger.warning(
                    "NL2CAD 第 %d 次视觉校验未通过但接受模型（%s）: %s",
                    attempt,
                    last_visual.status.value,
                    last_visual.summary,
                )

        logger.info("NL2CAD 第 %d 次生成成功: %s", attempt, output_path)
        return Nl2CadLLMResult(
            script=script,
            output_path=output_path,
            attempts=attempt,
            validation_report=last_report,
            feedback_used=feedback_used,
            visual_check=last_visual,
            parameters=extract_parameters(script),
        )

    raise Nl2CadLLMError(
        f"NL2CAD 连续 {max_attempts} 次生成均未通过校验。最后一次反馈: {feedback_used[-1] if feedback_used else '无'}"
    )


# 兼容层（与并行会话早期接口对齐）：DummyLlmClient + 同步包装
class DummyLlmClient:
    """免密钥 mock LLM 客户端（演示/测试用，返回固定脚本或回显 prompt）。"""

    def __init__(self, script: str | None = None) -> None:
        self.script = script

    async def generate(self, prompt: str) -> str:
        return self.script or "result = cq.Workplane('XY').box(50, 30, 20)"


def generate_from_nl_sync(
    natural_language: str,
    llm_call: LLMCall | None = None,
    max_attempts: int = 3,
    output_format: str = "step",
    task_id: str | None = None,
    vision_call: VisionCall | None = None,
) -> Nl2CadLLMResult:
    """同步包装：无事件循环环境下调用 generate_cadquery_script。"""
    import asyncio

    return asyncio.run(
        generate_cadquery_script(
            natural_language,
            llm_call=llm_call,
            max_attempts=max_attempts,
            output_format=output_format,
            task_id=task_id,
            vision_call=vision_call,
        )
    )


__all__ = [
    "Nl2CadLLMError",
    "Nl2CadLLMNotConfigured",
    "Nl2CadLLMResult",
    "build_user_prompt",
    "extract_code",
    "make_llm_call_from_provider",
    "generate_cadquery_script",
]
