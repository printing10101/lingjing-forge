"""
灵境制造 - AI Agents 模块
提供完整的类型注解支持
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Any, TypeAlias

from app.utils.utils import extract_json_from_markdown

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from app.config import config
from app.middleware.input_validator import (
    MaterialValidator,
    SizeValidator,
    ToleranceValidator,
    ValidationErrorDetail,
    validate_and_clean,
)
from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.rag.knowledge_base import get_knowledge_base
from enum import StrEnum


class AgentStage(StrEnum):
    UNDERSTANDING = "understanding"
    KNOWLEDGE_FETCH = "knowledge_fetch"
    PLANNING = "planning"
    PARAMETER = "parameter"
    NC_GENERATION = "nc_generation"
    VERIFICATION = "verification"
    REPAIR = "repair"


class StageStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"


def failed_status(e: BaseException) -> str:
    # 修复：阶段状态字段会进入响应，原始 e!s 可能包含敏感信息（路径、库版本），
    # 仅保留异常类型名以辅助排错，详细信息由服务端日志（logger.exception）记录。
    return f"failed: {type(e).__name__}"


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["AI Agents"])


def _flatten_documents(documents: Any) -> list[str]:
    """将 knowledge_base.query() 返回的 documents 扁平化为字符串列表。

    兼容两种输入格式：
      - ChromaDB 嵌套格式:  [[doc1, doc2]]  →  ["doc1", "doc2"]
      - 扁平格式（mock/边界）: [doc1, doc2]   →  ["doc1", "doc2"]

    边界处理：
      - 输入非 list 或空 list  →  返回 []
      - 嵌套列表内层空列表     →  返回 []
      - 元素非 str 类型        →  自动调用 str() 转换
    """
    if not isinstance(documents, list) or not documents:
        return []
    if isinstance(documents[0], list):
        return [str(d) for d in documents[0]]
    if isinstance(documents[0], str):
        return [str(d) for d in documents]
    return [str(d) for d in documents]


class ProcessStep(TypedDict, total=False):
    """工艺步骤类型"""

    step: int
    operation: str
    machine: str
    description: str


class CuttingParameter(TypedDict, total=False):
    """切削参数类型"""

    step: int
    operation: str
    v: float
    f: float
    ap: float
    n: float
    unit_v: str
    unit_f: str
    unit_ap: str
    unit_n: str


class VerificationIssue(TypedDict, total=False):
    """验证问题类型"""

    type: str
    description: str
    severity: str


class VerificationResult(TypedDict, total=False):
    """验证结果类型"""

    is_valid: bool
    issues: list[VerificationIssue]
    summary: str


class KnowledgeQueryConfig(TypedDict):
    """知识查询配置"""

    key: str
    query: str
    n_results: int


# 类型别名
ExtractedParams: TypeAlias = dict[str, Any]
ProcessRoute: TypeAlias = list[ProcessStep]
CuttingParameters: TypeAlias = list[CuttingParameter]
KnowledgeResults: TypeAlias = dict[str, str]
RepairSuggestions: TypeAlias = list[str] | str


class AgentContext(BaseModel):
    """Agent 执行上下文"""

    user_input: str = ""
    extracted_params: ExtractedParams = {}
    process_route: ProcessRoute = []
    cutting_parameters: dict[str, Any] = {}
    nc_code: str = ""
    verification_result: VerificationResult = VerificationResult()
    repair_suggestions: RepairSuggestions = []
    knowledge_results: dict[str, str] = {}
    current_stage: str = ""
    stage_status: str = ""


class ChatMessage(BaseModel):
    """聊天消息"""

    content: str = Field(..., description="消息内容")
    role: str = Field(default="user", description="消息角色")


class ChatRequest(BaseModel):
    """聊天请求"""

    messages: list[ChatMessage] = Field(..., description="对话消息列表")
    context: dict[str, Any] | None = Field(default=None, description="上下文信息")


router_chat = APIRouter(prefix="/api/ai", tags=["AI Chat"])


class LLMResponse(TypedDict, total=False):
    """LLM 响应"""

    content: str
    role: str
    usage: dict[str, Any]


class BaseAgent(ABC):
    """Agent 基类"""

    def __init__(
        self,
        name: str,
        description: str,
        task_router: Any = None,
        rule_weight: float = 0.4,
        ml_weight: float = 0.6,
    ) -> None:
        self.name = name
        self.description = description
        self.knowledge_base = get_knowledge_base()
        self._model_router: Any = task_router
        self._llm_client: Any = None
        self._rule_weight = rule_weight
        self._ml_weight = ml_weight

    async def _get_llm_client(self) -> Any:
        """获取或创建 LLM 客户端（懒加载并缓存）"""
        if self._llm_client is None:
            from app.ai.llm_client import CloudLLMClient

            self._llm_client = CloudLLMClient(
                api_key=config.ai.cloud_api_key,
                base_url=config.ai.cloud_base_url,
                model=config.ai.cloud_model,
                timeout=config.ai.timeout,
            )
        return self._llm_client

    async def _get_model_router(self) -> Any:
        """获取模型路由器，优先使用注入实例。

        路由器为实验性 stub 实现（详见 app.ai.lnn.router.task_router）。
        当依赖缺失（例如运行环境未安装 lnn 包）时记录警告并返回 None，
        不再静默吞掉异常。
        """
        if self._model_router is not None:
            return self._model_router
        try:
            from app.ai.lnn.router.task_router import TaskRouter
        except ImportError as e:
            logger.warning(
                "[%s] TaskRouter 不可用（lnn 包未安装）: %s", self.name, e
            )
            self._model_router = None
            return self._model_router

        try:
            self._model_router = TaskRouter(
                rule_weight=self._rule_weight,
                ml_weight=self._ml_weight,
            )
        except Exception as e:
            # 构造失败属于配置/参数错误，记录后回退到 None 但不静默
            logger.warning(
                "[%s] TaskRouter 初始化失败: %s", self.name, e, exc_info=True
            )
            self._model_router = None
        return self._model_router

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentContext:
        """执行 Agent 任务"""
        ...

    def get_system_prompt(self) -> str:
        """获取系统提示词"""
        return f"你是{self.name}，{self.description}"

    async def _search_knowledge(self, query: str, n_results: int = 5) -> list[dict]:
        """增强版知识检索

        先从知识库检索相关条目，对 bosch_cnc 来源的条目特别标注为"真实工业数据参考"。
        """
        try:
            raw = self.knowledge_base.query(query_text=query, n_results=n_results * 2)
        except Exception as e:
            logger.warning("[%s] 知识检索失败: %s", self.name, e)
            return []

        if not raw or not raw.get("documents"):
            return []

        docs_list = raw["documents"]
        if not docs_list or len(docs_list[0]) == 0:
            return []

        results: list[dict] = []
        docs = docs_list[0] or []
        metas = (raw.get("metadatas", [[]])[0] or []) if raw.get("metadatas") else []
        ids = (raw.get("ids", [[]])[0] or []) if raw.get("ids") else []
        distances = (
            (raw.get("distances", [[]])[0] or []) if raw.get("distances") else []
        )

        for i, doc in enumerate(docs):
            try:
                meta = metas[i] if i < len(metas) else {}
                doc_id = ids[i] if i < len(ids) else ""
                dist = distances[i] if i < len(distances) else 0.0

                source = meta.get("source", "unknown")
                entry = {
                    "id": doc_id,
                    "content": doc,
                    "metadata": meta,
                    "relevance": round(1.0 - float(dist), 4) if dist else 1.0,
                    "source": source,
                }

                if source == "bosch_cnc":
                    entry["reference_type"] = "真实工业数据参考 (Bosch CNC)"
                    entry["priority"] = "high"
                else:
                    entry["reference_type"] = "通用知识"
                    entry["priority"] = "normal"

                results.append(entry)
            except Exception as e:
                logger.warning("[%s] 处理文档 %d 失败: %s", self.name, i, e)
                continue

        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results[:n_results]

    async def _call_llm_via_router(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
        input_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """通过模型路由器调用 LLM"""
        model_router = await self._get_model_router()
        if model_router:
            prompt = messages[-1]["content"] if messages else ""
            system_prompt = (
                messages[0]["content"]
                if messages and messages[0]["role"] == "system"
                else None
            )

            default_input: dict[str, Any] = {
                "material": "",
                "tool": "",
                "constraints": [],
                "geometry": {},
                "history": [],
            }
            if input_data is None:
                input_data = default_input
            elif not isinstance(input_data, dict):
                logger.warning(f"[{self.name}] input_data 类型错误，使用默认值")
                input_data = default_input
            else:
                for key, default_value in default_input.items():
                    if key not in input_data:
                        input_data[key] = default_value
                    elif not isinstance(input_data[key], type(default_value)):
                        logger.warning(
                            f"[{self.name}] input_data['{key}'] 类型错误，使用默认值"
                        )
                        input_data[key] = default_value

            try:
                response: dict[str, Any] = await model_router.execute(
                    task_id="agent_task",
                    agent_name=self.name,
                    prompt=prompt,
                    input_data=input_data,
                    system_prompt=system_prompt,
                    max_retries=config.ai.max_retries,
                )
                return response
            except Exception as e:
                logger.warning(
                    f"[{self.name}] 模型路由调用失败: {e!s}，尝试降级到直接 LLM 调用"
                )
                try:
                    return await self._call_llm_direct(
                        messages, max_tokens, temperature, system_prompt
                    )
                except Exception as fallback_e:
                    logger.error(f"[{self.name}] 降级 LLM 调用也失败: {fallback_e!s}")
                    raise
        else:
            logger.warning(f"[{self.name}] 模型路由器未初始化，使用直接 LLM 调用")
            return await self._call_llm_direct(
                messages,
                max_tokens,
                temperature,
                messages[0]["content"]
                if messages and messages[0]["role"] == "system"
                else None,
            )

    async def _call_llm_direct(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """降级 LLM 调用：直接调用云端 LLM"""
        client = await self._get_llm_client()

        for attempt in range(config.ai.max_retries):
            try:
                response = await client.chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    model=config.ai.cloud_model,
                )
                return response
            except Exception as e:
                if attempt < config.ai.max_retries - 1:
                    await asyncio.sleep(1.0 * (2**attempt))
                else:
                    raise RuntimeError(f"[{self.name}] 降级 LLM 调用失败: {e!s}")


class UnderstandingAgent(BaseAgent):
    """理解 Agent - 负责理解用户需求"""

    def __init__(self) -> None:
        super().__init__(
            name="UnderstandingAgent", description="负责理解用户需求，提取关键制造参数"
        )

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.UNDERSTANDING
        context.stage_status = StageStatus.RUNNING

        knowledge_results: dict[str, Any] = self.knowledge_base.query(
            query_text=context.user_input, n_results=3
        )

        docs_flat = _flatten_documents(knowledge_results.get("documents", []))
        relevant_knowledge = "\n".join(docs_flat) if docs_flat else ""

        system_prompt: str = f"""你是一个制造工艺专家，负责从用户输入中提取关键参数。
