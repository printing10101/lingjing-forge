"""G 代码生成模块配置（ChatterReport→OperationPlan→GeneratorAdapter→审核→G 代码导出）。

环境变量前缀：LNN_GC_*

工程优先策略（项目记忆硬约束）：
- 系统定位「工程师助手」，非「全自动 G 代码生成器」
- 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床
- 系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件
- 复用现有 app.postprocessor 包 + GCodeGenerator（212 个测试用例覆盖）
- cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
- allow_delete_succeeded 始终 False（SUCCEEDED 任务可能被阶段 7 CAM 校验引用）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _int_env, _path, logger


@dataclass
class GCodeGenerationConfig:
    """G 代码生成模块配置（阶段 6）。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    环境变量前缀：LNN_GC_*

    工程优先策略（项目记忆硬约束）：
    - 系统定位「工程师助手」，非「全自动 G 代码生成器」
    - 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床
    - 系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件
    - 复用现有 app.postprocessor 包 + GCodeGenerator（212 个测试用例覆盖）
    - cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
    - allow_delete_succeeded 始终 False（SUCCEEDED 任务可能被阶段 7 CAM 校验引用）

    pipeline.py 实际使用字段范围：
    - cfg.output_dir（pipeline.py 第 634 行：决定 G 代码导出根目录）
    - cfg.precision_tier（pipeline.py 第 684 行：写入 disclaimer，仅用于显示告知）
    其他参数（controller_type / program_number / safe_z / stock_top_z）由 API 请求传入，不在 Config 中。
    """

    # 总开关：桌面轻量档位下可关闭
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_GC_ENABLED", True))

    # 输出目录：存放每次 G 代码生成任务的工作目录（含 .gcode 文件 + 审核 JSON）
    # pipeline.py 在此目录下为每个任务创建独立 workspace_dir
    output_dir: str = field(
        default_factory=lambda: _path("LNN_GC_OUTPUT_DIR", os.path.join("output", "gcode_generation"))
    )

    # 并发约束：G 代码生成为 CPU 密集型（GeneratorAdapter 单次 < 500ms）
    # 桌面模式默认串行，避免与阶段 1-5 抢占资源
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_GC_MAX_CONCURRENT", 1))

    # 任务超时（秒）：单任务生成 + 导出 < 5 秒，审核等待不计入
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_GC_TASK_TIMEOUT", 60))

    # 任务历史保留时长（小时）：与阶段 2-5 一致，工程师审核需要时间
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_GC_TASK_RETENTION_HOURS", 168))

    # 默认精度档位（仅用于 disclaimer 显示告知，实际精度由上游 mesh 决定）
    # pipeline.py 通过 getattr(cfg, "precision_tier", "mesh_calibrated") 读取
    precision_tier: str = field(default_factory=lambda: _env("LNN_GC_PRECISION_TIER", "mesh_calibrated"))

    # 默认 mesh 标定状态：保守默认为 False，强制上游显式声明已标定
    default_mesh_calibrated: bool = field(default_factory=lambda: _bool_env("LNN_GC_DEFAULT_MESH_CALIBRATED", False))

    # 默认控制器类型（仅供 disclaimer 显示追溯，实际 controller_type 由 API 请求传入）
    default_controller_type: str = field(default_factory=lambda: _env("LNN_GC_DEFAULT_CONTROLLER_TYPE", "fanuc_0i"))

    # 是否允许 SUCCEEDED 状态任务删除（项目记忆硬约束：始终 False）
    # SUCCEEDED 任务可能已被阶段 7 CAM 校验引用，删除会破坏追溯链
    allow_delete_succeeded: bool = field(default_factory=lambda: _bool_env("LNN_GC_ALLOW_DELETE_SUCCEEDED", False))

    # CAM 二次校验强制（项目记忆硬约束：始终 True，不可关闭）
    # 生成的 G 代码仅供人工加载到 CAM 软件，系统绝不直接接口 CNC 控制器
    cam_validation_required: bool = field(default_factory=lambda: _bool_env("LNN_GC_CAM_VALIDATION_REQUIRED", True))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        # precision_tier 接受阶段 1-5 已有的档位 + mesh_calibrated（阶段 6 默认值）
        valid_tiers = {"coarse", "standard", "high", "mesh_calibrated"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_GC_PRECISION_TIER='%s', expected one of %s. Falling back to 'mesh_calibrated'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "mesh_calibrated"

        if self.max_concurrent < 1:
            logger.warning(
                "LNN_GC_MAX_CONCURRENT=%s invalid, must be >= 1. Setting to 1 (serial).",
                self.max_concurrent,
            )
            self.max_concurrent = 1

        if self.task_timeout_seconds < 5:
            logger.warning(
                "LNN_GC_TASK_TIMEOUT=%s too small (<5s), G 代码生成可能未完成。Setting to 60.",
                self.task_timeout_seconds,
            )
            self.task_timeout_seconds = 60

        # 项目记忆硬约束：SUCCEEDED 禁删，强制 False
        if self.allow_delete_succeeded:
            logger.warning(
                "LNN_GC_ALLOW_DELETE_SUCCEEDED=true 违反项目记忆硬约束"
                "（SUCCEEDED 任务可能被阶段 7 CAM 校验引用），强制重置为 false。"
            )
            self.allow_delete_succeeded = False

        # 项目记忆硬约束：CAM 二次校验强制，始终 True
        if not self.cam_validation_required:
            logger.warning(
                "LNN_GC_CAM_VALIDATION_REQUIRED=false 违反项目记忆硬约束"
                "（生成的 G 代码必须经 CAM 软件二次校验后方可上机床，"
                "系统绝不直接接口 CNC 控制器），强制重置为 true。"
            )
            self.cam_validation_required = True
