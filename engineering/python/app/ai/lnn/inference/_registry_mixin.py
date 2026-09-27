"""LNN 模型注册表加载 mixin（从 predictor 拆出）。"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from app.ai.lnn.inference.predictor import LNNPredictor

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from app.ai.lnn.inference.model_cache import get_model_cache
from app.ai.lnn.inference.registry import BaseModelRegistry, ModelEntry

logger = logging.getLogger(__name__)


class _RegistryMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）

    @classmethod
    def from_registry(
        cls,
        registry: BaseModelRegistry,
        model_name: str,
        **kwargs,
    ) -> "LNNPredictor":
        """Create predictor from registry with model caching support"""
        cache = get_model_cache()
        cached_model = cache.get(model_name)

        if cached_model is not None:
            logger.info(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} operation=load status=FROM_CACHE")
            created = cls(model=cached_model, model_name=model_name, **kwargs)  # type: ignore[call-arg]
            return cast("LNNPredictor", created)

        logger.info(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} operation=load status=FROM_REGISTRY")
        load_start = time.perf_counter()
        model = cls._load_model_from_registry(registry, model_name)
        load_duration = time.perf_counter() - load_start

        try:
            from app.utils.utils import get_metrics_collector

            get_metrics_collector().record_lnn_model_load(model_name, load_duration)
        except (ImportError, AttributeError, RuntimeError, ValueError) as e:
            # 模型加载指标记录失败仅影响可观测性，不影响加载流程
            logger.debug(
                f"Failed to record model load metrics for {model_name}: {e}",
                exc_info=True,
            )

        try:
            memory_bytes = cls._calculate_model_memory(model)
            cache.put(model_name, model, memory_bytes)
            logger.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} "
                f"operation=cache status=CACHED memory={memory_bytes} bytes"
            )
        except (OSError, ValueError, TypeError, AttributeError) as e:
            # 模型缓存写入或内存计算可能因缓存后端或属性访问失败，
            # 失败时记录警告但允许模型继续使用（不缓存即可）
            logger.warning(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} operation=cache status=FAILED error={e}"
            )

        created = cls(model=model, model_name=model_name, **kwargs)  # type: ignore[call-arg]
        return cast("LNNPredictor", created)

    @staticmethod
    def _load_model_from_registry(registry: BaseModelRegistry, model_name: str) -> Any:
        """Load model from registry using the standard get() interface.

        Args:
            registry: Model registry instance (must implement BaseModelRegistry)
            model_name: Name of the model to load

        Returns:
            Model instance

        Raises:
            KeyError: If model not found in registry
            RuntimeError: If registry type is unsupported
        """
        if not isinstance(registry, BaseModelRegistry):
            if not (hasattr(registry, "get") and callable(getattr(registry, "get"))):
                supported = [BaseModelRegistry.__name__, "dict", "dict-like with get()"]
                actual = type(registry).__name__
                raise RuntimeError(
                    f"模型加载失败：注册表类型不兼容。错误详情: 不支持的注册表类型 '{actual}'。"
                    f"预期类型为: {', '.join(supported)}。"
                    "请将注册表包装为 BaseModelRegistry 适配器，"
                    "或使用支持 get() 方法的字典类对象。"
                )

        try:
            model = registry.get(model_name)
            if model is None:
                raise KeyError(
                    f"模型加载异常：模型 '{model_name}' 在注册表中存在但返回为空（None）。"
                    "可能原因：1) 模型文件已损坏或丢失；2) 模型加载过程出现异常。"
                    "请检查模型文件完整性，或调用 POST /api/v1/lnn/models/{name}/load 重新加载模型。"
                )

            if isinstance(model, ModelEntry):
                return _RegistryMixin._build_model_from_entry(model)

            return model
        except KeyError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, ImportError) as e:
            # 模型加载涉及文件 IO、模块导入、张量加载等具体异常
            raise RuntimeError(
                f"模型加载失败：无法加载模型 '{model_name}'。错误详情: {e}。"
                "可能原因：1) 模型权重文件不存在或已损坏；"
                "2) 模型配置与权重不匹配；3) 内存/GPU 显存不足。"
                "请检查模型文件路径和完整性，或查看日志获取详细错误信息。"
            ) from e

    @staticmethod
    def _build_model_from_entry(entry: ModelEntry) -> Any:
        """Build a real model instance from a ModelEntry metadata."""
        if entry.info is None:
            raise ValueError(f"模型条目缺少元数据: {entry}")
        if entry.model is not None:
            logger.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={entry.info.name} "
                f"operation=build_model status=FROM_CACHED_ENTRY input_dim={entry.model.input_dim}"
            )
            return entry.model

        from app.ai.lnn.inference.registry import LNNModelRegistry

        model_cls = LNNModelRegistry.MODEL_CLASS_MAP.get(entry.info.model_type)
        if model_cls is None:
            raise ValueError(f"Unsupported model type: {entry.info.model_type}")

        input_dim = len(entry.info.input_features) if entry.info.input_features else 1
        output_dim = len(entry.info.output_features) if entry.info.output_features else 1

        logger.info(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={entry.info.name} "
            f"operation=build_model status=CREATING input_dim={input_dim} output_dim={output_dim} "
            f"input_features={entry.info.input_features}"
        )

        model = model_cls(
            model_name=entry.info.name,
            input_dim=input_dim,
            output_dim=output_dim,
        )

        model_path = entry.info.model_path
        if model_path and os.path.exists(model_path):
            logger.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={entry.info.name} "
                f"operation=build_model status=LOADING_FILE path={model_path}"
            )
            try:
                model.load(model_path)
            except (OSError, IOError, RuntimeError, ValueError, TypeError) as e:
                # 模型权重加载失败时使用初始化权重继续构建，记录以便排查
                logger.warning(
                    f"Failed to load weights from {model_path}, falling back to initialized weights: {e}",
                    exc_info=True,
                )

        model.build()

        logger.info(
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={entry.info.name} "
            f"operation=build_model status=BUILT model_input_dim={model.input_dim}"
        )

        entry.model = model
        entry.is_loaded = True
        return model

    @staticmethod
    def _calculate_model_memory(model) -> int:
        """
        Calculate memory size of a model in bytes.

        Args:
            model: Model instance

        Returns:
            Memory size in bytes
        """
        if not HAS_TORCH or not isinstance(model, torch.nn.Module):
            return 0

        try:
            param_size = sum(p.numel() * p.element_size() for p in model.parameters())
            buffer_size = sum(b.numel() * b.element_size() for b in model.buffers())
            return param_size + buffer_size
        except (AttributeError, RuntimeError, TypeError):
            # 计算模型内存可能因张量属性访问失败，回退返回 0（不影响主流程）
            return 0
