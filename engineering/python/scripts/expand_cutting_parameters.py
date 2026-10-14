"""切参数据扩容脚本（ICT 备赛 C3：cutting_parameters.json 12 → 200+ 条）。

数据来源（I1 技术路线 §1 的"文献数字化"一路，其余两路待数据就位后接入）：
- 公开切削用量手册的常用推荐区间，按 材料 × 刀具材料 × 工序 矩阵结构化录入；
- 每条带 ``source=handbook_common_range``，description 注明区间性质；
- uniwear 派生行（需转速/进给工况列）与 pilot 真值行暂缓——数据源未含
  可溯源切参数值，按"禁止无出处编造数值"纪律不生成本批数据。

用法：
    python scripts/expand_cutting_parameters.py --dry-run   # 只校验不落盘
    python scripts/expand_cutting_parameters.py             # 原子写回 JSON
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = PROJECT_ROOT / "app" / "data" / "cutting_parameters.json"

SOURCE_HANDBOOK = "handbook_common_range"

# ---------------------------------------------------------------------------
# 手册矩阵：切削速度 Vc (m/min)，按 (材料, 刀具材料)
# 数值为公开手册常用推荐区间；ceramic 仅对钢/铸铁车削有效，PCD 仅对
# 有色金属/塑料有效（与铁族材料的亲和反应限制），未列出的组合不生成。
# ---------------------------------------------------------------------------
SPEED_MATRIX: dict[str, dict[str, tuple[float, float]]] = {
    "material_45steel": {
        "HSS": (20, 30),
        "carbide": (80, 120),
        "coated_carbide": (120, 180),
        "ceramic": (200, 350),
    },
    "material_ss304": {
        "HSS": (10, 20),
        "carbide": (60, 100),
        "coated_carbide": (90, 140),
        "ceramic": (150, 250),
    },
    "material_ss316l": {
        "HSS": (8, 16),
        "carbide": (50, 90),
        "coated_carbide": (80, 120),
        "ceramic": (120, 200),
    },
    "material_ht250": {
        "HSS": (15, 25),
        "carbide": (60, 100),
        "coated_carbide": (80, 130),
        "ceramic": (200, 400),
    },
    "material_qt500": {
        "HSS": (12, 20),
        "carbide": (50, 90),
        "coated_carbide": (70, 120),
        "ceramic": (150, 300),
    },
    "material_tc4": {
        "HSS": (8, 15),
        "carbide": (25, 50),
        "coated_carbide": (35, 70),
    },
    "material_al6061": {
        "HSS": (60, 120),
        "carbide": (200, 400),
        "coated_carbide": (200, 500),
        "PCD": (600, 1200),
    },
    "material_h59": {
        "HSS": (50, 80),
        "carbide": (150, 250),
        "coated_carbide": (150, 300),
        "PCD": (300, 600),
    },
    "material_t2": {
        "HSS": (40, 70),
        "carbide": (120, 220),
        "coated_carbide": (120, 250),
        "PCD": (300, 700),
    },
    "material_abs": {
        "HSS": (40, 80),
        "carbide": (100, 250),
        "coated_carbide": (100, 250),
        "PCD": (250, 500),
    },
    "material_pom": {
        "HSS": (40, 90),
        "carbide": (100, 250),
        "coated_carbide": (100, 250),
        "PCD": (250, 500),
    },
    "material_pa6": {
        "HSS": (40, 100),
        "carbide": (100, 300),
        "coated_carbide": (100, 300),
        "PCD": (250, 500),
    },
    # ---- C3 第二批（2026-10-14）：新增常用材料，同一"公开手册常用推荐区间"口径 ----
    "material_q235": {
        "HSS": (18, 28),
        "carbide": (90, 130),
        "coated_carbide": (130, 190),
        "ceramic": (220, 350),
    },
    "material_20steel": {
        "HSS": (18, 28),
        "carbide": (90, 130),
        "coated_carbide": (130, 190),
        "ceramic": (220, 350),
    },
    "material_40cr": {
        "HSS": (15, 25),
        "carbide": (70, 110),
        "coated_carbide": (100, 160),
        "ceramic": (180, 300),
    },
    "material_65mn": {
        "HSS": (12, 20),
        "carbide": (60, 100),
        "coated_carbide": (90, 140),
        "ceramic": (150, 250),
    },
    "material_t8a": {
        "HSS": (12, 20),
        "carbide": (60, 100),
        "coated_carbide": (90, 150),
        "ceramic": (150, 250),
    },
    "material_20crmnti": {
        "HSS": (15, 24),
        "carbide": (70, 110),
        "coated_carbide": (100, 160),
        "ceramic": (180, 300),
    },
    "material_gcr15": {
        "HSS": (12, 18),
        "carbide": (55, 90),
        "coated_carbide": (80, 130),
        "ceramic": (140, 240),
    },
    "material_ht200": {
        "HSS": (18, 30),
        "carbide": (70, 120),
        "coated_carbide": (90, 150),
        "ceramic": (250, 450),
    },
    "material_qt450": {
        "HSS": (14, 24),
        "carbide": (60, 100),
        "coated_carbide": (80, 140),
        "ceramic": (180, 320),
    },
    "material_al7075": {
        "HSS": (60, 120),
        "carbide": (250, 450),
        "coated_carbide": (300, 600),
        "PCD": (700, 1500),
    },
    "material_2a12": {
        "HSS": (50, 100),
        "carbide": (200, 400),
        "coated_carbide": (200, 450),
        "PCD": (500, 1200),
    },
    "material_zl101": {
        "HSS": (40, 90),
        "carbide": (150, 300),
        "coated_carbide": (180, 350),
        "PCD": (500, 1500),
    },
    "material_h62": {
        "HSS": (60, 100),
        "carbide": (180, 300),
        "coated_carbide": (180, 320),
        "PCD": (400, 800),
    },
    "material_qal94": {
        "HSS": (30, 60),
        "carbide": (100, 200),
        "coated_carbide": (120, 250),
        "PCD": (250, 600),
    },
    "material_ta1": {
        "HSS": (10, 20),
        "carbide": (30, 60),
        "coated_carbide": (45, 90),
    },
    "material_az91d": {
        "HSS": (100, 250),
        "carbide": (300, 600),
        "coated_carbide": (300, 800),
        "PCD": (600, 1500),
    },
    "material_inconel718": {
        "HSS": (5, 12),
        "carbide": (25, 45),
        "coated_carbide": (35, 70),
        "ceramic": (200, 300),
    },
}

MATERIAL_NAMES: dict[str, str] = {
    "material_45steel": "45#钢",
    "material_ss304": "不锈钢304",
    "material_ss316l": "不锈钢316L",
    "material_ht250": "灰铸铁HT250",
    "material_qt500": "球墨铸铁QT500",
    "material_tc4": "钛合金TC4",
    "material_al6061": "铝合金6061",
    "material_h59": "黄铜H59",
    "material_t2": "紫铜T2",
    "material_abs": "ABS塑料",
    "material_pom": "POM赛钢",
    "material_pa6": "尼龙PA6",
    # ---- C3 第二批新增材料 ----
    "material_q235": "Q235碳素结构钢",
    "material_20steel": "20#优质碳素钢",
    "material_40cr": "40Cr合金结构钢",
    "material_65mn": "65Mn弹簧钢",
    "material_t8a": "T8A碳素工具钢",
    "material_20crmnti": "20CrMnTi渗碳齿轮钢",
    "material_gcr15": "GCr15轴承钢",
    "material_ht200": "灰铸铁HT200",
    "material_qt450": "球墨铸铁QT450-10",
    "material_al7075": "铝合金7075",
    "material_2a12": "硬铝2A12",
    "material_zl101": "铸造铝合金ZL101",
    "material_h62": "黄铜H62",
    "material_qal94": "铝青铜QA19-4",
    "material_ta1": "工业纯钛TA1",
    "material_az91d": "镁合金AZ91D",
    "material_inconel718": "镍基高温合金Inconel 718",
}

# 进给量按 (材料类别, 刀具/工序) 给区间；drill/turn/boring 为 mm/r，
# 铣削系列为 mm/齿（与既有 12 条的 feed_unit 约定一致）。
FEED_MATRIX: dict[str, dict[str, tuple[float, float]]] = {
    "steel": {
        "twist_drill": (0.12, 0.25),
        "endmill": (0.05, 0.12),
        "ball_nose": (0.03, 0.08),
        "taper_ball": (0.03, 0.08),
        "turning_tool": (0.15, 0.35),
        "boring_bar": (0.06, 0.18),
    },
    "stainless": {
        "twist_drill": (0.06, 0.15),
        "endmill": (0.04, 0.10),
        "ball_nose": (0.02, 0.06),
        "taper_ball": (0.02, 0.06),
        "turning_tool": (0.10, 0.25),
        "boring_bar": (0.05, 0.12),
    },
    "titanium": {
        "twist_drill": (0.05, 0.12),
        "endmill": (0.03, 0.08),
        "ball_nose": (0.02, 0.05),
        "taper_ball": (0.02, 0.05),
        "turning_tool": (0.08, 0.20),
        "boring_bar": (0.04, 0.10),
    },
    "iron": {
        "twist_drill": (0.12, 0.28),
        "endmill": (0.06, 0.15),
        "ball_nose": (0.03, 0.08),
        "taper_ball": (0.03, 0.08),
        "turning_tool": (0.15, 0.40),
        "boring_bar": (0.06, 0.20),
    },
    "nonferrous": {
        "twist_drill": (0.10, 0.30),
        "endmill": (0.06, 0.18),
        "ball_nose": (0.04, 0.10),
        "taper_ball": (0.04, 0.10),
        "turning_tool": (0.10, 0.35),
        "boring_bar": (0.05, 0.20),
    },
    "plastic": {
        "twist_drill": (0.10, 0.30),
        "endmill": (0.08, 0.25),
        "ball_nose": (0.05, 0.15),
        "taper_ball": (0.05, 0.15),
        "turning_tool": (0.10, 0.30),
        "boring_bar": (0.05, 0.15),
    },
    # C3 第二批：镍基高温合金（低进给、小切深以防加工硬化）
    "superalloy": {
        "twist_drill": (0.05, 0.10),
        "endmill": (0.03, 0.07),
        "ball_nose": (0.02, 0.05),
        "taper_ball": (0.02, 0.05),
        "turning_tool": (0.08, 0.20),
        "boring_bar": (0.04, 0.10),
    },
}

MATERIAL_CLASS: dict[str, str] = {
    "material_45steel": "steel",
    "material_ss304": "stainless",
    "material_ss316l": "stainless",
    "material_ht250": "iron",
    "material_qt500": "iron",
    "material_tc4": "titanium",
    "material_al6061": "nonferrous",
    "material_h59": "nonferrous",
    "material_t2": "nonferrous",
    "material_abs": "plastic",
    "material_pom": "plastic",
    "material_pa6": "plastic",
    # ---- C3 第二批新增材料 ----
    "material_q235": "steel",
    "material_20steel": "steel",
    "material_40cr": "steel",
    "material_65mn": "steel",
    "material_t8a": "steel",
    "material_20crmnti": "steel",
    "material_gcr15": "steel",
    "material_ht200": "iron",
    "material_qt450": "iron",
    "material_al7075": "nonferrous",
    "material_2a12": "nonferrous",
    "material_zl101": "nonferrous",
    "material_h62": "nonferrous",
    "material_qal94": "nonferrous",
    "material_ta1": "titanium",
    "material_az91d": "nonferrous",
    "material_inconel718": "superalloy",
}

TOOL_SERIES = (
    "twist_drill",
    "endmill",
    "ball_nose",
    "taper_ball",
    "turning_tool",
    "boring_bar",
)

PER_REV_SERIES = {"twist_drill", "turning_tool", "boring_bar"}

# ceramic 只以车削形式出现在钢/铸铁（见 SPEED_MATRIX），在此声明组合合法性，
# 生成时据此跳过 SPEED_MATRIX 里有速度但工序不适用的行。
CERAMIC_ALLOWED_SERIES = {"turning_tool"}

VALID_FEED_UNITS = {"mm/r", "mm/齿"}


def feed_unit_for(series: str) -> str:
    return "mm/r" if series in PER_REV_SERIES else "mm/齿"


def generate_entries() -> list[dict]:
    """按矩阵生成候选条目（不做去重，纯矩阵展开）。"""
    entries: list[dict] = []
    for material_id, by_tool in SPEED_MATRIX.items():
        material_name = MATERIAL_NAMES[material_id]
        material_class = MATERIAL_CLASS[material_id]
        for tool_material, (v_min, v_max) in by_tool.items():
            for series in TOOL_SERIES:
                if tool_material == "ceramic" and series not in CERAMIC_ALLOWED_SERIES:
                    continue
                f_min, f_max = FEED_MATRIX[material_class][series]
                entries.append(
                    {
                        "id": f"param_{material_id.removeprefix('material_')}_{tool_material}_{series}",
                        "material_id": material_id,
                        "material_name": material_name,
                        "tool_series": series,
                        "tool_material": tool_material,
                        "cutting_speed_min_mpm": v_min,
                        "cutting_speed_max_mpm": v_max,
                        "feed_min_mmpr": f_min,
                        "feed_max_mmpr": f_max,
                        "feed_unit": feed_unit_for(series),
                        "description": (
                            f"{material_name}使用{tool_material}{series}加工的推荐切削参数"
                            "（公开手册常用推荐区间）"
                        ),
                        "source": SOURCE_HANDBOOK,
                    }
                )
    return entries


def validate_entry(entry: dict, index: int) -> None:
    """单条 schema 校验，非法即抛 ValueError（fail fast）。"""
    required = (
        "id",
        "material_id",
        "material_name",
        "tool_series",
        "tool_material",
        "cutting_speed_min_mpm",
        "cutting_speed_max_mpm",
        "feed_min_mmpr",
        "feed_max_mmpr",
        "feed_unit",
        "description",
    )
    for key in required:
        if key not in entry:
            raise ValueError(f"第 {index} 条缺少字段 {key}")
    for key in ("cutting_speed_min_mpm", "cutting_speed_max_mpm",
                "feed_min_mmpr", "feed_max_mmpr"):
        if not isinstance(entry[key], (int, float)) or entry[key] <= 0:
            raise ValueError(f"第 {index} 条字段 {key} 必须为正数")
    if entry["cutting_speed_min_mpm"] > entry["cutting_speed_max_mpm"]:
        raise ValueError(f"第 {index} 条切削速度区间颠倒")
    if entry["feed_min_mmpr"] > entry["feed_max_mmpr"]:
        raise ValueError(f"第 {index} 条进给区间颠倒")
    if entry["feed_unit"] not in VALID_FEED_UNITS:
        raise ValueError(f"第 {index} 条 feed_unit 非法: {entry['feed_unit']}")
    if not entry.get("source"):
        raise ValueError(f"第 {index} 条缺少 source 溯源字段")


def dedup_key(entry: dict) -> tuple:
    return (entry["material_id"], entry["tool_series"], entry["tool_material"])


def merge(existing: list[dict], candidates: list[dict]) -> list[dict]:
    """既有条目不动，候选按 (material, series, tool_material) 去重后追加。"""
    seen = {dedup_key(e) for e in existing}
    ids = {e["id"] for e in existing}
    merged = list(existing)
    added = 0
    for entry in candidates:
        validate_entry(entry, added)
        if dedup_key(entry) in seen:
            continue
        if entry["id"] in ids:
            raise ValueError(f"id 重复: {entry['id']}")
        seen.add(dedup_key(entry))
        ids.add(entry["id"])
        merged.append(entry)
        added += 1
    return merged


def atomic_write(path: Path, payload: list[dict]) -> None:
    """先写临时文件再 os.replace，避免半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.stem + "_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="切参数据扩容（手册矩阵数字化）")
    parser.add_argument("--dry-run", action="store_true", help="只校验与预览，不写回")
    parser.add_argument(
        "--target", type=int, default=500, help="扩容后最少条数验收线（第二批默认 500）"
    )
    args = parser.parse_args(argv)

    existing = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    candidates = generate_entries()
    merged = merge(existing, candidates)

    by_material: dict[str, int] = {}
    for e in merged:
        by_material[e["material_name"]] = by_material.get(e["material_name"], 0) + 1

    print(f"既有条目: {len(existing)}")
    print(f"矩阵候选: {len(candidates)}")
    print(f"合并后:   {len(merged)}（新增 {len(merged) - len(existing)}）")
    for name in sorted(by_material):
        print(f"  {name}: {by_material[name]} 条")

    if len(merged) < args.target:
        print(f"未达验收线 {args.target} 条", file=sys.stderr)
        return 1

    if args.dry_run:
        print("dry-run：校验通过，未写盘")
        return 0

    atomic_write(DATA_FILE, merged)
    print(f"已原子写回 {DATA_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
