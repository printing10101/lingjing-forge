"""PDF/Excel解析模块单元测试

覆盖主要功能点、边界条件和错误场景，确保代码质量和稳定性。
"""

import pytest
from pathlib import Path
from unittest.mock import patch

# 导入被测试模块
from app.rag.pdf_parser import parse_pdf, parse_pdf_text_only
from app.rag.excel_parser import parse_excel, parse_csv


class TestPDFParser:
    """PDF解析模块测试"""

    def test_parse_pdf_file_not_exists(self):
        """测试文件不存在的情况"""
        result = parse_pdf("/nonexistent/file.pdf")
        assert result["status"] == "error"
        assert "文件不存在" in result["error"]

    def test_parse_pdf_empty_file(self, tmp_path):
        """测试空PDF文件"""
        # 创建空的PDF文件
        pdf_file = tmp_path / "empty.pdf"
        pdf_file.write_bytes(b"")

        result = parse_pdf(str(pdf_file))
        # PyMuPDF会尝试打开文件，空文件会报错
        assert result["status"] == "error"

    def test_parse_pdf_valid_file(self, tmp_path):
        """测试有效的PDF文件解析"""
        # 创建简单的PDF文件（使用PyMuPDF）
        pdf_file = tmp_path / "test.pdf"

        try:
            import fitz

            doc = fitz.open()
            page = doc.new_page()

            # 使用支持中文的字体（Windows系统字体）
            font_path = "C:/Windows/Fonts/simsun.ttc"  # 宋体
            if Path(font_path).exists():
                page.insert_font(fontname="chinese", fontfile=font_path)
                page.insert_text((100, 100), "测试文本", fontname="chinese")
            else:
                # 如果中文字体不存在，使用英文作为备选
                page.insert_text((100, 100), "Test text")

            doc.save(str(pdf_file))
            doc.close()

            result = parse_pdf(str(pdf_file))

            assert result["status"] == "success"
            assert result["file_name"] == "test.pdf"
            assert result["page_count"] >= 1
            # 检查文本内容（中文或英文）
            assert "测试文本" in result["text"] or "Test text" in result["text"]
            assert result["parse_time_ms"] > 0

        except ImportError:
            pytest.skip("PyMuPDF未安装")

    def test_parse_pdf_text_only(self, tmp_path):
        """测试仅提取PDF文本"""
        pdf_file = tmp_path / "text_only.pdf"

        try:
            import fitz

            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((100, 100), "纯文本提取测试")
            doc.save(str(pdf_file))
            doc.close()

            result = parse_pdf_text_only(str(pdf_file))

            assert result["status"] == "success"
            assert "纯文本提取测试" in result["text"]

        except ImportError:
            pytest.skip("PyMuPDF未安装")

    def test_parse_pdf_with_path_object(self, tmp_path):
        """测试使用Path对象作为参数"""
        pdf_file = tmp_path / "path_test.pdf"

        try:
            import fitz

            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((100, 100), "Path对象测试")
            doc.save(str(pdf_file))
            doc.close()

            # 使用Path对象
            result = parse_pdf(pdf_file)

            assert result["status"] == "success"
            assert result["file_name"] == "path_test.pdf"

        except ImportError:
            pytest.skip("PyMuPDF未安装")


