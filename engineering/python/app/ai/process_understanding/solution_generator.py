"""
工艺方案生成模块

根据用户提供的加工需求参数，从知识库检索相关知识，
通过LLM生成完整的工艺方案，包含加工路线、切削参数、风险提示和置信度评估。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from app.ai.process_understanding.task_classifier import TaskType
from app.ai.process_understanding.knowledge_retriever import (
    KnowledgeRetriever,
    RetrievalDocument,
)

logger = logging.getLogger(__name__)

# 工艺方案生成时 RAG 检索的默认文档数量
DEFAULT_SOLUTION_TOP_K = 8


@dataclass
class ProcessStep:
    """加工工序步骤"""

    step_number: int
    operation: str
    machine: str = ""
    description: str = ""


@dataclass
class CuttingParam:
    """切削参数"""

    step: int
    operation: str
    tool: str = ""
    spindle_speed: str = ""  # 转速 (r/min)
    feed_rate: str = ""  # 进给 (mm/r 或 mm/min)
    depth_of_cut: str = ""  # 切深 (mm)


@dataclass
class RiskItem:
    """风险提示"""

    risk: str
    severity: str = "medium"  # high / medium / low
    mitigation: str = ""


@dataclass
class ProcessSolution:
    """工艺方案"""

    material: str = ""
    precision_level: str = ""
    batch_size: str = ""
    machine_type: str = ""
    process_route: list[ProcessStep] = field(default_factory=list)
    cutting_parameters: list[CuttingParam] = field(default_factory=list)
    risk_warnings: list[RiskItem] = field(default_factory=list)
    confidence_score: float = 0.0
    uncertainty: str = ""
    references: list[str] = field(default_factory=list)
    generation_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "precision_level": self.precision_level,
            "batch_size": self.batch_size,
            "machine_type": self.machine_type,
            "process_route": [
                {
                    "step": s.step_number,
                    "operation": s.operation,
                    "machine": s.machine,
                    "description": s.description,
                }
                for s in self.process_route
            ],
            "cutting_parameters": [
                {
                    "step": p.step,
                    "operation": p.operation,
                    "tool": p.tool,
                    "spindle_speed": p.spindle_speed,
                    "feed_rate": p.feed_rate,
                    "depth_of_cut": p.depth_of_cut,
                }
                for p in self.cutting_parameters
            ],
            "risk_warnings": [
                {"risk": r.risk, "severity": r.severity, "mitigation": r.mitigation} for r in self.risk_warnings
            ],
            "confidence_score": self.confidence_score,
            "uncertainty": self.uncertainty,
            "references": self.references,
        }


# 工艺方案生成 Prompt

SOLUTION_GENERATION_PROMPT = """你是一个资深CNC工艺工程师。请为以下加工需求生成完整的工艺方案。

## 加工需求
- 材料：{material}
- 精度要求：{precision_level}
- 批量大小：{batch_size}
- 设备类型：{machine_type}

## 参考知识
{knowledge_context}

## 输出要求
请严格按以下JSON格式输出，不要包含其他内容：

```json
{{
  "process_route": [
    {{"step": 1, "operation": "工序名称", "machine": "设备类型", "description": "工序说明"}}
  ],
  "cutting_parameters": [
    {{
      "step": 1, "operation": "工序名称", "tool": "刀具类型",
      "spindle_speed": "转速(r/min)", "feed_rate": "进给量",
      "depth_of_cut": "切深(mm)"
    }}
  ],
  "risk_warnings": [
    {{"risk": "风险描述", "severity": "high/medium/low", "mitigation": "应对措施"}}
  ],
  "confidence_score": 7.5,
  "uncertainty": "主要不确定性说明"
}}
```

