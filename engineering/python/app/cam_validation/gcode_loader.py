"""G 代码加载器（阶段 7 输入侧）。

职责
====
消费阶段 6（ADR-014 GCodeGenerationPipeline）导出的两类产物：
    1. 审核记录 JSON：outputs/gcode/{gc_task_id}/{gc_task_id}.report.json
    2. G 代码文件：outputs/gcode/{gc_task_id}/{gc_task_id}.nc / .mpf / .h

将阶段 6 的产物加载为 GCodeLoadResult，供 InternalValidator + CamAdapter 消费。

阶段 6 report.json 字段（来自 GCodeGenerationTask.to_dict() + 导出时追加 task_status）：
    - task_id                  : 阶段 6 任务 ID（追溯用）
    - task_status              : 必须为 "succeeded"（阶段 6 未审核通过则拒绝加载）
    - exported_at              : 阶段 6 导出时间戳
    - reviewer                 : 阶段 6 审核人
    - controller_type          : 目标控制器
    - material_name            : 材料名
    - safe_z / stock_top_z     : 安全 Z / 毛坯顶面 Z
    - gcode_file_path          : G 代码文件路径（绝对或相对 PROJECT_ROOT）
    - gcode_total_lines        : G 代码总行数
    - feature_results          : 每个特征的 G 代码段信息（含 line_range）
    - cam_validation_required  : 始终 True（项目记忆硬约束）
    - prediction_method        : 阶段 5 预测方法
    - pending_calibration      : 是否含 HRC52 待校准材料
    - source_chatter_report_path / source_operation_plan_path : 上游追溯

异常
====
- GCodeReportLoadError：report.json 不存在 / JSON 解析失败 / 必填字段缺失 /
  task_status != "succeeded" / G 代码文件不存在 / G 代码读取失败
  （复用 cam_store.GCodeReportLoadError，不重复定义）

工程优先策略（项目记忆硬约束）：
- 阶段 7 仅消费阶段 6 SUCCEEDED 任务的产物
- G 代码文件路径解析：若为相对路径，相对 PROJECT_ROOT 解析
- feature_results.line_range 在 JSON 中是 list [start, end]，加载时转为 tuple
- cam_validation_required 始终 True，加载后强制断言（防御性编程）
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any

from app.cam_validation.cam_store import GCodeReportLoadError
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)


# 必填字段集合（阶段 6 report.json）


REQUIRED_GCODE_REPORT_FIELDS: frozenset[str] = frozenset(
    {
        "task_id",
        "task_status",
        "controller_type",
        "material_name",
        "safe_z",
        "stock_top_z",
        "gcode_file_path",
        "feature_results",
        "cam_validation_required",
        "prediction_method",
    }
)


# GCodeLoadResult dataclass


@dataclass
class GCodeLoadResult:
    """阶段 6 G 代码加载结果。

    封装从阶段 6 report.json + G 代码文件加载的全部上下文，
    供 InternalValidator（CollisionDetector）+ CamAdapter 消费。

    Attributes:
        task_id: 阶段 6 G 代码任务 ID（用于追溯）
        gcode_text: G 代码文本（用于 ToolpathParser.parse_gcode()）
        feature_results: 每个特征的 G 代码段信息（来自阶段 6，含 line_range）
        controller_type: 目标控制器（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）
        material_name: 材料名（继承阶段 4/5/6）
        safe_z: 安全 Z 高度（mm）
        stock_top_z: 毛坯顶面 Z（mm）
        prediction_method: 阶段 5 预测方法（analytical / neural_network / mixed）
        pending_calibration: 是否含 HRC52 待校准材料
        gcode_file_path: G 代码文件绝对路径
        gcode_total_lines: G 代码总行数
        cam_validation_required: 始终 True（项目记忆硬约束）
        source_chatter_report_path: 阶段 5 ChatterReport 路径（追溯用）
        source_operation_plan_path: 阶段 3 OperationPlan 路径（追溯用）
        reviewer: 阶段 6 审核人
        exported_at: 阶段 6 导出时间戳
        tool_diameter_mm: 阶段 6 携带的刀具直径（mm，可选；缺省 None 表示
            report 未携带——体素仿真将回退配置默认值并如实标注来源）
        tool_type: 阶段 6 携带的刀具类型（可选）
        load_warnings: 加载过程中的非致命警告（如 line_range 转换异常等）
    """

    task_id: str
    gcode_text: str
    feature_results: list[dict[str, Any]]
    controller_type: str
    material_name: str
    safe_z: float
    stock_top_z: float
    prediction_method: str
    pending_calibration: bool
    gcode_file_path: str
    gcode_total_lines: int
    cam_validation_required: bool
    source_chatter_report_path: str = ""
    source_operation_plan_path: str = ""
    reviewer: str = ""
    exported_at: float = 0.0
    tool_diameter_mm: float | None = None
    tool_type: str = ""
    load_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "gcode_text_length": len(self.gcode_text),
            "feature_results": self.feature_results,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "safe_z": self.safe_z,
            "stock_top_z": self.stock_top_z,
            "prediction_method": self.prediction_method,
            "pending_calibration": self.pending_calibration,
            "gcode_file_path": self.gcode_file_path,
            "gcode_total_lines": self.gcode_total_lines,
            "cam_validation_required": self.cam_validation_required,
            "source_chatter_report_path": self.source_chatter_report_path,
            "source_operation_plan_path": self.source_operation_plan_path,
            "reviewer": self.reviewer,
            "exported_at": self.exported_at,
            "load_warnings": self.load_warnings,
        }


# GCodeLoader 类


class GCodeLoader:
    """阶段 7 G 代码加载器。

    工程优先策略（项目记忆硬约束）：
    - 仅消费阶段 6 SUCCEEDED 任务的产物
    - G 代码文件路径解析：若为相对路径，相对 PROJECT_ROOT 解析
    - feature_results.line_range 在 JSON 中是 list，加载时转为 tuple
    - cam_validation_required 始终 True，加载后强制断言
    - 不修改阶段 6 产物文件，仅读取
    """

    def __init__(self, project_root: str | None = None) -> None:
        """初始化 G 代码加载器。

        Args:
            project_root: 项目根目录（用于解析相对路径的 G 代码文件路径）。
                         若为 None，则从 app.config 自动推导。
        """
        if project_root is None:
            # 从 app.config 推导 PROJECT_ROOT（与 config.__init__.PROJECT_ROOT 一致）
            from app.config import PROJECT_ROOT

            self._project_root = PROJECT_ROOT
        else:
            self._project_root = project_root
        logger.debug("GCodeLoader initialized (project_root=%s)", self._project_root)

    def load_from_report(self, report_path: str) -> GCodeLoadResult:
        """从阶段 6 report.json 加载 G 代码 + 特征信息。

        Args:
            report_path: 阶段 6 report.json 路径（绝对或相对 PROJECT_ROOT）

        Returns:
            GCodeLoadResult

        Raises:
            GCodeReportLoadError: 文件不存在 / JSON 解析失败 / 必填字段缺失 /
                                 task_status != "succeeded" / G 代码文件不存在
        """
        load_warnings: list[str] = []

        # 1. 读取 report.json
        report_full_path = self._resolve_path(report_path)
        if not os.path.isfile(report_full_path):
            raise GCodeReportLoadError(safe_error_message(ValueError(f"阶段 6 report.json 不存在: {report_path}")))
        try:
            with open(report_full_path, "r", encoding="utf-8") as f:
                report_data: dict[str, Any] = json.load(f)
        except json.JSONDecodeError as e:
            raise GCodeReportLoadError(f"阶段 6 report.json 解析失败: {e}") from e
        except OSError as e:
            raise GCodeReportLoadError(safe_error_message(ValueError(f"阶段 6 report.json 读取失败: {e}"))) from e

        logger.debug(
            "GCodeLoader: report.json loaded (path=%s, task_id=%s)",
            report_full_path,
            report_data.get("task_id", "<missing>"),
        )

        # 2. 校验必填字段
        missing_fields = REQUIRED_GCODE_REPORT_FIELDS - set(report_data.keys())
        if missing_fields:
            missing_str = ", ".join(sorted(missing_fields))
            raise GCodeReportLoadError(f"阶段 6 report.json 必填字段缺失: {missing_str}")

        # 3. 校验 task_status == "succeeded"
        task_status = report_data["task_status"]
        if task_status != "succeeded":
            raise GCodeReportLoadError(
                f"阶段 6 任务未审核通过（task_status={task_status}），请先在阶段 6 完成审核并导出 SUCCEEDED 产物"
            )

        # 4. 校验 cam_validation_required == True（项目记忆硬约束）
        cam_validation_required = bool(report_data["cam_validation_required"])
        if not cam_validation_required:
            # 项目记忆硬约束：cam_validation_required 始终 True
            # 阶段 6 report.json 中此字段应为 True，若为 False 则视为数据损坏
            logger.warning(
                "GCodeLoader: 阶段 6 report.json cam_validation_required=False，强制视为 True（项目记忆硬约束）"
            )
            load_warnings.append("阶段 6 report.json cam_validation_required=False，已强制视为 True")
            cam_validation_required = True

        # 5. 提取并校验 feature_results
        raw_feature_results = report_data["feature_results"]
        if not isinstance(raw_feature_results, list):
            raise GCodeReportLoadError(
                f"阶段 6 report.json feature_results 必须是列表，实际类型: {type(raw_feature_results).__name__}"
            )
        if not raw_feature_results:
            raise GCodeReportLoadError("阶段 6 report.json feature_results 为空，无法执行 CAM 校验")

        # 深拷贝 + line_range list tuple 转换
        feature_results: list[dict[str, Any]] = []
        for idx, fr in enumerate(raw_feature_results):
            if not isinstance(fr, dict):
                load_warnings.append(f"feature_results[{idx}] 不是 dict，已跳过")
                continue
            fr_copy = dict(fr)
            # line_range: list [start, end] tuple (start, end)
            lr = fr_copy.get("line_range")
            if isinstance(lr, (list, tuple)) and len(lr) == 2:
                fr_copy["line_range"] = (int(lr[0]), int(lr[1]))
            elif lr is not None:
                load_warnings.append(f"feature_results[{idx}] line_range 格式异常: {lr}，已置为 (0, 0)")
                fr_copy["line_range"] = (0, 0)
            else:
                fr_copy["line_range"] = (0, 0)
            feature_results.append(fr_copy)

        if not feature_results:
            raise GCodeReportLoadError("阶段 6 report.json feature_results 解析后为空")

        # 6. 读取 G 代码文件
        gcode_file_path = report_data["gcode_file_path"]
        gcode_text = self._load_gcode_text(gcode_file_path)

        # 6.5 可选刀具参数（阶段 6 新版 report.json 携带；旧版缺省 → None，
        # 体素仿真侧回退配置默认并标注 tool_source，工程师可见）
        tool_diameter_mm: float | None = None
        raw_diameter = report_data.get("tool_diameter_mm")
        if raw_diameter is not None:
            try:
                parsed = float(raw_diameter)
                if math.isfinite(parsed) and parsed > 0:
                    tool_diameter_mm = parsed
                else:
                    load_warnings.append(f"阶段 6 report.json tool_diameter_mm 非法（{raw_diameter}），已忽略")
            except (TypeError, ValueError):
                load_warnings.append(f"阶段 6 report.json tool_diameter_mm 非法（{raw_diameter}），已忽略")

        # 7. 构造 GCodeLoadResult
        result = GCodeLoadResult(
            task_id=str(report_data["task_id"]),
            gcode_text=gcode_text,
            feature_results=feature_results,
            controller_type=str(report_data["controller_type"]),
            material_name=str(report_data["material_name"]),
            safe_z=float(report_data["safe_z"]),
            stock_top_z=float(report_data["stock_top_z"]),
            prediction_method=str(report_data["prediction_method"]),
            pending_calibration=bool(report_data.get("pending_calibration", False)),
            gcode_file_path=gcode_file_path,
            gcode_total_lines=int(report_data.get("gcode_total_lines", 0)),
            cam_validation_required=cam_validation_required,
            source_chatter_report_path=str(report_data.get("source_chatter_report_path", "")),
            source_operation_plan_path=str(report_data.get("source_operation_plan_path", "")),
            reviewer=str(report_data.get("reviewer", "")),
            exported_at=float(report_data.get("exported_at", 0.0)),
            tool_diameter_mm=tool_diameter_mm,
            tool_type=str(report_data.get("tool_type", "")),
            load_warnings=load_warnings,
        )

        logger.info(
            "GCodeLoader: 加载成功 (task_id=%s, features=%d, gcode_lines=%d)",
            result.task_id,
            len(result.feature_results),
            result.gcode_total_lines,
        )
        return result

    def _resolve_path(self, path: str) -> str:
        """解析路径：若为相对路径，相对 PROJECT_ROOT 解析。

        安全：拒绝 ``..`` 路径遍历；绝对路径必须位于 PROJECT_ROOT 之下，
        防止通过 report.json 的 gcode_file_path 字段读取任意系统文件。

        Args:
            path: 绝对或相对路径

        Returns:
            绝对路径

        Raises:
            GCodeReportLoadError: 路径越界（不在 PROJECT_ROOT 之下）时抛出
        """
        project_root = os.path.abspath(self._project_root)
        # 解析最终绝对路径（处理 .. 和符号链接）
        if os.path.isabs(path):
            resolved = os.path.abspath(path)
        else:
            resolved = os.path.abspath(os.path.join(project_root, path))
        # 强制校验：最终路径必须位于 PROJECT_ROOT 之下
        if not resolved.startswith(project_root + os.sep) and resolved != project_root:
            raise GCodeReportLoadError(
                f"路径越界被拒绝（安全策略）：{path!r} 解析为 {resolved!r}，不在项目根目录 {project_root!r} 之下"
            )
        return resolved

    def _load_gcode_text(self, gcode_file_path: str) -> str:
        """读取 G 代码文件文本。

        Args:
            gcode_file_path: G 代码文件路径（绝对或相对 PROJECT_ROOT）

        Returns:
            G 代码文本

        Raises:
            GCodeReportLoadError: 文件不存在 / 读取失败
        """
        full_path = self._resolve_path(gcode_file_path)
        if not os.path.isfile(full_path):
            raise GCodeReportLoadError(safe_error_message(ValueError(f"阶段 6 G 代码文件不存在: {gcode_file_path}")))
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            raise GCodeReportLoadError(safe_error_message(ValueError(f"阶段 6 G 代码文件读取失败: {e}"))) from e


__all__ = [
    "REQUIRED_GCODE_REPORT_FIELDS",
    "GCodeLoadResult",
    "GCodeLoader",
]