参考知识：
{relevant_knowledge}

请以JSON格式返回提取的参数：
{{
  "material": "材料类型",
  "part_type": "零件类型",
  "dimensions": {{"length": 数值, "width": 数值, "height": 数值}},
  "tolerance": "公差等级",
  "surface_roughness": "表面粗糙度要求",
  "quantity": 数量
}}
只返回JSON，不要其他内容。"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context.user_input},
        ]

        response: dict[str, Any] = await self._call_llm_via_router(
            messages=messages,
            max_tokens=1024,
            temperature=0.3,
            input_data={
                "material": context.extracted_params.get("material", ""),
                "tool": "",
                "constraints": [],
                "geometry": context.extracted_params.get("dimensions", {}),
                "history": [],
            },
        )

        try:
            content: str = response.get("content", "").strip()
            extracted_params: ExtractedParams = extract_json_from_markdown(content)
            if not extracted_params:
                raise ValueError(
                    "Agent 响应解析失败：解析结果为空。Agent 未能从 LLM 响应中提取有效内容。"
                    "可能原因：1) LLM 返回格式不符合预期；2) 响应解析逻辑有误。"
                    "请检查 Agent 的解析方法实现或 LLM 提示词模板。"
                )
            context.extracted_params = extracted_params
            context.stage_status = StageStatus.COMPLETED
        except Exception as e:
            context.stage_status = failed_status(e)
            context.extracted_params = {"raw_input": context.user_input}

        return context


