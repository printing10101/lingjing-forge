"""LLM 抽取核心模块（M1.4）。

实现基于 LLM 的自动化实体和关系抽取，包含：
    - 文档分块处理（单批次不超过 10 页）
    - LLM 调用与失败重试（自动重试 3 次）
    - 抽取结果合并与可信度评分
    - 验证流程集成
    - CLI 命令行入口

依赖：
    - app.ai.llm_client（OllamaClient / CloudLLMClient）
    - app.knowledge_graph.extractor.pdf_extractor（文档提取）
    - app.knowledge_graph.extractor.prompts（Prompt 模板）
    - app.knowledge_graph.extractor.validator（结果验证）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ai.llm_client import (
    BaseLLMClient,
    CloudLLMClient,
    LLMError,
    OllamaClient,
    ServiceUnavailableError,
)
from app.knowledge_graph.extractor.pdf_extractor import (
    DocumentPage,
    chunk_pages,
    extract_document,
)
from app.knowledge_graph.extractor.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    build_user_prompt,
)
from app.knowledge_graph.extractor.validator import (
    ExtractionValidator,
)

logger = logging.getLogger(__name__)


# 数据类


@dataclass
class ExtractionResult:
    """单次抽取结果。"""

    entities: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    source_path: str = ""
    extraction_method: str = ""
    total_pages: int = 0
    processed_pages: int = 0
    validation_report: dict[str, Any] | None = None
    status: str = "unverified"  # unverified / approved / needs_revision
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为字典。"""
        result = {
            "entities": self.entities,
            "relations": self.relations,
            "source_path": self.source_path,
            "extraction_method": self.extraction_method,
            "total_pages": self.total_pages,
            "processed_pages": self.processed_pages,
            "status": self.status,
            "metadata": self.metadata,
        }
        if self.validation_report:
            result["validation_report"] = self.validation_report
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractionResult:
        """从字典构建。"""
        return cls(
            entities=data.get("entities", []),
            relations=data.get("relations", []),
            source_path=data.get("source_path", ""),
            extraction_method=data.get("extraction_method", ""),
            total_pages=data.get("total_pages", 0),
            processed_pages=data.get("processed_pages", 0),
            validation_report=data.get("validation_report"),
            status=data.get("status", "unverified"),
            metadata=data.get("metadata", {}),
        )


# LLM 抽取器


