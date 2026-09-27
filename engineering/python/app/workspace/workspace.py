"""
Workspace Resolution Module

Implements automatic workspace resolution for task execution, including model selection,
dataset matching, and environment variable injection based on task metadata.
"""

import logging
import os
import json
import threading
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

from app.ai.lnn.core import ModelType

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceContext:
    """工作空间上下文"""

    task_id: str
    task_type: str
    machine_id: str | None = None
    material_type: str | None = None
    model_type: ModelType | None = None
    model_path: str | None = None
    dataset_path: str | None = None
    config_path: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    workspace_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, ModelType):
                d[k] = v.value
            else:
                d[k] = v
        return d


class ModelSelector:
    """模型选择器 - 根据任务类型自动选择LNN模型"""

    MODEL_TYPE_MAP = {
        "lnn_predict": ModelType.CFC,
        "lnn_train": ModelType.CFC,
        "time_series_predict": ModelType.LTC,
        "time_series_train": ModelType.LTC,
        "wear_analysis": ModelType.HYBRID_LNN,
        "vibration_analysis": ModelType.LTC,
        "rul_prediction": ModelType.LTC,
        "constraint_checking": ModelType.CFC,
        "error_handling": ModelType.CFC,
    }

    MODEL_PATHS = {
        ModelType.CFC: "models/cfc",
        ModelType.LTC: "models/ltc",
        ModelType.HYBRID_LNN: "models/hybrid",
    }

    @classmethod
    def select_model(cls, task_type: str, metadata: dict[str, Any] | None = None) -> ModelType:
        """
        根据任务类型选择模型

        Args:
            task_type: 任务类型
            metadata: 任务元数据（可覆盖默认选择）

        Returns:
            模型类型
        """
        if metadata and "model_type" in metadata:
            try:
                return ModelType(metadata["model_type"])
            except ValueError:
                logger.warning("Invalid model_type in metadata: %s", metadata["model_type"])

        return cls.MODEL_TYPE_MAP.get(task_type, ModelType.CFC)

    @classmethod
    def get_model_path(cls, model_type: ModelType, base_dir: str) -> str:
        """
        获取模型路径

        Args:
            model_type: 模型类型
            base_dir: 基础目录

        Returns:
            模型完整路径
        """
        relative_path = cls.MODEL_PATHS.get(model_type, "models")
        return str(Path(base_dir) / relative_path)

    @classmethod
    def find_best_model(cls, model_type: ModelType, base_dir: str, machine_id: str | None = None) -> str | None:
        """
        查找最佳匹配模型文件

        Args:
            model_type: 模型类型
            base_dir: 基础目录
            machine_id: 机床ID（可选，用于查找机床特定模型）

        Returns:
            模型文件路径，未找到返回None
        """
        model_dir = Path(cls.get_model_path(model_type, base_dir))

        if not model_dir.exists():
            logger.warning("Model directory not found: %s", model_dir)
            return None

        candidates = []

        if machine_id:
            candidates = list(model_dir.glob(f"*{machine_id}*.npz"))

        if not candidates:
            candidates = list(model_dir.glob("*.npz"))

        if not candidates:
            candidates = list(model_dir.glob("*.pt"))

        if not candidates:
            logger.warning("No model files found in %s", model_dir)
            return None

        best_model = candidates[0]
        logger.info("Selected model: %s", best_model)
        return str(best_model)