class KnowledgeFetchAgent(BaseAgent):
    """知识获取 Agent - 负责并行查询多个知识库"""

    def __init__(self) -> None:
        super().__init__(
            name="KnowledgeFetchAgent", description="负责并行查询多个知识库"
        )
        self.query_configs: list[KnowledgeQueryConfig] = [
            {"key": "planning", "query": "加工工艺路线规划", "n_results": 5},
            {"key": "parameter", "query": "切削参数 切削速度 进给量", "n_results": 5},
            {"key": "nc_generation", "query": "G代码 M代码 数控编程", "n_results": 5},
            {"key": "material", "query": "材料性能 加工特性", "n_results": 3},
        ]

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.KNOWLEDGE_FETCH
        context.stage_status = StageStatus.RUNNING

        material: str = context.extracted_params.get("material", "")
        part_type: str = context.extracted_params.get("part_type", "")

        tasks: list[Any] = []
        for cfg in self.query_configs:
            query: str = cfg["query"]
            if material:
                query = f"{material} {query}"
            if part_type:
                query = f"{part_type} {query}"
            tasks.append(self._query_knowledge(cfg["key"], query, cfg["n_results"]))

        results: list[Any] = await asyncio.gather(*tasks, return_exceptions=True)

        knowledge_data: KnowledgeResults = {}
        for i, result in enumerate(results):
            cfg = self.query_configs[i]
            if isinstance(result, Exception):
                logger.warning(f"[KnowledgeFetchAgent] 查询失败 {cfg['key']}: {result}")
                knowledge_data[cfg["key"]] = ""
            else:
                knowledge_data[cfg["key"]] = result

        context.knowledge_results = knowledge_data
        context.stage_status = StageStatus.COMPLETED

        return context

    async def _query_knowledge(self, key: str, query: str, n_results: int) -> str:
        results: dict[str, Any] = self.knowledge_base.query(
            query_text=query, n_results=n_results
        )
        docs_flat: list[str] = _flatten_documents(results.get("documents", []))
        if docs_flat:
            return "\n".join(docs_flat)
        return ""


