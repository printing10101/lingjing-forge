"""稳定标签方向及临界/异常输入的回归检查。"""

import numpy as np
import pytest

from experiments.real_validation.stability_rule import classify_stability


def test_below_limit_is_stable_and_boundary_is_not():
    assert classify_stability([0.5, 1.0, 1.5], 1.0).tolist() == [1, 0, 0]
    assert int(classify_stability(0.1, 0.2)) == 1


@pytest.mark.parametrize("ap,limit", [(np.nan, 1), (1, np.inf), (-1, 1), (1, 0), (1, -1)])
def test_invalid_physical_inputs_are_rejected(ap, limit):
    with pytest.raises(ValueError):
        classify_stability(ap, limit)


def test_shared_evaluator_uses_same_direction():
    from types import SimpleNamespace
    from experiments.real_validation.measured_stability_dataset import evaluate_stability_classification

    dataset = SimpleNamespace(
        stability=np.array([1, 0, 0]), ap=np.array([0.5, 1.0, 1.5]),
        features=np.zeros((3, 7)),
    )
    metrics = evaluate_stability_classification(lambda _: np.ones(3), dataset)
    assert metrics["accuracy"] == 1
    assert metrics["mcc"] == 1


def test_independent_seven_point_entry_uses_same_direction():
    from experiments.real_validation.run_real_points_validation import evaluate_points

    points = [{"ap": 0.5, "stable": 1}, {"ap": 1.0, "stable": 0}, {"ap": 1.5, "stable": 0}]
    result = evaluate_points(points, lambda _: 1.0)
    assert result["predicted_stable"] == [1, 0, 0]
    assert result["metrics"]["accuracy"] == 1
