"""measured_stability_points.csv 的统一模式（schema）与校验工具。

每行代表**一个真实测量的切削试验**：给定的切削参数下，实测结果是稳定还是颤振，
以及（若论文报告了边界值）实测极限切深。

字段说明：
    source              数据来源（论文短名 或 数据集名，如 "i-CNC Zenodo 15308467"）
    doi                 论文 DOI 或数据集 URL（**必填**，学术诚信硬约束）
    material            材料（如 "Al6061-T6"）
    hardness_hb         布氏硬度 (HB)
    n_rpm               主轴转速 (rpm)
    feed_mm_per_tooth   每齿进给量 (mm/tooth)
    ap_mm               轴向切深 (mm)——本次试验实际采用的切深
    ae_mm               径向切宽 (mm)
    tool_diameter_mm    刀具直径 (mm)
    num_teeth           刀具齿数
    stable              实测结果：1=稳定，0=颤振（失稳）
    a_lim_measured_mm   实测极限切深 (mm)；论文未报告边界值时留空（NaN）

注意：
    - ap_mm 是"试验采用的切深"，stable 是该切深下的实测结果；
      a_lim_measured_mm 是"边界切深"，两者语义不同，不要混填。
    - 本 schema 拒绝伪造：stable / a_lim_measured_mm 必须来自真实测量，
      来源标注 "SCHEMA-FIXTURE" 的行仅用于接口测试。
"""

import csv
import os
from typing import Dict, List, Tuple

SCHEMA_COLUMNS: List[str] = [
    "source",
    "doi",
    "material",
    "hardness_hb",
    "n_rpm",
    "feed_mm_per_tooth",
    "ap_mm",
    "ae_mm",
    "tool_diameter_mm",
    "num_teeth",
    "stable",
    "a_lim_measured_mm",
]

# 数值列（缺失/空 NaN）
NUMERIC_COLUMNS: List[str] = [
    "hardness_hb",
    "n_rpm",
    "feed_mm_per_tooth",
    "ap_mm",
    "ae_mm",
    "tool_diameter_mm",
    "num_teeth",
    "stable",
    "a_lim_measured_mm",
]

# 必填非空列
REQUIRED_COLUMNS: List[str] = [
    "source",
    "doi",
    "n_rpm",
    "ap_mm",
    "ae_mm",
    "stable",
]

# 键列（引擎 7 维特征所需）
FEATURE_KEY_COLUMNS: List[str] = [
    "n_rpm",
    "feed_mm_per_tooth",
    "ap_mm",
    "ae_mm",
    "hardness_hb",
    "tool_diameter_mm",
    "num_teeth",
]


def validate_schema(rows: List[Dict[str, str]]) -> Tuple[bool, List[str]]:
    """校验行列表是否符合 schema，返回 (是否通过, 问题列表)。"""
    problems: List[str] = []
    if not rows:
        return False, ["空文件：没有任何行"]
    for i, row in enumerate(rows, start=2):  # 第 1 行是表头
        for col in REQUIRED_COLUMNS:
            val = (row.get(col) or "").strip()
            if not val:
                problems.append(f"第 {i} 行：必填列 '{col}' 为空")
        # stable 取值校验
        st = (row.get("stable") or "").strip()
        if st not in ("0", "1"):
            problems.append(f"第 {i} 行：stable 必须为 0 或 1，得到 '{st}'")
        # doi 校验
        doi = (row.get("doi") or "").strip()
        if not (doi.startswith("http") or doi.startswith("10.")):
            problems.append(f"第 {i} 行：doi 必须是以 http 或 10. 开头的有效引用，得到 '{doi}'")
    return len(problems) == 0, problems


def load_rows(csv_path: str) -> List[Dict[str, str]]:
    """读取 CSV 为行字典列表（保持顺序）。"""
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [dict(r) for r in reader]


def to_float(row: Dict[str, str], col: str) -> float:
    """将列转为 float；空/非法返回 NaN。"""
    raw = (row.get(col) or "").strip()
    if not raw:
        return float("nan")
    try:
        return float(raw)
    except ValueError:
        return float("nan")
