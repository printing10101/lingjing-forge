"""XM-100 知识图谱加工经验导入与查询展示脚本。

本脚本演示如何将 XM-100 桌面五轴加工中心的典型加工经验导入知识图谱，
并通过查询 API 实现工艺知识问答，为后续智能工艺推荐奠定基础。

展示场景：
    1. 导入 XM-100 加工经验记录（多材料、多刀具、多工序）
    2. 查询某材料适配的所有刀具（按可信度排序）
    3. 查询某刀具能加工的所有材料
    4. 图谱规模统计
    5. 模拟工艺问答：基于图谱回答典型工艺问题

输出：
    - 控制台问答展示
    - JSON 报告：python/output/xm100_demo/kg_experience_report.json
    - Markdown 报告：python/output/xm100_demo/kg_experience_report.md

注意：
    - 本脚本使用 GraphStore + FeedbackUpdater + KnowledgeGraphQueryAPI
    - 加工记录为模拟数据，基于 XM-100 能力与典型桌面加工经验
    - 反馈更新会调整 Tool-Material 关系的可信度
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.knowledge_graph.graph_store import GraphStore
from app.knowledge_graph.feedback_updater import FeedbackUpdater
from app.knowledge_graph.query_api import KnowledgeGraphQueryAPI


# XM-100 加工经验记录（模拟数据）


def build_xm100_records() -> list[dict[str, Any]]:
    """构建 XM-100 加工经验记录。

    每条记录代表一次实际加工，包含：
    - 机床、刀具、材料、工艺计划
    - 首次合格率、实测尺寸、表面粗糙度
    """
    records: list[dict[str, Any]] = []

    # 节点ID必须符合 <type>-<slug> 格式且以字母开头
    # 记录1：φ10立铣刀加工45钢方肩 — 标准工况，合格
    records.append(
        {
            "record_id": "XM100-REC-001",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-endmill_wc_d10",
            "workpiece_material": "material-45steel",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-shoulder-mill-steel-rough",
                        "name": "45钢方肩粗铣",
                        "feature_id": "feature-shoulder-pocket-10mm",
                        "params": {"speed": 6000, "feed": 800, "depth": 1.0},
                    },
                    {
                        "process_id": "process-shoulder-mill-steel-finish",
                        "name": "45钢方肩精铣",
                        "feature_id": "feature-shouldar-pocket-10mm",
                        "params": {"speed": 8000, "feed": 400, "depth": 0.2},
                    },
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"width": 50.02, "depth": 10.01},
            "surface_roughness": 1.6,
        }
    )

    # 记录2：φ6锥度球头刀加工铝合金曲面 — 五轴RTCP，合格
    records.append(
        {
            "record_id": "XM100-REC-002",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-endmill_wc_taper_d6",
            "workpiece_material": "material-aluminum_6061",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-5axis-curve-aluminum",
                        "name": "铝合金曲面五轴精加工",
                        "feature_id": "feature-curved-surface-r30",
                        "params": {"speed": 12000, "feed": 1500, "depth": 0.3},
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"profile_error": 0.015},
            "surface_roughness": 0.8,
        }
    )

    # 记录3：φ10立铣刀加工不锈钢 — 参数偏激进，不合格
    records.append(
        {
            "record_id": "XM100-REC-003",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-endmill_wc_d10",
            "workpiece_material": "material-stainless_304",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-slot-mill-304",
                        "name": "304不锈钢槽铣",
                        "feature_id": "feature-slot-8mm",
                        "params": {"speed": 8000, "feed": 1000, "depth": 2.0},
                    }
                ]
            },
            "first_pass_acceptance": False,
            "actual_dimensions": {"width": 8.05, "depth": 8.02},
            "surface_roughness": 3.2,
        }
    )

    # 记录4：φ1微型立铣刀加工ABS塑料 — 雕刻，合格
    records.append(
        {
            "record_id": "XM100-REC-004",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-endmill_wc_micro_d1",
            "workpiece_material": "material-abs",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-engraving-abs",
                        "name": "ABS塑料雕刻",
                        "feature_id": "feature-engraving-logo",
                        "params": {"speed": 15000, "feed": 500, "depth": 0.5},
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"depth": 0.48},
            "surface_roughness": 1.2,
        }
    )

    # 记录5：φ50面铣刀加工45钢平面 — 大切深，合格
    records.append(
        {
            "record_id": "XM100-REC-005",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-facemill_wc_d50",
            "workpiece_material": "material-45steel",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-facemill-steel",
                        "name": "45钢平面铣削",
                        "feature_id": "feature-flat-surface-100x100",
                        "params": {"speed": 4000, "feed": 600, "depth": 1.5},
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"flatness": 0.02},
            "surface_roughness": 1.6,
        }
    )

    # 记录6：φ6锥度球头刀加工黄铜 — 五轴叶轮，合格
    records.append(
        {
            "record_id": "XM100-REC-006",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-endmill_wc_taper_d6",
            "workpiece_material": "material-brass",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-5axis-impeller-brass",
                        "name": "黄铜叶轮五轴加工",
                        "feature_id": "feature-impeller-blade",
                        "params": {"speed": 10000, "feed": 800, "depth": 0.5},
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"blade_profile": 0.02},
            "surface_roughness": 0.8,
        }
    )

    # 记录7：φ10立铣刀加工45钢 — 第二次，合格（提升可信度）
    records.append(
        {
            "record_id": "XM100-REC-007",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-endmill_wc_d10",
            "workpiece_material": "material-45steel",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-shoulder-mill-steel-rough",
                        "name": "45钢方肩粗铣",
                        "feature_id": "feature-shoulder-pocket-10mm",
                        "params": {"speed": 6000, "feed": 800, "depth": 1.0},
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"width": 50.01, "depth": 10.00},
            "surface_roughness": 1.4,
        }
    )

    # 记录8：V型雕刻刀加工胡桃木 — 雕刻，合格
    records.append(
        {
            "record_id": "XM100-REC-008",
            "machine_id": "xmachine_xm100",
            "tool_id": "tool-vbit_wc_60deg",
            "workpiece_material": "material-wood_walnut",
            "process_plan": {
                "steps": [
                    {
                        "process_id": "process-vcarve-wood",
                        "name": "胡桃木V型雕刻",
                        "feature_id": "feature-vcarve-sign",
                        "params": {"speed": 18000, "feed": 1200, "depth": 1.0},
                    }
                ]
            },
            "first_pass_acceptance": True,
            "actual_dimensions": {"depth": 0.98},
            "surface_roughness": 3.2,
        }
    )

    return records


# 核心展示逻辑


def import_records(updater: FeedbackUpdater, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """导入加工记录到知识图谱。"""
    results = []
    for rec in records:
        stats = updater.update_from_machining_record(rec)
        results.append(
            {
                "record_id": rec["record_id"],
                "tool": rec["tool_id"],
                "material": rec["workpiece_material"],
                "first_pass": rec["first_pass_acceptance"],
                "stats": stats,
            }
        )
    return results


def query_tools_for_material(api: KnowledgeGraphQueryAPI, material_id: str) -> list[dict[str, Any]]:
    """查询某材料适配的所有刀具。"""
    return api.tools_for_material(material_id, min_confidence=0.0)


def query_materials_for_tool(api: KnowledgeGraphQueryAPI, tool_id: str) -> list[dict[str, Any]]:
    """查询某刀具能加工的所有材料。"""
    return api.materials_for_tool(tool_id, min_confidence=0.0)


def print_qa_section(api: KnowledgeGraphQueryAPI) -> list[dict[str, Any]]:
    """打印工艺问答展示，返回问答记录用于报告。"""
    qa_log: list[dict[str, Any]] = []

    def log(q: str, a: str) -> None:
        print(f"\n  Q: {q}")
        print(f"  A: {a}")
        qa_log.append({"question": q, "answer": a})

    print("\n" + "=" * 70)
    print("XM-100 工艺知识问答（基于知识图谱）")
    print("=" * 70)

    # Q1: 45钢用什么刀具？
    tools = query_tools_for_material(api, "material-45steel")
    if tools:
        tool_names = [t["tool"]["node_id"] for t in tools]
        confs = [f"{t['confidence']:.2f}" for t in tools]
        log(
            "45钢可以用哪些刀具加工？",
            f"知识图谱中有 {len(tools)} 把刀具适配 45钢：{', '.join(tool_names)}（可信度分别为 {', '.join(confs)}）",
        )
    else:
        log("45钢可以用哪些刀具加工？", "暂无数据")

    # Q2: φ10立铣刀能加工什么材料？
    mats = query_materials_for_tool(api, "tool-endmill_wc_d10")
    if mats:
        mat_names = [m["material"]["node_id"] for m in mats]
        confs = [f"{m['confidence']:.2f}" for m in mats]
        log(
            "φ10立铣刀(tool-endmill_wc_d10)能加工哪些材料？",
            f"知识图谱中该刀具适配 {len(mats)} 种材料：{', '.join(mat_names)}（可信度分别为 {', '.join(confs)}）",
        )
    else:
        log("φ10立铣刀能加工什么材料？", "暂无数据")

    # Q3: φ6锥度球头刀适合五轴加工吗？
    taper_tools = api.search_nodes(id_pattern="tool-endmill_wc_taper%", node_type="tool")
    if taper_tools:
        names = [t["node_id"] for t in taper_tools]
        log(
            "XM-100 有哪些锥度球头刀适合五轴加工？",
            f"知识图谱中有 {len(taper_tools)} 把锥度球头刀：{', '.join(names)}，"
            f"均标记 suitable_for_5axis=true，适合 RTCP/TWP 五轴加工",
        )
    else:
        log("XM-100 有哪些锥度球头刀？", "暂无数据")

    # Q4: 图谱规模
    stats = api.stats()
    log(
        "当前知识图谱的规模如何？",
        f"节点 {stats['node_count']} 个，关系 {stats['edge_count']} 条。"
        f"节点类型分布: {stats['node_types']}，"
        f"关系类型分布: {stats['edge_types']}",
    )

    # Q5: 哪些工艺节点已有实测数据？
    process_nodes = api.nodes_by_type("process")
    sampled = [p for p in process_nodes if (p.get("properties") or {}).get("sample_count", 0) > 0]
    if sampled:
        names = [
            f"{p['node_id']}(n={p['properties']['sample_count']}, 合格率={p['properties'].get('success_rate', 0):.0%})"
            for p in sampled
        ]
        log(
            "哪些工艺已有 XM-100 实测数据？",
            f"有 {len(sampled)} 个工艺节点已有实测数据：" + "; ".join(names),
        )
    else:
        log("哪些工艺已有 XM-100 实测数据？", "暂无工艺节点有实测数据")

    return qa_log


def write_reports(
    import_results: list[dict[str, Any]],
    qa_log: list[dict[str, Any]],
    stats: dict[str, Any],
    output_dir: str,
) -> None:
    """写出 JSON 与 Markdown 报告。"""
    os.makedirs(output_dir, exist_ok=True)

    report = {
        "title": "XM-100 知识图谱加工经验导入报告",
        "machine": "XM-100 (Xmaker, Fanuc 0i 兼容)",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "imported_records": import_results,
        "graph_stats": stats,
        "qa_log": qa_log,
    }

    json_path = os.path.join(output_dir, "kg_experience_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON 报告] {json_path}")

    md_path = os.path.join(output_dir, "kg_experience_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# XM-100 知识图谱加工经验导入报告\n\n")
        f.write("- **机床**: XM-100 (Xmaker, Fanuc 0i 兼容)\n")
        f.write(f"- **生成时间**: {report['timestamp']}\n\n")

        f.write("## 1. 导入的加工记录\n\n")
        f.write("| 记录ID | 刀具 | 材料 | 首次合格 | Process更新 | 关系更新 |\n")
        f.write("|--------|------|------|----------|-------------|----------|\n")
        for r in import_results:
            s = r["stats"]
            f.write(
                f"| {r['record_id']} | {r['tool']} | {r['material']} | "
                f"{'是' if r['first_pass'] else '否'} | "
                f"{s['process_nodes_updated']} | "
                f"{s['tool_material_edges_updated']} |\n"
            )

        f.write("\n## 2. 知识图谱规模\n\n")
        f.write(f"- **节点总数**: {stats['node_count']}\n")
        f.write(f"- **关系总数**: {stats['edge_count']}\n")
        f.write(f"- **节点类型分布**: {stats['node_types']}\n")
        f.write(f"- **关系类型分布**: {stats['edge_types']}\n\n")

        f.write("## 3. 工艺知识问答\n\n")
        for qa in qa_log:
            f.write(f"**Q: {qa['question']}**\n\n")
            f.write(f"A: {qa['answer']}\n\n")

        f.write("## 4. 说明\n\n")
        f.write("- 加工记录为基于 XM-100 能力的模拟数据\n")
        f.write("- FeedbackUpdater 根据 first_pass_acceptance 调整关系可信度\n")
        f.write("- 可信度公式: confidence = 0.5 × success_rate + 0.2\n")
        f.write("- Process 节点累计 sample_count、success_count、avg_surface_roughness\n")
    print(f"[Markdown 报告] {md_path}")


def main() -> int:
    print("=" * 60)
    print("XM-100 知识图谱加工经验导入与查询展示")
    print("=" * 60)

    print("\n[1/4] 初始化知识图谱存储...")
    store = GraphStore(auto_load=False)
    updater = FeedbackUpdater(graph_store=store)
    api = KnowledgeGraphQueryAPI(store)
    print("    ✓ GraphStore + FeedbackUpdater + QueryAPI 已就绪")

    print("\n[2/4] 导入 XM-100 加工经验记录...")
    records = build_xm100_records()
    print(f"    共 {len(records)} 条记录待导入")
    import_results = import_records(updater, records)
    for r in import_results:
        status = "✓" if r["first_pass"] else "✗"
        print(
            f"    {status} {r['record_id']}: {r['tool']} → {r['material']} "
            f"(Process更新={r['stats']['process_nodes_updated']}, "
            f"关系更新={r['stats']['tool_material_edges_updated']})"
        )

    print("\n[3/4] 查询知识图谱...")
    stats = api.stats()
    print(f"    图谱规模: {stats['node_count']} 节点, {stats['edge_count']} 关系")
    print(f"    节点类型: {stats['node_types']}")
    print(f"    关系类型: {stats['edge_types']}")

    # 查询示例
    tools_for_steel = query_tools_for_material(api, "material-45steel")
    print(f"\n    45钢适配刀具 ({len(tools_for_steel)} 把):")
    for t in tools_for_steel:
        print(f"      - {t['tool']['node_id']} (可信度={t['confidence']:.3f})")

    mats_for_d10 = query_materials_for_tool(api, "tool-endmill_wc_d10")
    print(f"\n    φ10立铣刀适配材料 ({len(mats_for_d10)} 种):")
    for m in mats_for_d10:
        print(f"      - {m['material']['node_id']} (可信度={m['confidence']:.3f})")

    print("\n[4/4] 工艺知识问答展示...")
    qa_log = print_qa_section(api)

    # 生成报告
    print("\n生成报告...")
    output_dir = os.path.join(_PROJECT_ROOT, "output", "xm100_demo")
    write_reports(import_results, qa_log, stats, output_dir)

    print("\n展示完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
