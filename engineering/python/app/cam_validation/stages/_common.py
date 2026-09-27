"""CAM 校验流水线 stages 子包共享基础设施（P1-3 拆分自原 pipeline.py）。

本模块提供 3 个 stage mixin 共享的：
    - 常量：``_DEFAULT_STOCK_LENGTH_MM`` / ``_DEFAULT_STOCK_WIDTH_MM`` /
      ``_DEFAULT_MODE``
    - 数据类：``CamValidationResult``（编排器返回值，公开 API）
    - 模块 logger

设计原则：
    - 所有 stage mixin 通过 ``from ._common import ...`` 共享这些定义
    - ``CamValidationResult`` 是阶段 7 的公开数据类，需保持向后兼容
      （原 ``from app.cam_validation.pipeline import CamValidationResult``
       仍可用，由 pipeline.py re-export shim 保障）
    - stage mixin 之间不直接继承，通过 ``CamValidationPipeline`` 多重继承组合

项目记忆硬约束：
    - cam_validation_required 始终 True，不可由环境变量关闭
    - SUCCEEDED 状态禁止删除（cam_report.json 是链路最终产物，供审计追溯）
    - 系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.cam_validation.cam_disclaimer import CamDisclaimer

logger = logging.getLogger(__name__)


# 常量

# 默认毛坯尺寸（mm）：阶段 6 GCodeReport 未携带 stock_length/width/height，
# 阶段 7 使用合理默认值（与阶段 6 默认对齐）。
# stock_height = stock_top_z（保证 StockModel 一致性，避免触发警告）
_DEFAULT_STOCK_LENGTH_MM: float = 200.0
_DEFAULT_STOCK_WIDTH_MM: float = 150.0

# CAM 校验模式（阶段 7 仅支持 3-axis；5-axis 需 CamAdapter 调用 NX/PowerMill）
_DEFAULT_MODE: str = "3axis"


# CamValidationResult：编排器返回值


@dataclass
class CamValidationResult:
    """CAM 校验任务结果摘要，用于 API 响应。

    封装任务状态 + 双层校验统计 + 导出产物路径 + 错误信息 + disclaimer。
    与阶段 6 GCodeGenerationResult 结构对齐，字段调整为阶段 7 语义。

    Attributes:
        task_id: 任务 ID（前缀 "cam_"）
        status: 任务状态（pending / running / validated / reviewed /
            succeeded / failed / timeout / cancelled）
        source_gcode_report_path: 阶段 6 report.json 路径（追溯上游）
        source_gcode_file_path: 阶段 6 G 代码文件路径
        controller_type: 目标控制器类型
        material_name: 材料名（继承阶段 6）
        gcode_total_lines: G 代码总行数（继承阶段 6）
        total_features: 总特征数
        passed_features: 双层校验均通过的特征数
        failed_features: 任一层校验失败的特征数
        pending_calibration: 是否含 HRC52 待校准材料（继承阶段 5/6）
        prediction_method: 阶段 5 预测方法（继承阶段 6）
        cam_backend_requested: 请求的 CAM 后端
        cam_backend_used: 实际使用的 CAM 后端（可能因降级与 requested 不同）
        cam_backend_fallback_reason: 降级原因
        cam_report_path: 导出的 cam_report.json 路径（阶段 7 最终产物）
        internal_report_path: 导出的 internal_report.json 路径（调试细节）
        error_message: 错误信息（FAILED 时填充）
        disclaimer: CAM 校验精度告知
    """

    task_id: str
    status: str
    source_gcode_report_path: str
    source_gcode_file_path: str
    controller_type: str
    material_name: str
    gcode_total_lines: int
    total_features: int
    passed_features: int
    failed_features: int
    pending_calibration: bool
    prediction_method: str
    cam_backend_requested: str
    cam_backend_used: str
    cam_backend_fallback_reason: str
    cam_report_path: str | None = None
    internal_report_path: str | None = None
    error_message: str | None = None
    disclaimer: CamDisclaimer | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，供 API 响应。"""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source_gcode_report_path": self.source_gcode_report_path,
            "source_gcode_file_path": self.source_gcode_file_path,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "gcode_total_lines": self.gcode_total_lines,
            "total_features": self.total_features,
            "passed_features": self.passed_features,
            "failed_features": self.failed_features,
            "pending_calibration": self.pending_calibration,
            "prediction_method": self.prediction_method,
            "cam_backend_requested": self.cam_backend_requested,
            "cam_backend_used": self.cam_backend_used,
            "cam_backend_fallback_reason": self.cam_backend_fallback_reason,
            "cam_report_path": self.cam_report_path,
            "internal_report_path": self.internal_report_path,
            "error_message": self.error_message,
            "disclaimer": (self.disclaimer.to_dict() if self.disclaimer else None),
        }