class LLMExtractor:
    """基于 LLM 的文档知识抽取器。

    用法::

        extractor = LLMExtractor(llm_client=client)
        result = await extractor.extract("path/to/document.pdf")
        logger.info("提取了 %s 个实体", len(result.entities))
        logger.info("提取了 %s 个关系", len(result.relations))

    Args:
        llm_client: LLM 客户端实例。
        max_pages_per_batch: 每批次最大页数（默认 10）。
        max_retries: LLM 调用失败最大重试次数（默认 3）。
        temperature: LLM 温度参数（默认 0.1，低温度保证抽取稳定性）。
        max_tokens: LLM 最大输出 token 数（默认 4096）。
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        max_pages_per_batch: int = 10,
        max_retries: int = 3,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> None:
        self.llm_client = llm_client
        self.max_pages_per_batch = max_pages_per_batch
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.validator = ExtractionValidator()

    async def extract(
        self,
        file_path: str | Path,
        *,
        validate: bool = True,
    ) -> ExtractionResult:
        """从文档中抽取实体和关系。

        Args:
            file_path: 文档文件路径（支持 PDF / Word）。
            validate: 是否执行验证（默认 True）。

        Returns:
            ExtractionResult 抽取结果。
        """
        file_path = Path(file_path)
        logger.info("开始抽取文档: %s", file_path)

        # 1. 提取文档文本
        doc = extract_document(file_path)
        logger.info(
            "文档提取完成: %d 页, 方法=%s",
            doc.total_pages,
            doc.extraction_method,
        )

        # 2. 分块处理
        page_chunks = chunk_pages(doc.pages, self.max_pages_per_batch)
        logger.info("文档分为 %d 个批次处理", len(page_chunks))

        # 3. 逐批次 LLM 抽取
        all_entities: list[dict[str, Any]] = []
        all_relations: list[dict[str, Any]] = []
        processed_pages = 0

        for batch_idx, chunk in enumerate(page_chunks):
            logger.info(
                "处理批次 %d/%d (页数: %d)",
                batch_idx + 1,
                len(page_chunks),
                len(chunk),
            )

            batch_entities, batch_relations = await self._extract_batch(
                chunk,
                total_pages=doc.total_pages,
                batch_idx=batch_idx,
            )

            all_entities.extend(batch_entities)
            all_relations.extend(batch_relations)
            processed_pages += len(chunk)

        # 4. 合并去重
        entities = self._merge_entities(all_entities)
        relations = self._merge_relations(all_relations)

        logger.info(
            "抽取完成: %d 个实体, %d 个关系 (去重后)",
            len(entities),
            len(relations),
        )

        # 5. 构建结果
        result = ExtractionResult(
            entities=entities,
            relations=relations,
            source_path=str(file_path),
            extraction_method=doc.extraction_method,
            total_pages=doc.total_pages,
            processed_pages=processed_pages,
            metadata={"filename": file_path.name},
        )

        # 6. 验证
        if validate:
            report = self.validator.validate(result.to_dict())
            result.validation_report = self.validator.to_dict(report)
            logger.info(
                "验证结果: 准确率=%.1f%%, 建议=%s",
                report.accuracy_score,
                report.recommendation,
            )

        return result

    async def _extract_batch(
        self,
        pages: list[DocumentPage],
        total_pages: int,
        batch_idx: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """抽取单个批次。

        Args:
            pages: 当前批次的页面列表。
            total_pages: 文档总页数。
            batch_idx: 批次索引。

        Returns:
            (entities, relations) 元组。
        """
        if not pages:
            return [], []

        start_page = pages[0].page_number
        end_page = pages[-1].page_number
        document_text = "\n\n".join(p.text for p in pages if p.text.strip())

        if not document_text.strip():
            logger.warning("批次 %d 文本为空，跳过", batch_idx)
            return [], []

        # 构建 prompt
        user_prompt = build_user_prompt(
            document_text=document_text,
            start_page=start_page,
            end_page=end_page,
            total_pages=total_pages,
        )

        messages = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # 调用 LLM（带重试）
        response = await self._call_llm_with_retry(messages, batch_idx)
        if response is None:
            return [], []

        # 解析响应
        return self._parse_llm_response(response)

    async def _call_llm_with_retry(
        self,
        messages: list[dict[str, str]],
        batch_idx: int,
    ) -> str | None:
        """调用 LLM 并处理重试。

        Args:
            messages: 消息列表。
            batch_idx: 批次索引（用于日志）。

        Returns:
            LLM 响应文本，失败返回 None。
        """
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug(
                    "批次 %d: LLM 调用尝试 %d/%d",
                    batch_idx,
                    attempt,
                    self.max_retries,
                )
                response = await self.llm_client.chat_completion(
                    messages=messages,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                content = response.get("content", "")
                if content:
                    logger.debug("批次 %d: LLM 响应长度=%d", batch_idx, len(content))
                    return content
                else:
                    logger.warning("批次 %d: LLM 返回空内容", batch_idx)
                    last_error = ValueError("LLM 返回空内容")
            except (LLMError, ServiceUnavailableError) as exc:
                last_error = exc
                logger.warning(
                    "批次 %d: LLM 调用失败 (尝试 %d/%d): %s",
                    batch_idx,
                    attempt,
                    self.max_retries,
                    exc,
                )
            except (ConnectionError, TimeoutError, RuntimeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "批次 %d: LLM 调用异常 (尝试 %d/%d): %s",
                    batch_idx,
                    attempt,
                    self.max_retries,
                    exc,
                )

            if attempt < self.max_retries:
                await asyncio.sleep(1.0 * attempt)

        logger.error(
            "批次 %d: LLM 调用 %d 次后仍失败: %s",
            batch_idx,
            self.max_retries,
            last_error,
        )
        return None

    def _parse_llm_response(self, response_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """解析 LLM 响应文本。

        Args:
            response_text: LLM 返回的文本。

        Returns:
            (entities, relations) 元组。
        """
        # 尝试提取 JSON
        json_str = self._extract_json(response_text)
        if not json_str:
            logger.warning("无法从 LLM 响应中提取 JSON")
            return [], []

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("JSON 解析失败: %s", exc)
            return [], []

        entities = data.get("entities", [])
        relations = data.get("relations", [])

        # 标准化实体
        normalized_entities = []
        for ent in entities:
            if isinstance(ent, dict):
                normalized_entities.append(self._normalize_entity(ent))

        # 标准化关系
        normalized_relations = []
        for rel in relations:
            if isinstance(rel, dict):
                normalized_relations.append(self._normalize_relation(rel))

        return normalized_entities, normalized_relations

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """从文本中提取 JSON 字符串。

        实现已收敛到 :func:`app.utils.utils.extract_json_text`，
        本方法仅为保持类接口兼容的委托。
        """
        from app.utils.utils import extract_json_text

        return extract_json_text(text)

    @staticmethod
    def _normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
        """标准化实体数据。"""
        result = {
            "entity_type": entity.get("entity_type", ""),
            "id": entity.get("id", ""),
            "name": entity.get("name", ""),
            "confidence": entity.get("confidence", 50),
            "properties": entity.get("properties", {}),
            "status": "unverified",
        }

        # 确保 confidence 在 [0, 100] 范围内
        conf = result["confidence"]
        if isinstance(conf, (int, float)):
            result["confidence"] = max(0, min(100, int(conf)))
        else:
            result["confidence"] = 50

        return result

    @staticmethod
    def _normalize_relation(relation: dict[str, Any]) -> dict[str, Any]:
        """标准化关系数据。"""
        result = {
            "relation_type": relation.get("relation_type", ""),
            "source_id": relation.get("source_id", ""),
            "target_id": relation.get("target_id", ""),
            "confidence": relation.get("confidence", 50),
            "properties": relation.get("properties", {}),
            "status": "unverified",
        }

        # 确保 confidence 在 [0, 100] 范围内
        conf = result["confidence"]
        if isinstance(conf, (int, float)):
            result["confidence"] = max(0, min(100, int(conf)))
        else:
            result["confidence"] = 50

        return result

    @staticmethod
    def _merge_entities(
        entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """合并去重实体。"""
        seen: dict[str, dict[str, Any]] = {}
        for ent in entities:
            eid = ent.get("id", "")
            if not eid:
                continue
            if eid in seen:
                # 保留置信度更高的版本
                if ent.get("confidence", 0) > seen[eid].get("confidence", 0):
                    seen[eid] = ent
            else:
                seen[eid] = ent
        return list(seen.values())

    @staticmethod
    def _merge_relations(
        relations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """合并去重关系。"""
        seen: dict[tuple[str, str, str], dict[str, Any]] = {}
        for rel in relations:
            key = (
                rel.get("source_id", ""),
                rel.get("target_id", ""),
                rel.get("relation_type", ""),
            )
            if not all(key):
                continue
            if key in seen:
                if rel.get("confidence", 0) > seen[key].get("confidence", 0):
                    seen[key] = rel
            else:
                seen[key] = rel
        return list(seen.values())


# 工厂函数


def create_llm_client() -> BaseLLMClient:
    """根据环境变量创建 LLM 客户端（同步工厂，用于知识图谱抽取场景）。

    .. note::
        本函数为知识图谱抽取模块的专用工厂，基于 ``LLM_PROVIDER`` 等环境变量
        构造客户端，便于在脚本化抽取流程中独立配置。**新代码应优先使用**
        ``await app.ai.llm_client.get_llm_client()``，它优先复用
        ProviderRegistry 中已激活的 Provider，回退到 ``config.ai`` 配置，
        并统一管理 httpx 连接池。仅当需要独立环境变量配置时使用本函数。

    环境变量：
        LLM_PROVIDER: ollama / cloud（默认 ollama）
        OLLAMA_BASE_URL: Ollama 服务地址（默认 http://localhost:11434）
        OLLAMA_MODEL: Ollama 模型名（默认 qwen3.5:35b-128k）
        CLOUD_API_KEY: 云服务 API Key
        CLOUD_BASE_URL: 云服务 Base URL
        CLOUD_MODEL: 云服务模型名

    Returns:
        BaseLLMClient 实例。
    """
    provider = os.environ.get("LLM_PROVIDER", "ollama").lower()

    if provider == "cloud":
        api_key = os.environ.get("CLOUD_API_KEY", "")
        base_url = os.environ.get("CLOUD_BASE_URL", "https://api.openai.com/v1")
        # 注意：此处默认模型 gpt-4o-mini 是抽取任务的有意选择，
        # 与 config.ai.AIConfig.cloud_model（gpt-3.5-turbo）不同，勿合并。
        model = os.environ.get("CLOUD_MODEL", "gpt-4o-mini")
        if not api_key:
            raise ValueError("CLOUD_API_KEY 环境变量未设置")
        return CloudLLMClient(
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
    else:
        # 复用 AIConfig：OLLAMA_BASE_URL / OLLAMA_MODEL 的读取与默认值
        # 以配置模块为唯一权威，避免多处硬编码漂移。
        from app.config.ai import AIConfig

        ai_cfg = AIConfig()
        return OllamaClient(
            base_url=ai_cfg.ollama_base_url,
            model=ai_cfg.ollama_model,
        )


# CLI


async def _async_main():  # pragma: no cover
    """异步主函数。"""
    import argparse

    parser = argparse.ArgumentParser(description="LLM 文档知识抽取工具（M1.4）")
    parser.add_argument("--input", "-i", required=True, help="输入文档路径")
    parser.add_argument("--output", "-o", help="输出 JSON 文件路径")
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="跳过验证步骤",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=10,
        help="每批次最大页数（默认 10）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 创建 LLM 客户端
    client = create_llm_client()

    # 创建抽取器
    extractor = LLMExtractor(
        llm_client=client,
        max_pages_per_batch=args.max_pages,
    )

    # 执行抽取
    result = await extractor.extract(
        args.input,
        validate=not args.no_validate,
    )

    # 输出结果
    output_data = result.to_dict()

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        logger.info("结果已保存到 %s", args.output)
    else:
        logger.info(json.dumps(output_data, ensure_ascii=False, indent=2))

    # 打印摘要
    summary = f"""
抽取完成:
  - 源文档: {result.source_path}
  - 总页数: {result.total_pages}
  - 提取实体: {len(result.entities)} 个
  - 提取关系: {len(result.relations)} 个
  - 状态: {result.status}
"""
    if result.validation_report:
        vs = result.validation_report.get("summary", {})
        summary += f"""  - 验证准确率: {vs.get("accuracy_score", 0):.1f}%
  - 建议: {vs.get("recommendation", "N/A")}
"""
    logger.info(summary)


def main():  # pragma: no cover
    """命令行入口。"""
    asyncio.run(_async_main())


if __name__ == "__main__":  # pragma: no cover
    main()
