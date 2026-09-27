"""数控加工仿真与硬件档位配置。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env


@dataclass
class SimulationConfig:
    voxel_size: float = field(default_factory=lambda: _float_env("LNN_SIM_VOXEL_SIZE", 1.0))
    voxel_size_min: float = 0.1
    voxel_size_max: float = 10.0
    max_store_size: int = field(default_factory=lambda: _int_env("LNN_SIM_MAX_STORE", 500))
    max_store_age_seconds: int = field(default_factory=lambda: _int_env("LNN_SIM_STORE_AGE", 86400))
    collision_margin_mm: float = field(default_factory=lambda: _float_env("LNN_SIM_COLLISION_MARGIN", 0.5))
    batch_processing_size: int = field(default_factory=lambda: _int_env("LNN_SIM_BATCH_SIZE", 2000))
    default_safe_z_height_mm: float = field(default_factory=lambda: _float_env("LNN_SIM_SAFE_Z", 10.0))
    idle_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_IDLE_TIMEOUT", 1800))


# Hardware Tier Configuration
# [U-P0-2] 防复发：硬件档位声明 + 轻量模式
# 设计依据：用户三方评估指出"本地化承诺 vs 硬件现实"矛盾——
# 项目承诺全本地 LLM，但用户机器可能无 GPU/内存不足，导致 Ollama 启动失败。
# 通过显式档位声明 + 轻量模式开关，让用户根据硬件条件选择合适的能力子集。
# 档位定义（与 docs/user-guide/安装指南.md 硬件配置表对齐）：
# - minimal : 4 核 CPU / 8 GB RAM / 无 GPU（仅规则引擎 + 云端 API）
# - standard: 8 核 CPU / 16 GB RAM / 可选 GPU（默认，支持本地小模型）
# - high : 8 核+ CPU / 32 GB RAM / NVIDIA GPU ≥ 6 GB（本地 7B-14B 模型）
# - ultra : 工作站级（本地 14B+ 模型 + GPU 训练）
# 轻量模式（lightweight_mode）：
# - 跳过 Ollama 启动探测（即使安装了 Ollama 也不加载）
# - 禁用本地模型路由，强制使用云端 API 或规则引擎
# - 限制 AI 并发数为 1，降低内存占用
# - 适用于老旧硬件 / 临时演示 / 仅需 CAM 核心功能的场景


@dataclass
class HardwareTierConfig:
    """硬件档位配置。

    通过环境变量 ``LNN_HARDWARE_TIER`` / ``LNN_LIGHTWEIGHT_MODE`` /
    ``LNN_SKIP_OLLAMA`` / ``LNN_MAX_CONCURRENT_AI`` 控制。
    前端 ``useSettingsStore`` 同步持久化用户偏好（localStorage），
    后端通过环境变量在启动时固化（运行时不可变，需重启生效）。
    """

    tier: str = field(default_factory=lambda: _env("LNN_HARDWARE_TIER", "standard"))
    lightweight_mode: bool = field(default_factory=lambda: _bool_env("LNN_LIGHTWEIGHT_MODE", False))
    skip_ollama: bool = field(default_factory=lambda: _bool_env("LNN_SKIP_OLLAMA", False))
    max_concurrent_ai: int = field(default_factory=lambda: _int_env("LNN_MAX_CONCURRENT_AI", 2))

    def __post_init__(self) -> None:
        """校验档位值合法性，非法值回退到 standard 并记录警告。"""
        valid_tiers = {"minimal", "standard", "high", "ultra"}
        if self.tier not in valid_tiers:
            import warnings

            warnings.warn(
                f"Invalid LNN_HARDWARE_TIER='{self.tier}', "
                f"expected one of {sorted(valid_tiers)}. "
                f"Falling back to 'standard'.",
                stacklevel=2,
            )
            self.tier = "standard"

        # 轻量模式自动派生：minimal 档位隐式启用轻量模式
        if self.tier == "minimal":
            self.lightweight_mode = True
            self.skip_ollama = True

        # 轻量模式下限制 AI 并发数为 1
        if self.lightweight_mode and self.max_concurrent_ai > 1:
            self.max_concurrent_ai = 1
