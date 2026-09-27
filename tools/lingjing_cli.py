import argparse
import json
import os
import sys
import traceback
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_PYTHON_DIR = _PROJECT_ROOT / "python"

sys.path.insert(0, str(_PYTHON_DIR))

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def _load_version():
    version_file = _PROJECT_ROOT / "VERSION"
    if version_file.exists():
        return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0"


VERSION = _load_version()

MANUFACTURING_ERRORS = {}
DATA_ERRORS = {}

try:
    from app.core.error_taxonomy import ManufacturingError
    MANUFACTURING_ERRORS["ManufacturingError"] = ManufacturingError
except ImportError as e:
    print(f"警告: 无法导入 ManufacturingError: {e}", file=sys.stderr)

try:
    from app.data.process_data_manager import DataLoadError, QueryError
    DATA_ERRORS["DataLoadError"] = DataLoadError
    DATA_ERRORS["QueryError"] = QueryError
except ImportError as e:
    print(f"警告: 无法导入数据错误类型: {e}", file=sys.stderr)

try:
    from app.dxf.exceptions import DxfPipelineError
    MANUFACTURING_ERRORS["DxfPipelineError"] = DxfPipelineError
except ImportError as e:
    print(f"警告: 无法导入 DxfPipelineError: {e}", file=sys.stderr)


class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _init_colors():
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            global Colors

            class _NoColors:
                GREEN = YELLOW = RED = CYAN = BOLD = DIM = RESET = ""

            Colors = _NoColors()  # type: ignore


_init_colors()


def _print_step(step_num, total, message):
    print(f"  {Colors.CYAN}[{step_num}/{total}]{Colors.RESET} {message}...")


def _print_ok(message):
    print(f"  {Colors.GREEN}OK{Colors.RESET} {message}")


def _print_error(message):
    print(f"{Colors.RED}Error:{Colors.RESET} {message}")


def _print_warning(message):
    print(f"{Colors.YELLOW}Warning:{Colors.RESET} {message}")


def _print_separator(title=None):
    if title:
        print(f"\n{Colors.BOLD}{'=' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}  {title}{Colors.RESET}")
        print(f"{Colors.BOLD}{'=' * 60}{Colors.RESET}\n")
    else:
        print(f"{Colors.DIM}{'-' * 60}{Colors.RESET}")


MATERIAL_TYPE_MAP = {
    "steel": ["carbon_steel", "alloy_steel"],
    "aluminum": ["aluminum"],
    "stainless": ["stainless_steel"],
    "titanium": ["titanium"],
    "cast_iron": ["cast_iron"],
}

TOOL_TYPE_MAP = {
    "end_mill": "endmill",
    "ball_mill": "ball_mill",
    "drill": "twist_drill",
    "face_mill": "face_mill",
    "chamfer_mill": "chamfer_mill",
}

SERIES_TO_DISPLAY_TYPE = {
    "twist_drill": "drill",
    "endmill": "end_mill",
    "face_mill": "face_mill",
    "center_drill": "drill",
    "ball_mill": "ball_mill",
}


