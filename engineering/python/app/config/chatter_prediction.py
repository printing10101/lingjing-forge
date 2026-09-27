"""颤振预测接入模块配置（ChatterParams→双路径预测→ChatterReport）。

环境变量前缀：LNN_CH_*

工程优先策略（项目记忆硬约束）：
- 默认走 Tlusty 解析法路径（stability.py 已实现，工程可用）
- LTC 神经网络路径标记为「实验性」，仅在 chatter_model.pt 存在时尝试
- chatter_model.pt 不存在或推理失败时自动回退到 Tlusty 解析法
- cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
- allow_delete_succeeded 始终 False（SUCCEEDED 任务可能被阶段 6 引用）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _int_env, _path, logger


@dataclass
class ChatterPredictionConfig:
    """颤振预测接入模块配置（阶段 5）。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    环境变量前缀：LNN_CH_*

    工程优先策略（项目记忆硬约束）：
    - 默认走 Tlusty 解析法路径（stability.py 已实现，工程可用）
    - LTC 神经网络路径标记为「实验性」，仅在 chatter_model.pt 存在时尝试
    - chatter_model.pt 不存在或推理失败时自动回退到 Tlusty 解析法
    - cam_validation_required 始终 True（项目记忆硬约束，不可关闭）
    - allow_delete_succeeded 始终 False（SUCCEEDED 任务可能被阶段 6 引用）
    """

    # 总开关：桌面轻量档位下可关闭，避免 stability 模块加载开销
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_CH_ENABLED", True))

    # 输出目录：存放每次颤振预测任务的工作目录（含 ChatterReport JSON）
    output_dir: str = field(
        default_factory=lambda: _path("LNN_CH_OUTPUT_DIR", os.path.join("output", "chatter_prediction"))
    )

    # 并发约束：双路径预测为 CPU 密集型（解析法 < 10ms / 特征，LTC 推理视模型而定）
    # 桌面模式默认串行，避免与 stage 1-4 抢占资源
    max_concurrent: int = field(default_factory=lambda: _int_env("LNN_CH_MAX_CONCURRENT", 1))

    # 任务超时（秒）：单特征预测 < 1 秒，多特征批量需更长时间
    # 此 timeout 仅覆盖 run_pipeline 阶段，工程师审核等待不计入
    task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_CH_TASK_TIMEOUT", 120))

    # 任务历史保留时长（小时）：与阶段 2/3/4 一致，工程师审核需要时间
    task_retention_hours: int = field(default_factory=lambda: _int_env("LNN_CH_TASK_RETENTION_HOURS", 168))

    # 默认精度档位（仅用于显示告知，实际精度由上游 mesh 决定）
    # 继承自阶段 1/2/3/4，本模块不引入新档位
    precision_tier: str = field(default_factory=lambda: _env("LNN_CH_PRECISION_TIER", "standard"))

    # 默认 mesh 标定状态：保守默认为 False，强制上游显式声明已标定
    default_mesh_calibrated: bool = field(default_factory=lambda: _bool_env("LNN_CH_DEFAULT_MESH_CALIBRATED", False))

    # 默认机床类型（仅供追溯，实际机床动态参数由阶段 4 ChatterParams 携带）
    default_machine_type: str = field(default_factory=lambda: _env("LNN_CH_DEFAULT_MACHINE_TYPE", "vmc_850"))

    # 是否强制走解析法路径（测试用，忽略 chatter_model.pt 存在性）
    # 生产环境应保持 False，让适配器自动检测 LTC 可用性
    force_analytical: bool = field(default_factory=lambda: _bool_env("LNN_CH_FORCE_ANALYTICAL", False))

    # 是否允许 SUCCEEDED 状态任务删除（项目记忆硬约束：始终 False）
    # SUCCEEDED 任务可能已被阶段 6 G 代码生成引用，删除会破坏追溯链
    allow_delete_succeeded: bool = field(default_factory=lambda: _bool_env("LNN_CH_ALLOW_DELETE_SUCCEEDED", False))

    # CAM 二次校验强制（项目记忆硬约束：始终 True，不可关闭）
    # 本系统输出的 ChatterReport 仅供阶段 6 参考，实际加工必须经 CAM 校验
    cam_validation_required: bool = field(default_factory=lambda: _bool_env("LNN_CH_CAM_VALIDATION_REQUIRED", True))

    def __post_init__(self) -> None:
        """启动时校验配置合法性。"""
        valid_tiers = {"coarse", "standard", "high"}
        if self.precision_tier not in valid_tiers:
            logger.warning(
                "Invalid LNN_CH_PRECISION_TIER='%s', expected one of %s. Falling back to 'standard'.",
                self.precision_tier,
                sorted(valid_tiers),
            )
            self.precision_tier = "standard"

        if self.max_concurrent < 1:
            logger.warning(
                "LNN_CH_MAX_CONCURRENT=%s invalid, must be >= 1. Setting to 1 (serial).",
                self.max_concurrent,
            )
            self.max_concurrent = 1

        if self.task_timeout_seconds < 10:
            logger.warning(
                "LNN_CH_TASK_TIMEOUT=%s too small (<10s), 批量预测可能未完成。Setting to 120.",
                self.task_timeout_seconds,
            )
            self.task_timeout_seconds = 120

        # 项目记忆硬约束：SUCCEEDED 禁删，强制 False
        if self.allow_delete_succeeded:
            logger.warning(
                "LNN_CH_ALLOW_DELETE_SUCCEEDED=true 违反项目记忆硬约束"
                "（SUCCEEDED 任务可能被阶段 6 G 代码生成引用），强制重置为 false。"
            )
            self.allow_delete_succeeded = False

        # 项目记忆硬约束：CAM 二次校验强制，始终 True
        if not self.cam_validation_required:
            logger.warning(
                "LNN_CH_CAM_VALIDATION_REQUIRED=false 违反项目记忆硬约束"
                "（生成的 ChatterReport 仅供阶段 6 参考，"
                "实际加工必须经 CAM 软件二次校验），强制重置为 true。"
            )
            self.cam_validation_required = True
