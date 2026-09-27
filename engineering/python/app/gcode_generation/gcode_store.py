"""G 代码生成接入模块：任务存储 + 状态机 + 审核枚举（阶段 6）。

数据流：
    阶段 5 输出的 ChatterReport JSON（含每条特征的 limit_depth_mm / axial_depth_mm / stable）
        + 阶段 3 输出的 OperationPlan JSON
        → GeneratorAdapter 封装现有 GCodeGenerator.generate() 生成基础 G 代码
        → 遍历 ChatterReport.feature_results 计算安全裕度（SAFETY_MARGIN_RATIO=0.8）
        → stable == False 的特征禁止生成 G 代码（强制工程师审核降低切深）
        → 工程师审核每个特征的 G 代码段（confirmed / rejected / edited）
        → 导出 G 代码文件（.nc / .mpf / .h）+ 审核记录 JSON（供阶段 7 CAM 校验使用）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动 G 代码生成器」。
    G 代码生成基于现有 app.postprocessor 包 + app.process_planning.gcode_generator.GCodeGenerator，
    安全裕度标注由本模块的 GeneratorAdapter 承担，不修改现有 G 代码生成核心逻辑。
    生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机，
    系统绝不直接接口 CNC 控制器。

精度继承链：
    阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5 → 阶段 6（本模块）
    本模块不引入新的精度档位，全程继承上游告知。
    精度档位影响告知文本：HRC52 + pending_calibration 时强制标注待校准。

工程优先策略（项目记忆硬约束：工程生产优先于学术价值）：
    - 复用现有 GCodeGenerator（212 个测试用例覆盖），不重写
    - SAFETY_MARGIN_RATIO=0.8，实际切深超过极限切深 80% 时发出警告
    - stable == False 的特征禁止生成 G 代码，强制工程师审核
    - cam_validation_required 始终 True，不可由环境变量关闭
    - SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
    - allow_delete_succeeded 强制 False（不可由环境变量开启）
    - 生成的 G 代码必须经 CAM 软件二次校验，绝不直接接口 CNC 控制器
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.utils.task_store import InMemoryTaskStore


# 枚举：任务状态 / 审核状态


class GCodeGenerationTaskStatus(str, Enum):
    """G 代码生成任务状态机（单轮审核，与阶段 5 一致）。

    状态转移图：
        PENDING → RUNNING → GENERATED → REVIEWED → SUCCEEDED
                          ↘ FAILED
                          ↘ TIMEOUT
                          ↘ CANCELLED

    - PENDING   : 任务已创建，等待触发执行
    - RUNNING   : 正在加载 ChatterReport + OperationPlan + 调用 GCodeGenerator
    - GENERATED : G 代码已生成，等待工程师审核
    - REVIEWED  : 工程师已审核全部特征 G 代码段
    - SUCCEEDED : G 代码已导出至 output_dir，可供阶段 7 CAM 校验使用
    - FAILED    : 执行失败（ChatterReport 加载失败 / GCodeGenerator 抛错 / 语法校验失败）
    - TIMEOUT   : 超过 task_timeout_seconds
    - CANCELLED : 用户主动取消

    与阶段 5 区别：阶段 6 输出 GENERATED 而非 PREDICTED（语义对齐）。
    """

    PENDING = "pending"
    RUNNING = "running"
    GENERATED = "generated"
    REVIEWED = "reviewed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class GCodeReviewStatus(str, Enum):
    """工程师审核单个特征 G 代码段的状态。

    - PENDING   : 待审核
    - CONFIRMED : 工程师确认该特征 G 代码段（含安全裕度判断）
    - REJECTED  : 工程师拒绝该特征（不进入最终 G 代码）
    - EDITED    : 工程师编辑了参数（如调整 axial_depth 后重新生成）
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EDITED = "edited"


# 安全裕度常量（与阶段 5 predictor_adapter.py 对齐）

# 安全裕度比例：实际切深应 ≤ 极限切深 × 0.8
# 超过此比例时在 warnings 中标注（与阶段 5 predictor_adapter.SAFETY_MARGIN_RATIO 一致）
SAFETY_MARGIN_RATIO: float = 0.8