## 注意事项
1. 加工路线应符合制造业标准工艺规程
2. 切削参数需考虑材料特性与设备能力
3. 风险提示需具有实际指导意义
4. 置信度评估需基于知识匹配度与数据支持度（1-10分）
5. 对于高硬度材料，应适当降低切削参数
6. 批量生产需考虑工装夹具和测量方案"""


class SolutionGenerator:
    """工艺方案生成器。

    工作流程：
    1. 从知识库检索相关工艺知识
    2. 构建结构化Prompt（含检索到的知识）
    3. 调用LLM生成完整工艺方案
    4. 解析并验证输出结果
    """

    def __init__(self):
        self._llm_client: Any = None
        self._retriever: KnowledgeRetriever | None = None
        self._total_generations = 0
        self._total_latency_ms = 0.0

    async def _get_llm_client(self) -> Any:
        # 修复断点 A：通过 get_llm_client() 工厂函数接入 Provider 网关，
        # 优先使用用户在系统设置中激活的 Provider（本地 Ollama/LM Studio/llama.cpp/vLLM 或云端 API），
        # 无激活 Provider 时回退到 config.ai 配置（向后兼容）。
        if self._llm_client is None:
            from app.ai.llm_client import get_llm_client

            self._llm_client = await get_llm_client()
        return self._llm_client

    def _get_retriever(self) -> KnowledgeRetriever:
        if self._retriever is None:
            self._retriever = KnowledgeRetriever()
        return self._retriever

    async def generate(
        self,
        material: str = "45钢",
        precision_level: str = "IT8",
        batch_size: str = "单件",
        machine_type: str = "CNC加工中心",
        additional_context: str | None = None,
    ) -> ProcessSolution:
        """生成工艺方案。

        Args:
            material: 工件材料
            precision_level: 精度要求
            batch_size: 批量大小
            machine_type: 设备类型
            additional_context: 补充上下文信息

        Returns:
            ProcessSolution 包含完整工艺方案
        """
        start_time = time.perf_counter()
        self._total_generations += 1

        # 1. 检索相关知识
        retriever = self._get_retriever()
        search_query = f"{material} {precision_level} {machine_type} 加工工艺 切削参数"
        retrieval_result = await retriever.retrieve(
            query=search_query,
            task_type=TaskType.SOLUTION_GENERATION,
            top_k=DEFAULT_SOLUTION_TOP_K,
        )
        knowledge_text = self._format_knowledge(retrieval_result.documents)
        if additional_context:
            knowledge_text = f"{additional_context}\n\n{knowledge_text}"

        # 2. 构建Prompt
        prompt = SOLUTION_GENERATION_PROMPT.format(
            material=material,
            precision_level=precision_level,
            batch_size=batch_size,
            machine_type=machine_type,
            knowledge_context=knowledge_text or "无相关参考知识",
        )

        # 3. 调用LLM生成方案
        client = await self._get_llm_client()
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"请为{precision_level}精度的{material}工件生成在{machine_type}上的完整加工工艺方案。",
            },
        ]

        try:
            response = await client.chat_completion(
                messages=messages,
                max_tokens=4096,
                temperature=0.3,
            )
            content = response.get("content", "").strip()
        except (RuntimeError, OSError, ValueError, TypeError, ImportError, AttributeError, KeyError) as e:
            logger.error("LLM方案生成失败: %s", e, exc_info=True)
            return self._create_fallback_solution(material, precision_level, batch_size, machine_type)

        # 4. 解析结果
        solution = self._parse_solution(content, material, precision_level, batch_size, machine_type)

        elapsed = (time.perf_counter() - start_time) * 1000
        solution.generation_time_ms = elapsed
        self._total_latency_ms += elapsed

        logger.info(
            "方案生成完成: material=%s, steps=%d, confidence=%.1f, %.1fms",
            material,
            len(solution.process_route),
            solution.confidence_score,
            elapsed,
        )

        return solution

    @staticmethod
    def _format_knowledge(docs: list[RetrievalDocument]) -> str:
        """格式化检索到的知识文档。"""
        if not docs:
            return ""
        lines = []
        for i, doc in enumerate(docs[:8], 1):
            source = doc.source
            content = doc.content[:500] if doc.content else ""
            lines.append(f"[{i}] (来源: {source}) {content}")
        return "\n\n".join(lines)

    @staticmethod
    def _parse_solution(
        raw_content: str,
        material: str,
        precision_level: str,
        batch_size: str,
        machine_type: str,
    ) -> ProcessSolution:
        """解析LLM生成的工艺方案JSON。"""
        from app.utils.utils import extract_json_from_markdown

        try:
            data = extract_json_from_markdown(raw_content)
            if not data:
                raise ValueError("JSON解析结果为空")

            # 解析工艺路线
            process_route: list[ProcessStep] = []
            for step in data.get("process_route", []):
                process_route.append(
                    ProcessStep(
                        step_number=int(step.get("step", len(process_route) + 1)),
                        operation=step.get("operation", ""),
                        machine=step.get("machine", ""),
                        description=step.get("description", ""),
                    )
                )

            # 解析切削参数
            cutting_params: list[CuttingParam] = []
            for param in data.get("cutting_parameters", []):
                cutting_params.append(
                    CuttingParam(
                        step=int(param.get("step", len(cutting_params) + 1)),
                        operation=param.get("operation", ""),
                        tool=param.get("tool", ""),
                        spindle_speed=str(param.get("spindle_speed", "")),
                        feed_rate=str(param.get("feed_rate", "")),
                        depth_of_cut=str(param.get("depth_of_cut", "")),
                    )
                )

            # 解析风险提示
            risks = []
            for risk in data.get("risk_warnings", []):
                risks.append(
                    RiskItem(
                        risk=risk.get("risk", ""),
                        severity=risk.get("severity", "medium"),
                        mitigation=risk.get("mitigation", ""),
                    )
                )

            return ProcessSolution(
                material=material,
                precision_level=precision_level,
                batch_size=batch_size,
                machine_type=machine_type,
                process_route=process_route,
                cutting_parameters=cutting_params,
                risk_warnings=risks,
                confidence_score=float(data.get("confidence_score", 5.0)),
                uncertainty=data.get("uncertainty", ""),
            )
        except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as e:
            logger.warning("方案解析失败: %s，使用降级方案", e, exc_info=True)
            return SolutionGenerator._create_fallback_solution(material, precision_level, batch_size, machine_type)

    @staticmethod
    def _create_fallback_solution(
        material: str,
        precision_level: str,
        batch_size: str,
        machine_type: str,
    ) -> ProcessSolution:
        """创建降级方案（规则引擎）。"""
        return ProcessSolution(
            material=material,
            precision_level=precision_level,
            batch_size=batch_size,
            machine_type=machine_type,
            process_route=[
                ProcessStep(1, "下料", "锯床", "按工艺尺寸下料，留足加工余量"),
                ProcessStep(2, "粗加工", machine_type, "粗加工各表面，去除大部分余量"),
                ProcessStep(3, "半精加工", machine_type, "半精加工，为精加工做准备"),
                ProcessStep(4, "精加工", machine_type, f"精加工至{precision_level}精度要求"),
                ProcessStep(5, "去毛刺", "钳工台", "去除加工毛刺，清洁表面"),
                ProcessStep(6, "检验", "三坐标测量机", "按图纸要求全尺寸检验"),
            ],
            cutting_parameters=[
                CuttingParam(1, "粗加工", "硬质合金刀具", "800-1200", "0.2-0.4mm/r", "1-3mm"),
                CuttingParam(2, "精加工", "硬质合金刀具", "1200-2000", "0.05-0.15mm/r", "0.1-0.5mm"),
            ],
            risk_warnings=[
                RiskItem("参数为通用推荐值，实际加工需根据具体工况调整", "medium", "建议进行试切验证"),
            ],
            confidence_score=4.0,
            uncertainty="当前方案基于通用工艺规则生成，缺少针对具体特征的知识匹配",
        )

    def get_stats(self) -> dict[str, Any]:
        """获取方案生成器性能统计。"""
        return {
            "total_generations": self._total_generations,
            "avg_latency_ms": (
                self._total_latency_ms / self._total_generations if self._total_generations > 0 else 0.0
            ),
        }
