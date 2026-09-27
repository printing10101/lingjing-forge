"""顶层应用配置聚合（AppConfig + config 单例）。

按域拆分重构后的聚合入口：将各子模块的 dataclass 组装为顶层 AppConfig，
并实例化全局单例 ``config``。字段顺序、字段名、类型、默认值与原
``app/config/__init__.py`` 完全一致，保持 100% 向后兼容。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env
from app.config.ai import AIConfig, FineTuneSettings, ModelRouterSettings
from app.config.cam_validation import CamValidationConfig
from app.config.chatter_prediction import ChatterPredictionConfig
from app.config.cutting_parameters import CuttingParametersConfig
from app.config.database import DatabaseConfig
from app.config.dreaming import DreamingConfig
from app.config.environment import EnvironmentConfig
from app.config.feature_extraction import FeatureExtractionConfig
from app.config.gcode_generation import GCodeGenerationConfig
from app.config.image_to_3d import ImageTo3DConfig
from app.config.logging_config import LoggingConfig
from app.config.parametric_geometry import ParametricGeometryConfig
from app.config.paths import PathsConfig
from app.config.process_planning import ProcessPlanningConfig
from app.config.safety import SecurityConfig
from app.config.server import ServerConfig
from app.config.sharp import SharpConfig
from app.config.simulation import HardwareTierConfig, SimulationConfig
from app.config.storage import StorageConfig
from app.config.tasks import TaskSystemConfig
from app.config.token import MESConfig, TokenConfig


@dataclass
class AppConfig:
    app_name: str = field(default_factory=lambda: _env("APP_NAME", "灵境制造"))
    app_version: str = field(default_factory=lambda: _env("APP_VERSION", "2.8.0"))
    offline_mode: bool = field(default_factory=lambda: _bool_env("OFFLINE_MODE", False))
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    model_router: ModelRouterSettings = field(default_factory=ModelRouterSettings)
    finetune: FineTuneSettings = field(default_factory=FineTuneSettings)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    hardware: HardwareTierConfig = field(default_factory=HardwareTierConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    token: TokenConfig = field(default_factory=TokenConfig)
    tasks: TaskSystemConfig = field(default_factory=TaskSystemConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    process_planning: ProcessPlanningConfig = field(default_factory=ProcessPlanningConfig)
    mes: MESConfig = field(default_factory=MESConfig)
    sharp: SharpConfig = field(default_factory=SharpConfig)
    image_to_3d: ImageTo3DConfig = field(default_factory=ImageTo3DConfig)
    feature_extraction: FeatureExtractionConfig = field(default_factory=FeatureExtractionConfig)
    parametric_geometry: ParametricGeometryConfig = field(default_factory=ParametricGeometryConfig)
    cutting_parameters: CuttingParametersConfig = field(default_factory=CuttingParametersConfig)
    chatter_prediction: ChatterPredictionConfig = field(default_factory=ChatterPredictionConfig)
    gcode_generation: GCodeGenerationConfig = field(default_factory=GCodeGenerationConfig)
    cam_validation: CamValidationConfig = field(default_factory=CamValidationConfig)
    dreaming: DreamingConfig = field(default_factory=DreamingConfig)


config = AppConfig()