class DatasetMatcher:
    """数据集匹配器 - 根据机床ID和材料类型自动选择数据集"""

    DATASET_STRUCTURE = {
        "wear_data": "datasets/wear",
        "vibration_data": "datasets/vibration",
        "training_data": "datasets/training",
        "inference_data": "datasets/inference",
    }

    @classmethod
    def match_dataset(
        cls,
        machine_id: str,
        material_type: str | None = None,
        dataset_type: str = "training",
        base_dir: str = "data",
    ) -> str | None:
        """
        匹配数据集

        Args:
            machine_id: 机床ID
            material_type: 材料类型（可选）
            dataset_type: 数据集类型
            base_dir: 基础目录

        Returns:
            数据集路径，未找到返回None
        """
        dataset_dir = Path(base_dir) / cls.DATASET_STRUCTURE.get(dataset_type, "datasets")

        if not dataset_dir.exists():
            logger.warning("Dataset directory not found: %s", dataset_dir)
            return None

        patterns = [f"{machine_id}"]

        if material_type:
            patterns.append(f"{machine_id}_{material_type}")

        for pattern in reversed(patterns):
            matches = list(dataset_dir.glob(f"{pattern}*"))
            if matches:
                selected = matches[0]
                logger.info("Matched dataset: %s for machine %s", selected, machine_id)
                return str(selected)

        logger.warning("No dataset matched for machine %s, material %s", machine_id, material_type)
        return None

    @classmethod
    def load_dataset_config(cls, machine_id: str, base_dir: str = "data") -> dict[str, Any]:
        """
        加载机床数据集配置

        Args:
            machine_id: 机床ID
            base_dir: 基础目录

        Returns:
            数据集配置字典
        """
        config_path = Path(base_dir) / "datasets" / f"{machine_id}_config.json"

        # H18 修复：移除 TOCTOU 检查（exists() 后再 open() 期间文件可能被删除）
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info("Loaded dataset config for machine %s", machine_id)
            return config
        except FileNotFoundError:
            logger.warning("Dataset config not found for machine %s", machine_id)
            return {}
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error("Failed to load dataset config: %s", e)
            return {}


class EnvironmentInjector:
    """环境变量与配置注入器"""

    @classmethod
    def load_machine_config(cls, machine_id: str, base_dir: str = "config") -> dict[str, Any]:
        """
        加载机床配置

        Args:
            machine_id: 机床ID
            base_dir: 配置目录

        Returns:
            机床配置字典
        """
        config_path = Path(base_dir) / f"{machine_id}.json"

        # H18 修复：移除 TOCTOU 检查（exists() 后再 open() 期间文件可能被删除）
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            logger.info("Loaded machine config for %s", machine_id)
            return config
        except FileNotFoundError:
            logger.warning("Machine config not found: %s", config_path)
            return {}
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error("Failed to load machine config: %s", e)
            return {}

    @classmethod
    def inject_environment(
        cls,
        workspace_dir: str,
        machine_config: dict[str, Any],
        task_context: WorkspaceContext,
    ) -> dict[str, str]:
        """
        注入环境变量

        Args:
            workspace_dir: 工作空间目录
            machine_config: 机床配置
            task_context: 任务上下文

        Returns:
            环境变量字典
        """
        env = {
            "LNN_WORKSPACE": workspace_dir,
            "LNN_TASK_ID": task_context.task_id,
            "LNN_TASK_TYPE": task_context.task_type,
            "LNN_MODEL_TYPE": task_context.model_type.value if task_context.model_type else "",
            "LNN_MODEL_PATH": task_context.model_path or "",
            "LNN_DATASET_PATH": task_context.dataset_path or "",
        }

        if machine_config:
            for key, value in machine_config.items():
                env_key = f"LNN_MACHINE_{key.upper()}"
                env[env_key] = str(value)

        if task_context.machine_id:
            env["LNN_MACHINE_ID"] = task_context.machine_id

        if task_context.material_type:
            env["LNN_MATERIAL_TYPE"] = task_context.material_type

        logger.info(
            "Injected %d environment variables for task %s",
            len(env),
            task_context.task_id,
        )
        return env


