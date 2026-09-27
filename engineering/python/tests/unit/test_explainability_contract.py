"""可解释性可视化契约单元测试.

对应 ADR-016 / app/contracts/explainability.py.

覆盖：
- ExplanationType（4 值 + all / is_valid）
- ProjectionMethod（3 值 + all / is_valid / default）
- ComparisonType（3 值 + all / is_valid）
- HiddenStateExplanation（frame_ids 非空 / 4 个长度一致 / projection_method 合法 /
  projection_dim ∈ {2,3} / sample_count 一致 + to_payload）
- GateDynamicsExplanation（frame_ids 非空 / gate_values / time_constants 长度一致 + to_payload）
- CounterfactualExplanation（base_input 非空 / perturbed_feature 非空 /
  perturbation_range 与 outputs 长度一致 + to_payload）
- ConfidenceExplanation（sample_count > 0 / std / epistemic / aleatoric ≥ 0 + to_payload）
- ExplanationRequest（explanation_type 合法 / model_uri 非空 + input_signature 一致性/差异性/异常）
- ExplanationRecord（5 个边界 + to_dict isoformat + metadata_json 默认）
- ExplanationComparison（6 个边界含 base ≠ compared + to_dict）
- 6 个异常类继承关系 + code 属性 + explanation_id 属性
- IExplainabilityService ABC 不可实例化 + 子类化测试
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.contracts.explainability import (
    ComparisonMismatchError,
    ComparisonType,
    ConfidenceExplanation,
    CounterfactualExplanation,
    ExplanationComparison,
    ExplanationLookupError,
    ExplanationRecord,
    ExplanationRequest,
    ExplanationType,
    ExplanationValidationError,
    ExplainabilityError,
    GateDynamicsExplanation,
    HiddenStateExplanation,
    IExplainabilityService,
    ProjectionError,
    ProjectionMethod,
    SamplingError,
)


# ExplanationType


@pytest.mark.unit
@pytest.mark.contracts
class TestExplanationType:
    """ExplanationType 常量类."""

    def test_values(self):
        assert ExplanationType.HIDDEN_STATE == "hidden_state"
        assert ExplanationType.GATE_DYNAMICS == "gate_dynamics"
        assert ExplanationType.COUNTERFACTUAL == "counterfactual"
        assert ExplanationType.CONFIDENCE == "confidence"

    def test_all_returns_four(self):
        result = ExplanationType.all()
        assert len(result) == 4
        for t in (
            ExplanationType.HIDDEN_STATE,
            ExplanationType.GATE_DYNAMICS,
            ExplanationType.COUNTERFACTUAL,
            ExplanationType.CONFIDENCE,
        ):
            assert t in result

    def test_all_no_duplicates(self):
        result = ExplanationType.all()
        assert len(set(result)) == 4

    @pytest.mark.parametrize(
        "value",
        ["hidden_state", "gate_dynamics", "counterfactual", "confidence"],
    )
    def test_is_valid_true(self, value: str):
        assert ExplanationType.is_valid(value) is True

    @pytest.mark.parametrize("value", ["", "HIDDEN_STATE", "hidden", "gate", None])
    def test_is_valid_false(self, value: Any):
        assert ExplanationType.is_valid(value) is False  # type: ignore[arg-type]


# ProjectionMethod


@pytest.mark.unit
@pytest.mark.contracts
class TestProjectionMethod:
    """ProjectionMethod 常量类."""

    def test_values(self):
        assert ProjectionMethod.PCA == "pca"
        assert ProjectionMethod.TSNE == "tsne"
        assert ProjectionMethod.UMAP == "umap"

    def test_all_returns_three(self):
        result = ProjectionMethod.all()
        assert len(result) == 3
        for m in (ProjectionMethod.PCA, ProjectionMethod.TSNE, ProjectionMethod.UMAP):
            assert m in result

    def test_all_no_duplicates(self):
        result = ProjectionMethod.all()
        assert len(set(result)) == 3

    @pytest.mark.parametrize("value", ["pca", "tsne", "umap"])
    def test_is_valid_true(self, value: str):
        assert ProjectionMethod.is_valid(value) is True

    @pytest.mark.parametrize("value", ["", "PCA", "random", "tsne_v2", None])
    def test_is_valid_false(self, value: Any):
        assert ProjectionMethod.is_valid(value) is False  # type: ignore[arg-type]

    def test_default_is_pca(self):
        assert ProjectionMethod.default() == ProjectionMethod.PCA


# ComparisonType


@pytest.mark.unit
@pytest.mark.contracts
class TestComparisonType:
    """ComparisonType 常量类."""

    def test_values(self):
        assert ComparisonType.SAME_MODEL_DIFF_INPUT == "same_model_diff_input"
        assert ComparisonType.DIFF_MODEL_SAME_INPUT == "diff_model_same_input"
        assert ComparisonType.DIFF_MODEL_DIFF_INPUT == "diff_model_diff_input"

    def test_all_returns_three(self):
        result = ComparisonType.all()
        assert len(result) == 3
        for c in (
            ComparisonType.SAME_MODEL_DIFF_INPUT,
            ComparisonType.DIFF_MODEL_SAME_INPUT,
            ComparisonType.DIFF_MODEL_DIFF_INPUT,
        ):
            assert c in result

    def test_all_no_duplicates(self):
        result = ComparisonType.all()
        assert len(set(result)) == 3

    @pytest.mark.parametrize(
        "value",
        [
            "same_model_diff_input",
            "diff_model_same_input",
            "diff_model_diff_input",
        ],
    )
    def test_is_valid_true(self, value: str):
        assert ComparisonType.is_valid(value) is True

    @pytest.mark.parametrize("value", ["", "SAME", "same_model", "diff_model", None])
    def test_is_valid_false(self, value: Any):
        assert ComparisonType.is_valid(value) is False  # type: ignore[arg-type]


# HiddenStateExplanation


@pytest.mark.unit
@pytest.mark.contracts
class TestHiddenStateExplanation:
    """HiddenStateExplanation dataclass 构造校验."""

    def _make(self, **overrides) -> HiddenStateExplanation:
        defaults: dict[str, Any] = dict(
            frame_ids=[0, 1, 2],
            projections=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            energies=[0.5, 0.6, 0.7],
            keyframe_flags=[True, False, True],
            projection_method=ProjectionMethod.PCA,
            projection_dim=2,
            hidden_dim=64,
            sample_count=3,
            model_uri="model://ltc/1.0.0",
        )
        defaults.update(overrides)
        return HiddenStateExplanation(**defaults)

    def test_valid_explanation(self):
        exp = self._make()
        assert exp.frame_ids == [0, 1, 2]
        assert exp.projection_dim == 2

    def test_empty_frame_ids_rejected(self):
        with pytest.raises(ValueError, match="frame_ids"):
            self._make(
                frame_ids=[],
                projections=[],
                energies=[],
                keyframe_flags=[],
                sample_count=0,
            )

    def test_projections_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="projections"):
            self._make(
                frame_ids=[0, 1, 2],
                projections=[[0.1, 0.2], [0.3, 0.4]],  # 少一个
            )

    def test_energies_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="energies"):
            self._make(energies=[0.5, 0.6])  # 少一个

    def test_keyframe_flags_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="keyframe_flags"):
            self._make(keyframe_flags=[True, False])  # 少一个

    @pytest.mark.parametrize("method", ["pca", "tsne", "umap"])
    def test_valid_projection_method(self, method: str):
        exp = self._make(projection_method=method)
        assert exp.projection_method == method

    @pytest.mark.parametrize("method", ["", "PCA", "random", "lda"])
    def test_invalid_projection_method_rejected(self, method: str):
        with pytest.raises(ValueError, match="projection_method"):
            self._make(projection_method=method)

    @pytest.mark.parametrize("dim", [2, 3])
    def test_valid_projection_dim(self, dim: int):
        exp = self._make(projection_dim=dim)
        assert exp.projection_dim == dim

    @pytest.mark.parametrize("dim", [0, 1, 4, 10])
    def test_invalid_projection_dim_rejected(self, dim: int):
        with pytest.raises(ValueError, match="projection_dim"):
            self._make(projection_dim=dim)

    def test_sample_count_mismatch_rejected(self):
        with pytest.raises(ValueError, match="sample_count"):
            self._make(sample_count=10)  # 与 frame_ids 长度不一致

    def test_to_payload_keys(self):
        d = self._make().to_payload()
        assert set(d.keys()) == {
            "explanation_type",
            "frame_ids",
            "projections",
            "energies",
            "keyframe_flags",
            "projection_method",
            "projection_dim",
            "hidden_dim",
            "sample_count",
            "model_uri",
        }

    def test_to_payload_explanation_type(self):
        """to_payload 自动填充 explanation_type=hidden_state."""
        d = self._make().to_payload()
        assert d["explanation_type"] == ExplanationType.HIDDEN_STATE


# GateDynamicsExplanation


@pytest.mark.unit
@pytest.mark.contracts
class TestGateDynamicsExplanation:
    """GateDynamicsExplanation dataclass 构造校验."""

    def _make(self, **overrides) -> GateDynamicsExplanation:
        defaults: dict[str, Any] = dict(
            frame_ids=[0, 1, 2],
            gate_values=[[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            time_constants=[[1.0, 2.0], [1.5, 2.5], [1.2, 2.2]],
            mean_gate_per_feature=[0.3, 0.4],
            anomaly_frames=[2],
            model_uri="model://ltc/1.0.0",
        )
        defaults.update(overrides)
        return GateDynamicsExplanation(**defaults)

    def test_valid_explanation(self):
        exp = self._make()
        assert exp.frame_ids == [0, 1, 2]
        assert exp.anomaly_frames == [2]

    def test_empty_frame_ids_rejected(self):
        with pytest.raises(ValueError, match="frame_ids"):
            self._make(
                frame_ids=[],
                gate_values=[],
                time_constants=[],
            )

    def test_gate_values_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="gate_values"):
            self._make(gate_values=[[0.1, 0.2], [0.3, 0.4]])  # 少一个

    def test_time_constants_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="time_constants"):
            self._make(time_constants=[[1.0, 2.0], [1.5, 2.5]])  # 少一个

    def test_to_payload_keys(self):
        d = self._make().to_payload()
        assert set(d.keys()) == {
            "explanation_type",
            "frame_ids",
            "gate_values",
            "time_constants",
            "mean_gate_per_feature",
            "anomaly_frames",
            "model_uri",
        }

    def test_to_payload_explanation_type(self):
        d = self._make().to_payload()
        assert d["explanation_type"] == ExplanationType.GATE_DYNAMICS


# CounterfactualExplanation


@pytest.mark.unit
@pytest.mark.contracts
class TestCounterfactualExplanation:
    """CounterfactualExplanation dataclass 构造校验."""

    def _make(self, **overrides) -> CounterfactualExplanation:
        defaults: dict[str, Any] = dict(
            base_input={"spindle_speed": 8000.0, "feed_rate": 0.05},
            perturbed_feature="spindle_speed",
            perturbation_range=[-10.0, -5.0, 0.0, 5.0, 10.0],
            outputs=[0.1, 0.12, 0.15, 0.18, 0.22],
            sensitivity=0.006,
            critical_points=[
                {"perturbation": 5.0, "output": 0.18, "delta": 0.03},
            ],
            model_uri="model://ltc/1.0.0",
        )
        defaults.update(overrides)
        return CounterfactualExplanation(**defaults)

    def test_valid_explanation(self):
        exp = self._make()
        assert exp.perturbed_feature == "spindle_speed"
        assert len(exp.perturbation_range) == 5

    def test_empty_base_input_rejected(self):
        with pytest.raises(ValueError, match="base_input"):
            self._make(base_input={})

    def test_empty_perturbed_feature_rejected(self):
        with pytest.raises(ValueError, match="perturbed_feature"):
            self._make(perturbed_feature="")

    def test_range_outputs_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="perturbation_range"):
            self._make(
                perturbation_range=[-10.0, -5.0, 0.0, 5.0],  # 4 个
                outputs=[0.1, 0.12, 0.15, 0.18, 0.22],  # 5 个
            )

    def test_to_payload_keys(self):
        d = self._make().to_payload()
        assert set(d.keys()) == {
            "explanation_type",
            "base_input",
            "perturbed_feature",
            "perturbation_range",
            "outputs",
            "sensitivity",
            "critical_points",
            "model_uri",
        }

    def test_to_payload_explanation_type(self):
        d = self._make().to_payload()
        assert d["explanation_type"] == ExplanationType.COUNTERFACTUAL


# ConfidenceExplanation


@pytest.mark.unit
@pytest.mark.contracts
class TestConfidenceExplanation:
    """ConfidenceExplanation dataclass 构造校验."""

    def _make(self, **overrides) -> ConfidenceExplanation:
        defaults: dict[str, Any] = dict(
            sample_count=30,
            mean=0.15,
            std=0.03,
            percentiles={"p5": 0.10, "p50": 0.15, "p95": 0.20},
            histogram={"bins": [0.05, 0.15, 0.25], "counts": [5, 20, 5]},
            epistemic=0.02,
            aleatoric=0.01,
            anomaly_score=0.18,
            model_uri="model://ltc/1.0.0",
        )
        defaults.update(overrides)
        return ConfidenceExplanation(**defaults)

    def test_valid_explanation(self):
        exp = self._make()
        assert exp.sample_count == 30
        assert exp.mean == 0.15

    @pytest.mark.parametrize("count", [1, 10, 30, 100])
    def test_valid_sample_count(self, count: int):
        exp = self._make(sample_count=count)
        assert exp.sample_count == count

    @pytest.mark.parametrize("count", [0, -1, -10])
    def test_invalid_sample_count_rejected(self, count: int):
        with pytest.raises(ValueError, match="sample_count"):
            self._make(sample_count=count)

    @pytest.mark.parametrize("std", [0.0, 0.01, 0.5, 10.0])
    def test_valid_std(self, std: float):
        exp = self._make(std=std)
        assert exp.std == std

    @pytest.mark.parametrize("std", [-0.01, -0.5, -1.0])
    def test_invalid_std_rejected(self, std: float):
        with pytest.raises(ValueError, match="std"):
            self._make(std=std)

    @pytest.mark.parametrize("ep", [0.0, 0.01, 0.5])
    def test_valid_epistemic(self, ep: float):
        exp = self._make(epistemic=ep)
        assert exp.epistemic == ep

    @pytest.mark.parametrize("ep", [-0.01, -0.5])
    def test_invalid_epistemic_rejected(self, ep: float):
        with pytest.raises(ValueError, match="epistemic"):
            self._make(epistemic=ep)

    @pytest.mark.parametrize("al", [0.0, 0.01, 0.5])
    def test_valid_aleatoric(self, al: float):
        exp = self._make(aleatoric=al)
        assert exp.aleatoric == al

    @pytest.mark.parametrize("al", [-0.01, -0.5])
    def test_invalid_aleatoric_rejected(self, al: float):
        with pytest.raises(ValueError, match="aleatoric"):
            self._make(aleatoric=al)

    def test_to_payload_keys(self):
        d = self._make().to_payload()
        assert set(d.keys()) == {
            "explanation_type",
            "sample_count",
            "mean",
            "std",
            "percentiles",
            "histogram",
            "epistemic",
            "aleatoric",
            "anomaly_score",
            "model_uri",
        }

    def test_to_payload_explanation_type(self):
        d = self._make().to_payload()
        assert d["explanation_type"] == ExplanationType.CONFIDENCE


# ExplanationRequest


@pytest.mark.unit
@pytest.mark.contracts
class TestExplanationRequest:
    """ExplanationRequest dataclass 构造校验 + input_signature()."""

    def _make(self, **overrides) -> ExplanationRequest:
        defaults: dict[str, Any] = dict(
            explanation_type=ExplanationType.HIDDEN_STATE,
            model_uri="model://ltc/1.0.0",
        )
        defaults.update(overrides)
        return ExplanationRequest(**defaults)

    def test_valid_request(self):
        req = self._make()
        assert req.explanation_type == ExplanationType.HIDDEN_STATE
        assert req.model_uri == "model://ltc/1.0.0"

    def test_default_optional_fields(self):
        req = self._make()
        assert req.source_snapshot_id is None
        assert req.input_data is None
        assert req.options == {}
        assert req.created_by is None

    @pytest.mark.parametrize(
        "exp_type",
        ["hidden_state", "gate_dynamics", "counterfactual", "confidence"],
    )
    def test_valid_explanation_type(self, exp_type: str):
        req = self._make(explanation_type=exp_type)
        assert req.explanation_type == exp_type

    @pytest.mark.parametrize("exp_type", ["", "HIDDEN", "random", "hidden"])
    def test_invalid_explanation_type_rejected(self, exp_type: str):
        with pytest.raises(ValueError, match="explanation_type"):
            self._make(explanation_type=exp_type)

    def test_empty_model_uri_rejected(self):
        with pytest.raises(ValueError, match="model_uri"):
            self._make(model_uri="")

    def test_input_signature_consistency(self):
        """相同输入 + 相同模型 + 相同解释类型 → 相同签名."""
        req1 = self._make(
            input_data={"x": 1.0},
            options={"projection_dim": 2},
        )
        req2 = self._make(
            input_data={"x": 1.0},
            options={"projection_dim": 2},
        )
        assert req1.input_signature() == req2.input_signature()

    def test_input_signature_differs_on_input_data(self):
        """不同 input_data → 不同签名."""
        req1 = self._make(input_data={"x": 1.0})
        req2 = self._make(input_data={"x": 2.0})
        assert req1.input_signature() != req2.input_signature()

    def test_input_signature_differs_on_options(self):
        """不同 options → 不同签名."""
        req1 = self._make(options={"dim": 2})
        req2 = self._make(options={"dim": 3})
        assert req1.input_signature() != req2.input_signature()

    def test_input_signature_differs_on_model_uri(self):
        """不同 model_uri → 不同签名."""
        req1 = self._make(model_uri="model://ltc/1.0.0")
        req2 = self._make(model_uri="model://ltc/2.0.0")
        assert req1.input_signature() != req2.input_signature()

    def test_input_signature_differs_on_explanation_type(self):
        """不同 explanation_type → 不同签名."""
        req1 = self._make(explanation_type=ExplanationType.HIDDEN_STATE)
        req2 = self._make(explanation_type=ExplanationType.GATE_DYNAMICS)
        assert req1.input_signature() != req2.input_signature()

    def test_input_signature_length(self):
        """签名长度应为 16 字符（sha256 前 16 字符）."""
        sig = self._make().input_signature()
        assert len(sig) == 16

    def test_input_signature_serializable_input(self):
        """可序列化的 input_data 应正常返回签名."""
        req = self._make(
            input_data={"spindle_speed": 8000.0, "feed_rate": 0.05},
            options={"projection_method": "pca"},
        )
        sig = req.input_signature()
        assert isinstance(sig, str)
        assert len(sig) == 16

    def test_input_signature_unserializable_input_fallback_str(self):
        """不可 JSON 序列化的 input_data 走 default=str 宽容路径（不抛错）.

        生产实现使用 ``json.dumps(..., default=str)``：不可序列化对象
        （如 ``object()``）会被转为 repr 字符串参与签名，保证签名计算
        永不因输入类型失败（签名仅用于去重，宽容处理不破坏正确性）。
        """
        req = self._make(input_data={"object": object()})  # object() 不可序列化
        sig = req.input_signature()
        assert isinstance(sig, str)
        assert len(sig) == 16

    def test_input_signature_options_order_independent(self):
        """options 字段顺序不影响签名（sort_keys=True）."""
        req1 = self._make(options={"a": 1, "b": 2})
        req2 = self._make(options={"b": 2, "a": 1})
        assert req1.input_signature() == req2.input_signature()


# ExplanationRecord


@pytest.mark.unit
@pytest.mark.contracts
class TestExplanationRecord:
    """ExplanationRecord dataclass 构造校验."""

    def _make(self, **overrides) -> ExplanationRecord:
        defaults: dict[str, Any] = dict(
            id="exp_001",
            explanation_type=ExplanationType.HIDDEN_STATE,
            model_uri="model://ltc/1.0.0",
            source_snapshot_id="snap_001",
            input_signature="abc123def456abcd",
            payload_path="/data/exp_001.json",
            payload_size_bytes=1024,
        )
        defaults.update(overrides)
        return ExplanationRecord(**defaults)

    def test_valid_record(self):
        rec = self._make()
        assert rec.id == "exp_001"
        assert rec.payload_size_bytes == 1024

    def test_default_optional_fields(self):
        rec = self._make()
        assert rec.metadata_json == {}
        assert rec.created_by is None
        assert rec.expires_at is None
        # created_at 默认 utcnow
        assert isinstance(rec.created_at, datetime)

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            self._make(id="")

    @pytest.mark.parametrize(
        "exp_type",
        ["hidden_state", "gate_dynamics", "counterfactual", "confidence"],
    )
    def test_valid_explanation_type(self, exp_type: str):
        rec = self._make(explanation_type=exp_type)
        assert rec.explanation_type == exp_type

    @pytest.mark.parametrize("exp_type", ["", "HIDDEN", "random"])
    def test_invalid_explanation_type_rejected(self, exp_type: str):
        with pytest.raises(ValueError, match="explanation_type"):
            self._make(explanation_type=exp_type)

    def test_empty_model_uri_rejected(self):
        with pytest.raises(ValueError, match="model_uri"):
            self._make(model_uri="")

    def test_empty_payload_path_rejected(self):
        with pytest.raises(ValueError, match="payload_path"):
            self._make(payload_path="")

    @pytest.mark.parametrize("size", [0, 1, 1024, 1048576])
    def test_valid_payload_size_bytes(self, size: int):
        rec = self._make(payload_size_bytes=size)
        assert rec.payload_size_bytes == size

    @pytest.mark.parametrize("size", [-1, -100])
    def test_invalid_payload_size_bytes_rejected(self, size: int):
        with pytest.raises(ValueError, match="payload_size_bytes"):
            self._make(payload_size_bytes=size)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "id",
            "explanation_type",
            "model_uri",
            "source_snapshot_id",
            "input_signature",
            "payload_path",
            "payload_size_bytes",
            "metadata",
            "created_by",
            "created_at",
            "expires_at",
        }

    def test_to_dict_isoformat(self):
        """to_dict 将 created_at / expires_at 转为 isoformat."""
        created = datetime(2026, 7, 14, 10, 0, 0)
        expires = datetime(2026, 7, 21, 10, 0, 0)
        rec = self._make(created_at=created, expires_at=expires)
        d = rec.to_dict()
        assert d["created_at"] == created.isoformat()
        assert d["expires_at"] == expires.isoformat()

    def test_to_dict_metadata_field_renamed(self):
        """to_dict 将 metadata_json 字段重命名为 metadata."""
        rec = self._make(metadata_json={"projection_dim": 2})
        d = rec.to_dict()
        assert "metadata" in d
        assert d["metadata"] == {"projection_dim": 2}
        # 不应保留原字段名
        assert "metadata_json" not in d

    def test_to_dict_expires_at_none(self):
        rec = self._make()
        d = rec.to_dict()
        assert d["expires_at"] is None


# ExplanationComparison


@pytest.mark.unit
@pytest.mark.contracts
class TestExplanationComparison:
    """ExplanationComparison dataclass 构造校验.

    特别测试 base ≠ compared 校验。
    """

    def _make(self, **overrides) -> ExplanationComparison:
        defaults: dict[str, Any] = dict(
            id="cmp_001",
            base_explanation_id="exp_001",
            compared_explanation_id="exp_002",
            comparison_type=ComparisonType.SAME_MODEL_DIFF_INPUT,
            diff_payload_path="/data/cmp_001.json",
        )
        defaults.update(overrides)
        return ExplanationComparison(**defaults)

    def test_valid_comparison(self):
        cmp = self._make()
        assert cmp.id == "cmp_001"
        assert cmp.base_explanation_id == "exp_001"
        assert cmp.compared_explanation_id == "exp_002"

    def test_default_optional_fields(self):
        cmp = self._make()
        assert cmp.created_by is None
        assert isinstance(cmp.created_at, datetime)

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="id"):
            self._make(id="")

    def test_empty_base_rejected(self):
        with pytest.raises(ValueError, match="base_explanation_id"):
            self._make(base_explanation_id="")

    def test_empty_compared_rejected(self):
        with pytest.raises(ValueError, match="compared_explanation_id"):
            self._make(compared_explanation_id="")

    def test_base_equal_compared_rejected(self):
        """base 与 compared 相同应被拒绝."""
        with pytest.raises(ValueError, match="base 与 compared 不能相同"):
            self._make(
                base_explanation_id="exp_001",
                compared_explanation_id="exp_001",
            )

    @pytest.mark.parametrize(
        "cmp_type",
        [
            "same_model_diff_input",
            "diff_model_same_input",
            "diff_model_diff_input",
        ],
    )
    def test_valid_comparison_type(self, cmp_type: str):
        cmp = self._make(comparison_type=cmp_type)
        assert cmp.comparison_type == cmp_type

    @pytest.mark.parametrize("cmp_type", ["", "SAME", "same_model", "random"])
    def test_invalid_comparison_type_rejected(self, cmp_type: str):
        with pytest.raises(ValueError, match="comparison_type"):
            self._make(comparison_type=cmp_type)

    def test_empty_diff_payload_path_rejected(self):
        with pytest.raises(ValueError, match="diff_payload_path"):
            self._make(diff_payload_path="")

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "id",
            "base_explanation_id",
            "compared_explanation_id",
            "comparison_type",
            "diff_payload_path",
            "created_by",
            "created_at",
        }

    def test_to_dict_isoformat(self):
        ts = datetime(2026, 7, 14, 10, 0, 0)
        cmp = self._make(created_at=ts)
        d = cmp.to_dict()
        assert d["created_at"] == ts.isoformat()


# 异常层级


@pytest.mark.unit
@pytest.mark.contracts
class TestExceptions:
    """异常层级关系 + code 属性 + explanation_id 属性."""

    def test_explainability_error_is_runtime_error(self):
        assert issubclass(ExplainabilityError, RuntimeError)

    def test_lookup_error_is_explainability_error(self):
        assert issubclass(ExplanationLookupError, ExplainabilityError)

    def test_validation_error_is_explainability_error(self):
        assert issubclass(ExplanationValidationError, ExplainabilityError)

    def test_projection_error_is_explainability_error(self):
        assert issubclass(ProjectionError, ExplainabilityError)

    def test_sampling_error_is_explainability_error(self):
        assert issubclass(SamplingError, ExplainabilityError)

    def test_comparison_mismatch_error_is_explainability_error(self):
        assert issubclass(ComparisonMismatchError, ExplainabilityError)

    def test_distinct_subclasses(self):
        """5 个子类互不继承."""
        subclasses = [
            ExplanationLookupError,
            ExplanationValidationError,
            ProjectionError,
            SamplingError,
            ComparisonMismatchError,
        ]
        for i, a in enumerate(subclasses):
            for j, b in enumerate(subclasses):
                if i != j:
                    assert not issubclass(a, b)

    def test_base_default_code(self):
        err = ExplainabilityError("test")
        assert err.code == "EXPLAINABILITY_ERROR"

    def test_base_custom_code(self):
        err = ExplainabilityError("test", code="CUSTOM_CODE")
        assert err.code == "CUSTOM_CODE"

    def test_lookup_error_code(self):
        err = ExplanationLookupError("exp_001")
        assert err.code == "EXPLANATION_NOT_FOUND"

    def test_lookup_error_has_explanation_id(self):
        err = ExplanationLookupError("exp_001")
        assert err.explanation_id == "exp_001"

    def test_validation_error_code(self):
        err = ExplanationValidationError("bad input")
        assert err.code == "EXPLANATION_VALIDATION_ERROR"

    def test_projection_error_code(self):
        err = ProjectionError("too few samples")
        assert err.code == "PROJECTION_ERROR"

    def test_sampling_error_code(self):
        err = SamplingError("dropout failed")
        assert err.code == "SAMPLING_ERROR"

    def test_comparison_mismatch_error_code(self):
        err = ComparisonMismatchError("type mismatch")
        assert err.code == "COMPARISON_MISMATCH"

    def test_raise_lookup_error(self):
        with pytest.raises(ExplanationLookupError):
            raise ExplanationLookupError("exp_001")

    def test_raise_validation_error(self):
        with pytest.raises(ExplanationValidationError):
            raise ExplanationValidationError("bad input")

    def test_raise_projection_error(self):
        with pytest.raises(ProjectionError):
            raise ProjectionError("too few samples")

    def test_raise_sampling_error(self):
        with pytest.raises(SamplingError):
            raise SamplingError("dropout failed")

    def test_raise_comparison_mismatch_error(self):
        with pytest.raises(ComparisonMismatchError):
            raise ComparisonMismatchError("type mismatch")

    def test_catch_subclass_as_base(self):
        """子类异常可被基类 except 捕获."""
        with pytest.raises(ExplainabilityError):
            raise ProjectionError("too few samples")

    def test_catch_as_runtime_error(self):
        """ExplainabilityError 继承自 RuntimeError，可被 RuntimeError 捕获."""
        with pytest.raises(RuntimeError):
            raise ExplainabilityError("base error")


# IExplainabilityService ABC


@pytest.mark.unit
@pytest.mark.contracts
class TestIExplainabilityService:
    """IExplainabilityService 抽象接口."""

    def test_cannot_instantiate_abstract_class(self):
        """ABC 不可直接实例化."""
        with pytest.raises(TypeError):
            IExplainabilityService()  # type: ignore[abstract]

    def test_has_eight_abstract_methods(self):
        """应包含 8 个 abstract method."""
        abstract_methods = {
            "generate_hidden_state_explanation",
            "generate_gate_dynamics_explanation",
            "generate_counterfactual_explanation",
            "generate_confidence_explanation",
            "get_explanation",
            "list_explanations",
            "delete_explanation",
            "compare_explanations",
        }
        # __abstractmethods__ 是 frozenset
        assert hasattr(IExplainabilityService, "__abstractmethods__")
        assert set(IExplainabilityService.__abstractmethods__) == abstract_methods

    def test_can_subclass_with_all_methods(self):
        """实现全部 8 个方法后可子类化."""

        class DummyService(IExplainabilityService):
            async def generate_hidden_state_explanation(
                self,
                model_uri,
                *,
                source_snapshot_id=None,
                projection_method=ProjectionMethod.PCA,
                projection_dim=2,
                max_frames=1000,
                created_by=None,
            ):
                return ExplanationRecord(
                    id="exp_001",
                    explanation_type=ExplanationType.HIDDEN_STATE,
                    model_uri=model_uri,
                    source_snapshot_id=source_snapshot_id,
                    input_signature="abc123def456abcd",
                    payload_path="/data/exp_001.json",
                    payload_size_bytes=0,
                )

            async def generate_gate_dynamics_explanation(
                self,
                model_uri,
                *,
                source_snapshot_id=None,
                anomaly_sigma=2.0,
                created_by=None,
            ):
                return ExplanationRecord(
                    id="exp_002",
                    explanation_type=ExplanationType.GATE_DYNAMICS,
                    model_uri=model_uri,
                    source_snapshot_id=source_snapshot_id,
                    input_signature="def456abc789ef01",
                    payload_path="/data/exp_002.json",
                    payload_size_bytes=0,
                )

            async def generate_counterfactual_explanation(
                self,
                model_uri,
                *,
                base_input,
                perturbed_feature,
                perturbation_range=None,
                perturbation_step=0.05,
                source_snapshot_id=None,
                created_by=None,
            ):
                return ExplanationRecord(
                    id="exp_003",
                    explanation_type=ExplanationType.COUNTERFACTUAL,
                    model_uri=model_uri,
                    source_snapshot_id=source_snapshot_id,
                    input_signature="fff0001112223334",
                    payload_path="/data/exp_003.json",
                    payload_size_bytes=0,
                )

            async def generate_confidence_explanation(
                self,
                model_uri,
                *,
                input_data,
                sample_count=30,
                source_snapshot_id=None,
                created_by=None,
            ):
                return ExplanationRecord(
                    id="exp_004",
                    explanation_type=ExplanationType.CONFIDENCE,
                    model_uri=model_uri,
                    source_snapshot_id=source_snapshot_id,
                    input_signature="4445556667778889",
                    payload_path="/data/exp_004.json",
                    payload_size_bytes=0,
                )

            async def get_explanation(self, explanation_id, *, include_payload=False):
                return {"id": explanation_id}

            async def list_explanations(
                self,
                *,
                explanation_type=None,
                model_uri=None,
                limit=50,
                offset=0,
            ):
                return [], 0

            async def delete_explanation(self, explanation_id):
                return True

            async def compare_explanations(
                self,
                base_explanation_id,
                compared_explanation_id,
                *,
                comparison_type=ComparisonType.SAME_MODEL_DIFF_INPUT,
                created_by=None,
            ):
                return ExplanationComparison(
                    id="cmp_001",
                    base_explanation_id=base_explanation_id,
                    compared_explanation_id=compared_explanation_id,
                    comparison_type=comparison_type,
                    diff_payload_path="/data/cmp_001.json",
                )

        service = DummyService()
        assert service is not None

    def test_cannot_subclass_with_missing_methods(self):
        """缺少任一 abstract method 仍不可实例化."""

        class IncompleteService(IExplainabilityService):
            async def generate_hidden_state_explanation(self, model_uri, **kwargs):
                pass

            # 缺少其他 7 个方法

        with pytest.raises(TypeError):
            IncompleteService()  # type: ignore[abstract]
