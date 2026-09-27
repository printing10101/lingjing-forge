"""CAM 校验接入模块（阶段 7）。

将阶段 6 输出的 G 代码文件 + 审核记录 JSON 执行「内部预校验 + CAM 软件二次校验」
双层校验，输出 CAM 校验报告 JSON，作为整条 7 阶段链路的最终产物（不触及物理机床）。

核心 pipeline：
    阶段 6 G 代码文件 + report.json
        → GCodeLoader.load() 加载 G 代码文本 + 特征行号区间 + 控制器类型
        → InternalValidator.validate() 复用 CollisionDetector 内部预校验（AABB 包围盒）
            + 按 block_number 归因到 feature_results.line_range
        → CamAdapter.validate() CAM 软件二次校验
            （internal_only / pycam / nx_open / powermill / manual 五后端策略）
        → CAM 软件不可用时自动降级到 manual（生成校验清单 + 工程师回填）
        → 工程师审核每个特征校验结果（pending → confirmed / rejected / edited）
        → confirm_task → SUCCEEDED
        → 导出 cam_report.json（最终结论）+ internal_report.json（调试细节，供前端可视化）

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动 CAM 校验器」。
    内部预校验（CollisionDetector）是 AABB 包围盒级别快速预筛，
        **不可替代** CAM 软件二次校验
        （无法检测刀轨几何精度 / 切削力 / 机床运动学 / 后处理器语法兼容性）。
    CAM 软件二次校验通过 subprocess 调用 NX Open / PowerMill / PyCAM，
        系统绝不直接接口 CNC 控制器。
    cam_validation_required 始终 True，不可由环境变量关闭。
    SUCCEEDED 状态禁止删除（链路最终产物，需保留供审计追溯）。
    HRC52 pending_calibration 由阶段 5 标注，阶段 7 仅继承并体现在校验告知文本中。

复用基础设施（不重写）：
    - app.simulation.collision_detector.CollisionDetector
        + CollisionReport / CollisionEvent / WorkspaceLimits / FiveAxisToolVector
    - app.simulation.toolpath_parser.ToolpathParser.parse_gcode() -> list[ToolpathSegment]
    - app.simulation.stock_model.StockModel

精度继承链（不引入新的精度档位）：
    阶段 1 image_to_3d.precision_tier
    → 阶段 2 feature_extraction
    → 阶段 3 parametric_geometry
    → 阶段 4 cutting_parameters
    → 阶段 5 chatter_prediction
    → 阶段 6 gcode_generation
    → 阶段 7 cam_validation（本模块全程继承上游告知）
    精度档位影响 disclaimer 文本告知，不改变 CAM 校验逻辑。

K_s 传递策略（项目记忆硬约束）：
    K_s（cutting_force_coeff）由阶段 4 → 阶段 5 → 阶段 6 传递到阶段 7。
    阶段 7 不涉及 K_s 计算，仅继承阶段 5 ChatterReport 中标注的 prediction_method
    与 pending_calibration 状态（用于 disclaimer 文本告知）。

线程安全（项目记忆硬约束）：
    - CamTaskStore（见 app.utils.task_store.InMemoryTaskStore）的任务字典、
      审核、导出操作分别由独立锁保护
    - CAM 软件调用使用 cam_call_lock 防止 NX/PowerMill 并发实例崩溃
"""

from __future__ import annotations

# 阶段 7 CAM 校验模块公开符号导出
# 导入顺序遵循依赖关系（与阶段 5/6 风格对齐）：
# cam_store（基础：异常 + dataclass + 枚举 + 常量 + 工具函数）
#  cam_disclaimer（依赖 config）
#  gcode_loader（依赖 cam_store 的 GCodeReportLoadError）
#  internal_validator（依赖 cam_store + simulation.collision_detector）
#  cam_adapter（依赖 cam_store 的 CamAdapterError）
#  pipeline（编排器，依赖上述全部）

from app.cam_validation.cam_store import (
    CamAdapterError,
    CamReviewStatus,
    CamTaskStore,
    CamValidationError,
    CamValidationPipelineError,
    CamValidationTask,
    CamValidationTaskStatus,
    FeatureValidationResult,
    GCodeReportLoadError,
    InternalValidationError,
    PENDING_CALIBRATION_MATERIALS,
    ReviewError,
    SAFETY_MARGIN_RATIO,
    VALID_CAM_BACKENDS,
    VoxelValidationError,
    generate_task_id,
    get_task_store,
    is_valid_cam_backend,
)
from app.cam_validation.cam_disclaimer import (
    INDUSTRIAL_HARD_GATES,
    CamDisclaimer,
    build_cam_disclaimer,
)
from app.cam_validation.gcode_loader import (
    REQUIRED_GCODE_REPORT_FIELDS,
    GCodeLoader,
    GCodeLoadResult,
)
from app.cam_validation.internal_validator import (
    InternalValidationReport,
    InternalValidator,
)
from app.cam_validation.voxel_validator import (
    VoxelValidationReport,
    VoxelValidator,
)
from app.cam_validation.cam_adapter import (
    CamAdapter,
    CamSoftwareReport,
)
from app.cam_validation.pipeline import (
    CamValidationPipeline,
    CamValidationResult,
)

__all__: list[str] = [
    # cam_store：枚举
    "CamValidationTaskStatus",
    "CamReviewStatus",
    # cam_store：常量
    "SAFETY_MARGIN_RATIO",
    "PENDING_CALIBRATION_MATERIALS",
    "VALID_CAM_BACKENDS",
    # cam_store：异常
    "CamValidationError",
    "GCodeReportLoadError",
    "InternalValidationError",
    "CamAdapterError",
    "VoxelValidationError",
    "ReviewError",
    "CamValidationPipelineError",
    # cam_store：dataclass
    "FeatureValidationResult",
    "CamValidationTask",
    # cam_store：工具函数 + store 类
    "generate_task_id",
    "get_task_store",
    "is_valid_cam_backend",
    "CamTaskStore",
    # cam_disclaimer
    "INDUSTRIAL_HARD_GATES",
    "CamDisclaimer",
    "build_cam_disclaimer",
    # gcode_loader
    "REQUIRED_GCODE_REPORT_FIELDS",
    "GCodeLoadResult",
    "GCodeLoader",
    # internal_validator
    "InternalValidationReport",
    "InternalValidator",
    # voxel_validator（体素材料去除仿真，闭环强制层）
    "VoxelValidationReport",
    "VoxelValidator",
    # cam_adapter
    "CamAdapter",
    "CamSoftwareReport",
    # pipeline（编排器）
    "CamValidationPipeline",
    "CamValidationResult",
]
