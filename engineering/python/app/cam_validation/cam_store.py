"""CAM 校验任务存储 + 状态机 + 审核枚举（阶段 7）。

数据流：
    阶段 6 输出的 G 代码文件 + report.json
        → GCodeLoader 加载 G 代码文本 + feature_results（含 line_range）
        → InternalValidator 复用 CollisionDetector 执行内部预校验
            + 按 block_number 归因到 feature_results.line_range
        → CamAdapter 调用 CAM 软件二次校验（5 后端策略）
        → 工程师审核每个特征校验结果（pending → confirmed / rejected / edited）
        → confirm_task → SUCCEEDED
        → 导出 cam_report.json + internal_report.json

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动 CAM 校验器」。
    内部预校验（CollisionDetector）是 AABB 快速预筛，不可替代 CAM 软件二次校验。
    cam_validation_required 始终 True，不可由环境变量关闭。
    SUCCEEDED 状态禁止删除（链路最终产物，需保留供审计追溯）。

状态机（与阶段 5/6 对齐，单轮审核）：
    PENDING → RUNNING → VALIDATED → REVIEWED → SUCCEEDED
                ↘ FAILED
                ↘ TIMEOUT
                ↘ CANCELLED

    - PENDING   : 任务已创建，等待执行
    - RUNNING   : 正在加载 G 代码 + 调用 CollisionDetector + CAM 软件二次校验
    - VALIDATED : 双层校验完成，等待工程师审核
    - REVIEWED  : 工程师已审核全部特征校验结果
    - SUCCEEDED : CAM 校验报告已导出至 output_dir，**禁止删除**
    - FAILED    : 校验失败（G 代码加载失败 / CollisionDetector 抛错 / CAM 软件返回错误）
    - TIMEOUT   : 超过 task_timeout_seconds
    - CANCELLED : 用户主动取消

线程安全（项目记忆硬约束）：
    - CamTaskStore 使用 threading.Lock 保护 _tasks 字典
    - 审核操作使用独立的 _review_lock 防止并发审核冲突
    - 导出操作使用 _export_lock 防止文件写入竞争
    - CAM 软件调用使用 _cam_call_lock 防止 NX/PowerMill 并发实例崩溃
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.safe_errors import safe_error_message
from app.utils.task_store import InMemoryTaskStore


# 枚举：任务状态 / 审核状态


class CamValidationTaskStatus(str, Enum):
    """CAM 校验任务状态机（单轮审核，与阶段 5/6 一致）。

    状态转移图：
        PENDING → RUNNING → VALIDATED → REVIEWED → SUCCEEDED
                          ↘ FAILED
                          ↘ TIMEOUT
                          ↘ CANCELLED

    与阶段 5/6 区别：阶段 7 输出 VALIDATED 而非 GENERATED / PREDICTED
    （语义对齐「双层校验完成等待审核」）。

    - PENDING   : 任务已创建，等待触发执行
    - RUNNING   : 正在加载 G 代码 + 调用 InternalValidator + CamAdapter
    - VALIDATED : 内部预校验 + CAM 软件二次校验均完成，等待工程师审核
    - REVIEWED  : 工程师已审核全部特征校验结果
    - SUCCEEDED : CAM 校验报告已导出至 output_dir，**禁止删除**
    - FAILED    : 校验失败（G 代码加载失败 / CollisionDetector 抛错 / CAM 软件返回错误）
    - TIMEOUT   : 超过 task_timeout_seconds
    - CANCELLED : 用户主动取消
    """

    PENDING = "pending"
    RUNNING = "running"
    VALIDATED = "validated"
    REVIEWED = "reviewed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class CamReviewStatus(str, Enum):
    """工程师审核单个特征校验结果的状态（与阶段 6 GCodeReviewStatus 对齐）。

    - PENDING   : 待审核
    - CONFIRMED : 工程师确认该特征校验结果（双层校验均通过 / 已知警告可接受）
    - REJECTED  : 工程师拒绝该特征（需阶段 6 重新生成 G 代码）
    - EDITED    : 工程师编辑了校验参数（如调整 safe_z / 修改后端策略后重跑）
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EDITED = "edited"


# 常量

# 安全裕度比例（与阶段 5/6 对齐，仅用于 disclaimer 显示告知，不影响校验逻辑）
SAFETY_MARGIN_RATIO: float = 0.8

# HRC52 待校准材料集合（与阶段 5/6 对齐，阶段 7 仅继承不二次拟合）
PENDING_CALIBRATION_MATERIALS: frozenset[str] = frozenset(
    {
        "steel_hrc52",
        "hrc52",
        "hrc_52",
        "hardened_steel_hrc52",
    }
)