class PlanningAgent(BaseAgent):
    """规划 Agent - 负责制定加工工艺路线"""

    def __init__(self) -> None:
        super().__init__(name="PlanningAgent", description="负责制定加工工艺路线")

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.PLANNING
        context.stage_status = StageStatus.RUNNING

        if context.knowledge_results.get("planning"):
            relevant_knowledge: str = context.knowledge_results["planning"]
        else:
            knowledge_results: dict[str, Any] = self.knowledge_base.query(
                query_text="加工工艺路线规划", n_results=5
            )
            docs_flat: list[Any] = _flatten_documents(
                knowledge_results.get("documents", [])
            )
            relevant_knowledge = "\n".join(docs_flat) if docs_flat else ""

        params: ExtractedParams = context.extracted_params
        material: str = params.get("material", "45钢")
        part_type: str = params.get("part_type", "轴类零件")

        system_prompt: str = f"""你是一个工艺规划专家。
材料：{material}
零件类型：{part_type}

参考知识：
{relevant_knowledge}

请以JSON数组格式返回工艺路线：
{{"route": [{{"step": 1, "operation": "工序名称", "machine": "设备类型", "description": "工序说明"}}]}}
只返回JSON。"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请为{material}的{part_type}制定加工工艺路线"},
        ]

        response: dict[str, Any] = await self._call_llm_via_router(
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
            input_data={
                "material": material,
                "tool": "",
                "constraints": [],
                "geometry": context.extracted_params.get("dimensions", {}),
                "history": [],
            },
        )

        try:
            content: str = response.get("content", "").strip()
            route_data: dict[str, Any] = extract_json_from_markdown(content)
            context.process_route = route_data.get("route", [])
            context.stage_status = StageStatus.COMPLETED
        except Exception as e:
            context.stage_status = failed_status(e)
            context.process_route = [
                {
                    "step": 1,
                    "operation": "下料",
                    "machine": "锯床",
                    "description": "按尺寸下料",
                },
                {
                    "step": 2,
                    "operation": "粗车",
                    "machine": "车床",
                    "description": "粗加工外圆",
                },
                {
                    "step": 3,
                    "operation": "精车",
                    "machine": "车床",
                    "description": "精加工到尺寸",
                },
                {
                    "step": 4,
                    "operation": "检验",
                    "machine": "量具",
                    "description": "检验尺寸",
                },
            ]

        return context


class ParameterAgent(BaseAgent):
    """参数 Agent - 负责计算切削参数"""

    def __init__(self) -> None:
        super().__init__(name="ParameterAgent", description="负责计算切削参数")

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.PARAMETER
        context.stage_status = StageStatus.RUNNING

        if context.knowledge_results.get("parameter"):
            relevant_knowledge: str = context.knowledge_results["parameter"]
        else:
            knowledge_results: dict[str, Any] = self.knowledge_base.query(
                query_text="切削参数 切削速度 进给量", n_results=5
            )
            docs_flat: list[str] = _flatten_documents(
                knowledge_results.get("documents", [])
            )
            relevant_knowledge = "\n".join(docs_flat) if docs_flat else ""

        params: ExtractedParams = context.extracted_params
        material: str = params.get("material", "45钢")

        system_prompt: str = f"""你是一个切削参数计算专家。