# HRC52 待校准材料集合（与阶段 5 PENDING_CALIBRATION_MATERIALS 对齐）
PENDING_CALIBRATION_MATERIALS: frozenset[str] = frozenset(
    {
        "steel_hrc52",
        "hrc52",
        "hrc_52",
        "hardened_steel_hrc52",
    }
)


# 异常类


class GCodeGenerationError(Exception):
    """G 代码生成基础异常。"""


class ChatterReportLoadError(GCodeGenerationError):
    """阶段 5 ChatterReport 加载失败。

    可能原因：
    - 文件不存在
    - JSON 格式错误
    - 必填字段缺失
    - task_status != SUCCEEDED（阶段 5 未审核通过）
    """


class OperationPlanLoadError(GCodeGenerationError):
    """阶段 3 OperationPlan 加载失败。"""


class ReviewError(GCodeGenerationError):
    """审核操作异常。

    可能原因：
    - 任务状态非 GENERATED（无法审核）
    - SUCCEEDED 状态禁止删除
    - 审核字段非法
    """


class GCodeGenerationPipelineError(GCodeGenerationError):
    """流水线编排异常。"""


# FeatureGCodeResult：单个特征的 G 代码结果


@dataclass
class FeatureGCodeResult:
    """单个特征的 G 代码生成结果。

    所有数值单位：
    - axial_depth_mm: mm（实际切深，来自阶段 5 ChatterReport）
    - limit_depth_mm: mm（极限切深，来自阶段 5 ChatterReport）
    - safety_margin_ratio: 无量纲（axial / limit，应 ≤ 0.8）
    """

    feature_id: str
    feature_type: str  # plane / cylinder / hole / boss
    material_id: str
    spindle_rpm: float
    axial_depth_mm: float  # 实际切深（来自阶段 5）
    limit_depth_mm: float  # 极限切深（来自阶段 5）
    stable: bool  # 是否稳定（来自阶段 5）
    safety_margin_ratio: float  # axial / limit（limit > 0 时）
    gcode_lines: list[str] = field(default_factory=list)
    line_range: tuple[int, int] = (0, 0)  # 在最终程序中的行号范围 [start, end]
    warning: str = ""  # 安全裕度警告（若 axial > 0.8 × limit）
    review_status: str = GCodeReviewStatus.PENDING.value
    edited_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "material_id": self.material_id,
            "spindle_rpm": self.spindle_rpm,
            "axial_depth_mm": round(self.axial_depth_mm, 4),
            "limit_depth_mm": round(self.limit_depth_mm, 4),
            "stable": self.stable,
            "safety_margin_ratio": round(self.safety_margin_ratio, 4),
            "gcode_lines": self.gcode_lines,
            "line_range": list(self.line_range),
            "warning": self.warning,
            "review_status": self.review_status,
            "edited_params": self.edited_params,
        }

    @property
    def effective_result(self) -> dict[str, float]:
        """获取生效结果（edited 时用 edited_params 覆盖，否则用预测值）。

        与阶段 5 FeatureChatterResult.effective_result() 契约一致：
        - review_status == edited 且 edited_params 非空 → 用编辑值
        - 否则 → 用原值副本

        可编辑字段：axial_depth_mm / limit_depth_mm / stable
        """
        base = {
            "axial_depth_mm": self.axial_depth_mm,
            "limit_depth_mm": self.limit_depth_mm,
            "stable": 1.0 if self.stable else 0.0,
        }
        if self.review_status == GCodeReviewStatus.EDITED.value and self.edited_params:
            result = dict(base)
            if "axial_depth_mm" in self.edited_params:
                result["axial_depth_mm"] = float(self.edited_params["axial_depth_mm"])
            if "limit_depth_mm" in self.edited_params:
                result["limit_depth_mm"] = float(self.edited_params["limit_depth_mm"])
            if "stable" in self.edited_params:
                result["stable"] = 1.0 if self.edited_params["stable"] else 0.0
            return result
        return base


# GCodeGenerationTask：G 代码生成任务


