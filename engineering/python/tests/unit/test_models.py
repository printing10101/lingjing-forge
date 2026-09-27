"""Unit tests for Pydantic models in app/models/."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


class TestLNNModelInfo:
    """Tests for LNNModelInfo schema (from schemas.py)."""

    def test_valid_model_info(self):
        from app.models.schemas import LNNModelInfo

        model = LNNModelInfo(
            name="test_model", version="1.0", last_updated="2026-01-01"
        )
        assert model.name == "test_model"
        assert model.version == "1.0"
        assert model.last_updated == "2026-01-01"

    def test_empty_version_default(self):
        from app.models.schemas import LNNModelInfo

        model = LNNModelInfo(name="test", version="1.0", last_updated="2026-01-01")
        assert model.version == "1.0"
        assert isinstance(model.name, str)


class TestLNNPredictRequest:
    """Tests for LNNPredictRequest schema."""

    def test_valid_predict_request(self):
        from app.models.schemas import LNNPredictRequest

        req = LNNPredictRequest(model_name="test_model", input_data=[1.0, 2.0, 3.0])
        assert req.model_name == "test_model"
        assert len(req.input_data) == 3

    def test_predict_missing_model_name(self):
        from app.models.schemas import LNNPredictRequest

        with pytest.raises(ValidationError):
            LNNPredictRequest(input_data=[1.0])

    def test_predict_missing_input_data(self):
        from app.models.schemas import LNNPredictRequest

        with pytest.raises(ValidationError):
            LNNPredictRequest(model_name="test")

    def test_predict_empty_input_data_allowed(self):
        from app.models.schemas import LNNPredictRequest

        req = LNNPredictRequest(model_name="test", input_data=[])
        assert req.input_data == []


class TestLNNTrainRequest:
    """Tests for LNNTrainRequest schema."""

    def test_valid_train_request(self):
        from app.models.schemas import LNNTrainRequest, LNNHyperparameters

        req = LNNTrainRequest(
            model_name="test_model",
            data_path="/data/test.csv",
            hyperparameters=LNNHyperparameters(
                learning_rate=0.001,
                epochs=10,
                batch_size=32,
                optimizer="adam",
            ),
        )
        assert req.model_name == "test_model"
        assert req.data_path == "/data/test.csv"
        assert req.hyperparameters.epochs == 10


class TestAlternativePlan:
    """Tests for AlternativePlan schema."""

    def test_valid_alternative_plan(self):
        from app.models.schemas import AlternativePlan

        plan = AlternativePlan(
            plan_id="plan_123",
            parameters={"key": "value"},
            expected_outcome="Expected result",
            confidence=0.85,
            reasoning="Test reasoning",
        )
        assert plan.plan_id == "plan_123"
        assert plan.confidence == 0.85

    def test_alternative_plan_confidence_required(self):
        from app.models.schemas import AlternativePlan

        with pytest.raises(ValidationError):
            AlternativePlan(
                plan_id="p1",
                parameters={},
                expected_outcome="ok",
                reasoning="r",
            )

    def test_alternative_plan_dict(self):
        from app.models.schemas import AlternativePlan

        plan = AlternativePlan(
            plan_id="p1",
            parameters={"x": 1},
            expected_outcome="ok",
            confidence=0.8,
            reasoning="r",
        )
        d = plan.model_dump()
        assert d["plan_id"] == "p1"


class TestAgentPredictRequest:
    """Tests for AgentPredictRequest schema."""

    def test_valid_agent_predict(self):
        from app.models.schemas import AgentPredictRequest

        req = AgentPredictRequest(model_name="agent_model", input_data=[0.5, 0.6])
        assert req.model_name == "agent_model"

    def test_agent_predict_missing_fields(self):
        from app.models.schemas import AgentPredictRequest

        with pytest.raises(ValidationError):
            AgentPredictRequest()


class TestAgentTokenCreateRequest:
    """Tests for AgentTokenCreateRequest schema."""

    def test_valid_token_create(self):
        from app.models.schemas import AgentTokenCreateRequest

        req = AgentTokenCreateRequest(scopes=["R", "W"])
        assert "R" in req.scopes
        assert "W" in req.scopes

    def test_token_requires_minimum_one_scope(self):
        from app.models.schemas import AgentTokenCreateRequest

        with pytest.raises(ValidationError):
            AgentTokenCreateRequest(scopes=[])


class TestValidationModels:
    """Tests for validation models in validation.py."""

    def test_validation_result_valid(self):
        from app.models.validation import ValidationResult

        result = ValidationResult(is_valid=True)
        assert result.is_valid is True
        assert result.errors == []

    def test_validation_result_invalid(self):
        from app.models.validation import ValidationResult

        result = ValidationResult(is_valid=False, errors=["bad input"])
        assert result.is_valid is False
        assert "bad input" in result.errors

    def test_validation_result_merge(self):
        from app.models.validation import ValidationResult

        r1 = ValidationResult(is_valid=True, warnings=["warning1"])
        r2 = ValidationResult(is_valid=False, errors=["error1"])
        merged = r1.merge(r2)
        assert merged.is_valid is False
        assert "error1" in merged.errors
        assert "warning1" in merged.warnings