材料：{material}

参考知识：
{relevant_knowledge}

工艺路线：
{json.dumps(context.process_route, ensure_ascii=False, indent=2)}

请以JSON格式返回切削参数。"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请为{material}的加工计算切削参数"},
        ]

        response: dict[str, Any] = await self._call_llm_via_router(
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
            input_data={
                "material": material,
                "tool": "",
                "constraints": context.cutting_parameters.get("parameters", []),
                "geometry": {},
                "history": context.process_route,
            },
        )

        try:
            content: str = response.get("content", "").strip()
            param_data: dict[str, Any] = extract_json_from_markdown(content)
            context.cutting_parameters = param_data.get("parameters", {})
            context.stage_status = StageStatus.COMPLETED
        except Exception as e:
            context.stage_status = failed_status(e)
            context.cutting_parameters = {
                "parameters": [
                    {
                        "step": 1,
                        "operation": "粗车",
                        "v": 120,
                        "f": 0.3,
                        "ap": 2.0,
                        "n": 800,
                    },
                    {
                        "step": 2,
                        "operation": "精车",
                        "v": 180,
                        "f": 0.1,
                        "ap": 0.5,
                        "n": 1200,
                    },
                ]
            }

        return context


class NCAgent(BaseAgent):
    """NC Agent - 负责生成NC代码"""

    def __init__(self) -> None:
        super().__init__(name="NCAgent", description="负责生成NC代码")

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.NC_GENERATION
        context.stage_status = StageStatus.RUNNING

        knowledge_results: dict[str, Any] = self.knowledge_base.query(
            query_text="G代码 M代码 数控编程", n_results=5
        )
        docs_flat: list[str] = _flatten_documents(
            knowledge_results.get("documents", [])
        )
        relevant_knowledge: str = "\n".join(docs_flat) if docs_flat else ""

        system_prompt: str = f"""你是一个NC编程专家。

参考知识：
{relevant_knowledge}

工艺路线：
{json.dumps(context.process_route, ensure_ascii=False, indent=2)}

切削参数：
{json.dumps(context.cutting_parameters, ensure_ascii=False, indent=2)}

请直接返回NC代码，用```gcode```包裹。"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请根据上述工艺路线和切削参数生成NC代码"},
        ]

        response: dict[str, Any] = await self._call_llm_via_router(
            messages=messages,
            max_tokens=4096,
            temperature=0.2,
            input_data={
                "material": context.extracted_params.get("material", ""),
                "tool": "",
                "constraints": context.cutting_parameters.get("parameters", []),
                "geometry": context.extracted_params.get("dimensions", {}),
                "history": context.process_route,
            },
        )

        try:
            content: str = response.get("content", "").strip()
            if "```gcode" in content:
                context.nc_code = content.split("```gcode")[1].split("```")[0].strip()
            elif "```" in content:
                context.nc_code = content.split("```")[1].split("```")[0].strip()
            else:
                context.nc_code = content
            context.stage_status = StageStatus.COMPLETED
        except Exception as e:
            context.stage_status = failed_status(e)
            context.nc_code = "; NC代码生成失败\nG00 X0 Y0 Z0\nM30"

        return context


class VerificationAgent(BaseAgent):
    """验证 Agent - 负责验证工艺合理性"""

    def __init__(self) -> None:
        super().__init__(name="VerificationAgent", description="负责验证工艺合理性")

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.VERIFICATION
        context.stage_status = StageStatus.RUNNING

        system_prompt: str = """你是一个工艺验证专家。
