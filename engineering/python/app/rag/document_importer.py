"""Document import service for RAG knowledge base.

Handles importing documents (PDF, DOCX, MD, TXT) with intelligent
chunking, embedding generation, and vector storage.

v2 增强：
- 切分时基于正则提取制造领域实体（材料牌号、实验编号、信号类型等）
- 实体随 chunk 一并写入 EntityIndex 倒排索引，支持基于实体的跨源检索
"""

from __future__ import annotations

import logging
import hashlib
import json
import os
from zipfile import BadZipFile
import re
import time
from pathlib import Path
from typing import Any

from app.dependencies import get_embedding_service

logger = logging.getLogger(__name__)

CHUNK_SIZE = 400
CHUNK_OVERLAP = 60


# 制造领域实体抽取（基于正则，无需 NLP 依赖）
# 匹配规则按实体类型分组，命中后归一化（小写）写入倒排索引

# 材料牌号：TC4, TC6, TC11, Ti-6Al-4V, HRC52, HRC45, 45钢, 6061, 304 等
_MATERIAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTC[0-9]{1,2}\b"),  # TC4, TC11
    re.compile(r"\bTi-?\dAl-?\dV?\b", re.IGNORECASE),  # Ti-6Al-4V / Ti6Al4V
    re.compile(r"\bHRC\s*\d{1,3}\b"),  # HRC52, HRC 45
    re.compile(r"(?<![A-Za-z0-9])\d{2,4}钢(?![A-Za-z0-9])"),  # 中文连续句中的 45钢 / 304钢
    re.compile(r"\b(?:6061|7075|2024|AISI\s*\d{3,4})\b", re.IGNORECASE),  # 铝合金/不锈钢牌号
    re.compile(r"(?:钛合金|不锈钢|铝合金|硬质合金|高温合金)"),
]

# 实验编号：W1-W9, c1/c4/c6
_EXPERIMENT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bW[1-9]\b"),  # W1-W9
    re.compile(r"\bc[1-9]\b"),  # c1-c9
]

# 信号类型：振动/切削力/声发射/主轴功率
_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"振动|声发射|\b(?:vibration|RMS|acoustic)\b", re.IGNORECASE),
    re.compile(r"切削力|主轴功率|\b(?:cutting\s*force|spindle\s*power)\b", re.IGNORECASE),
    re.compile(r"频域|频谱|\bfrequency\s*domain\b", re.IGNORECASE),
]

# 数据集名：NUAA, PHM2010, Uniwear, Bosch
_DATASET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bNUAA\b"),
    re.compile(r"\bPHM\s*2010\b", re.IGNORECASE),
    re.compile(r"\bUniwear\b", re.IGNORECASE),
    re.compile(r"\bBosch\b", re.IGNORECASE),
]

# 工艺参数关键词
_PROCESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:切削速度|进给量|背吃刀量|切削深度|转速)"),
    re.compile(r"\b(?:cutting\s*speed|feed\s*rate|depth\s*of\s*cut)\b", re.IGNORECASE),
]


def extract_entities(text: str) -> list[str]:
    """从文本中抽取制造领域实体。

    采用正则匹配，无需 NLP 依赖。命中实体归一化为小写后去重。

    Args:
        text: 待抽取的文本（通常是 chunk 内容）

    Returns:
        去重后的实体列表（小写形式）
    """
    if not text or not text.strip():
        return []

    found: set[str] = set()
    all_patterns = _MATERIAL_PATTERNS + _EXPERIMENT_PATTERNS + _SIGNAL_PATTERNS + _DATASET_PATTERNS + _PROCESS_PATTERNS
    for pattern in all_patterns:
        for match in pattern.finditer(text):
            entity = match.group(0).strip().lower()
            # 过滤过短的无意义匹配
            if len(entity) >= 2:
                found.add(entity)

    return sorted(found)


