"""录入文献实测稳定性点 → measured_stability_points.csv。

用途：从已发表的稳定性叶瓣实验论文中提取**实测**稳定/失稳数据点（论文表格或
数字化图件数据），逐行录入 schema。这是零设备条件下获取"真实测量数据"的主要
通道——每一行都是已发表、可查证的实测结果。

学术诚信硬约束：
    - 每个点必须有 source（论文短名）和 doi（或可访问 URL）。
    - 只录入论文**明确报告**的数值；从图件数字化得到的数据要在 source 中注明
      "（digitized from Fig.X）"。
    - 禁止凭记忆/推断填写数值——那不是实测数据。

用法：
    # 交互式录入（推荐，逐步校验）
    ../.venv/Scripts/python.exe real_validation/ingest_literature_points.py

    # 单行追加（管道/脚本方式）
    ../.venv/Scripts/python.exe real_validation/ingest_literature_points.py \
        --append "Budak&Altintas 1998|10.1016/S0007-8506(07)62603-3|Al7075|150|8000|0.1|1.0|8.0|10.0|2|1|"

    # 校验现有文件
    ../.venv/Scripts/python.exe real_validation/ingest_literature_points.py --validate
"""

import argparse
import csv
import os
import sys
from typing import Dict, List

try:
    from .schema import SCHEMA_COLUMNS, load_rows, validate_schema
except ImportError:  # 直接以脚本方式运行（python real_validation/xxx.py）时无包上下文
    import os as _os
    _PKG_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # experiments/
    if _PKG_DIR not in sys.path:
        sys.path.insert(0, _PKG_DIR)
    from real_validation.schema import SCHEMA_COLUMNS, load_rows, validate_schema

DEFAULT_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "datasets", "measured_stability", "measured_stability_points.csv",
)


def _write_rows(csv_path: str, rows: List[Dict[str, str]]) -> None:
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCHEMA_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({c: (r.get(c, "") or "") for c in SCHEMA_COLUMNS})
    print(f"已写入 {len(rows)} 行 → {csv_path}")


def append_row(csv_path: str, row: Dict[str, str]) -> None:
    rows = load_rows(csv_path) if os.path.exists(csv_path) else []
    rows.append(row)
    ok, problems = validate_schema(rows)
    if not ok:
        print("写入被拒绝，schema 校验失败：")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    _write_rows(csv_path, rows)


def interactive(csv_path: str) -> None:
    print("交互式录入文献实测稳定性点（Ctrl+C 退出）")
    print("提示：每一行对应论文中一次**实测**切削试验。")
    while True:
        print("\n--- 新行 ---")
        row: Dict[str, str] = {}
        row["source"] = input("source（论文短名，如 Budak&Altintas 1998）: ").strip()
        row["doi"] = input("doi 或 URL: ").strip()
        row["material"] = input("材料（如 Al7075）: ").strip()
        for col, label in [
            ("hardness_hb", "硬度 HB"),
            ("n_rpm", "主轴转速 rpm"),
            ("feed_mm_per_tooth", "每齿进给 mm/tooth"),
            ("ap_mm", "轴向切深 mm（试验采用的切深）"),
            ("ae_mm", "径向切宽 mm"),
            ("tool_diameter_mm", "刀具直径 mm"),
            ("num_teeth", "齿数"),
        ]:
            v = input(f"{label}: ").strip()
            row[col] = v
        st = input("实测结果 stable（1=稳定 / 0=颤振）: ").strip()
        row["stable"] = "1" if st == "1" else "0"
        row["a_lim_measured_mm"] = input(
            "实测边界切深 mm（论文报告了才填，没有直接回车）: "
        ).strip()
        append_row(csv_path, row)
        again = input("继续录入？(y/n): ").strip().lower()
        if again != "y":
            break


def parse_append_arg(text: str) -> Dict[str, str]:
    parts = text.split("|")
    if len(parts) != len(SCHEMA_COLUMNS):
        sys.exit(
            f"参数必须含 {len(SCHEMA_COLUMNS)} 个 | 分隔字段，按序：\n"
            + " | ".join(SCHEMA_COLUMNS)
        )
    return dict(zip(SCHEMA_COLUMNS, parts))


def main() -> int:
    ap = argparse.ArgumentParser(description="录入文献实测稳定性点")
    ap.add_argument("--csv", default=DEFAULT_CSV)
    ap.add_argument("--append", help="单行追加，格式见文档")
    ap.add_argument("--validate", action="store_true", help="校验现有文件")
    args = ap.parse_args()

    if args.validate:
        if not os.path.exists(args.csv):
            print(f"文件不存在: {args.csv}")
            return 2
        rows = load_rows(args.csv)
        ok, problems = validate_schema(rows)
        print(f"{args.csv}：{len(rows)} 行")
        if ok:
            print("schema 校验通过 ✓")
            return 0
        for p in problems:
            print("  -", p)
        return 1

    if args.append:
        append_row(args.csv, parse_append_arg(args.append))
        return 0

    interactive(args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
