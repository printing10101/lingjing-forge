"""Postprocessor config submodule (split from config_loader)."""

from __future__ import annotations
import copy
import os
import time
from pathlib import Path

import yaml  # type: ignore[import-untyped]

import logging

logger = logging.getLogger(__name__)

# 支持的控制器件标识（loader 专属，config_loader 经 __all__ 再导出）
VALID_CONTROLLER_IDS = (
    "fanuc",
    "siemens",
    "heidenhain",
    "gsk",
    "hnc",
    "knd",
    "mitsubishi",
    "fagor",
    "xmachine",
)

from app.postprocessor._validator import ConfigValidationError, ConfigValidator  # noqa: E402
from app.postprocessor._limiter import ConfigLimiter  # noqa: E402

# 统一使用 utils 版本（deepcopy 语义：返回值与入参完全隔离）。
# 保留 _deep_merge 别名：config_loader / dialect.compiler / api.postprocessor_dialects
# 均从本模块再导入此名称。
from app.utils.dict_utils import deep_merge as _deep_merge  # noqa: E402


class ConfigLoadError(Exception):
    """配置加载异常。"""


class ConfigLoader:
    """CNC后处理器配置加载器。

    负责YAML文件加载、基础配置与控制器特定配置的合并、
    配置验证、以及配置缓存管理。
    """

    _cache: dict[str, tuple[dict, float]] = {}
    _cache_ttl: float = 300.0

    def __init__(self, cache_ttl: float = 300.0) -> None:
        """初始化配置加载器。

        Args:
            cache_ttl: 缓存有效期（秒），默认300秒
        """
        self._cache_ttl = cache_ttl

    @classmethod
    def clear_cache(cls, controller_id: str | None = None) -> None:
        """清除配置缓存。

        Args:
            controller_id: 指定控制器清除，None 则清除全部
        """
        if controller_id is None:
            cls._cache.clear()
            logger.info("已清除全部配置缓存")
        else:
            # 缓存键格式为 "{config_path}:{controller_id}"，需匹配后缀
            keys_to_delete = [key for key in cls._cache if key.endswith(f":{controller_id}")]
            for key in keys_to_delete:
                del cls._cache[key]
            if keys_to_delete:
                logger.info("已清除控制器 %s 的配置缓存 (%d 个)", controller_id, len(keys_to_delete))

    def _resolve_path(self, config_path: str | None) -> str:
        """解析配置文件路径。

        支持相对路径（相对于项目根目录）和绝对路径。

        Args:
            config_path: 配置文件路径

        Returns:
            解析后的绝对路径
        """
        if config_path is None:
            # 向上逐级搜索 config/postprocessor_config.yaml：
            # 开发布局（仓库根 config/）与打包布局（backend/config/）均可命中。
            # V2.7.0 解耦后 config/ 位于仓库根，固定 dirname 层级已不可靠。
            probe = Path(os.path.abspath(__file__))
            for parent in probe.parents:
                candidate = parent / "config" / "postprocessor_config.yaml"
                if candidate.exists():
                    return str(candidate)
            raise FileNotFoundError(
                f"未找到 postprocessor_config.yaml：已从 {os.path.dirname(probe)} 向上逐级搜索，均不存在。"
            )

        if os.path.isabs(config_path):
            return config_path

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        return os.path.join(project_root, config_path)

    def load(
        self,
        config_path: str | None = None,
        controller_id: str | None = None,
        use_cache: bool = True,
    ) -> dict:
        """加载并合并配置。

        加载顺序：
        1. 解析YAML文件
        2. 提取基础配置（base段）
        3. 提取控制器特定配置并深度合并到基础配置
        4. 验证最终配置

        Args:
            config_path: 配置文件路径，None 使用默认路径
            controller_id: 控制器标识 (fanuc/siemens/heidenhain)，
                           None 则从配置中读取 target_controller
            use_cache: 是否使用缓存

        Returns:
            合并后的完整配置字典

        Raises:
            FileNotFoundError: 配置文件不存在
            ConfigLoadError: YAML解析失败
            ConfigValidationError: 配置验证失败
        """
        resolved_path = self._resolve_path(config_path)
        cache_key = f"{resolved_path}:{controller_id or 'auto'}"

        if use_cache and cache_key in self._cache:
            cached_config, cached_time = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                logger.debug("使用缓存配置: %s", cache_key)
                return copy.deepcopy(cached_config)
            del self._cache[cache_key]

        raw_config = self._load_yaml(resolved_path)

        base_config = raw_config.get("base", {})
        if not base_config:
            raise ConfigLoadError("配置文件中缺少 'base' 段")

        if controller_id is None:
            controller_id = self._resolve_controller_id(raw_config, base_config)

        if controller_id not in VALID_CONTROLLER_IDS:
            raise ConfigLoadError(f"无效的控制器标识: '{controller_id}', 有效值: {VALID_CONTROLLER_IDS}")

        controller_specific = raw_config.get("controllers", {}).get(controller_id, {})

        merged_config = _deep_merge(base_config, controller_specific)

        merged_config["_controller_id"] = controller_id
        merged_config["_config_path"] = resolved_path

        validator = ConfigValidator()
        if not validator.validate(merged_config):
            error_details = "\n".join(validator.errors)
            raise ConfigValidationError(f"配置验证失败 ({len(validator.errors)} 错误):\n{error_details}")

        self._cache[cache_key] = (copy.deepcopy(merged_config), time.time())
        logger.info("配置加载成功: 控制器=%s, 路径=%s", controller_id, resolved_path)

        return merged_config

    def _load_yaml(self, file_path: str) -> dict:
        """加载并解析YAML文件。

        Args:
            file_path: YAML文件绝对路径

        Returns:
            解析后的字典

        Raises:
            FileNotFoundError: 文件不存在
            ConfigLoadError: YAML解析失败
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"配置文件不存在: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"YAML解析失败 ({file_path}): {e}") from e

        if data is None:
            raise ConfigLoadError(f"配置文件为空: {file_path}")

        if not isinstance(data, dict):
            raise ConfigLoadError(f"配置文件格式错误: 期望字典类型, 实际 {type(data).__name__}")

        return data

    def _resolve_controller_id(self, raw_config: dict, base_config: dict) -> str:
        """从配置中解析控制器标识。

        优先级：控制器特定配置的 target_controller > 顶层 target_controller > fanuc

        Args:
            raw_config: 原始YAML配置
            base_config: 基础配置

        Returns:
            控制器标识字符串
        """
        top_controller = raw_config.get("target_controller", "")
        if top_controller in CONTROLLER_FULL_ID_MAP:
            return CONTROLLER_FULL_ID_MAP[top_controller]

        for cid, full_name in CONTROLLER_ID_TO_FULL.items():
            ctrl = raw_config.get("controllers", {}).get(cid, {})
            if ctrl.get("target_controller") == full_name:
                return cid

        logger.warning("无法确定控制器类型，使用默认: fanuc")
        return "fanuc"

    def load_for_controller(
        self,
        controller_id: str,
        config_path: str | None = None,
    ) -> dict:
        """加载指定控制器的配置。

        Args:
            controller_id: 控制器标识 (fanuc/siemens/heidenhain)
            config_path: 配置文件路径

        Returns:
            合并后的控制器配置字典

        Raises:
            ConfigLoadError: 无效的控制器标识
            ConfigValidationError: 配置验证失败
        """
        return self.load(config_path=config_path, controller_id=controller_id)

    def reload(self, config_path: str | None = None) -> dict:
        """强制重新加载配置（忽略缓存）。

        Args:
            config_path: 配置文件路径

        Returns:
            合并后的完整配置字典
        """
        self.clear_cache()
        return self.load(config_path=config_path, use_cache=False)


def create_limiter(config: dict) -> ConfigLimiter:
    """从配置字典创建限幅器实例。

    Args:
        config: 合并后的配置字典

    Returns:
        ConfigLimiter 实例
    """
    return ConfigLimiter(config)


# 常量（自 config_loader 迁移）
CONTROLLER_FULL_ID_MAP = {
    "fanuc_0i": "fanuc",
    "siemens_840d": "siemens",
    "heidenhain_tnc": "heidenhain",
    "gsk_980_25i": "gsk",
    "hnc_848_22": "hnc",
    "knd_1000_2000_3000": "knd",
    "mitsubishi_m70_m80": "mitsubishi",
    "fagor_8055": "fagor",
    "xmachine_xm100": "xmachine",
}
CONTROLLER_ID_TO_FULL = {
    "fanuc": "fanuc_0i",
    "siemens": "siemens_840d",
    "heidenhain": "heidenhain_tnc",
    "gsk": "gsk_980_25i",
    "hnc": "hnc_848_22",
    "knd": "knd_1000_2000_3000",
    "mitsubishi": "mitsubishi_m70_m80",
    "fagor": "fagor_8055",
    "xmachine": "xmachine_xm100",
}
