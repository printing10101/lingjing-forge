"""世界模型契约单元测试.

对应 ADR-017 第 1 节 / app/contracts/world_model.py.

覆盖：
- 模块级常量（WM_PREDICT_STATE_TASK_TYPE / DEFAULT_STATE_DIM / DEFAULT_ACTION_DIM /
  DEFAULT_HORIZON / MAX_HORIZON / MIN_HORIZON）
- StateField / ActionField 字段标签常量
- WorldModelPredictRequest（current_state / candidate_action / horizon / model_uri 校验）
- TrajectoryStep（step / chatter_probability / confidence / tool_wear_increment /
  surface_roughness 数值边界 + to_dict）
- TrajectoryMetrics（mean/max_chatter_probability / cumulative_tool_wear /
  final_surface_roughness 数值边界 + to_dict）
- WorldModelInfo（world_model_version / training_data_size / prediction_horizon /
  uncertainty_estimate 校验 + to_dict）
- WorldModelPredictResponse（predicted_trajectory 非空 + 嵌套 to_dict）
- WorldModelVersion（to_dict 含 isoformat）
- 异常层级（WorldModelError → PredictionError / ModelNotFoundError / InvalidStateError）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.contracts.world_model import (
    DEFAULT_ACTION_DIM,
    DEFAULT_HORIZON,
    DEFAULT_STATE_DIM,
    MAX_HORIZON,
    MIN_HORIZON,
    WM_PREDICT_STATE_TASK_TYPE,
    ActionField,
    InvalidStateError,
    ModelNotFoundError,
    PredictionError,
    StateField,
    TrajectoryMetrics,
    TrajectoryStep,
    WorldModelError,
    WorldModelInfo,
    WorldModelPredictRequest,
    WorldModelPredictResponse,
    WorldModelVersion,
)


@pytest.mark.unit
@pytest.mark.contracts
class TestModuleConstants:
    """模块级常量."""

    def test_wm_predict_state_task_type(self):
        assert WM_PREDICT_STATE_TASK_TYPE == "wm_predict_state"

    def test_default_state_dim(self):
        assert DEFAULT_STATE_DIM == 8

    def test_default_action_dim(self):
        assert DEFAULT_ACTION_DIM == 4

    def test_default_horizon(self):
        assert DEFAULT_HORIZON == 10

    def test_max_horizon(self):
        assert MAX_HORIZON == 100

    def test_min_horizon(self):
        assert MIN_HORIZON == 1

    def test_horizon_range_consistency(self):
        """MIN_HORIZON < DEFAULT_HORIZON < MAX_HORIZON."""
        assert MIN_HORIZON < DEFAULT_HORIZON < MAX_HORIZON


@pytest.mark.unit
@pytest.mark.contracts
class TestStateField:
    """StateField 字段标签常量."""

    def test_field_values(self):
        assert StateField.SPINDLE_SPEED == "spindle_speed"
        assert StateField.FEED_RATE == "feed_rate"
        assert StateField.DEPTH_OF_CUT == "depth_of_cut"
        assert StateField.WIDTH_OF_CUT == "width_of_cut"
        assert StateField.TOOL_WEAR == "tool_wear"
        assert StateField.VIBRATION_RMS == "vibration_rms"
        assert StateField.TEMPERATURE == "temperature"
        assert StateField.CHATTER_PROBABILITY == "chatter_probability"

    def test_all_returns_eight_fields(self):
        """all() 返回 8 个状态字段，与 DEFAULT_STATE_DIM 一致."""
        fields = StateField.all()
        assert len(fields) == 8
        assert len(fields) == DEFAULT_STATE_DIM

    def test_all_no_duplicates(self):
        fields = StateField.all()
        assert len(fields) == len(set(fields))


@pytest.mark.unit
@pytest.mark.contracts
class TestActionField:
    """ActionField 字段标签常量."""

    def test_field_values(self):
        assert ActionField.SPINDLE_SPEED_DELTA == "spindle_speed_delta"
        assert ActionField.FEED_RATE_DELTA == "feed_rate_delta"
        assert ActionField.DEPTH_OF_CUT_DELTA == "depth_of_cut_delta"
        assert ActionField.WIDTH_OF_CUT_DELTA == "width_of_cut_delta"

    def test_all_returns_four_fields(self):
        """all() 返回 4 个动作字段，与 DEFAULT_ACTION_DIM 一致."""
        fields = ActionField.all()
        assert len(fields) == 4
        assert len(fields) == DEFAULT_ACTION_DIM

    def test_all_no_duplicates(self):
        fields = ActionField.all()
        assert len(fields) == len(set(fields))


@pytest.mark.unit
@pytest.mark.contracts
class TestWorldModelPredictRequest:
    """WorldModelPredictRequest dataclass 构造校验."""

    def _make_request(self, **overrides) -> WorldModelPredictRequest:
        defaults: dict[str, Any] = dict(
            current_state={StateField.SPINDLE_SPEED: 8000.0},
            candidate_action={ActionField.SPINDLE_SPEED_DELTA: 0.1},
            horizon=DEFAULT_HORIZON,
            model_uri="model://world_model/1.0.0",
        )
        defaults.update(overrides)
        return WorldModelPredictRequest(**defaults)

    def test_valid_request(self):
        req = self._make_request()
        assert req.horizon == DEFAULT_HORIZON
        assert req.model_uri == "model://world_model/1.0.0"

    def test_default_horizon(self):
        """horizon 默认为 DEFAULT_HORIZON."""
        req = WorldModelPredictRequest(
            current_state={"a": 1.0},
            candidate_action={"b": 0.1},
        )
        assert req.horizon == DEFAULT_HORIZON

    def test_default_model_uri(self):
        req = WorldModelPredictRequest(
            current_state={"a": 1.0},
            candidate_action={"b": 0.1},
        )
        assert req.model_uri == "model://world_model/1.0.0"

    def test_empty_current_state_rejected(self):
        with pytest.raises(ValueError, match="current_state"):
            self._make_request(current_state={})

    def test_empty_current_state_allowed_with_unified_state(self):
        """ADR-020 思路 1：融合模式下 current_state 可为空（由 unified_state 提供）。"""
        req = self._make_request(
            current_state={},
            unified_state={"geometry": {}, "dynamics": {}},
        )
        assert req.unified_state is not None
        assert req.current_state == {}

    def test_empty_candidate_action_rejected(self):
        with pytest.raises(ValueError, match="candidate_action"):
            self._make_request(candidate_action={})

    def test_empty_model_uri_rejected(self):
        with pytest.raises(ValueError, match="model_uri"):
            self._make_request(model_uri="")

    @pytest.mark.parametrize("horizon", [MIN_HORIZON, MAX_HORIZON, 50])
    def test_valid_horizon_boundaries(self, horizon: int):
        req = self._make_request(horizon=horizon)
        assert req.horizon == horizon

    @pytest.mark.parametrize("horizon", [MIN_HORIZON - 1, MAX_HORIZON + 1, 0, -1])
    def test_invalid_horizon_rejected(self, horizon: int):
        with pytest.raises(ValueError, match="horizon"):
            self._make_request(horizon=horizon)


@pytest.mark.unit
@pytest.mark.contracts
class TestTrajectoryStep:
    """TrajectoryStep dataclass 构造校验."""

    def _make_step(self, **overrides) -> TrajectoryStep:
        defaults: dict[str, Any] = dict(
            step=0,
            predicted_state={StateField.CHATTER_PROBABILITY: 0.1},
            chatter_probability=0.1,
            tool_wear_increment=0.001,
            surface_roughness=0.5,
            confidence=0.9,
        )
        defaults.update(overrides)
        return TrajectoryStep(**defaults)

    def test_valid_step(self):
        step = self._make_step()
        assert step.step == 0
        assert step.confidence == 0.9

    @pytest.mark.parametrize("step", [0, 1, 100])
    def test_valid_step_indices(self, step: int):
        s = self._make_step(step=step)
        assert s.step == step

    @pytest.mark.parametrize("step", [-1, -100])
    def test_negative_step_rejected(self, step: int):
        with pytest.raises(ValueError, match="step"):
            self._make_step(step=step)

    @pytest.mark.parametrize("prob", [0.0, 0.5, 1.0])
    def test_valid_chatter_probability(self, prob: float):
        s = self._make_step(chatter_probability=prob)
        assert s.chatter_probability == prob

    @pytest.mark.parametrize("prob", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_chatter_probability_rejected(self, prob: float):
        with pytest.raises(ValueError, match="chatter_probability"):
            self._make_step(chatter_probability=prob)

    @pytest.mark.parametrize("conf", [0.0, 0.5, 1.0])
    def test_valid_confidence(self, conf: float):
        s = self._make_step(confidence=conf)
        assert s.confidence == conf

    @pytest.mark.parametrize("conf", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_confidence_rejected(self, conf: float):
        with pytest.raises(ValueError, match="confidence"):
            self._make_step(confidence=conf)

    @pytest.mark.parametrize("wear", [0.0, 0.001, 1.0])
    def test_valid_tool_wear_increment(self, wear: float):
        s = self._make_step(tool_wear_increment=wear)
        assert s.tool_wear_increment == wear

    @pytest.mark.parametrize("wear", [-0.001, -1.0])
    def test_negative_tool_wear_increment_rejected(self, wear: float):
        with pytest.raises(ValueError, match="tool_wear_increment"):
            self._make_step(tool_wear_increment=wear)

    @pytest.mark.parametrize("roughness", [0.0, 0.5, 5.0])
    def test_valid_surface_roughness(self, roughness: float):
        s = self._make_step(surface_roughness=roughness)
        assert s.surface_roughness == roughness

    @pytest.mark.parametrize("roughness", [-0.001, -1.0])
    def test_negative_surface_roughness_rejected(self, roughness: float):
        with pytest.raises(ValueError, match="surface_roughness"):
            self._make_step(surface_roughness=roughness)

    def test_to_dict_keys(self):
        step = self._make_step()
        d = step.to_dict()
        assert set(d.keys()) == {
            "step",
            "predicted_state",
            "chatter_probability",
            "tool_wear_increment",
            "surface_roughness",
            "confidence",
        }

    def test_to_dict_values(self):
        step = self._make_step(step=5, confidence=0.7)
        d = step.to_dict()
        assert d["step"] == 5
        assert d["confidence"] == 0.7


@pytest.mark.unit
@pytest.mark.contracts
class TestTrajectoryMetrics:
    """TrajectoryMetrics dataclass 构造校验."""

    def _make_metrics(self, **overrides) -> TrajectoryMetrics:
        defaults: dict[str, Any] = dict(
            mean_chatter_probability=0.1,
            max_chatter_probability=0.3,
            cumulative_tool_wear=0.05,
            final_surface_roughness=0.8,
        )
        defaults.update(overrides)
        return TrajectoryMetrics(**defaults)

    def test_valid_metrics(self):
        m = self._make_metrics()
        assert m.max_chatter_probability == 0.3

    @pytest.mark.parametrize("v", [0.0, 0.5, 1.0])
    def test_valid_mean_chatter_probability(self, v: float):
        assert self._make_metrics(mean_chatter_probability=v).mean_chatter_probability == v

    @pytest.mark.parametrize("v", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_mean_chatter_probability_rejected(self, v: float):
        with pytest.raises(ValueError, match="mean_chatter_probability"):
            self._make_metrics(mean_chatter_probability=v)

    @pytest.mark.parametrize("v", [0.0, 0.5, 1.0])
    def test_valid_max_chatter_probability(self, v: float):
        assert self._make_metrics(max_chatter_probability=v).max_chatter_probability == v

    @pytest.mark.parametrize("v", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_max_chatter_probability_rejected(self, v: float):
        with pytest.raises(ValueError, match="max_chatter_probability"):
            self._make_metrics(max_chatter_probability=v)

    @pytest.mark.parametrize("v", [0.0, 0.05, 1.0])
    def test_valid_cumulative_tool_wear(self, v: float):
        assert self._make_metrics(cumulative_tool_wear=v).cumulative_tool_wear == v

    @pytest.mark.parametrize("v", [-0.001, -1.0])
    def test_negative_cumulative_tool_wear_rejected(self, v: float):
        with pytest.raises(ValueError, match="cumulative_tool_wear"):
            self._make_metrics(cumulative_tool_wear=v)

    @pytest.mark.parametrize("v", [0.0, 0.8, 5.0])
    def test_valid_final_surface_roughness(self, v: float):
        assert self._make_metrics(final_surface_roughness=v).final_surface_roughness == v

    @pytest.mark.parametrize("v", [-0.001, -1.0])
    def test_negative_final_surface_roughness_rejected(self, v: float):
        with pytest.raises(ValueError, match="final_surface_roughness"):
            self._make_metrics(final_surface_roughness=v)

    def test_to_dict_keys(self):
        d = self._make_metrics().to_dict()
        assert set(d.keys()) == {
            "mean_chatter_probability",
            "max_chatter_probability",
            "cumulative_tool_wear",
            "final_surface_roughness",
        }


@pytest.mark.unit
@pytest.mark.contracts
class TestWorldModelInfo:
    """WorldModelInfo dataclass 构造校验."""

    def _make_info(self, **overrides) -> WorldModelInfo:
        defaults: dict[str, Any] = dict(
            world_model_version="1.0.0",
            training_data_size=10000,
            prediction_horizon=DEFAULT_HORIZON,
            uncertainty_estimate=0.05,
        )
        defaults.update(overrides)
        return WorldModelInfo(**defaults)

    def test_valid_info(self):
        info = self._make_info()
        assert info.world_model_version == "1.0.0"

    def test_empty_version_rejected(self):
        with pytest.raises(ValueError, match="world_model_version"):
            self._make_info(world_model_version="")

    @pytest.mark.parametrize("v", [0, 1, 10000])
    def test_valid_training_data_size(self, v: int):
        assert self._make_info(training_data_size=v).training_data_size == v

    @pytest.mark.parametrize("v", [-1, -100])
    def test_negative_training_data_size_rejected(self, v: int):
        with pytest.raises(ValueError, match="training_data_size"):
            self._make_info(training_data_size=v)

    @pytest.mark.parametrize("v", [MIN_HORIZON, MAX_HORIZON, 50])
    def test_valid_prediction_horizon(self, v: int):
        assert self._make_info(prediction_horizon=v).prediction_horizon == v

    @pytest.mark.parametrize("v", [MIN_HORIZON - 1, MAX_HORIZON + 1, 0, -1])
    def test_invalid_prediction_horizon_rejected(self, v: int):
        with pytest.raises(ValueError, match="prediction_horizon"):
            self._make_info(prediction_horizon=v)

    @pytest.mark.parametrize("v", [0.0, 0.5, 1.0])
    def test_valid_uncertainty_estimate(self, v: float):
        assert self._make_info(uncertainty_estimate=v).uncertainty_estimate == v

    @pytest.mark.parametrize("v", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_uncertainty_estimate_rejected(self, v: float):
        with pytest.raises(ValueError, match="uncertainty_estimate"):
            self._make_info(uncertainty_estimate=v)

    def test_to_dict_keys(self):
        d = self._make_info().to_dict()
        assert set(d.keys()) == {
            "world_model_version",
            "training_data_size",
            "prediction_horizon",
            "uncertainty_estimate",
        }


@pytest.mark.unit
@pytest.mark.contracts
class TestWorldModelPredictResponse:
    """WorldModelPredictResponse dataclass 构造校验."""

    def _make_step(self, step: int = 0) -> TrajectoryStep:
        return TrajectoryStep(
            step=step,
            predicted_state={StateField.CHATTER_PROBABILITY: 0.1},
            chatter_probability=0.1,
            tool_wear_increment=0.001,
            surface_roughness=0.5,
            confidence=0.9,
        )

    def _make_metrics(self) -> TrajectoryMetrics:
        return TrajectoryMetrics(
            mean_chatter_probability=0.1,
            max_chatter_probability=0.3,
            cumulative_tool_wear=0.05,
            final_surface_roughness=0.8,
        )

    def _make_info(self) -> WorldModelInfo:
        return WorldModelInfo(
            world_model_version="1.0.0",
            training_data_size=10000,
            prediction_horizon=DEFAULT_HORIZON,
            uncertainty_estimate=0.05,
        )

    def _make_response(self, **overrides) -> WorldModelPredictResponse:
        defaults: dict[str, Any] = dict(
            predicted_trajectory=[self._make_step()],
            trajectory_metrics=self._make_metrics(),
            model_info=self._make_info(),
        )
        defaults.update(overrides)
        return WorldModelPredictResponse(**defaults)

    def test_valid_response(self):
        resp = self._make_response()
        assert len(resp.predicted_trajectory) == 1

    def test_empty_trajectory_rejected(self):
        with pytest.raises(ValueError, match="predicted_trajectory"):
            self._make_response(predicted_trajectory=[])

    def test_to_dict_nested_structure(self):
        """to_dict 递归调用 step/metrics/info 的 to_dict."""
        resp = self._make_response(
            predicted_trajectory=[self._make_step(0), self._make_step(1)],
        )
        d = resp.to_dict()
        assert set(d.keys()) == {
            "predicted_trajectory",
            "trajectory_metrics",
            "model_info",
        }
        # predicted_trajectory 是 list[dict]
        assert isinstance(d["predicted_trajectory"], list)
        assert len(d["predicted_trajectory"]) == 2
        assert isinstance(d["predicted_trajectory"][0], dict)
        assert d["predicted_trajectory"][0]["step"] == 0
        assert d["predicted_trajectory"][1]["step"] == 1
        # trajectory_metrics 是 dict
        assert isinstance(d["trajectory_metrics"], dict)
        assert "max_chatter_probability" in d["trajectory_metrics"]
        # model_info 是 dict
        assert isinstance(d["model_info"], dict)
        assert d["model_info"]["world_model_version"] == "1.0.0"


@pytest.mark.unit
@pytest.mark.contracts
class TestWorldModelVersion:
    """WorldModelVersion dataclass（无 __post_init__，仅 to_dict）."""

    def _make_version(self, **overrides) -> WorldModelVersion:
        defaults: dict[str, Any] = dict(
            version="1.0.0",
            model_uri="model://world_model/1.0.0",
            description="初始版本",
            created_at=datetime(2026, 7, 14, 12, 0, 0),
            training_data_size=10000,
            prediction_horizon=DEFAULT_HORIZON,
            is_active=True,
        )
        defaults.update(overrides)
        return WorldModelVersion(**defaults)

    def test_valid_version(self):
        v = self._make_version()
        assert v.version == "1.0.0"
        assert v.is_active is True

    def test_default_is_active_false(self):
        v = WorldModelVersion(
            version="1.0.0",
            model_uri="model://world_model/1.0.0",
            description="",
            created_at=datetime(2026, 7, 14),
            training_data_size=0,
            prediction_horizon=DEFAULT_HORIZON,
        )
        assert v.is_active is False

    def test_to_dict_isoformat(self):
        """to_dict 将 created_at 转为 isoformat 字符串."""
        v = self._make_version()
        d = v.to_dict()
        assert d["created_at"] == "2026-07-14T12:00:00"
        assert d["version"] == "1.0.0"
        assert d["is_active"] is True

    def test_to_dict_keys(self):
        d = self._make_version().to_dict()
        assert set(d.keys()) == {
            "version",
            "model_uri",
            "description",
            "created_at",
            "training_data_size",
            "prediction_horizon",
            "is_active",
        }


@pytest.mark.unit
@pytest.mark.contracts
class TestExceptions:
    """异常层级关系."""

    def test_prediction_error_is_world_model_error(self):
        assert issubclass(PredictionError, WorldModelError)

    def test_model_not_found_error_is_world_model_error(self):
        assert issubclass(ModelNotFoundError, WorldModelError)

    def test_invalid_state_error_is_world_model_error(self):
        assert issubclass(InvalidStateError, WorldModelError)

    def test_world_model_error_is_exception(self):
        assert issubclass(WorldModelError, Exception)

    def test_raise_prediction_error(self):
        with pytest.raises(PredictionError):
            raise PredictionError("network forward failed")

    def test_catch_subclass_as_base(self):
        """子类异常可被基类 except 捕获."""
        with pytest.raises(WorldModelError):
            raise PredictionError("network forward failed")

    def test_distinct_subclasses(self):
        """三个子类互不继承."""
        assert not issubclass(PredictionError, ModelNotFoundError)
        assert not issubclass(PredictionError, InvalidStateError)
        assert not issubclass(ModelNotFoundError, InvalidStateError)