class WorkspaceResolver:
    """工作空间解析器 - 整合模型选择、数据集匹配和环境注入"""

    def __init__(self, base_dir: str | None = None):
        """
        初始化工作空间解析器

        Args:
            base_dir: 基础目录
        """
        if base_dir is None:
            from app.config import PROJECT_ROOT

            base_dir = PROJECT_ROOT

        self.base_dir = base_dir
        self.workspace_base = os.path.join(base_dir, "workspaces")

        os.makedirs(self.workspace_base, exist_ok=True)
        logger.info("WorkspaceResolver initialized with base: %s", self.workspace_base)

    def resolve(self, task_id: str, task_type: str, metadata: dict[str, Any] | None = None) -> WorkspaceContext:
        """
        解析任务工作空间

        Args:
            task_id: 任务ID
            task_type: 任务类型
            metadata: 任务元数据

        Returns:
            工作空间上下文
        """
        metadata = metadata or {}

        machine_id = metadata.get("machine_id")
        material_type = metadata.get("material_type")

        context = WorkspaceContext(
            task_id=task_id,
            task_type=task_type,
            machine_id=machine_id,
            material_type=material_type,
        )

        context.model_type = ModelSelector.select_model(task_type, metadata)
        context.model_path = ModelSelector.find_best_model(context.model_type, self.base_dir, machine_id)

        if task_type in (
            "lnn_train",
            "lnn_predict",
            "wear_analysis",
            "vibration_analysis",
        ):
            context.dataset_path = DatasetMatcher.match_dataset(
                machine_id or "default",
                material_type,
                dataset_type="training" if "train" in task_type else "inference",
                base_dir=self.base_dir,
            )

        workspace_dir = os.path.join(self.workspace_base, task_id)
        os.makedirs(workspace_dir, exist_ok=True)
        context.workspace_dir = workspace_dir

        machine_config = {}
        if machine_id:
            machine_config = EnvironmentInjector.load_machine_config(machine_id, self.base_dir)
            context.config_path = str(Path(self.base_dir) / f"{machine_id}.json")

        context.environment = EnvironmentInjector.inject_environment(workspace_dir, machine_config, context)

        logger.info(
            "Workspace resolved for task %s: model=%s, dataset=%s",
            task_id,
            context.model_type.value if context.model_type else "N/A",
            context.dataset_path or "N/A",
        )

        return context

    def create_task_workspace(self, task_id: str) -> str:
        """
        创建任务工作空间目录

        Args:
            task_id: 任务ID

        Returns:
            工作空间目录路径
        """
        workspace_dir = os.path.join(self.workspace_base, task_id)
        os.makedirs(workspace_dir, exist_ok=True)

        for subdir in ["checkpoints", "logs", "outputs", "temp"]:
            os.makedirs(os.path.join(workspace_dir, subdir), exist_ok=True)

        logger.info("Task workspace created: %s", workspace_dir)
        return workspace_dir

    def cleanup_workspace(self, task_id: str, keep_outputs: bool = False) -> None:
        """
        清理任务工作空间

        Args:
            task_id: 任务ID
            keep_outputs: 是否保留输出目录
        """
        import shutil

        workspace_dir = os.path.join(self.workspace_base, task_id)

        if not os.path.exists(workspace_dir):
            return

        if keep_outputs:
            os.path.join(workspace_dir, "outputs")
            temp_dir = os.path.join(workspace_dir, "temp")
            logs_dir = os.path.join(workspace_dir, "logs")

            for d in [temp_dir, logs_dir]:
                if os.path.exists(d):
                    shutil.rmtree(d)

            logger.info("Task workspace partially cleaned (outputs kept): %s", workspace_dir)
        else:
            shutil.rmtree(workspace_dir)
            logger.info("Task workspace cleaned: %s", workspace_dir)


class _ResolverHolder:
    """Thread-safe lazy holder for the :class:`WorkspaceResolver` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: WorkspaceResolver | None = None

    def get(self) -> WorkspaceResolver:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = WorkspaceResolver()
            return self._instance

    def init(self, base_dir: str | None = None) -> WorkspaceResolver:
        """强制重新创建解析器（用于启动时指定 base_dir 的场景）。"""
        with self._lock:
            self._instance = WorkspaceResolver(base_dir)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _ResolverHolder()


def get_resolver() -> WorkspaceResolver:
    """获取共享的 :class:`WorkspaceResolver` 单例；首次访问时懒初始化。

    Returns:
        :class:`WorkspaceResolver` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_resolver)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_resolver(base_dir: str | None = None) -> WorkspaceResolver:
    """初始化全局工作空间解析器。

    行为与重构前完全一致：强制创建新实例（用于启动时指定 base_dir）。
    """
    return _holder.init(base_dir)
