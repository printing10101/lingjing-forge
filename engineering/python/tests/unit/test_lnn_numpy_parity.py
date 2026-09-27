"""LNN NumPy 推理模型双副本 parity 锁.

背景（2026-09 P1-4 事故催生）：``app/ai/lnn/models/``（工程运行时，
V2.7 解耦误迁后回迁）与 ``research/models/``（科研实验，15+ 实验文件
依赖）是同一组 NumPy 推理实现的两个副本。2b89bec 回迁时两侧已存在
typing 语法与 train() 防护的表面分叉——行为无关，但**没有任何机制
阻止未来某侧修改后静默漂移**（P1-4 事故的根源正是无锁的副本假设）。

本套件锁死两侧的推理行为一致性：同种子构建 → 同输入 → forward 输出
必须逐元素一致。任何一侧的行为性修改都会在此显式失败，强制同步决策；
typing/注释/docstring 层面的差异不影响本锁（AST 级锁过于脆弱）。

research/ 未来物理解耦为独立仓库时（路线图 D 线），本套件按设计自动
skip 退役。
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import numpy as np
import pytest

_UNIT_DIR = Path(__file__).resolve().parent  # engineering/python/tests/unit
_PYTHON_DIR = _UNIT_DIR.parent.parent  # engineering/python
_REPO_ROOT = _PYTHON_DIR.parent.parent  # 仓库根
APP_DIR = _PYTHON_DIR / "app" / "ai" / "lnn" / "models"
RESEARCH_DIR = _REPO_ROOT / "research" / "models"

pytestmark = pytest.mark.unit

if not RESEARCH_DIR.is_dir():
    pytest.skip("research/ 已解耦为独立仓库，双副本 parity 锁退役", allow_module_level=True)

# 三对具体模型（base_lnn 为抽象基类，无可对拍行为）
_MODEL_FILES = ("ltc_model", "cfc_model", "hybrid_lnn")
# 统一小尺寸配置（权重初始化走全局 numpy RNG，种子对齐即权重对齐）
_COMMON_KWARGS = {
    "input_dim": 8,
    "output_dim": 4,
    "hidden_dim": 16,
    "num_layers": 1,
    "memory_size": 32,
}


def _load_app_models() -> dict:
    mods = {}
    for name in _MODEL_FILES:
        mods[name] = importlib.import_module(f"app.ai.lnn.models.{name}")
    return mods


def _load_research_models() -> dict:
    """以临时假包加载 research/models（其内部为相对导入 ``.base_lnn``）。"""
    pkg_name = "_research_lnn_parity_pkg"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(RESEARCH_DIR)]
    sys.modules[pkg_name] = pkg
    mods = {}
    try:
        for name in _MODEL_FILES:
            # 每次强制重新执行，避免受其他测试污染的缓存
            sys.modules.pop(f"{pkg_name}.{name}", None)
            mods[name] = importlib.import_module(f"{pkg_name}.{name}")
    finally:
        for key in list(sys.modules):
            if key == pkg_name or key.startswith(f"{pkg_name}."):
                del sys.modules[key]
    return mods


def _forward_output(module: types.ModuleType, file_name: str, seed: int) -> np.ndarray:
    """同种子构建 → 同输入 forward。返回模型输出。"""
    model_cls = getattr(module, {
        "ltc_model": "LTCModel",
        "cfc_model": "CFCModel",
        "hybrid_lnn": "HybridLNNModel",
    }[file_name])
    np.random.seed(seed)
    model = model_cls(**_COMMON_KWARGS)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((4, _COMMON_KWARGS["input_dim"])).astype(np.float64)
    return np.asarray(model.forward(x))


@pytest.mark.parametrize("file_name", _MODEL_FILES)
def test_numpy_inference_parity(file_name: str):
    """同种子同输入下，app 与 research 副本的 forward 输出必须一致。"""
    app_mods = _load_app_models()
    research_mods = _load_research_models()

    app_out = _forward_output(app_mods[file_name], file_name, seed=42)
    research_out = _forward_output(research_mods[file_name], file_name, seed=42)

    assert app_out.shape == research_out.shape, (
        f"{file_name} 输出形状漂移: app={app_out.shape} research={research_out.shape}"
    )
    np.testing.assert_allclose(
        app_out, research_out, rtol=1e-9, atol=1e-12,
        err_msg=f"{file_name} 推理行为漂移——请同步修改两侧副本或显式决策分叉",
    )