# 合法 CAM 后端（与 CamValidationConfig.default_cam_backend 校验逻辑对齐）
VALID_CAM_BACKENDS: frozenset[str] = frozenset(
    {
        "internal_only",
        "pycam",
        "nx_open",
        "powermill",
        "manual",
    }
)


# 异常类


class CamValidationError(Exception):
    """CAM 校验基础异常。"""


class GCodeReportLoadError(CamValidationError):
    """阶段 6 G 代码 report.json 加载失败。

    可能原因：
    - 文件不存在
    - JSON 格式错误
    - 必填字段缺失
    - task_status != SUCCEEDED（阶段 6 未审核通过）
    """


class InternalValidationError(CamValidationError):
    """InternalValidator 内部预校验异常。

    可能原因：
    - CollisionDetector 抛错
    - ToolpathParser 解析 G 代码失败
    - StockModel 构造失败
    """


class CamAdapterError(CamValidationError):
    """CAM 软件适配层异常。

    可能原因：
    - NX Open / PowerMill / PyCAM 不可用
    - subprocess 超时
    - CAM 软件返回非零退出码
    - 后端降级到 manual 时未抛错（生成校验清单）
    """


class VoxelValidationError(CamValidationError):
    """体素材料去除仿真校验异常（VoxelValidator）。

    可能原因：
    - ToolpathParser 解析 G 代码失败
    - 运动段数超过 voxel_max_segments 上限（fail-closed，拒绝部分仿真）
    - safe_z 不高于 stock_top_z
    - 切削内核执行异常
    """


class ReviewError(CamValidationError):
    """审核操作异常。

    可能原因：
    - 任务状态非 VALIDATED（无法审核）
    - SUCCEEDED 状态禁止删除
    - 审核字段非法
    """


class CamValidationPipelineError(CamValidationError):
    """流水线编排异常。"""


# FeatureValidationResult：单个特征的校验结果


@dataclass
class FeatureValidationResult:
    """单个特征的 CAM 校验结果。

    封装两层校验对该特征的结论：
    - internal_check_passed：InternalValidator（CollisionDetector）的 AABB 预校验
    - cam_check_passed：CamAdapter（NX/PowerMill/PyCAM/manual）的完整刀轨仿真

    所有数值字段来自阶段 6 feature_results，阶段 7 不重新计算，仅校验。

    Attributes:
        feature_id: 特征 ID（与阶段 6 feature_results.feature_id 对齐）
        feature_type: 特征类型（plane / cylinder / hole / boss）
        line_range: 在 G 代码中的行号区间 [start, end]（来自阶段 6）
        internal_check_passed: InternalValidator 是否通过（无碰撞事件）
        internal_events: 碰撞事件列表（来自 CollisionDetector，空列表表示无碰撞）
        cam_check_passed: CamAdapter 是否通过（CAM 软件二次校验）
        cam_messages: CAM 软件返回的消息列表（警告 / 错误 / 提示）
        cam_backend_used: 实际使用的 CAM 后端（可能因降级与 default 不同）
        review_status: 工程师审核状态（pending / confirmed / rejected / edited）
        edited_params: 工程师编辑的参数（如调整 safe_z 后重跑的记录）
    """

    feature_id: str
    feature_type: str  # plane / cylinder / hole / boss
    line_range: tuple[int, int] = (0, 0)  # 来自阶段 6 feature_results.line_range
    # InternalValidator 结果
    internal_check_passed: bool = True
    internal_events: list[dict[str, Any]] = field(default_factory=list)
    # VoxelValidator 结果（体素材料去除仿真，闭环强制层）
    voxel_check_passed: bool = True
    voxel_collision_blocks: list[int] = field(default_factory=list)
    # CamAdapter 结果
    cam_check_passed: bool = True
    cam_messages: list[str] = field(default_factory=list)
    cam_backend_used: str = "internal_only"
    # 审核状态
    review_status: str = CamReviewStatus.PENDING.value
    edited_params: dict[str, Any] = field(default_factory=dict)
    # 阶段 6 上下文（用于审核时回溯）
    spindle_rpm: float = 0.0
    axial_depth_mm: float = 0.0
    limit_depth_mm: float = 0.0
    stable: bool = True
    safety_margin_ratio: float = 0.0
    warning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "line_range": list(self.line_range),
            "internal_check_passed": self.internal_check_passed,
            "internal_events": self.internal_events,
            "voxel_check_passed": self.voxel_check_passed,
            "voxel_collision_blocks": list(self.voxel_collision_blocks),
            "cam_check_passed": self.cam_check_passed,
            "cam_messages": self.cam_messages,
            "cam_backend_used": self.cam_backend_used,
            "review_status": self.review_status,
            "edited_params": self.edited_params,
            "spindle_rpm": round(self.spindle_rpm, 4),
            "axial_depth_mm": round(self.axial_depth_mm, 4),
            "limit_depth_mm": round(self.limit_depth_mm, 4),
            "stable": self.stable,
            "safety_margin_ratio": round(self.safety_margin_ratio, 4),
            "warning": self.warning,
        }

    @property
    def overall_passed(self) -> bool:
        """综合判定：内部预校验 + 体素仿真 + CAM 软件二次校验均通过。

        工程师审核时以此为基础，但最终是否上机仍由工程师决定。
        manual 后端 cam_check_passed=True 仅表示「校验清单已生成，等待工程师回填」，
        实际是否通过需工程师审核 manual_checklist 字段后手动设置 review_status。
        """
        return self.internal_check_passed and self.voxel_check_passed and self.cam_check_passed


