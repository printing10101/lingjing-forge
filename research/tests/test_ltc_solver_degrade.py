"""LTC 求解器数值降级与训练器梯度裁剪的单元测试。

背景（《真实数据验证现状》§五）：真实数据上 torchdiffeq dopri5 会因刚性段
报 "underflow in dt nan"，或"成功"返回含 NaN 的解（DLLNNWithPhysics
stage-2 NaN 的直接病灶）。LTCCell 现对这两类失败自动降级一阶 Euler；
DLLNNTrainer / BaselineTrainer 增加梯度裁剪作为第二道防线。
"""

import importlib.util
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

try:
    import torch
except ImportError:  # 与 research/tests 其他用例一致：无 torch 环境跳过而非收集报错
    torch = None

RESEARCH_DIR = Path(__file__).resolve().parents[1]
EXPERIMENTS_DIR = RESEARCH_DIR / "experiments"
ENGINEERING_PY = RESEARCH_DIR.parent / "engineering" / "python"

pytestmark = pytest.mark.skipif(torch is None, reason="torch 未安装")


def _load_models_module():
    """按文件路径加载 experiments/models.py，避免 research/models 包名遮蔽。"""
    spec = importlib.util.spec_from_file_location(
        "lingjing_ltc_models_degrade_test", EXPERIMENTS_DIR / "models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_cell(models, input_size=3, hidden_size=4):
    cell = models.LTCCell(input_size, hidden_size)
    cell.eval()
    return cell


def _euler_expected(cell, x, h, dt):
    """与 LTCCell._euler_step 相同的参考计算（独立写一遍以防实现相互掩盖）。"""
    with torch.no_grad():
        tau = torch.clamp(cell.ode_func.tau, min=0.01)
        dh = torch.tanh(
            torch.mm(x, cell.ode_func.W.t())
            + torch.mm(h, cell.ode_func.U.t())
            + cell.ode_func.bias
        )
        return h + dt * (dh - h) / tau.unsqueeze(0)


@pytest.fixture(autouse=True)
def _reset_degrade_flag():
    """类级告警标志在用例间复位，保证 warn-once 断言互不污染。"""
    models_mod = _load_models_module()
    saved = models_mod.LTCCell._degrade_warned
    models_mod.LTCCell._degrade_warned = False
    yield
    models_mod.LTCCell._degrade_warned = saved


class TestOdeintDegradation:
    def test_normal_solve_passes_through(self):
        models = _load_models_module()
        cell = _make_cell(models)
        x = torch.rand(2, 3)
        h = torch.rand(2, 4)
        # 有限解原样通过，不做任何替换
        fake_traj = torch.stack([h, h + 0.5])
        with patch.object(models, "_HAS_TORCHDIFFEQ", True), patch.object(
            models, "_torchdiffeq_odeint", return_value=fake_traj
        ):
            out = cell(x, h, dt=0.1)
        assert torch.allclose(out, h + 0.5)

    def test_solver_exception_degrades_to_euler(self):
        models = _load_models_module()
        cell = _make_cell(models)
        x = torch.rand(2, 3)
        h = torch.rand(2, 4)
        with patch.object(models, "_HAS_TORCHDIFFEQ", True), patch.object(
            models,
            "_torchdiffeq_odeint",
            side_effect=RuntimeError("underflow in dt nan"),
        ):
            with pytest.warns(RuntimeWarning, match="降级"):
                out = cell(x, h, dt=0.1)
        assert torch.isfinite(out).all()
        assert torch.allclose(out, _euler_expected(cell, x, h, 0.1))

    def test_nan_output_degrades_to_euler(self):
        models = _load_models_module()
        cell = _make_cell(models)
        x = torch.rand(2, 3)
        h = torch.rand(2, 4)
        nan_traj = torch.full((2, 2, 4), float("nan"))
        with patch.object(models, "_HAS_TORCHDIFFEQ", True), patch.object(
            models, "_torchdiffeq_odeint", return_value=nan_traj
        ):
            with pytest.warns(RuntimeWarning, match="非有限值"):
                out = cell(x, h, dt=0.1)
        assert torch.isfinite(out).all()
        assert torch.allclose(out, _euler_expected(cell, x, h, 0.1))

    def test_degrade_warning_emitted_once(self):
        models = _load_models_module()
        cell = _make_cell(models)
        x = torch.rand(2, 3)
        h = torch.rand(2, 4)
        with patch.object(models, "_HAS_TORCHDIFFEQ", True), patch.object(
            models,
            "_torchdiffeq_odeint",
            side_effect=RuntimeError("underflow in dt nan"),
        ) as mock_ode:
            with pytest.warns(RuntimeWarning):
                cell(x, h, dt=0.1)
            # 第二次失败不再刷告警，但仍正确降级
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                out = cell(x, h, dt=0.1)
            assert len(caught) == 0
        assert torch.isfinite(out).all()

    def test_locks_to_euler_after_repeated_failures(self):
        models = _load_models_module()
        cell = _make_cell(models)
        x = torch.rand(2, 3)
        h = torch.rand(2, 4)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with patch.object(models, "_HAS_TORCHDIFFEQ", True), patch.object(
                models,
                "_torchdiffeq_odeint",
                side_effect=RuntimeError("underflow in dt nan"),
            ) as mock_ode:
                for _ in range(models.LTCCell._max_ode_failures):
                    out = cell(x, h, dt=0.1)
                # 达到失败上限后不再尝试 odeint（消除逐步异常开销），直接 Euler
                calls_after_lock = mock_ode.call_count
                out_locked = cell(x, h, dt=0.1)
        assert calls_after_lock == models.LTCCell._max_ode_failures
        assert mock_ode.call_count == models.LTCCell._max_ode_failures
        assert torch.isfinite(out_locked).all()
        assert torch.allclose(out_locked, _euler_expected(cell, x, h, 0.1))

    def test_without_torchdiffeq_uses_euler_directly(self):
        models = _load_models_module()
        cell = _make_cell(models)
        x = torch.rand(2, 3)
        h = torch.rand(2, 4)
        with patch.object(models, "_HAS_TORCHDIFFEQ", False):
            out = cell(x, h, dt=0.1)
        assert torch.allclose(out, _euler_expected(cell, x, h, 0.1))


class TestTrainerGradClip:
    def test_trainers_default_grad_clip(self):
        sys.path.insert(0, str(ENGINEERING_PY))
        sys.path.insert(0, str(RESEARCH_DIR))
        sys.path.insert(0, str(EXPERIMENTS_DIR))
        # experiments/trainer.py 顶层 `from models import create_model` 依赖
        # sys.modules['models'] 指向 experiments/models.py;但先跑的测试
        # (如 test_lnn_convergence 的 `from models.xxx import`)会把 research
        # 的 models 包缓存成 'models'。导入前摘掉这些名字,导完恢复。
        saved_modules = {
            name: sys.modules.pop(name)
            for name in ("models", "trainer", "config")
            if name in sys.modules
        }
        try:
            import models as experiments_models  # noqa: F401 先缓存，模拟脚本导入顺序
            from config import ExperimentConfig
            from trainer import BaselineTrainer, DLLNNTrainer
        finally:
            for p in (str(EXPERIMENTS_DIR), str(RESEARCH_DIR), str(ENGINEERING_PY)):
                if p in sys.path:
                    sys.path.remove(p)
            for name, module in saved_modules.items():
                sys.modules.setdefault(name, module)

        config = ExperimentConfig()
        dllnn = DLLNNTrainer(config, device="cpu")
        baseline = BaselineTrainer("LSTM", config, device="cpu")
        assert dllnn.grad_clip_norm > 0
        assert baseline.grad_clip_norm > 0

        # 裁剪实际生效：人为制造大梯度后范数应被压到阈值附近
        loss = sum(p.sum() * 1e6 for p in dllnn.model.parameters() if p.requires_grad)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(dllnn.model.parameters(), dllnn.grad_clip_norm)
        total_norm = float(
            torch.norm(
                torch.stack(
                    [
                        p.grad.norm()
                        for p in dllnn.model.parameters()
                        if p.grad is not None
                    ]
                )
            )
        )
        assert np.isfinite(total_norm)
        assert total_norm <= dllnn.grad_clip_norm * 1.01
