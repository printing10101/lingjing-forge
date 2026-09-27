"""RL 权重真实加载与训练启动诚实化的回归测试.

背景（空壳修复 2026-09）：
- 此前 ``_load_weights`` 解析到权重路径后只打 debug 日志、从不加载，
  推理永远使用随机初始化权重却对外呈现"决策完成"；
- 此前 ``RLAgentService.start_training`` 只创建 status=RUNNING 的记录、
  无任何训练 worker，状态永远悬挂在 RUNNING。

本文件锁定修复后的行为：
- 权重加载失败必须返回 False（并由 policy_info.weights_loaded 透出），
  不允许静默冒充；
- npz 权重必须真实写入网络属性，形状不匹配必须拒绝；
- ``start_training`` 在触碰数据库之前即抛 ``TrainingError``。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.contracts.rl_agent import (
    OptimizationTarget,
    PolicyAlgorithm,
    PolicyInfo,
    TrainingError,
    TrainingStartRequest,
)
from app.services.rl_agent_service import RLAgentService


# ── 测试替身 ──────────────────────────────────────────────────────


class _FakeEntry:
    """LNNModelRegistry 条目替身：仅暴露 storage_uri / info 接口."""

    def __init__(self, storage_uri: str | None):
        self.storage_uri = storage_uri
        self.info = None


class _FakeRegistry:
    """LNNModelRegistry 替身：entry=None 时模拟"注册表无记录"."""

    def __init__(self, entry: _FakeEntry | None):
        self._entry = entry

    def get(self, model_uri: str) -> _FakeEntry:
        if self._entry is None:
            raise KeyError(model_uri)
        return self._entry


class _NumpyLikeNet:
    """带 NumPy 权重属性的哑网络（模拟 policy.py 的 NumPy 回退实现）."""

    def __init__(self) -> None:
        self._w1 = np.zeros((4, 4), dtype=np.float32)
        self._b1 = np.zeros(4, dtype=np.float32)


@pytest.fixture
def patch_registry(monkeypatch):
    """把 _load_weights 内部延迟导入的 LNNModelRegistry 替换为可控替身."""

    def _patch(entry: _FakeEntry | None) -> None:
        import app.ai.lnn.inference.registry as registry_module

        monkeypatch.setattr(registry_module, "LNNModelRegistry", lambda: _FakeRegistry(entry))

    return _patch


# ── _load_weights：诚实返回加载结果 ─────────────────────────────────


class TestLoadWeightsHonesty:
    def test_returns_false_when_registry_has_no_entry(self, patch_registry, monkeypatch):
        patch_registry(None)
        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://missing", kind="policy") is False

    def test_returns_false_when_no_storage_uri(self, patch_registry, monkeypatch):
        patch_registry(_FakeEntry(storage_uri=None))
        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://entry-without-uri", kind="policy") is False

    def test_returns_false_for_unsupported_scheme(self, patch_registry, monkeypatch):
        patch_registry(_FakeEntry(storage_uri="s3://bucket/model.pt"))
        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://remote", kind="value") is False

    def test_returns_false_when_file_missing(self, patch_registry, monkeypatch, tmp_path):
        patch_registry(_FakeEntry(storage_uri=str(tmp_path / "nope.pt")))
        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://gone", kind="policy") is False


# ── _load_weights：npz 真实加载 ────────────────────────────────────


class TestNpzWeightLoading:
    def _write_npz(self, path, arrays: dict[str, np.ndarray]) -> None:
        np.savez(path, **arrays)

    def test_npz_weights_are_actually_applied(self, patch_registry, monkeypatch, tmp_path):
        weights = {
            "_w1": np.ones((4, 4), dtype=np.float32),
            "_b1": np.full(4, 0.5, dtype=np.float32),
        }
        weight_file = tmp_path / "policy.npz"
        self._write_npz(weight_file, weights)
        patch_registry(_FakeEntry(storage_uri=str(weight_file)))

        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://npz-model", kind="policy") is True
        # 关键断言：权重确实写入了网络（此前实现只打日志、不写入）
        assert np.allclose(net._w1, weights["_w1"])
        assert np.allclose(net._b1, weights["_b1"])

    def test_npz_shape_mismatch_returns_false(self, patch_registry, monkeypatch, tmp_path):
        weight_file = tmp_path / "bad_shape.npz"
        self._write_npz(weight_file, {"_w1": np.ones((3, 4), dtype=np.float32)})
        patch_registry(_FakeEntry(storage_uri=str(weight_file)))

        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://bad-shape", kind="policy") is False

    def test_npz_without_known_keys_returns_false(self, patch_registry, monkeypatch, tmp_path):
        weight_file = tmp_path / "unknown_keys.npz"
        self._write_npz(weight_file, {"unrelated": np.ones(2, dtype=np.float32)})
        patch_registry(_FakeEntry(storage_uri=str(weight_file)))

        from app.services._agent_helpers import _load_weights

        net = _NumpyLikeNet()
        assert _load_weights(net, "memory://unknown-keys", kind="policy") is False


# ── 真实 PolicyNet（NumPy 回退模式）往返 ───────────────────────────


class TestRealPolicyNetRoundtrip:
    def test_numpy_policy_net_roundtrip(self, patch_registry, monkeypatch, tmp_path):
        """torch 不可用（NumPy 回退）时：npz 往返必须恢复一致权重."""
        from app.plugins.rl_agent.policy import PolicyConfig, PolicyNet

        net = PolicyNet(PolicyConfig())
        if not hasattr(net, "_w1"):
            pytest.skip("torch 可用，PolicyNet 为 torch 实现，npz 往返不适用")

        weight_file = tmp_path / "roundtrip.npz"
        perturbed = {
            "_w1": net._w1 + np.float32(0.123),
            "_b1": net._b1 + np.float32(0.456),
        }
        np.savez(weight_file, **perturbed)
        patch_registry(_FakeEntry(storage_uri=str(weight_file)))

        from app.services._agent_helpers import _load_weights

        assert _load_weights(net, "memory://roundtrip", kind="policy") is True
        assert np.allclose(net._w1, perturbed["_w1"])
        assert np.allclose(net._b1, perturbed["_b1"])


# ── start_training：训练循环未接线时显式拒绝 ────────────────────────


class TestStartTrainingRefusal:
    @pytest.mark.asyncio
    async def test_start_training_refuses_before_touching_db(self, monkeypatch, tmp_path):
        """训练数据缺失必须显式 TrainingError，且不创建假 RUNNING 记录.

        回归测试（空壳修复）：此前只写一条 RUNNING 记录即返回"训练已启动"，
        状态永远悬挂。修复后：数据校验在触碰数据库之前执行，缺失即拒绝，
        绝不创建无 worker 支撑的假记录。
        """
        monkeypatch.setenv("RL_AGENT_TRAINING_DATA", str(tmp_path / "missing.jsonl"))
        service = RLAgentService()

        async def _fail_session():
            raise AssertionError("训练数据缺失时不得访问数据库")

        service._get_session = _fail_session
        request = TrainingStartRequest(
            max_steps=100,
            seed=0,
            algorithm=PolicyAlgorithm.PPO,
            optimization_target=OptimizationTarget.BALANCE,
        )
        with pytest.raises(TrainingError) as exc_info:
            await service.start_training(request)
        assert "训练数据不可用" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_refusal_message_mentions_weights_loaded_hint(self, monkeypatch, tmp_path):
        """错误信息应给出可操作指引（离线推理 + weights_loaded 标记）."""
        monkeypatch.setenv("RL_AGENT_TRAINING_DATA", str(tmp_path / "missing.jsonl"))
        service = RLAgentService()
        request = TrainingStartRequest()
        with pytest.raises(TrainingError) as exc_info:
            await service.start_training(request)
        assert "weights_loaded" in str(exc_info.value)


# ── start_training：数据就绪时的真实接线 ───────────────────────────


class _FakeBeginCM:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


class _FakeSession:
    """AsyncSession 替身：捕获 add 的 ORM 并在 flush 时分配 id."""

    def __init__(self):
        self.added: list = []
        self.committed = False
        self.closed = False

    def begin(self):
        return _FakeBeginCM()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.added[-1].id = "rltr_test_1"

    async def commit(self):
        self.committed = True

    async def close(self):
        self.closed = True


class _FakeThread:
    """线程替身：只捕获启动参数，不真正执行训练循环."""

    last: "_FakeThread | None" = None

    def __init__(self, target=None, args=(), daemon=None, name=None, kwargs=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        _FakeThread.last = self

    def start(self):
        pass


class _ShimThreading:
    """替身 threading 模块（仅 Thread 被服务层在 start_training 中使用）."""

    Thread = _FakeThread


class TestStartTrainingWiring:
    @pytest.mark.asyncio
    async def test_start_training_creates_running_record_and_thread(self, monkeypatch, tmp_path):
        """提供真实轨迹数据集时：创建 RUNNING 记录并启动训练线程（接线回归）.

        此前该端点是空壳（恒拒绝或假 RUNNING）；修复后数据就绪即真实启动
        PPO 训练线程。本测试用替身隔离 DB 与线程，锁定接线顺序。
        """
        import json as _json

        import app.services.rl_agent_service as svc_module
        from app.contracts.world_model import ActionField, StateField

        state_dim = len(StateField.all())
        action_dim = len(ActionField.all())
        state = [0.1] * state_dim
        next_state = [0.2] * state_dim
        action = [0.0] * action_dim
        data_file = tmp_path / "trajectories.jsonl"
        data_file.write_text(
            _json.dumps({"state": state, "action": action, "next_state": next_state}),
            encoding="utf-8",
        )
        monkeypatch.setenv("RL_AGENT_TRAINING_DATA", str(data_file))

        service = RLAgentService()
        fake_session = _FakeSession()

        async def _fake_get_session():
            return fake_session

        service._get_session = _fake_get_session
        monkeypatch.setattr(svc_module, "threading", _ShimThreading)

        request = TrainingStartRequest(
            max_steps=10,
            seed=0,
            algorithm=PolicyAlgorithm.PPO,
            optimization_target=OptimizationTarget.BALANCE,
        )
        info = await service.start_training(request)

        # RUNNING 记录已创建且持久化
        assert fake_session.committed
        run_orm = fake_session.added[0]
        assert run_orm.status == "running"
        assert run_orm.total_steps_target == 10
        # 返回 RUNNING 状态信息
        assert info.status == "running"
        assert info.max_steps == 10
        # 训练线程已就绪（target 为 _training_worker，携带 run_id 与 trainer）
        assert _FakeThread.last is not None
        assert _FakeThread.last.target.__name__ == "_training_worker"
        assert _FakeThread.last.args[0] == "rltr_test_1"
        assert _FakeThread.last.daemon is True
        # 清理活跃训练注册表（线程替身未执行 unregister）
        service._unregister_active_trainer("rltr_test_1")


# ── PolicyInfo：weights_loaded 契约字段 ────────────────────────────


class TestPolicyInfoWeightsLoaded:
    def test_defaults_to_untrained(self):
        info = PolicyInfo(
            algorithm=PolicyAlgorithm.PPO,
            policy_version="1.0.0",
            training_episodes=0,
            exploration_rate=0.1,
        )
        assert info.weights_loaded is False

    def test_to_dict_contains_weights_loaded(self):
        info = PolicyInfo(
            algorithm=PolicyAlgorithm.PPO,
            policy_version="1.0.0",
            training_episodes=3,
            exploration_rate=0.0,
            weights_loaded=True,
        )
        assert info.to_dict()["weights_loaded"] is True

    def test_default_roundtrip_is_untrained(self):
        info = PolicyInfo(
            algorithm=PolicyAlgorithm.PPO,
            policy_version="1.0.0",
            training_episodes=0,
            exploration_rate=0.5,
        )
        assert info.to_dict()["weights_loaded"] is False