def cmd_dxf2nc(args):
    try:
        from app.dxf.pipeline import DxfProcessPipeline
    except ImportError as e:
        _print_error(f"无法导入DXF流水线模块: {e}")
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        _print_error(f"输入文件不存在: {input_path}")
        sys.exit(1)
    if input_path.suffix.lower() != ".dxf":
        _print_warning(f"文件扩展名不是 .dxf，将尝试解析: {input_path}")

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix(".nc")

    material = args.material
    controller = args.controller
    safe_z = args.safe_z
    program_number = args.program_number

    _print_separator(f"DXF → NC 转换: {input_path.name}")
    print(f"  材料: {material}")
    print(f"  数控系统: {controller}")
    print(f"  安全高度: {safe_z}mm")
    print(f"  程序号: O{program_number}")
    if args.feedrate:
        print(f"  进给率: {args.feedrate} mm/min (手动指定)")
    if args.spindle:
        print(f"  主轴转速: {args.spindle} RPM (手动指定)")
    if args.cut_depth:
        print(f"  切深: {args.cut_depth} mm (手动指定)")
    print()

    total_steps = 6
    _print_step(1, total_steps, "初始化DXF处理流水线")
    try:
        pipeline = DxfProcessPipeline()
    except Exception as e:
        _print_error(f"初始化流水线失败: {e}")
        sys.exit(1)
    _print_ok("流水线已就绪")

    _print_step(2, total_steps, "解析DXF文件")
    try:
        result = pipeline.run(
            file_path=str(input_path),
            material=material,
            controller_type=controller,
            safe_z=safe_z,
            program_number=program_number,
        )
    except tuple(MANUFACTURING_ERRORS.values()) if MANUFACTURING_ERRORS else Exception as e:
        _print_error(f"制造流程异常: {e}")
        if args.verbose:
            traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        _print_error(f"未知错误: {e}")
        if args.verbose:
            traceback.print_exc()
        sys.exit(1)

    _print_separator("执行摘要")
    for stage in result.stages:
        status_icon = (
            f"{Colors.GREEN}✓{Colors.RESET}"
            if stage.status in ("success", "completed_with_errors")
            else f"{Colors.RED}✗{Colors.RESET}"
        )
        print(f"  {status_icon} {stage.name} ({stage.status})")
        if stage.errors:
            for err in stage.errors:
                print(f"    {Colors.RED}→{Colors.RESET} {err}")
        if stage.warnings:
            for warn in stage.warnings:
                print(f"    {Colors.YELLOW}→{Colors.RESET} {warn}")

    if not result.success:
        _print_error(f"流水线执行失败: {result.summary}")
        sys.exit(1)

    gcode = None
    if result.process_result and result.process_result.gcode_result:
        gcode = result.process_result.gcode_result
    elif result.gcode_result:
        gcode = result.gcode_result

    if gcode and gcode.program_text:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(gcode.program_text)
        except OSError as e:
            _print_error(f"写入输出文件失败: {e}")
            sys.exit(1)

        print()
        _print_separator("输出结果")
        print(f"  {Colors.BOLD}输出文件:{Colors.RESET} {output_path}")
        print(f"  {Colors.BOLD}G代码行数:{Colors.RESET} {gcode.total_lines}")
        print(f"  {Colors.BOLD}使用刀具数:{Colors.RESET} {gcode.tool_count}")
        print(f"  {Colors.BOLD}预估加工时间:{Colors.RESET} {gcode.estimated_cycle_time_min:.1f} min")
        if gcode.errors:
            print(f"  {Colors.BOLD}{Colors.RED}G代码错误:{Colors.RESET} {len(gcode.errors)} 条")
        if gcode.warnings:
            print(f"  {Colors.BOLD}{Colors.YELLOW}G代码警告:{Colors.RESET} {len(gcode.warnings)} 条")
        print(f"\n{Colors.GREEN}转换完成!{Colors.RESET}")
    else:
        _print_error("流水线未生成G代码输出")
        sys.exit(1)


def _load_data_manager():
    try:
        from app.data.process_data_manager import ProcessPlanningDataManager
        return ProcessPlanningDataManager()
    except Exception:
        return None


