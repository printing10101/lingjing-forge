# PDF/Excel 工艺文档结构化数据提取与知识图谱对接系统

## 1. 设计思路

### 1.1 系统定位
本系统面向工艺师日常工作中90%使用PDF/Excel文档的实际业务场景，提供高性能、高可靠性的文档解析能力。系统采用离线批处理模式，专注于解析质量，为后续工艺知识管理平台的构建提供高质量的数据基础。

### 1.2 设计原则
- **模块化设计**：PDF解析、Excel解析、知识图谱对接各自独立，便于维护和扩展
- **优雅降级**：对于无法解析的内容或异常格式，给出明确的错误提示和日志记录，不影响整体解析流程
- **中文优先**：优先实现并优化中文文档解析能力，预留英文文档扩展接口
- **错误容忍**：表格识别准确率目标为80%以上，对于复杂表格结构不追求100%完美识别

### 1.3 技术选型
- **PDF解析**：基于PyMuPDF（fitz）库，支持多页PDF文档的连续解析，保留文本的原始排版信息和段落结构
- **Excel解析**：基于openpyxl库，支持.xlsx格式，能够识别合并单元格、冻结窗格等复杂表格结构
- **CSV解析**：作为Excel的补充，支持.csv格式的表格数据提取
- **知识图谱对接**：设计标准化数据接口，实现实体映射、关系建立、属性填充等功能

## 2. 模块架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    文档解析系统架构                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  PDF解析模块  │  │ Excel解析模块 │  │  CSV解析模块  │      │
│  │ pdf_parser   │  │ excel_parser │  │ excel_parser │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│         │                 │                 │               │
│         └────────────────┬┴─────────────────┘               │
│                          ▼                                  │
│              ┌─────────────────────┐                        │
│              │   表格结构识别引擎   │                        │
│              │  - 表头识别          │                        │
│              │  - 行列关系解析      │                        │
│              │  - 层级结构分析      │                        │
│              └──────────┬──────────┘                        │
│                         ▼                                   │
│              ┌─────────────────────┐                        │
│              │   知识图谱对接接口   │                        │
│              │  kg_interface       │                        │
│              │  - 实体映射          │                        │
│              │  - 关系建立          │                        │
│              │  - 属性填充          │                        │
│              └──────────┬──────────┘                        │
│                         ▼                                   │
│              ┌─────────────────────┐                        │
│              │   知识图谱系统       │                        │
│              │  (Neo4j/其他图数据库)│                        │
│              └─────────────────────┘                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 3. 核心算法说明

### 3.1 PDF表格提取算法

PDF表格提取基于PyMuPDF的`find_tables()`方法，核心流程如下：

1. **页面遍历**：逐页遍历PDF文档，提取每页的文本和表格
2. **表格检测**：使用`page.find_tables()`检测页面中的表格结构
3. **数据提取**：调用`table.extract()`获取表格的二维数组数据
4. **表头识别**：将第一行作为表头，剩余行作为数据行
5. **结构化表示**：将表格转换为包含headers、rows、row_count、column_count的字典结构

```python
# 核心代码片段
tab_finder = page.find_tables()
for table in tab_finder.tables:
    data = table.extract()
    headers = [str(cell).strip() if cell else "" for cell in data[0]]
    rows = [[str(cell).strip() if cell else "" for cell in row] for row in data[1:]]
```

### 3.2 Excel表格提取算法

Excel表格提取基于openpyxl库，核心流程如下：

1. **工作簿加载**：使用`openpyxl.load_workbook()`加载Excel文件，启用data_only模式
2. **工作表遍历**：遍历所有工作表，提取每个工作表的数据
3. **空行过滤**：过滤完全空行，保留有效数据
4. **表头识别**：将第一行作为表头，剩余行作为数据行
5. **合并单元格处理**：openpyxl自动处理合并单元格，返回合并后的值

```python
# 核心代码片段
wb = openpyxl.load_workbook(file_path, data_only=True)
for sheet_name in wb.sheetnames:
    ws = wb[sheet_name]
    data = []
    for row in ws.iter_rows(values_only=True):
        if any(cell is not None for cell in row):
            data.append([str(cell) if cell is not None else "" for cell in row])
```

### 3.3 知识图谱实体映射算法

知识图谱对接接口将表格数据转换为实体和关系，核心流程如下：

1. **表头映射**：将中文表头映射为知识图谱属性名（如"工序号"→"step_number"）
2. **实体创建**：为表格的每一行创建一个实体，类型为ProcessStep
3. **属性填充**：根据表头映射，将单元格值填充为实体属性
4. **关系建立**：建立工序之间的顺序关系（next_step）