def _parse_pdf(file_path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(str(file_path)) as pdf:
        texts = []
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                texts.append(text)
        return "\n\n".join(texts)


def _parse_docx(file_path: Path) -> str:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.opc.exceptions import PackageNotFoundError
    except ImportError:
        raise ValueError("缺少 python-docx，无法解析 Word 文档") from None
    try:
        doc = Document(str(file_path))
    except (BadZipFile, PackageNotFoundError, KeyError) as exc:
        raise ValueError("Word 文档格式错误或损坏，请检查文件") from exc
    blocks = []
    for element in doc.element.body:
        if element.tag.endswith("}p"):
            text = Paragraph(element, doc).text
            if text.strip():
                blocks.append(text)
        elif element.tag.endswith("}tbl"):
            table = Table(element, doc)
            blocks.append("\n".join("\t".join(cell.text for cell in row.cells) for row in table.rows))
    return "\n\n".join(blocks)


def _parse_spreadsheet(file_path: Path) -> str:
    from app.rag.excel_parser import parse_csv, parse_excel
    result = parse_csv(file_path) if file_path.suffix.lower() == ".csv" else parse_excel(file_path)
    if result["status"] != "success":
        raise ValueError(result["error"])
    blocks = []
    for table in result["tables"]:
        rows = [table["headers"], *table["rows"]]
        blocks.append("工作表：" + table.get("sheet_name", file_path.stem) + "\n" +
                      "\n".join("\t".join(str(value) for value in row) for row in rows))
    return "\n\n".join(blocks)


def _parse_md(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8")


def _parse_txt(file_path: Path) -> str:
    return file_path.read_text(encoding="utf-8", errors="replace")


def _parse_json(file_path: Path) -> list[dict[str, Any]]:
    import json

    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return [{"document": json.dumps(item, ensure_ascii=False), "metadata": {}} for item in data]
    return [{"document": json.dumps(data, ensure_ascii=False), "metadata": {}}]


PARSERS = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".md": _parse_md,
    ".markdown": _parse_md,
    ".txt": _parse_txt,
    ".json": None,
    ".xlsx": _parse_spreadsheet,
    ".xlsm": _parse_spreadsheet,
    ".csv": _parse_spreadsheet,
}


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    from langchain.text_splitter import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "；", "，", ".", ";", ",", " ", ""],
        length_function=len,
    )
    return splitter.split_text(text)


