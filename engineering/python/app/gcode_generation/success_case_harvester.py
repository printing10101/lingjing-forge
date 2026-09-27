"""成功案例自动入工艺库（Phase 0 自进化 · M4a）。

失败案例库（M1-M3）只记成功计数要素；本模块是成功案例的另一条管道：
把一次通过 GENERATED 的任务中 **stable 特征的实证切削参数** 沉淀为
工艺四元组（ProcessQuadruple），供 ``recommend_process`` 检索复用。

映射规则（保守，宁缺毋滥）：
- feature: FeatureGCodeResult.feature_type → 四元组特征词表
  （plane→face, cylinder→profile, hole→hole, boss→profile），
  未知类型直接跳过，不硬造词条；
- process: 固定 ``mill``——生成链路只证明「铣削该特征且颤振稳定」，
  不区分粗/精加工（上游 operation plan 未透传工序类型）；
- tool: ``unspecified``——生成链路无刀具信息，诚实标注缺失；
- parameters: spindle_rpm / axial_depth_mm / limit_depth_mm /
  safety_margin_ratio（全部来自阶段 5 ChatterReport，不二次加工）；
- confidence: 0.9——生成 + L1-L6 安全校验 + 颤振稳定验证的实证，
  高于经验值（0.7）、低于手册（1.0），因未上机验证；
- source: ``generated_validated``。

去重策略：同 (feature, material) 已存在 **相同参数**（rpm 与切深按
0.01 mm 取整一致）的 generated_validated 实证则跳过——重复实证不重复
入索引；不同参数的实证有价值（覆盖参数区间），正常入索引。

非致命约束：采集/写入任何异常只记 warning，绝不影响生成主流程
（与失败案例库 _record_outcome 同一约定）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.gcode_generation.gcode_store import FeatureGCodeResult
from app.rag.process_quadruple import ProcessQuadruple, ProcessQuadrupleIndex

logger = logging.getLogger(__name__)

__all__ = [
    "SUCCESS_CONFIDENCE",
    "SUCCESS_SOURCE",
    "FEATURE_TYPE_TO_QUAD_FEATURE",
    "build_quadruples_from_success",
    "ingest_success_cases",
]

# 成功实证的置信度与来源标签
SUCCESS_CONFIDENCE = 0.9
SUCCESS_SOURCE = "generated_validated"

# feature_type → 四元组特征词表映射（缺省保守：未知即跳过）
FEATURE_TYPE_TO_QUAD_FEATURE: dict[str, str] = {
    "plane": "face",
    "cylinder": "profile",
    "hole": "hole",
    "boss": "profile",
}

# 参数数值取整精度（查重用；切深按 0.01 mm、转速按 1 rpm 对齐）
_DEPTH_PRECISION = 2
_RPM_PRECISION = 0


def _round_key(value: float, ndigits: int) -> float:
    return round(float(value), ndigits)


def _params_fingerprint(parameters: dict[str, Any]) -> tuple[float, float]:
    """参数指纹：(spindle_rpm, axial_depth_mm) 取整元组，查重比较用。"""
    rpm = _round_key(parameters.get("spindle_rpm", 0.0), _RPM_PRECISION)
    depth = _round_key(parameters.get("axial_depth_mm", 0.0), _DEPTH_PRECISION)
    return (rpm, depth)


def build_quadruples_from_success(
    task_id: str,
    controller_type: str,
    feature_results: list[FeatureGCodeResult],
) -> list[ProcessQuadruple]:
    """从一次成功任务的特征结果构建工艺四元组列表。

    只收 stable 特征；feature_type 不在映射表内的跳过。
    纯函数：不触库、不抛业务异常（输入异常向上抛由调用方兜底）。
    """
    quads: list[ProcessQuadruple] = []
    for fr in feature_results:
        if not fr.stable:
            continue
        quad_feature = FEATURE_TYPE_TO_QUAD_FEATURE.get(fr.feature_type)
        if quad_feature is None:
            logger.debug("跳过未知 feature_type=%s（feature_id=%s）", fr.feature_type, fr.feature_id)
            continue
        parameters: dict[str, Any] = {
            "spindle_rpm": fr.spindle_rpm,
            "axial_depth_mm": fr.axial_depth_mm,
            "limit_depth_mm": fr.limit_depth_mm,
            "safety_margin_ratio": fr.safety_margin_ratio,
        }
        quads.append(
            ProcessQuadruple(
                feature=quad_feature,
                process="mill",
                tool="unspecified",
                parameters=parameters,
                material=fr.material_id,
                confidence=SUCCESS_CONFIDENCE,
                source=SUCCESS_SOURCE,
                chunk_ids=[],
                tags=[
                    "success_case",
                    f"task:{task_id}",
                    f"controller:{controller_type}" if controller_type else "controller:unknown",
                ],
            )
        )
    return quads


def ingest_success_cases(
    quads: list[ProcessQuadruple],
    index: ProcessQuadrupleIndex,
) -> tuple[int, int]:
    """把成功实证四元组写入工艺索引（带去重）。

    Returns:
        (added, skipped) —— 新入索引数与去重跳过数。
    """
    added = 0
    skipped = 0
    for quad in quads:
        if _is_duplicate(quad, index):
            skipped += 1
            continue
        index.add(quad)
        added += 1
    if added:
        # 成功实证是低频高价值写入，立即持久化防进程崩溃丢数据
        index.flush(force=True)
    return added, skipped


def _is_duplicate(quad: ProcessQuadruple, index: ProcessQuadrupleIndex) -> bool:
    """同 (feature, material) 且同参数指纹的实证视为重复。"""
    try:
        similar = index.find_similar(quad.feature, quad.material, top_k=50)
    except Exception as e:  # noqa: BLE001 - 查重失败按「不重复」处理，宁多入不丢数据
        logger.warning("工艺四元组查重失败（按新增处理）: %s", e)
        return False
    fingerprint = _params_fingerprint(quad.parameters)
    for item in similar:
        if item.get("source") != SUCCESS_SOURCE:
            continue
        if item.get("match_level") != "exact":
            continue
        if _params_fingerprint(item.get("parameters", {})) == fingerprint:
            return True
    return False
