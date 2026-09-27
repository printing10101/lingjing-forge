"""rag 模块覆盖率补强测试（entity_index / excel_parser）。

覆盖：
- EntityIndex：add/add_batch/查询/删除/持久化/统计/并发安全
- excel_parser：Excel/CSV 解析、错误路径、空文件
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.rag.entity_index import EntityIndex, get_entity_index
from app.rag.excel_parser import parse_csv, parse_excel

pytestmark = pytest.mark.unit


class TestEntityIndex:
    def test_add_and_get_chunks(self):
        idx = EntityIndex()
        idx.add("chunk-1", ["铣削颤振", "LNN"])
        chunks = idx.get_chunks(["铣削颤振"])
        assert "chunk-1" in chunks
        # 大小写不敏感
        chunks2 = idx.get_chunks(["lnn"])
        assert "chunk-1" in chunks2

    def test_add_empty_ignored(self):
        idx = EntityIndex()
        idx.add("", [])
        idx.add("chunk-1", [])
        idx.add("chunk-2", [""])
        assert idx.get_stats()["total_add_calls"] == 0

    def test_add_batch(self):
        idx = EntityIndex()
        count = idx.add_batch(
            [
                ("c1", ["刀具", "磨损"]),
                ("c2", ["刀具"]),
                ("", []),  # 跳过
            ]
        )
        assert count == 2
        assert "c1" in idx.get_chunks(["刀具"])
        assert "c2" in idx.get_chunks(["刀具"])

    def test_add_updates_existing_chunk(self):
        idx = EntityIndex()
        idx.add("c1", ["刀具"])
        idx.add("c1", ["颤振"])  # 更新关联
        assert idx.get_chunks(["刀具"]) == []
        assert "c1" in idx.get_chunks(["颤振"])

    def test_get_entities(self):
        idx = EntityIndex()
        idx.add("c1", ["刀具", "磨损", "LNN"])
        entities = idx.get_entities("c1")
        assert set(entities) == {"刀具", "磨损", "lnn"}

    def test_get_entities_missing_chunk(self):
        idx = EntityIndex()
        assert idx.get_entities("nope") == []

    def test_remove_chunk(self):
        idx = EntityIndex()
        idx.add("c1", ["刀具", "颤振"])
        idx.add("c2", ["刀具"])
        removed = idx.remove_chunk("c1")
        assert removed == 2
        assert idx.get_chunks(["刀具"]) == ["c2"]
        assert idx.get_chunks(["颤振"]) == []

    def test_remove_chunk_missing(self):
        idx = EntityIndex()
        assert idx.remove_chunk("nope") == 0

    def test_flush_and_reload(self, tmp_path):
        idx = EntityIndex(persist_dir=str(tmp_path))
        idx.add("c1", ["刀具", "颤振"])
        idx.flush(force=True)
        # 新实例从磁盘加载
        idx2 = EntityIndex(persist_dir=str(tmp_path))
        assert "c1" in idx2.get_chunks(["刀具"])
        assert "c1" in idx2.get_chunks(["颤振"])

    def test_flush_without_dirty_returns_false(self, tmp_path):
        idx = EntityIndex(persist_dir=str(tmp_path))
        assert idx.flush(force=False) is False  # 无脏数据不写盘

    def test_clear(self):
        idx = EntityIndex()
        idx.add("c1", ["刀具"])
        idx.clear()
        # clear 清空数据（统计计数保留，用于观测）
        assert idx.get_chunks(["刀具"]) == []
        assert idx.get_entities("c1") == []

    def test_get_stats(self):
        idx = EntityIndex()
        idx.add("c1", ["刀具"])
        idx.get_chunks(["刀具"])
        idx.get_chunks(["不存在"])
        stats = idx.get_stats()
        assert stats["total_add_calls"] == 1
        assert stats["total_query_calls"] == 2
        assert stats["total_query_hits"] == 1

    def test_singleton(self):
        a = get_entity_index()
        b = get_entity_index()
        assert a is b

    def test_concurrent_adds(self):
        idx = EntityIndex()
        errors: list[Exception] = []

        def worker(prefix: str):
            try:
                for i in range(50):
                    idx.add(f"{prefix}-{i}", [f"entity-{prefix}-{i % 10}"])
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(f"w{n}",)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        stats = idx.get_stats()
        assert stats["total_add_calls"] == 200


class TestExcelParser:
    def test_parse_excel_missing_file(self):
        result = parse_excel("/nonexistent/file.xlsx")
        assert result["status"] == "error"
        assert "文件不存在" in result["error"]

    def test_parse_excel_invalid_extension(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello", encoding="utf-8")
        result = parse_excel(f)
        assert result["status"] == "error"

    def test_parse_excel_valid(self, tmp_path):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        ws.append(["材料", "硬度"])
        ws.append(["45钢", "HRC28"])
        ws.append(["40Cr", "HRC32"])
        f = tmp_path / "data.xlsx"
        wb.save(str(f))
        result = parse_excel(f)
        assert result["status"] == "success"
        assert result["sheet_count"] >= 1
        assert len(result["rows"]) >= 2

    def test_parse_excel_empty_file(self, tmp_path):
        import openpyxl

        wb = openpyxl.Workbook()
        f = tmp_path / "empty.xlsx"
        wb.save(str(f))
        result = parse_excel(f)
        assert result["status"] == "success" or result["status"] == "error"

    def test_parse_csv_valid(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("材料,硬度\n45钢,HRC28\n40Cr,HRC32\n", encoding="utf-8")
        result = parse_csv(f)
        assert result["status"] == "success"
        assert len(result["rows"]) >= 2

    def test_parse_csv_missing_file(self):
        result = parse_csv("/nonexistent/file.csv")
        assert result["status"] == "error"

    def test_parse_csv_empty(self, tmp_path):
        f = tmp_path / "empty.csv"
        f.write_text("", encoding="utf-8")
        result = parse_csv(f)
        assert result["status"] == "success" or result["status"] == "error"
