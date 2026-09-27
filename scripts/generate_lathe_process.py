#!/usr/bin/env python3
"""后端盖轴承钢套车床工艺生成与工时测算脚本。

调用灵境制造项目中的 OperationSequencer 与 GCodeGenerator，
根据图纸参数生成车削工艺路线、G 代码，并基于真实切削参数
计算单件加工时间、换件/夹准辅助时间及日产量。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 保证能 import 到 engineering/python/app 下的模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_ROOT = PROJECT_ROOT / "engineering" / "python"
os.environ.setdefault("PYTHONPATH", str(PYTHON_ROOT))
import sys

sys.path.insert(0, str(PYTHON_ROOT))

from app.cutting_params_db import get_cutting_params  # noqa: E402
from app.process_planning.feature_dependency import MachiningFeature  # noqa: E402
from app.process_planning.gcode_generator import GCodeGenerator  # noqa: E402
from app.process_planning.operation_sequencer import (  # noqa: E402
    OperationPlan,
    OperationSequencer,
)


# 图纸参数（可辨识 + 工程合理假设）
PART_INFO = {
    "name": "后端盖轴承钢套",
    "drawing_no": "M0033",
    "material": "45#钢",
    "hardness": "调质 20-24HRC",
    "quantity": 70000,
    "blank": {
        "diameter": 78.0,  # 棒料毛坯直径，比零件外径大 2mm（单边 1mm 余量）
        "length_per_piece": 81.0,  # 单件下料长度，含 1mm 端面余量（满足料头≤60mm）
        "bar_length": 6000.0,  # 标准棒料长度 6m
        "bar_end_loss": 50.0,  # 每根棒料两端料头损耗
        "steel_density_kg_cm3": 0.00785,  # 45# 钢密度 g/mm³ → kg/cm³
        "material_price_yuan_kg": 3.5,  # 批量采购参考价，需按实际询价调整
    },
    "dimensions": {
        "outer_diameter": 76.0,
        "total_length": 80.0,
        "bores": [
            {"diameter": 46.0, "length": 26.0, "tolerance": "H8", "ra": 3.2},
            {"diameter": 26.0, "length": 54.0, "tolerance": "H8", "ra": 3.2},
        ],
        "chamfer": 0.5,
    },
    "tolerance_general": "GB/T 1804-M",
    "tolerance_special": "GB1004-B（建议按 GB/T 1804-m 级执行）",
    "notes": [
        "图纸中部分小尺寸标注（左侧 20.6 / 6.6 等）分辨率不足，已按套筒类零件常规结构假设为两段内孔：Ø46×26 与 Ø26×54。",
        "如实际尺寸不同，可修改本脚本 PART_INFO 后重新生成。",
    ],
}


# 时间测算常数（基于数控车床人工上下料 + 三爪卡盘装夹）
TIME_CONSTANTS = {
    "clamp_align_min": 2.0,  # 夹准时间：装夹 + 找正 + 关防护门 / 件
    "part_swap_min": 1.0,  # 换件时间：松开卡盘、取件、放新毛坯 / 件
    "tool_change_min": 0.5,  # 换刀/换刀补时间 / 次
    "shift_hours": 8.0,  # 每班工时
    "utilization": 0.85,  # 设备综合利用率（含对刀、抽检、短暂停顿）
    "setup_batch_min": 15.0,  # 每批首件对刀、程序调用、检具准备
    "batch_size_for_setup": 1000,  # 多少件分摊一次批量准备
}


@dataclass
class LatheOperationTime:
    """单道工序的详细时间拆解。"""

    name: str
    method: str
    diameter_mm: float
    length_mm: float
    spindle_rpm: int
    feed_mm_rev: float
    depth_of_cut_mm: float
    radial_stock_mm: float
    passes: int
    cutting_time_min: float
    tool_change_time_min: float = 0.0
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def build_features(part_info: dict[str, Any]) -> list[MachiningFeature]:
    """根据零件参数构建 MachiningFeature 列表（粗/精分离）。"""
    od = part_info["dimensions"]["outer_diameter"]
    length = part_info["dimensions"]["total_length"]
    chamfer = part_info["dimensions"]["chamfer"]

    features: list[MachiningFeature] = [
        # 端面
        MachiningFeature(
            name="端面粗车",
            type="end_face",
            geometric_type="plane",
            tolerance_grade="IT10",
            surface_roughness_ra=6.3,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": od, "length": length},
        ),
        MachiningFeature(
            name="端面精车",
            type="end_face",
            geometric_type="plane",
            tolerance_grade="IT7",
            surface_roughness_ra=3.2,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": od, "length": length},
        ),
        # 外圆
        MachiningFeature(
            name="外圆粗车",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT10",
            surface_roughness_ra=6.3,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": od, "length": length},
        ),
        MachiningFeature(
            name="外圆精车",
            type="outer_cylinder",
            geometric_type="cylinder",
            tolerance_grade="IT7",
            surface_roughness_ra=1.6,
            is_datum_candidate=True,
            machining_method="turning",
            priority="high",
            surface="A",
            dimensions={"diameter": od, "length": length},
        ),
    ]

    # 内孔：每个孔分粗/精两道
    for idx, bore in enumerate(part_info["dimensions"]["bores"], start=1):
        features.extend(
            [
                MachiningFeature(
                    name=f"内孔{idx}-粗镗-Ø{int(bore['diameter'])}",
                    type="inner_bore",
                    geometric_type="cylinder",
                    tolerance_grade="IT10",
                    surface_roughness_ra=6.3,
                    is_datum_candidate=False,
                    machining_method="boring",
                    priority="medium",
                    surface="A",
                    dimensions={
                        "diameter": bore["diameter"],
                        "depth": bore["length"],
                    },
                    tolerances={"diameter": bore["tolerance"]},
                ),
                MachiningFeature(
                    name=f"内孔{idx}-精镗-Ø{int(bore['diameter'])}",
                    type="inner_bore",
                    geometric_type="cylinder",
                    tolerance_grade="IT7",
                    surface_roughness_ra=bore["ra"],
                    is_datum_candidate=False,
                    machining_method="boring",
                    priority="medium",
                    surface="A",
                    dimensions={
                        "diameter": bore["diameter"],
                        "depth": bore["length"],
                    },
                    tolerances={"diameter": bore["tolerance"]},
                ),
            ]
        )

    # 倒角
    features.append(
        MachiningFeature(
            name="倒角C0.5",
            type="chamfer",
            geometric_type="chamfer",
            tolerance_grade="IT10",
            surface_roughness_ra=12.5,
            is_datum_candidate=False,
            machining_method="turning",
            priority="low",
            surface="A",
            dimensions={"width": chamfer},
        )
    )

    return features


def estimate_turning_time(
    name: str,
    method: str,
    diameter: float,
    length: float,
    blank_diameter: float,
    is_rough: bool,
    is_boring: bool = False,
    pre_hole_diameter: float = 20.0,
) -> LatheOperationTime:
    """基于真实切削学估算车削/镗孔工序时间。

    参数依据：45#钢调质 20-24HRC，硬质合金车刀/镗刀。
    """
    # 45#钢调质车削基础参数
    if is_rough:
        vc = 110.0  # m/min
        feed = 0.20  # mm/rev
        depth = 2.0  # mm（单边切深）
    else:
        vc = 130.0  # m/min
        feed = 0.08  # mm/rev
        depth = 0.3  # mm（单边精车切深）

    # 转速 n = 1000 * Vc / (π * D)
    rpm = int(1000.0 * vc / (math.pi * max(diameter, 1.0)))

    # 径向加工余量
    if is_boring:
        # 内孔：从预钻孔径镗至目标孔径
        radial_stock = max((diameter - pre_hole_diameter) / 2.0, 0.0)
    elif "端面" in name:
        # 端面：轴向余量约 2-3 mm
        radial_stock = 2.5
    else:
        # 外圆：从毛坯直径车到目标直径
        radial_stock = max((blank_diameter - diameter) / 2.0, 0.0)

    # 粗车切除全部余量；精车只切除固定余量（0.3 mm 单边）
    if is_rough:
        passes = max(1, math.ceil(radial_stock / depth))
    else:
        passes = 1
        radial_stock = depth  # 精车只切一刀，显示为切深值

    # 端面车削按半径路径估算（外圆/端面）
    if "端面" in name:
        effective_length = blank_diameter / 2.0
    else:
        effective_length = length

    # 每转进给 进给速度 mm/min
    feed_mm_min = feed * rpm
    if feed_mm_min <= 0:
        feed_mm_min = 50.0

    cutting_time = (effective_length * passes) / feed_mm_min

    return LatheOperationTime(
        name=name,
        method=method,
        diameter_mm=diameter,
        length_mm=effective_length,
        spindle_rpm=rpm,
        feed_mm_rev=round(feed, 3),
        depth_of_cut_mm=round(depth, 2),
        radial_stock_mm=round(radial_stock, 2),
        passes=passes,
        cutting_time_min=round(cutting_time, 2),
        notes="粗车分多刀" if is_rough else "精车一刀成形",
    )


def calculate_process_times(
    operation_plan: OperationPlan,
    blank_diameter: float,
) -> list[LatheOperationTime]:
    """遍历工序计划，为每道工序计算真实切削时间，并估算换刀时间。"""
    times: list[LatheOperationTime] = []
    prev_tool = ""
    for op in operation_plan.operations:
        name = op.name
        method = op.machining_method
        is_rough = "粗" in method

        # 从 operation 名称解析直径与长度
        diameter = PART_INFO["dimensions"]["outer_diameter"]
        length = PART_INFO["dimensions"]["total_length"]
        if "外圆" in name or "端面" in name:
            diameter = PART_INFO["dimensions"]["outer_diameter"]
            length = PART_INFO["dimensions"]["total_length"]
        elif "内孔" in name:
            for bore in PART_INFO["dimensions"]["bores"]:
                if f"Ø{int(bore['diameter'])}" in name:
                    diameter = bore["diameter"]
                    length = bore["length"]
                    break
        elif "倒角" in name:
            diameter = PART_INFO["dimensions"]["outer_diameter"]
            length = PART_INFO["dimensions"]["chamfer"]

        is_boring = "镗" in method or "内孔" in name
        lot = estimate_turning_time(
            name=name,
            method=method,
            diameter=diameter,
            length=length,
            blank_diameter=blank_diameter,
            is_rough=is_rough,
            is_boring=is_boring,
            pre_hole_diameter=20.0,
        )

        # 按刀具切换计算换刀时间；同一刀具连续工序不换刀
        current_tool = op.tool_type
        if current_tool and current_tool != prev_tool:
            lot.tool_change_time_min = TIME_CONSTANTS["tool_change_min"]
        else:
            lot.tool_change_time_min = 0.0
        prev_tool = current_tool

        times.append(lot)
    return times


def generate_report(
    operation_plan: OperationPlan,
    gcode_text: str,
    times: list[LatheOperationTime],
    output_dir: Path,
) -> Path:
    """生成 Markdown 工艺报告。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "lathe_process_report.md"

    total_cutting = sum(t.cutting_time_min for t in times)
    total_tool_change = sum(t.tool_change_time_min for t in times)
    operation_count = len(times)
    unique_tools = len({t.method for t in times})

    # 单件时间
    clamp_align = TIME_CONSTANTS["clamp_align_min"]
    part_swap = TIME_CONSTANTS["part_swap_min"]
    per_piece_time = total_cutting + total_tool_change + clamp_align + part_swap

    # 批量准备时间分摊
    setup_amortized = TIME_CONSTANTS["setup_batch_min"] / TIME_CONSTANTS["batch_size_for_setup"]
    per_piece_time += setup_amortized

    # 日产量
    shift_min = TIME_CONSTANTS["shift_hours"] * 60
    available_min = shift_min * TIME_CONSTANTS["utilization"]
    daily_output = int(available_min / per_piece_time)

    # 70000 件总需求
    total_days = math.ceil(PART_INFO["quantity"] / daily_output)
    total_hours = (PART_INFO["quantity"] * per_piece_time) / 60.0

    # 毛坯/棒料利用率
    blank = PART_INFO["blank"]
    # 先按理想长度计算最大可切件数，再计入锯口损耗
    pieces_per_bar = math.floor(blank["bar_length"] / blank["length_per_piece"])
    ideal_waste_mm = blank["bar_length"] - pieces_per_bar * blank["length_per_piece"]
    bar_end_waste_mm = ideal_waste_mm + blank["bar_end_loss"]

    lines = []
    lines.append("# 后端盖轴承钢套 — 车床加工工艺报告")
    lines.append("")
    lines.append("## 1. 零件信息")
    lines.append("")
    lines.append(f"- **零件名称**：{PART_INFO['name']}")
    lines.append(f"- **图号**：{PART_INFO['drawing_no']}")
    lines.append(f"- **材料**：{PART_INFO['material']}（{PART_INFO['hardness']}）")
    lines.append(f"- **订单数量**：{PART_INFO['quantity']:,} 件")
    lines.append(f"- **包工包料单价**：9.0 元/件")
    lines.append(f"- **未注公差**：{PART_INFO['tolerance_general']}")
    lines.append(f"- **特殊公差要求**：{PART_INFO['tolerance_special']}")
    lines.append("")
    lines.append("### 1.1 关键尺寸（图纸可辨识 + 工程假设）")
    lines.append("")
    lines.append(f"- 外圆：Ø{PART_INFO['dimensions']['outer_diameter']} × {PART_INFO['dimensions']['total_length']} mm")
    for bore in PART_INFO["dimensions"]["bores"]:
        lines.append(f"- 内孔：Ø{bore['diameter']}{bore['tolerance']} × {bore['length']} mm，Ra{bore['ra']}")
    lines.append(f"- 倒角：C{PART_INFO['dimensions']['chamfer']}")
    lines.append("")
    lines.append(
        "> **图纸辨识说明**：图纸左侧部分小尺寸（20.6 / 6.6 等）分辨率不足，"
        "已按套筒常规结构假设为 Ø46×26 + Ø26×54 两段内孔。若实际尺寸不同，"
        "可修改 `scripts/generate_lathe_process.py` 中 `PART_INFO` 重新生成。"
    )
    lines.append("")

    lines.append("## 2. 工艺路线（由灵境制造自动生成）")
    lines.append("")
    lines.append("| 序号 | 工序名称 | 加工方法 | 刀具 | 表面 | 公差 | 预估工时(min) |")
    lines.append("|------|----------|----------|------|------|------|---------------|")
    for op in operation_plan.operations:
        lines.append(
            f"| {op.seq:02d} | {op.name} | {op.machining_method} | {op.tool_type} | "
            f"{op.surface} | {op.tolerance_grade} | {op.estimated_time_min} |"
        )
    lines.append("")

    lines.append("### 2.1 最快加工工艺流程说明")
    lines.append("")
    lines.append("针对 70,000 件大批量，推荐一次装夹完成全部车削要素，减少掉头：")
    lines.append("")
    lines.append(f"1. **下料**：Ø78 圆钢按 {blank['length_per_piece']:.0f} mm 长度锯切（或棒料送料机直接送料）。")
    lines.append("2. **调质**：外协热处理 20-24HRC（不计入机加工时间）。")
    lines.append("3. **装夹**：三爪卡盘夹持毛坯，伸出 80 mm；若用棒料送料机则自动夹紧。")
    lines.append("4. **T01 外圆粗车刀**：车端面见平 → 粗车外圆至 Ø76.5（留 0.5 mm 精车余量）。")
    lines.append("5. **T02 外圆精车刀**：精车端面保证总长 80 → 精车外圆至 Ø76。")
    lines.append("6. **T03 镗刀**：钻/镗中心底孔 Ø20 → 粗镗 Ø46 台阶 → 精镗 Ø46 H8；粗镗 Ø26 台阶 → 精镗 Ø26 H8。")
    lines.append("7. **T04 倒角刀**：所有锐边倒钝 C0.5。")
    lines.append("8. **下料/换件**：切断或松开卡盘换件，循环下一工件。")
    lines.append("9. **后处理**：去毛刺、清洗、终检。")
    lines.append("")

    lines.append("## 3. 真实切削时间测算")
    lines.append("")
    lines.append(
        "| 工序 | 直径(mm) | 行程(mm) | 转速(rpm) | 进给(mm/rev) | 切深(mm) | 余量(mm) | 走刀次数 | 切削时间(min) |"
    )
    lines.append(
        "|------|----------|----------|-----------|--------------|----------|----------|----------|---------------|"
    )
    for t in times:
        lines.append(
            f"| {t.name} | {t.diameter_mm:.1f} | {t.length_mm:.1f} | {t.spindle_rpm} | "
            f"{t.feed_mm_rev:.3f} | {t.depth_of_cut_mm:.2f} | {t.radial_stock_mm:.2f} | {t.passes} | {t.cutting_time_min:.2f} |"
        )
    lines.append("")
    lines.append(f"- **切削时间合计**：{total_cutting:.2f} min")
    lines.append(f"- **换刀/刀补时间合计**：{total_tool_change:.2f} min")
    lines.append("")

    lines.append("## 4. 辅助时间与单件总时间")
    lines.append("")
    lines.append(f"- **夹准时间**（装夹 + 找正 + 关防护门）：{clamp_align:.1f} min/件")
    lines.append(f"- **换件时间**（松卡盘、取件、放新毛坯）：{part_swap:.1f} min/件")
    lines.append(f"- **批量准备分摊**（对刀、程序调用、首检）：{setup_amortized:.3f} min/件")
    lines.append(f"- **单件总时间**：{per_piece_time:.2f} min")
    lines.append("")

    lines.append("## 5. 日产量与总工期")
    lines.append("")
    lines.append(f"- **班制**：{TIME_CONSTANTS['shift_hours']:.0f} h/班")
    lines.append(f"- **设备利用率**：{TIME_CONSTANTS['utilization'] * 100:.0f}%")
    lines.append(f"- **每班可用加工时间**：{available_min:.0f} min")
    lines.append(f"- **日产量（单班，人工上下料）**：约 **{daily_output} 件/天**")
    lines.append(f"- **70,000 件总机加工时间**：约 {total_hours:.0f} h（{total_days:.0f} 个工作日，单班）")
    lines.append("")

    # 批量生产优化方案
    base_cutting = total_cutting + total_tool_change
    optimized_scenarios = [
        ("自动棒料送料机 + 三爪卡盘", 0.3, 0.85, "换件时间降至 0.3 min"),
        ("双主轴数控车床", 0.2, 0.90, "正反倒角一次装夹完成，省掉头"),
        ("双机并行（两台数控车）", 1.0, 0.85, "日产能翻倍", 2),
    ]
    lines.append("### 5.1 批量生产优化方案")
    lines.append("")
    lines.append("| 场景 | 换件时间(min) | 利用率 | 日产量(件/天) | 70,000件工期(天) | 说明 |")
    lines.append("|------|---------------|--------|---------------|------------------|------|")
    for scenario, swap_time, util, desc, *extra in optimized_scenarios:
        per_piece = base_cutting + clamp_align + swap_time + setup_amortized
        daily = int((shift_min * util) / per_piece)
        machines = extra[0] if extra else 1
        daily *= machines
        days = math.ceil(PART_INFO["quantity"] / daily)
        lines.append(f"| {scenario} | {swap_time:.1f} | {util * 100:.0f}% | {daily} | {days} | {desc} |")
    lines.append("")

    lines.append("## 6. 毛坯与下料建议")
    lines.append("")
    lines.append(f"- **毛坯规格**：热轧 45# 圆钢 Ø{blank['diameter']:.0f} × {blank['length_per_piece']:.0f} mm")
    lines.append(f"- **棒料长度**：{blank['bar_length']:.0f} mm（标准 6m）")
    lines.append(f"- **每棒可切件数**：约 {pieces_per_bar} 件")
    lines.append(
        f"- **每棒料头损耗**：约 {bar_end_waste_mm:.0f} mm "
        f"（含 {blank['bar_end_loss']:.0f} mm 两端锯口；净余料约 {ideal_waste_mm:.0f} mm，控制在 ≤60 mm）"
    )
    lines.append(
        "> 按舅舅要求：角料（料头）直径与毛坯一致 **Ø{:.0f} mm**；通过 Ø78×81 mm 下料，"
        "6m 棒料可切 74 件，净余料仅 {:.0f} mm，满足料头控制要求。".format(blank["diameter"], ideal_waste_mm)
    )
    lines.append("")

    # 成本测算
    blank_volume_cm3 = math.pi * (blank["diameter"] / 20.0) ** 2 * (blank["length_per_piece"] / 10.0)
    blank_weight_kg = blank_volume_cm3 * blank["steel_density_kg_cm3"]
    material_cost = blank_weight_kg * blank["material_price_yuan_kg"]
    total_contract = PART_INFO["quantity"] * 9.0
    total_material = PART_INFO["quantity"] * material_cost
    labor_gross = total_contract - total_material

    lines.append("## 7. 成本测算（敏感性分析）")
    lines.append("")
    lines.append(f"- **单件毛坯重量**：约 {blank_weight_kg:.2f} kg")
    lines.append(f"- **材料单价（参考）**：{blank['material_price_yuan_kg']:.1f} 元/kg")
    lines.append(f"- **单件材料成本**：约 {material_cost:.2f} 元/件")
    lines.append(f"- **包工包料单价**：9.0 元/件")
    lines.append(f"- **单件毛利（未计刀具/电费/人工/场地）**：约 {9.0 - material_cost:.2f} 元/件")
    lines.append(f"- **70,000 件合同总额**：{total_contract:,.0f} 元")
    lines.append(f"- **70,000 件材料总成本（参考）**：约 {total_material:,.0f} 元")
    lines.append(f"- **剩余空间（人工/刀具/能耗/利润）**：约 {labor_gross:,.0f} 元")
    break_even_price = 9.0 / blank_weight_kg if blank_weight_kg > 0 else 0.0
    lines.append(f"- **材料盈亏平衡价**：≤ {break_even_price:.2f} 元/kg（仅覆盖毛坯材料，无加工利润）")
    lines.append("")
    lines.append(
        "> **重要提示**：9.0 元/件包工包料价格偏低，单件毛利空间仅约 {:.2f} 元。"
        "实际能否盈利强烈取决于 45# 钢批量采购价、刀具寿命、设备利用率。"
        "建议先小批量试制验证真实刀耗和工时；若材料采购价无法压至 {:.2f} 元/kg 以下，"
        "需重新评估报价。".format(9.0 - material_cost, break_even_price)
    )
    lines.append("")

    lines.append("## 8. 刀具与转速建议")
    lines.append("")
    # 汇总每把刀具/工序的转速
    seen = set()
    for t in times:
        key = (t.name, t.spindle_rpm, t.feed_mm_rev)
        if key not in seen:
            seen.add(key)
            lines.append(f"- {t.name}：S{t.spindle_rpm} rpm，F{t.feed_mm_rev:.3f} mm/rev")
    lines.append("")

    lines.append("## 9. 生成的数控程序（节选）")
    lines.append("")
    lines.append("```gcode")
    # 只展示前 60 行，避免报告过长
    lines.extend(gcode_text.splitlines()[:60])
    lines.append("```")
    lines.append("")

    lines.append("## 10. 风险提示与假设说明")
    lines.append("")
    lines.append(
        "1. **图纸尺寸不确定性**：图纸左侧部分小尺寸（20.6 / 6.6 等）分辨率不足，内孔结构按 Ø46×26 + Ø26×54 假设。实际投产前请用高清图纸或 STEP 模型复核。"
    )
    lines.append(
        "2. **预钻孔假设**：内孔镗削按已预钻 Ø20 mm 底孔计算。若毛坯中心未预钻孔，需增加中心钻 + 钻孔工序（约 +0.5~1.5 min/件）。"
    )
    lines.append("3. **调质处理**：20-24HRC 建议外协，不计入机加工时间；调质后硬度会显著影响刀具寿命和切削参数。")
    lines.append(
        "4. **G 代码说明**：本报告 G 代码由项目通用后处理器生成，数控车床实际使用前需按具体机床（Fanuc/Siemens/广数等）和车削循环（G71/G72/G70 等）调整。"
    )
    lines.append(
        f"5. **产能瓶颈**：单台单班约 {daily_output} 件/天，70000 件约需 {total_days:.0f} 个工作日。大批量交付必须采用自动送料、双主轴或多机并行。"
    )
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    print("=" * 60)
    print(f"正在生成：{PART_INFO['name']} 车床工艺")
    print("=" * 60)

    # 1. 构建特征
    features = build_features(PART_INFO)
    print(f"已构建 {len(features)} 个加工特征（含粗/精分离）")

    # 2. 工序规划
    sequencer = OperationSequencer()
    operation_plan = sequencer.plan_operations(
        features=features,
        material=PART_INFO["material"],
        part_type="shaft",
    )
    print(f"已规划 {len(operation_plan.operations)} 道工序，{len(operation_plan.setups)} 次装夹")

    # 3. G 代码生成
    generator = GCodeGenerator()
    gcode_result = generator.generate(
        operation_plan=operation_plan,
        controller_type="fanuc_0i",
        material_name=PART_INFO["material"],
        program_number=1001,
        safe_z=80.0,
        stock_top_z=50.0,
    )
    print(
        f"G 代码生成完成：{gcode_result.total_lines} 行，"
        f"{gcode_result.tool_count} 把刀具，"
        f"预估周期 {gcode_result.estimated_cycle_time_min:.2f} min（系统启发值）"
    )

    # 4. 真实时间测算
    times = calculate_process_times(
        operation_plan,
        blank_diameter=PART_INFO["blank"]["diameter"],
    )

    # 5. 输出报告
    output_dir = PROJECT_ROOT / "output"
    report_path = generate_report(operation_plan, gcode_result.program_text, times, output_dir)
    print(f"工艺报告已保存：{report_path}")

    # 6. 同时保存 JSON 结构化数据，便于前端/其他模块消费
    json_path = output_dir / "lathe_process_data.json"
    data = {
        "part_info": PART_INFO,
        "time_constants": TIME_CONSTANTS,
        "operation_plan": operation_plan.to_dict(),
        "detailed_times": [t.__dict__ for t in times],
        "gcode_summary": {
            "controller_type": gcode_result.controller_type,
            "program_number": gcode_result.program_number,
            "total_lines": gcode_result.total_lines,
            "tool_count": gcode_result.tool_count,
            "estimated_cycle_time_min": gcode_result.estimated_cycle_time_min,
        },
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结构化数据已保存：{json_path}")


if __name__ == "__main__":
    main()
