"""P1-3 白盒模块：DXF 六阶段流水线编排声明（纯 Python，零框架依赖）。

抽取自 `app/dxf/pipeline.py` 的「阶段编排判定」逻辑（P1-1 方法论复用）：

- 六阶段定义（名称/顺序/是否可降级）→ `STAGES`
- 阶段失败是否阻断后续 → `stage_failure_is_fatal`
- 执行进度计算 → `progress_of`
- 结果汇总（success/summary）→ `summarize_pipeline`

设计要点：
- 纯 stdlib（dataclass + enum + typing），不 import dxf/cadquery/ezdxf
- 阶段名与 pipeline.py 中文名逐字对齐（测试锁定防漂移）
- 编排语义与 pipeline.py 实际行为一致：
  - Stage 3（3D模型转换）失败 → 降级继续（非致命）
  - 其余阶段失败 → 流水线中止（致命）
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class StageKey(str, Enum):
    """DXF 流水线六阶段标识（与 pipeline.py 阶段顺序一致）。"""

    PARSE = "parse"
    FEATURES = "features"
    MODEL_CONVERT = "model_convert"
    DATA_ASSEMBLY = "data_assembly"
    PROCESS_PLANNING = "process_planning"
    VALIDATION = "validation"


class StageStatus(str, Enum):
    """阶段执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(frozen=True)
class StageSpec:
    """阶段声明：顺序 + 名称 + 致命性。"""

    key: StageKey
    name: str  # 与 pipeline.py DxfPipelineStage(name=...) 对齐
    fatal_on_failure: bool = True  # False = 失败降级继续


# 六阶段声明（顺序即执行顺序；名称与 pipeline.py 逐字对齐）
STAGES: tuple[StageSpec, ...] = (
    StageSpec(StageKey.PARSE, "DXF解析", fatal_on_failure=True),
    StageSpec(StageKey.FEATURES, "特征提取", fatal_on_failure=True),
    StageSpec(StageKey.MODEL_CONVERT, "3D模型转换", fatal_on_failure=False),  # 降级继续
    StageSpec(StageKey.DATA_ASSEMBLY, "数据组装", fatal_on_failure=True),
    StageSpec(StageKey.PROCESS_PLANNING, "工艺规划", fatal_on_failure=True),
    StageSpec(StageKey.VALIDATION, "结果验证", fatal_on_failure=True),
)

_STAGE_BY_KEY = {s.key: s for s in STAGES}
_STAGE_ORDER = [s.key for s in STAGES]


def stage_name(key: StageKey | str) -> str:
    """按 key 取阶段名称（中文，与 pipeline.py 对齐）。"""
    k = StageKey(key)
    return _STAGE_BY_KEY[k].name


def stage_failure_is_fatal(key: StageKey | str) -> bool:
    """阶段失败是否致命（True=中止流水线，False=降级继续）。"""
    k = StageKey(key)
    return _STAGE_BY_KEY[k].fatal_on_failure


def is_fatal_stage(key: StageKey | str) -> bool:
    """是否致命阶段（失败会中止流水线）。"""
    return stage_failure_is_fatal(key)


def stage_index(key: StageKey | str) -> int:
    """阶段在流水线中的序号（0 起）。"""
    return _STAGE_ORDER.index(StageKey(key))


def progress_of(stage_statuses: dict[str, str]) -> float:
    """按各阶段状态计算流水线完成度（0.0-1.0）。

    规则：成功/失败阶段计入已完成；pending/running 不计。
    与 pipeline.py 的「顺序执行、致命失败中止」语义一致——
    已完成阶段数 / 总阶段数。

    Args:
        stage_statuses: {stage_key: status}（status 为 StageStatus 值或
            pipeline.py 的 success/failed/pending）。

    Returns:
        完成度 0.0-1.0（全部完成 = 1.0）。
    """
    if not stage_statuses:
        return 0.0
    done = 0
    for key in _STAGE_ORDER:
        status = stage_statuses.get(key.value)
        if status in (StageStatus.SUCCESS.value, StageStatus.FAILED.value):
            done += 1
    return round(done / len(STAGES), 4)


def should_abort_after(stage_key: StageKey | str, failed: bool) -> bool:
    """某阶段结束后是否应中止流水线。

    Args:
        stage_key: 刚执行的阶段。
        failed: 该阶段是否失败。

    Returns:
        True 中止；False 继续下一阶段。
    """
    if not failed:
        return False
    # 失败阶段若致命 中止；可降级阶段失败 继续
    return stage_failure_is_fatal(stage_key)


def summarize_pipeline(
    stage_statuses: dict[str, str],
    success: bool,
) -> str:
    """生成流水线结果摘要（与 pipeline.py summary 语义一致）。

    Returns:
        摘要文本（如「流水线在 DXF解析 阶段失败」/「DXF流水线处理成功」）。
    """
    if success:
        return "DXF流水线处理成功"

    # 找第一个失败阶段（按执行顺序）
    for spec in STAGES:
        status = stage_statuses.get(spec.key.value)
        if status == StageStatus.FAILED.value:
            return f"流水线在{spec.name}阶段失败"
    return "DXF流水线执行失败"


__all__ = [
    "StageKey",
    "StageStatus",
    "StageSpec",
    "STAGES",
    "stage_name",
    "stage_failure_is_fatal",
    "is_fatal_stage",
    "stage_index",
    "progress_of",
    "should_abort_after",
    "summarize_pipeline",
]