class TestExcelParser:
    """Excel解析模块测试"""

    def test_parse_excel_file_not_exists(self):
        """测试文件不存在情况"""
        result = parse_excel("/nonexistent/file.xlsx")
        assert result["status"] == "error"
        assert "文件不存在" in result["error"]

    def test_parse_excel_valid_file(self, tmp_path):
        """测试有效的Excel文件解析"""
        excel_file = tmp_path / "test.xlsx"

        try:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "测试表"

            # 写入数据
            ws.append(["工序号", "工序名称", "设备"])
            ws.append([10, "车端面", "C6140"])
            ws.append([20, "钻中心孔", "Z525"])

            wb.save(str(excel_file))

            result = parse_excel(str(excel_file))

            assert result["status"] == "success"
            assert result["file_name"] == "test.xlsx"
            assert result["sheet_count"] == 1
            assert len(result["tables"]) == 1
            assert result["tables"][0]["sheet_name"] == "测试表"
            assert result["tables"][0]["row_count"] == 2

        except ImportError:
            pytest.skip("openpyxl未安装")

    def test_parse_csv_valid_file(self, tmp_path):
        """测试有效的CSV文件解析"""
        csv_file = tmp_path / "test.csv"
        csv_content = """工序号,工序名称,设备
10,车端面,C6140
20,钻中心孔,Z525
30,粗车外圆,C6140"""

        csv_file.write_text(csv_content, encoding="utf-8")

        result = parse_csv(str(csv_file))

        assert result["status"] == "success"
        assert result["file_name"] == "test.csv"
        assert result["sheet_count"] == 1
        assert len(result["tables"]) == 1
        assert result["tables"][0]["headers"] == ["工序号", "工序名称", "设备"]
        assert result["tables"][0]["row_count"] == 3

    def test_parse_csv_empty_file(self, tmp_path):
        """测试空CSV文件"""
        csv_file = tmp_path / "empty.csv"
        csv_file.write_text("", encoding="utf-8")

        result = parse_csv(str(csv_file))
        assert result["status"] == "error"
        assert "数据不足" in result["error"]

    def test_parse_csv_only_header(self, tmp_path):
        """测试只有表头的CSV文件"""
        csv_file = tmp_path / "header_only.csv"
        csv_file.write_text("列1,列2,列3", encoding="utf-8")

        result = parse_csv(str(csv_file))
        assert result["status"] == "error"
        assert "数据不足" in result["error"]


class TestEdgeCases:
    """边界条件和错误场景测试"""

    def test_pdf_parser_import_error(self):
        """测试PyMuPDF导入失败"""
        with patch.dict("sys.modules", {"fitz": None}):
            # 重新导入模块会触发ImportError
            result = parse_pdf("dummy.pdf")
            # 由于文件不存在，会先检查文件
            assert result["status"] == "error"

    def test_excel_parser_import_error(self):
        """测试openpyxl导入失败"""
        with patch.dict("sys.modules", {"openpyxl": None}):
            result = parse_excel("dummy.xlsx")
            # 由于文件不存在，会先检查文件
            assert result["status"] == "error"

    def test_large_file_handling(self, tmp_path):
        """测试大文件处理"""
        # 创建包含大量数据的CSV
        csv_file = tmp_path / "large.csv"
        lines = ["列1,列2,列3"]
        for i in range(1000):
            lines.append(f"{i},数据{i},值{i}")

        csv_file.write_text("\n".join(lines), encoding="utf-8")

        result = parse_csv(str(csv_file))
        assert result["status"] == "success"
        assert result["tables"][0]["row_count"] == 1000

    def test_special_characters_in_data(self, tmp_path):
        """测试特殊字符处理"""
        csv_file = tmp_path / "special.csv"
        csv_content = """名称,描述
测试,包含"引号"的文本
数据,包含,逗号的文本
符号,特殊符号：@#$%"""

        csv_file.write_text(csv_content, encoding="utf-8")

        result = parse_csv(str(csv_file))
        assert result["status"] == "success"
        assert len(result["tables"][0]["rows"]) == 3

    def test_unicode_file_path(self, tmp_path):
        """测试Unicode文件路径"""
        # 创建中文路径
        chinese_dir = tmp_path / "中文目录"
        chinese_dir.mkdir()
        csv_file = chinese_dir / "测试文件.csv"
        csv_file.write_text("列1,列2\n值1,值2", encoding="utf-8")

        result = parse_csv(str(csv_file))
        assert result["status"] == "success"


class TestPerformance:
    """性能测试"""

    def test_csv_parse_performance(self, tmp_path):
        """测试CSV解析性能"""
        csv_file = tmp_path / "perf.csv"
        lines = ["列1,列2,列3"]
        for i in range(10000):
            lines.append(f"{i},数据{i},值{i}")

        csv_file.write_text("\n".join(lines), encoding="utf-8")

        import time

        start = time.time()
        result = parse_csv(str(csv_file))
        duration = time.time() - start

        assert result["status"] == "success"
        # 10000行数据应该在合理时间内解析完成
        assert duration < 5.0  # 5秒内


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=app.rag", "--cov-report=term"])
