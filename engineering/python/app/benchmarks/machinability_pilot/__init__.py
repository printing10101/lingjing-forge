"""machinability pilot 基准：LLM 生成曲面 CAD 的加工物理评估。

科研背景见 docs/paper_and_competition/F0（行动路径）/ F2（pilot 设计）。
当前包含 M1 真值案例集生成（CLI: python -m ...generate_cases）；
S1-S5 评估漏斗在 M2/M3 接入。

注意：不在包 __init__ 里导入 generate_cases——它是 `python -m` 的执行目标，
预导入会触发 runpy 的 sys.modules 冲突。
"""

from app.benchmarks.machinability_pilot.case_families import (
    DIFFICULTY_TIERS,
    FAMILY_REGISTRY,
    CaseFamily,
    all_case_ids,
)

__all__ = [
    "DIFFICULTY_TIERS",
    "FAMILY_REGISTRY",
    "CaseFamily",
    "all_case_ids",
]
