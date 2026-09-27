"""灵境制造 全局配置管理。

所有配置项集中管理，支持环境变量覆盖。
遵循12-Factor App原则：配置存储在环境变量中，代码中仅提供合理的开发默认值。

配置层级（按域拆分子模块，本 ``__init__.py`` 仅做聚合 re-export）：
- ServerConfig: HTTP服务器绑定参数
- AIConfig: AI推理/训练模型配置
- SimulationConfig: 数控加工仿真参数
- DatabaseConfig: 数据库路径和连接配置
- StorageConfig: 文件存储路径配置
- SecurityConfig: 安全和认证配置
- PathsConfig: 项目路径约定
- TaskSystemConfig: 异步任务系统配置
- LoggingConfig: 日志轮转和保留策略
- ProcessPlanningConfig: 工艺规划阈值参数
- TokenConfig: LNN认证令牌管理
- SharpConfig: SHARP 三元组验证智能体配置
- ImageTo3DConfig: 拍照重建模块配置（COLMAP+OpenMVS / Hunyuan3D）
- FeatureExtractionConfig: 几何特征提取模块配置（RANSAC 平面/圆柱/孔检测）
- ParametricGeometryConfig: 参数化几何输出模块配置（特征→B-rep→STEP）
- CuttingParametersConfig: 切削参数推荐模块配置（材料→切削参数→ChatterParams）
- ChatterPredictionConfig: 颤振预测接入模块配置（ChatterParams→双路径预测→ChatterReport）
- GCodeGenerationConfig: G 代码生成模块配置（ChatterReport→OperationPlan→GeneratorAdapter→审核→G 代码导出）
- CamValidationConfig: CAM 校验模块配置（G 代码→InternalValidator→CamAdapter→审核→CAM 校验报告导出）
- DreamingConfig: Dreaming 离线反思模块配置（ADR-021，Memory+Dreaming+Outcomes 闭环）
- AppConfig: 顶层聚合配置

环境变量命名约定: LNN_<SECTION>_<KEY>
示例: LNN_SIM_VOXEL_SIZE 对应 SimulationConfig.voxel_size

向后兼容声明：
    全项目大量代码使用 ``from app.config import config, XXX``，所有原有公开符号
    （含 27 个 dataclass 类、5 个私有辅助函数、PROJECT_ROOT / PYTHON_DIR 常量、
    logger、config 单例）均在本模块重新导出，保证 100% 向后兼容。
    ``config.xxx.yyy`` 访问路径与原实现完全一致。
"""

from __future__ import annotations

# 共享工具与常量（_utils.py）
from app.config._utils import (
    PROJECT_ROOT,
    PYTHON_DIR,
    _ROOT_DIR,
    _bool_env,
    _env,
    _float_env,
    _int_env,
    _path,
    logger,
)

# 跨模块复用的运行时限/上限常量（limits.py）
from app.config.limits import (
    DEFAULT_MAX_UPLOAD_SIZE,
    DEFAULT_QUERY_LIMIT,
    DEFAULT_SQLITE_LOCK_TIMEOUT_SEC,
    DEFAULT_THREAD_JOIN_TIMEOUT_SEC,
    MAX_AUDIT_EXPORT_LIMIT,
    MAX_CONCURRENT_TRAINING_TASKS,
    MAX_EXPORT_LIMIT,
    MAX_FILE_SIZE,
    MAX_UPLOAD_SIZE,
    SSE_HEARTBEAT_TIMEOUT_SEC,
)

# 各域子模块的 dataclass
from app.config.ai import AIConfig, FineTuneSettings, ModelRouterSettings
from app.config.cam_validation import CamValidationConfig
from app.config.chatter_prediction import ChatterPredictionConfig
from app.config.cutting_parameters import CuttingParametersConfig
from app.config.database import DatabaseConfig
from app.config.dreaming import DreamingConfig
from app.config.environment import EnvironmentConfig
from app.config.feature_extraction import FeatureExtractionConfig
from app.config.gcode_generation import GCodeGenerationConfig
from app.config.image_to_3d import ImageTo3DConfig, PartPriorConfig
from app.config.logging_config import LoggingConfig
from app.config.parametric_geometry import ParametricGeometryConfig
from app.config.paths import PathsConfig
from app.config.process_planning import ProcessPlanningConfig
from app.config.safety import SecurityConfig, _resolve_cors_origins
from app.config.server import ServerConfig
from app.config.sharp import SharpConfig
from app.config.simulation import HardwareTierConfig, SimulationConfig
from app.config.storage import StorageConfig
from app.config.tasks import TaskSystemConfig
from app.config.token import MESConfig, TokenConfig

# 顶层 AppConfig + 全局单例 config
from app.config.app_config import AppConfig, config

__all__ = [
    # 顶层
    "AppConfig",
    "config",
    # 域 dataclass
    "AIConfig",
    "CamValidationConfig",
    "ChatterPredictionConfig",
    "CuttingParametersConfig",
    "DatabaseConfig",
    "DreamingConfig",
    "EnvironmentConfig",
    "FeatureExtractionConfig",
    "FineTuneSettings",
    "GCodeGenerationConfig",
    "HardwareTierConfig",
    "ImageTo3DConfig",
    "LoggingConfig",
    "MESConfig",
    "ModelRouterSettings",
    "ParametricGeometryConfig",
    "PartPriorConfig",
    "PathsConfig",
    "ProcessPlanningConfig",
    "SecurityConfig",
    "ServerConfig",
    "SharpConfig",
    "SimulationConfig",
    "StorageConfig",
    "TaskSystemConfig",
    "TokenConfig",
    # 常量
    "PROJECT_ROOT",
    "PYTHON_DIR",
    # limits.py 跨模块复用常量
    "DEFAULT_MAX_UPLOAD_SIZE",
    "DEFAULT_QUERY_LIMIT",
    "DEFAULT_SQLITE_LOCK_TIMEOUT_SEC",
    "DEFAULT_THREAD_JOIN_TIMEOUT_SEC",
    "MAX_AUDIT_EXPORT_LIMIT",
    "MAX_CONCURRENT_TRAINING_TASKS",
    "MAX_EXPORT_LIMIT",
    "MAX_FILE_SIZE",
    "MAX_UPLOAD_SIZE",
    "SSE_HEARTBEAT_TIMEOUT_SEC",
]

# 显式声明：保持 `from app.config import _env, _path, ...` 可用
# （测试代码 test_permission_enforced.py 直接导入这些私有辅助函数）
# 同时保持 `from app.config import logger` 可用。
# 这些符号已通过上方 import 语句绑定到本模块命名空间，无需重复声明。