def _load_materials_fallback():
    json_path = _PYTHON_DIR / "app" / "data" / "materials.json"
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _load_tools_fallback():
    json_path = _PYTHON_DIR / "app" / "data" / "tools.json"
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def cmd_material(args):
    dm = _load_data_manager()
    materials_raw = []
    via_data_manager = False

    if dm is not None:
        try:
            materials_raw = dm.get_all_materials()
            via_data_manager = True
        except Exception as e:
            if args.verbose:
                _print_warning(f"数据管理器查询失败: {e}，回退到JSON直接加载")
            materials_raw = _load_materials_fallback()
    else:
        if args.verbose:
            _print_warning("数据管理器不可用，从JSON文件直接加载")
        materials_raw = _load_materials_fallback()

    if not materials_raw:
        _print_error("无法加载材料数据")
        sys.exit(1)

    if args.list:
        results = list(materials_raw)
    else:
        results = list(materials_raw)
        if args.type:
            allowed_categories = MATERIAL_TYPE_MAP.get(args.type, [args.type])
            if via_data_manager:
                results = [m for m in results if m.category in allowed_categories]
            else:
                results = [m for m in results if m.get("category") in allowed_categories]

        if args.name:
            search = args.name.strip().lower()
            if via_data_manager:
                results = [m for m in results if search in m.name.lower()]
            else:
                results = [m for m in results if search in m.get("name", "").lower()]

        if args.hardness_min is not None:
            if via_data_manager:
                results = [m for m in results if m.hardness_hb >= args.hardness_min]
            else:
                results = [m for m in results if m.get("hardness_hb", 0) >= args.hardness_min]

        if args.hardness_max is not None:
            if via_data_manager:
                results = [m for m in results if m.hardness_hb <= args.hardness_max]
            else:
                results = [m for m in results if m.get("hardness_hb", 0) <= args.hardness_max]

        if args.density_min is not None:
            if via_data_manager:
                results = [m for m in results if m.density_gcm3 >= args.density_min]
            else:
                results = [m for m in results if m.get("density_gcm3", 0) >= args.density_min]

        if args.density_max is not None:
            if via_data_manager:
                results = [m for m in results if m.density_gcm3 <= args.density_max]
            else:
                results = [m for m in results if m.get("density_gcm3", 0) <= args.density_max]

    if not results:
        print(f"{Colors.YELLOW}未找到符合条件的材料{Colors.RESET}")
        return

    if args.json:
        output = []
        for m in results:
            if via_data_manager:
                output.append({
                    "id": m.id,
                    "name": m.name,
                    "category": m.category,
                    "density_gcm3": m.density_gcm3,
                    "hardness_hb": m.hardness_hb,
                    "tensile_strength_mpa": m.tensile_strength_mpa,
                    "cutting_performance": m.cutting_performance,
                    "description": m.description,
                })
            else:
                output.append(m)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    _print_separator(f"材料查询结果 ({len(results)} 条)")
    header = f"  {'名称':<14s} {'类别':<16s} {'密度(g/cm³)':<12s} {'硬度(HB)':<10s} {'抗拉(MPa)':<10s} {'加工性':<10s}"
    print(Colors.BOLD + header + Colors.RESET)
    _print_separator()
    for m in results:
        if via_data_manager:
            name = m.name
            category = m.category
            density = f"{m.density_gcm3:.2f}"
            hardness = f"{m.hardness_hb:.0f}"
            tensile = f"{m.tensile_strength_mpa:.0f}"
            perf = m.cutting_performance
        else:
            name = m.get("name", "")
            category = m.get("category", "")
            density = f"{m.get('density_gcm3', 0):.2f}"
            hardness = f"{m.get('hardness_hb', 0):.0f}"
            tensile = f"{m.get('tensile_strength_mpa', 0):.0f}"
            perf = m.get("cutting_performance", "")
        print(f"  {name:<14s} {category:<16s} {density:<12s} {hardness:<10s} {tensile:<10s} {perf:<10s}")