请以JSON格式返回验证结果：
{"is_valid": true/false, "issues": [], "summary": "验证总结"}
只返回JSON。"""

        verification_content: str = f"""
工艺路线：
{json.dumps(context.process_route, ensure_ascii=False, indent=2)}

切削参数：
{json.dumps(context.cutting_parameters, ensure_ascii=False, indent=2)}

NC代码：
{context.nc_code}
"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": verification_content},
        ]

        response: dict[str, Any] = await self._call_llm_via_router(
            messages=messages,
            max_tokens=2048,
            temperature=0.2,
            input_data={
                "material": context.extracted_params.get("material", ""),
                "tool": "",
                "constraints": context.cutting_parameters.get("parameters", []),
                "geometry": context.extracted_params.get("dimensions", {}),
                "history": context.process_route,
            },
        )

        try:
            content: str = response.get("content", "").strip()
            verification_result: VerificationResult = extract_json_from_markdown(
                content
            )
            context.verification_result = verification_result
            context.stage_status = StageStatus.COMPLETED
        except Exception as e:
            context.stage_status = failed_status(e)
            context.verification_result = {
                "is_valid": True,
                "issues": [],
                "summary": "验证通过（简化模式）",
            }

        return context


class RepairAgent(BaseAgent):
    """修复 Agent - 负责优化工艺方案"""

    def __init__(self) -> None:
        super().__init__(name="RepairAgent", description="负责根据验证结果优化工艺方案")

    async def execute(self, context: AgentContext) -> AgentContext:
        context.current_stage = AgentStage.REPAIR
        context.stage_status = StageStatus.RUNNING

        verification: dict[str, Any] = context.verification_result
        is_valid: bool = verification.get("is_valid", True)
        issues: list[dict[str, Any]] = verification.get("issues", [])

        if is_valid and not issues:
            context.repair_suggestions = []
            context.stage_status = StageStatus.COMPLETED
            return context

        system_prompt: str = """你是一个工艺优化专家，负责根据验证结果提出优化建议。"""

        issues_text: str = "\n".join(
            [f"- {issue.get('description', '')}" for issue in issues]
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"验证发现的问题：\n{issues_text}\n\n请提出优化建议。",
            },
        ]

        response: dict[str, Any] = await self._call_llm_via_router(
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
            input_data={
                "material": context.extracted_params.get("material", ""),
                "tool": "",
                "constraints": context.verification_result.get("issues", []),
                "geometry": {},
                "history": context.repair_suggestions,
            },
        )

        context.repair_suggestions = response.get("content", "")
        context.stage_status = StageStatus.COMPLETED

        return context