```python
# 核心代码片段
for row_idx, row in enumerate(rows):
    entity = {
        "id": f"entity_{table_idx}_{row_idx}",
        "type": "ProcessStep",
        "properties": {}
    }
    for col_idx, header in enumerate(headers):
        if col_idx < len(row):
            prop_name = _map_header_to_property(header)
            entity["properties"][prop_name] = row[col_idx]
    entities.append(entity)
```

## 4. 实现细节

### 4.1 PDF解析模块（pdf_parser.py）

**主要函数**：
- `parse_pdf(file_path)`：解析PDF文档，提取文本和表格内容
- `parse_pdf_text_only(file_path)`：仅提取PDF文本内容（不提取表格）
- `_extract_tables_from_page(page, page_num)`：从PDF页面中提取表格

**返回结构**：
```python
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
```

**错误处理**：
- 文件不存在：返回status="error"，记录错误日志
- PyMuPDF未安装：返回status="error"，提示安装命令
- 解析异常：返回status="error"，记录异常信息，不影响后续处理

### 4.2 Excel解析模块（excel_parser.py）

**主要函数**：
- `parse_excel(file_path)`：解析Excel文档，提取表格数据
- `parse_csv(file_path)`：解析CSV文件，提取表格数据
- `_extract_table_from_sheet(ws, sheet_idx, sheet_name)`：从Excel工作表中提取表格数据

**返回结构**：
```python
{
    "status": "success" | "error",
    "file_name": str,
    "file_size": int,
    "sheet_count": int,
    "tables": list[dict],  # 提取的表格列表
    "rows": list[dict],  # 所有数据行
    "parse_time_ms": float,
    "error": str | None
}
```

**错误处理**：
- 文件不存在：返回status="error"，记录错误日志
- openpyxl未安装：返回status="error"，提示安装命令
- 工作表提取失败：记录警告日志，跳过该工作表，继续处理其他工作表

### 4.3 知识图谱对接接口（kg_interface.py）

**主要函数**：
- `对接知识图谱(tables)`：将解析的表格数据对接到知识图谱系统
- `_extract_kg_from_table(table, table_idx)`：从表格数据中提取知识图谱实体和关系
- `_map_header_to_property(header)`：将表头名称映射为知识图谱属性名
- `_store_to_knowledge_graph(entities, relations)`：存储实体和关系到知识图谱
- `convert_table_to_kg_entities(table)`：将单个表格转换为知识图谱实体格式

**表头映射表**：
| 中文表头 | 属性名称 |
|---------|---------|
| 工序号 | step_number |
| 工序名称 | step_name |
| 设备 | equipment |
| 切削速度 | cutting_speed |
| 进给量 | feed_rate |
| 切削深度 | cutting_depth |
| 工时 | processing_time |
| 刀具编号 | tool_id |
| 刀具名称 | tool_name |
| 规格 | specification |
| 数量 | quantity |
| 备注 | remark |

## 5. 接口定义

### 5.1 PDF解析接口

```python
def parse_pdf(file_path: str | Path) -> dict[str, Any]:
    """解析PDF文档，提取文本和表格内容
    
    Args:
        file_path: PDF文件路径
        
    Returns:
        包含解析结果的字典
    """
```

### 5.2 Excel解析接口

```python
def parse_excel(file_path: str | Path) -> dict[str, Any]:
    """解析Excel文档，提取表格数据
    
    Args:
        file_path: Excel文件路径（.xls或.xlsx）
        
    Returns:
        包含解析结果的字典
    """
```

### 5.3 CSV解析接口

```python
def parse_csv(file_path: str | Path) -> dict[str, Any]:
    """解析CSV文件，提取表格数据
    
    Args:
        file_path: CSV文件路径
        
    Returns:
        包含解析结果的字典
    """
```

### 5.4 知识图谱对接接口

```python
def 对接知识图谱(tables: list[dict[str, Any]]) -> str:
    """将解析的表格数据对接到知识图谱系统
    
    Args:
        tables: 从PDF/Excel解析出的表格列表
        
    Returns:
        对接状态: "success" | "error"
    """
```

## 6. 使用说明

### 6.1 环境准备

安装依赖库：
```bash
pip install pymupdf openpyxl pytest pytest-cov
```

### 6.2 PDF解析使用示例

```python
from app.rag.pdf_parser import parse_pdf
import logging

logging.basicConfig(level=logging.INFO)

# 解析PDF文件
result = parse_pdf('docs/knowledge-graph/samples/sample-process-card.pdf')

print(f'解析状态: {result["status"]}')
print(f'页数: {result["page_count"]}')
print(f'表格数量: {len(result["tables"])}')
print(f'解析耗时: {result["parse_time_ms"]:.2f}ms')

if result['tables']:
    for i, table in enumerate(result['tables'], 1):
        print(f'\n表格 {i} (第{table["page"]}页):')
        print(f'  表头: {table["headers"]}')
        print(f'  行数: {table["row_count"]}')
```