class DocumentImportService:
    """Service for importing documents with chunking, embedding, and vector storage."""

    def __init__(self, knowledge_base: Any):
        self.knowledge_base = knowledge_base
        self._emb = get_embedding_service()
        self._import_history: list[dict[str, Any]] = []

    def import_document(
        self,
        file_path: str,
        additional_metadata: dict[str, Any] | None = None,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ) -> dict[str, Any]:
        start_time = time.time()
        file_path_obj = Path(file_path)
        suffix = file_path_obj.suffix.lower()

        if not file_path_obj.exists():
            error = f"File not found: {file_path}"
            logger.error(error)
            return self._error_result(file_path_obj.name, error)

        try:
            parse_start = time.time()
            source_hash = hashlib.sha256(file_path_obj.read_bytes()).hexdigest()
            if suffix == ".json":
                items = _parse_json(file_path_obj)
                chunks = [item["document"] for item in items]
                chunk_metas: list[dict[str, Any]] = [item["metadata"] for item in items]
            else:
                parser = PARSERS.get(suffix)
                if parser is None:
                    return self._error_result(
                        file_path_obj.name,
                        f"Unsupported file type: {suffix}",
                    )
                content = parser(file_path_obj)
                if not content or not content.strip():
                    return self._error_result(
                        file_path_obj.name,
                        "Document content is empty after parsing",
                    )
                chunks = _chunk_text(content, chunk_size, chunk_overlap)
                chunk_metas = [{} for _ in chunks]
            parse_ms = (time.time() - parse_start) * 1000
        except (OSError, ValueError, TypeError, UnicodeDecodeError) as e:
            # 文件 IO、解析器返回格式错误、编码错误等已知异常
            logger.exception("Failed to parse document: %s", file_path)
            return self._error_result(file_path_obj.name, str(e))

        if not chunks:
            return self._error_result(file_path_obj.name, "No chunks produced")

        try:
            if hashlib.sha256(file_path_obj.read_bytes()).hexdigest() != source_hash:
                return self._error_result(file_path_obj.name, "Document changed during parsing; retry import")
        except OSError:
            return self._error_result(file_path_obj.name, "Document became unreadable during parsing")
        document_id = hashlib.sha256(os.path.normcase(str(file_path_obj.resolve())).encode("utf-8")).hexdigest()
        revision = hashlib.sha256(json.dumps(
            [source_hash, chunk_size, chunk_overlap, chunks], ensure_ascii=False,
        ).encode("utf-8")).hexdigest()

        base_meta = {
            **(additional_metadata or {}),
            "source": "file_import",
            "file_name": file_path_obj.name,
            "file_type": suffix,
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "document_id": document_id,
            "document_revision": revision,
            "source_sha256": source_hash,
        }

        try:
            embed_start = time.time()
            embeddings = self._emb.embed_batch(chunks)
            embed_ms = (time.time() - embed_start) * 1000
        except (OSError, RuntimeError, ValueError) as e:
            # embedding 推理（onnx/numpy/磁盘模型 IO）可能失败
            logger.exception("Failed to generate embeddings: %s", file_path)
            from app.core.safe_errors import safe_error_message

            safe = safe_error_message(
                e,
                context="rag.document_importer.embed",
                fallback="Embedding生成失败",
            )
            return self._error_result(
                file_path_obj.name,
                f"Embedding failed: {safe['message']} (error_id={safe['error_id']})",
            )

        import_start = time.time()
        chunk_count = 0
        failed_chunks = 0
        # 实体提取统计
        total_entities = 0
        replace_chunks = getattr(self.knowledge_base.collection, "replace_document_chunks", None)
        if callable(replace_chunks):
            ids = [f"import_{document_id}_{revision}_{i}" for i in range(len(chunks))]
            entities = [extract_entities(chunk) for chunk in chunks]
            metadatas = [{**chunk_metas[i], **base_meta, "chunk_index": i,
                          "entity_count": len(entities[i])} for i in range(len(chunks))]
            try:
                replace_chunks(document_id, ids, chunks, embeddings, metadatas, entities)
            except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                logger.exception("Document replacement failed: %s", file_path_obj.name)
                return self._error_result(file_path_obj.name, "Document replacement failed; retry import")
            result = {"file_name": file_path_obj.name, "file_size": file_path_obj.stat().st_size,
                      "document_id": document_id, "document_revision": revision,
                      "source_sha256": source_hash, "chunk_count": len(chunks), "failed_chunks": 0,
                      "entities_extracted": sum(map(len, entities)), "success": True,
                      "parse_time_ms": round(parse_ms, 2), "embed_time_ms": round(embed_ms, 2),
                      "import_time_ms": round((time.time() - import_start) * 1000, 2),
                      "total_time_ms": round((time.time() - start_time) * 1000, 2)}
            self._import_history.append(result)
            return result
        # 批量写入优化：分批 add，减少与 ChromaDB 的 round-trip 次数
        # 一次 add() 内部要序列化、计算距离索引、写入 SQLite，单条调用开销约 1-5ms
        # 批量 32 条可显著降低总延迟
        BATCH_WRITE_SIZE = 32
        for batch_start in range(0, len(chunks), BATCH_WRITE_SIZE):
            batch_end = min(batch_start + BATCH_WRITE_SIZE, len(chunks))
            batch_ids: list[str] = []
            batch_docs: list[str] = []
            batch_embs: list[list[float]] = []
            batch_metas: list[dict[str, Any]] = []
            batch_entities: list[list[str]] = []
            batch_indices: list[int] = []
            for i in range(batch_start, batch_end):
                chunk = chunks[i]
                meta = {**base_meta, "chunk_index": i, **chunk_metas[i]}
                doc_id = f"import_{document_id}_{revision}_{i}"
                # 切分时提取实体，写入倒排索引
                ents = extract_entities(chunk)
                total_entities += len(ents)
                if ents:
                    meta["entity_count"] = len(ents)
                batch_ids.append(doc_id)
                batch_docs.append(chunk)
                batch_embs.append(embeddings[i])
                batch_metas.append(meta)
                batch_entities.append(ents)
                batch_indices.append(i)
            try:
                self.knowledge_base.collection.add(
                    ids=batch_ids,
                    documents=batch_docs,
                    embeddings=batch_embs,
                    metadatas=batch_metas,
                    entities_list=batch_entities,
                )
                chunk_count += len(batch_indices)
            except (OSError, RuntimeError, ValueError, KeyError) as e:
                # 整批写入失败：降级为逐条写入，定位具体失败 chunk
                logger.warning(
                    "Batch write failed for %s chunks %d-%d, retrying one-by-one: %s",
                    file_path_obj.name,
                    batch_start,
                    batch_end - 1,
                    e,
                )
                for idx_in_batch, orig_i in enumerate(batch_indices):
                    try:
                        self.knowledge_base.collection.add(
                            ids=[batch_ids[idx_in_batch]],
                            documents=[batch_docs[idx_in_batch]],
                            embeddings=[batch_embs[idx_in_batch]],
                            metadatas=[batch_metas[idx_in_batch]],
                            entities_list=[batch_entities[idx_in_batch]],
                        )
                        chunk_count += 1
                    except (OSError, RuntimeError, ValueError, KeyError) as single_err:
                        logger.warning(
                            "Failed to store chunk %d of %s: %s",
                            orig_i,
                            file_path_obj.name,
                            single_err,
                            exc_info=True,
                        )
                        failed_chunks += 1
        import_ms = (time.time() - import_start) * 1000

        total_ms = (time.time() - start_time) * 1000
        result = {
            "file_name": file_path_obj.name,
            "file_size": file_path_obj.stat().st_size,
            "chunk_count": chunk_count,
            "failed_chunks": failed_chunks,
            "entities_extracted": total_entities,
            "parse_time_ms": round(parse_ms, 2),
            "embed_time_ms": round(embed_ms, 2),
            "import_time_ms": round(import_ms, 2),
            "total_time_ms": round(total_ms, 2),
            "success": chunk_count > 0,
        }
        if failed_chunks > 0:
            result["warning"] = f"{failed_chunks}/{chunk_count + failed_chunks} chunks failed"
        self._import_history.append(result)
        logger.info(
            "Imported %s: %d chunks, %d entities in %.0fms (parse=%.0fms, embed=%.0fms, store=%.0fms)",
            file_path_obj.name,
            chunk_count,
            total_entities,
            total_ms,
            parse_ms,
            embed_ms,
            import_ms,
        )
        return result

    def import_documents_batch(
        self,
        file_paths: list[str],
        additional_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        results = []
        total_chunks = 0
        total_failed = 0
        for fp in file_paths:
            result = self.import_document(fp, additional_metadata)
            results.append(result)
            if result.get("success"):
                total_chunks += result.get("chunk_count", 0)
            total_failed += result.get("failed_chunks", 0)
        return {
            "total_files": len(file_paths),
            "total_chunks": total_chunks,
            "total_failed_chunks": total_failed,
            "results": results,
        }

    def get_import_history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._import_history[-limit:]

    def get_document_stats(self) -> dict[str, Any]:
        if not self._import_history:
            return {"total_imports": 0, "total_chunks": 0, "avg_chunks_per_import": 0}
        total_chunks = sum(h.get("chunk_count", 0) for h in self._import_history)
        return {
            "total_imports": len(self._import_history),
            "total_chunks": total_chunks,
            "avg_chunks_per_import": round(total_chunks / len(self._import_history), 1),
        }

    @staticmethod
    def _error_result(file_name: str, error: str) -> dict[str, Any]:
        return {
            "file_name": file_name,
            "chunk_count": 0,
            "success": False,
            "error": error,
        }