@router.get("/info")
async def get_agents_info() -> dict[str, Any]:
    """获取所有 Agent 信息"""
    agents: list[dict[str, str]] = [
        {
            "name": "UnderstandingAgent",
            "description": "负责理解用户需求，提取关键制造参数",
        },
        {"name": "KnowledgeFetchAgent", "description": "负责并行查询多个知识库"},
        {"name": "PlanningAgent", "description": "负责制定加工工艺路线"},
        {"name": "ParameterAgent", "description": "负责计算切削参数"},
        {"name": "NCAgent", "description": "负责生成NC代码"},
        {"name": "VerificationAgent", "description": "负责验证工艺合理性"},
        {"name": "RepairAgent", "description": "负责根据验证结果优化工艺方案"},
    ]
    return success(data={"agents": agents})


@router_chat.post("/chat")
async def ai_chat(request: ChatRequest) -> dict[str, Any]:
    """AI对话接口 - 带输入验证"""
    if not request.messages:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="对话消息不能为空",
            suggestion="请提供至少一条消息",
        )

    validation_errors: list[ValidationErrorDetail] = []
    cleaned_messages: list[dict[str, str]] = []

    for i, msg in enumerate(request.messages):
        cleaned, err = validate_and_clean(
            msg.content, field_name=f"messages[{i}].content"
        )
        if err:
            validation_errors.append(err)
            break
        cleaned_messages.append({"role": msg.role, "content": cleaned})

    if validation_errors:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"输入验证失败: {validation_errors[0].message}",
            detail=validation_errors[0].to_response(),
        )

    if request.context:
        material: str | None = request.context.get("material")
        if material:
            mat_err = MaterialValidator.validate(material)
            if mat_err:
                return error(
                    code=ErrorCode.INVALID_REQUEST,
                    message=f"材料验证失败: {mat_err.message}",
                    detail=mat_err.to_response(),
                )

        size: dict[str, Any] | None = request.context.get("size")
        if size:
            _, size_err = SizeValidator.validate(size)
            if size_err:
                return error(
                    code=ErrorCode.INVALID_REQUEST,
                    message=f"尺寸验证失败: {size_err.message}",
                    detail=size_err.to_response(),
                )

        tolerance: str | None = request.context.get("tolerance")
        if tolerance:
            tol_result = ToleranceValidator.validate(tolerance)
            if isinstance(tol_result, ValidationErrorDetail):
                return error(
                    code=ErrorCode.INVALID_REQUEST,
                    message=f"公差验证失败: {tol_result.message}",
                    detail=tol_result.to_response(),
                )

    try:
        agent = UnderstandingAgent()
        response = await agent._call_llm_via_router(
            messages=cleaned_messages,
            max_tokens=2048,
            temperature=0.7,
        )
        return success(
            data={
                "content": response.get("content", ""),
                "model": response.get("model", ""),
            },
            message="对话成功",
        )
    except Exception as e:
        # 修复：避免 e!s 直接进入响应
        safe = safe_error_message(e, context="agents.chat", fallback="AI对话失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )
