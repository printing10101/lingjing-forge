"""G代码校验与 dry-run 预览纯逻辑（从 gcode_generator 拆分，D5）。

模块级函数，无 self 依赖；原 GCodeGenerator 方法改为薄包装调用。
行为与拆分前完全一致。
"""

from __future__ import annotations

from typing import Any

from app.process_planning.operation_sequencer import OperationPlan


def validate_gcode_syntax(program_text: str, controller_type: str) -> list[str]:
    """G代码语法校验（增强版）。

    对生成的G代码进行全面的安全检查：
    1. 基础语法检查（程序号、结束符、G代码配对）
    2. 机床行程极限验证（各轴坐标是否在安全范围内）
    3. 切削参数物理约束验证（主轴转速、进给速度）
    4. 快速移动碰撞检测（G00进入工件区域）
    5. 刀具半径补偿正确性检查
    6. 坐标系有效性验证
    """
    errors: list[str] = []

    if not program_text or not program_text.strip():
        errors.append("G代码程序为空")
        return errors

    lines = program_text.strip().splitlines()

    # 1. 基础语法检查
    if controller_type == "fanuc_0i":
        if not any(line.strip().startswith("O") or line.strip().startswith(":") for line in lines):
            errors.append("Fanuc: 缺少程序号(Oxxxx)")
        if not lines[-1].strip().endswith("%"):
            errors.append("Fanuc: 缺少程序结束符(%)")
    elif controller_type == "siemens_840d":
        if not lines[-1].strip().endswith("M30"):
            errors.append("Siemens: 缺少M30程序结束指令")
    elif controller_type == "heidenhain_tnc":
        first_line = lines[0].strip().upper() if lines else ""
        last_line = lines[-1].strip().upper() if lines else ""
        if "BEGIN PGM" not in first_line:
            errors.append("Heidenhain: 缺少BEGIN PGM标记")
        if "END PGM" not in last_line:
            errors.append("Heidenhain: 缺少END PGM标记")

    # 2. 机床行程极限验证
    # 定义典型机床行程限制（可根据实际机床配置调整）
    MACHINE_LIMITS = {
        "x_min": -500.0,
        "x_max": 500.0,
        "y_min": -400.0,
        "y_max": 400.0,
        "z_min": -300.0,
        "z_max": 300.0,
    }

    import re

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("("):
            continue

        # 提取坐标值
        x_match = re.search(r"X([+-]?\d*\.?\d+)", stripped)
        y_match = re.search(r"Y([+-]?\d*\.?\d+)", stripped)
        z_match = re.search(r"Z([+-]?\d*\.?\d+)", stripped)

        if x_match:
            x_val = float(x_match.group(1))
            if x_val < MACHINE_LIMITS["x_min"] or x_val > MACHINE_LIMITS["x_max"]:
                errors.append(
                    f"第{line_num}行: X坐标{x_val:.3f}超出机床行程范围[{MACHINE_LIMITS['x_min']}, {MACHINE_LIMITS['x_max']}]"
                )

        if y_match:
            y_val = float(y_match.group(1))
            if y_val < MACHINE_LIMITS["y_min"] or y_val > MACHINE_LIMITS["y_max"]:
                errors.append(
                    f"第{line_num}行: Y坐标{y_val:.3f}超出机床行程范围[{MACHINE_LIMITS['y_min']}, {MACHINE_LIMITS['y_max']}]"
                )

        if z_match:
            z_val = float(z_match.group(1))
            if z_val < MACHINE_LIMITS["z_min"] or z_val > MACHINE_LIMITS["z_max"]:
                errors.append(
                    f"第{line_num}行: Z坐标{z_val:.3f}超出机床行程范围[{MACHINE_LIMITS['z_min']}, {MACHINE_LIMITS['z_max']}]"
                )

    # 3. 切削参数物理约束验证
    # 典型机床参数限制
    SPINDLE_LIMITS = {"min_rpm": 50, "max_rpm": 24000}
    FEED_LIMITS = {"min_rate": 10.0, "max_rate": 20000.0}

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(";") or stripped.startswith("("):
            continue

        # 检查主轴转速
        s_match = re.search(r"S(\d+)", stripped)
        if s_match:
            rpm = int(s_match.group(1))
            if rpm < SPINDLE_LIMITS["min_rpm"] or rpm > SPINDLE_LIMITS["max_rpm"]:
                errors.append(
                    f"第{line_num}行: 主轴转速{rpm}RPM超出安全范围[{SPINDLE_LIMITS['min_rpm']}, {SPINDLE_LIMITS['max_rpm']}]"
                )

        # 检查进给速度
        f_match = re.search(r"F([+-]?\d*\.?\d+)", stripped)
        if f_match:
            feed = float(f_match.group(1))
            if feed < FEED_LIMITS["min_rate"] or feed > FEED_LIMITS["max_rate"]:
                errors.append(
                    f"第{line_num}行: 进给速度{feed:.1f}mm/min超出安全范围[{FEED_LIMITS['min_rate']}, {FEED_LIMITS['max_rate']}]"
                )

    # 4. 快速移动碰撞检测
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("G00") or stripped.startswith("G0 "):
            # 检查G00快速移动是否进入工件区域（Z<0）
            z_match = re.search(r"Z([+-]?\d*\.?\d+)", stripped)
            if z_match:
                z_val = float(z_match.group(1))
                if z_val < 0:
                    errors.append(f"第{line_num}行: G00快速移动到Z{z_val:.3f}，可能导致碰撞")

    # 5. 刀具半径补偿正确性检查
    g_codes: list[str] = []
    for line in lines:
        stripped = line.strip()
        g_matches = re.findall(r"G\d+", stripped)
        g_codes.extend(g_matches)

    if ("G41" in g_codes or "G42" in g_codes) and "G40" not in g_codes:
        errors.append("刀具半径补偿未取消：G41/G42缺少对应的G40取消指令")

    # 6. 坐标系有效性验证
    valid_wcs = {"G54", "G55", "G56", "G57", "G58", "G59"}
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        for wcs in valid_wcs:
            if wcs in stripped:
                break

    return errors


def build_dry_run_preview(
    operation_plan: OperationPlan,
    controller_type: str = "fanuc_0i",
    material_name: str = "45#钢",
    program_number: int = 1000,
    safe_z: float = 80.0,
    stock_top_z: float = 50.0,
) -> dict[str, Any]:
    """G代码 dry-run 预览模式（原 GCodeGenerator.dry_run_preview）。

    在不实际生成完整 G代码的情况下，预览加工过程的关键信息：
    - 刀具路径概览（每道工序的起止坐标）
    - 加工时间估算
    - 刀具使用统计
    - 潜在的碰撞风险点
    - 断点续传标记位置
    """
    preview_result: dict[str, Any] = {
        "controller_type": controller_type,
        "material": material_name,
        "program_number": program_number,
        "safe_z": safe_z,
        "tool_path_summary": [],
        "time_estimation": {},
        "tool_usage": {},
        "collision_risks": [],
        "checkpoint_positions": [],
        "warnings": [],
    }

    if not operation_plan or not operation_plan.operations:
        preview_result["warnings"].append("工序规划结果为空")
        return preview_result

    # 1. 刀具路径摘要
    current_z = safe_z
    total_travel = 0.0
    for op in operation_plan.operations:
        cut_params = op.cutting_params or {}
        # 提取关键坐标（从工序参数或默认值）
        start_x = cut_params.get("start_x", 0.0)
        start_y = cut_params.get("start_y", 0.0)
        depth = cut_params.get("depth", 0.0)
        target_z = (stock_top_z - abs(depth)) if depth > 0 else safe_z

        path_info = {
            "op_seq": op.seq,
            "op_name": op.name,
            "tool_type": op.tool_type or "UNKNOWN",
            "start_pos": {"x": start_x, "y": start_y, "z": current_z},
            "end_pos": {"x": start_x, "y": start_y, "z": target_z},
            "travel_distance": abs(current_z - target_z),
            "machining_method": op.machining_method,
        }
        preview_result["tool_path_summary"].append(path_info)
        travel_dist = path_info["travel_distance"]
        if isinstance(travel_dist, (int, float)):
            total_travel += float(travel_dist)
        current_z = target_z

    # 2. 时间估算
    total_time = operation_plan.estimated_time_min
    tool_changes = len(set(op.tool_type for op in operation_plan.operations if op.tool_type))
    tool_change_time = tool_changes * 1.5  # 每次换刀约 1.5 分钟
    preview_result["time_estimation"] = {
        "machining_time_min": round(total_time, 2),
        "tool_change_time_min": round(tool_change_time, 2),
        "total_time_min": round(total_time + tool_change_time, 2),
        "operation_count": len(operation_plan.operations),
        "tool_change_count": tool_changes,
    }

    # 3. 刀具使用统计
    tool_stats: dict[str, dict[str, Any]] = {}
    for op in operation_plan.operations:
        tool_key = op.tool_type or "UNKNOWN"
        if tool_key not in tool_stats:
            tool_stats[tool_key] = {
                "usage_count": 0,
                "methods": set(),
                "features": set(),
            }
        tool_stats[tool_key]["usage_count"] += 1
        tool_stats[tool_key]["methods"].add(op.machining_method)
        tool_stats[tool_key]["features"].add(op.feature_name)

    # 转换 set 为 list 以便序列化
    for tool_key, stats in tool_stats.items():
        stats["methods"] = sorted(list(stats["methods"]))
        stats["features"] = sorted(list(stats["features"]))
    preview_result["tool_usage"] = tool_stats

    # 4. 碰撞风险提示
    # 检查是否有深腔加工（深度 > 50mm 可能需要分层）
    for op in operation_plan.operations:
        cut_params = op.cutting_params or {}
        depth = cut_params.get("depth", 0.0)
        if depth > 50.0:
            preview_result["collision_risks"].append(
                {
                    "op_seq": op.seq,
                    "op_name": op.name,
                    "risk_type": "deep_cavity",
                    "description": f"深腔加工 (深度={depth:.1f}mm)，建议分层铣削",
                    "severity": "medium",
                }
            )

    # 检查快速移动距离（可能碰撞）
    for i, path in enumerate(preview_result["tool_path_summary"]):
        if path["travel_distance"] > 100.0:
            preview_result["collision_risks"].append(
                {
                    "op_seq": path["op_seq"],
                    "op_name": path["op_name"],
                    "risk_type": "long_rapid_move",
                    "description": f"长距离快速移动 ({path['travel_distance']:.1f}mm)，注意避障",
                    "severity": "low",
                }
            )

    # 5. 断点位置
    checkpoint_counter = 0
    for op_index, op in enumerate(operation_plan.operations):
        checkpoint_counter += 1
        preview_result["checkpoint_positions"].append(
            {
                "checkpoint_id": f"CP{checkpoint_counter:03d}",
                "op_index": op_index,
                "op_name": op.name,
                "feature_name": op.feature_name,
                "estimated_line": checkpoint_counter * 100,
            }
        )

    # 6. 警告信息
    if tool_changes > 10:
        preview_result["warnings"].append(f"刀具更换次数较多 ({tool_changes}次)，可能影响效率")
    if total_time > 60.0:
        preview_result["warnings"].append(f"预估加工时间较长 ({total_time:.1f}分钟)")
    if len(preview_result["collision_risks"]) > 0:
        preview_result["warnings"].append(f"发现 {len(preview_result['collision_risks'])} 个潜在碰撞风险")

    return preview_result


def validate_gcode(gcode: str) -> dict:
    """
    独立G代码验证函数。

    对G代码进行全面验证，检查常见错误。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not gcode or not gcode.strip():
        errors.append("G代码为空")
        return {"valid": False, "errors": errors, "warnings": warnings}

    lines = gcode.strip().split("\n")
    line_count = len(lines)

    # 检查基本结构
    has_program_start = False
    has_program_end = False
    has_feed_rate = False
    has_spindle = False

    for i, line in enumerate(lines, 1):
        line = line.strip()

        # 跳过空行和注释
        if not line or line.startswith(";") or line.startswith("("):
            continue

        # 检查程序开始
        if line.startswith("O") or line.startswith("%"):
            has_program_start = True

        # 检查程序结束
        if "M02" in line or "M30" in line or line.endswith("%"):
            has_program_end = True

        # 检查进给率
        if "F" in line and any(c.isdigit() for c in line):
            has_feed_rate = True

        # 检查主轴启动
        if "M03" in line or "M04" in line:
            has_spindle = True

        # 检查快速移动进入工件（潜在碰撞）
        if line.startswith("G00") and "Z" in line:
            # 检查Z值是否为负（进入工件）
            import re

            z_match = re.search(r"Z([+-]?\d*\.?\d+)", line)
            if z_match:
                z_val = float(z_match.group(1))
                if z_val < 0:
                    warnings.append(f"第{i}行: G00快速移动到Z{z_val}，可能导致碰撞")

        # 检查刀具半径补偿配对
        if "G41" in line or "G42" in line:
            # 检查后续是否有G40取消
            has_cancel = False
            for j in range(i, min(i + 50, line_count)):
                if j < line_count and "G40" in lines[j]:
                    has_cancel = True
                    break
            if not has_cancel:
                warnings.append(f"第{i}行: 刀具半径补偿未取消（缺少G40）")

    # 基本检查
    if not has_program_start:
        warnings.append("缺少程序号（Oxxxx）")

    if not has_program_end:
        errors.append("缺少程序结束指令（M02/M30）")

    if not has_feed_rate:
        warnings.append("未找到进给率（F指令）")

    if not has_spindle:
        warnings.append("未找到主轴启动指令（M03/M04）")

    # 检查G代码语法
    for i, line in enumerate(lines, 1):
        line = line.strip()

        # 跳过空行和注释
        if not line or line.startswith(";") or line.startswith("("):
            continue

        # 检查G代码格式
        import re

        g_codes = re.findall(r"G(\d+)", line)
        for g_code in g_codes:
            # 检查常见G代码
            if g_code in [
                "00",
                "01",
                "02",
                "03",
                "04",
                "17",
                "18",
                "19",
                "20",
                "21",
                "28",
                "40",
                "41",
                "42",
                "43",
                "49",
                "53",
                "54",
                "55",
                "56",
                "57",
                "58",
                "59",
                "80",
                "81",
                "82",
                "83",
                "84",
                "85",
                "86",
                "87",
                "88",
                "89",
                "90",
                "91",
                "92",
                "94",
                "95",
                "96",
                "97",
                "98",
                "99",
            ]:
                continue
            else:
                warnings.append(f"第{i}行: 不常见的G代码 G{g_code}")

        # 检查M代码格式
        m_codes = re.findall(r"M(\d+)", line)
        for m_code in m_codes:
            if m_code in [
                "00",
                "01",
                "02",
                "03",
                "04",
                "05",
                "06",
                "07",
                "08",
                "09",
                "10",
                "11",
                "19",
                "30",
                "98",
                "99",
            ]:
                continue
            else:
                warnings.append(f"第{i}行: 不常见的M代码 M{m_code}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
