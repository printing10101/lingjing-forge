"""工艺 / NC 代码解释引擎。

落地竞品分析中识别的 SolidWorks AURA 式 LLM 对话解释补强点。

核心能力：
1. **explain_process**：将工艺规划 JSON 转为自然语言解释
2. **explain_nc_code**：将 NC/G 代码转为自然语言解释（结合 ToolpathParser 结构化解析）
3. **chat**：多轮对话，支持基于会话历史的上下文追问

设计要点：
- 优先使用 ``ProviderRouter.chat_completion()`` 调用 LLM
- LLM 不可用时降级为「结构化摘要 + 规则提示」的规则化解释（软依赖）
- 多轮对话通过 ``SessionStore`` 持久化历史
- 不修改现有 ``ProcessUnderstandingEngine``，作为独立模块
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from app.ai.process_explainer.prompts import (
    SYSTEM_PROMPT_NC,
    SYSTEM_PROMPT_PROCESS,
    build_followup_prompt,
    build_nc_explanation_prompt,
    build_process_explanation_prompt,
    summarize_history,
)
from app.ai.process_explainer.session_store import SessionStore, get_session_store

logger = logging.getLogger(__name__)


@dataclass
class ExplanationResult:
    """解释结果。"""

    session_id: str
    answer: str
    mode: str  # "llm" / "rule_based"
    model: str = ""
    usage: dict = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "answer": self.answer,
            "mode": self.mode,
            "model": self.model,
            "usage": self.usage,
            "error": self.error,
        }


class ProcessExplainer:
    """工艺 / NC 代码解释引擎。

    单例模式：通过 ``get_process_explainer()`` 获取。
    """

    def __init__(
        self,
        session_store: SessionStore | None = None,
    ) -> None:
        self._store = session_store or get_session_store()

    # 公开接口

    async def explain_process(
        self,
        process_plan: dict,
        user_question: str = "",
        material: str = "",
        blank_size: str = "",
        feature_count: int | None = None,
        session_id: str | None = None,
    ) -> ExplanationResult:
        """解释工艺规划。

        Args:
            process_plan: 工艺规划 JSON（dict）
            user_question: 用户的上下文问题（可选）
            material: 工件材料
            blank_size: 毛坯尺寸描述
            feature_count: 加工特征数（None 则从 plan 推断）
            session_id: 会话 ID（None 则新建）
        """
        session_id = await self._ensure_session(session_id)
        if feature_count is None:
            ops = process_plan.get("operations") or process_plan.get("steps") or []
            feature_count = len(ops) if isinstance(ops, list) else 0

        plan_json = json.dumps(process_plan, ensure_ascii=False, indent=2)
        user_prompt = build_process_explanation_prompt(
            material=material or "未指定",
            blank_size=blank_size or "未指定",
            feature_count=feature_count,
            process_plan_json=plan_json,
            user_question=user_question,
        )

        # 记录用户提问
        await self._store.add_message(
            session_id=session_id,
            role="user",
            content=user_question or "[解释工艺规划]",
            metadata={"type": "process_explain", "material": material},
        )

        # 调用 LLM
        result = await self._call_llm(
            system_prompt=SYSTEM_PROMPT_PROCESS,
            user_prompt=user_prompt,
            session_id=session_id,
        )
        return result

    async def explain_nc_code(
        self,
        nc_code: str,
        controller_type: str = "fanuc",
        user_question: str = "",
        session_id: str | None = None,
    ) -> ExplanationResult:
        """解释 NC / G 代码。

        Args:
            nc_code: NC 代码文本
            controller_type: 控制器类型（fanuc / siemens / heidenhain 等）
            user_question: 用户的上下文问题（可选）
            session_id: 会话 ID（None 则新建）
        """
        session_id = await self._ensure_session(session_id)

        # 结构化解析（软依赖 ToolpathParser）
        segments_summary = self._parse_nc_segments(nc_code, controller_type)
        line_count = nc_code.count("\n") + 1

        user_prompt = build_nc_explanation_prompt(
            controller_type=controller_type,
            line_count=line_count,
            nc_code=nc_code[:4000],  # 截断防止 token 超限
            segments_summary=segments_summary,
            user_question=user_question,
        )

        # 记录用户提问
        await self._store.add_message(
            session_id=session_id,
            role="user",
            content=user_question or "[解释 NC 代码]",
            metadata={
                "type": "nc_explain",
                "controller": controller_type,
                "line_count": line_count,
            },
        )

        result = await self._call_llm(
            system_prompt=SYSTEM_PROMPT_NC,
            user_prompt=user_prompt,
            session_id=session_id,
        )
        return result

    async def chat(
        self,
        user_message: str,
        session_id: str | None = None,
    ) -> ExplanationResult:
        """多轮对话：基于会话历史回答用户追问。

        Args:
            user_message: 用户当前问题
            session_id: 会话 ID（None 则新建）
        """
        session_id = await self._ensure_session(session_id)

        # 获取历史
        history = await self._store.get_messages_as_llm_format(session_id, limit=10)

        # 记录当前用户消息
        await self._store.add_message(
            session_id=session_id,
            role="user",
            content=user_message,
            metadata={"type": "chat"},
        )

        # 构造带上下文的 Prompt
        history_summary = summarize_history(history, max_turns=5)
        user_prompt = build_followup_prompt(history_summary, user_message)

        # 综合系统 Prompt（兼顾工艺 + NC 代码）
        system_prompt = (
            SYSTEM_PROMPT_PROCESS + "\n\n" + SYSTEM_PROMPT_NC + "\n\n若用户问题与历史对话相关，请结合上下文回答。"
        )

        result = await self._call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_id=session_id,
        )
        return result

    async def get_session_history(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """获取会话历史。"""
        msgs = await self._store.get_history(session_id, limit)
        return [m.to_dict() for m in msgs]

    async def clear_session(self, session_id: str) -> int:
        """清空会话。"""
        return await self._store.clear_session(session_id)

    # 内部实现

    async def _ensure_session(self, session_id: str | None) -> str:
        """确保 session_id 有效，None 则创建新会话。"""
        if session_id:
            return session_id
        return await self._store.create_session()

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        session_id: str,
    ) -> ExplanationResult:
        """调用 LLM 生成解释，失败时降级为规则化解释。"""
        try:
            from app.ai.llm.router import get_router

            router = get_router()
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = await router.chat_completion(
                messages=messages,
                max_tokens=2048,
                temperature=0.3,
            )
            answer = response.get("content", "").strip()
            if not answer:
                answer = self._rule_based_fallback(user_prompt)

            # 记录助手回复
            await self._store.add_message(
                session_id=session_id,
                role="assistant",
                content=answer,
                metadata={
                    "model": response.get("model", ""),
                    "usage": response.get("usage", {}),
                },
            )

            return ExplanationResult(
                session_id=session_id,
                answer=answer,
                mode="llm",
                model=response.get("model", ""),
                usage=response.get("usage", {}),
            )
        except ImportError:
            logger.warning("LLM router 不可用，降级为规则化解释")
            answer = self._rule_based_fallback(user_prompt)
            await self._store.add_message(
                session_id=session_id,
                role="assistant",
                content=answer,
                metadata={"mode": "rule_based_fallback"},
            )
            return ExplanationResult(
                session_id=session_id,
                answer=answer,
                mode="rule_based",
                error="llm_router_unavailable",
            )
        except Exception as e:
            logger.exception("LLM 调用失败: %s", e)
            answer = self._rule_based_fallback(user_prompt)
            await self._store.add_message(
                session_id=session_id,
                role="assistant",
                content=answer,
                metadata={"mode": "rule_based_fallback", "error": "llm_call_failed"},
            )
            return ExplanationResult(
                session_id=session_id,
                answer=answer,
                mode="rule_based",
                error="llm_call_failed",
            )

    def _rule_based_fallback(self, user_prompt: str) -> str:
        """LLM 不可用时的规则化降级解释。"""
        return (
            "【规则化解释（LLM 不可用降级）】\n\n"
            "当前未配置可用的 LLM Provider，无法生成智能解释。"
            "以下为基础提示：\n\n"
            "1. 若需解释工艺规划，请检查工艺参数是否符合 Kienzle 切削力模型与 "
            "Tlusty 稳定性叶图约束。\n"
            "2. 若需解释 NC 代码，请关注 G00（快速定位）、G01（直线插补）、"
            "G02/G03（圆弧插补）、M06（换刀）等关键指令。\n"
            "3. 配置 LLM Provider 后（Ollama / LM Studio / 云端 API），"
            "可获得完整的智能解释能力。\n\n"
            f"原始问题摘要：{user_prompt[:200]}……"
        )

    def _parse_nc_segments(self, nc_code: str, controller_type: str) -> str:
        """使用 ToolpathParser 解析 NC 代码为结构化段摘要。"""
        try:
            from app.simulation.toolpath_parser import ToolpathParser

            parser = ToolpathParser(controller_type=controller_type)
            segments = parser.parse_gcode(nc_code)
            if not segments:
                return "（解析未返回任何刀路段）"

            lines = [f"共解析出 {len(segments)} 个刀路段："]
            for i, seg in enumerate(segments[:20]):  # 最多展示 20 段
                start = seg.start_point
                end = seg.end_point
                feed = seg.feed_rate or "—"
                spindle = seg.spindle_speed or "—"
                tool = seg.tool_id or "—"
                lines.append(
                    f"  段{i + 1} [{seg.type}] "
                    f"({start[0]:.2f},{start[1]:.2f},{start[2]:.2f}) → "
                    f"({end[0]:.2f},{end[1]:.2f},{end[2]:.2f}) "
                    f"F={feed} S={spindle} T{tool}"
                )
            if len(segments) > 20:
                lines.append(f"  ……（剩余 {len(segments) - 20} 段未展示）")
            return "\n".join(lines)
        except ImportError:
            return "（ToolpathParser 不可用）"
        except Exception as e:
            return f"（解析失败：{e}）"


# ── 全局单例（双重检查锁，线程安全） ────────────────────────────────
_global_explainer: ProcessExplainer | None = None
_explainer_lock = threading.Lock()


def get_process_explainer() -> ProcessExplainer:
    """获取全局 ProcessExplainer 单例。"""
    global _global_explainer
    if _global_explainer is not None:
        return _global_explainer
    with _explainer_lock:
        if _global_explainer is None:
            _global_explainer = ProcessExplainer()
    return _global_explainer


__all__ = [
    "ExplanationResult",
    "ProcessExplainer",
    "get_process_explainer",
]