# CamValidationTask：CAM 校验任务


@dataclass
class CamValidationTask:
    """CAM 校验任务。

    封装从阶段 6 G 代码 + report.json 到 CAM 校验报告的完整流程状态。

    Attributes:
        task_id: 任务 ID（前缀 "cam_" + uuid4）
        source_gcode_report_path: 阶段 6 report.json 路径
        source_gcode_file_path: 阶段 6 G 代码文件路径
        controller_type: 控制器类型（来自阶段 6）
        material_name: 材料名（来自阶段 6）
        safe_z: 安全 Z 高度（来自阶段 6）
        stock_top_z: 毛坯顶面 Z（来自阶段 6）
        status: 任务状态（见 CamValidationTaskStatus）
        feature_validation_results: 每个特征的校验结果列表
        gcode_total_lines: G 代码总行数（来自阶段 6）
        cam_backend_requested: 请求的 CAM 后端（来自 CamValidationConfig.default_cam_backend）
        cam_backend_used: 实际使用的 CAM 后端（可能因降级与 requested 不同）
        cam_backend_fallback_reason: 降级原因（如 "NX Open executable not configured"）
        cam_report_path: 导出的 cam_report.json 路径（阶段 7 最终产物）
        internal_report_path: 导出的 internal_report.json 路径（调试细节，供前端可视化）
        cam_validation_required: 始终 True（项目记忆硬约束）
        workspace_dir: 任务工作目录
        error_message: 错误信息（FAILED 时填充）
        started_at: 任务开始时间戳
        completed_at: 任务完成时间戳
        reviewed_by: 审核人
        reviewed_at: 审核时间戳
        warnings: 警告列表
        errors: 错误列表
        total_features: 总特征数
        passed_features: 双层校验均通过的特征数
        failed_features: 任一层校验失败的特征数
        pending_calibration: 是否含 HRC52 待校准材料（继承阶段 5/6）
        prediction_method: 阶段 5 的预测方法（继承阶段 6）
    """

    task_id: str
    source_gcode_report_path: str = ""
    source_gcode_file_path: str = ""
    controller_type: str = "fanuc_0i"
    material_name: str = "45#钢"
    safe_z: float = 80.0
    stock_top_z: float = 50.0
    status: str = CamValidationTaskStatus.PENDING.value
    feature_validation_results: list[FeatureValidationResult] = field(default_factory=list)
    gcode_total_lines: int = 0
    # CAM 后端策略
    cam_backend_requested: str = "internal_only"
    cam_backend_used: str = "internal_only"
    cam_backend_fallback_reason: str = ""
    # 导出产物
    cam_report_path: str = ""
    internal_report_path: str = ""
    # 体素材料去除仿真（闭环强制层；None = 本任务产生于闭环上线前/未执行）
    voxel_check_passed: bool | None = None
    voxel_collision_count: int = 0
    voxel_engine: str = ""
    # 体素仿真所用刀具参数及其来源（actual=任务提供 / report=阶段6携带 /
    # config_default=配置默认——默认值与实际装刀可能不符，需工程师确认）
    voxel_tool_diameter_mm: float = 0.0
    voxel_tool_source: str = ""
    # 实际装刀参数（可选，创建任务时由工程师/刀具表显式提供）
    tool_diameter_mm: float | None = None
    tool_type: str = ""
    # 程序级运动学校验（行程/主轴/进给/快移扎刀/装刀；None = 未执行/旧任务）
    kinematics_check_passed: bool | None = None
    kinematics_error_count: int = 0
    # 项目记忆硬约束
    cam_validation_required: bool = True
    # 流程元数据
    workspace_dir: str = ""
    error_message: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    reviewed_by: str = ""
    reviewed_at: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # 统计
    total_features: int = 0
    passed_features: int = 0
    failed_features: int = 0
    # 继承自阶段 5/6
    pending_calibration: bool = False
    prediction_method: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source_gcode_report_path": self.source_gcode_report_path,
            "source_gcode_file_path": self.source_gcode_file_path,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "safe_z": self.safe_z,
            "stock_top_z": self.stock_top_z,
            "status": self.status,
            "feature_validation_results": [r.to_dict() for r in self.feature_validation_results],
            "gcode_total_lines": self.gcode_total_lines,
            "cam_backend_requested": self.cam_backend_requested,
            "cam_backend_used": self.cam_backend_used,
            "cam_backend_fallback_reason": self.cam_backend_fallback_reason,
            "cam_report_path": self.cam_report_path,
            "internal_report_path": self.internal_report_path,
            "voxel_check_passed": self.voxel_check_passed,
            "voxel_collision_count": self.voxel_collision_count,
            "voxel_engine": self.voxel_engine,
            "voxel_tool_diameter_mm": self.voxel_tool_diameter_mm,
            "voxel_tool_source": self.voxel_tool_source,
            "tool_diameter_mm": self.tool_diameter_mm,
            "tool_type": self.tool_type,
            "kinematics_check_passed": self.kinematics_check_passed,
            "kinematics_error_count": self.kinematics_error_count,
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
            "passed_features": self.passed_features,
            "failed_features": self.failed_features,
            "pending_calibration": self.pending_calibration,
            "prediction_method": self.prediction_method,
        }