@dataclass
class GCodeGenerationTask:
    """G 代码生成任务。

    封装从 ChatterReport + OperationPlan 到 G 代码文件的完整流程状态。
    """

    task_id: str
    source_chatter_report_path: str
    source_operation_plan_path: str
    controller_type: str = "fanuc_0i"
    material_name: str = "45#钢"
    program_number: int = 1000
    safe_z: float = 80.0
    stock_top_z: float = 50.0
    status: str = GCodeGenerationTaskStatus.PENDING.value
    feature_gcode_results: list[FeatureGCodeResult] = field(default_factory=list)
    gcode_text: str = ""
    gcode_report_path: str = ""  # 输出给阶段 7 的 JSON 路径
    gcode_file_path: str = ""  # 导出的 G 代码文件路径
    cam_validation_required: bool = True  # 项目记忆硬约束：始终 True
    workspace_dir: str = ""
    error_message: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # 生成方法统计
    total_features: int = 0
    stable_features: int = 0
    unstable_features: int = 0  # stable == False 的特征数
    pending_calibration: bool = False  # 是否含 HRC52 待校准材料
    prediction_method: str = ""  # 阶段 5 的预测方法（analytical / neural_network / mixed）
    # 匹配刀具共识直径（mm，继承阶段 3 OperationPlan；None = 上游未携带，
    # 阶段 7 体素仿真回退配置默认值并如实标注 tool_source）
    tool_diameter_mm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "task_id": self.task_id,
            "source_chatter_report_path": self.source_chatter_report_path,
            "source_operation_plan_path": self.source_operation_plan_path,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "program_number": self.program_number,
            "safe_z": self.safe_z,
            "stock_top_z": self.stock_top_z,
            "status": self.status,
            "feature_gcode_results": [r.to_dict() for r in self.feature_gcode_results],
            "gcode_text": self.gcode_text,
            "gcode_report_path": self.gcode_report_path,
            "gcode_file_path": self.gcode_file_path,
            "cam_validation_required": self.cam_validation_required,
            "workspace_dir": self.workspace_dir,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at,
            "warnings": self.warnings,
            "errors": self.errors,
            "total_features": self.total_features,
            "stable_features": self.stable_features,
            "unstable_features": self.unstable_features,
            "pending_calibration": self.pending_calibration,
            "prediction_method": self.prediction_method,
        }
        if self.tool_diameter_mm is not None:
            result["tool_diameter_mm"] = self.tool_diameter_mm
        return result


def generate_task_id() -> str:
    """生成 G 代码生成任务 ID。

    格式：gc_<uuid4>（与阶段 5 ch_ 前缀对齐）
    """
    return f"gc_{uuid.uuid4()}"


# TaskStore：线程安全的任务存储


class TaskStore(InMemoryTaskStore):
    """G 代码生成任务存储。

    公共实现见 :class:`app.utils.task_store.InMemoryTaskStore`；
    本类仅绑定 G 代码生成阶段的异常类型与删除保护文案。
    """

    def _task_error(self, message: str) -> Exception:
        return GCodeGenerationError(message)

    def _review_error(self, message: str) -> Exception:
        return ReviewError(message)

    def _succeeded_delete_message(self, task_id: str) -> str:
        return f"任务 {task_id} 已 SUCCEEDED，禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）"


def get_task_store() -> TaskStore:
    """获取 TaskStore 单例。"""
    return TaskStore()


# 导出文件扩展名映射


CONTROLLER_FILE_EXTENSIONS: dict[str, str] = {
    "fanuc_0i": ".nc",
    "siemens_840d": ".mpf",
    "heidenhain_tnc": ".h",
    "xmachine_xm100": ".nc",
}

DEFAULT_FILE_EXTENSION = ".nc"


def get_file_extension(controller_type: str) -> str:
    """获取控制器对应的 G 代码文件扩展名。"""
    return CONTROLLER_FILE_EXTENSIONS.get(controller_type, DEFAULT_FILE_EXTENSION)


__all__ = [
    # 枚举
    "GCodeGenerationTaskStatus",
    "GCodeReviewStatus",
    # 常量
    "SAFETY_MARGIN_RATIO",
    "PENDING_CALIBRATION_MATERIALS",
    "CONTROLLER_FILE_EXTENSIONS",
    "DEFAULT_FILE_EXTENSION",
    # 异常
    "GCodeGenerationError",
    "ChatterReportLoadError",
    "OperationPlanLoadError",
    "ReviewError",
    "GCodeGenerationPipelineError",
    # dataclass
    "FeatureGCodeResult",
    "GCodeGenerationTask",
    # 工具函数
    "generate_task_id",
    "get_task_store",
    "get_file_extension",
    "TaskStore",
]
