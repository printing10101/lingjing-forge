"""后处理器注册表与工厂模式实现。

提供后处理器的注册、查找、实例化机制，
支持基于配置文件动态选择和加载后处理器。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.postprocessor.base import BasePostProcessor
from app.postprocessor.config_loader import ConfigLoader, CONTROLLER_ID_TO_FULL
from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.gsk import GSKPostProcessor
from app.postprocessor.hnc import HNCPostProcessor
from app.postprocessor.knd import KNDPostProcessor
from app.postprocessor.mitsubishi import MitsubishiPostProcessor
from app.postprocessor.fagor import FagorPostProcessor
from app.postprocessor.xmachine import XMachineXM100PostProcessor

logger = logging.getLogger(__name__)


class PostProcessorRegistry:
    """后处理器注册表单例。

    管理所有已注册的CNC后处理器类型，
    支持动态注册新类型和基于配置文件的实例化。

    Attributes:
        _instance: 单例实例
        _processors: 已注册的后处理器类型映射
        _instances: 已创建的实例缓存
        _config_loader: 配置加载器实例
    """

    _instance: PostProcessorRegistry | None = None
    _instance_lock = threading.Lock()
    _processors: dict[str, type[BasePostProcessor]] = {}
    _instances: dict[str, BasePostProcessor] = {}
    _config_loader: ConfigLoader
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> PostProcessorRegistry:
        # 安全修复：双重检查锁，防止并发创建多个实例
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._processors = {}
                    instance._instances = {}
                    instance._config_loader = ConfigLoader()
                    instance._lock = threading.Lock()
                    instance._register_builtin()
                    cls._instance = instance
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）。"""
        with cls._instance_lock:
            cls._instance = None

    def _register_builtin(self) -> None:
        """注册内置后处理器类型。"""
        self._processors["fanuc_0i"] = FanucPostProcessor
        self._processors["siemens_840d"] = SiemensPostProcessor
        self._processors["heidenhain_tnc"] = HeidenhainPostProcessor
        # 国产 CNC（Fanuc 0i 兼容方言）
        self._processors["gsk_980_25i"] = GSKPostProcessor
        self._processors["hnc_848_22"] = HNCPostProcessor
        self._processors["knd_1000_2000_3000"] = KNDPostProcessor
        # 国际高端 CNC
        self._processors["mitsubishi_m70_m80"] = MitsubishiPostProcessor
        self._processors["fagor_8055"] = FagorPostProcessor
        # 桌面级五轴机床
        self._processors["xmachine_xm100"] = XMachineXM100PostProcessor

    def register(
        self,
        controller_id: str,
        processor_cls: type[BasePostProcessor],
    ) -> None:
        """注册新的后处理器类型。

        Args:
            controller_id: 控制器标识符
            processor_cls: 后处理器类（必须继承自BasePostProcessor）

        Raises:
            TypeError: 如果processor_cls不是BasePostProcessor的子类
        """
        if not issubclass(processor_cls, BasePostProcessor):
            raise TypeError(f"processor_cls must be a subclass of BasePostProcessor, got {processor_cls.__name__}")
        # 安全修复：保护 _processors 字典的并发读写
        with self._lock:
            self._processors[controller_id] = processor_cls
        logger.info("Registered post-processor: %s -> %s", controller_id, processor_cls.__name__)

    def get_processor(
        self,
        controller_id: str,
        **config: Any,
    ) -> BasePostProcessor:
        """获取或创建后处理器实例。

        Args:
            controller_id: 控制器标识符
            **config: 传递给后处理器构造函数的配置参数（支持旧版接口）

        Returns:
            后处理器实例

        Raises:
            KeyError: 如果指定的控制器类型未注册
        """
        config_key = f"{controller_id}:{sorted(config.items())}"
        # 安全修复：保护 _instances 和 _processors 字典的并发读写
        with self._lock:
            if config_key in self._instances:
                return self._instances[config_key]

            processor_cls = self._processors.get(controller_id)
            if processor_cls is None:
                available = ", ".join(self._processors.keys())
                raise KeyError(f"Unknown controller type: '{controller_id}'. Available: {available}")

            instance = processor_cls(**config)
            self._instances[config_key] = instance
            return instance

    def list_controllers(self) -> list[str]:
        """列出所有已注册的控制器类型。"""
        # 安全修复：保护 _processors 字典的并发读
        with self._lock:
            return list(self._processors.keys())

    def clear_instances(self) -> None:
        """清除所有已创建的实例缓存。"""
        # 安全修复：保护 _instances 字典的并发写
        with self._lock:
            self._instances.clear()

    def load_from_config(
        self,
        config_path: str | None = None,
        use_cache: bool = True,
    ) -> BasePostProcessor:
        """从配置文件加载并实例化后处理器。

        使用ConfigLoader加载YAML配置，合并基础配置和控制器特定配置，
        验证配置完整性，并将完整配置传递给后处理器。

        Args:
            config_path: YAML配置文件路径，默认查找"config/postprocessor_config.yaml"
            use_cache: 是否使用配置缓存

        Returns:
            配置好的后处理器实例

        Raises:
            FileNotFoundError: 如果配置文件不存在
            ConfigLoadError: 配置加载失败
            ConfigValidationError: 配置验证失败
        """
        merged_config = self._config_loader.load(
            config_path=config_path,
            use_cache=use_cache,
        )

        controller_id = merged_config.get("_controller_id", "fanuc")

        # 使用 config_loader 中统一定义的映射表，避免割裂式维护
        full_id = CONTROLLER_ID_TO_FULL.get(controller_id, "fanuc_0i")

        decimal_places = merged_config.get("decimal_places", 3)
        safe_z_height = float(merged_config.get("safe_z_height", 80.0))
        rapid_feed = float(merged_config.get("rapid_feed", 10000))

        instance = self.get_processor(
            full_id,
            decimal_places=decimal_places,
            safe_z_height=safe_z_height,
            rapid_feed=rapid_feed,
            config=merged_config,
        )

        logger.info(
            "Loaded post-processor from config: %s (%s) with full configuration",
            full_id,
            controller_id,
        )
        return instance

    def reload_config(self, config_path: str | None = None) -> BasePostProcessor:
        """强制重新加载配置并返回新实例。

        清除所有缓存后重新加载配置。

        Args:
            config_path: 配置文件路径

        Returns:
            新配置的后处理器实例
        """
        self.clear_instances()
        self._config_loader.clear_cache()
        return self.load_from_config(config_path=config_path, use_cache=False)
