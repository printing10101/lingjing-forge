"""创建测试用 PDF 和 Excel 样本文件"""

from pathlib import Path


def create_sample_pdf():
    """创建测试用 PDF 文件"""
    try:
        import fitz  # PyMuPDF
        
        output_path = Path("docs/knowledge-graph/samples/sample-process-card.pdf")
        doc = fitz.open()
        page = doc.new_page()
        
        # 标题
        page.insert_text((100, 50), "机械加工工艺过程卡片", fontsize=16)
        
        # 基本信息
        y = 100
        page.insert_text((100, y), "零件名称：传动轴", fontsize=12)
        page.insert_text((100, y+20), "材料：45钢", fontsize=12)
        page.insert_text((100, y+40), "图号：TS-2024-001", fontsize=12)
        page.insert_text((100, y+60), "重量：2.5kg", fontsize=12)
        
        # 表格数据 - 使用简单的文本格式
        y = 200
        headers = ["工序号", "工序名称", "设备", "切削速度", "进给量", "切削深度"]
        page.insert_text((100, y), "  |  ".join(headers), fontsize=10)
        
        data = [
            ["10", "车端面", "C6140", "80", "0.3", "2.0"],
            ["20", "钻中心孔", "Z525", "30", "0.2", "1.5"],
            ["30", "粗车外圆", "C6140", "60", "0.4", "3.0"],
            ["40", "精车外圆", "C6140", "100", "0.2", "0.5"],
            ["50", "铣键槽", "X6132", "50", "0.1", "2.0"],
        ]
        
        for row in data:
            y += 20
            page.insert_text((100, y), "  |  ".join(row), fontsize=10)
        
        # 技术要求
        y += 40
        page.insert_text((100, y), "技术要求：", fontsize=12)
        page.insert_text((100, y+20), "1. 表面粗糙度：Ra1.6", fontsize=10)
        page.insert_text((100, y+40), "2. 热处理：调质处理 HRC28-32", fontsize=10)
        page.insert_text((100, y+60), "3. 未注倒角：C1", fontsize=10)
        
        doc.save(str(output_path))
        doc.close()
        print(f"PDF 样本文件已创建：{output_path}")
        return True
    except Exception as e:
        print(f"创建 PDF 样本文件失败：{e}")
        return False


def create_sample_excel():
    """创建测试用 Excel/CSV 文件"""
    try:
        import csv
        
        output_path = Path("docs/knowledge-graph/samples/sample-process.csv")
        
        # 创建 CSV 文件（作为 Excel 的替代格式）
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # 写入表头
            headers = ["工序号", "工序名称", "设备", "切削速度(m/min)", "进给量(mm/r)", "切削深度(mm)", "工时(min)"]
            writer.writerow(headers)
            
            # 写入数据行
            data = [
                [10, "车端面", "C6140", 80, 0.3, 2.0, 5],
                [20, "钻中心孔", "Z525", 30, 0.2, 1.5, 3],
                [30, "粗车外圆", "C6140", 60, 0.4, 3.0, 8],
                [40, "精车外圆", "C6140", 100, 0.2, 0.5, 6],
                [50, "铣键槽", "X6132", 50, 0.1, 2.0, 4],
            ]
            
            for row in data:
                writer.writerow(row)
        
        print(f"CSV 样本文件已创建：{output_path}")
        return True
    except Exception as e:
        print(f"创建 CSV 样本文件失败：{e}")
        return False


if __name__ == "__main__":
    create_sample_pdf()
    create_sample_excel()
