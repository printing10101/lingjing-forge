"""PDF文档解析模块

基于pymupdf库实现PDF文档内容的高效提取，支持多页PDF文档的连续解析，
保留文本的原始排版信息和段落结构，特别优化了中文文档解析能力。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_pdf(file_path: str | Path) -> dict[str, Any]:
    """解析PDF文档，提取文本和表格内容

    Args:
        file_path: PDF文件路径

    Returns:
        包含解析结果的字典，结构如下：
        {
            "status": "success" | "error",
            "file_name": str,
            "file_size": int,
            "page_count": int,
            "text": str,  # 完整文本内容
            "tables": list[dict],  # 提取的表格列表
            "parse_time_ms": float,
            "error": str | None
        }
    """
    start_time = time.time()
    file_path = Path(file_path)

    result = {
        "status": "error",
        "file_name": file_path.name,
        "file_size": 0,
        "page_count": 0,
        "text": "",
        "tables": [],
        "parse_time_ms": 0.0,
        "error": None,
    }

    # 检查文件是否存在
    if not file_path.exists():
        error_msg = f"文件不存在: {file_path}"
        logger.error(error_msg)
        result["error"] = error_msg
        return result

    # 获取文件大小
    try:
        result["file_size"] = file_path.stat().st_size
    except OSError as e:
        logger.warning("无法获取文件大小: %s", e)

    # 解析PDF
    try:
        import fitz  # PyMuPDF

        logger.info("开始解析PDF文件: %s", file_path.name)

        # P0-1 修复：使用 with 语句确保异常路径下文档句柄被释放
        with fitz.open(str(file_path)) as doc:
            result["page_count"] = len(doc)

            all_text = []
            all_tables = []

            for page_num in range(len(doc)):
                page = doc[page_num]

                # 提取文本
                text = page.get_text()
                if text.strip():
                    all_text.append(f"--- 第 {page_num + 1} 页 ---\n{text}")

                # 提取表格
                tables = _extract_tables_from_page(page, page_num)
                all_tables.extend(tables)

            result["text"] = "\n\n".join(all_text)
            result["tables"] = all_tables
            result["status"] = "success"

            parse_time = (time.time() - start_time) * 1000
            result["parse_time_ms"] = round(parse_time, 2)

            logger.info(
                f"PDF解析完成: {file_path.name}, "
                f"{result['page_count']}页, "
                f"{len(all_tables)}个表格, "
                f"耗时{parse_time:.0f}ms"
            )

    except ImportError:
        error_msg = "PyMuPDF(fitz)库未安装，请运行: pip install pymupdf"
        logger.error(error_msg)
        result["error"] = error_msg
    except (OSError, ValueError, RuntimeError):
        error_msg = "PDF解析失败: 文件格式错误或损坏，请检查文件"
        logger.exception(error_msg)
        result["error"] = error_msg

    return result


def _extract_tables_from_page(page: Any, page_num: int) -> list[dict[str, Any]]:
    """从PDF页面中提取表格

    Args:
        page: PyMuPDF页面对象
        page_num: 页码（从0开始）

    Returns:
        表格列表，每个表格包含表头和数据行
    """
    tables = []

    try:
        # 使用PyMuPDF的表格提取功能
        tab_finder = page.find_tables()

        for idx, table in enumerate(tab_finder.tables):
            try:
                # 提取表格数据
                data = table.extract()

                if not data or len(data) < 2:
                    continue

                # 第一行作为表头
                headers = [str(cell).strip() if cell else "" for cell in data[0]]

                # 剩余行作为数据
                rows = []
                for row_data in data[1:]:
                    row = [str(cell).strip() if cell else "" for cell in row_data]
                    rows.append(row)

                table_info = {
                    "page": page_num + 1,
                    "table_index": idx,
                    "headers": headers,
                    "rows": rows,
                    "row_count": len(rows),
                    "column_count": len(headers),
                }

                tables.append(table_info)

            except (OSError, ValueError, TypeError, KeyError) as e:
                logger.warning("提取第%s页第%s个表格失败: %s", page_num + 1, idx + 1, e)
                continue

    except (OSError, RuntimeError) as e:
        logger.debug("第%s页表格提取失败: %s", page_num + 1, e)

    return tables


def parse_pdf_text_only(file_path: str | Path) -> dict[str, Any]:
    """仅提取PDF文本内容（不提取表格）

    Args:
        file_path: PDF文件路径

    Returns:
        包含文本内容的字典
    """
    file_path = Path(file_path)

    result = {"status": "error", "file_name": file_path.name, "text": "", "error": None}

    if not file_path.exists():
        result["error"] = f"文件不存在: {file_path}"
        return result

    try:
        import fitz

        # P0-1 修复：使用 with 语句确保异常路径下文档句柄被释放
        with fitz.open(str(file_path)) as doc:
            all_text = []

            for page in doc:
                text = page.get_text()
                if text.strip():
                    all_text.append(text)

            result["text"] = "\n\n".join(all_text)
            result["status"] = "success"

    except (OSError, ValueError, RuntimeError) as e:
        result["error"] = "PDF文本提取失败: 文件格式错误或损坏，请检查文件"
        logger.exception("PDF文本提取失败: %s", e)

    return result


if __name__ == "__main__":
    # 测试代码
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    if len(sys.argv) < 2:
        logger.info("用法: python pdf_parser.py <pdf_file>")
        sys.exit(1)

    pdf_file = sys.argv[1]
    result = parse_pdf(pdf_file)

    logger.info("\n解析状态: %s", result["status"])
    logger.info("文件名: %s", result["file_name"])
    logger.info("文件大小: %s bytes", result["file_size"])
    logger.info("页数: %s", result["page_count"])
    logger.info("表格数量: %s", len(result["tables"]))
    logger.info(f"解析耗时: {result['parse_time_ms']:.2f}ms")

    if result["error"]:
        logger.error("错误: %s", result["error"])

    if result["tables"]:
        logger.info("\n提取的表格:")
        for i, table in enumerate(result["tables"], 1):
            logger.info("\n表格 %s (第%s页):", i, table["page"])
            logger.info("  表头: %s", table["headers"])
            logger.info("  行数: %s", table["row_count"])
            if table["rows"]:
                logger.info("  首行数据: %s", table["rows"][0])