def generate_task_id() -> str:
    """生成 CAM 校验任务 ID。

    格式：cam_<uuid4>（与阶段 5 ch_ / 阶段 6 gc_ 前缀对齐）
    """
    return f"cam_{uuid.uuid4()}"


# CamTaskStore：线程安全的任务存储


class CamTaskStore(InMemoryTaskStore):
    """CAM 校验任务存储。

    公共实现见 :class:`app.utils.task_store.InMemoryTaskStore`；
    额外持有 ``_cam_call_lock`` 防止 NX/PowerMill 并发实例崩溃，
    并通过 ``safe_error_message`` 脱敏任务异常消息。
    """

    def __init__(self) -> None:
        super().__init__()
        if not hasattr(self, "_cam_call_lock"):
            self._cam_call_lock = threading.Lock()

    def _task_error(self, message: str) -> Exception:
        return CamValidationError(safe_error_message(ValueError(message)))

    def _review_error(self, message: str) -> Exception:
        return ReviewError(message)

    def _succeeded_delete_message(self, task_id: str) -> str:
        return f"任务 {task_id} 已 SUCCEEDED，禁止删除（cam_report.json 是阶段 7 最终产物，需保留供审计追溯）"

    @property
    def cam_call_lock(self) -> threading.Lock:
        """CAM 软件调用锁（NX Open / PowerMill subprocess 串行化）。"""
        return self._cam_call_lock


def get_task_store() -> CamTaskStore:
    """获取 CamTaskStore 单例。"""
    return CamTaskStore()


# 辅助函数


def is_valid_cam_backend(backend: str) -> bool:
    """检查 CAM 后端是否合法。

    与 CamValidationConfig.default_cam_backend 校验逻辑对齐：
    合法值 = {internal_only, pycam, nx_open, powermill, manual}
    """
    return backend in VALID_CAM_BACKENDS


__all__ = [
    # 枚举
    "CamValidationTaskStatus",
    "CamReviewStatus",
    # 常量
    "SAFETY_MARGIN_RATIO",
    "PENDING_CALIBRATION_MATERIALS",
    "VALID_CAM_BACKENDS",
    # 异常
    "CamValidationError",
    "GCodeReportLoadError",
    "InternalValidationError",
    "CamAdapterError",
    "VoxelValidationError",
    "ReviewError",
    "CamValidationPipelineError",
    # dataclass
    "FeatureValidationResult",
    "CamValidationTask",
    # 工具函数
    "generate_task_id",
    "get_task_store",
    "is_valid_cam_backend",
    "CamTaskStore",
]
