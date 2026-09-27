"""CAM 校验模块配置（阶段 7）。

环境变量前缀：LNN_CAM_*

工程优先策略（项目记忆硬约束）：
- 系统定位「工程师助手」，非「全自动 CAM 仿真器」
- 系统绝不直接接口 CNC 控制器，阶段 7 产物终止于「CAM 校验报告 JSON」
- cam_validation_required 始终 True（不可关闭）
- allow_delete_succeeded 始终 False（避免追溯链断裂）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env, _path, logger


@dataclass
class CamValidationConfig:
    """CAM 校验模块配置（阶段 7）。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    环境变量前缀：LNN_CAM_*

    工程优先策略（项目记忆硬约束）：
    - 系统定位「工程师助手」，非「全自动 CAM 仿真器」
    - 系统绝不直接接口 CNC 控制器，阶段 7 产物终止于「CAM 校验报告 JSON」
    - 大一独立项目不触及物理机床；阶段 7 产物终止于「CAM 校验报告 JSON」
    - cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
    - allow_delete_succeeded 始终 False（避免阶段 5 任务悬空，与阶段 6 对齐）
    - HRC52 pending_calibration 由阶段 5 标注，阶段 7 仅继承，不二次拟合
    - 复用现有 app.simulation.collision_detector.CollisionDetector（组合 has-a）
    - 复用现有 app.simulation.toolpath_parser.ToolpathParser

    pipeline.py 实际使用字段范围：
    - cfg.output_dir：决定 CAM 校验任务工作目录（cam_report.json + internal_report.json）
    - cfg.precision_tier：写入 disclaimer，仅用于显示告知
    - cfg.default_cam_backend：CamAdapter 默认后端（internal_only / pycam / nx_open / powermill / manual）
    - cfg.nx_open_executable / powermill_executable / pycam_executable：CAM 软件 subprocess 调用入口
    - cfg.cam_validation_required / cfg.allow_delete_succeeded：__post_init__ 强制约束
    """

    # 总开关：桌面轻量档位下可关闭
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_CAM_ENABLED", True))

    # 输出目录：存放每次 CAM 校验任务的工作目录（含 cam_report.json + internal_report.json）
    # pipeline.py 在此目录下为每个任务创建独立 workspace_dir
    output_dir: str = field(
        default_factory=lambda: _path("LNN_CAM_OUTPUT_DIR", os.path.join("output", "cam_validation"))
    )

    # 并发约束：CAM 校验为 CPU 密集型（InternalValidator 秒级 + CAM 软件 subprocess 分钟级）
    # 桌面模式默认串行，避免与阶段 1-6 抢占资源
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_CAM_MAX_CONCURRENT", 1))

    # 任务超时（秒）：CAM 软件 subprocess 可能耗时数分钟，留足缓冲
    # internal_only 后端秒级返回，manual 后端等待工程师回填可能跨日
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_CAM_TASK_TIMEOUT", 600))

    # 任务历史保留时长（小时）：与阶段 2-6 一致，工程师审核需要时间
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_CAM_TASK_RETENTION_HOURS", 168))

    # 默认精度档位（仅用于 disclaimer 显示告知，实际精度由上游 mesh 决定）
    # 阶段 7 继承阶段 6 的 precision_tier，不重新标定
    precision_tier: str = field(default_factory=lambda: _env("LNN_CAM_PRECISION_TIER", "mesh_calibrated"))

    # 默认 CAM 后端（CamAdapter 策略模式）
    # - internal_only：仅运行 InternalValidator（秒级，AABB 包围盒），不调用外部 CAM 软件
    # - pycam：subprocess 调用开源 PyCAM 包装器脚本（4 项基础检查，无需许可证）
    # - nx_open：调用 Siemens NX Open subprocess（需 NX 许可证）
    # - powermill：调用 Autodesk PowerMill subprocess（需 PowerMill 许可证）
    # - manual：生成校验清单 + 工程师回填（默认降级路径，无需任何外部软件）
    default_cam_backend: str = field(default_factory=lambda: _env("LNN_CAM_DEFAULT_BACKEND", "internal_only"))

    # NX Open 可执行文件路径（仅 default_cam_backend=nx_open 时使用）
    # 留空时 CamAdapter 自动降级到 manual
    nx_open_executable: str = field(default_factory=lambda: _env("LNN_CAM_NX_OPEN_EXECUTABLE", ""))

    # PowerMill 可执行文件路径（仅 default_cam_backend=powermill 时使用）
    # 留空时 CamAdapter 自动降级到 manual
    powermill_executable: str = field(default_factory=lambda: _env("LNN_CAM_POWERMILL_EXECUTABLE", ""))

    # PyCAM 包装器脚本路径（仅 default_cam_backend=pycam 时使用）
    # 指向项目自带的 python/scripts/cam_adapters/pycam/autorun_gcode_check.py
    # 留空时 CamAdapter 自动降级到 manual（与 nx_open_executable / powermill_executable 风格对齐）
    pycam_executable: str = field(default_factory=lambda: _env("LNN_CAM_PYCAM_EXECUTABLE", ""))

    # 是否允许 SUCCEEDED 状态任务删除（项目记忆硬约束：始终 False）
    # SUCCEEDED 任务包含 cam_report.json，删除会破坏追溯链
    allow_delete_succeeded: bool = field(default_factory=lambda: _bool_env("LNN_CAM_ALLOW_DELETE_SUCCEEDED", False))

    # CAM 二次校验强制（项目记忆硬约束：始终 True，不可关闭）
    # 阶段 6 G 代码必须经阶段 7 CAM 软件二次校验后方可上机床
    # 系统绝不直接接口 CNC 控制器，CAM 校验报告 JSON 为阶段 7 最终产物
    cam_validation_required: bool = field(default_factory=lambda: _bool_env("LNN_CAM_VALIDATION_REQUIRED", True))

    # ── 体素材料去除仿真（仿真强制闭环，优化升级路线图 A 线）──
    # 注意：本校验层无开关（硬约束，与 cam_validation_required 同级），
    # DNC 下发闸门（app.dnc.nc_gate）要求 voxel_check_passed=True 才放行。
    # 以下仅提供性能/几何参数。

    # ── 程序级运动学校验（行程/主轴/进给/快移扎刀/装刀）──
    # 与体素仿真同为 DNC 下发闸门条件（kinematics_check_passed=True 才放行）。
    # 关闭后闸门按"未知"处理（不放行），因此实际上不可跳过——此开关仅供
    # 调试对照（如隔离运动学误报）使用。
    kinematics_enabled: bool = field(default_factory=lambda: _bool_env("LNN_CAM_KINEMATICS_ENABLED", True))

    # 运动学校验机床画像（machines.json 的 id）；默认 vmc_850
    kinematics_machine_id: str = field(default_factory=lambda: _env("LNN_CAM_KINEMATICS_MACHINE_ID", "vmc_850"))

    # 体素边长（mm）：越小越精细越慢。推荐 0.5-2.0（粗仿 2.0，精仿 0.5）
    voxel_size_mm: float = field(default_factory=lambda: _float_env("LNN_CAM_VOXEL_SIZE_MM", 1.0))

    # 体素仿真用刀具直径（mm）：阶段 6 report.json 未携带刀具直径，
    # 使用配置默认值；与实际装刀不符时仿真结论无效，工程师审核界面需核对
    voxel_tool_diameter_mm: float = field(default_factory=lambda: _float_env("LNN_CAM_VOXEL_TOOL_DIAMETER_MM", 10.0))

    # 体素仿真用刀具类型（flat / ball / bullnose / tapered / drill）
    voxel_tool_type: str = field(default_factory=lambda: _env("LNN_CAM_VOXEL_TOOL_TYPE", "flat"))

    # 运动段数上限：超过即拒绝仿真（fail-closed，不允许部分仿真冒充完整校验）
    voxel_max_segments: int = field(default_factory=lambda: _int_env("LNN_CAM_VOXEL_MAX_SEGMENTS", 50000))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        # precision_tier 接受阶段 1-6 已有的档位
        valid_tiers = {"coarse", "standard", "high", "mesh_calibrated"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_CAM_PRECISION_TIER='%s', expected one of %s. Falling back to 'mesh_calibrated'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "mesh_calibrated"

        # default_cam_backend 必须是 5 个合法后端之一
        valid_backends = {
            "internal_only",
            "pycam",
            "nx_open",
            "powermill",
            "manual",
        }
        if self.default_cam_backend not in valid_backends:
            logger.warning(
                "Invalid LNN_CAM_DEFAULT_BACKEND='%s', expected one of %s. Falling back to 'internal_only'.",
                self.default_cam_backend,
                sorted(valid_backends),
            )
            self.default_cam_backend = "internal_only"

        if self.max_concurrent < 1:
            logger.warning(
                "LNN_CAM_MAX_CONCURRENT=%s invalid, must be >= 1. Setting to 1 (serial).",
                self.max_concurrent,
            )
            self.max_concurrent = 1

        if self.task_timeout_seconds < 30:
            logger.warning(
                "LNN_CAM_TASK_TIMEOUT=%s too small (<30s), CAM 软件 subprocess 可能未完成。Setting to 600.",
                self.task_timeout_seconds,
            )
            self.task_timeout_seconds = 600

        # 项目记忆硬约束：SUCCEEDED 禁删，强制 False
        # LNN_CAM_ALLOW_DELETE_SUCCEEDED 环境变量不可开启
        if self.allow_delete_succeeded:
            logger.warning(
                "LNN_CAM_ALLOW_DELETE_SUCCEEDED=true 违反项目记忆硬约束"
                "（SUCCEEDED 任务包含 cam_report.json，删除会破坏追溯链），"
                "强制重置为 false。"
            )
            self.allow_delete_succeeded = False

        # 项目记忆硬约束：CAM 二次校验强制，始终 True
        # LNN_CAM_VALIDATION_REQUIRED 环境变量不可关闭
        if not self.cam_validation_required:
            logger.warning(
                "LNN_CAM_VALIDATION_REQUIRED=false 违反项目记忆硬约束"
                "（阶段 6 G 代码必须经阶段 7 CAM 软件二次校验后方可上机床，"
                "系统绝不直接接口 CNC 控制器），强制重置为 true。"
            )
            self.cam_validation_required = True

        # 体素仿真参数合法性（性能参数，非法时回退默认值）
        if not (0.05 <= self.voxel_size_mm <= 5.0):
            logger.warning(
                "LNN_CAM_VOXEL_SIZE_MM=%s 非法（允许 0.05-5.0mm），回退 1.0。",
                self.voxel_size_mm,
            )
            self.voxel_size_mm = 1.0

        if not (0.5 <= self.voxel_tool_diameter_mm <= 300.0):
            logger.warning(
                "LNN_CAM_VOXEL_TOOL_DIAMETER_MM=%s 非法（允许 0.5-300mm），回退 10.0。",
                self.voxel_tool_diameter_mm,
            )
            self.voxel_tool_diameter_mm = 10.0

        valid_voxel_tool_types = {"flat", "ball", "bullnose", "tapered", "drill"}
        if self.voxel_tool_type not in valid_voxel_tool_types:
            logger.warning(
                "LNN_CAM_VOXEL_TOOL_TYPE='%s' 非法（允许 %s），回退 'flat'。",
                self.voxel_tool_type,
                sorted(valid_voxel_tool_types),
            )
            self.voxel_tool_type = "flat"

        if self.voxel_max_segments < 100:
            logger.warning(
                "LNN_CAM_VOXEL_MAX_SEGMENTS=%s 过小（<100），回退 50000。",
                self.voxel_max_segments,
            )
            self.voxel_max_segments = 50000
