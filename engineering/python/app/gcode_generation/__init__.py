"""G 代码生成接入模块（阶段 6）。

将阶段 5 输出的 ChatterReport JSON（每条特征的 limit_depth_mm / axial_depth_mm / stable）
+ 阶段 3 输出的 OperationPlan JSON 转换为带工程师审核状态的 G 代码文件，
导出 .nc/.mpf/.h 文件 + 审核记录 JSON 供阶段 7 CAM 校验使用。

核心 pipeline：
    阶段 5 ChatterReport JSON + 阶段 3 OperationPlan JSON
        → ChatterReportLoader.load() 加载特征稳定性 + 安全裕度
        → GeneratorAdapter.adapt() 封装现有 GCodeGenerator 生成基础 G 代码
        → stable == False 的特征使 is_valid == False → FAILED（强制回阶段 5）
        → 工程师审核每个特征 G 代码段（confirmed / rejected / edited）
        → 全部审核完毕 → REVIEWED → confirm_task → SUCCEEDED
        → 导出 G 代码文件至 {output_dir}/{task_id}.{ext} + 报告 JSON 至 {output_dir}/{task_id}.report.json

定位声明（项目记忆硬约束）：
    本模块是「工程师助手」，不是「全自动 G 代码生成器」。
    G 代码生成基于现有 app.postprocessor 包 + app.process_planning.gcode_generator.GCodeGenerator
    （212 个测试用例覆盖，不重写）。
    生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床。
    系统绝不直接接口 CNC 控制器。
    cam_validation_required 始终 True，不可由环境变量关闭。
    SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）。

精度继承链（不引入新的精度档位）：
    阶段 1 image_to_3d.precision_tier
    → 阶段 2 feature_extraction.precision_tier
    → 阶段 3 parametric_geometry.precision_tier
    → 阶段 4 cutting_parameters.precision_tier
    → 阶段 5 chatter_prediction
    → 阶段 6 gcode_generation（本模块全程继承上游告知）
    精度档位影响 disclaimer 文本告知，不改变 G 代码生成逻辑。

K_s 传递策略（项目记忆硬约束）：
    K_s（cutting_force_coeff）直接来自阶段 4，不进行二次拟合。
    阶段 6 不涉及拟合，仅继承阶段 5 ChatterReport 中的 limit_depth_mm / axial_depth_mm / stable。
"""

from __future__ import annotations

# 任务存储 + 状态机 + 审核枚举
from app.gcode_generation.gcode_store import (
    ChatterReportLoadError,
    CONTROLLER_FILE_EXTENSIONS,
    DEFAULT_FILE_EXTENSION,
    FeatureGCodeResult,
    GCodeGenerationError,
    GCodeGenerationPipelineError,
    GCodeGenerationTask,
    GCodeGenerationTaskStatus,
    GCodeReviewStatus,
    OperationPlanLoadError,
    PENDING_CALIBRATION_MATERIALS,
    ReviewError,
    SAFETY_MARGIN_RATIO,
    TaskStore,
    generate_task_id,
    get_file_extension,
    get_task_store,
)

# 精度告知 + 工业硬门槛
from app.gcode_generation.gcode_disclaimer import (
    GCodeDisclaimer,
    INDUSTRIAL_HARD_GATES,
    build_gcode_disclaimer,
)

# ChatterReport 加载器
from app.gcode_generation.chatter_report_loader import (
    ChatterReportLoader,
    LoadedChatterReport,
    REQUIRED_FEATURE_FIELDS,
    REQUIRED_REPORT_FIELDS,
)

# GeneratorAdapter（封装现有 GCodeGenerator）
from app.gcode_generation.generator_adapter import (
    GeneratorAdapter,
    GeneratorAdapterError,
    load_operation_plan,
)

# 流水线编排器
from app.gcode_generation.pipeline import (
    GCodeGenerationPipeline,
    GCodeGenerationResult,
    GCodeReviewError,
)

__all__ = [
    # 任务存储 + 状态机
    "GCodeGenerationTask",
    "GCodeGenerationTaskStatus",
    "GCodeReviewStatus",
    "FeatureGCodeResult",
    "GCodeGenerationError",
    "ChatterReportLoadError",
    "OperationPlanLoadError",
    "ReviewError",
    "GCodeGenerationPipelineError",
    "TaskStore",
    "generate_task_id",
    "get_task_store",
    "get_file_extension",
    # 常量
    "SAFETY_MARGIN_RATIO",
    "PENDING_CALIBRATION_MATERIALS",
    "CONTROLLER_FILE_EXTENSIONS",
    "DEFAULT_FILE_EXTENSION",
    # 精度告知
    "GCodeDisclaimer",
    "INDUSTRIAL_HARD_GATES",
    "build_gcode_disclaimer",
    # ChatterReport 加载器
    "ChatterReportLoader",
    "LoadedChatterReport",
    "REQUIRED_REPORT_FIELDS",
    "REQUIRED_FEATURE_FIELDS",
    # GeneratorAdapter
    "GeneratorAdapter",
    "GeneratorAdapterError",
    "load_operation_plan",
    # 流水线
    "GCodeGenerationPipeline",
    "GCodeGenerationResult",
    "GCodeReviewError",
]
