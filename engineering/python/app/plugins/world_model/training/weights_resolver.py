"""世界模型权重路径解析（torch-free）.

ADR-020 思路 1 P1 解锁 L3 权重阻塞：``WorldModelPlugin._resolve_weights_path``
在 ModelRegistry 解析失败时，回退到本模块的约定式 URI → 文件路径解析，
让训练产出的 checkpoint 能被 ``TrajectoryPredictor.load_model`` 加载。

URI 约定
--------
``model://world_model/<version>`` → ``<models_dir>/world_model/<version>.pt``

- ``<models_dir>`` 默认 ``data/models``，可由环境变量
  ``WORLD_MODEL_MODELS_DIR`` 覆盖（绝对路径优先）。
- ``<version>`` 仅允许 ``[A-Za-z0-9_.-]+``，防止路径穿越。
- 文件不存在时返回 None（与 ``_resolve_weights_path`` 既有契约一致：
  None 表示使用随机初始化权重，不抛异常）。

设计权衡
--------
- 不修改 ``LNNModelRegistry.PREDEFINED_MODELS``：world_model 是 ADR-017
  世界模型，与 LNN 颤振预测模型（CFC/LTC/HybridLNN）类型不同，强行注册
  会污染既有注册表语义。
- 不依赖 torch：plugin 层在纯 numpy 环境下也需要解析路径（虽然加载权重
  仍需 torch，但路径解析本身是纯字符串操作，独立可测）。
- 与 ``LNNTrainer.save_checkpoint`` 输出格式对齐：训练器按本模块的
  ``build_canonical_weights_path`` 写入 checkpoint，plugin 层按本模块的
  ``resolve_world_model_weights_path`` 读取，形成闭环。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认模型存储根目录（本地化，无云依赖；与 MLflow 默认 data/mlruns 同级）
DEFAULT_MODELS_DIR = os.environ.get(
    "WORLD_MODEL_MODELS_DIR",
    str(Path("data").resolve() / "models"),
)

# URI 前缀（与 plugin.py 中 "model://world_model/1.0.0" 默认值对齐）
_WORLD_MODEL_URI_PREFIX = "model://world_model/"

# 版本字符串白名单（防止 ``../`` 路径穿越）
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class WeightsResolutionError(RuntimeError):
    """权重路径解析失败（URI 格式非法或版本字符串不安全）。"""


def _is_safe_version(version: str) -> bool:
    """校验版本字符串是否安全（无路径穿越字符）.

    拒绝 ``.`` 和 ``..``：虽然 ``{version}.pt`` 拼接后不会真正穿越目录
    （会变成 ``...pt`` 文件名），但这两个字符串语义上指向当前/父目录，
    作为版本字符串不合理且可能诱导下游误用，防御性拒绝。
    """
    if not version or version in (".", ".."):
        return False
    return bool(_VERSION_PATTERN.match(version))


def build_canonical_weights_path(
    version: str,
    models_dir: str | None = None,
) -> str:
    """构造世界模型 checkpoint 的规范存储路径.

    训练器调用此函数决定 checkpoint 写入位置，保证与
    ``resolve_world_model_weights_path`` 形成读写闭环。

    Args:
        version: 模型版本字符串（如 ``1.0.0``、``fusion-v1-20260715``）。
        models_dir: 模型存储根目录。None 时使用 ``DEFAULT_MODELS_DIR``。

    Returns
    -------
    str
        checkpoint 文件绝对路径，形如
        ``<models_dir>/world_model/<version>.pt``。

    Raises
    ------
    WeightsResolutionError
        版本字符串含非法字符（可能造成路径穿越）。
    """
    if not _is_safe_version(version):
        raise WeightsResolutionError(f"版本字符串不安全（仅允许 [A-Za-z0-9_.-]）: {version!r}")
    base = Path(models_dir) if models_dir else Path(DEFAULT_MODELS_DIR)
    return str(base.resolve() / "world_model" / f"{version}.pt")


def resolve_world_model_weights_path(
    model_uri: str,
    models_dir: str | None = None,
) -> str | None:
    """从 ``model://world_model/<version>`` URI 解析 checkpoint 文件路径.

    约定式解析（不查注册表）：
    - URI 必须以 ``model://world_model/`` 开头；
    - 剩余部分作为版本字符串，按白名单校验；
    - 拼接 ``<models_dir>/world_model/<version>.pt``；
    - 文件存在则返回绝对路径，不存在则返回 None（与
      ``_resolve_weights_path`` 既有 None 契约一致）。

    Args:
        model_uri: 模型 URI，如 ``model://world_model/1.0.0``。
        models_dir: 模型存储根目录。None 时使用 ``DEFAULT_MODELS_DIR``。

    Returns
    -------
    Optional[str]
        checkpoint 绝对路径；URI 不匹配约定或文件不存在时返回 None。

    Raises
    ------
    WeightsResolutionError
        URI 匹配前缀但版本字符串非法（提示调用方修正 URI，而非静默降级）。
    """
    if not isinstance(model_uri, str) or not model_uri.startswith(_WORLD_MODEL_URI_PREFIX):
        # 非 world_model URI：交由调用方继续走其他解析路径（如 ModelRegistry）
        return None

    version = model_uri[len(_WORLD_MODEL_URI_PREFIX) :]
    if not _is_safe_version(version):
        raise WeightsResolutionError(
            f"world_model URI 版本字符串不安全（仅允许 [A-Za-z0-9_.-]）: uri={model_uri!r} version={version!r}"
        )

    base = Path(models_dir) if models_dir else Path(DEFAULT_MODELS_DIR)
    candidate = base.resolve() / "world_model" / f"{version}.pt"
    if candidate.is_file():
        logger.debug("world_model 权重路径解析成功: uri=%s path=%s", model_uri, candidate)
        return str(candidate)
    logger.debug(
        "world_model 权重文件不存在（使用随机初始化）: uri=%s expected=%s",
        model_uri,
        candidate,
    )
    return None


__all__ = [
    "DEFAULT_MODELS_DIR",
    "WeightsResolutionError",
    "build_canonical_weights_path",
    "resolve_world_model_weights_path",
]
