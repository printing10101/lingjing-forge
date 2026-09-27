"""知识图谱抽取模块（M1.4）。

实现基于 LLM 的自动化实体和关系抽取，包含：
    - PDF/Word 文档文本提取
    - LLM 抽取专用 prompt 模板
    - 抽取结果自动验证
    - 人工审核界面

核心组件：
    - PDFExtractor / WordExtractor: 文档文本提取
    - LLMExtractor: LLM 抽取核心模块
    - ExtractionValidator: 抽取结果验证
    - ReviewInterface: 人工审核界面

用法示例::

    from app.knowledge_graph.extractor import (
        extract_document,
        LLMExtractor,
        ExtractionValidator,
    )
    from app.ai.llm_client import OllamaClient

    # 1. 提取文档文本
    doc = extract_document("path/to/document.pdf")

    # 2. LLM 抽取
    client = OllamaClient(base_url="http://localhost:11434", model="qwen3.5:35b-128k")
    extractor = LLMExtractor(llm_client=client)
    result = await extractor.extract("path/to/document.pdf")

    # 3. 验证结果
    validator = ExtractionValidator()
    report = validator.validate(result.to_dict())
    # 准确率: {report.accuracy_score}%

    # 4. 人工审核
    # 启动审核界面: python -m app.knowledge_graph.extractor.review
"""

from app.knowledge_graph.extractor.pdf_extractor import (
    DocumentPage,
    ExtractedDocument,
    PDFExtractor,
    WordExtractor,
    chunk_pages,
    extract_document,
)
from app.knowledge_graph.extractor.llm_extractor import (
    ExtractionResult,
    LLMExtractor,
    create_llm_client,
)
from app.knowledge_graph.extractor.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT_TEMPLATE,
    build_user_prompt,
)
from app.knowledge_graph.extractor.validator import (
    EntityValidationResult,
    ExtractionValidator,
    RelationValidationResult,
    ValidationReport,
)

__all__ = [
    # PDF/Word 提取
    "DocumentPage",
    "ExtractedDocument",
    "PDFExtractor",
    "WordExtractor",
    "chunk_pages",
    "extract_document",
    # LLM 抽取
    "ExtractionResult",
    "LLMExtractor",
    "create_llm_client",
    # Prompt 模板
    "EXTRACTION_SYSTEM_PROMPT",
    "EXTRACTION_USER_PROMPT_TEMPLATE",
    "build_user_prompt",
    # 验证
    "EntityValidationResult",
    "ExtractionValidator",
    "RelationValidationResult",
    "ValidationReport",
]