def cmd_tool(args):
    dm = _load_data_manager()
    tools_raw = []
    via_data_manager = False

    if dm is not None:
        try:
            tools_raw = dm.get_all_tools()
            via_data_manager = True
        except Exception as e:
            if args.verbose:
                _print_warning(f"数据管理器查询失败: {e}，回退到JSON直接加载")
            tools_raw = _load_tools_fallback()
    else:
        if args.verbose:
            _print_warning("数据管理器不可用，从JSON文件直接加载")
        tools_raw = _load_tools_fallback()

    if not tools_raw:
        _print_error("无法加载刀具数据")
        sys.exit(1)

    if args.list:
        results = list(tools_raw)
    else:
        results = list(tools_raw)
        if args.type:
            target_series = TOOL_TYPE_MAP.get(args.type, args.type)
            if via_data_manager:
                results = [t for t in results if t.series == target_series]
            else:
                results = [t for t in results if t.get("series") == target_series]

        if args.diameter_min is not None:
            if via_data_manager:
                results = [t for t in results if t.diameter_mm >= args.diameter_min]
            else:
                results = [t for t in results if t.get("diameter_mm", 0) >= args.diameter_min]

        if args.diameter_max is not None:
            if via_data_manager:
                results = [t for t in results if t.diameter_mm <= args.diameter_max]
            else:
                results = [t for t in results if t.get("diameter_mm", 0) <= args.diameter_max]

        if args.material:
            search_mat = args.material.strip().lower()
            if via_data_manager:
                results = [t for t in results if search_mat in t.material.lower()]
            else:
                results = [t for t in results if search_mat in t.get("material", "").lower()]

    if not results:
        print(f"{Colors.YELLOW}未找到符合条件的刀具{Colors.RESET}")
        return

    if args.json:
        output = []
        for t in results:
            if via_data_manager:
                display_type = SERIES_TO_DISPLAY_TYPE.get(t.series, t.series)
                output.append({
                    "tool_id": t.id,
                    "name": t.name,
                    "type": display_type,
                    "diameter_mm": t.diameter_mm,
                    "material": t.material,
                    "application": t.application,
                    "description": t.description,
                })
            else:
                display_type = SERIES_TO_DISPLAY_TYPE.get(t.get("series", ""), t.get("series", ""))
                output.append({
                    "tool_id": t.get("id", ""),
                    "name": t.get("name", ""),
                    "type": display_type,
                    "diameter_mm": t.get("diameter_mm", 0),
                    "material": t.get("material", ""),
                    "application": t.get("application", ""),
                    "description": t.get("description", ""),
                })
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return

    _print_separator(f"刀具查询结果 ({len(results)} 条)")
    header = f"  {'刀号':<24s} {'类型':<10s} {'直径(mm)':<10s} {'材质':<10s} {'应用':<14s}"
    print(Colors.BOLD + header + Colors.RESET)
    _print_separator()
    for t in results:
        if via_data_manager:
            tool_id = t.id
            display_type = SERIES_TO_DISPLAY_TYPE.get(t.series, t.series)
            diameter = f"{t.diameter_mm:.1f}"
            material = t.material
            application = t.application
        else:
            tool_id = t.get("id", "")
            display_type = SERIES_TO_DISPLAY_TYPE.get(t.get("series", ""), t.get("series", ""))
            diameter = f"{t.get('diameter_mm', 0):.1f}"
            material = t.get("material", "")
            application = t.get("application", "")
        print(f"  {tool_id:<24s} {display_type:<10s} {diameter:<10s} {material:<10s} {application:<14s}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="lingjing_cli",
        description="灵境制造 - 命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python tools/lingjing_cli.py dxf2nc part.dxf -m 铝合金6061\n"
               "  python tools/lingjing_cli.py material --type steel\n"
               "  python tools/lingjing_cli.py tool --type drill",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"lingjing_cli v{VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    dxf_parser = subparsers.add_parser(
        "dxf2nc",
        help="将DXF图纸转换为NC加工程序",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python tools/lingjing_cli.py dxf2nc part.dxf\n"
               "  python tools/lingjing_cli.py dxf2nc part.dxf -m 铝合金6061 -c siemens_840d\n"
               "  python tools/lingjing_cli.py dxf2nc part.dxf -o output.nc --verbose",
    )
    dxf_parser.add_argument("input", help="输入的DXF文件路径")
    dxf_parser.add_argument("--output", "-o", default=None, help="输出文件路径 (默认: 输入文件名.nc)")
    dxf_parser.add_argument("--material", "-m", default="45#钢", help="零件材料名称 (默认: 45#钢)")
    dxf_parser.add_argument(
        "--controller", "-c",
        choices=["fanuc_0i", "siemens_840d", "heidenhain"],
        default="fanuc_0i",
        help="目标数控系统 (默认: fanuc_0i)",
    )
    dxf_parser.add_argument("--feedrate", "-f", type=float, default=None, help="进给率 mm/min (默认: 从知识库查询)")
    dxf_parser.add_argument("--spindle", "-s", type=int, default=None, help="主轴转速 RPM (默认: 从知识库查询)")
    dxf_parser.add_argument("--cut-depth", "-d", type=float, default=None, help="切削深度 mm (默认: 从知识库查询)")
    dxf_parser.add_argument("--safe-z", type=float, default=50.0, help="安全Z高度 mm (默认: 50.0)")
    dxf_parser.add_argument("--program-number", "-p", type=int, default=1000, help="NC程序号 (默认: 1000)")
    dxf_parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    dxf_parser.set_defaults(func=cmd_dxf2nc)

    mat_parser = subparsers.add_parser(
        "material",
        help="查询材料信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python tools/lingjing_cli.py material --list\n"
               "  python tools/lingjing_cli.py material --type aluminum\n"
               "  python tools/lingjing_cli.py material --name 钢 --hardness-min 150",
    )
    mat_parser.add_argument(
        "--type", "-t",
        choices=["steel", "aluminum", "titanium", "stainless", "cast_iron"],
        default=None,
        help="按材料类型过滤",
    )
    mat_parser.add_argument("--name", "-n", default=None, help="按材料名称搜索 (部分匹配)")
    mat_parser.add_argument("--hardness-min", type=float, default=None, help="最小硬度 (HB)")
    mat_parser.add_argument("--hardness-max", type=float, default=None, help="最大硬度 (HB)")
    mat_parser.add_argument("--density-min", type=float, default=None, help="最小密度 (g/cm³)")
    mat_parser.add_argument("--density-max", type=float, default=None, help="最大密度 (g/cm³)")
    mat_parser.add_argument("--json", action="store_true", help="以JSON格式输出")
    mat_parser.add_argument("--list", action="store_true", help="列出所有可用材料")
    mat_parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    mat_parser.set_defaults(func=cmd_material)

    tool_parser = subparsers.add_parser(
        "tool",
        help="查询刀具信息",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python tools/lingjing_cli.py tool --list\n"
               "  python tools/lingjing_cli.py tool --type end_mill\n"
               "  python tools/lingjing_cli.py tool --diameter-min 5 --diameter-max 12",
    )
    tool_parser.add_argument(
        "--type", "-t",
        choices=["end_mill", "ball_mill", "drill", "face_mill", "chamfer_mill"],
        default=None,
        help="按刀具类型过滤",
    )
    tool_parser.add_argument("--diameter-min", type=float, default=None, help="最小直径 (mm)")
    tool_parser.add_argument("--diameter-max", type=float, default=None, help="最大直径 (mm)")
    tool_parser.add_argument("--material", default=None, help="按适用材料过滤")
    tool_parser.add_argument("--json", action="store_true", help="以JSON格式输出")
    tool_parser.add_argument("--list", action="store_true", help="列出所有可用刀具")
    tool_parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    tool_parser.set_defaults(func=cmd_tool)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except BrokenPipeError:
        sys.stderr.close()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}操作已取消{Colors.RESET}")
        sys.exit(130)
    except Exception as e:
        _print_error(f"未预期的错误: {e}")
        if hasattr(args, "verbose") and args.verbose:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()