### 6.3 Excel解析使用示例

```python
from app.rag.excel_parser import parse_excel
import logging

logging.basicConfig(level=logging.INFO)

# 解析Excel文件
result = parse_excel('docs/knowledge-graph/samples/sample-process.xlsx')

print(f'解析状态: {result["status"]}')
print(f'工作表数量: {result["sheet_count"]}')
print(f'表格数量: {len(result["tables"])}')
print(f'数据行数: {len(result["rows"])}')

if result['tables']:
    for i, table in enumerate(result['tables'], 1):
        print(f'\n表格 {i} ({table["sheet_name"]}):')
        print(f'  表头: {table["headers"]}')
        print(f'  行数: {table["row_count"]}')
```

### 6.4 知识图谱对接使用示例

```python
from app.rag.pdf_parser import parse_pdf
from app.rag.excel_parser import parse_excel
from app.rag.kg_interface import 对接知识图谱

# 解析文档
pdf_result = parse_pdf('docs/knowledge-graph/samples/sample-process-card.pdf')
excel_result = parse_excel('docs/knowledge-graph/samples/sample-process.xlsx')

# 对接知识图谱
tables = pdf_result["tables"] + excel_result["tables"]
kg_status = 对接知识图谱(tables)

print(f'KG对接状态: {kg_status}')
```

### 6.5 命令行使用

PDF解析：
```bash
cd python
python -m app.rag.pdf_parser docs/knowledge-graph/samples/sample-process-card.pdf
```

Excel解析：
```bash
cd python
python -m app.rag.excel_parser docs/knowledge-graph/samples/sample-process.xlsx
```

知识图谱对接测试：
```bash
cd python
python -m app.rag.kg_interface
```

## 7. 维护指南

### 7.1 代码结构

```
python/app/rag/
├── pdf_parser.py          # PDF解析模块
├── excel_parser.py        # Excel/CSV解析模块
├── kg_interface.py        # 知识图谱对接接口
└── tests/
    └── test_parsers.py    # 单元测试
```

### 7.2 测试执行

运行单元测试：
```bash
cd python
pytest app/rag/tests/test_parsers.py -v --cov=app.rag --cov-report=term
```

预期结果：
- 所有单元测试用例通过
- 代码覆盖率报告显示覆盖率达到80%以上

### 7.3 性能优化建议

1. **PDF解析**：
   - 对于大型PDF文件（>100页），建议分批处理
   - 使用`parse_pdf_text_only()`仅提取文本，减少表格提取开销

2. **Excel解析**：
   - 对于大型Excel文件，建议使用`data_only=True`模式
   - 过滤空行可以减少内存占用

3. **知识图谱对接**：
   - 批量导入实体和关系，减少数据库交互次数
   - 使用索引优化实体查询性能

### 7.4 扩展接口

1. **添加新的表头映射**：
   在`kg_interface.py`的`_map_header_to_property()`函数中添加映射关系

2. **支持新的文件格式**：
   在`excel_parser.py`中添加新的解析函数，参考`parse_csv()`的实现

3. **对接实际知识图谱系统**：
   在`kg_interface.py`的`_store_to_knowledge_graph()`函数中实现实际的图数据库接口

### 7.5 常见问题

1. **PDF表格提取不准确**：
   - PyMuPDF的表格检测基于线条和边框，对于无边框表格可能无法识别
   - 可以尝试调整PDF文档的格式，添加表格边框

2. **Excel合并单元格处理**：
   - openpyxl自动处理合并单元格，返回合并后的值
   - 对于复杂合并结构，建议手动检查提取结果

3. **中文乱码问题**：
   - PDF解析时确保使用UTF-8编码
   - CSV解析时指定`encoding='utf-8'`参数

### 7.6 版本历史

- v1.0.0 (2026-06-15)：初始版本，实现PDF/Excel解析和知识图谱对接基础功能
  - 支持PDF文本和表格提取
  - 支持Excel和CSV表格提取
  - 实现知识图谱实体映射和关系建立
  - 编写完整的单元测试和技术文档

## 8. 附录

### 8.1 依赖库版本

- PyMuPDF (fitz): >=1.23.0
- openpyxl: >=3.1.0
- pytest: >=7.0.0
- pytest-cov: >=4.0.0

### 8.2 测试样本文件

测试样本文件位于`docs/knowledge-graph/samples/`目录下：
- `sample-process-card.pdf`：PDF工艺卡片样本
- `sample-process.csv`：CSV工艺表格样本（作为Excel的替代）

### 8.3 参考资料

- PyMuPDF文档：https://pymupdf.readthedocs.io/
- openpyxl文档：https://openpyxl.readthedocs.io/
- 参考代码：`python/app/rag/document_importer.py`
