"""T3 补测（四）：JSON 导入器与统计报告。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.importer.importers._common import ImportReport, ImportStats
from app.knowledge_graph.importer.importers.machine_importer import import_machines
from app.knowledge_graph.importer.importers.material_importer import import_materials
from app.knowledge_graph.importer.importers.process_importer import import_process_rules
from app.knowledge_graph.importer.importers.tool_importer import import_tools

pytestmark = pytest.mark.unit


def _write(tmp_path: Path, name: str, payload) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_import_materials_maps_and_dedups(tmp_path: Path):
    source = _write(tmp_path, "materials.json", [
        {"id": "M-45", "name": "45钢", "category": "steel", "density_gcm3": 7.85,
         "hardness_hb": 220, "description": "常用结构钢"},
        {"id": "M-45", "name": "45钢", "category": "steel"},  # 同文件重复
        {"id": "M-NONAME", "category": "steel"},             # 缺 name → 跳过
        {"id": "M-AL", "name": "6061铝合金", "category": "aluminium", "tensile_strength_mpa": 310},
    ])
    graph = GraphStore(auto_load=False)
    stats = import_materials(graph, source_path=source)

    assert stats.success == 2
    assert stats.duplicate >= 1
    assert stats.failed >= 1
    assert stats.elapsed_ms >= 0
    assert stats.to_dict()["node_type"] == "material"

    node_ids = [node["node_id"] for node in graph.list_nodes_by_type("material")]
    assert len(node_ids) == 2
    steel = graph.get_node(node_ids[0]) or graph.get_node(node_ids[1])
    assert steel["properties"]["raw_id"].startswith("M-")

    # 二次导入同文件：全部计为重复
    again = import_materials(graph, source_path=source)
    assert again.success == 0 and again.duplicate >= 2


def test_import_machines_fills_properties(tmp_path: Path):
    source = _write(tmp_path, "machines.json", [
        {"id": "VMC850", "name": "VMC850", "type": "vertical_mill",
         "spindle_power_kw": 7.5, "travel_xyz_mm": [800, 500, 500], "tool_changer_capacity": 24},
        {"name": "无 ID 机床", "type": "lathe"},
    ])
    graph = GraphStore(auto_load=False)
    stats = import_machines(graph, source_path=source)

    assert stats.success == 1
    assert stats.failed == 1
    machine = graph.list_nodes_by_type("machine")[0]
    assert machine["properties"]["name"] == "VMC850"
    assert machine["properties"]["spindle_power_kw"] == 7.5
    assert machine["properties"]["tool_changer_capacity"] == 24


def test_import_missing_file_records_failure(tmp_path: Path):
    graph = GraphStore(auto_load=False)
    stats = import_materials(graph, source_path=tmp_path / "absent.json", retries=1)
    assert stats.success == 0
    assert stats.failed >= 1
    assert stats.error_messages


def test_import_report_markdown_and_dict():
    stats = ImportStats(source_file="materials.json", node_type="material")
    stats.success = 3
    stats.duplicate = 1
    stats.failed = 2
    stats.error_messages = ["missing name"]
    stats.node_type_breakdown = {"material": 3}
    stats.elapsed_ms = 12.5

    report = ImportReport(materials=stats, total_nodes=6, overall_success=True,
                          overall_message="部分成功", started_at=1.0, finished_at=1.5)
    payload = report.to_dict()
    assert payload["files"]["materials"]["success"] == 3
    assert payload["overall_success"] is True and payload["elapsed_s"] == 0.5
    markdown = report.render_markdown()
    assert "materials.json" in markdown
    assert "3" in markdown


def test_import_tools_creates_nodes_and_feature_edges(tmp_path: Path):
    source = _write(tmp_path, "tools.json", [
        {"id": "T1", "name": "D10 立铣刀", "series": "endmill", "diameter_mm": 10,
         "material": " carbide", "application": "roughing"},
    ])
    graph = GraphStore(auto_load=False)
    stats = import_tools(graph, source_path=source)
    assert stats.success == 1
    tools = graph.list_nodes_by_type("tool")
    assert tools and tools[0]["properties"]["diameter_mm"] == 10


def test_import_process_rules_creates_process_node(tmp_path: Path):
    source = _write(tmp_path, "process_rules.json", [
        {"id": "R-001", "name": "钻孔前先铣基准面", "category": "sequence",
         "description": "先加工基准面，再钻孔"},
        {"id": "R-001", "name": "重复规则", "description": "重复"},
        {"name": "缺 id 规则"},
    ])
    graph = GraphStore(auto_load=False)
    stats = import_process_rules(graph, source_path=source)
    assert stats.success == 1
    assert stats.duplicate == 1
    assert stats.failed == 1
    processes = graph.list_nodes_by_type("process")
    assert len(processes) == 1
    assert processes[0]["properties"]["name"] == "钻孔前先铣基准面"
